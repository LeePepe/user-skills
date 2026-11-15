# Daily Brief & @bot Bridge (L1)

## Daily Brief contract

`daily-brief.py` is offline-first and uses the canonical `team-lead` Linear client. `--dry-run` never refreshes credentials or mutates files, Linear, Herdr, or the router. `--verify-markdown PATH| -` is a deterministic sink and must run before credentials, network, collection, or Jev.

The first line is `<!-- GTT-LINEAR-DEDUP:daily-brief-YYYY-MM-DD -->`. The first section is `## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）`. The heading is retained for compatibility; on Daily Learn/Daily Brief, an Owner top-level comment such as `1, 2`, `执行 1`, or `都做` also works without `@bot`. Nonempty buckets appear in this order and share global numbering; empty buckets are omitted:

1. `🚨 待你拍板（Needs Decision）`
2. `🔨 在途跟进（In Progress）`
3. `📌 今日计划（Planned & Learn）`
4. `💡 昨日完结顺推（Recommended Next-Hop）`

Each Todo is one line, has a visible summary of at most 25 characters, one direct action link or command, and clickable TIA/PR references. Recommendations are capped at three and never dispatch automatically.

After `---`, the completed heading is `## 📊 昨日完成 (MM-DD · N 项 · workflow-hub 四大方向)`. Always render these layers in order, with accurate counts:

1. Products & Apps
2. Shared Foundations
3. Engineering Workflows
4. Knowledge & Narrative

Each layer contains a populated three-column table (`项目 | 单据 / PR | 一句话结果`) or `_无_`. Results are at most 20 visible characters. Every displayed `TIA-123` and `#123` is a real Markdown link with a valid destination; code spans and fenced code are literal text, not links. The validator rejects malformed numbering, links, counts, headings, budgets, or layer order.

## Jev truth table

Layer 0 deduplicates yesterday's issue/topic/PR/commit/ledger identities and suppresses items already covered by needs, active, or planned Todos. Layer 1 calls canonical `jev_decide` with `agent_role="linear_board"`, `timeout_sec=1.5`, and `falcon_only=True`. Offline, timeout, fallback, or invalid answers use the deterministic mapping.

- `has_actionable_next_hop < 0.50`: drop.
- Valid `next_hop_kind_match >= 0.50`: accept the returned `choice`; otherwise use the layer fallback.
- Rank valid `[0,1]` priority descending, deterministic ties, then cap at three.
- Gallery → owner decision; foundation → downstream gates; app/bridge/job → live E2E; multi-item or knowledge → story/retro.

## @bot bridge safety and state

The bridge scans only personal TIA issues and excludes Work Business/Bing/Workplace through the full ancestry; personal children of personal `[Root]` remain eligible. It ignores bot-authored comments, keeps an atomic private watermark, and retains pending/retry/follow-up issue IDs across overlapping cursors. `--dry-run` performs no refresh, lock, router, credential, Herdr, or watermark writes.

A Daily Brief selection is `@bot 执行 1, 2`, direct Owner `执行 1, 2`, or checked `- [x]` items. Selection is parsed against the brief's global numbers; malformed or out-of-range numbers are ignored. The bridge acknowledges, dispatches the saved target, waits for the correlated standalone marker, posts the full result, then reassigns the issue to `@ltp19941128`. A blocked M2 result is **Owner decision required**, never completion.

Daily Learn completion receipts are compact: card title, verified clickable `leepepe.github.io` Blog URL, and one-line summary only. Unverified publication is reported as pending; knowledge-vault Markdown is never pasted into Linear.

Ack → dispatch → wait → result-comment → owner-reassign is retryable. Timeouts keep watching the original handle. Reassignment retries only reassignment. Ambiguous legacy pending state never auto-replays: inspect the saved handle and verified receipt first. Native Delegate/mention requires admin-approved `app:assignable` and `app:mentionable`; textual `@bot` remains pollable. OAuth admin consent is explicit and never inferred.

Publication enumerates closed candidates, prefers exact dedup marker over exact title, preserves non-canceled state, reopens canceled Daily Briefs in Todo, and verifies TIA parent membership and Todo availability before mutation. Timeline updates require the explicit Teams confirmation gate. Canonical credentials and HTTP behavior remain in `skills/team-lead/scripts/linear-sync.py`; this drawer documents routing, not a second client.
