#!/usr/bin/env python3
"""Cross-Repo Gate, Slow Script & Hook Drift Detector (`repo-kit`).

Detects 6 classes of cross-repo verification/gate drift and speed bottlenecks:
  - RG-001 (missing_verify_entrypoint): Repo has CI workflows or git hooks (.githooks / scripts/hooks)
            or product packages/sources but lacks an executable `scripts/verify`.
  - RG-002 (missing_incremental_verify): `scripts/verify` exists but lacks incremental path-scoped
            selection (`--changed`, `VERIFY_BASE`, `git diff --name-only`, or `detect_changed_files`).
  - RG-003 (serial_layer_or_suite_runner): `scripts/verify` or gate script executes layers/suites in a
            serial loop (`for layer in $layers; do ... context run "$layer"` instead of passing all
            `$layers` at once to `context run $layers` which runs `_topological_waves` + `ThreadPoolExecutor`,
            or running multiple test suites sequentially without a worker pool).
  - RG-004 (prepush_forces_full_or_bypasses_verify): Active `pre-push` hook unconditionally forces
            `scripts/verify --all` (without a `VERIFY_ALL=1` guard) or fails to invoke `scripts/verify`
            when `scripts/verify` exists.
  - RG-005 (hook_path_or_ci_drift): `git config core.hooksPath` is unset or points to a directory where
            `pre-push` is missing/non-executable while `.githooks/pre-push` or `scripts/hooks/pre-push`
            exists, or `.github/workflows/ci.yml` does not invoke `scripts/verify` / `shared-ci/quality.yml`.
  - RG-006 (shared_ci_pin_drift): Repo pins an outdated `shared-ci` SHA compared to local `shared-ci` HEAD
            or uses legacy inline `AGENTS.md` pin without `.github/repo-contract.json`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

DEFAULT_DEV_ROOT = Path.home() / "Development"
REFERENCE_DOC = "skills/repo-kit/references/repo-bound-skills-and-gates.md"

RULE_METADATA: dict[str, dict[str, str]] = {
    "RG-001": {
        "category": "missing_verify_entrypoint",
        "severity": "P1",
        "speedup_angle": "angle_d_tiered_fast_fail",
        "title": "Missing executable `scripts/verify` entrypoint",
    },
    "RG-002": {
        "category": "missing_incremental_verify",
        "severity": "P1",
        "speedup_angle": "angle_a_path_scoped",
        "title": "`scripts/verify` lacks incremental path-scoped selection",
    },
    "RG-003": {
        "category": "serial_layer_or_suite_runner",
        "severity": "P2",
        "speedup_angle": "angle_c_parallel_dag",
        "title": "Serial layer or test-suite loop in `scripts/verify` / hook",
    },
    "RG-004": {
        "category": "prepush_forces_full_or_bypasses_verify",
        "severity": "P1",
        "speedup_angle": "angle_a_path_scoped",
        "title": "`pre-push` forces `--all` or bypasses `scripts/verify`",
    },
    "RG-005": {
        "category": "hook_path_or_ci_drift",
        "severity": "P2",
        "speedup_angle": "angle_d_tiered_fast_fail",
        "title": "`core.hooksPath` or `.github/workflows/ci.yml` drift",
    },
    "RG-006": {
        "category": "shared_ci_pin_drift",
        "severity": "P2",
        "speedup_angle": "angle_b_watermark_cache",
        "title": "Outdated `shared-ci` SHA pin or missing `.github/repo-contract.json`",
    },
}

RULE_PENALTIES: dict[str, int] = {
    "RG-001": 30,
    "RG-002": 20,
    "RG-003": 15,
    "RG-004": 20,
    "RG-005": 15,
    "RG-006": 10,
}

_SKIP_DEV_DIRS = {
    "handoff",
    "handoffs",
    "worktrees",
    "worktree-archives",
    "artifacts",
    "workflow-evidence",
    ".ruff_cache",
}

_USER_HOME_PREFIX = "/" + "Users/"
_LINUX_HOME_PREFIX = "/" + "home/"
_USER_HOME_PATH_RE = re.compile(
    r"(?:" + _USER_HOME_PREFIX + r"|" + _LINUX_HOME_PREFIX + r")[^/\s\"'<>`|)]+"
)

_SERIAL_CONTEXT_LOOP_RE = re.compile(
    r"(?:for\s+\w+\s+in\s+[^;\n]+;\s*do|while\s+[^\n]*read\s+[^\n]*;\s*do)[\s\S]{0,260}?"
    r"(?:context\s+run|run_layer|scripts/context/run|\$\{?context\[@\]\}?\s+run)\b",
    re.MULTILINE,
)
_INCREMENTAL_VERIFY_RE = re.compile(
    r"(?:--changed\b|VERIFY_BASE\b|git\s+diff\s+--name-only|detect_changed_files|verify_runner\.py)",
    re.MULTILINE,
)
_PREPUSH_FORCE_ALL_RE = re.compile(
    r"^\s*(?:exec\s+)?(?:\"\$REPO_ROOT/|\$\(git rev-parse --show-toplevel\)/|\./)?scripts/verify\"?\s+--all\b",
    re.MULTILINE,
)
_PREPUSH_CALLS_VERIFY_RE = re.compile(r"(?:^|[\s\"'/])scripts/verify\b", re.MULTILINE)
_SHARED_CI_PIN_RE = re.compile(r"shared-ci(?:@|/blob/|/[^@\s]+@)([0-9a-f]{7,40})")


class _SelfDisclosingRepoKitParser(argparse.ArgumentParser):
    """ArgumentParser that prints a <=3 line fix + pointer to repo-bound-skills-and-gates.md on exit=2."""

    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[repo-kit] CLI error: {message}\n"
            "  Fix: run `python3 skills/repo-kit/scripts/repo_gate_auditor.py {scan,bottlenecks,loop-check} [--repo <dir> | --dev-root <dir>] --json`\n"
            f"  Reference: {REFERENCE_DOC} | skills/repo-kit/references/eval-cases.json\n"
        )
        raise SystemExit(2)


def _redact_path(raw: str | Path, *, dev_root: Path | None = None) -> str:
    text = str(raw or "")
    home_str = str(Path.home())
    if dev_root is not None:
        for cand in {str(dev_root), str(dev_root.resolve())}:
            if cand and cand != "/":
                text = text.replace(cand, "~/Development")
    if home_str and home_str != "/":
        text = text.replace(home_str, "~")
    text = _USER_HOME_PATH_RE.sub("~", text)
    return text


def _display_rel_path(path: Path | None, repo_dir: Path, *, dev_root: Path | None = None) -> str:
    """Safely format a path relative to repo_dir, falling back without raising ValueError when outside repo_dir."""
    if path is None:
        return "pre-push"
    for cand_path, cand_repo in ((path, repo_dir), (path.resolve(), repo_dir.resolve())):
        try:
            return cand_path.relative_to(cand_repo).as_posix()
        except ValueError:
            continue
    if path.parent.name:
        return f"{path.parent.name}/{path.name}"
    return _redact_path(path.name or str(path), dev_root=dev_root)


def _is_git_repo(path: Path) -> bool:
    try:
        return path.is_dir() and ((path / ".git").is_dir() or (path / ".git").is_file())
    except OSError:
        return False


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _git_config_hooks_path(repo_dir: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo_dir), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    cfg = _read_text(repo_dir / ".git" / "config")
    m = re.search(r"(?m)^\s*hooksPath\s*=\s*(\S+)", cfg)
    return m.group(1).strip() if m else ""


def _resolve_shared_ci_head(dev_root: Path | None) -> str:
    if dev_root is None:
        return ""
    shared_ci_dir = dev_root / "shared-ci"
    if not _is_git_repo(shared_ci_dir):
        return ""
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(shared_ci_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            if re.fullmatch(r"[0-9a-f]{40}", sha):
                return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _strip_shell_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _discover_target_repos(
    *,
    repo: Path | None = None,
    dev_root: Path | None = None,
    repos_csv: str | Sequence[str] | None = None,
) -> tuple[list[Path], Path]:
    if repo is not None:
        resolved_repo = repo.expanduser().resolve()
        inferred_dev_root = dev_root.expanduser().resolve() if dev_root is not None else resolved_repo.parent
        return [resolved_repo], inferred_dev_root

    eff_dev_root = (dev_root or DEFAULT_DEV_ROOT).expanduser().resolve()
    filter_names: list[str] = []
    if isinstance(repos_csv, str):
        filter_names = [x.strip() for x in re.split(r"[,;\s]+", repos_csv) if x.strip()]
    elif isinstance(repos_csv, Sequence):
        for item in repos_csv:
            for x in re.split(r"[,;\s]+", str(item)):
                if x.strip():
                    filter_names.append(x.strip())

    if filter_names:
        selected: list[Path] = []
        for name in filter_names:
            cand = Path(name).expanduser()
            if not cand.is_absolute():
                cand = eff_dev_root / name
            if cand.is_dir():
                selected.append(cand.resolve())
        return selected, eff_dev_root

    repos: list[Path] = []
    if not eff_dev_root.is_dir():
        return repos, eff_dev_root
    try:
        entries = sorted(eff_dev_root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return repos, eff_dev_root

    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SKIP_DEV_DIRS:
            continue
        if entry.is_symlink():
            continue
        if _is_git_repo(entry):
            repos.append(entry)
    return repos, eff_dev_root


def _extract_shared_ci_pin_info(repo_dir: Path) -> dict[str, Any]:
    contract_json = repo_dir / ".github" / "repo-contract.json"
    has_contract_json = contract_json.is_file()
    pinned_sha = ""
    pin_source = None

    if has_contract_json:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            data = json.loads(_read_text(contract_json))
            if isinstance(data, Mapping) and isinstance(data.get("shared_ci"), str):
                pinned_sha = data["shared_ci"].strip()
                pin_source = ".github/repo-contract.json"

    shared_ci_file = repo_dir / ".shared-ci"
    if not pinned_sha and shared_ci_file.is_file():
        m = re.search(r"\b([0-9a-f]{40})\b", _read_text(shared_ci_file))
        if m:
            pinned_sha = m.group(1)
            pin_source = ".shared-ci"

    agents_md = repo_dir / "AGENTS.md"
    has_legacy_agents_pin = False
    if agents_md.is_file():
        m_ag = _SHARED_CI_PIN_RE.search(_read_text(agents_md))
        if m_ag:
            has_legacy_agents_pin = True
            if not pinned_sha:
                pinned_sha = m_ag.group(1)
                pin_source = "AGENTS.md"

    verify_script = repo_dir / "scripts" / "verify"
    if not pinned_sha and verify_script.is_file():
        v_txt = _read_text(verify_script)
        m_v = re.search(r"\bpin=['\"]?([0-9a-f]{40})['\"]?", v_txt) or _SHARED_CI_PIN_RE.search(v_txt)
        if m_v:
            pinned_sha = m_v.group(1)
            pin_source = "scripts/verify"

    wf_dir = repo_dir / ".github" / "workflows"
    if not pinned_sha and wf_dir.is_dir():
        for wf in sorted(wf_dir.glob("*.yml")):
            m_wf = _SHARED_CI_PIN_RE.search(_read_text(wf))
            if m_wf:
                pinned_sha = m_wf.group(1)
                pin_source = f".github/workflows/{wf.name}"
                break

    return {
        "pinned_sha": pinned_sha,
        "pin_source": pin_source,
        "has_repo_contract_json": has_contract_json,
        "has_legacy_agents_pin": has_legacy_agents_pin,
    }


def inspect_single_repo(
    repo_dir: Path,
    *,
    dev_root: Path | None = None,
    shared_ci_head: str = "",
) -> dict[str, Any]:
    """Audit a single repository for RG-001..RG-006 gate drift & 4-Angle speedup opportunities."""
    repo_name = repo_dir.name
    verify_path = repo_dir / "scripts" / "verify"
    verify_runner_py = repo_dir / "scripts" / "verify_runner.py"
    has_verify_file = verify_path.is_file()
    has_verify_exec = _is_executable(verify_path)
    verify_raw = _read_text(verify_path) if has_verify_file else ""
    verify_code = _strip_shell_comments(verify_raw)
    if verify_runner_py.is_file():
        verify_code_with_runner = verify_code + "\n" + _read_text(verify_runner_py)
    else:
        verify_code_with_runner = verify_code

    ci_yml = repo_dir / ".github" / "workflows" / "ci.yml"
    has_ci_yml = ci_yml.is_file()
    ci_raw = _read_text(ci_yml) if has_ci_yml else ""

    githooks_prepush = repo_dir / ".githooks" / "pre-push"
    scripts_hooks_prepush = repo_dir / "scripts" / "hooks" / "pre-push"
    tracked_prepush_candidates = [
        p for p in (githooks_prepush, scripts_hooks_prepush) if p.is_file()
    ]
    hooks_path_cfg = _git_config_hooks_path(repo_dir)
    active_prepush: Path | None = None
    if hooks_path_cfg:
        cfg_dir = Path(hooks_path_cfg) if Path(hooks_path_cfg).is_absolute() else (repo_dir / hooks_path_cfg)
        cand_prepush = cfg_dir / "pre-push"
        if cand_prepush.is_file():
            active_prepush = cand_prepush
    if active_prepush is None and tracked_prepush_candidates:
        active_prepush = tracked_prepush_candidates[0]

    prepush_raw = _read_text(active_prepush) if active_prepush is not None else ""
    prepush_code = _strip_shell_comments(prepush_raw)

    has_product_or_gate_surface = bool(
        has_ci_yml
        or tracked_prepush_candidates
        or (repo_dir / "Packages").is_dir()
        or (repo_dir / "Sources").is_dir()
        or (repo_dir / "skills").is_dir()
        or (repo_dir / "scripts").is_dir()
    )

    findings: list[dict[str, Any]] = []

    def add_finding(rule_id: str, evidence: str, remediation: str) -> None:
        meta = RULE_METADATA[rule_id]
        findings.append(
            {
                "rule_id": rule_id,
                "category": meta["category"],
                "severity": meta["severity"],
                "speedup_angle": meta["speedup_angle"],
                "repo": repo_name,
                "title": meta["title"],
                "evidence": evidence,
                "remediation": remediation,
                "reference": REFERENCE_DOC,
            }
        )

    # RG-001: missing_verify_entrypoint
    if has_product_or_gate_surface and not has_verify_exec:
        detail = (
            "`scripts/verify` exists but is not executable (`chmod +x scripts/verify` required)"
            if has_verify_file
            else "Repo has CI/hooks/code surface but lacks `scripts/verify` entrypoint"
        )
        add_finding(
            "RG-001",
            detail,
            "Create executable `scripts/verify` supporting `[--changed|--all]` and wire `.githooks/pre-push` + CI to it.",
        )

    # RG-002: missing_incremental_verify
    has_incremental_verify = False
    if has_verify_file:
        has_incremental_verify = bool(_INCREMENTAL_VERIFY_RE.search(verify_code_with_runner))
        if not has_incremental_verify:
            add_finding(
                "RG-002",
                "`scripts/verify` does not reference `--changed`, `VERIFY_BASE`, `git diff --name-only`, or `detect_changed_files`",
                "Add incremental path-scoped selection (`git diff --name-only` / `context layers --stdin` or `detect_changed_files`) as default mode.",
            )

    # RG-003: serial_layer_or_suite_runner
    serial_verify_loop = bool(has_verify_file and _SERIAL_CONTEXT_LOOP_RE.search(verify_code))
    serial_hook_loop = bool(active_prepush is not None and _SERIAL_CONTEXT_LOOP_RE.search(prepush_code))
    serial_unittest_calls = len(re.findall(r"(?m)^\s*(?:python3|python|\$python)\b[^\n]*\bunittest\b", verify_code))
    has_parallel_pool = bool(
        re.search(r"ThreadPoolExecutor|ProcessPoolExecutor|concurrent\.futures|xargs\s+-P", verify_code_with_runner)
    )
    if serial_verify_loop or serial_hook_loop or (serial_unittest_calls >= 3 and not has_parallel_pool):
        where = (
            "scripts/verify"
            if (serial_verify_loop or serial_unittest_calls >= 3)
            else _display_rel_path(active_prepush, repo_dir, dev_root=dev_root)
        )
        add_finding(
            "RG-003",
            f"`{where}` runs layers/suites in a serial loop instead of batch topological wave or parallel worker pool",
            "Pass all layers at once (`context run $layers --mode \"$mode\"`) or dispatch suites via `ThreadPoolExecutor`.",
        )

    # RG-004: prepush_forces_full_or_bypasses_verify
    if active_prepush is not None:
        prepush_label = _display_rel_path(active_prepush, repo_dir, dev_root=dev_root)
        forces_all = bool(_PREPUSH_FORCE_ALL_RE.search(prepush_code)) and "VERIFY_ALL" not in prepush_code
        calls_verify = bool(_PREPUSH_CALLS_VERIFY_RE.search(prepush_code))
        if forces_all:
            add_finding(
                "RG-004",
                f"`{prepush_label}` unconditionally runs `scripts/verify --all` on every push",
                "Run `scripts/verify --all` only when `VERIFY_ALL=1` is set; default to incremental `exec \"$REPO_ROOT/scripts/verify\"`.",
            )
        elif has_verify_exec and not calls_verify:
            add_finding(
                "RG-004",
                f"`{prepush_label}` bypasses `scripts/verify` despite `scripts/verify` existing",
                "Delegate `pre-push` verification to `exec \"$REPO_ROOT/scripts/verify\"` so local and CI gates never drift.",
            )

    # RG-005: hook_path_or_ci_drift
    hook_drift_reasons: list[str] = []
    if tracked_prepush_candidates:
        expected_dirs = {_display_rel_path(p.parent, repo_dir, dev_root=dev_root) for p in tracked_prepush_candidates}
        norm_cfg = hooks_path_cfg.rstrip("/")
        if not norm_cfg:
            hook_drift_reasons.append(
                f"`core.hooksPath` is unset while `{_display_rel_path(tracked_prepush_candidates[0], repo_dir, dev_root=dev_root)}` exists"
            )
        else:
            cfg_dir = Path(norm_cfg) if Path(norm_cfg).is_absolute() else (repo_dir / norm_cfg)
            cfg_prepush = cfg_dir / "pre-push"
            if not _is_executable(cfg_prepush):
                hook_drift_reasons.append(
                    f"`core.hooksPath={_redact_path(norm_cfg, dev_root=dev_root)}` has no executable `pre-push` while `{sorted(expected_dirs)[0]}/pre-push` exists"
                )
    if has_ci_yml and has_verify_exec:
        ci_calls_verify_or_shared = bool(
            re.search(r"scripts/verify\b|shared-ci/\.github/workflows/quality\.yml|verify_runner\.py", ci_raw)
        )
        if not ci_calls_verify_or_shared:
            hook_drift_reasons.append("`.github/workflows/ci.yml` does not invoke `scripts/verify` or `shared-ci/quality.yml`")
    if hook_drift_reasons:
        add_finding(
            "RG-005",
            "; ".join(hook_drift_reasons),
            "Set `git config core.hooksPath .githooks` (or `scripts/hooks`) and ensure `.github/workflows/ci.yml` invokes `scripts/verify` / `shared-ci/quality.yml`.",
        )

    # RG-006: shared_ci_pin_drift
    pin_info = _extract_shared_ci_pin_info(repo_dir)
    pinned_sha = pin_info["pinned_sha"]
    pin_drift_reasons: list[str] = []
    if pinned_sha and repo_name != "shared-ci":
        if shared_ci_head and not shared_ci_head.startswith(pinned_sha) and not pinned_sha.startswith(shared_ci_head):
            pin_drift_reasons.append(
                f"pinned `shared-ci@{pinned_sha[:12]}` ({pin_info['pin_source']}) lags `shared-ci` HEAD `{shared_ci_head[:12]}`"
            )
        if pin_info["has_legacy_agents_pin"] and not pin_info["has_repo_contract_json"]:
            pin_drift_reasons.append(
                "uses legacy inline `AGENTS.md` `shared-ci` pin without `.github/repo-contract.json`"
            )
    if pin_drift_reasons:
        add_finding(
            "RG-006",
            "; ".join(pin_drift_reasons),
            "Record `shared_ci` SHA in `.github/repo-contract.json` and align workflow callers to current `shared-ci` HEAD.",
        )

    deducted = sum(RULE_PENALTIES.get(f["rule_id"], 10) for f in findings)
    health_score = max(0, 100 - deducted)
    triggered_rules = [f["rule_id"] for f in findings]

    has_watermark = bool(
        re.search(r"watermark|content[_-]hash|sha256|cache", verify_code_with_runner, re.IGNORECASE)
        or pin_info["has_repo_contract_json"]
    )
    speedup_recommendations = {
        "angle_a_path_scoped": (
            "configured"
            if (has_incremental_verify and "RG-002" not in triggered_rules and "RG-004" not in triggered_rules)
            else "Scope default `scripts/verify` and `pre-push` to `git diff --name-only` changed layers/skills; reserve `--all` for `VERIFY_ALL=1` or shared root edits."
        ),
        "angle_b_watermark_cache": (
            "configured"
            if (has_watermark and "RG-006" not in triggered_rules)
            else "Pin deterministic `.github/repo-contract.json` SHA and cache unchanged layer/artifact hashes."
        ),
        "angle_c_parallel_dag": (
            "configured"
            if (has_parallel_pool or (has_verify_file and "RG-003" not in triggered_rules))
            else "Replace serial `for layer in $layers; do context run \"$layer\"` loops with single-call `context run $layers` or `ThreadPoolExecutor`."
        ),
        "angle_d_tiered_fast_fail": (
            "configured"
            if (has_verify_exec and "RG-001" not in triggered_rules and "RG-005" not in triggered_rules)
            else "Enforce Tier-0 syntax/contract check (<2s) before Tier-1 unit tests in `scripts/verify`, wired identically in `core.hooksPath` and CI."
        ),
    }

    return {
        "repo": repo_name,
        "repo_path": _redact_path(repo_dir, dev_root=dev_root),
        "gate_health_score": health_score,
        "status": "healthy" if not findings else ("degraded" if health_score >= 60 else "critical"),
        "has_verify_entrypoint": has_verify_exec,
        "has_incremental_verify": has_incremental_verify,
        "has_parallel_runner": has_parallel_pool or (has_verify_file and "RG-003" not in triggered_rules),
        "hooks_path": hooks_path_cfg or None,
        "shared_ci_pin": pinned_sha[:12] if pinned_sha else None,
        "has_repo_contract_json": pin_info["has_repo_contract_json"],
        "triggered_rules": triggered_rules,
        "findings_count": len(findings),
        "findings": findings,
        "speedup_recommendations": speedup_recommendations,
    }


def _synthesize_actionable_proposals(
    repo_reports: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_rule: dict[str, list[Mapping[str, Any]]] = {}
    for rep in repo_reports:
        for f in rep.get("findings") or []:
            if isinstance(f, Mapping):
                by_rule.setdefault(str(f["rule_id"]), []).append(f)

    proposals: list[dict[str, Any]] = []
    for rule_id in sorted(by_rule.keys()):
        items = by_rule[rule_id]
        meta = RULE_METADATA[rule_id]
        affected_repos = sorted({str(x.get("repo") or "") for x in items if x.get("repo")})
        sample_ev = str(items[0].get("evidence") or "")
        sample_rem = str(items[0].get("remediation") or "")
        proposals.append(
            {
                "id": f"skill-retro-repo-kit-{rule_id.lower()}",
                "ref": f"retro://skill-retro/repo-kit/{rule_id.lower()}",
                "metric_id": f"repo-gate-{rule_id.lower()}",
                "rule_id": rule_id,
                "layer": "L2",
                "category": meta["category"],
                "skill": "repo-kit",
                "priority": meta["severity"],
                "speedup_angle": meta["speedup_angle"],
                "affected_repos": affected_repos,
                "affected_repos_count": len(affected_repos),
                "title": f"[Repo Gate {rule_id}] {meta['title']} ({len(affected_repos)} repo(s): {', '.join(affected_repos[:4])})",
                "detail": (
                    f"rule={rule_id} | category={meta['category']} | skill=repo-kit | "
                    f"repos={','.join(affected_repos)} | angle={meta['speedup_angle']} | sample={sample_ev}"
                ),
                "action": sample_rem,
                "plain_reason": (
                    f"跨 {len(affected_repos)} 个仓库（{', '.join(affected_repos[:3])}）检测到 `{rule_id}` ({meta['category']})，"
                    f"可通过 `{meta['speedup_angle']}` 与 `repo-kit` 模板统一收敛"
                ),
            }
        )
    return proposals


def scan_repositories(
    *,
    repo: Path | None = None,
    dev_root: Path | None = None,
    repos: str | Sequence[str] | None = None,
    flow_map: bool = False,
) -> dict[str, Any]:
    """Scan one repository or all repositories under dev_root for RG-001..RG-006."""
    target_repos, eff_dev_root = _discover_target_repos(repo=repo, dev_root=dev_root, repos_csv=repos)
    shared_ci_head = _resolve_shared_ci_head(eff_dev_root)
    reports: list[dict[str, Any]] = []
    scan_errors: list[dict[str, str]] = []
    for r in target_repos:
        try:
            reports.append(
                inspect_single_repo(r, dev_root=eff_dev_root, shared_ci_head=shared_ci_head)
            )
        except Exception as exc:
            scan_errors.append(
                {
                    "repo": r.name,
                    "error": f"{type(exc).__name__}: {_redact_path(str(exc), dev_root=eff_dev_root)}",
                }
            )
            reports.append(
                {
                    "repo": r.name,
                    "repo_path": _redact_path(r, dev_root=eff_dev_root),
                    "gate_health_score": 100,
                    "status": "scan_error",
                    "scan_error": f"{type(exc).__name__}: {_redact_path(str(exc), dev_root=eff_dev_root)}",
                    "has_verify_entrypoint": False,
                    "has_incremental_verify": False,
                    "has_parallel_runner": False,
                    "hooks_path": None,
                    "shared_ci_pin": None,
                    "has_repo_contract_json": False,
                    "triggered_rules": [],
                    "findings_count": 0,
                    "findings": [],
                    "speedup_recommendations": {
                        "angle_a_path_scoped": "configured",
                        "angle_b_watermark_cache": "configured",
                        "angle_c_parallel_dag": "configured",
                        "angle_d_tiered_fast_fail": "configured",
                    },
                }
            )
    all_findings = [f for r in reports for f in r["findings"]]
    rule_counts: dict[str, int] = {rid: 0 for rid in RULE_METADATA}
    for f in all_findings:
        rule_counts[f["rule_id"]] = rule_counts.get(f["rule_id"], 0) + 1

    avg_score = round(sum(r["gate_health_score"] for r in reports) / max(1, len(reports)), 1) if reports else 100.0
    actionable_proposals = _synthesize_actionable_proposals(reports)

    payload: dict[str, Any] = {
        "ok": True,
        "schema": "repo-gate-audit/v1",
        "skill": "repo-kit",
        "dev_root": _redact_path(eff_dev_root, dev_root=eff_dev_root),
        "shared_ci_head": shared_ci_head[:12] if shared_ci_head else None,
        "repos_scanned": len(reports),
        "healthy_repos_count": sum(1 for r in reports if r["findings_count"] == 0 and r.get("status") != "scan_error"),
        "drifted_repos_count": sum(1 for r in reports if r["findings_count"] > 0),
        "scan_errors_count": len(scan_errors),
        "scan_errors": scan_errors,
        "average_gate_health_score": avg_score,
        "total_findings": len(all_findings),
        "rule_counts": rule_counts,
        "speedup_4_angles": {
            "angle_a_path_scoped": sum(1 for f in all_findings if f["speedup_angle"] == "angle_a_path_scoped"),
            "angle_b_watermark_cache": sum(1 for f in all_findings if f["speedup_angle"] == "angle_b_watermark_cache"),
            "angle_c_parallel_dag": sum(1 for f in all_findings if f["speedup_angle"] == "angle_c_parallel_dag"),
            "angle_d_tiered_fast_fail": sum(1 for f in all_findings if f["speedup_angle"] == "angle_d_tiered_fast_fail"),
        },
        "repos": reports,
        "actionable_proposals": actionable_proposals,
        "reference": REFERENCE_DOC,
    }
    if flow_map:
        payload["flow_map"] = {
            "stages": [
                "1. repo_gate_auditor.py scan/bottlenecks -> detect RG-001..RG-006 + 4-Angle speedup recommendations",
                "2. skill_retro.py probe/triage/diagnose --skill repo-kit -> ingest cross_repo_gate_health & actionable_proposals",
                "3. M1-TL -> Writer -> Reviewer -> apply repo-kit templates (.githooks/pre-push, scripts/verify, .github/repo-contract.json)",
            ]
        }
    return payload


scan_repos = scan_repositories


def build_bottlenecks_report(
    *,
    repo: Path | None = None,
    dev_root: Path | None = None,
    repos: str | Sequence[str] | None = None,
    flow_map: bool = False,
) -> dict[str, Any]:
    base = scan_repositories(repo=repo, dev_root=dev_root, repos=repos, flow_map=flow_map)
    bottleneck_repos = [
        r for r in base["repos"] if any(rid in r["triggered_rules"] for rid in ("RG-002", "RG-003", "RG-004"))
    ]
    return {
        **base,
        "subcommand": "bottlenecks",
        "bottleneck_repos_count": len(bottleneck_repos),
        "bottleneck_repos": bottleneck_repos,
    }


def build_loop_check_report(
    *,
    repo: Path | None = None,
    dev_root: Path | None = None,
    repos: str | Sequence[str] | None = None,
    flow_map: bool = True,
) -> dict[str, Any]:
    base = scan_repositories(repo=repo, dev_root=dev_root, repos=repos, flow_map=flow_map)
    return {
        **base,
        "subcommand": "loop-check",
        "self_driven_loop_ready": True,
        "target_skill": "repo-kit",
        "next_stage_commands": [
            "python3 skills/skill-retro/scripts/skill_retro.py probe --skill repo-kit --fast --json",
            "python3 skills/skill-retro/scripts/skill_retro.py diagnose --skill repo-kit --mode full --json",
        ],
    }


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        "--repo-dir",
        "--worktree",
        dest="repo",
        type=Path,
        default=None,
        help="Single target repository or worktree directory to audit.",
    )
    parser.add_argument(
        "--dev-root",
        dest="dev_root",
        type=Path,
        default=None,
        help="Development root containing multiple repositories (default ~/Development).",
    )
    parser.add_argument(
        "--repos",
        dest="repos",
        default=None,
        help="Comma-separated repository names under --dev-root to scan.",
    )
    parser.add_argument(
        "--flow-map",
        dest="flow_map",
        action="store_true",
        default=False,
        help="Include end-to-end repo-kit -> skill-retro self-driven loop flow map.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit 1 when any RG-001..RG-006 finding is detected.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Output structured JSON report (default True).",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    valid_subcommands = {"scan", "bottlenecks", "loop-check"}

    if not args_list or args_list[0] in {"-h", "--help"}:
        top = _SelfDisclosingRepoKitParser(
            prog="repo_gate_auditor.py",
            description="Deterministic Cross-Repo Gate, Slow Script & Hook Drift Detector (RG-001..RG-006).",
        )
        top.add_argument(
            "command",
            nargs="?",
            choices=sorted(valid_subcommands),
            help="Subcommand: scan | bottlenecks | loop-check (defaults to scan when flags are passed directly).",
        )
        _add_common_args(top)
        top.parse_args(args_list)
        out = scan_repositories()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    first = args_list[0]
    if first.startswith("-"):
        subcmd = "scan"
        rest = args_list
    elif first in valid_subcommands:
        subcmd = first
        rest = args_list[1:]
    else:
        sys.stderr.write(
            f"[repo-kit] Unknown subcommand: {first!r} (choose from: scan, bottlenecks, loop-check)\n"
            "  Fix: run `python3 skills/repo-kit/scripts/repo_gate_auditor.py {scan,bottlenecks,loop-check} [--repo <dir> | --dev-root <dir>] --json`\n"
            f"  Reference: {REFERENCE_DOC}\n"
        )
        raise SystemExit(2)

    sub_parser = _SelfDisclosingRepoKitParser(prog=f"repo_gate_auditor.py {subcmd}")
    _add_common_args(sub_parser)
    ns = sub_parser.parse_args(rest)

    if subcmd == "bottlenecks":
        report = build_bottlenecks_report(
            repo=ns.repo,
            dev_root=ns.dev_root,
            repos=ns.repos,
            flow_map=ns.flow_map,
        )
    elif subcmd == "loop-check":
        report = build_loop_check_report(
            repo=ns.repo,
            dev_root=ns.dev_root,
            repos=ns.repos,
            flow_map=True if not ns.flow_map else ns.flow_map,
        )
    else:
        report = scan_repositories(
            repo=ns.repo,
            dev_root=ns.dev_root,
            repos=ns.repos,
            flow_map=ns.flow_map,
        )

    if report["total_findings"] > 0 and ns.strict:
        sys.stderr.write(
            f"[repo-kit] {report['total_findings']} cross-repo gate finding(s) detected across {report['drifted_repos_count']} repo(s).\n"
            "  Fix: apply `repo-kit` incremental verify, batch layer runner, or `core.hooksPath` remediation.\n"
            f"  Reference: {REFERENCE_DOC}\n"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if (ns.strict and report["total_findings"] > 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
