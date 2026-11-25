from __future__ import annotations

from .core_common import *
from .core_eval import *
from .core_facts import *
from .core_routing import *

def _add_output_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument("--json", action="store_true", help="Emit machine-readable JSON (the default output format).")
    command.add_argument("--summary", "--compact", dest="compact", action="store_true", help="Bound output to a compact summary.")


def _add_repo_aliases(command: argparse.ArgumentParser, *, default: str | None = None) -> None:
    command.add_argument("--repo", "--root", "--dir", "--target-dir", dest="repo", default=default)


def parser() -> argparse.ArgumentParser:
    root_parser = _SelfDisclosingContractParser()
    sub = root_parser.add_subparsers(dest="command", parser_class=_SelfDisclosingContractParser)

    init = sub.add_parser("init")
    init.add_argument("--skill", required=True); init.add_argument("--category", required=True)
    init.add_argument("--version", default="1.0.0", help="Semantic version X.Y.Z for .claude-plugin/plugin.json")
    init.add_argument("--design", action="store_true"); init.add_argument("--repo", default=None); _add_output_flags(init)

    sink = sub.add_parser("sink-rule")
    sink.add_argument("--skill", required=True); sink.add_argument("--kind", choices=["regex-gate", "case-card", "mental-model"], required=True)
    sink.add_argument("--id"); sink.add_argument("--target-file"); sink.add_argument("--replace-row"); sink.add_argument("--section"); sink.add_argument("--value", default="")
    sink.add_argument("--platforms", default="desktop,mobile,web"); sink.add_argument("--dimensions", default=""); sink.add_argument("--containers", default="")
    sink.add_argument("--title", default=""); sink.add_argument("--severity", default="Medium"); sink.add_argument("--regex", default=""); sink.add_argument("--remediation", default="")
    sink.add_argument("--bad-snippet", default=None); sink.add_argument("--good-snippet", default=None); _add_output_flags(sink)

    add_p = sub.add_parser("add-case")
    add_p.add_argument("--skill", required=True); add_p.add_argument("--repo", default=None); add_p.add_argument("--drawer", default="references/eval-cases.json")
    add_p.add_argument("--id", default="auto"); add_p.add_argument("--title", required=True); add_p.add_argument("--tags", default=""); add_p.add_argument("--routes", default=""); add_p.add_argument("--layers", default="")
    add_p.add_argument("--platforms", default=""); add_p.add_argument("--dimensions", default=""); add_p.add_argument("--containers", default=""); add_p.add_argument("--symptom", default=""); add_p.add_argument("--fix-rule", default="")
    add_p.add_argument("--forbidden-regex", default=""); add_p.add_argument("--bad-snippet", default=None); add_p.add_argument("--good-snippet", default=None); add_p.add_argument("--cmd", default=""); add_p.add_argument("--expect-exit", type=int, default=0)
    add_p.add_argument("--expect-contains", default=""); add_p.add_argument("--expect-json-subset", default=""); add_p.add_argument("--file", default=""); add_p.add_argument("--must-contain", default=""); add_p.add_argument("--must-not-contain", default="")
    add_p.add_argument("--negative-cmd", default="", help="Standalone command self-test requiring a genuine nonzero exit"); add_p.add_argument("--expect-fail-exit", type=int, default=None)
    add_p.add_argument("--negative-stdout-contains", default=""); add_p.add_argument("--negative-stderr-contains", default=""); _add_output_flags(add_p)

    eval_p = sub.add_parser("eval")
    eval_group = eval_p.add_mutually_exclusive_group(required=False); eval_group.add_argument("--all", action="store_true"); eval_group.add_argument("--skill")
    eval_p.add_argument("--skills", default=None); eval_p.add_argument("target_arg", nargs="?"); _add_repo_aliases(eval_p); eval_p.add_argument("--drawer", default=None)
    eval_p.add_argument("--jobs", type=int, default=None); eval_p.add_argument("--strict", action="store_true"); eval_p.add_argument("--no-cache", action="store_true"); _add_output_flags(eval_p)

    target_p = sub.add_parser("eval-target")
    target_p.add_argument("--skills", default="auto", help="Comma-separated skill names or 'auto'"); target_p.add_argument("--skill", default=None)
    target_p.add_argument("--worktree", default=None); target_p.add_argument("--handoff-dir", default=None); target_p.add_argument("--phase", choices=["spec", "code", "all"], default="all")
    target_p.add_argument("--receipt-out", default=None); _add_repo_aliases(target_p); target_p.add_argument("--strict", action="store_true"); _add_output_flags(target_p)

    desc_p = sub.add_parser("description-eval")
    desc_p.add_argument("--skill", required=True); _add_repo_aliases(desc_p); desc_p.add_argument("--mock-jev-json", default=None); desc_p.add_argument("--strict", action="store_true"); _add_output_flags(desc_p)

    star_p = sub.add_parser("sync-jev-stars")
    star_p.add_argument("--skill", required=True); _add_repo_aliases(star_p); star_p.add_argument("--db-path", default=None); star_p.add_argument("--source-name", default=None); star_p.add_argument("--cmd-template", default=None); star_p.add_argument("--dry-run", action="store_true"); _add_output_flags(star_p)

    uptake_p = sub.add_parser("uptake-eval")
    uptake_p.add_argument("--skill", "--skills", dest="skill", default=None); uptake_p.add_argument("--triggered-skills", default=None); uptake_p.add_argument("--target-dir", "--worktree", dest="target_dir", default=None)
    uptake_p.add_argument("--scenario", choices=["skills_unavailable", "skills_available_unmentioned", "skills_available_mentioned"], default="skills_available_unmentioned"); uptake_p.add_argument("--uptake-map", dest="uptake_map_path", default=None)
    uptake_p.add_argument("--baseline-pct", type=float, default=None); uptake_p.add_argument("--skill-pct", type=float, default=None); uptake_p.add_argument("--build-success", action="store_true", default=None); uptake_p.add_argument("--strict", action="store_true"); _add_output_flags(uptake_p)

    audit_parser = sub.add_parser("audit")
    group = audit_parser.add_mutually_exclusive_group(required=False); group.add_argument("--all", action="store_true"); group.add_argument("--skill")
    audit_parser.add_argument("--skills", default=None); audit_parser.add_argument("target_arg", nargs="?"); _add_repo_aliases(audit_parser); audit_parser.add_argument("--no-cache", action="store_true"); audit_parser.add_argument("--strict", action="store_true"); _add_output_flags(audit_parser)

    ver_parser = sub.add_parser("version")
    ver_parser.add_argument("--skill", default=None); ver_parser.add_argument("--all", action="store_true"); _add_repo_aliases(ver_parser); ver_parser.add_argument("--no-cache", action="store_true"); _add_output_flags(ver_parser)

    vers_parser = sub.add_parser("versions")
    vers_parser.add_argument("--skill", default=None); vers_parser.add_argument("--all", action="store_true", default=True); _add_repo_aliases(vers_parser); vers_parser.add_argument("--no-cache", action="store_true"); _add_output_flags(vers_parser)

    hygiene = sub.add_parser("hygiene-facts", aliases=["facts", "inspect-json"])
    _add_repo_aliases(hygiene); hygiene.add_argument("--json-file", "--inspect-json", dest="json_file", default=None); hygiene.add_argument("--check-diff", action=argparse.BooleanOptionalAction, default=True); hygiene.add_argument("--strict", action="store_true"); _add_output_flags(hygiene)
    return root_parser


def _normalize_argv(argv: list[str] | None = None) -> list[str]:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        return ["audit", "--all"]
    if values[0] in {"-h", "--help"}:
        return values
    if values[0].startswith("-"):
        return ["audit", "--all", *values]
    return values


def _normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if hasattr(args, "repo"):
        args.repo = _normalize_repo_path(getattr(args, "repo", None))
    target_arg = getattr(args, "target_arg", None)
    if target_arg:
        known = (ROOT / "skills" / target_arg / "SKILL.md").is_file()
        if known:
            args.skill = target_arg
        elif Path(target_arg).expanduser().exists():
            args.repo = _normalize_repo_path(target_arg)
        else:
            raise SystemExit(f"unknown target: {target_arg}")
    return args


def main() -> int:
    args = _normalize_args(parser().parse_args(_normalize_argv()))
    return {
        "init": init_skill,
        "sink-rule": sink_rule,
        "add-case": add_case,
        "eval": run_eval,
        "eval-target": run_eval_target,
        "description-eval": description_eval,
        "sync-jev-stars": sync_jev_stars,
        "uptake-eval": uptake_eval,
        "audit": audit,
        "version": version_cmd,
        "versions": version_cmd,
        "hygiene-facts": hygiene_facts,
        "facts": hygiene_facts,
        "inspect-json": hygiene_facts,
    }[args.command](args)


__all__ = [name for name in globals() if not name.startswith("__")]
