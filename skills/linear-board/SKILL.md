---
name: linear-board
description: 管理 Linear TIA Idea Inbox 与 Global Goal Status Board；运行每日 05:00 Daily Brief（昨日汇总、今日 Todo 四桶与 Jev 顺推）、@bot 双向桥接和 Project Root Tracker。
---
# Linear Board

`linear-board` 管理 workflow-hub 的 Idea、Goal 状态与个人执行回执。Linear MCP 已禁用；看板读写统一经 canonical [`../team-lead/scripts/linear-sync.py`](../team-lead/scripts/linear-sync.py)，凭据只来自 `~/.config/linear/credentials.json`。

## 核心边界

- Idea 只记录问题、背景、证据指针与下一步；成熟后才提升为 Goal。
- 遵守 `1 Goal = 1 Issue`（TIA 为团队前缀），description 维护 Task checklist、R8 状态与阻塞证据。
- `[Root]`、Work Business/Bing/Workplace 与完整祖先链按路由规则处理；不要把 API key 写入仓库。
- OAuth native Delegate/mention 需要管理员明确授予 `app:assignable` 与 `app:mentionable`；文本 `@bot` 不等于 native live handoff。
- Daily Learn、Daily Brief、跨设备 M2 同步中枢 `TIA-463`（`[M2-Sync]` / `<!-- GTT-M2-SYNC-HUB:v1 -->`），以及已有 Bot 参与的个人 TIA issue：Owner 评论可直接触发，`@bot` 可省略；普通 issue 仍需显式 `@bot`/Bot thread。
- Daily Brief 只在验证来源后发布；dry-run 不刷新、不写入、不派发。Teams timeline 必须经过显式 confirmation gate。

## When-to-Read & CLI 路由

| 需要 | 先读 | 入口 |
|---|---|---|
| Idea Inbox / Goal / ledger | `scripts/linear-sync.py` 与团队台账 | `idea`、`ensure-goal`、`sync-ledger` |
| 每日简报、四桶/四层、Jev | [daily-brief-and-bot.md](references/daily-brief-and-bot.md) | `daily-brief.py --date today --dry-run` |
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

`daily-brief.py` 在 CST 05:00 生成 Plan A：第一屏是全局编号的 Todo 四桶，随后是昨日完成的 workflow-hub 四层矩阵；具体契约、Jev truth table、发布与 bridge 状态机见 L1。建议先用 `--dry-run`，再由受控 LaunchAgent 调用 `--propose-linear --json`。

```bash
python3 skills/linear-board/scripts/daily-brief.py --date today --dry-run
python3 skills/linear-board/scripts/daily-brief.py --date today --propose-linear --parent-issue TIA-375 --json
```

## Safety

不要绕过 admin consent、parent membership、Todo 状态、timeline confirmation 或 dedup marker。异常应保留 sanitized warning 与 receipt；不要用终端输出冒充 Linear 证据。详细 bridge 的 ack → dispatch → wait → result-comment → owner-reassign 合同，以及 canonical credential implementation link，统一看 [daily-brief-and-bot.md](references/daily-brief-and-bot.md)。
