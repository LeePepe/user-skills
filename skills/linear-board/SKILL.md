---
name: linear-board
description: 管理 Linear TIA Idea Inbox 与 Global Goal Status Board；运行每日 05:00 Daily Brief（昨日汇总、今日 Todo 四桶与 Jev 顺推）、@bot 双向桥接和 Project Root Tracker。
---
# Linear Board

`linear-board` 管理 workflow-hub 的 Idea、Goal 状态与个人执行回执。Linear MCP 已禁用；看板读写统一经 canonical [`../team-lead/scripts/linear-sync.py`](../team-lead/scripts/linear-sync.py)，凭据只来自 `~/.config/linear/credentials.json`。

## 核心边界

- Idea 只记录问题、背景、证据指针与下一步；成熟后才提升为 Goal。
- 遵守 `1 Goal = 1 Issue`（TIA 为团队前缀），description 维护 Task checklist、R8 状态与阻塞证据。
- **个人方向三层层级契约（L1–L3）**：
  - **L1 Project**：`Personal Products & Home`、`Personal Core & Gateways`、`user-skills`（`Work Business` 工作 `[Root]` `TIA-365..TIA-374` 保持原样）。
  - **L2 Project Milestone**：与 `m2-domain-map.json` 模块 1:1 对齐（含 `Personal Core & Gateways` 下的 `Pew CLI Telemetry` 与 `Shared Design System`）。
  - **L3 Stage `[Epic]` vs. 顶层单据（`parentId = null`）**：跨多 Goal 阶段性战役在对应 `Project + Milestone` 下建 `[Epic]` 父单并将子 Goal 挂载其下（`parentId = <Epic ID>`）；**个人 1:1 `[Root]` 空壳（`TIA-375..TIA-389`）已全部退役（`Canceled`），且永久禁止任何 `[Routine]` 父单概念**；独立单次 Goal 与日常例行单据（`[Daily Brief]`、`[Daily Learn]`、`[M2-Sync]`）一律以 `parentId = null` 直接挂在对应 `Project + Project Milestone` 顶层。
- **Assignee 指派铁律（`Assignee = Owner` 严格等于「球在 Owner 脚下」）**：
  - ✅ **仅 3 类允许设 `Assignee = Owner`（`@ltp19941128`）**：① 待 Owner 拍板/选型（`In Review` / `NEED_OWNER_DECISION` / `NEED_GO`，拍板转 `In Progress` 立即清空）；② 当天最新一期 `[Daily Brief]` 与 `[Daily Learn]`（新一期生成时自动将前一期标 `Done` 并清空 `Assignee`）；③ 需 Owner 真人外部回复/操作的任务。
  - ❌ **一律 `Assignee = null`**：① 所有 `[Epic]`、`[Root]`、`[M2-Sync]` 容器/中枢单；② AI 后台执行的 `In Progress` / `Todo` Goal；③ 所有 `Done` / `Canceled` 单据。
- Work Business/Bing/Workplace 与完整祖先链按路由规则处理；不要把 API key 写入仓库。
- OAuth native Delegate/mention 需要管理员明确授予 `app:assignable` 与 `app:mentionable`；文本 `@bot` 不等于 native live handoff。
- Daily Learn、Daily Brief、跨设备 M2 同步中枢 `TIA-463`（`[M2-Sync]` / `<!-- GTT-M2-SYNC-HUB:v1 -->`），以及已有 Bot 参与的个人 TIA issue：Owner 评论可直接触发，`@bot` 可省略；普通 issue 仍需显式 `@bot`/Bot thread。
- Daily Brief 只在验证来源后发布；dry-run 不刷新、不写入、不派发。Teams timeline 必须经过显式 confirmation gate。

## When-to-Read & CLI 路由

| 需要 | 先读 | 入口 |
|---|---|---|
| Idea Inbox / Goal / ledger | `scripts/linear-sync.py` 与团队台账 | `idea`、`ensure-goal`、`sync-ledger` |
| 每日简报、四桶/四层、Jev | [daily-brief-and-bot.md](references/daily-brief-and-bot.md) | `daily-brief.py --date today --dry-run` |
| Multica + Linear 极简双区简报 | [daily-brief-and-bot.md](references/daily-brief-and-bot.md) | `daily-brief.py --date today --target both --json` |
| Backlog 指纹去重与每日 Top 3 顺推 (Loop 3B) | `scripts/backlog_groomer.py` | `backlog_groomer.py groom\|top3\|loop-check --date today` |
| 邮件深链提取与预落盘 Actionable Issue | [daily-brief-and-bot.md](references/daily-brief-and-bot.md) | `email-action-pipeline.py ingest-workiq --input-json PATH --propose-linear` |
| Markdown 合同验证 | `daily-brief.py` | `daily-brief.py --verify-markdown PATH\| -` |
| `@bot` 选择、重试、Owner 回收 | [daily-brief-and-bot.md](references/daily-brief-and-bot.md) | `linear-bot-bridge.py --once --json` |
| 零 Token 回归抽屉 | [eval-cases.json](references/eval-cases.json) | `skill_contract.py eval --skill linear-board --strict` |
| Project Root Tracker | `scripts/root_tracker.py` | `root_tracker.py refresh` |
| Teams China release timeline | `state/teams-china-release-timeline.json` | `sync-teams-timeline --preview` |

## Promote Idea -> Goal

未指定 project 时按 ledger、Goal 与仓库关键词路由到 `Agent Workflow`、`Bing` 或 `Personal project`；成熟 Idea 才提升为 Goal。

```bash
python3 scripts/linear-sync.py idea --title "..." --description "..." --project "Agent Workflow"
python3 scripts/linear-sync.py ensure-goal --handoff-dir <dir> --issue TIA-123
python3 scripts/linear-sync.py sync-ledger --handoff-dir <dir> --issue TIA-123
```

未指定 project 时按 ledger、Goal 与仓库关键词路由；workflow/skills/编排归入 `Agent Workflow`，产品归入对应个人项目。Goal 发布前核对 parent、team、状态和 Owner。

## Daily Brief

`daily-brief.py` 在 CST 05:00 生成 Plan A（或 `--dual-zone` 极简双区 `issue-brief` 视图，`<=15` 行人看区 + `<details>` 折叠上下文 + `<!-- multica-cursor:daily-brief-YYYY-MM-DD -->` 水位线）。支持 `--propose-linear`、`--publish-multica` 或 `--target linear|multica|both` 双端去重发布并指派给 Owner（Multica `lee@multica.local`）。

```bash
python3 skills/linear-board/scripts/daily-brief.py --date today --dry-run
python3 skills/linear-board/scripts/daily-brief.py --date today --publish-multica --json
python3 skills/linear-board/scripts/daily-brief.py --date today --target both --json
```

## Safety

不要绕过 admin consent、parent membership、Todo 状态、timeline confirmation 或 dedup marker。异常应保留 sanitized warning 与 receipt；不要用终端输出冒充 Linear 证据。详细 bridge 的 ack → dispatch → wait → result-comment → owner-reassign 合同，以及 canonical credential implementation link，统一看 [daily-brief-and-bot.md](references/daily-brief-and-bot.md)。
