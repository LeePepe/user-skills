"""CLI argument parser and entrypoint for digest_vault."""

from __future__ import annotations

from .common import *
from .cards_and_blog import archive_card, precheck_card, publish_blog
from .discovery import discover_topics
from .events_and_reconcile import handle_learning_event, reconcile_learning_state
from .rag_search import search_knowledge_vault

REFERENCE = "skills/knowledge-digest/references/templates.md"


class _DigestVaultArgumentParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {redact_text(message)}\n")
        sys.stderr.write("Try `python3 skills/knowledge-digest/scripts/digest_vault.py --help`.\n")
        sys.stderr.write(f"Reference: {REFERENCE}\n")
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _DigestVaultArgumentParser(description="Knowledge Digest vault & blog flywheel CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arch = sub.add_parser("archive", help="Archive a markdown card into knowledge-vault and update index.md")
    p_arch.add_argument("--slug", required=True)
    p_arch.add_argument("--category", required=True)
    p_arch.add_argument("--title", required=True)
    p_arch.add_argument("--file", required=True, type=Path)
    p_arch.add_argument("--folder", choices=("concepts", "craft", "thoughts", "raw"), default=None)
    p_arch.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_arch.add_argument("--updated", default=None)

    p_disc = sub.add_parser("discover-topics", help="Scan git tech stack, collected-tools, and vault gaps with R09")
    p_disc.add_argument("--top", type=int, default=None)
    p_disc.add_argument("--track", choices=VALID_TRACKS, default=None, help="Restrict candidate discovery to one learning track")
    p_disc.add_argument("--propose-aidash", action="store_true")
    p_disc.add_argument("--propose-linear", action="store_true", help="Explicitly create/update the daily TIA digest issue")
    p_disc.add_argument("--dual-zone", action="store_true", help="Render Linear/Multica daily digest issue using strict Dual-Zone contract")
    p_disc.add_argument("--no-materialize", action="store_true")
    p_disc.add_argument("--materialize-before-approval", action="store_true", help="Deprecated; cannot override candidate-only gate")
    p_disc.add_argument("--materialize", action="store_true", help="Explicit standalone archival only")
    p_disc.add_argument("--include-unreconciled-repos", action="store_true")
    p_disc.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_disc.add_argument("--collected-tools-dir", type=Path, default=DEFAULT_COLLECTED_TOOLS_DIR)
    p_disc.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
    p_disc.add_argument("--state-path", type=Path, default=None)
    p_disc.add_argument("--topic-ledger-path", type=Path, default=None)
    p_disc.add_argument("--no-git", action="store_true", help="Disable default git root scanning for isolated runs")

    for cmd_name in ("handle-learning-event", "handle-decision", "explore-topic"):
        p_evt = sub.add_parser(cmd_name, help="Handle Owner learning topic decision or post-approval exploration gate")
        p_evt.add_argument("--action", required=True)
        p_evt.add_argument("--topic-key", default=None)
        p_evt.add_argument("--ref", default=None)
        p_evt.add_argument("--external-id", default=None)
        p_evt.add_argument("--event-id", default=None)
        p_evt.add_argument("--revision", type=int, default=None)
        p_evt.add_argument("--note", default="")
        p_evt.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        p_evt.add_argument("--state-path", type=Path, default=None)
        p_evt.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)

    p_pub = sub.add_parser("publish-blog", help="Render a knowledge-vault card into leepepe.github.io (DC-006..008)")
    p_pub.add_argument("--slug", required=True)
    p_pub.add_argument("--blog-repo", type=Path, default=DEFAULT_BLOG_REPO)
    p_pub.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_pub.add_argument("--mode", choices=("observe", "auto"), default="observe")

    p_pre = sub.add_parser("precheck-card", help="Validate required card sections, frontmatter, index, and isolation redlines")
    p_pre.add_argument("--file", required=True, type=Path)
    p_pre.add_argument("--folder", choices=("concepts", "craft", "thoughts", "raw"), default=None)
    p_pre.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=None)
    p_pre.add_argument("--require-index", action="store_true")

    p_rec = sub.add_parser("reconcile-state", help="Detect or repair learned topic-ledger drift against learning-topics-state/proposals")
    p_rec.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_rec.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
    p_rec.add_argument("--state-path", type=Path, default=None)
    p_rec.add_argument("--topic-ledger-path", type=Path, default=None)
    p_rec.add_argument("--apply", action="store_true", help="Write reconciled learned revisions to state and proposals.jsonl")

    p_search = sub.add_parser("search", help="Hybrid RAG semantic search over knowledge-vault cards")
    p_search.add_argument("--query", required=True, help="Search question or keywords")
    p_search.add_argument("--top-k", dest="top_k", type=int, default=5, help="Maximum hits to return")
    p_search.add_argument("--use-jev", dest="use_jev", action="store_true", help="Enable optional Jev Layer-1 reranking")
    p_search.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_search.add_argument("--scope", dest="corpus_scope", default="knowledge-vault", help="Corpus scope (strictly knowledge-vault)")
    p_search.add_argument("--json", action="store_true", help="Emit bounded JSON output (<=12KB)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "archive":
        res = archive_card(
            slug=args.slug,
            category=args.category,
            title=args.title,
            file_path=args.file,
            vault_dir=args.vault_dir,
            folder=args.folder,
            updated_date=args.updated,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command == "discover-topics":
        mat: bool | None = None
        if getattr(args, "no_materialize", False):
            mat = False
        elif getattr(args, "materialize", False):
            mat = True
        elif not args.propose_aidash:
            mat = False
        res = discover_topics(
            top=args.top,
            track=args.track,
            propose_aidash=args.propose_aidash,
            propose_linear=args.propose_linear,
            dual_zone=getattr(args, "dual_zone", False),
            vault_dir=args.vault_dir,
            collected_tools_dir=args.collected_tools_dir,
            git_roots=() if getattr(args, "no_git", False) else DEFAULT_GIT_ROOTS,
            proposals_path=args.proposals_path,
            state_path=args.state_path,
            topic_ledger_path=args.topic_ledger_path,
            materialize_cards=False if args.no_materialize else args.materialize,
            materialize_before_approval=args.materialize_before_approval,
            include_unreconciled_repos=args.include_unreconciled_repos,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok", True) else 1
    if args.command in {"handle-learning-event", "handle-decision", "explore-topic"}:
        res = handle_learning_event(
            action=args.action,
            topic_key=args.topic_key,
            ref=args.ref,
            external_id=args.external_id,
            event_id=args.event_id,
            revision=args.revision,
            note=args.note,
            proposals_path=args.proposals_path,
            state_path=args.state_path,
            vault_dir=args.vault_dir,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok", True) else 1
    if args.command == "publish-blog":
        res = publish_blog(
            slug=args.slug,
            blog_repo=args.blog_repo,
            vault_dir=args.vault_dir,
            mode=args.mode,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command == "precheck-card":
        res = precheck_card(
            file_path=args.file,
            folder=args.folder,
            vault_dir=args.vault_dir,
            require_index=args.require_index,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    if args.command == "reconcile-state":
        res = reconcile_learning_state(
            vault_dir=args.vault_dir,
            proposals_path=args.proposals_path,
            state_path=args.state_path,
            topic_ledger_path=args.topic_ledger_path,
            apply=args.apply,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    if args.command == "search":
        try:
            res = search_knowledge_vault(
                query=args.query,
                vault_dir=args.vault_dir,
                top_k=args.top_k,
                use_jev=args.use_jev,
                corpus_scope=args.corpus_scope,
            )
        except VaultIsolationError as exc:
            err = {
                "ok": False,
                "status": "vault_isolation_error",
                "error": redact_text(str(exc)),
                "corpus_scope": "knowledge-vault",
            }
            print(compact_json_dumps(err, max_bytes=ACI_MAX_BYTES, preserve_keys=("ok", "status", "error", "corpus_scope")))
            return 1
        out = json.dumps(res, ensure_ascii=False, indent=None if args.json else 2)
        if len(out.encode("utf-8")) > ACI_MAX_BYTES:
            out = compact_json_dumps(res, max_bytes=ACI_MAX_BYTES, preserve_keys=("ok", "corpus_scope", "query", "hit_count", "hits"))
        print(out)
        return 0 if res.get("ok") else 1
    return 1
