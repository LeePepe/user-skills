"""Self-disclosing CLI argument parser, OAuth interactive login, and main entrypoint."""
from __future__ import annotations

from ._imports import *

SUBCOMMAND_ALIASES: dict[str, str] = {
    "idea": "idea",
    "ensure-goal": "ensure-goal",
    "ensure": "ensure-goal",
    "sync-ledger": "sync-ledger",
    "sync": "sync-ledger",
    "list-issues": "list-issues",
    "issues": "list-issues",
    "get-issue": "get-issue",
    "update-issue": "update-issue",
    "comment": "comment",
    "archive-completed": "archive-completed",
    "auth": "auth",
}


class _SelfDisclosingLinearSyncParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[linear-sync] CLI error: {message}\n"
            "  Fix: run `python3 skills/team-lead/scripts/linear-sync.py {ensure-goal,sync-ledger,list-issues,idea} --help`\n"
            "  Reference: skills/team-lead/references/ledger.md\n"
        )
        raise SystemExit(2)


def _normalize_argv(argv: Sequence[str] | None) -> list[str]:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens:
        return tokens
    value_flags = {
        "--title",
        "--description",
        "--team",
        "--state",
        "--label",
        "--project",
        "--mode",
        "--source",
        "--dedup-key",
        "--repo",
        "--offline-snapshot",
        "--handoff-dir",
        "--handoff",
        "--dir",
        "--root",
        "--issue",
        "--issue-id",
        "--id",
        "--event-note",
        "--query",
        "--limit",
        "--priority",
        "--assignee",
        "--delegate",
        "--add-label",
        "--body",
        "--retention-days",
        "--max-active-issues",
        "--header",
        "--client-id",
        "--client-secret",
        "--port",
    }
    subcmd_index: int | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in {"-h", "--help"}:
            return tokens
        if tok in SUBCOMMAND_ALIASES:
            subcmd_index = i
            break
        if tok == "--create-as-user":
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-") and tokens[i + 1] not in SUBCOMMAND_ALIASES:
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-"):
            if "=" not in tok and tok in value_flags and i + 1 < len(tokens):
                i += 2
            else:
                i += 1
        else:
            break
    if subcmd_index is not None:
        canonical = SUBCOMMAND_ALIASES[tokens[subcmd_index]]
        return [canonical, *tokens[:subcmd_index], *tokens[subcmd_index + 1 :]]
    return tokens


def _resolve_handoff_dir_arg(args: argparse.Namespace) -> Path:
    raw = getattr(args, "handoff_dir", None) or getattr(args, "handoff_dir_pos", None)
    if not raw:
        raise LinearSyncError(
            "Missing required --handoff-dir <dir> (or positional <handoff_dir>)"
        )
    args.handoff_dir = str(raw)
    return Path(str(raw)).expanduser().resolve()


def _resolve_issue_id_arg(args: argparse.Namespace) -> str:
    raw = getattr(args, "id", None)
    if not raw:
        raise LinearSyncError("Missing required --id / --issue-id / --issue <identifier>")
    args.id = str(raw)
    return str(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingLinearSyncParser(prog="linear-sync.py", description="Sync team-lead ledgers with Linear.")
    parser.add_argument("--json", action="store_true", default=True)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    idea = subparsers.add_parser("idea", help="Capture an idea in the Linear team inbox.")
    idea.add_argument("--title", required=True)
    idea.add_argument("--description", default="")
    idea.add_argument("--team", default="TIA")
    idea.add_argument("--state", default="Triage")
    idea.add_argument("--label", default="")
    idea.add_argument("--project", default="")
    idea.add_argument("--mode", choices=("off", "observe", "auto"))
    idea.add_argument("--source", default="")
    idea.add_argument("--dedup-key", default="")
    idea.add_argument("--repo", default="")
    idea.add_argument("--offline-snapshot", default="")
    idea.add_argument("--json", action="store_true", default=True)

    ensure = subparsers.add_parser(
        "ensure-goal",
        aliases=["ensure"],
        help="Bind or create the Linear issue for a Goal.",
    )
    ensure.add_argument("handoff_dir_pos", nargs="?", default=None, help="Optional positional handoff directory.")
    ensure.add_argument("--handoff-dir", "--handoff", "--dir", "--root", dest="handoff_dir", default=None)
    ensure.add_argument("--issue", "--issue-id", "--id", dest="issue", default="")
    ensure.add_argument("--team", default="TIA")
    ensure.add_argument("--project", default="")
    ensure.add_argument("--mode", choices=("off", "observe", "auto"))
    ensure.add_argument("--offline-snapshot", default="")
    ensure.add_argument("--record-observe", action="store_true")
    ensure.add_argument("--create-as-user", action="store_true")
    ensure.add_argument("--json", action="store_true", default=True)

    sync = subparsers.add_parser(
        "sync-ledger",
        aliases=["sync"],
        help="Synchronize a Goal ledger to its Linear issue.",
    )
    sync.add_argument("handoff_dir_pos", nargs="?", default=None, help="Optional positional handoff directory.")
    sync.add_argument("--handoff-dir", "--handoff", "--dir", "--root", dest="handoff_dir", default=None)
    sync.add_argument("--event-note", default="")
    sync.add_argument("--quiet", action="store_true")
    sync.add_argument("--project", default="")
    sync.add_argument("--mode", choices=("off", "observe", "auto"))
    sync.add_argument("--offline-snapshot", default="")
    sync.add_argument("--record-observe", action="store_true")
    sync.add_argument("--create-as-user", action="store_true")
    sync.add_argument("--json", action="store_true", default=True)

    list_issues = subparsers.add_parser(
        "list-issues",
        aliases=["issues"],
        help="List issues on a Linear team.",
    )
    list_issues.add_argument("--team", default="TIA")
    list_issues.add_argument("--state", default="")
    list_issues.add_argument("--project", default="")
    list_issues.add_argument("--query", default="")
    list_issues.add_argument("--limit", type=int, default=25)
    list_issues.add_argument("--json", action="store_true", default=True)

    get_issue = subparsers.add_parser("get-issue", help="Show a Linear issue.")
    get_issue.add_argument("--id", "--issue-id", "--issue", dest="id", required=True)
    get_issue.add_argument("--json", action="store_true", default=True)

    update = subparsers.add_parser("update-issue", help="Update a Linear issue.")
    update.add_argument("--id", "--issue-id", "--issue", dest="id", required=True)
    update.add_argument("--title")
    update.add_argument("--description")
    update.add_argument("--state")
    update.add_argument("--project")
    update.add_argument("--priority", type=int, choices=range(5))
    update.add_argument("--assignee")
    update.add_argument("--delegate")
    update.add_argument("--add-label", action="append", default=[])
    update.add_argument("--mode", choices=("off", "observe", "auto"))
    update.add_argument("--json", action="store_true", default=True)

    comment = subparsers.add_parser("comment", help="Comment on a Linear issue.")
    comment.add_argument("--id", "--issue-id", "--issue", dest="id", required=True)
    comment.add_argument("--body", required=True)
    comment.add_argument("--create-as-user")
    comment.add_argument("--mode", choices=("off", "observe", "auto"))
    comment.add_argument("--json", action="store_true", default=True)

    archive = subparsers.add_parser("archive-completed", help="Archive completed and canceled Linear issues under keep_2_days_and_cap_150 policy.")
    archive.add_argument("--team", default="TIA")
    archive.add_argument("--retention-days", type=int, default=2)
    archive.add_argument("--max-active-issues", type=int, default=150)
    archive.add_argument("--mode", choices=("off", "observe", "auto"))
    archive.add_argument("--json", action="store_true", default=True)
    archive.add_argument("--offline-snapshot", default="")

    auth = subparsers.add_parser("auth", help="Manage Linear credentials.")
    auth.add_argument("--json", action="store_true", default=True)
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_set = auth_sub.add_parser("set")
    auth_set.add_argument("--key-stdin", action="store_true")
    auth_set.add_argument("--header", default="Authorization")
    auth_set.add_argument("--json", action="store_true", default=True)
    oauth_app = auth_sub.add_parser("oauth-app")
    oauth_app.add_argument("--key-stdin", action="store_true")
    oauth_app.add_argument("--header", default="Authorization")
    oauth_app.add_argument("--json", action="store_true", default=True)
    oauth_login = auth_sub.add_parser("oauth-login")
    oauth_login.add_argument("--client-id", default="")
    oauth_login.add_argument("--client-secret", default="")
    oauth_login.add_argument("--port", type=int, default=8765)
    oauth_login.add_argument("--agent-scopes", action="store_true", help="Request app:assignable/app:mentionable; requires workspace-admin approval.")
    oauth_login.add_argument("--json", action="store_true", default=True)
    auth_refresh = auth_sub.add_parser("refresh")
    auth_refresh.add_argument("--json", action="store_true", default=True)
    auth_show = auth_sub.add_parser("show")
    auth_show.add_argument("--json", action="store_true", default=True)
    auth_remove = auth_sub.add_parser("remove")
    auth_remove.add_argument("--force", action="store_true")
    auth_remove.add_argument("--json", action="store_true", default=True)
    return parser


def _oauth_interactive_login(client_id: str, client_secret: str, port: int = 8765, *, agent_scopes: bool = False) -> dict[str, Any]:
    import http.server
    import subprocess

    record = _credential_record()
    cid = (client_id or str(record.get("clientId") or "")).strip()
    csec = (client_secret or str(record.get("clientSecret") or "")).strip()
    if not (cid and csec):
        raise LinearSyncError("oauth-login requires --client-id and --client-secret (or saved clientId/clientSecret)")
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    scopes = "read,write,issues:create,comments:create"
    if agent_scopes:
        scopes += ",app:assignable,app:mentionable"
    auth_url = f"{OAUTH_AUTHORIZE_URL}?" + urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "actor": "app",
    })
    result: dict[str, Any] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            code = (parse_qs(parsed.query).get("code") or [""])[0]
            if not code:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code")
                return
            data = urlencode({
                "grant_type": "authorization_code",
                "client_id": cid,
                "client_secret": csec,
                "redirect_uri": redirect_uri,
                "code": code,
            }).encode("utf-8")
            req = Request(
                OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urlopen(req, timeout=15) as resp:
                tok = json.loads(resp.read().decode("utf-8"))
            access_token = str(tok.get("access_token") or "").strip()
            refresh_token = str(tok.get("refresh_token") or "").strip() or None
            expires_in = tok.get("expires_in")
            expires_at = (
                datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + int(expires_in), tz=timezone.utc).isoformat()
                if isinstance(expires_in, (int, float))
                else None
            )
            header_val = f"Bearer {access_token}"
            saved = save_credentials(
                header_val,
                "Authorization",
                actor="app",
                refresh_token=refresh_token,
                client_id=cid,
                client_secret=csec,
                expires_at=expires_at,
            )
            result.update({
                "ok": True,
                "command": "auth oauth-login",
                "path": str(saved),
                "mode": "0600",
                "masked_key": mask_value(header_val),
                "actor": "app",
                "has_refresh_token": bool(refresh_token),
                "expires_at": expires_at,
            })
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Linear GTT Bot (actor=app) 授权成功（已保存 refresh_token 自动续期）！可以关闭此页面。".encode("utf-8"))

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 120
    subprocess.run(["open", auth_url], check=False)
    while "ok" not in result:
        server.handle_request()
        if "ok" in result:
            break
    server.server_close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    normalized_argv = _normalize_argv(argv)
    args = parser.parse_args(normalized_argv)
    subcmd = SUBCOMMAND_ALIASES.get(args.subcommand, args.subcommand)
    args.subcommand = subcmd
    try:
        if subcmd == "idea":
            payload = cmd_idea(args)
        elif subcmd == "ensure-goal":
            payload = cmd_ensure_goal(args)
        elif subcmd == "sync-ledger":
            payload = cmd_sync_ledger(args)
        elif subcmd == "list-issues":
            payload = cmd_list_issues(args)
        elif subcmd == "get-issue":
            _resolve_issue_id_arg(args)
            payload = cmd_get_issue(args)
        elif subcmd == "update-issue":
            _resolve_issue_id_arg(args)
            payload = cmd_update_issue(args)
        elif subcmd == "comment":
            _resolve_issue_id_arg(args)
            payload = cmd_comment(args)
        elif subcmd == "archive-completed":
            payload = cmd_archive_completed(args)
        elif subcmd == "auth":
            path = _credential_path()
            if args.auth_command in {"set", "oauth-app"}:
                key = sys.stdin.readline().strip() if args.key_stdin or not sys.stdin.isatty() else getpass.getpass("Linear API key: ")
                actor = "app" if args.auth_command == "oauth-app" else None
                saved = save_credentials(key, args.header, actor=actor)
                payload = {"ok": True, "command": f"auth {args.auth_command}", "path": str(saved), "mode": "0600", "masked_key": mask_value(key), **({"actor": actor} if actor else {})}
            elif args.auth_command == "oauth-login":
                options = {"agent_scopes": True} if args.agent_scopes else {}
                payload = _oauth_interactive_login(args.client_id, args.client_secret, args.port, **options)
            elif args.auth_command == "refresh":
                refreshed = refresh_oauth_credentials()
                if not refreshed:
                    raise LinearSyncError("Unable to refresh OAuth token (missing refreshToken/clientId/clientSecret or refresh rejected)")
                rec = _credential_record()
                payload = {
                    "ok": True,
                    "command": "auth refresh",
                    "path": str(path),
                    "masked_key": mask_value(refreshed),
                    "actor": rec.get("actor"),
                    "has_refresh_token": bool(rec.get("refreshToken")),
                    "expires_at": rec.get("expiresAt"),
                }
            elif args.auth_command == "show":
                key, source, source_path = load_credential_info()
                rec = _credential_record()
                payload = {"ok": True, "command": "auth show", "configured": bool(key),
                           "source": source, "path": str(source_path or path), "masked_key": mask_value(key or ""),
                           **({"actor": rec["actor"]} if rec.get("actor") else {}),
                           "has_refresh_token": bool(rec.get("refreshToken")),
                           **({"expires_at": rec["expiresAt"]} if rec.get("expiresAt") else {}),
                           "mode": oct((source_path or path).stat().st_mode & 0o777)[2:].zfill(4) if (source_path or path).exists() else None}
            else:
                removed = False
                if _has_symlink_component(path):
                    raise LinearSyncError(f"Refusing symlinked credential path: {path}")
                if path.exists():
                    path.unlink()
                    removed = True
                payload = {"ok": True, "command": "auth remove", "removed": removed}
        else:
            raise LinearSyncError(f"Unknown subcommand: {args.subcommand}")
    except LinearSyncError as exc:
        if subcmd == "sync-ledger" and getattr(args, "quiet", False):
            return 0
        sys.stderr.write(
            f"linear-sync: {exc}\n"
            "  Fix: run `python3 skills/team-lead/scripts/linear-sync.py --help`\n"
            "  Reference: skills/team-lead/references/ledger.md\n"
        )
        return 2
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0
