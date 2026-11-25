from .common import *
from .data_loaders import *
from .trace_builders import *
from .collection import *
from .exporters import *
from skill_runtime import SelfDisclosingArgumentParser as _RuntimeArgumentParser, compact_json_dumps


class SelfDisclosingArgumentParser(_RuntimeArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {message}\n")
        sys.stderr.write("Try --help for supported trace backfill options.\n")
        sys.stderr.write("Reference: skills/retro-loop/SKILL.md\n")
        raise SystemExit(2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = SelfDisclosingArgumentParser(
        description="Backfill historical Goal & Session traces into Arize Phoenix and Langfuse."
    )
    parser.add_argument(
        "--target",
        choices=("both", "phoenix", "langfuse"),
        default="both",
        help="Observability target(s) to export traces into (default: both).",
    )
    parser.add_argument("--limit-goals", type=int, default=35, help="Maximum historical Goal traces to export.")
    parser.add_argument("--limit-sessions", type=int, default=55, help="Maximum Session Prompt traces to export.")
    parser.add_argument("--handoff-root", type=Path, default=DEFAULT_HANDOFF_ROOT)
    parser.add_argument("--copilot-sessions-dir", type=Path, default=DEFAULT_COPILOT_SESSIONS_DIR)
    parser.add_argument("--usage-jsonl", type=Path, default=DEFAULT_USAGE_JSONL)
    parser.add_argument("--warehouse-db", type=Path, default=DEFAULT_WAREHOUSE_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_OBS_CONFIG)
    parser.add_argument("--phoenix-project", default=None, help="Override Phoenix project name.")
    parser.add_argument("--dry-run", action="store_true", help="Build trace trees and validate spans without HTTP export.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output.")
    parser.add_argument("--summary", action="store_true", help="Emit bounded compact JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit_goals < 0 or args.limit_sessions < 0:
        print(json.dumps({"ok": False, "error": "limits must be non-negative"}), file=sys.stderr)
        return 2

    skills_root = Path(__file__).resolve().parents[3]
    goal_traces, session_traces = collect_historical_traces(
        handoff_root=args.handoff_root.expanduser(),
        copilot_sessions_dir=args.copilot_sessions_dir.expanduser(),
        usage_jsonl=args.usage_jsonl.expanduser(),
        warehouse_db=args.warehouse_db.expanduser(),
        limit_goals=args.limit_goals,
        limit_sessions=args.limit_sessions,
        skills_root=skills_root,
    )
    all_traces = goal_traces + session_traces
    summary = summarize_traces(goal_traces, session_traces)
    cfg = load_obs_config(args.config.expanduser())
    project_name = args.phoenix_project or cfg["phoenix_project"]

    exports: dict[str, Any] = {}
    ok = True
    if not args.dry_run and all_traces:
        try:
            if args.target in ("both", "phoenix"):
                exports["phoenix"] = export_to_phoenix(
                    all_traces,
                    otlp_url=cfg["phoenix_otlp_url"],
                    project_name=project_name,
                )
                ok = ok and bool(exports["phoenix"].get("ok"))
            if args.target in ("both", "langfuse"):
                exports["langfuse"] = export_to_langfuse(
                    all_traces,
                    base_url=cfg["langfuse_url"],
                    public_key=cfg["langfuse_public_key"],
                    secret_key=cfg["langfuse_secret_key"],
                )
                ok = ok and bool(exports["langfuse"].get("ok"))
        except (HTTPError, URLError, ValueError, RuntimeError, OSError) as exc:
            ok = False
            exports["error"] = str(exc)

    result = {
        "ok": ok,
        "dry_run": args.dry_run,
        "target": args.target,
        "summary": summary,
        "exports": exports,
    }
    if args.json:
        if args.summary:
            print(compact_json_dumps(result, summary=True, preserve_keys=("ok", "dry_run", "target", "summary")))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"[trace_backfill_exporter] ok={ok} dry_run={args.dry_run} target={args.target} "
            f"goals={summary['goal_traces']} sessions={summary['session_traces']} spans={summary['total_spans']}"
        )
        if not ok and "error" in exports:
            print(f"Error: {exports['error']}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
