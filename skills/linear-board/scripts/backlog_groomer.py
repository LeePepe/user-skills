#!/usr/bin/env python3
"""Loop 3B: Backlog Grooming, Deduplication & Daily Top-3 Push Loop (`backlog_groomer.py`).

Implements the 3-Stage Loop 3B Contract:
1. Stage 1 (Layer 0 — Deterministic Backlog Scan & Fingerprint Deduplication):
   - Scans open `Backlog` / `Todo` issues across Multica (`multica issue list --limit 100 --offset N --output json`)
     and Linear TIA (canonical `linear-sync.py` GraphQLClient, or hermetic `--issues-json`).
   - Excludes closed/completed/canceled tickets, Daily Brief/Daily Learn/M2-Sync containers,
     and non-personal Work Business / Bing / Workplace scopes.
   - Computes a deterministic normalized fingerprint (`problem_fingerprint` + token Jaccard
     and title/ref key normalization) to group duplicate or overlapping Backlog/Todo items
     into `duplicate_clusters`, selecting a canonical survivor issue and merge/close recommendations.
2. Stage 2 (Layer 1 — Jev Fast-Thinking Readiness & ROI Scoring via `jev_client.py`):
   - Evaluates each deduplicated candidate via `jev_decide()` (`choice+noul` for dependency
     readiness + `score` for ROI/impact priority, with `--mock-jev-json` and deterministic
     multi-factor fallback scoring based on priority, age, blocker readiness, and ROI tags).
   - Selects the Daily Top 3 Recommended Push Items (`top3_push_candidates` / `top3_recommendations`).
3. Stage 3 (Layer 2 — Daily Brief Top-3 Injection & `multica_loop_runner.py` Integration):
   - Writes groomed Top-3 push candidates to `~/.local/share/linear-board/top3-groomed.json`
     (or `--output` / `LINEAR_BACKLOG_GROOMER_STATE_FILE`) and emits a Dual-Zone (`issue-brief`
     verified) collector payload for `multica_loop_runner.py` (`backlog-grooming-and-push`).
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
LINEAR_BOARD_SCRIPTS_DIR = SCRIPT_PATH.parent
SKILLS_ROOT = SCRIPT_PATH.parents[2]
TEAM_LEAD_SCRIPTS_DIR = SKILLS_ROOT / "team-lead" / "scripts"
LINEAR_SYNC_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "linear-sync.py"
JEV_CLIENT_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "jev_client.py"
ISSUE_BRIEF_SCRIPT_PATH = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"
DAILY_BRIEF_SCRIPT_PATH = LINEAR_BOARD_SCRIPTS_DIR / "daily-brief.py"

LOOP_ID = "backlog-grooming-and-push"
CST = timezone(timedelta(hours=8))

MULTICA_DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
MULTICA_DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
MULTICA_OWNER_EMAIL = "lee@multica.local"

EXCLUDED_TERMS: tuple[str, ...] = ("work business", "bing", "workplace")
OPEN_BACKLOG_STATES: frozenset[str] = frozenset(
    {"backlog", "todo", "triage", "unstarted", "open", "queued"}
)
CLOSED_STATES: frozenset[str] = frozenset(
    {"done", "completed", "canceled", "cancelled", "archived", "duplicate"}
)
ACTIVE_IN_PROGRESS_STATES: frozenset[str] = frozenset(
    {"in progress", "in_progress", "inprogress", "started", "in review", "in_review"}
)

STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "for",
        "of",
        "in",
        "on",
        "with",
        "by",
        "at",
        "from",
        "into",
        "is",
        "are",
        "be",
        "as",
        "goal",
        "task",
        "todo",
        "backlog",
        "issue",
        "ticket",
        "feat",
        "fix",
        "add",
        "update",
        "implement",
        "support",
        "的",
        "与",
        "和",
        "及",
        "或",
        "在",
        "把",
        "将",
        "对",
        "为",
        "了",
        "并",
        "支持",
        "实现",
        "新增",
        "优化",
        "更新",
        "修复",
        "任务",
        "单据",
        "待办",
        "问题",
    }
)

BRACKET_TAG_RE = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+")
ISSUE_ID_RE = re.compile(r"\b(?:TIA|MUL)-\d+\b", re.IGNORECASE)
PR_REF_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+\b")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]{2,}")
BLOCKER_RE = re.compile(
    r"(?i)\b(?:blocked\s+by|depends\s+on|waiting\s+on|waiting\s+for|blocked|hold|need_go|need_owner_decision|needs\s+decision|owner\s+decision|task_blocked|input_blocked|review_fail)\b"
    r"|阻塞|等待前置|依赖未就绪|待拍板|待选型|暂缓"
    r"|(?:demo|gallery|选型)[^\n.!。]{0,100}(?:awaiting|waiting\s+for|pending|待拍板|待选|待你选择)"
    r"|(?:awaiting|waiting\s+for|pending|待拍板|待选)[^\n.!。]{0,100}(?:demo|gallery|选型)"
)
HIGH_ROI_RE = re.compile(
    r"(?i)\b(?:roi|automate|automation|loop|dedup|deduplicate|groom|hygiene|verify|gate|guardrail|telemetry|autopilot|zero-token|0-agent)\b"
    r"|自动|去重|巡检|门禁|防呆|提效|闭环|顺推|复用"
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_state_path() -> Path:
    override = os.environ.get("LINEAR_BACKLOG_GROOMER_STATE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "linear-board" / "top3-groomed.json"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    old_flag = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_flag
    return module


def _load_linear_sync() -> Any:
    return _load_module("backlog_groomer_linear_sync", LINEAR_SYNC_SCRIPT_PATH)


def _load_jev_client() -> Any:
    return _load_module("backlog_groomer_jev_client", JEV_CLIENT_SCRIPT_PATH)


def _load_issue_brief() -> Any:
    return _load_module("backlog_groomer_issue_brief", ISSUE_BRIEF_SCRIPT_PATH)


def _load_daily_brief() -> Any:
    return _load_module("backlog_groomer_daily_brief", DAILY_BRIEF_SCRIPT_PATH)


def _resolve_multica_bin(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env_bin = os.environ.get("MULTICA_BIN")
    if env_bin:
        return env_bin
    if Path("/opt/homebrew/bin/multica").is_file():
        return "/opt/homebrew/bin/multica"
    return shutil.which("multica") or "/opt/homebrew/bin/multica"


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=merged_env,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def parse_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    raw = str(value or "today").strip()
    if not raw or raw.lower() == "today":
        return datetime.now(CST).date()
    return date.fromisoformat(raw)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CST)
        return parsed.astimezone(CST)
    except ValueError:
        return None


def _compute_signature(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _is_excluded_container_or_work(title: str, description: str, project_name: str, labels: Sequence[str]) -> bool:
    combined = f"{title} {project_name} {' '.join(labels)}".lower()
    for term in EXCLUDED_TERMS:
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", combined):
            return True
    if re.search(r"(?i)^\[(?:daily brief|daily learn|root|epic|m2-sync|routine)\b", title.strip()):
        return True
    if re.search(
        r"<!--\s*(?:GTT-LINEAR-DEDUP:daily-brief-|GTT-M2-SYNC-HUB:v1|multica-cursor:(?:daily-brief|daily-learn|local-herdr-worktree-gc|backlog-grooming-and-push))",
        description,
    ):
        return True
    return False


def _normalize_priority(raw_priority: Any) -> int:
    """Normalize priority into Linear 1..4 scale (1=Urgent, 2=High, 3=Medium, 4=Low, 0=None->3)."""
    if isinstance(raw_priority, (int, float)) and not isinstance(raw_priority, bool):
        val = int(raw_priority)
        if val in {1, 2, 3, 4}:
            return val
        return 3
    text = str(raw_priority or "").strip().lower()
    mapping = {
        "urgent": 1,
        "p0": 1,
        "p1": 1,
        "critical": 1,
        "high": 2,
        "p2": 2,
        "medium": 3,
        "normal": 3,
        "p3": 3,
        "low": 4,
        "p4": 4,
        "none": 3,
        "": 3,
    }
    return mapping.get(text, 3)


def _extract_labels(raw_labels: Any) -> list[str]:
    if isinstance(raw_labels, Mapping):
        nodes = raw_labels.get("nodes") or []
        return [str(n.get("name") or "").strip() for n in nodes if isinstance(n, Mapping) and n.get("name")]
    if isinstance(raw_labels, list):
        out: list[str] = []
        for item in raw_labels:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, Mapping) and item.get("name"):
                out.append(str(item["name"]).strip())
        return out
    return []


def _scope_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    view = dict(raw)
    project = view.get("project")
    if not isinstance(project, Mapping):
        view["project"] = {"name": str(project or view.get("project_name") or "").strip()}
    team = view.get("team")
    if team is not None and not isinstance(team, Mapping):
        view["team"] = {"name": str(team).strip()}
    parent = view.get("parent")
    if parent is None and view.get("parent_issue") is not None:
        parent = view.get("parent_issue")
    if parent is None and view.get("parent_issue_id"):
        parent = {"id": str(view["parent_issue_id"]).strip()}
    if parent is not None or "parent" in view or "parent_issue" in view or "parent_issue_id" in view:
        view["parent"] = parent
    return view


def _scope_excluded(
    raw: Mapping[str, Any],
    *,
    client: Any = None,
    cache: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    daily_brief: Any = None,
) -> bool:
    if raw.get("_scope_verified") is True:
        return False
    has_parent_scope = any(
        key in raw and raw.get(key) not in (None, "")
        for key in ("parent", "parent_issue", "parent_issue_id")
    )
    has_team_scope = "team" in raw and raw.get("team") not in (None, "")
    if not has_parent_scope and not has_team_scope:
        return False
    module = daily_brief or _load_daily_brief()
    return bool(module._excluded_issue(_scope_view(raw), client, cache, warnings))


def _normalize_issue_record(
    raw: Mapping[str, Any],
    *,
    default_source: str = "linear",
    scope_client: Any = None,
    scope_cache: dict[str, Any] | None = None,
    scope_warnings: list[str] | None = None,
    daily_brief: Any = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    source = str(raw.get("source") or default_source).strip().lower()
    issue_id = str(raw.get("id") or raw.get("uuid") or raw.get("identifier") or "").strip()
    identifier = str(raw.get("identifier") or raw.get("key") or issue_id).strip()
    title = str(raw.get("title") or "").strip()
    if not title or not (issue_id or identifier):
        return None

    if _scope_excluded(
        raw,
        client=scope_client,
        cache=scope_cache,
        warnings=scope_warnings,
        daily_brief=daily_brief,
    ):
        return None

    description = str(raw.get("description") or "")
    raw_state = raw.get("state")
    if isinstance(raw_state, Mapping):
        state_name = str(raw_state.get("name") or raw_state.get("type") or "backlog").strip().lower()
        state_type = str(raw_state.get("type") or state_name).strip().lower()
    else:
        state_name = str(raw.get("status") or raw_state or "backlog").strip().lower()
        state_type = str(raw.get("status_category") or state_name).strip().lower()

    if state_name in CLOSED_STATES or state_type in CLOSED_STATES:
        return None
    if state_name in ACTIVE_IN_PROGRESS_STATES or state_type in ACTIVE_IN_PROGRESS_STATES:
        return None
    if state_name not in OPEN_BACKLOG_STATES and state_type not in OPEN_BACKLOG_STATES:
        return None

    raw_project = raw.get("project")
    if isinstance(raw_project, Mapping):
        project_name = str(raw_project.get("name") or "").strip()
    else:
        project_name = str(raw_project or raw.get("repo") or "").strip()

    labels = _extract_labels(raw.get("labels"))
    if _is_excluded_container_or_work(title, description, project_name, labels):
        return None

    priority = _normalize_priority(raw.get("priority"))
    url = str(raw.get("url") or "").strip()
    if not url and re.fullmatch(r"TIA-\d+", identifier, re.IGNORECASE):
        url = f"https://linear.app/tianpeili/issue/{identifier.upper()}"

    created_at = str(raw.get("createdAt") or raw.get("created_at") or "").strip()
    updated_at = str(raw.get("updatedAt") or raw.get("updated_at") or created_at).strip()
    blocked_by = raw.get("blocked_by") or raw.get("blockedBy") or []
    if isinstance(blocked_by, (str, int)):
        blocked_by = [str(blocked_by)]
    elif isinstance(blocked_by, list):
        blocked_by = [str(x).strip() for x in blocked_by if str(x).strip()]
    else:
        blocked_by = []

    record = {
        "id": issue_id or identifier,
        "identifier": identifier.upper() if re.fullmatch(r"tia-\d+", identifier, re.IGNORECASE) else identifier,
        "source": source,
        "title": title,
        "description": description,
        "state": state_name,
        "state_type": state_type,
        "priority": priority,
        "project": project_name or "user-skills",
        "labels": labels,
        "url": url,
        "created_at": created_at,
        "updated_at": updated_at,
        "blocked_by": blocked_by,
        "_scope_verified": True,
    }
    for key in ("team", "parent", "parent_issue", "parent_issue_id", "project_id"):
        if key in raw:
            record[key] = raw[key]
    return record


# ---------------------------------------------------------------------------
# Stage 1: Deterministic Backlog Scan & Fingerprint Deduplication
# ---------------------------------------------------------------------------

def _expand_cjk_ngrams(token: str) -> list[str]:
    if not re.fullmatch(r"[\u4e00-\u9fff]+", token):
        return [token]
    cleaned = token
    for sw in ("支持", "实现", "新增", "优化", "更新", "修复", "任务", "单据", "待办", "问题", "功能", "模块"):
        cleaned = cleaned.replace(sw, "")
    if not cleaned:
        cleaned = token
    if len(cleaned) <= 2:
        return [cleaned]
    bigrams = [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]
    return [cleaned, *bigrams]


def tokenize_issue_text(title: str, description: str = "") -> tuple[list[str], list[str]]:
    """Return `(core_tokens, ref_keys)` for deterministic fingerprinting & Jaccard comparison."""
    stripped_title = BRACKET_TAG_RE.sub("", str(title or "")).lower()
    first_desc_line = ""
    for ln in str(description or "").splitlines():
        s = ln.strip()
        if s and not s.startswith("<!--"):
            first_desc_line = s[:200].lower()
            break
    combined = f"{stripped_title} {first_desc_line}"

    ref_keys: list[str] = []
    for m in ISSUE_ID_RE.findall(combined):
        norm_id = m.upper()
        if norm_id not in ref_keys:
            ref_keys.append(norm_id)
    for m in PR_REF_RE.findall(combined):
        norm_pr = m.lower()
        if norm_pr not in ref_keys:
            ref_keys.append(norm_pr)

    cleaned = ISSUE_ID_RE.sub(" ", stripped_title)
    cleaned = PR_REF_RE.sub(" ", cleaned)
    raw_tokens = TOKEN_RE.findall(cleaned)
    tokens: list[str] = []
    for tok in raw_tokens:
        tok_norm = tok.strip("_-").lower()
        if not tok_norm or tok_norm in STOPWORDS or len(tok_norm) <= 1:
            continue
        for expanded in _expand_cjk_ngrams(tok_norm):
            if expanded and expanded not in STOPWORDS and expanded not in tokens:
                tokens.append(expanded)

    if not tokens and stripped_title.strip():
        tokens = [re.sub(r"\s+", "-", stripped_title.strip())[:32]]

    return sorted(tokens), sorted(ref_keys)


def compute_problem_fingerprint(issue: Mapping[str, Any]) -> dict[str, Any]:
    title = str(issue.get("title") or "")
    description = str(issue.get("description") or "")
    tokens, ref_keys = tokenize_issue_text(title, description)
    # Canonical normalized title stem (ascii + cjk alphanumerics only)
    stripped_title = BRACKET_TAG_RE.sub("", title).lower()
    stripped_title = ISSUE_ID_RE.sub("", stripped_title)
    stripped_title = PR_REF_RE.sub("", stripped_title)
    title_stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", stripped_title)
    canonical_tokens = [t for t in tokens if len(t) >= 2][:12]
    fp_basis = "|".join(canonical_tokens) if canonical_tokens else title_stem
    fp_hash = hashlib.sha256(fp_basis.encode("utf-8")).hexdigest()[:12]
    return {
        "problem_fingerprint": f"fp-{fp_hash}",
        "title_stem": title_stem,
        "tokens": tokens,
        "ref_keys": ref_keys,
    }


def token_jaccard_similarity(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> float:
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return round(intersection / union, 4) if union else 0.0


def _are_issues_duplicate(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    jaccard_threshold: float = 0.50,
) -> tuple[bool, float, str]:
    fp_a = a["fingerprint_meta"]
    fp_b = b["fingerprint_meta"]
    if fp_a["problem_fingerprint"] == fp_b["problem_fingerprint"] and fp_a["title_stem"]:
        return True, 1.0, "exact_fingerprint"
    if fp_a["title_stem"] and fp_a["title_stem"] == fp_b["title_stem"]:
        return True, 1.0, "exact_title_stem"

    jaccard = token_jaccard_similarity(fp_a["tokens"], fp_b["tokens"])
    shared_refs = set(fp_a["ref_keys"]) & set(fp_b["ref_keys"])
    if shared_refs and jaccard >= 0.30:
        return True, max(jaccard, 0.75), f"shared_ref_key:{','.join(sorted(shared_refs))}"
    if jaccard >= jaccard_threshold:
        return True, jaccard, "token_jaccard"
    # Substring stem containment for bilingual or prefixed titles
    stem_a, stem_b = fp_a["title_stem"], fp_b["title_stem"]
    if len(stem_a) >= 8 and len(stem_b) >= 8 and (stem_a in stem_b or stem_b in stem_a) and jaccard >= 0.35:
        return True, max(jaccard, 0.65), "stem_containment"
    return False, jaccard, "distinct"


def _survivor_sort_key(issue: Mapping[str, Any]) -> tuple[Any, ...]:
    has_tia = 0 if re.fullmatch(r"TIA-\d+", str(issue.get("identifier") or "")) else 1
    priority = int(issue.get("priority") or 3)
    state_rank = 0 if str(issue.get("state") or "") == "todo" else 1
    desc_len = -len(str(issue.get("description") or ""))
    return (has_tia, priority, state_rank, desc_len, str(issue.get("identifier") or ""))


def cluster_and_deduplicate_issues(
    issues: Sequence[Mapping[str, Any]],
    *,
    jaccard_threshold: float = 0.50,
) -> dict[str, Any]:
    enriched: list[dict[str, Any]] = []
    seen_source_ids: set[tuple[str, str]] = set()
    for raw in issues:
        norm = _normalize_issue_record(raw, default_source=str(raw.get("source") or "linear"))
        if norm is None:
            continue
        dedup_pair = (norm["source"], norm["identifier"])
        if dedup_pair in seen_source_ids:
            continue
        seen_source_ids.add(dedup_pair)
        norm["fingerprint_meta"] = compute_problem_fingerprint(norm)
        norm["problem_fingerprint"] = norm["fingerprint_meta"]["problem_fingerprint"]
        enriched.append(norm)

    enriched.sort(key=_survivor_sort_key)

    n = len(enriched)
    parent = list(range(n))
    pair_reasons: dict[tuple[int, int], tuple[float, str]] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            if ri < rj:
                parent[rj] = ri
            else:
                parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            is_dup, sim, reason = _are_issues_duplicate(
                enriched[i],
                enriched[j],
                jaccard_threshold=jaccard_threshold,
            )
            if is_dup:
                union(i, j)
                pair_reasons[(i, j)] = (sim, reason)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    deduplicated_candidates: list[dict[str, Any]] = []
    duplicate_clusters: list[dict[str, Any]] = []

    for indices in groups.values():
        members = [enriched[i] for i in indices]
        members_sorted = sorted(members, key=_survivor_sort_key)
        survivor = dict(members_sorted[0])
        duplicates = [dict(m) for m in members_sorted[1:]]

        all_sources = sorted({m["source"] for m in members_sorted})
        all_identifiers = [m["identifier"] for m in members_sorted]
        survivor["merged_sources"] = all_sources
        survivor["cluster_identifiers"] = all_identifiers
        survivor["duplicate_count"] = len(duplicates)

        if duplicates:
            sims: list[float] = []
            reasons: list[str] = []
            for a_idx in indices:
                for b_idx in indices:
                    if (a_idx, b_idx) in pair_reasons:
                        sim, r = pair_reasons[(a_idx, b_idx)]
                        sims.append(sim)
                        if r not in reasons:
                            reasons.append(r)
            cluster_id = f"dup-{survivor['problem_fingerprint'].removeprefix('fp-')}"
            duplicate_clusters.append(
                {
                    "cluster_id": cluster_id,
                    "problem_fingerprint": survivor["problem_fingerprint"],
                    "canonical_issue": {
                        "id": survivor["id"],
                        "identifier": survivor["identifier"],
                        "source": survivor["source"],
                        "title": survivor["title"],
                        "priority": survivor["priority"],
                        "state": survivor["state"],
                        "url": survivor["url"],
                    },
                    "duplicate_issues": [
                        {
                            "id": d["id"],
                            "identifier": d["identifier"],
                            "source": d["source"],
                            "title": d["title"],
                            "priority": d["priority"],
                            "state": d["state"],
                            "url": d["url"],
                        }
                        for d in duplicates
                    ],
                    "max_similarity": max(sims) if sims else 1.0,
                    "match_reasons": reasons or ["exact_fingerprint"],
                    "recommended_action": (
                        f"保留主单 {survivor['identifier']} ({survivor['source']})，"
                        f"合并/关闭重复单 {', '.join(d['identifier'] for d in duplicates)}"
                    ),
                }
            )
        deduplicated_candidates.append(survivor)

    deduplicated_candidates.sort(key=_survivor_sort_key)
    duplicate_clusters.sort(
        key=lambda cl: (str((cl.get("canonical_issue") or {}).get("identifier") or ""), str(cl.get("cluster_id") or ""))
    )

    return {
        "total_open_backlog_issues": len(enriched),
        "deduplicated_count": len(deduplicated_candidates),
        "duplicate_cluster_count": len(duplicate_clusters),
        "duplicate_issue_count": sum(len(c["duplicate_issues"]) for c in duplicate_clusters),
        "deduplicated_candidates": deduplicated_candidates,
        "duplicate_clusters": duplicate_clusters,
    }


def collect_backlog_issues(
    *,
    issues_json: Path | Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    linear_rows: Sequence[Mapping[str, Any]] | None = None,
    linear_client: Any = None,
    linear_scope_cache: dict[str, Any] | None = None,
    include_multica: bool = True,
    multica_server_url: str = MULTICA_DEFAULT_SERVER_URL,
    multica_workspace_id: str = MULTICA_DEFAULT_WORKSPACE_ID,
    multica_bin: str | None = None,
    runner: Runner = _default_runner,
    dry_run: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if issues_json is not None:
        if isinstance(issues_json, (str, Path)):
            raw_payload = json.loads(Path(issues_json).expanduser().read_text(encoding="utf-8"))
        else:
            raw_payload = issues_json

        raw_list: list[Mapping[str, Any]] = []
        if isinstance(raw_payload, list):
            raw_list = [x for x in raw_payload if isinstance(x, Mapping)]
        elif isinstance(raw_payload, Mapping):
            for key in ("issues", "linear_issues", "multica_issues", "items"):
                val = raw_payload.get(key)
                if isinstance(val, list):
                    source_hint = "multica" if key == "multica_issues" else "linear"
                    for item in val:
                        if isinstance(item, Mapping):
                            raw_list.append({"source": item.get("source") or source_hint, **dict(item)})
        normalized: list[dict[str, Any]] = []
        daily_brief = _load_daily_brief()
        for item in raw_list:
            rec = _normalize_issue_record(
                item,
                default_source=str(item.get("source") or "linear"),
                scope_warnings=warnings,
                daily_brief=daily_brief,
            )
            if rec is not None:
                normalized.append(rec)
        return normalized, warnings

    collected: list[dict[str, Any]] = []

    # 1. Collect from Linear TIA. Reuse rows already fetched by Daily Brief when supplied.
    client = linear_client
    if linear_rows is None and client is None:
        try:
            sync_mod = _load_linear_sync()
            api_key = sync_mod.load_api_key()
            if api_key:
                client = sync_mod.GraphQLClient(api_key, allow_refresh=not dry_run)
        except Exception:
            client = None

    if linear_rows is not None or client is not None:
        scope_cache: dict[str, Any] = linear_scope_cache if linear_scope_cache is not None else {}
        daily_brief = _load_daily_brief()
        try:
            rows_to_normalize = list(linear_rows) if linear_rows is not None else list(client.list_issues("TIA", limit=None) or [])
            for row in rows_to_normalize:
                if not isinstance(row, Mapping):
                    continue
                scoped = _scope_view(row)
                if daily_brief._scope_complete(scoped):
                    for key in (row.get("id"), row.get("identifier")):
                        if key:
                            scope_cache[str(key)] = scoped
            for row in rows_to_normalize:
                if not isinstance(row, Mapping):
                    continue
                raw = {**dict(row), "source": "linear"}
                rec = _normalize_issue_record(
                    raw,
                    default_source="linear",
                    scope_client=client,
                    scope_cache=scope_cache,
                    scope_warnings=warnings,
                    daily_brief=daily_brief,
                )
                if rec is not None:
                    collected.append(rec)
        except Exception:
            warnings.append("Linear TIA issue scan unavailable; continuing with remaining sources")
    else:
        warnings.append("Linear credentials unavailable; skipped live Linear scan")

    # 2. Collect from Multica CLI. The API accepts at most 100 rows per page.
    if include_multica:
        binary = _resolve_multica_bin(multica_bin)
        offset = 0
        page_size = 100
        while True:
            cmd = [binary]
            if multica_server_url:
                cmd.extend(["--server-url", multica_server_url])
            if multica_workspace_id:
                cmd.extend(["--workspace-id", multica_workspace_id])
            cmd.extend(["issue", "list", "--limit", str(page_size), "--offset", str(offset), "--output", "json"])
            try:
                proc = runner(cmd, check=False, timeout=15)
            except subprocess.TimeoutExpired:
                warnings.append("Multica issue list timed out; source incomplete")
                break
            except (OSError, subprocess.CalledProcessError):
                warnings.append("Multica issue list execution failed; source incomplete")
                break
            if proc.returncode != 0:
                warnings.append("Multica issue list returned an error; source incomplete")
                break
            stdout = (proc.stdout or "").strip()
            if not stdout:
                warnings.append("Multica issue list returned no data; source incomplete")
                break
            try:
                parsed = json.loads(stdout)
            except (TypeError, ValueError):
                warnings.append("Multica issue list returned invalid JSON; source incomplete")
                break
            if isinstance(parsed, list):
                rows = parsed
                has_more = False
            elif isinstance(parsed, Mapping) and isinstance(parsed.get("issues"), list):
                rows = parsed["issues"]
                has_more = bool(parsed.get("has_more", False))
            else:
                warnings.append("Multica issue list response was incomplete; source incomplete")
                break
            raw_count = len(rows)
            for row in rows:
                if isinstance(row, Mapping):
                    rec = _normalize_issue_record({**dict(row), "source": "multica"}, default_source="multica")
                    if rec is not None:
                        collected.append(rec)
            if not has_more:
                break
            if raw_count == 0:
                warnings.append("Multica issue list reported more pages without rows; source incomplete")
                break
            offset += raw_count

    return collected, warnings


# ---------------------------------------------------------------------------
# Stage 2: Jev Readiness & ROI Scoring + Daily Top 3 Push Selection
# ---------------------------------------------------------------------------

def _extract_prob(raw: Any, field: str) -> float | None:
    if isinstance(raw, Mapping):
        raw = raw.get(field, raw.get("value"))
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 0.0 or val > 1.0:
        return None
    return val


def _deterministic_candidate_metrics(issue: Mapping[str, Any], *, target_date: date) -> dict[str, Any]:
    title = str(issue.get("title") or "")
    description = str(issue.get("description") or "")
    labels = " ".join(issue.get("labels") or [])
    combined = f"{title}\n{description}\n{labels}"

    explicit_blockers = list(issue.get("blocked_by") or [])
    has_blocker_signal = bool(explicit_blockers) or bool(BLOCKER_RE.search(combined))

    priority = int(issue.get("priority") or 3)
    priority_weight = {1: 0.92, 2: 0.80, 3: 0.62, 4: 0.42}.get(priority, 0.60)
    state_bonus = 0.06 if str(issue.get("state") or "") == "todo" else 0.0
    roi_bonus = 0.10 if HIGH_ROI_RE.search(combined) else 0.0
    cross_board_bonus = 0.04 if len(issue.get("merged_sources") or []) > 1 else 0.0

    created_dt = _parse_timestamp(issue.get("created_at") or issue.get("updated_at"))
    age_days = max(0, (target_date - created_dt.date()).days) if created_dt else 3
    age_bonus = min(0.06, age_days * 0.005)

    blocker_penalty = -0.45 if has_blocker_signal else 0.0
    deterministic_score = max(
        0.05,
        min(
            0.99,
            round(priority_weight + state_bonus + roi_bonus + cross_board_bonus + age_bonus + blocker_penalty, 4),
        ),
    )
    deterministic_choice = "BLOCKED_DEPENDENCY" if has_blocker_signal else (
        "READY_HIGH_ROI" if deterministic_score >= 0.68 else "DEFER_LOW_ROI"
    )
    return {
        "has_blocker": has_blocker_signal,
        "age_days": age_days,
        "deterministic_score": deterministic_score,
        "deterministic_choice": deterministic_choice,
    }


def _resolve_mock_for_candidate(
    mock_jev_json: Any,
    *,
    issue: Mapping[str, Any],
    index_1based: int,
) -> Any:
    if mock_jev_json is None:
        return None
    if isinstance(mock_jev_json, (str, Path)):
        raw_str = str(mock_jev_json).strip()
        p = Path(raw_str).expanduser()
        if p.is_file():
            parsed = json.loads(p.read_text(encoding="utf-8"))
        else:
            parsed = json.loads(raw_str)
    else:
        parsed = mock_jev_json

    if isinstance(parsed, list):
        idx0 = index_1based - 1
        return parsed[idx0] if 0 <= idx0 < len(parsed) else None
    if isinstance(parsed, Mapping):
        if "answers" in parsed or "readiness_choice" in parsed or "roi_score" in parsed:
            return parsed
        for key in (
            str(issue.get("identifier") or ""),
            str(issue.get("id") or ""),
            str(issue.get("problem_fingerprint") or ""),
            str(index_1based),
            str(issue.get("title") or ""),
        ):
            if key and key in parsed:
                return parsed[key]
    return parsed


def score_candidate_with_jev(
    issue: Mapping[str, Any],
    *,
    target_date: date,
    index_1based: int = 1,
    mock_jev_json: Any = None,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    det = _deterministic_candidate_metrics(issue, target_date=target_date)
    det_score = det["deterministic_score"]
    det_choice = det["deterministic_choice"]
    det_ready = not det["has_blocker"] and det_choice == "READY_HIGH_ROI"

    questions = {
        "readiness_choice": {
            "type": "choice",
            "instructions": "Classify whether this Backlog/Todo item is dependency-ready and high-ROI to push into today's Top 3.",
            "criteria": {
                "READY_HIGH_ROI": "Dependencies are satisfied and pushing this item today yields clear engineering or product ROI.",
                "BLOCKED_DEPENDENCY": "Waiting on an unresolved upstream dependency, external blocker, or pending Owner freeze.",
                "DEFER_LOW_ROI": "Low urgency or insufficient specification; keep in backlog for now.",
            },
        },
        "readiness_confidence": {
            "type": "noul",
            "instructions": "Probability from 0.0 to 1.0 that the selected readiness_choice is accurate (>0.70 accepts READY_HIGH_ROI).",
        },
        "roi_score": {
            "type": "score",
            "instructions": "Score expected ROI and execution priority from 0.0 (lowest) to 1.0 (highest).",
        },
    }
    state_payload = {
        "identifier": issue.get("identifier"),
        "source": issue.get("source"),
        "title": str(issue.get("title") or "")[:200],
        "priority": issue.get("priority"),
        "state": issue.get("state"),
        "project": issue.get("project"),
        "duplicate_count": issue.get("duplicate_count", 0),
        "has_blocker": det["has_blocker"],
        "age_days": det["age_days"],
    }

    candidate_mock = _resolve_mock_for_candidate(mock_jev_json, issue=issue, index_1based=index_1based)
    decide = jev_fn or _load_jev_client().jev_decide
    response = decide(
        state=state_payload,
        questions=questions,
        agent_role="linear-board",
        goal_id=f"loop-{LOOP_ID}",
        timeout_sec=1.5,
        falcon_only=True,
        mock_response=candidate_mock,
    )

    if not isinstance(response, Mapping) or response.get("fallback_reason"):
        return {
            "ready_to_push": det_ready,
            "readiness_choice": det_choice,
            "readiness_confidence": None,
            "roi_score": det_score,
            "composite_score": det_score,
            "decision_source": "deterministic_fallback_unavailable",
        }

    answers = response.get("answers", response)
    if not isinstance(answers, Mapping):
        return {
            "ready_to_push": det_ready,
            "readiness_choice": det_choice,
            "readiness_confidence": None,
            "roi_score": det_score,
            "composite_score": det_score,
            "decision_source": "deterministic_fallback_malformed",
        }

    raw_choice = answers.get("readiness_choice")
    if isinstance(raw_choice, Mapping):
        raw_choice = raw_choice.get("choice")
    choice = str(raw_choice or "").strip()

    conf = _extract_prob(answers.get("readiness_confidence"), "noul")
    if conf is None:
        conf = _extract_prob(answers.get("actionable_probability"), "noul")
    roi = _extract_prob(answers.get("roi_score"), "score")
    if roi is None:
        roi = _extract_prob(answers.get("next_hop_priority"), "score")

    valid_choices = {"READY_HIGH_ROI", "BLOCKED_DEPENDENCY", "DEFER_LOW_ROI"}
    if choice not in valid_choices or conf is None:
        final_roi = roi if roi is not None else det_score
        return {
            "ready_to_push": det_ready,
            "readiness_choice": det_choice,
            "readiness_confidence": conf,
            "roi_score": final_roi,
            "composite_score": round(0.6 * final_roi + 0.4 * det_score, 4),
            "decision_source": "deterministic_fallback_incomplete_jev",
        }

    final_roi = roi if roi is not None else det_score
    blended = round(0.70 * final_roi + 0.30 * det_score, 4)

    # Hard blocker or explicit non-ready choice is never overridden into ready
    if det["has_blocker"] or choice in {"BLOCKED_DEPENDENCY", "DEFER_LOW_ROI"}:
        return {
            "ready_to_push": False,
            "readiness_choice": "BLOCKED_DEPENDENCY" if det["has_blocker"] else choice,
            "readiness_confidence": conf,
            "roi_score": final_roi,
            "composite_score": round(blended * 0.5, 4),
            "decision_source": "jev_choice_reject",
        }

    if conf > 0.70:
        return {
            "ready_to_push": True,
            "readiness_choice": "READY_HIGH_ROI",
            "readiness_confidence": conf,
            "roi_score": final_roi,
            "composite_score": blended,
            "decision_source": "jev_high_confidence",
        }

    if conf < 0.30:
        return {
            "ready_to_push": False,
            "readiness_choice": "DEFER_LOW_ROI",
            "readiness_confidence": conf,
            "roi_score": final_roi,
            "composite_score": round(blended * 0.5, 4),
            "decision_source": "jev_low_confidence_reject",
        }

    # Grey zone 0.30..0.70 -> deterministic fallback
    return {
        "ready_to_push": det_ready,
        "readiness_choice": det_choice,
        "readiness_confidence": conf,
        "roi_score": final_roi,
        "composite_score": blended if det_ready else round(blended * 0.5, 4),
        "decision_source": "jev_grey_zone_fallback",
    }


def _sanitize_human_summary(text: str, *, limit: int = 25) -> str:
    cleaned = BRACKET_TAG_RE.sub("", str(text or ""))
    cleaned = ISSUE_ID_RE.sub("", cleaned)
    cleaned = PR_REF_RE.sub("", cleaned)
    cleaned = re.sub(
        r"(?<![A-Za-z0-9_-])(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])(?![A-Za-z0-9_-])",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z0-9_-])[0-9a-f]{7,40}(?![A-Za-z0-9_-])", "", cleaned)
    cleaned = re.sub(r"[*`<>|\n\r]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ·—-:")
    if not cleaned:
        cleaned = "推进待办事项"
    prefix = "顺推 "
    budget = max(4, limit - len(prefix))
    short = cleaned[:budget].strip()
    return f"{prefix}{short}"[:limit].strip()


def groom_backlog(
    issues: Sequence[Mapping[str, Any]],
    *,
    target_date: date | None = None,
    top_k: int = 3,
    jaccard_threshold: float = 0.50,
    mock_jev_json: Any = None,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
    state_output_path: Path | None = None,
    write_state: bool = False,
) -> dict[str, Any]:
    resolved_date = parse_date(target_date)
    date_str = resolved_date.isoformat()

    dedup_report = cluster_and_deduplicate_issues(issues, jaccard_threshold=jaccard_threshold)
    candidates = dedup_report["deduplicated_candidates"]

    ranked_candidates: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates, 1):
        verdict = score_candidate_with_jev(
            cand,
            target_date=resolved_date,
            index_1based=idx,
            mock_jev_json=mock_jev_json,
            jev_fn=jev_fn,
        )
        summary_25 = _sanitize_human_summary(cand["title"], limit=25)
        identifier = str(cand.get("identifier") or cand.get("id") or f"ITEM-{idx}")
        url = str(cand.get("url") or "").strip()
        if not url and re.fullmatch(r"TIA-\d+", identifier):
            url = f"https://linear.app/tianpeili/issue/{identifier}"
        action_md = f"[打开]({url})" if url else f"`@bot {identifier}`"

        entry = {
            "id": cand["id"],
            "identifier": identifier,
            "source": cand["source"],
            "merged_sources": cand.get("merged_sources", [cand["source"]]),
            "title": cand["title"],
            "summary": summary_25,
            "priority": cand["priority"],
            "state": cand["state"],
            "project": cand["project"],
            "url": url,
            "action": action_md,
            "ref": identifier if re.fullmatch(r"TIA-\d+", identifier) else cand["project"],
            "problem_fingerprint": cand["problem_fingerprint"],
            "duplicate_count": cand.get("duplicate_count", 0),
            "cluster_identifiers": cand.get("cluster_identifiers", [identifier]),
            "ready_to_push": verdict["ready_to_push"],
            "readiness_choice": verdict["readiness_choice"],
            "readiness_confidence": verdict["readiness_confidence"],
            "roi_score": verdict["roi_score"],
            "composite_score": verdict["composite_score"],
            "decision_source": verdict["decision_source"],
            "group": "recommended",
        }
        ranked_candidates.append(entry)

    ranked_candidates.sort(
        key=lambda row: (
            not row["ready_to_push"],
            -float(row["composite_score"]),
            int(row["priority"]),
            str(row["identifier"]),
        )
    )

    ready_items = [row for row in ranked_candidates if row["ready_to_push"]]
    top3 = ready_items[: max(0, int(top_k))]

    result = {
        "schema": "linear-board-backlog-groomer/v1",
        "ok": True,
        "date": date_str,
        "total_open_backlog_issues": dedup_report["total_open_backlog_issues"],
        "deduplicated_count": dedup_report["deduplicated_count"],
        "duplicate_cluster_count": dedup_report["duplicate_cluster_count"],
        "duplicate_issue_count": dedup_report["duplicate_issue_count"],
        "duplicate_clusters": dedup_report["duplicate_clusters"],
        "ranked_candidates": ranked_candidates,
        "top3_push_candidates": top3,
        "top3_recommendations": top3,
    }

    if write_state or state_output_path is not None:
        out_path = Path(state_output_path).expanduser() if state_output_path is not None else _default_state_path()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".top3-groomed-", dir=str(out_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialized)
            os.replace(tmp_name, out_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        result["state_output_path"] = str(out_path)

    return result


# ---------------------------------------------------------------------------
# Stage 3: Dual-Zone Issue Rendering & `multica_loop_runner.py` Collector
# ---------------------------------------------------------------------------

def _sanitize_human_zone_line(text: str) -> str:
    cleaned = re.sub(
        r"(?<![A-Za-z0-9_-])(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])(?![A-Za-z0-9_-])",
        "",
        str(text or ""),
    )
    cleaned = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z0-9_-])[0-9a-f]{7,40}(?![A-Za-z0-9_-])", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" ·—-")


def build_dual_zone_grooming_markdown(
    groom_report: Mapping[str, Any],
    *,
    target_date: date | None = None,
) -> tuple[str, list[dict[str, str]], str]:
    resolved_date = parse_date(target_date or groom_report.get("date"))
    date_str = resolved_date.isoformat()
    clusters = list(groom_report.get("duplicate_clusters") or [])
    top3 = list(groom_report.get("top3_push_candidates") or [])
    total_open = int(groom_report.get("total_open_backlog_issues") or 0)
    dup_count = int(groom_report.get("duplicate_issue_count") or 0)

    summary = _sanitize_human_zone_line(
        f"扫描 {total_open} 项待办单据，识别 {len(clusters)} 组重复单（共 {dup_count} 项可合并），"
        f"筛选出今日 Top {len(top3)} 高优顺推项供勾选推进。"
    )

    items: list[dict[str, str]] = []
    for cand in top3[:3]:
        ident = str(cand.get("identifier") or "")
        url = str(cand.get("url") or "")
        clean_title = _sanitize_human_zone_line(BRACKET_TAG_RE.sub("", str(cand.get("title") or "待办项")))[:36]
        link_label = f"[{ident}]({url})" if (ident and url) else ident
        what = f"顺推 {link_label} {clean_title}".strip() if link_label else f"顺推 {clean_title}"
        why = f"依赖已就绪且综合优先级高（P{cand.get('priority', 3)}）"
        items.append({"what": _sanitize_human_zone_line(what), "why": _sanitize_human_zone_line(why)})

    for cl in clusters[:3]:
        canon = cl.get("canonical_issue") or {}
        dups = cl.get("duplicate_issues") or []
        canon_id = str(canon.get("identifier") or "主单")
        dup_ids = "、".join(str(d.get("identifier") or "") for d in dups if d.get("identifier"))
        canon_title = _sanitize_human_zone_line(BRACKET_TAG_RE.sub("", str(canon.get("title") or "")))[:24]
        items.append(
            {
                "what": _sanitize_human_zone_line(f"合并重复单 {dup_ids} 至主单 {canon_id}（{canon_title}）"),
                "why": "消除跨看板重复待办，收敛唯一追踪入口",
            }
        )

    if not items:
        items.append(
            {
                "what": "当前待办池无重复单且无新增高优顺推项",
                "why": "保持日常研发节奏",
            }
        )

    cursor = f"{LOOP_ID}-{date_str}"
    issue_brief = _load_issue_brief()
    dual_zone_md = issue_brief.render_dual_zone_issue(
        tldr=summary,
        items=items[:6],
        cursor=cursor,
        ai_payload={
            "loop_id": LOOP_ID,
            "date": date_str,
            "total_open_backlog_issues": total_open,
            "duplicate_clusters": clusters,
            "top3_push_candidates": top3,
        },
    )
    verification = issue_brief.verify_dual_zone_issue(dual_zone_md)
    if not verification["ok"]:
        raise ValueError(f"Dual-Zone backlog grooming issue failed verification: {verification['violations']}")
    return dual_zone_md, items[:6], summary


def run_loop_check(
    *,
    target_date: date | str | None = None,
    issues_json: Path | Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    linear_client: Any = None,
    include_multica: bool = True,
    multica_server_url: str = MULTICA_DEFAULT_SERVER_URL,
    multica_workspace_id: str = MULTICA_DEFAULT_WORKSPACE_ID,
    multica_bin: str | None = None,
    mock_jev_json: Any = None,
    state_output_path: Path | None = None,
    dry_run: bool = False,
    runner: Runner = _default_runner,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    resolved_date = parse_date(target_date)
    date_str = resolved_date.isoformat()

    issues, warnings = collect_backlog_issues(
        issues_json=issues_json,
        linear_client=linear_client,
        include_multica=include_multica,
        multica_server_url=multica_server_url,
        multica_workspace_id=multica_workspace_id,
        multica_bin=multica_bin,
        runner=runner,
        dry_run=dry_run,
    )

    groom_report = groom_backlog(
        issues,
        target_date=resolved_date,
        top_k=3,
        mock_jev_json=mock_jev_json,
        jev_fn=jev_fn,
        state_output_path=state_output_path,
        write_state=not dry_run or state_output_path is not None,
    )

    clusters = groom_report["duplicate_clusters"]
    top3 = groom_report["top3_push_candidates"]
    delta_count = len(clusters) + len(top3)
    has_delta = delta_count > 0

    sig = _compute_signature(
        {
            "clusters": [(c["cluster_id"], [d["identifier"] for d in c["duplicate_issues"]]) for c in clusters],
            "top3": [t["identifier"] for t in top3],
        }
    )
    cursor = f"{LOOP_ID}-{date_str}"
    watermark = f"{LOOP_ID}:{date_str}:{sig}"
    title = f"[P3 / Priority 3][Backlog Grooming] {date_str} 待办去重与每日 Top 3 推荐顺推清单"

    dual_zone_md, items, summary = build_dual_zone_grooming_markdown(groom_report, target_date=resolved_date)
    severity = "high" if clusters else ("medium" if top3 else "low")

    return {
        "ok": True,
        "loop_id": LOOP_ID,
        "date": date_str,
        "has_delta": has_delta,
        "delta_count": delta_count,
        "actionable": has_delta,
        "severity": severity,
        "watermark": watermark,
        "cursor": cursor,
        "title": title,
        "summary": summary,
        "items": items,
        "dual_zone_markdown": dual_zone_md if has_delta else "",
        "duplicate_clusters": clusters,
        "top3_push_candidates": top3,
        "top3_recommendations": top3,
        "groom_report": groom_report,
        "warnings": warnings,
        "raw": {
            "total_open_backlog_issues": groom_report["total_open_backlog_issues"],
            "duplicate_cluster_count": groom_report["duplicate_cluster_count"],
            "top3_identifiers": [t["identifier"] for t in top3],
        },
    }


def load_groomed_top3_state(path: Path | None = None, *, target_date: date | None = None) -> list[dict[str, Any]]:
    target = Path(path).expanduser() if path is not None else _default_state_path()
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, Mapping):
        return []
    if target_date is not None and payload.get("date") and str(payload.get("date")) != target_date.isoformat():
        return []
    top_rows = [dict(r) for r in (payload.get("top3_push_candidates") or payload.get("top3_recommendations") or []) if isinstance(r, Mapping)]
    seen_ids = {str(r.get("identifier") or r.get("id") or "") for r in top_rows}
    extra_ready = [
        dict(r)
        for r in (payload.get("ranked_candidates") or [])
        if isinstance(r, Mapping)
        and r.get("ready_to_push") is not False
        and str(r.get("identifier") or r.get("id") or "") not in seen_ids
    ]
    return top_rows + extra_ready


# ---------------------------------------------------------------------------
# CLI Entrypoint (`scan`, `groom`, `top3`, `loop-check`)
# ---------------------------------------------------------------------------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--date", default="today", help="Target date (YYYY-MM-DD or today)")
    p.add_argument("--issues-json", type=Path, default=None, help="Hermetic JSON file of issues for offline/test runs")
    p.add_argument("--no-multica", action="store_true", help="Skip live Multica CLI scan")
    p.add_argument("--multica-server-url", default=MULTICA_DEFAULT_SERVER_URL)
    p.add_argument("--multica-workspace-id", default=MULTICA_DEFAULT_WORKSPACE_ID)
    p.add_argument("--mock-jev-json", default=None, help="Inline JSON or file path for hermetic Jev responses")
    p.add_argument("--output", type=Path, default=None, help="Output file path for groomed state JSON")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", default=True)


class _SelfDisclosingBacklogGroomerParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[backlog_groomer] CLI error: {message}\n"
            "  Fix: run `python3 skills/linear-board/scripts/backlog_groomer.py {scan,groom,top3,loop-check} --help`\n"
            "  Reference: skills/linear-board/references/daily-brief-and-bot.md\n"
        )
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingBacklogGroomerParser(
        prog="backlog_groomer.py",
        description="Loop 3B: Backlog Grooming, Fingerprint Deduplication & Daily Top-3 Push Loop",
    )
    sub = parser.add_subparsers(dest="subcommand", required=False)

    p_scan = sub.add_parser("scan", help="Stage 1: Deterministic Backlog scan & fingerprint deduplication")
    _add_common_args(p_scan)

    p_groom = sub.add_parser("groom", help="Stage 1 + Stage 2: Deduplicate and rank Backlog with Jev")
    _add_common_args(p_groom)
    p_groom.add_argument("--top-k", type=int, default=3)

    p_top3 = sub.add_parser("top3", help="Emit Daily Top 3 recommended push candidates")
    _add_common_args(p_top3)
    p_top3.add_argument("--top-k", type=int, default=3)

    p_loop = sub.add_parser("loop-check", help="Stage 1-3: Full Loop 3B collector for multica_loop_runner.py")
    _add_common_args(p_loop)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0].startswith("-"):
        if "-h" not in args_list and "--help" not in args_list:
            args_list = ["groom", *args_list]
    parser = _build_parser()
    ns = parser.parse_args(args_list)

    target = parse_date(ns.date)
    include_multica = not bool(ns.no_multica)

    if ns.subcommand == "scan":
        issues, warnings = collect_backlog_issues(
            issues_json=ns.issues_json,
            include_multica=include_multica,
            multica_server_url=ns.multica_server_url,
            multica_workspace_id=ns.multica_workspace_id,
            dry_run=ns.dry_run,
        )
        report = cluster_and_deduplicate_issues(issues)
        report["ok"] = True
        report["date"] = target.isoformat()
        report["warnings"] = warnings
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand in {"groom", "top3"}:
        issues, warnings = collect_backlog_issues(
            issues_json=ns.issues_json,
            include_multica=include_multica,
            multica_server_url=ns.multica_server_url,
            multica_workspace_id=ns.multica_workspace_id,
            dry_run=ns.dry_run,
        )
        report = groom_backlog(
            issues,
            target_date=target,
            top_k=getattr(ns, "top_k", 3),
            mock_jev_json=ns.mock_jev_json,
            state_output_path=ns.output,
            write_state=(not ns.dry_run) or (ns.output is not None),
        )
        report["warnings"] = warnings
        if ns.subcommand == "top3":
            top3_payload = {
                "ok": True,
                "date": target.isoformat(),
                "top3_push_candidates": report["top3_push_candidates"],
                "top3_recommendations": report["top3_recommendations"],
                "duplicate_cluster_count": report["duplicate_cluster_count"],
                "warnings": warnings,
            }
            if "state_output_path" in report:
                top3_payload["state_output_path"] = report["state_output_path"]
            print(json.dumps(top3_payload, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand == "loop-check":
        report = run_loop_check(
            target_date=target,
            issues_json=ns.issues_json,
            include_multica=include_multica,
            multica_server_url=ns.multica_server_url,
            multica_workspace_id=ns.multica_workspace_id,
            mock_jev_json=ns.mock_jev_json,
            state_output_path=ns.output,
            dry_run=ns.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
