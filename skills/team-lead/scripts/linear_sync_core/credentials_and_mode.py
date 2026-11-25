"""Credentials, OAuth token refresh, and execution mode resolution for Linear sync."""
from __future__ import annotations

from ._imports import *

API_URL = "https://api.linear.app/graphql"
OAUTH_TOKEN_URL = "https://api.linear.app/oauth/token"
OAUTH_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
OWNER_USER_ID = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
ISSUE_HISTORY_FIELDS = (
    "history { nodes { id createdAt actor { id name displayName app } "
    "botActor { id name type } fromState { id name type } toState { id name type } "
    "fromTitle toTitle fromPriority toPriority updatedDescription } }"
)
MARKER_START = "<!-- GTT-LINEAR-SYNC:START -->"
MARKER_END = "<!-- GTT-LINEAR-SYNC:END -->"
KEY_RE = re.compile(r"^\s*(?:export\s+)?LINEAR_API_KEY\s*=\s*(['\"]?)([^'\"#\s]+)\1\s*$", re.MULTILINE)
ISSUE_RE = re.compile(r"(?mi)^\s*-\s*\**(?:Linear(?:\s+Issue)?)\**\s*:\s*([A-Z][A-Z0-9]*-\d+)(?:\s+\(([^)]+)\))?")
EVENT_RE = re.compile(r"(?m)^\s*-\s+[^|]+\s+\|\s+(.*)$")
PR_RE = re.compile(r"(?:github\.com/.+/(?:pull|pulls)/\d+|PR(?:\s*#|=)\s*\d+)", re.IGNORECASE)
DEFAULT_MODEL_PRICES = {
    "gpt-5.6-luna": {"input_per_mtok": 1.25, "output_per_mtok": 10.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "gpt-5.6-sol": {"input_per_mtok": 1.25, "output_per_mtok": 10.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "gemini-4-argon": {"input_per_mtok": 1.25, "output_per_mtok": 5.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "gemini-4-carbon": {"input_per_mtok": 1.25, "output_per_mtok": 5.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "mai-code-2-flash": {"input_per_mtok": 0.30, "output_per_mtok": 2.50, "cache_read_per_mtok": 0.03, "cache_write_per_mtok": 0.30},
    "default": {"input_per_mtok": 1.0, "output_per_mtok": 4.0, "cache_read_per_mtok": 0.1, "cache_write_per_mtok": 1.0},
}


class LinearSyncError(RuntimeError):
    """Expected, user-facing Linear synchronization failure."""


def mask_value(value: str) -> str:
    return "****" if len(value) <= 4 else f"{value[:2]}****{value[-2:]}"


def _credential_path() -> Path:
    return Path(os.environ.get("GTT_LINEAR_CREDENTIALS_FILE", "~/.config/linear/credentials.json")).expanduser()


def _clip_credential_path() -> Path:
    return Path(os.environ.get("CLIP_HOME", "~/.clip")).expanduser() / "linear" / "credentials.json"


def _has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == current.parent:
            return False
        current = current.parent


def load_credential_info() -> tuple[str | None, str | None, Path | None]:
    for path in (_credential_path(), _clip_credential_path()):
        try:
            if _has_symlink_component(path):
                raise LinearSyncError(f"Refusing symlinked credential path: {path}")
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                value = str(data.get("headerValue") or data.get("key") or "").strip()
                if value:
                    return value, "credentials", path
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    for path in (Path("~/.zshenv").expanduser(), Path("~/.zshrc").expanduser()):
        value = _read_key_file(path)
        if value:
            return value, str(path), path
    return None, None, None


def _credential_record() -> dict[str, Any]:
    path = _credential_path()
    try:
        if _has_symlink_component(path):
            raise LinearSyncError(f"Refusing symlinked credential path: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _oauth_app_enabled() -> bool:
    record = _credential_record()
    header_value = str(record.get("headerValue") or "")
    return (
        str(record.get("actor") or "").casefold() == "app"
        or (
            header_value.startswith("Bearer ")
            and bool(header_value[len("Bearer "):].strip())
        )
    )


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    if _has_symlink_component(path):
        raise LinearSyncError(f"Refusing symlinked credential path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.chmod(mode)
    os.replace(temp, path)
    path.chmod(mode)


def save_credentials(
    key: str,
    header_name: str = "Authorization",
    actor: str | None = None,
    *,
    refresh_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    expires_at: str | None = None,
) -> Path:
    existing = _credential_record()
    payload: dict[str, Any] = {
        "type": "header", "headerName": header_name, "headerValue": key,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    effective_actor = actor if actor is not None else existing.get("actor")
    if effective_actor:
        payload["actor"] = effective_actor
    for field, val in (
        ("refreshToken", refresh_token if refresh_token is not None else existing.get("refreshToken")),
        ("clientId", client_id if client_id is not None else existing.get("clientId")),
        ("clientSecret", client_secret if client_secret is not None else existing.get("clientSecret")),
        ("expiresAt", expires_at if expires_at is not None else existing.get("expiresAt")),
    ):
        if val:
            payload[field] = val
    path = _credential_path()
    _atomic_write_text(path, json.dumps(payload) + "\n")
    return path


def refresh_oauth_credentials() -> str | None:
    record = _credential_record()
    refresh_token = str(record.get("refreshToken") or "").strip()
    client_id = str(record.get("clientId") or "").strip()
    client_secret = str(record.get("clientSecret") or "").strip()
    if not (refresh_token and client_id and client_secret):
        return None
    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    access_token = str(tok.get("access_token") or "").strip()
    if not access_token:
        return None
    header_value = f"Bearer {access_token}"
    new_refresh = str(tok.get("refresh_token") or refresh_token).strip()
    expires_in = tok.get("expires_in")
    expires_at = (
        datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + int(expires_in), tz=timezone.utc).isoformat()
        if isinstance(expires_in, (int, float))
        else None
    )
    save_credentials(
        header_value,
        str(record.get("headerName") or "Authorization"),
        actor=str(record.get("actor") or "app"),
        refresh_token=new_refresh,
        client_id=client_id,
        client_secret=client_secret,
        expires_at=expires_at,
    )
    return header_value


def resolve_mode(
    explicit_mode: str | None,
    *,
    subcommand: str = "",
    source: str = "",
    handoff_dir: Path | None = None,
    quiet: bool = False,
) -> tuple[str, str | None]:
    value = (explicit_mode or "").strip().lower()
    if value:
        if value not in {"off", "observe", "auto"}:
            raise LinearSyncError(f"invalid mode: {value}")
        return value, None
    value = os.environ.get("GTT_LINEAR_MODE", "").strip().lower()
    if value:
        if value not in {"off", "observe", "auto"}:
            raise LinearSyncError(f"invalid GTT_LINEAR_MODE: {value}")
        return value, None
    if handoff_dir and _is_isolated_handoff_dir(handoff_dir) and quiet and os.environ.get("GTT_LINEAR_SYNC") != "1":
        return "off", "isolated_handoff_dir"
    if os.environ.get("GTT_LINEAR_SYNC") == "0":
        return "off", "env_disabled"
    if subcommand == "idea" and source.strip().lower() in {"proactive", "retro", "retro-loop", "telemetry", "cron"}:
        return "observe", None
    return "auto", None


def _is_isolated_handoff_dir(handoff_dir: Path) -> bool:
    resolved = handoff_dir.expanduser().resolve()
    isolated_roots = (
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/folders"),
        Path("/private/var/folders"),
    )
    if any(resolved == root or root in resolved.parents for root in isolated_roots):
        return True
    if any(part.lower().startswith(("tmp", ".tmp")) for part in resolved.parts):
        return True
    return resolved.parent.name != "handoff"


def _read_key_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = KEY_RE.search(text)
    return match.group(2) if match else None


def load_api_key() -> str | None:
    """Read credentials from env, protected files, then shell startup files."""
    value = os.environ.get("LINEAR_API_KEY", "").strip()
    if value:
        return value
    return load_credential_info()[0]
