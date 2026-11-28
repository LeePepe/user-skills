---
name: personal-m2-assistant
description: 个人跨 M2 唯一入口：将自然语言拆解到业务话题 M2，展示 Owner Gate 计划，并在明确批准后按 wave 派发或安全清理旧 M2。
---
# Personal M2 Assistant

`personal-m2-assistant` 是跨仓、跨会话的唯一总入口：理解个人指令、绑定业务话题 M2、预览计划，并在 Owner 明确拍板后派发。它不替代单仓 [`m2-dispatcher`](../m2-dispatcher/SKILL.md) 的 M2↔M1 Goal 生命周期。

## 触发与边界

- `/personal-m2-assistant <跨 M2 指令>` 与 `/m2 <跨 M2 指令>`（由 [`skill-slash-expander.ts`](../m2-dispatcher/scripts/skill-slash-expander.ts) 展开）。
- **M3 只管按业务话题直接转发，绝不预设 `Hot-Append` 或越权替 M2 拆 Goal**。是否同页面微调走 `Hot-Append`，还是独立新 Feature（新建 `handoff/<goal-2>/spec.md` + 独立 TL/Task/PR），完全由 `M2-Dispatcher` 判定。
- 业务话题与仓库映射唯一事实源是 [`m2-domain-map.json`](references/m2-domain-map.json)；`space`/`space_aliases` 是 Herdr Space 路由事实源。
- `--plan` 默认从 Linear `TIA-463` 快照读取 `spaces`/`agents`（`discovery.source="issue_cache"`），只有 `--live-herdr` 才实时扫描 Herdr；`agent_status=done` 可复用，`exited/stopped` 等终止状态不可复用。

## Owner Gate 与 Phase 0

1. `--plan` 只读发现、Jev/Falcon、Magpie 与缓存，不创建会话、关闭窗格或投递消息；默认展开所有 `~` 路径。
2. 计划输出 `plan.json`、`preview_markdown` 与终端 `preview_text`，同步 Linear Team TIA；使用 `ask_user` 请求 Owner 确认或修改。
3. 仅 Owner 明确批准后运行 `--apply --plan-file <plan.json> --approve`；缺少 `--approve` 必须 fail-closed。
4. 当消息包含“先讨论”“讨论一下”“先聊聊”“看看有哪些”“先调研”“不改代码”等探索意图时，`--plan` 设置 `phase0_discussion_only=true`，`sub_prompt` 原样转发，不预锁架构、指标层级或 `Hot-Append`。函数 `is_exploratory_discussion_message` 是确定性入口；“请直接修改代码”不匹配。
5. 派发后可生成 `post_apply_sync_task`，用 `--sync-herdr-to-issue` 更新 `TIA-463`；每个目标独立 `watchdog_tasks` / `watchdog_copilot_cli` / `--watch-target`，结果写入 `target_issue_writes`，累计码可达 `A2BCD`。安全 Allow 仅在合法 `apply_receipt`、目标匹配且命中只读安全基线时自动处理：`Allow path access`、`Curate Memory`、`Confirm folder trust`；业务决策永远交还 Owner。

## Grok Bot quota protection

`grok_bot_no_supervision` 在 `--caller-bot grok`、`--no-supervision`、`--no-routine` 或 Grok 模型环境启用：只执行 `--plan` → `ask_user` → `--apply`，不启动后台 `--watch` 或 Routine；返回 `watchdog_tasks: []`、`watchdog_cmd: null`。误调 `--watch` 会在 0 秒短路并返回 `grok_bot_supervision_disabled`；`--force-watch` 可显式覆盖。

## 派发、监督与安全

- 目标默认 `Gemini 4 Argon · 983K context`（`gemini-4-argon` / `long_context`）；NEW_M2 必须经安全 wrapper 使用 `--new-workspace --workspace-label <space> -- --model gemini-4-argon --context long_context`，不创建 Copilot Tab。
- `wave_order` 只表示软顺序；非 Phase 0 探索请求时，`--plan` 通过 `skill_runtime.hybrid_rag`（`M2_HANDOFF_ROOT` / `--handoff-root`）自动检索 Top-3 历史 handoff（`rag_hits`）并注入 `rag_context_hint` / `context_pass`，新旧 M2 立即可见历史 `spec.md` 决策、`Owned Paths` 与踩坑回执（空目录优雅降级）。`stdout` transport 只输出 receipt，不产生副作用。
- 真静默要求最后 8 行无 `background /tasks`、`Working`、`Thinking`、`Running`、`Waiting for` 等活动标记；Goal TL 仍 `in_progress` 时继续监督。`--status`、`--watch`、`--plan` 输出完整 `unified_rollup_markdown`，包含 Wave 依赖、完成、累计完成、运行中、跨设备 `[Host: <device>]`、`in_flight_details` 与 `remote_devices`。
- `--cleanup-duplicates` 仅清理安全重复 idle M2；保护 `HERDR_PANE_ID`、`w1:p1`、`w2J:p1`、Cindy/大总管及 working/blocked 窗格。Context Eviction 仅在旧 Goal 完成、无 review/Owner decision 且 Jev 同时确认同 Goal 概率低于 0.35 与旧上下文为噪声时成立。
- `m2-board-hook.sh` 的 `agentStop` / `sessionEnd` hook 以锁、指纹和 60s 节流避免空转；Linear 同步按设备分片保留 Work / Non-Work、Backlog 与最近完成。

## CLI

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --plan \
  --message '先检查通知开关，再写篇学习博客' --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --rag-query 'notification toggle spec' \
  --use-jev --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --apply \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json --approve --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --sync-herdr-to-issue \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json --hub-issue TIA-463 --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --watch \
  --plan-file /tmp/m2-plan.json --watch-target sapphire-notification --interval 15 --max-wait 2400 --json
```

常用离线参数：`--targets-json`、`--mock-jev-json`、`--mock-magpie-json`、`--domain-map-file`、`--handoff-root`、`--rag-top-k`、`--preview-md-file`、`--transport stdout`、`--no-herdr`、`--no-dsh`。完整参数以 `--help` 为准。

## 相关节点与验证

- How-To：[`I7 Unified M2 Ingress`](../workflow-hub/references/how-to-nodes.md)。
- 向后兼容入口：[`m2-dispatcher/scripts/m2-router.py`](../m2-dispatcher/scripts/m2-router.py)；权威 CLI 是本技能 `scripts/m2-router.py`，实现抽屉位于 `scripts/m2_router_core/`。
- 验证抽屉：[`eval-cases.json`](references/eval-cases.json)（Script-Only Data Drawer，使用 L2 CLI，不全量阅读）。
- 入口检查：`sh skills/personal-m2-assistant/scripts/install-m2-board-hook.sh --help`。
- 回归：`python3 -m unittest scripts/test_m2_router_rag.py scripts/test_m2_router.py scripts/test_personal_m2_retro.py scripts/test_verify_skills.py`；`skill_contract.py audit/eval --skill personal-m2-assistant --strict`；`./scripts/verify`。
