---
name: linear-board
description: 管理 Linear Team TIA 的 Idea Inbox 与 Global Goal Status Board，把 Idea 提升为 1 Goal = 1 Issue 并同步 Task checklist、阻塞证据和状态。
---
# Linear Board

`linear-board` 是 workflow-hub 的环 1。它管理想法与 Goal 的外部可见状态，
而 `ledger.md` 仍是本地执行事实源。

## Idea Inbox

- 先把未成熟想法写入 Linear Team `TIA` 的 `Triage`；若不可用则写入
  `Backlog`。
- Team `TIA` 的 Linear Projects 固定为 `Agent Workflow`、`Bing` 和
  `Personal project`。使用 `scripts/linear-sync.py idea --title ...`
  `--description ... --project "Agent Workflow"` 可显式指定；未指定时脚本会
  按 ledger、Goal 文本和仓库关键词自动路由。
- 所有 workflow、team-lead、skills、Linear board 和编排相关的 Idea/Goal 都归入
  `Agent Workflow`，Bing/Sapphire/FlightBook 等产品工作归入 `Bing`，个人应用归入
  `Personal project`。不在仓库或私有记忆中保存 API key。
- Idea 只记录问题、背景、证据指针和下一步，不伪装成已批准 Task。

## Global Goal Status Board

- 遵守 **`1 Goal = 1 Issue`**：一个 Goal 绑定一个 TIA Issue。
- Issue description 维护 Markdown Task checklist；每个 Task 仍遵守
  `1 Task = 1 Writer = 1 Worktree = 1 PR`。
- `Todo`、`In Progress`、`In Review`、`Done`、`Canceled` 映射 Goal 状态。
- 阻塞必须通过 blocker label/comment 写明准确 head、命令、证据和 Owner decision，
  不用终端输出或“正在处理”代替证据。

## Promote Idea -> Goal

执行 `ensure-goal --handoff-dir <dir> --issue TIA-xx` 将已有 Idea 提升为
Active Goal；不传 `--issue` 时按 `auto_create` 策略创建 Goal Issue。随后
`sync-ledger` 将 `ledger.md` 的 R8 状态、Task checklist 和去重后的里程碑评论
同步回 Linear。

Linear MCP 已禁用。所有 Linear 看板读写（`idea`、`ensure-goal`、`sync-ledger`、
`list-issues`、`get-issue`、`update-issue`、`comment`）一律通过
[`scripts/linear-sync.py`](scripts/linear-sync.py) 执行，并使用
`~/.config/linear/credentials.json` 中的 Bot OAuth App 身份。它是
`team-lead` canonical script 的共享入口；真正的实现仍在
[`../team-lead/scripts/linear-sync.py`](../team-lead/scripts/linear-sync.py)。
# Linear synchronization modes

`linear-sync.py` supports `--mode off|observe|auto` (or `GTT_LINEAR_MODE`).
`off` performs no network or file mutations; `observe` emits planned mutations
and may append `receipts/linear-observe.jsonl`; `auto` applies the diff.
Goal descriptions may include an `## Execution & Cost Attribution` section
summarizing R10 receipts, run metrics, duration, tokens, and honest lower-bound
costs. Credentials are stored in `~/.config/linear/credentials.json` with
directory mode `0700` and file mode `0600`; use `linear-sync.py auth set`,
`auth show`, or `auth remove`.

## Personal `@bot` bridge

The OAuth app must be installed with `app:assignable` and `app:mentionable` for
native delegation/mentions ([Linear agent setup](https://linear.app/developers/agents)).
An existing automation-only grant may return `App user not valid`; refreshing it
cannot add scopes. A workspace admin must explicitly reauthorize:

```bash
python3 scripts/linear-sync.py auth oauth-login --agent-scopes
```

This opens an approval flow using saved client credentials; it never grants scopes
without admin consent. Linear now assigns agents as **Delegate**, retaining the
human Assignee; the bridge also recognizes legacy Bot assignees. Literal `@bot`
comments remain pollable, but do not claim native ingress/live handoff verified or
close the Goal until the admin grant and a real Owner-triggered turn are confirmed.

Run `scripts/linear-bot-bridge.py --once --json` for one incremental scan, or
`--daemon --interval 20` for polling. It only considers personal TIA issues,
ignores bot-authored comments, persists an atomic 0600 watermark, and reassigns
verified M2 output to the Owner (clearing Bot delegation). Startup paginates old
issues/comments; subsequent scans use an overlapping updated-at cursor plus saved
pending/retry/follow-up issue IDs. Titles, teams, projects and the full parent chain exclude
Work Business/Bing/Workplace before routing; personal descriptions are not keyword-blocked.
`--dry-run` performs no Linear, Herdr, vault, credential-refresh, lock or watermark
writes. Webhooks bind to loopback, require `LINEAR_WEBHOOK_SECRET` and a signed,
fresh `webhookTimestamp`, reject replays, and coalesce deliveries into one scan worker.

Each turn saves its directive, topic slugs, root comment, markers and dispatch
handle under a process/thread lock. Busy learning targets defer until idle.
Router apply is only delivery: only the saved receipt/watch and this turn's framed
target output authorize a handoff. The prompt requests unique standalone response
markers; terminal prompt echoes, prior output and router rollups alone never count.
Fresh selected cards come only from concepts/craft/thoughts; otherwise the actual
M2 response is used. A real blocked M2 question is labelled **Owner decision
required**, not task completion.

Retries resume ack → dispatch → wait → result-comment → owner-reassign, preserving
output before posting. Timeouts keep watching the original handle; an Owner update
failure retries only reassignment. Transient per-issue errors back off independently.
State and pending router files are private 0600; router files are removed on completion.
An `ambiguous`/legacy pending diagnostic never auto-replays delivery: inspect the
saved handle and target, restore a verified router apply receipt when available,
or wait for the correlated response. Only after independently proving no delivery
occurred may an operator reset that turn to `dispatch`; never delete active state
merely to retry. New comments wait for the active turn to finish.

## Daily Brief

`skills/linear-board/scripts/daily-brief.py` renders the personal CST daily brief in Plan A's ultra-compact layout: today's flat numbered todos first, then yesterday's verified Linear/Git/ledger rows grouped by workflow-hub's four-layer product/output matrix. It uses the canonical `linear-sync.py` credential loader and never mutates Linear during `--dry-run`.

```bash
python3 skills/linear-board/scripts/daily-brief.py --date today --dry-run
python3 skills/linear-board/scripts/daily-brief.py --date today --propose-linear --parent-issue TIA-375 --json
```

Line 1 is `<!-- GTT-LINEAR-DEDUP:daily-brief-YYYY-MM-DD -->`; the issue title is separate from its description. The first visible heading is ``## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）``. Each flat line is `- [ ] **[N] summary** · link/command · [TIA-xxx](https://linear.app/tianpeili/issue/TIA-xxx)` with a single-line summary of at most 25 visible characters and one action; task stages follow in code spans. Needs Decision → In Progress → Planned/Daily Learn share continuous numbering. Then `---` precedes `## 📊 昨日完成 (MM-DD · N 项 · workflow-hub 四大方向)`. Always render Products & Apps → Shared Foundations → Engineering Workflows → Knowledge & Narrative, each with its count and a three-column `项目 | 单据 / PR | 一句话结果` table, or `_无_` when empty. Concrete project/repo destinations outrank incidental title mentions; generic projects use goal/title intent, defaulting to Engineering Workflows. Results stay at most 20 visible characters; N counts actual rows. No top title/scope blockquote, Todo subgroup headings or bottom Bot tutorial is rendered.

All rendered issue/PR references use clickable `[TIA-xxx](https://linear.app/tianpeili/issue/TIA-xxx)` / `[#123](https://github.com/LeePepe/user-skills/pull/123)`, including identifiers extracted from commit subjects, PR titles and ledger notes before truncation. Issue URLs prefer canonical source URLs with `https://linear.app/tianpeili/issue/TIA-xxx` fallback; PR URLs retain repository ownership from source metadata or explicit URLs. Without a resolvable PR destination, use the neutral `PR` label rather than inventing a URL. Yesterday's CST half-open rollup retains project/repo/goal row labels within the four layers; full PR metrics stay in the reference column. Today's open issues and active `## Tasks` R8/M1 stages are deduplicated by bound Linear identity. Compact refs retain task stages; JSON `todo_items` keeps full source context. Completed/canceled and Daily Brief issues never become todos; resolved blockers and completed goals disappear. Work Business/Bing/Workplace ancestry and work ledger metadata/prefixes are excluded; personal children of personal `[Root]` containers remain eligible. Incomplete/cyclic scope fails closed with sanitized warnings.

Reply `@bot 执行 1, 2`, or check selected `- [x]` boxes and comment/assign/delegate/mention `@bot`. The bridge wakes the corresponding M2, posts full Markdown after verified output, and reassigns to `@ltp19941128`; **Owner decision required** is not completion. Textual comments are pollable; native Delegate/mention requires workspace-admin `app:assignable`/`app:mentionable` grants. Publication enumerates all candidates (including closed), prefers exact marker comments over exact-title fallback, retains noncanceled state and reopens canceled briefs in Todo. Parent membership in TIA and Todo for creation are mandatory. Missing sources are explicit warnings; unconfirmed publication exits nonzero. Dry-run performs paginated reads without refresh, locks, files or mutations.

## Project Root Tracker

Run `python3 scripts/root_tracker.py refresh` to collect the offline-first root
snapshot, render the four-section `PR & PoP` Markdown, and sync only to Linear.
The layout separates merged PRs from active PoP, auto-heal, and review/CI queues;
resolved cross-session decisions are shown with `Resolve: TIA-34x` badges. The
tracker reconciles a decision when discovery reports zero unresolved comments,
no signals, and a new head, so stale owner prompts do not remain visible.

### Teams milestone timeline gate

`state/teams-china-release-timeline.json` is an offline-first input from `I1 Teams`.
`collect` and `sync-teams-timeline --preview` normalize Code Freeze/QA/Release
nodes, context owners, and clickable Teams links without writing Linear. Only
`--confirm-teams-timeline` (or `sync-teams-timeline --confirm`, or a persisted
`confirmed: true`) may update the Work Business / China Release Project Milestone
`targetDate` and `description`, and the compact `TIA-365 [Root] China Release`
`## Release Timeline & Teams Context` section. The confirmation gate is the
`I1 Teams -> I5 Confirm -> G1 Linear Milestone & [Root] L0` contract.
