from __future__ import annotations

from . import base as _base
from . import metrics as _metrics
from . import contracts as _contracts
from . import horizon as _horizon
from . import analysis as _analysis
from . import commands as _commands
globals().update({k: v for module in (_base, _metrics, _contracts, _horizon, _analysis, _commands) for k, v in vars(module).items() if not k.startswith("__")})

_PLAYBOOK_REF = "skills/agent-retro/references/agent-retro-playbook.md"
_EVAL_REF = "skills/agent-retro/references/eval-cases.json"


def _print_guidance(message: str) -> None:
    text = " ".join(str(message or "").split()).strip() or "invalid arguments"
    print(f"agent-retro: {text}", file=sys.stderr)
    print(f"See {_PLAYBOOK_REF}", file=sys.stderr)
    print(f"See {_EVAL_REF}", file=sys.stderr)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _print_guidance(message)
        raise SystemExit(2)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--role")
    parser.add_argument("--layer", help="comma-separated metric layers")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--mode")
    parser.add_argument("--aspects")
    parser.add_argument("--dir", dest="handoff_dir")
    parser.add_argument("--handoff-dir", dest="handoff_dir")
    parser.add_argument("--handoff-root", dest="handoff_root")


def _canonical_skill(candidate: str | None) -> str | None:
    value = str(candidate or "").strip()
    if not value:
        return None
    if is_valid_skill_slug(value, require_repo_dir=True):
        return value
    for pattern in (
        r"^(?:agent|skill)-retro-([a-z0-9][a-z0-9_-]*)$",
        r"^agent-retro-([a-z0-9][a-z0-9_-]*)-(?:goal|task)-.*$",
    ):
        match = re.match(pattern, value)
        if match and is_valid_skill_slug(match.group(1), require_repo_dir=True):
            return match.group(1)
    return None


def _diagnose_skill_from_context(goal_id: str | None) -> str | None:
    if resolved := _canonical_skill(goal_id):
        return resolved
    cwd_name = Path.cwd().name
    return _canonical_skill(cwd_name)


def _normalize_args(args: argparse.Namespace) -> None:
    aspects = str(getattr(args, "aspects", "") or "").strip()
    if hasattr(args, "layer") and aspects and not str(getattr(args, "layer", "") or "").strip():
        args.layer = aspects
    if args.command != "diagnose":
        return
    explicit = [str(getattr(args, key, "") or "").strip() for key in ("skill", "skill_pos")]
    explicit = [item for item in explicit if item]
    for candidate in (_canonical_skill(getattr(args, "skill", None)), _canonical_skill(getattr(args, "skill_pos", None))):
        if candidate:
            args.skill = candidate
            return
    if explicit:
        raise ValueError(f"unknown skill identifier: {explicit[0]}")
    for candidate in (_diagnose_skill_from_context(getattr(args, "goal_id", None)),):
        if candidate:
            args.skill = candidate
            return
    raise ValueError(
        "diagnose requires --skill, positional <skill>, --goal-id, or a worktree named agent-retro-<skill>/skill-retro-<skill>"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="agent_retro.py", description="Bounded offline multi-agent trajectory retro")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("collect", help="collect a bounded telemetry snapshot")
    _common(p)
    p.set_defaults(days=3)
    p.add_argument("--date")
    p.add_argument("--output", type=Path, default=DEFAULT_COLLECT_OUTPUT)
    p.add_argument("--watermark-file", type=Path, default=None)

    p = sub.add_parser("probe", help="fast bounded five-layer probe")
    _common(p)

    p = sub.add_parser("horizon", help="profile successful long-horizon sessions")
    _common(p)
    p.add_argument("--min-turns", type=int, default=40)
    p.add_argument("--min-duration-min", type=float, default=15)

    p = sub.add_parser("triage", help="cheap worker triage")
    _common(p)
    p.add_argument("--pick-top", type=int, choices=(1, 2, 3), default=3)
    p.add_argument("--pick-one", action="store_true")
    p.add_argument("--use-jev", action="store_true")
    p.add_argument("--mock-jev-json")
    p.add_argument("--emit-briefs")
    p.add_argument("--include-pre-fix", action="store_true")

    p = sub.add_parser("diagnose", help="deep attribution for one skill")
    _common(p)
    p.add_argument("skill_pos", nargs="?")
    p.add_argument("--skill")
    p.add_argument("--goal-id")

    p = sub.add_parser("emit-briefs", help="write non-clobbering specs and briefs")
    _common(p)
    p.add_argument("--skill")
    p.add_argument("--pick-top", type=int, choices=(1, 2, 3), default=3)
    p.add_argument("--pick-one", action="store_true")
    p.add_argument("--emit-briefs")

    p = sub.add_parser("rollup", help="aggregate receipts into a Dual-Zone issue")
    _common(p)
    p.add_argument("--date")
    p.add_argument("--issue-brief", action="store_true")

    p = sub.add_parser("file-issue", help="prepare or explicitly apply an issue publication")
    _common(p)
    p.add_argument("--date")
    p.add_argument("--server", "--server-url", dest="server", default="")
    p.add_argument("--workspace", "--workspace-id", dest="workspace", default="")
    p.add_argument("--owner", "--assignee-id", dest="owner", default="")
    p.add_argument("--agent", default="agent-retro")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--sync-linear", action="store_true")

    p = sub.add_parser("setup-autopilot", help="prepare the independent run_only schedule")
    _common(p)
    p.add_argument("--server", "--server-url", dest="server", default="")
    p.add_argument("--workspace", "--workspace-id", dest="workspace", default="")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--receipt", type=Path, default=None)

    p = sub.add_parser("record-m1-receipt", help="write an R10 M1 outcome receipt")
    p.add_argument("--skill", required=True)
    p.add_argument("--goal-id", required=True)
    p.add_argument("--status", required=True, choices=("FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"))
    p.add_argument("--merge-sha", "--commit-sha", dest="merge_sha")
    p.add_argument("--head-sha")
    p.add_argument("--pr", "--pr-url", dest="pr")
    p.add_argument("--pr-number")
    p.add_argument("--m1-tl-agent-name")
    p.add_argument("--writers-spawned")
    p.add_argument("--reviewers-spawned")
    p.add_argument("--summary", default="")
    p.add_argument("--summary-file")
    p.add_argument("--mode")
    p.add_argument("--aspects")
    p.add_argument("--dir", "--handoff-dir", "--handoff-root", "--emit-briefs", dest="handoff_root", default="~/Development/handoff")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("emit-relay", help="emit a host-to-guest Wave 2 relay")
    p.add_argument("--from", dest="from_skill", required=True)
    p.add_argument("--to", dest="to_skill", required=True)
    p.add_argument("--requirement", required=True)
    p.add_argument("--mode")
    p.add_argument("--aspects")
    p.add_argument("--summary-file")
    p.add_argument("--dir", "--handoff-dir", "--handoff-root", "--emit-briefs", dest="handoff_root", default="~/Development/handoff")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("inspect-json", aliases=["json-facts"], help="read-only JSON facts collector")
    p.add_argument("paths", nargs="*")
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--dir", action="append", default=[])
    p.add_argument("--keys", default="")
    p.add_argument("--head", "--limit", dest="limit", type=int, default=3)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _normalize_args(args)
        if args.command == "collect": result = command_collect(args)
        elif args.command == "probe": result = command_probe(args)
        elif args.command == "horizon": result = command_horizon(args)
        elif args.command == "triage": result = command_triage(args)
        elif args.command == "diagnose": result = command_diagnose(args)
        elif args.command == "emit-briefs": result = command_emit(args)
        elif args.command == "record-m1-receipt": result = command_record_receipt(args)
        elif args.command == "emit-relay": result = command_emit_relay(args)
        elif args.command in {"inspect-json", "json-facts"}: result = command_inspect_json(args)
        elif args.command == "rollup": result = command_rollup(args)
        elif args.command == "file-issue": result = command_file_issue(args)
        else: result = command_autopilot(args)
        exit_code = 0
        if isinstance(result, dict):
            if result.get("_stderr_hint"):
                _print_guidance(str(result["_stderr_hint"]))
            exit_code = int(result.pop("_exit_code", 0) or 0)
            result.pop("_stderr_hint", None)
        _compact_print(result, args.json, getattr(args, "summary", False))
        return exit_code
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print_guidance(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
