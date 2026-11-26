#!/usr/bin/env python3
"""Discover a pinned diff, Spec Kit artifacts, and layered repository context."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from discovery_output_budget import render_discovery_result

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SMELLS_FILE = SKILL_DIR / "references" / "review-smells.json"
TOP_K_SMELLS = 3
MAX_ACTIVE_SMELLS = 15
L1_REF_MAX_LINES = 160
DISCOVERY_CACHE_DIR = Path(tempfile.gettempdir()) / "code-review-discovery-cache"
EXCLUDED_DIRS = {
    ".git",
    ".build",
    ".worktrees",
    ".shared-ci",
    ".venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "Pods",
    ".swiftpm",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "DerivedData",
}
CONTEXT_NAMES = {"tech-context.md", "context.md"}
VALID_AXES = {"standards", "spec"}
VALID_SEVERITIES = {"P0", "P1", "P2"}
REQUIRED_FINDING_FIELDS = (
    "axis",
    "severity",
    "layer",
    "path",
    "title",
    "source",
    "evidence",
    "remediation",
)


class ReviewSurfaceDomainError(RuntimeError):
    """Domain-level error for review surface resolution (exit code 1)."""


class EmptyReviewSurfaceError(ReviewSurfaceDomainError):
    """Domain-level signal that no changed files exist on the review surface (exit code 1)."""


def git(repo: Path, *args: str, check: bool = True, strip: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip() if strip else result.stdout


def tracked_walk(root: Path):
    ls_proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if ls_proc.returncode == 0:
        seen: set[str] = set()
        for raw in ls_proc.stdout.split(b"\0"):
            if not raw:
                continue
            rel = raw.decode("utf-8", errors="replace")
            if rel in seen:
                continue
            seen.add(rel)
            parts = Path(rel).parts
            if any(part in EXCLUDED_DIRS for part in parts):
                continue
            candidate = root / rel
            if candidate.is_file():
                yield candidate
        return

    for path in root.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def is_context_filename(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in CONTEXT_NAMES
        or (lowered.startswith("context.") and lowered.endswith(".md"))
        or (lowered.startswith("tech-context.") and lowered.endswith(".md"))
    )


def match_glob(path: str, pattern: str) -> bool:
    """Match POSIX paths; ** crosses directories and * does not."""
    norm = pattern.strip().strip("'\"")
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.rstrip("/")
    if not norm:
        return False
    if norm == ".":
        return True
    has_glob = any(ch in norm for ch in ("*", "?", "["))
    if not has_glob:
        return path == norm or path.startswith(norm + "/")
    pieces: list[str] = []
    index = 0
    while index < len(norm):
        char = norm[index]
        if char == "*":
            if index + 1 < len(norm) and norm[index + 1] == "*":
                if index + 2 < len(norm) and norm[index + 2] == "/":
                    pieces.append("(?:.*/)?")
                    index += 3
                else:
                    pieces.append(".*")
                    index += 2
            else:
                pieces.append("[^/]*")
                index += 1
        elif char == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(char))
            index += 1
    regex = "".join(pieces)
    if re.fullmatch(regex, path) is not None:
        return True
    if not norm.endswith("*") and re.fullmatch(regex + "/.*", path) is not None:
        return True
    return False


def strip_yaml_inline_comment(raw: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(raw):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (idx == 0 or raw[idx - 1].isspace()):
            return raw[:idx].rstrip()
    return raw.strip()


def parse_inline_list(value: str) -> list[str]:
    value = strip_yaml_inline_comment(value)
    if not (value.startswith("[") and value.endswith("]")):
        return []
    return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip()]
    return []


def parse_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.match(r"^---\s*\n(.*?)\n---(?:\s*\n|$)", text, re.S)
    if not match:
        return {}
    block_raw = match.group(1).strip()
    if not block_raw:
        return {}
    if block_raw.startswith("{"):
        try:
            parsed = json.loads(block_raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    lines = match.group(1).splitlines()
    data: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line.startswith((" ", "\t", "-")) or ":" not in line:
            index += 1
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = strip_yaml_inline_comment(raw)
        if raw:
            data[key] = parse_inline_list(raw) if raw.startswith("[") else raw.strip("'\"")
            index += 1
            continue
        index += 1
        block: list[str] = []
        mapping: dict[str, list[str]] = {}
        while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
            child = strip_yaml_inline_comment(lines[index].strip())
            if not child or child.startswith("#"):
                index += 1
                continue
            if child.startswith("-"):
                item_text = child[1:].strip()
                if ":" in item_text and not (item_text.startswith(("'", '"')) and item_text.endswith(("'", '"'))):
                    # Preserve simple string bullet even when prose contains ':' (e.g. red_lines)
                    block.append(item_text.strip("'\""))
                else:
                    block.append(item_text.strip("'\""))
            elif ":" in child:
                child_key, child_value = child.split(":", 1)
                child_val = strip_yaml_inline_comment(child_value)
                mapping[child_key.strip()] = (
                    parse_inline_list(child_val)
                    if child_val.startswith("[")
                    else ([child_val.strip("'\"")] if child_val else [])
                )
            index += 1
        data[key] = mapping if mapping else block
    return data


def parse_diff_range(spec: str) -> tuple[str, str | None, bool]:
    """Parse `<base>...<head>` or `<base>..<head>` into `(base, head, three_dot)`."""
    raw = spec.strip()
    if "..." in raw:
        left, right = raw.split("...", 1)
        base = left.strip()
        head = right.strip() or "HEAD"
        if not base:
            raise RuntimeError(f"invalid diff range: {spec}")
        return base, head, True
    if ".." in raw:
        left, right = raw.split("..", 1)
        base = left.strip()
        head = right.strip() or "HEAD"
        if not base:
            raise RuntimeError(f"invalid diff range: {spec}")
        return base, head, False
    if not raw:
        raise RuntimeError("diff range cannot be empty")
    return raw, None, True


def resolve_base(
    repo: Path,
    requested: str | None,
    head_ref: str = "HEAD",
    three_dot: bool = True,
) -> tuple[str, str]:
    if head_ref != "HEAD":
        git(repo, "rev-parse", "--verify", f"{head_ref}^{{commit}}")
    if requested:
        git(repo, "rev-parse", "--verify", f"{requested}^{{commit}}")
        base_ref = requested
    else:
        remote_head = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
        candidates = [remote_head, "origin/main", "origin/master", "main", "master"]
        base_ref = ""
        for candidate in candidates:
            if candidate and git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}", check=False):
                base_ref = candidate
                break
        if not base_ref:
            raise RuntimeError("cannot derive a default branch; pass --base <ref>")
    if not three_dot:
        fixed = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        return base_ref, fixed
    merge_base = git(repo, "merge-base", head_ref, base_ref)
    if not merge_base:
        raise RuntimeError(f"{head_ref} has no merge-base with {base_ref}")
    return base_ref, merge_base


def changed_files(
    repo: Path,
    merge_base: str,
    committed_only: bool,
    head_ref: str = "HEAD",
    three_dot: bool = True,
) -> list[dict[str, str]]:
    if committed_only:
        sep = "..." if three_dot else ".."
        diff_range = f"{merge_base}{sep}{head_ref}"
    else:
        diff_range = merge_base
    output = git(repo, "diff", "--name-status", "--find-renames", diff_range, "--")
    changes = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        changes.append({"status": status, "path": path})
    if not committed_only:
        known_paths = {change["path"] for change in changes}
        untracked = git(repo, "ls-files", "--others", "--exclude-standard")
        for path in untracked.splitlines():
            if path and path not in known_paths:
                changes.append({"status": "??", "path": path})
    changes.sort(key=lambda change: change["path"])
    return changes


def discover_specs(repo: Path, branch: str, changes: list[dict[str, str]], requested: str | None) -> dict:
    candidates = sorted(repo.glob("specs/*/spec.md"))
    if requested:
        raw_req = Path(requested).expanduser()
        selected = raw_req.resolve() if raw_req.is_absolute() else (repo / raw_req).resolve()
        selected = selected / "spec.md" if selected.is_dir() else selected
        is_inside_repo = True
        try:
            selected.relative_to(repo)
        except ValueError:
            is_inside_repo = False
        if not is_inside_repo:
            if not selected.is_file():
                raise RuntimeError(f"invalid external spec file: {requested}")
            active = selected.parent
            artifacts = {}
            for name in ("spec.md", "plan.md", "tasks.md", "design.md"):
                artifact = active / name
                if artifact.is_file():
                    artifacts[name.removesuffix(".md")] = str(artifact)
            if "spec" not in artifacts:
                artifacts["spec"] = str(selected)
            return {
                "active_feature_dir": str(active),
                "artifacts": artifacts,
                "candidates": [relative(repo, path) for path in candidates],
            }
        if selected.name != "spec.md" or not selected.is_file():
            raise RuntimeError(f"invalid Spec Kit feature: {requested}")
        active = selected.parent
        artifacts = {}
        for name in ("spec.md", "plan.md", "tasks.md"):
            artifact = active / name
            if artifact.is_file():
                artifacts[name.removesuffix(".md")] = relative(repo, artifact)
        return {
            "active_feature_dir": relative(repo, active),
            "artifacts": artifacts,
            "candidates": [relative(repo, path) for path in candidates],
        }
    branch_leaf = branch.rsplit("/", 1)[-1].lower()
    number = re.match(r"(\d+)[-_]", branch_leaf)
    changed_features = {
        parts[1]
        for change in changes
        if len(parts := Path(change["path"]).parts) >= 3 and parts[0] == "specs"
    }
    matches = []
    for spec in candidates:
        feature = spec.parent.name.lower()
        if spec.parent.name in changed_features:
            matches.append(spec)
        elif feature == branch_leaf or feature in branch_leaf or branch_leaf in feature:
            matches.append(spec)
        elif number and feature.startswith(f"{number.group(1)}-"):
            matches.append(spec)
    active = matches[0].parent if len(matches) == 1 else None
    artifacts = {}
    if active:
        for name in ("spec.md", "plan.md", "tasks.md"):
            artifact = active / name
            if artifact.is_file():
                artifacts[name.removesuffix(".md")] = relative(repo, artifact)
    return {
        "active_feature_dir": relative(repo, active) if active else None,
        "artifacts": artifacts,
        "candidates": [relative(repo, path) for path in candidates],
    }


def _extract_exclusion_patterns(frontmatter: dict[str, object], context_dir: str) -> list[str]:
    patterns: list[str] = []
    exclusions = frontmatter.get("exclusions")
    if isinstance(exclusions, list):
        for entry in exclusions:
            if isinstance(entry, dict):
                for raw in _string_list(entry.get("patterns")):
                    patterns.append(f"{context_dir}/{raw}" if context_dir not in {"", "."} else raw)
            elif isinstance(entry, str) and entry.strip():
                raw = entry.strip()
                patterns.append(f"{context_dir}/{raw}" if context_dir not in {"", "."} else raw)
    for key in ("support", "support_excludes", "generated_excludes"):
        for raw in _string_list(frontmatter.get(key)):
            patterns.append(f"{context_dir}/{raw}" if context_dir not in {"", "."} else raw)
    return patterns


def _resolve_pattern_base_dir(repo: Path, context_rel: str, parent_field: object) -> str:
    context_parent = Path(context_rel).parent.as_posix()
    if isinstance(parent_field, str) and parent_field.strip():
        parent_norm = parent_field.strip().strip("'\"")
        if (repo / parent_norm).is_file() or parent_norm.lower().endswith(".md"):
            return Path(parent_norm).parent.as_posix()
    return context_parent


def _looks_like_path_pattern(token: str, repo: Path, base_dir: str, context_dir: str) -> bool:
    if any(ch in token for ch in ("/", "*", "?", "[")):
        return True
    if " " in token:
        return False
    if "." in token:
        return True
    for prefix in (base_dir, context_dir, "."):
        cand = repo / token if prefix in {"", "."} else repo / prefix / token
        if cand.exists():
            return True
    return False


def _layer_matches_file(layer: dict[str, object], file_path: str) -> tuple[bool, int]:
    """Return `(matched, specificity_score)` for `file_path` against `layer`."""
    patterns = layer.get("_match_patterns", [])
    base_dir = str(layer.get("_pattern_base", "."))
    layer_root = str(layer.get("root", "."))
    best_score = -1

    if isinstance(patterns, list) and patterns:
        for pat in patterns:
            if not isinstance(pat, str) or not pat:
                continue
            anchored = pat if base_dir in {"", "."} else f"{base_dir}/{pat}"
            if match_glob(file_path, anchored):
                best_score = max(best_score, 100 + len(anchored))
            elif base_dir not in {"", "."} and match_glob(file_path, pat):
                best_score = max(best_score, 90 + len(pat))
            elif layer_root not in {"", ".", base_dir}:
                root_anchored = f"{layer_root}/{pat}"
                if match_glob(file_path, root_anchored):
                    best_score = max(best_score, 95 + len(root_anchored))
        if best_score >= 0:
            return True, best_score
        if layer.get("_strict_scope_only"):
            return False, -1

    if layer_root not in {"", "."} and (file_path == layer_root or file_path.startswith(layer_root + "/")):
        return True, len(layer_root)
    return False, -1


def _extract_test_command(frontmatter: dict[str, object]) -> object:
    if frontmatter.get("test") is not None:
        return frontmatter.get("test")
    if frontmatter.get("gate") is not None:
        return frontmatter.get("gate")
    gates = frontmatter.get("gates")
    if isinstance(gates, list):
        selected = [g for g in gates if isinstance(g, dict) and g.get("kind") == "test"] or [
            g for g in gates if isinstance(g, dict)
        ]
        for gate in selected:
            cmd = gate.get("command")
            if isinstance(cmd, list):
                return " ".join(str(part) for part in cmd)
            if isinstance(cmd, str) and cmd.strip():
                return cmd.strip()
    return None


def discover_context(repo: Path, changes: list[dict[str, str]]) -> tuple[dict, list[dict], list[str]]:
    files = list(tracked_walk(repo))
    routing = sorted(relative(repo, path) for path in files if path.name.lower() in {"agents.md", "claude.md"})
    constitutions = sorted(
        relative(repo, path)
        for path in files
        if path.name.lower() == "constitution.md" or relative(repo, path) == ".specify/memory/constitution.md"
    )
    top_context = sorted(
        relative(repo, path)
        for path in files
        if (path.parent == repo and (is_context_filename(path.name) or path.name.lower() == "architecture.md"))
        or relative(repo, path).lower() in {"docs/architecture/tech-context.md", "docs/tech-context.md"}
    )

    excluded_patterns: list[str] = []
    raw_layers: list[dict[str, object]] = []
    for path in files:
        if not is_context_filename(path.name):
            continue
        context_rel = relative(repo, path)
        context_dir = relative(repo, path.parent)
        frontmatter = parse_frontmatter(path)
        if not frontmatter:
            continue
        excluded_patterns.extend(_extract_exclusion_patterns(frontmatter, context_dir))
        kind = str(frontmatter.get("kind", "")).strip().lower()
        if kind == "index":
            continue
        layer_name = frontmatter.get("layer")
        if not isinstance(layer_name, str) or not layer_name.strip():
            continue
        layer_name = layer_name.strip()

        is_json_leaf = kind == "leaf" or "scope" in frontmatter or "gates" in frontmatter
        scope_list = _string_list(frontmatter.get("scope")) if isinstance(frontmatter.get("scope"), list) else []
        paths_list = _string_list(frontmatter.get("paths"))
        owns_raw = _string_list(frontmatter.get("owns"))
        test_paths = _string_list(frontmatter.get("test_paths"))

        base_dir = _resolve_pattern_base_dir(repo, context_rel, frontmatter.get("parent"))
        owns_path_patterns = [
            tok for tok in owns_raw if _looks_like_path_pattern(tok, repo, base_dir, context_dir)
        ]
        match_patterns: list[str] = []
        for item in [*scope_list, *paths_list, *owns_path_patterns, *test_paths]:
            if item not in match_patterns:
                match_patterns.append(item)

        owns_combined: list[str] = []
        for item in (owns_raw + scope_list + paths_list + test_paths):
            if item not in owns_combined:
                owns_combined.append(item)

        depends_on = _string_list(frontmatter.get("depends_on")) or _string_list(frontmatter.get("dependencies"))
        depended_by = _string_list(frontmatter.get("depended_by")) or _string_list(frontmatter.get("dependents"))
        red_lines = _string_list(frontmatter.get("red_lines"))
        gates_val = frontmatter.get("gates") if isinstance(frontmatter.get("gates"), list) else []

        raw_layers.append(
            {
                "name": layer_name,
                "root": context_dir,
                "context": context_rel,
                "depends_on": depends_on,
                "depended_by": depended_by,
                "red_lines": red_lines,
                "roles": frontmatter.get("roles", {}) if isinstance(frontmatter.get("roles"), dict) else {},
                "test": _extract_test_command(frontmatter),
                "gates": gates_val,
                "owns": owns_combined,
                "changed_files": [],
                "_match_patterns": match_patterns,
                "_pattern_base": base_dir,
                "_strict_scope_only": bool(is_json_leaf and ("scope" in frontmatter or "test_paths" in frontmatter)),
                "_is_json_leaf": is_json_leaf,
            }
        )

    # Deduplicate same-named layers when both JSON CONTEXT.md and companion tech-context.md exist
    grouped_by_name: dict[str, list[dict[str, object]]] = {}
    for entry in raw_layers:
        grouped_by_name.setdefault(str(entry["name"]), []).append(entry)

    layers: list[dict[str, object]] = []
    for entries in grouped_by_name.values():
        if len(entries) == 1:
            layers.append(entries[0])
            continue
        entries.sort(key=lambda item: (0 if item.get("_is_json_leaf") else 1, len(str(item["context"]))))
        primary = dict(entries[0])
        for extra in entries[1:]:
            if not primary.get("roles") and extra.get("roles"):
                primary["roles"] = extra["roles"]
            if not primary.get("test") and extra.get("test"):
                primary["test"] = extra["test"]
            for field in ("depends_on", "depended_by", "red_lines", "owns", "_match_patterns"):
                merged = list(primary.get(field, []))  # type: ignore[arg-type]
                for val in extra.get(field, []):  # type: ignore[union-attr]
                    if val not in merged:
                        merged.append(val)
                primary[field] = merged
        layers.append(primary)

    unmapped: list[str] = []
    excluded_files: list[str] = []
    for change in changes:
        file_path = change["path"]
        best_owner: dict[str, object] | None = None
        best_score = -1
        for layer in layers:
            matched, score = _layer_matches_file(layer, file_path)
            if matched and score > best_score:
                best_score = score
                best_owner = layer
        if best_owner is not None:
            best_owner["changed_files"].append(file_path)  # type: ignore[union-attr]
        elif any(match_glob(file_path, pat) for pat in excluded_patterns):
            excluded_files.append(file_path)
        else:
            unmapped.append(file_path)

    cleaned_layers: list[dict[str, object]] = []
    for layer in sorted(layers, key=lambda item: (str(item["root"]), str(item["name"]))):
        layer_out = {k: v for k, v in layer.items() if not k.startswith("_")}
        if not layer_out.get("gates"):
            layer_out.pop("gates", None)
        cleaned_layers.append(layer_out)

    context_meta: dict[str, object] = {
        "routing_indexes": routing,
        "constitutions": constitutions,
        "top_technical_context": top_context,
    }
    if excluded_files:
        context_meta["excluded_support_files"] = sorted(excluded_files)
    return (
        context_meta,
        cleaned_layers,
        sorted(unmapped),
    )


def load_review_smells(smells_file: Path = DEFAULT_SMELLS_FILE) -> dict[str, object]:
    if not smells_file.is_file():
        return {"schema": "code-review-smells/v1", "version": 1, "active_smells": [], "archived_smells": []}
    return json.loads(smells_file.read_text(encoding="utf-8"))


def match_review_smells(
    changes: list[dict[str, str]],
    layers: list[dict],
    smells_file: Path = DEFAULT_SMELLS_FILE,
    top_k: int = TOP_K_SMELLS,
) -> list[dict[str, object]]:
    doc = load_review_smells(smells_file)
    active = doc.get("active_smells", [])
    if not isinstance(active, list):
        return []
    changed_exts = {Path(item["path"]).suffix.lower() for item in changes if Path(item["path"]).suffix}
    changed_layer_names = {
        str(layer.get("name", "")).lower()
        for layer in layers
        if layer.get("changed_files")
    }
    scored: list[tuple[int, int, dict[str, object]]] = []
    for idx, smell in enumerate(active):
        if not isinstance(smell, dict):
            continue
        langs = [str(x).lower() for x in smell.get("languages", ["*"])]
        smell_layers = [str(x).lower() for x in smell.get("layers", ["*"])]
        lang_exact = bool(changed_exts & set(langs))
        lang_wild = "*" in langs
        if not (lang_exact or lang_wild):
            continue
        layer_exact = bool(changed_layer_names & set(smell_layers))
        layer_wild = "*" in smell_layers or not changed_layer_names
        if not (layer_exact or layer_wild):
            continue
        score = (2 if lang_exact else 1) + (2 if layer_exact else 1)
        scored.append((-score, idx, smell))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:top_k]]


def build_flow_map(smells_file: Path = DEFAULT_SMELLS_FILE) -> dict[str, object]:
    doc = load_review_smells(smells_file)
    active = doc.get("active_smells", []) if isinstance(doc.get("active_smells"), list) else []
    ref_dir = SKILL_DIR / "references"
    ref_line_counts = {
        path.relative_to(SKILL_DIR).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(ref_dir.glob("*.md"))
    }
    all_within_budget = all(lines <= L1_REF_MAX_LINES for lines in ref_line_counts.values()) and (
        len(active) <= MAX_ACTIVE_SMELLS
    )
    return {
        "ok": all_within_budget,
        "skill": "code-review",
        "architecture_ref": "skills/code-review/SKILL.md",
        "entry_a_ingest": {
            "A1_register_review_smell": (
                "python3 skills/code-review/scripts/discover_review_context.py --register-smell "
                "--id RS-xxx --title '...' --languages .swift,.py --layers core,ui --axis standards --symptom '...' --check-rule '...'"
            ),
        },
        "shared_tiers": {
            "tier_1_json_drawer": {
                "file": "skills/code-review/references/review-smells.json",
                "active_smell_count": len(active),
                "active_smell_ids": [s.get("id") for s in active if isinstance(s, dict)],
            },
            "tier_2_l1_docs_line_counts": ref_line_counts,
            "tier_3_discovery_router": "skills/code-review/scripts/discover_review_context.py",
        },
        "entry_b_consume": {
            "B1_discover_and_slice": "python3 skills/code-review/scripts/discover_review_context.py --repo <repo> [--base <ref> | --range <base>..<head>]",
            "B2_packet_prompts": "skills/code-review/references/reviewer-prompts.md",
            "B3_review_rubric": "skills/code-review/references/review-rubric.md",
            "B4_output_contract": "skills/code-review/references/output-contract.md",
        },
        "anti_explosion_valves": {
            "valve_1_zero_token_surface_discovery": True,
            "valve_2_layer_and_language_slicing": True,
            "valve_3_top_k_and_line_caps": {
                "matched_smells_top_k": TOP_K_SMELLS,
                "l1_doc_max_lines": L1_REF_MAX_LINES,
                "active_smells_cap": MAX_ACTIVE_SMELLS,
            },
            "valve_4_one_in_one_out_compaction": True,
            "all_l1_docs_within_budget": all_within_budget,
        },
    }


def register_review_smell(
    smells_file: Path,
    smell_id: str,
    title: str,
    languages: list[str],
    layers: list[str],
    axis: str,
    symptom: str,
    check_rule: str,
) -> dict[str, object]:
    doc = load_review_smells(smells_file)
    active = list(doc.get("active_smells", [])) if isinstance(doc.get("active_smells"), list) else []
    archived = list(doc.get("archived_smells", [])) if isinstance(doc.get("archived_smells"), list) else []
    new_entry = {
        "id": smell_id,
        "title": title,
        "languages": languages or ["*"],
        "layers": layers or ["*"],
        "axis": axis,
        "symptom": symptom,
        "check_rule": check_rule,
        "registered_at": date.today().isoformat(),
    }
    active = [s for s in active if isinstance(s, dict) and s.get("id") != smell_id]
    active.append(new_entry)
    while len(active) > MAX_ACTIVE_SMELLS:
        archived.append(active.pop(0))
    doc["active_smells"] = active
    doc["archived_smells"] = archived
    doc["updated"] = date.today().isoformat()
    smells_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"ok": True, "registered": new_entry, "active_count": len(active)}


def _worst_severity(severities: list[str]) -> str:
    for level in ("P0", "P1", "P2"):
        if level in severities:
            return level
    return "none"


def validate_findings_payload(
    raw_payload: object,
    *,
    inconclusive: bool = False,
) -> dict[str, object]:
    findings_raw: object = raw_payload
    meta_inconclusive = inconclusive
    coverage_gaps: list[str] = []
    if isinstance(raw_payload, dict):
        if "findings" in raw_payload:
            findings_raw = raw_payload["findings"]
        if bool(raw_payload.get("inconclusive")):
            meta_inconclusive = True
        if isinstance(raw_payload.get("coverage_gaps"), list):
            coverage_gaps = [str(g).strip() for g in raw_payload["coverage_gaps"] if str(g).strip()]

    errors: list[str] = []
    if not isinstance(findings_raw, list):
        return {
            "ok": False,
            "verdict": "INCONCLUSIVE",
            "finding_count": 0,
            "errors": ["findings payload must be a JSON array of finding objects (or an object with a 'findings' array)"],
            "summary": {
                "standards": {"count": 0, "worst": "none"},
                "spec": {"count": 0, "worst": "none"},
            },
        }

    standards_sevs: list[str] = []
    spec_sevs: list[str] = []
    all_sevs: list[str] = []

    for idx, item in enumerate(findings_raw):
        if not isinstance(item, dict):
            errors.append(f"finding[{idx}]: must be a JSON object")
            continue
        for field in REQUIRED_FINDING_FIELDS:
            val = item.get(field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"finding[{idx}]: missing or empty required string field {field!r}")
        axis = str(item.get("axis", "")).strip()
        if axis and axis not in VALID_AXES:
            errors.append(f"finding[{idx}]: invalid axis {axis!r} (expected one of {sorted(VALID_AXES)})")
        severity = str(item.get("severity", "")).strip()
        if severity and severity not in VALID_SEVERITIES:
            errors.append(f"finding[{idx}]: invalid severity {severity!r} (expected one of {sorted(VALID_SEVERITIES)})")
        if axis in VALID_AXES and severity in VALID_SEVERITIES:
            all_sevs.append(severity)
            if axis == "standards":
                standards_sevs.append(severity)
            else:
                spec_sevs.append(severity)

    if errors:
        verdict = "INCONCLUSIVE"
    elif meta_inconclusive:
        verdict = "INCONCLUSIVE"
    elif "P0" in all_sevs:
        verdict = "FAIL"
    elif "P1" in all_sevs:
        verdict = "CHANGES REQUESTED"
    elif "P2" in all_sevs:
        verdict = "PASS WITH FOLLOW-UP"
    else:
        verdict = "PASS"

    return {
        "ok": not errors,
        "verdict": verdict,
        "finding_count": len(findings_raw),
        "coverage_gaps": coverage_gaps,
        "errors": errors,
        "summary": {
            "standards": {"count": len(standards_sevs), "worst": _worst_severity(standards_sevs)},
            "spec": {"count": len(spec_sevs), "worst": _worst_severity(spec_sevs)},
        },
        "reference": "skills/code-review/references/output-contract.md",
    }


def schema_template_payload() -> dict[str, object]:
    return {
        "schema": "code-review-findings/v1",
        "required_fields": list(REQUIRED_FINDING_FIELDS),
        "valid_axes": sorted(VALID_AXES),
        "valid_severities": ["P0", "P1", "P2"],
        "verdict_mapping": {
            "P0": "FAIL",
            "P1": "CHANGES REQUESTED",
            "P2": "PASS WITH FOLLOW-UP",
            "none": "PASS",
            "unpinned_or_missing_authority": "INCONCLUSIVE",
        },
        "example_finding": {
            "axis": "standards",
            "severity": "P1",
            "layer": "Core",
            "path": "Packages/Core/Service.swift:42",
            "title": "Remove UI module import from Core layer",
            "source": "Packages/Core/CONTEXT.md:depends_on",
            "evidence": "Service.swift imports AppUI while Core declares empty dependencies.",
            "remediation": "Move view formatting into the UI layer and return a pure Core value.",
        },
        "reference": "skills/code-review/references/output-contract.md",
    }


def resolve_pull_request_surface(
    repo: Path,
    pr_raw: str,
    requested_base: str | None,
    requested_head: str,
) -> tuple[str | None, str]:
    """Resolve `--pr <num|ref>` fail-closed into `(base_ref_or_none, head_commit_or_ref)`."""
    pr_spec = pr_raw.strip().lstrip("#")
    if not pr_spec:
        raise ReviewSurfaceDomainError("--pr value cannot be empty")

    resolved_base = requested_base
    resolved_head: str | None = None if requested_head == "HEAD" else requested_head

    # 1. Try explicit ref (e.g. refs/pull/123/head or branch/tag ref passed via --pr)
    pull_ref_candidates = (
        [pr_spec]
        if pr_spec.startswith("refs/")
        else [f"refs/pull/{pr_spec}/head", f"refs/remotes/origin/pr/{pr_spec}"]
    )
    for ref_cand in pull_ref_candidates:
        verified = git(repo, "rev-parse", "--verify", f"{ref_cand}^{{commit}}", check=False)
        if verified and resolved_head is None:
            resolved_head = ref_cand
            break

    # 2. Query `gh pr view` for baseRefName and headRefOid
    gh_err = ""
    try:
        pr_view = subprocess.run(
            ["gh", "pr", "view", pr_spec, "--json", "baseRefName,headRefOid"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        pr_view = None
        gh_err = str(exc)

    if pr_view is not None:
        if pr_view.returncode == 0 and pr_view.stdout.strip():
            try:
                pr_meta = json.loads(pr_view.stdout)
            except json.JSONDecodeError:
                pr_meta = None
            if isinstance(pr_meta, dict):
                cand_base = str(pr_meta.get("baseRefName") or "").strip()
                if cand_base and not resolved_base:
                    resolved_base = (
                        f"origin/{cand_base}"
                        if git(repo, "rev-parse", "--verify", f"origin/{cand_base}^{{commit}}", check=False)
                        else cand_base
                    )
                cand_head = str(pr_meta.get("headRefOid") or "").strip()
                if cand_head and resolved_head is None:
                    if git(repo, "rev-parse", "--verify", f"{cand_head}^{{commit}}", check=False):
                        resolved_head = cand_head
                    elif pr_spec.isdigit():
                        fetch_proc = subprocess.run(
                            ["git", "-C", str(repo), "fetch", "--quiet", "origin", f"pull/{pr_spec}/head"],
                            text=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            check=False,
                        )
                        if fetch_proc.returncode == 0 and git(
                            repo, "rev-parse", "--verify", f"{cand_head}^{{commit}}", check=False
                        ):
                            resolved_head = cand_head
                        elif fetch_proc.returncode == 0 and git(
                            repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}", check=False
                        ):
                            resolved_head = git(repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
        else:
            gh_err = pr_view.stderr.strip()

    # 3. Last-resort fetch of origin pull/<num>/head if still unresolved and numeric
    if resolved_head is None and pr_spec.isdigit() and git(repo, "remote", "get-url", "origin", check=False):
        fetch_proc = subprocess.run(
            ["git", "-C", str(repo), "fetch", "--quiet", "origin", f"pull/{pr_spec}/head"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if fetch_proc.returncode == 0 and git(repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}", check=False):
            resolved_head = git(repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}")

    if resolved_head is None:
        detail = f" ({gh_err})" if gh_err else ""
        raise ReviewSurfaceDomainError(
            f"cannot resolve pull request {pr_raw!r} via gh pr view or refs/pull/{pr_spec}/head{detail}; refusing to fall back to local HEAD"
        )
    if resolved_base and not git(repo, "rev-parse", "--verify", f"{resolved_base}^{{commit}}", check=False):
        raise ReviewSurfaceDomainError(
            f"pull request {pr_raw!r} base ref {resolved_base!r} is not present locally; fetch base ref or pass --range <base>..<head>"
        )
    return resolved_base, resolved_head


def _script_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _status_porcelain_sha256(repo: Path) -> str:
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all", strip=False)
    diff = git(repo, "diff", "--binary", "HEAD", "--", strip=False)
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", strip=False)
    digest = hashlib.sha256()
    digest.update(status.encode("utf-8", errors="replace"))
    digest.update(diff.encode("utf-8", errors="replace"))
    for raw_path in untracked.split("\0"):
        if not raw_path:
            continue
        path = repo / raw_path
        digest.update(raw_path.encode("utf-8", errors="replace"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return digest.hexdigest()


def _smells_mtime_ns(smells_path: Path) -> int:
    try:
        return smells_path.stat().st_mtime_ns
    except OSError:
        return 0


def _discovery_cache_path(
    repo: Path,
    args_key: str,
    head_sha: str,
    base_sha: str,
    status_sha: str,
    smells_mtime_ns: int,
) -> Path:
    material = "\0".join(
        (
            _script_sha256(),
            str(repo),
            args_key,
            head_sha,
            base_sha,
            status_sha,
            str(smells_mtime_ns),
        )
    ).encode("utf-8")
    return DISCOVERY_CACHE_DIR / f"{hashlib.sha256(material).hexdigest()}.json"


def _read_discovery_cache(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _write_discovery_cache(path: Path, rendered: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return




class _SelfDisclosingReviewParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"code-review discovery error: {message}\n"
            "  Fix: run `python3 skills/code-review/scripts/discover_review_context.py [--repo <dir>] [--base <ref> | --range <base>..<head>] [--empty-ok] [--validate-findings <json|->] [--summary|--compact] [--full] [--flow-map]`.\n"
            "  Reference: skills/code-review/references/review-rubric.md · skills/code-review/references/output-contract.md · skills/code-review/references/reviewer-prompts.md\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _SelfDisclosingReviewParser()
    parser.add_argument("positional_repo", nargs="?", default=None, help="Optional positional repository/worktree directory")
    parser.add_argument("--repo", "--repo-dir", "--repo-root", "--worktree", "--cwd", "--dir", dest="repo", default=".")
    parser.add_argument("--base", "--base-ref", dest="base")
    parser.add_argument("--head", "--head-ref", dest="head", default="HEAD")
    parser.add_argument("--range", "--diff-range", dest="diff_range", help="Explicit git diff range (<base>..<head> or <base>...<head>)")
    parser.add_argument("--pr", dest="pr", help="Optional pull request number or ref")
    parser.add_argument("--spec", "--spec-file", "--handoff", dest="spec")
    parser.add_argument("--committed-only", action="store_true")
    parser.add_argument("--empty-ok", "--allow-empty", dest="empty_ok", action="store_true", help="Exit 0 with empty_surface=true when no changed files exist")
    parser.add_argument("--strict-non-empty", action="store_true", help="Disable HEAD~1 auto-fallback when HEAD == base_ref and fail on empty surface")
    parser.add_argument(
        "--validate-findings",
        "--findings",
        "--findings-json",
        dest="validate_findings",
        help="Validate review findings JSON file, inline JSON, or '-' for stdin against output-contract.md",
    )
    parser.add_argument("--summary", "--compact", dest="summary", action="store_true", help="Bound discovery preview arrays and include totals")
    parser.add_argument("--full", "--no-truncate", "--verbose", dest="full", action="store_true", help="Emit uncompacted discovery JSON output")
    parser.add_argument("--no-cache", action="store_true", help="Disable deterministic discovery result caching")
    parser.add_argument("--inconclusive", action="store_true", help="Mark findings validation verdict INCONCLUSIVE due to unpinned revision or missing authority")
    parser.add_argument("--schema-template", action="store_true", help="Emit output-contract.md finding schema and verdict mapping JSON")
    parser.add_argument("--json", action="store_true", help="Emit JSON output (default behavior)")
    parser.add_argument("--smells-file", default=str(DEFAULT_SMELLS_FILE))
    parser.add_argument("--flow-map", action="store_true")
    parser.add_argument("--register-smell", action="store_true")
    parser.add_argument("--id", help="Review smell ID (e.g. RS-005)")
    parser.add_argument("--title", help="Review smell title")
    parser.add_argument("--languages", default="*", help="Comma-separated file extensions (e.g. .swift,.py)")
    parser.add_argument("--layers", default="*", help="Comma-separated layer names (e.g. core,ui)")
    parser.add_argument("--axis", default="standards", choices=["standards", "spec"])
    parser.add_argument("--symptom", default="")
    parser.add_argument("--check-rule", default="")
    args = parser.parse_args(argv)
    if args.positional_repo and args.repo == ".":
        args.repo = args.positional_repo

    if args.schema_template:
        json.dump(schema_template_payload(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.validate_findings is not None:
        raw_src = args.validate_findings.strip()
        try:
            if raw_src.startswith(("[", "{")):
                parsed_findings = json.loads(raw_src)
            else:
                raw_text = sys.stdin.read() if raw_src == "-" else Path(raw_src).expanduser().read_text(encoding="utf-8")
                parsed_findings = json.loads(raw_text)
        except OSError as exc:
            raise RuntimeError(f"cannot read findings file {raw_src}: {exc}") from exc
        except json.JSONDecodeError as exc:
            payload = {
                "ok": False,
                "verdict": "INCONCLUSIVE",
                "finding_count": 0,
                "errors": [f"invalid JSON in findings input: {exc}"],
                "summary": {
                    "standards": {"count": 0, "worst": "none"},
                    "spec": {"count": 0, "worst": "none"},
                },
                "reference": "skills/code-review/references/output-contract.md",
            }
            json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            return 1
        validation = validate_findings_payload(parsed_findings, inconclusive=args.inconclusive)
        json.dump(validation, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0 if validation.get("ok") else 1

    smells_path = Path(args.smells_file).expanduser().resolve()
    if args.flow_map:
        payload = build_flow_map(smells_path)
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0 if payload.get("ok") else 1

    if args.register_smell:
        if not args.id or not args.title or not args.check_rule:
            raise RuntimeError("--register-smell requires --id, --title, and --check-rule")
        langs = [x.strip() for x in args.languages.split(",") if x.strip()]
        layer_list = [x.strip() for x in args.layers.split(",") if x.strip()]
        result = register_review_smell(
            smells_path,
            smell_id=args.id,
            title=args.title,
            languages=langs,
            layers=layer_list,
            axis=args.axis,
            symptom=args.symptom,
            check_rule=args.check_rule,
        )
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    repo = Path(git(Path(args.repo).expanduser().resolve(), "rev-parse", "--show-toplevel")).resolve()
    requested_base = args.base
    head_ref = args.head or "HEAD"
    committed_only = bool(args.committed_only)
    three_dot = True

    if args.pr is not None and not args.diff_range:
        requested_base, head_ref = resolve_pull_request_surface(
            repo,
            args.pr,
            requested_base=requested_base,
            requested_head=head_ref,
        )
        committed_only = True

    if args.diff_range:
        range_base, range_head, three_dot = parse_diff_range(args.diff_range)
        requested_base = range_base
        if range_head is not None:
            head_ref = range_head
            committed_only = True

    base_ref, merge_base = resolve_base(repo, requested_base, head_ref=head_ref, three_dot=three_dot)
    cache_args = {
        "base_ref": base_ref,
        "base_arg": args.base,
        "head_ref": head_ref,
        "diff_range": args.diff_range,
        "pr": args.pr,
        "spec": args.spec,
        "committed_only": committed_only,
        "three_dot": three_dot,
        "empty_ok": args.empty_ok,
        "strict_non_empty": args.strict_non_empty,
        "smells_file": str(smells_path),
        "summary": args.summary,
        "full": args.full,
    }
    cache_path: Path | None = None
    if not args.no_cache:
        head_sha = git(repo, "rev-parse", "--verify", f"{head_ref}^{{commit}}")
        base_sha = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        cache_path = _discovery_cache_path(
            repo,
            json.dumps(cache_args, sort_keys=True, separators=(",", ":")),
            head_sha,
            base_sha,
            _status_porcelain_sha256(repo),
            _smells_mtime_ns(smells_path),
        )
        cached = _read_discovery_cache(cache_path)
        if cached is not None:
            sys.stdout.write(cached)
            return 0

    changes = changed_files(repo, merge_base, committed_only, head_ref=head_ref, three_dot=three_dot)
    fallback_base: str | None = None

    if not changes and not args.strict_non_empty and not args.base and not args.diff_range and args.pr is None:
        head_sha = git(repo, "rev-parse", "--verify", f"{head_ref}^{{commit}}", check=False)
        base_sha = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}", check=False)
        parent_sha = git(repo, "rev-parse", "--verify", f"{head_ref}~1^{{commit}}", check=False)
        if head_sha and base_sha and head_sha == base_sha and parent_sha:
            fallback_ref = "HEAD~1" if head_ref == "HEAD" else f"{head_ref}~1"
            fallback_changes = changed_files(repo, parent_sha, committed_only, head_ref=head_ref, three_dot=False)
            if fallback_changes:
                changes = fallback_changes
                merge_base = parent_sha
                three_dot = False
                fallback_base = f"{fallback_ref} (HEAD == base_ref)"

    if not changes and not args.empty_ok:
        raise EmptyReviewSurfaceError("review surface is empty")

    branch = git(repo, "branch", "--show-current") or head_ref
    spec_kit = discover_specs(repo, branch, changes, args.spec)
    context, layers, unmapped = discover_context(repo, changes)
    commits = git(repo, "log", "--oneline", f"{merge_base}..{head_ref}").splitlines()
    gaps = []
    if not context["routing_indexes"]:
        gaps.append("no AGENTS.md/CLAUDE.md routing index")
    if not context["constitutions"]:
        gaps.append("no constitution source")
    if not spec_kit["active_feature_dir"]:
        gaps.append("no unambiguous active Spec Kit feature")
    if not layers:
        gaps.append("no layer tech-context frontmatter")
    if unmapped:
        gaps.append(f"{len(unmapped)} changed path(s) are unmapped")
    sep = "..." if three_dot else ".."
    result: dict[str, object] = {
        "repository": str(repo),
        "branch": branch,
        "base_ref": base_ref,
        "fixed_point": merge_base,
        "mode": "committed-only" if committed_only else "worktree",
        "empty_surface": not bool(changes),
        "commits": commits,
        "diff_command": (
            f"git diff {merge_base}{sep}{head_ref} --"
            if committed_only
            else f"git diff {merge_base} --"
        ),
        "changed_files": changes,
        "context": context,
        "spec_kit": spec_kit,
        "layers": layers,
        "unmapped_files": unmapped,
        "coverage_gaps": gaps,
        "matched_review_smells": match_review_smells(changes, layers, smells_file=smells_path),
    }
    if fallback_base is not None:
        result["fallback_base"] = fallback_base
    rendered = render_discovery_result(result, summary=args.summary, full=args.full)
    sys.stdout.write(rendered)
    if cache_path is not None:
        _write_discovery_cache(cache_path, rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EmptyReviewSurfaceError as error:
        print(
            f"code-review discovery: {error} (pass --empty-ok to inspect context without diff, or --base <ref> / --range <base>..<head>).\n"
            "  Reference: skills/code-review/references/review-rubric.md · skills/code-review/references/output-contract.md",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except ReviewSurfaceDomainError as error:
        print(
            f"code-review discovery: {error}\n"
            "  Reference: skills/code-review/references/review-rubric.md · skills/code-review/references/output-contract.md",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except RuntimeError as error:
        print(
            f"code-review discovery failed: {error}\n"
            "  Reference: skills/code-review/references/review-rubric.md · skills/code-review/references/output-contract.md · skills/code-review/references/reviewer-prompts.md",
            file=sys.stderr,
        )
        raise SystemExit(2)
