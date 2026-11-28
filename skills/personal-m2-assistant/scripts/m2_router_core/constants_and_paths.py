from __future__ import annotations

from ._imports import *

from skill_runtime.cli_contract import (
    HERDR_ORIGIN_COMMENT_RE,
    append_herdr_origin_tag,
    extract_dispatched_instruction,
    format_herdr_origin_tag,
    parse_herdr_origin_tag,
    split_routed_prompt_sections,
    strip_herdr_origin_comments,
)

ROUTER_FILE = Path(__file__).resolve().parents[1] / "m2-router.py"

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SCHEMA_VERSION = 1

M2_SYNC_HUB_MARKER = "<!-- GTT-M2-SYNC-HUB:v1 -->"

M2_SYNC_STATE_START = "<!-- M2-SYNC-STATE-START -->"

M2_SYNC_STATE_END = "<!-- M2-SYNC-STATE-END -->"

M2_SYNC_SCHEMA = "m2-cross-device-sync/v1"

DEFAULT_M2_SYNC_HUB_ISSUE = "TIA-463"

LINEAR_SYNC_SCRIPT_PATH = ROUTER_FILE.resolve().parents[2] / "team-lead" / "scripts" / "linear-sync.py"

DEFAULT_M2_MODEL = "gemini-4-argon"

DEFAULT_M2_CONTEXT = "long_context"

DEFAULT_M2_MODEL_LABEL = "Gemini 4 Argon · 983K context"

ACTIVE_STATUSES = {"working", "running", "idle", "done", "blocked", "needs_input", "ready"}

HERDR_TERMINAL_STATUSES = {"cancelled", "canceled", "stopped", "archived", "exited", "dead"}

TERMINAL_STATUSES = {"done", *HERDR_TERMINAL_STATUSES}

ROUTES = {"ROUTE_SINGLE", "ROUTE_CROSS_SESSION", "CREATE_NEW_M2"}

NEW_UNMAPPED_DOMAIN = "NEW_UNMAPPED_DOMAIN"

MAPPED_DOMAIN = "MAPPED_DOMAIN"

NEW_DOMAIN_CANDIDATE = "NEW_DOMAIN_CANDIDATE"

RICH_PROMPT_KEYS = (
    "instruction",
    "instructions",
    "prompt",
    "detail",
    "details",
    "body",
    "task_prompt",
    "sub_prompt",
)

MAGPIE_BASE_URL = "http://127.0.0.1:3425"

MAGPIE_DEFAULT_MODEL = "group/steering"

DEFAULT_DOMAIN_MAP_PATH = ROUTER_FILE.resolve().parents[1] / "references" / "m2-domain-map.json"

DEFAULT_HOOK_SCRIPT_PATH = ROUTER_FILE.parent / "m2-board-hook.sh"

HERDR_BACKGROUND_RE = re.compile(r"(?:^|[-_])(?:goal-tl|writer|reviewer|explorer|pr-monitor)(?:[-_]|$)|/worktrees(?:/|$)", re.I)

HERDR_INTERACTIVE_AGENTS = {"copilot", "claude", "codex", "pi", "cindy"}

READONLY_PACKAGE_MANAGER_ROOTS = (
    "/opt/homebrew/bin",
    "/opt/homebrew/Cellar",
    "/opt/homebrew/opt",
    "/opt/homebrew/lib",
    "/opt/homebrew/share",
    "/opt/homebrew/libexec",
    "/usr/local/Cellar",
    "/usr/local/opt",
    "/usr/local/bin",
)

SAFE_BASELINE_ROOTS = (
    "~/Development",
    "/Volumes/SSD",
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    "~/.copilot",
    "~/.agents",
    "~/.codex",
    "~/.claude",
    "~/.local",
    "~/.config/pew",
    "~/Library/Application Support/AIDash",
    "/opt/homebrew/lib/node_modules/@nocoo/pew",
    *READONLY_PACKAGE_MANAGER_ROOTS,
)

WHITELISTED_LINEAR_MCP_TOOLS = {
    "save_issue",
    "create_issue",
    "update_issue",
    "save_comment",
    "create_comment",
    "update_comment",
    "save_project",
    "create_project",
    "update_project",
    "save_milestone",
    "create_milestone",
    "update_milestone",
    "save_document",
    "create_document",
    "update_document",
    "save_status_update",
    "create_status_update",
    "update_status_update",
    "save_issue_label",
    "create_issue_label",
    "save_project_label",
    "create_project_label",
    "mark_notification",
    "whoami",
    "extract_images",
}

WHITELISTED_READONLY_MCP_SERVERS = {
    "ado",
    "ado-edge",
    "github",
    "github-mcp-server",
}

WHITELISTED_ADO_READONLY_MCP_TOOLS = {
    "wit_work_item",
    "wit_query",
    "wit_backlog",
    "repo_pull_request",
    "repo_pull_request_org",
    "repo_pull_request_thread",
    "repo_repository",
    "repo_branch",
    "repo_file",
    "repo_search_commits",
    "pipelines_build",
    "pipelines_build_log",
    "pipelines_definition",
    "pipelines_run",
    "pipelines_artifact",
    "core_list_projects",
    "core_list_project_teams",
    "core_get_identity_ids",
    "advsec_get_alerts",
    "advsec_get_alert_details",
    "testplan",
    "testplan_show_test_results_from_build_id",
    "wiki",
    "work",
    "search_code",
    "search_wiki",
    "search_workitem",
}

WHITELISTED_HERDR_ENV_VARS = (
    "HERDR_SPAWN_ORIGIN", "HERDR_AGENT_ROLE", "HERDR_PARENT_PANE",
    "HERDR_ORIGIN_SENDER", "HERDR_ORIGIN_GOAL", "HERDR_ENV", "HERDR_PANE_ID",
)

WHITELISTED_CMD_BINARIES = {
    "gh",
    "git",
    "python3",
    "jq",
    "herdr",
    "shasum",
    "date",
    "sleep",
    "env",
    "ls",
    "cat",
    "head",
    "tail",
    "rg",
    "grep",
    "find",
}

FORBIDDEN_GIT_SUBCOMMANDS = {"commit", "push", "merge", "rebase", "reset", "checkout"}

GROK_QUOTA_PROTECTION_MODE = "grok_bot_no_supervision"

GROK_QUOTA_PROTECTION_REASON = "grok_bot_no_supervision_no_routine"

GROK_ENV_MODEL_VARS = (
    "M2_CALLER_BOT",
    "BOT_KIND",
    "GROK_BOT",
    "COPILOT_MODEL",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL",
    "DSH_MODEL",
    "HERDR_MODEL",
    "MODEL",
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

class RouterError(RuntimeError):
    """A user-actionable planning or dispatch error."""

class DiscoverySessions(list[dict[str, Any]]):
    """List-compatible session results carrying the workspace inventory."""

    def __init__(
        self,
        values: Iterable[dict[str, Any]] = (),
        *,
        workspaces: Iterable[dict[str, Any]] = (),
        discovery_source: str = "",
    ):
        super().__init__(values)
        self.workspaces = list(workspaces)
        self.discovery_source = discovery_source

def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default

def merge_target_prompt(target: dict[str, Any], message: str = "") -> str:
    """Preserve rich target instructions while retaining a distinct short title."""
    current = _text(target.get("sub_prompt"))
    extras: list[str] = []
    for key in RICH_PROMPT_KEYS:
        value = _text(target.get(key))
        if not value or key == "sub_prompt":
            continue
        if value == current or value == _text(message) or value in extras:
            continue
        if any(value in existing or existing in value for existing in extras):
            continue
        extras.append(value)
    if not extras:
        return current or _text(message)
    rich = "\n\n".join(extras)
    if not current or current == _text(message):
        return rich
    if current in rich or rich in current:
        return rich if len(rich) >= len(current) else current
    return f"{current}\n\n{rich}"

_EXPLORATORY_DISCUSSION_RE = re.compile(
    r"(?:先讨论|讨论一下|先聊聊|看看有哪些|先调研|不改代码|先探索|先分析|before\\s+(?:coding|implement)|discussion\\s+only)",
    re.IGNORECASE,
)

def is_exploratory_discussion_message(message: Any) -> bool:
    """Return whether an Owner explicitly requests Phase-0 discussion only."""
    return bool(_EXPLORATORY_DISCUSSION_RE.search(extract_dispatched_instruction(message)))

def _apply_phase0_discussion_lock(target: dict[str, Any], message: str) -> None:
    """Keep exploratory requests raw and prevent M3 from choosing a delivery posture."""
    target["phase0_discussion_only"] = True
    target["m2_action"] = "PHASE0_DISCUSSION_ONLY"
    target["sub_prompt"] = strip_herdr_origin_comments(message)
    target.pop("hot_append", None)
    target.pop("hot_append_assumption", None)

def _expand_cwd(raw_cwd: Any) -> str:
    text = _text(raw_cwd)
    if not text:
        return ""
    if text.startswith("~"):
        return str(Path(text).expanduser())
    return text

def _json_value(value: str) -> Any:
    path = Path(value).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)

def _parse_json_output(text: str) -> Any:
    raw = text.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some CLI wrappers print a short preamble before their JSON result.
        for marker in ("{", "["):
            index = raw.find(marker)
            if index >= 0:
                try:
                    return json.loads(raw[index:])
                except json.JSONDecodeError:
                    continue
    return None

def _repo_from(value: dict[str, Any]) -> str:
    for key in ("target_repo", "repo", "repository", "repository_name", "repo_root", "repositoryPath", "project", "goal"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    cwd = _text(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree"))
    if cwd:
        return Path(cwd.rstrip("/")).name
    return ""

def _session_id(value: dict[str, Any]) -> str:
    for key in ("session_id", "sessionId", "id", "herdr_target", "herdrTarget", "name", "pane_id"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    return ""

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

EVENTS_TAIL_MAX_BYTES = 64 * 1024

RECENT_SESSION_MAX_TURNS = 3

def _agent_session_id(value: dict[str, Any]) -> str:
    raw_as = value.get("agent_session") or value.get("agentSession")
    if isinstance(raw_as, dict):
        for key in ("value", "id", "session_id", "sessionId", "agent_session_id"):
            cand = _text(raw_as.get(key))
            if cand:
                return cand
    elif isinstance(raw_as, str) and _text(raw_as):
        return _text(raw_as)
    for key in ("agent_session_id", "agentSessionId", "copilot_session_id", "copilotSessionId"):
        cand = _text(value.get(key))
        if cand:
            return cand
    for key in ("session_id", "sessionId", "id"):
        cand = _text(value.get(key))
        if cand and _UUID_RE.match(cand):
            return cand
    return ""

def _parse_iso_epoch(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        fval = float(value)
        return fval / 1000.0 if fval > 1e11 else fval
    raw = _text(value)
    if not raw:
        return None
    if re.match(r"^\d+(?:\.\d+)?$", raw):
        fval = float(raw)
        return fval / 1000.0 if fval > 1e11 else fval
    norm = raw.replace("Z", "+00:00")
    norm = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", norm)
    try:
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.timestamp()
    except ValueError:
        return None

def _epoch_to_iso(epoch_val: Any) -> str:
    parsed = _parse_iso_epoch(epoch_val)
    if parsed is None:
        return ""
    cst = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(parsed, tz=cst).replace(microsecond=0).isoformat()

def _clean_single_line(text: Any, *, max_len: int = 320) -> str:
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", strip_herdr_origin_comments(text))
    compact = re.sub(r"\s+", " ", raw).strip()
    if len(compact) > max_len:
        return compact[: max_len - 1].rstrip() + "…"
    return compact

def _run_command(args: list[str], *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

ROLLUP_METADATA_KEYS = (
    "linear_issue", "parent_linear_issue", "handoff_dir", "goal_id", "depends_on",
    "upstream_waves", "rag_context_hint", "rag_hits", "sub_prompt", "terminal_title", "cwd",
    "m1_progress", "m1_stage_summary", "_handoff_discovered",
    "agent_session", "agent_session_id", "recent_session_context",
    "last_session_activity_at", "ledger_updated_at", "active_summary_source",
)

_LEDGER_TS_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b"
)

_NON_WORK_EXACT_TOKENS = {
    "aidash",
    "aidash-aidata",
    "pew",
    "pew-telemetry",
    "user-skills",
    "skills-workflow",
    "skills-howto",
    "skills-design",
    "skills-research-knowledge",
    "skills-retro-story",
    "skills-repo-ci",
    "skill-skill-contract",
    "voxpocket",
    "vitalstride",
    "helix",
    "davici",
    "nas",
    "nas-smart-home",
    "financial",
    "shared-design-system",
    "raven",
    "gateway-herdr-jev",
    "dsh-plugins",
    "learning-and-blog",
    "leepepe.github.io",
}

_WORK_SIGNAL_RE = re.compile(
    r"(?:\bbing-growth-sapphire-app\b|\bsapphire\b|\bsmartbanner\b|\bchina[-_ ]?bugs?\b"
    r"|\bchina\s+release\b|\bflightbook\b|\bwebiq\b|\bworkiq\b|\bteams-cli\b"
    r"|\binfo-teams\b|\binfo-email\b|\bemail-dsh\b|\bwork-recruitment\b"
    r"|\bintern\b|招聘|\bmy-pr-supervisor\b|\bpr-auto-review\b|\bdsat\b)",
    re.I,
)

_FORBIDDEN_SINGLE_PATH_CHARS = ("*", "?", "[", "]", "$", "`", ";", "|", "&", "<", ">", '"', "'")

_READONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "worktree",
    "branch",
}

_SAFE_GIT_BRANCH_FLAGS = {"--show-current", "-a", "--all", "-r", "--remotes", "--list"}

_FORBIDDEN_GIT_FLAGS = (
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--upload-pack",
    "--receive-pack",
    "--ext-diff",
    "--textconv",
    "--output",
    "--paginate",
    "-p",
)

_PROMPT_HEADER_RE = re.compile(
    r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|Read\s+access\s+covers\s+this\s+directory|"
    r"Do\s+you\s+want\s+to\s+use\s+this\s+tool|"
    r"\bCurate\s+Memory\b|curate\s+this\s+memory|"
    r"\bstore_memory\b|\bvote_memory\b|Confirm\s+folder\s+trust|"
    r"Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|\bCMD_CONFIRM\b|"
    r"Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
    r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command|"
    r"\bask_user\b|NEED_GO|NEED_OWNER_DECISION|Owner\s+Gate|Owner\s+decision\s+required|"
    r"Proceed\s+with|What\s+would\s+you\s+like|Which\s+option\s+would\s+you\s+like|"
    r"请选择方案|请确认方案|Question\s+for\s+Owner)",
    re.I,
)

_BUSINESS_DECISION_RE = re.compile(
    r"(?:\bask_user\b|NEED_GO|NEED_OWNER_DECISION|Owner\s+Gate|Owner\s+decision\s+required|"
    r"Proceed\s+with|What\s+would\s+you\s+like|Which\s+option\s+would\s+you\s+like|"
    r"请选择方案|请确认方案|Question\s+for\s+Owner)",
    re.I,
)

_COMPLETION_BOUNDARY_RE = re.compile(
    r"^(?:Completed\.|Done\.|Finished\.|[✓✔✗✘]\s+|\bExit\s+code\b|---+$|═+$|━+$)",
    re.I,
)

_FORBIDDEN_MCP_TOOL_KEYWORDS_RE = re.compile(
    r"(?:^|_)(?:delete|remove|destroy|drop|purge|wipe|retire|share|unshare|merge|bypass|"
    r"send|post|reply|exec|execute|run_pipeline|create_pipeline|rename_pipeline|"
    r"update_build|upload|attachment|diff_review|diff_comment|update_diff)(?:_|$)",
    re.I,
)

_FORBIDDEN_READONLY_MCP_KEYWORDS_RE = re.compile(
    r"(?:^|_)(?:write|create|update|delete|remove|destroy|drop|upsert|upload|link|unlink|"
    r"add|set|send|post|reply|merge|submit|vote|approve|reject|close|reopen|cancel|retry|"
    r"run_pipeline|assign|reorder|bypass|fork|push|commit|patch|edit|save|share|unshare|retire)(?:_|$)",
    re.I,
)

_VOLATILE_TUI_CHROME_RE = re.compile(
    r"(?:Session:\s*[\d.]+\s*AIC\s+used"
    r"|←\s*open\s+sidebar"
    r"|Interactive\s*·\s*Manual\s+Approval"
    r"|(?:Working|Thinking)\s*·\s*[\d.]+\s*[KMG]?i?B\b.*context\s*\(\d+%\))",
    re.I,
)
