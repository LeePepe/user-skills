---
name: personal-m2-assistant
description: 个人跨 M2 唯一入口：将自然语言拆解到业务话题 M2，展示 Owner Gate 计划，并在明确批准后按 wave 派发或安全清理旧 M2。
---
# Personal M2 Assistant

`personal-m2-assistant` 是跨仓、跨会话的唯一总入口。它负责理解一条个人指令、绑定业务话题 M2、预览计划并在 Owner 明确拍板后派发；它不替代单仓 `m2-dispatcher` 的 M2↔M1 Goal 生命周期合同。

## 触发与职责边界

- 直接触发：`/personal-m2-assistant <跨 M2 指令>`。
- 简短触发：`/m2 <跨 M2 指令>`，由 [`skill-slash-expander.ts`](../m2-dispatcher/scripts/skill-slash-expander.ts) 展开到本技能。
- 单仓 M2 开 TL、热追加、巡检、解卡与 Goal 回收仍走 [`m2-dispatcher`](../m2-dispatcher/SKILL.md)。
- **M3 只管按业务话题直接转发，绝不预设 `Hot-Append` 或越权替 M2 拆 Goal**：收到后续指令时，M3 只按业务话题原意直达对应 M2，绝不在 M3 层预设包装为「热追加（`Hot-Append`）合并进当前任务」或自行拆 Goal；是同页面微调走 `Hot-Append` 还是独立新 Feature 拆分为第二个 Goal（新建 `handoff/<goal-2>/spec.md` + 独立 TL/Task/PR）完全由接收方 `M2-Dispatcher` 判定。
- 业务话题与仓库映射的唯一事实源是 [`m2-domain-map.json`](references/m2-domain-map.json)；每个 topic 的 `space`/`space_aliases` 是 Herdr Space 路由的唯一事实源。
- **派发前准备阶段（`--plan`）默认从 Issue 缓存读取 `spaces` 与 `agents`**：`--plan` 默认直接从 Linear 同步总账本 `TIA-463` 及其持久化快照（`/tmp/m2-cross-device-sync-cache.json`、`m2-supervision-state.json`）读取已存好的 `spaces`（`workspaces`）和 `agents`（`sessions`，`discovery.source="issue_cache"`），不每次实时扫描 `herdr`，极大加快派发前准备速度（仅显式传入 `--live-herdr` 时才实时探测 Herdr）。交互式 Herdr 的 `agent_status=done` 仍可作为 `EXISTING_M2` 复用（仅真正 exited/stopped 等状态终止）。

## 默认配置与强制 Owner Gate

1. `--plan` 默认只读消费 `TIA-463` Issue 快照中的 `spaces`/`agents` 与 dsh Omni Hub 状态（或显式 `--live-herdr` 读取公开 Herdr `pane/agent/workspace list`）；不读取 dsh 私有 session 存储。
2. 调用 Jev/Falcon 做枚举选路，再由 Magpie 语义拆解并绑定已有会话；离线或网关不可达时只使用确定性兜底。
3. **默认模型/Context 与 `~` 展开**：所有目标 M2 默认使用 `Gemini 4 Argon · 983K context`（`--model gemini-4-argon --context long_context`）；所有 `cwd`（含 `m2-domain-map.json`、会话发现、新话题候选与派发参数）统一先展开 `~` 为绝对路径再传给 `herdr-dispatch.sh` 或 `dsh create-session`。
4. **`--plan` 尾部 64KB 直读与增量接续（`1 -> 子窗格私聊 2,3 -> 总入口 4` · M3 原意直达不代判 Goal 边界）**：绑定 `EXISTING_M2` 时优先按 `agent_session.value` seek 读取 `~/.copilot/session-state/<uuid>/events.jsonl` 末尾 `64KB`（缺失时回退 `herdr pane read <pane> --source recent-unwrapped --lines 50`），提取最近 3 轮真实 `user.message` 与最终 `assistant.message` 摘要及 `last_session_activity_at` 写入 `recent_session_context`，注入 Magpie 拆解器上下文（强制在最新 `2、3` 基础上生成增量 `sub_prompt` `4`）并展示在 `preview_markdown` / `preview_text` 中。`recent_session_context` 仅用于保证话题归属与避免覆盖子窗格最新对话上下文，生成的 `sub_prompt` 保持对用户新指令的原意直达，严禁在 M3 层预设为 `Hot-Append` 合并进当前 Goal 或替 M2 拆 Goal，由接收方 `M2-Dispatcher` 自行判定是同页面微调（`Hot-Append`）还是独立新 Feature（新建第二个 `handoff/<goal-2>/spec.md` + 独立 Task/PR）。
5. **Linear Team TIA 同步与终端摘要预览**：`--plan` 生成结构化 `plan.json`，其中 `preview_markdown` 保留完整 Markdown 正文，`preview_text(plan)` 提供终端摘要；计划与 Spec 拆解直接同步/展示在 Linear Team TIA（对应 Root/Goal Issue，如 `TIA-375`）及终端，不生成临时 `.md` 或打开 Orca。需要文件时只能显式传入 `--preview-md-file`。
6. 使用 `ask_user` 请求 Owner 确认或修改。计划阶段绝不创建会话、关闭窗格或投递消息。
7. 仅 Owner 明确批准后运行 `--apply --plan-file <plan.json> --approve`；未带 `--approve` 必须 fail-closed。
8. **派发后后台异步扫描 Herdr 写回 Issue + 每个 M2 独立后台监督与结果直写对应 Issue（`--sync-herdr-to-issue` + `--watch --watch-target`）**：`--apply` 将回执落盘至 `/tmp/m2-apply-<plan_id>.json`（或 `--apply-receipt-file`），同步维护本地监督状态账本 `m2-supervision-state.json`（或 `--state-file`），不再同步阻塞等待实时 Herdr 全量扫描与 Linear 请求，而是输出：
   - **派发后异步扫描写回 Issue（`post_apply_sync_task` · `--sync-herdr-to-issue`）**：`--apply` 生成后台任务 `post_apply_sync_task`（`mode="async", detach=true, shellId="sync-m2-issue-<plan_id>"`），在后台运行 `m2-router.py --sync-herdr-to-issue` 扫描真实 Herdr `workspace/pane/agent list`，将最新 `spaces` 与 `agents` 更新回写至 Linear `TIA-463`（`devices.<hostname>.spaces` 与 `devices.<hostname>.agents`）及本地缓存快照，供后续 `--plan` 极速读取。
   - **单 M2 独立完成即刻唤醒、累积汇总与结果直写对应 Issue（`watchdog_tasks` + `target_issue_writes`）**：为**每个已派发 M2 单独生成一条** `watchdog_copilot_cli`（汇总于 `watchdog_tasks`，每条 `mode="async", detach=true, shellId="watch-m2-<plan_id>-<slot>-r<seq>"`，带 `--watch-target <slot_id>`）。任一 M2 经真静默判定（`idle`/`done` 且无活动尾部）或阻塞退出时，立即更新 `TIA-463` 总账本，并**直接将看门狗/任务回报结果写入对应 Linear Issue**（`linear_issue` 如 `TIA-373` 或 `--target-issue`，通过 `linear-sync.py comment` 写入带 `<!-- M2-WATCH-REPORT:<plan_id>:<slot_id>:r<seq>:<status> -->` 幂等标记的结构化回报及 `sync-ledger`，回执见 `target_issue_writes`），同时输出完整 `unified_rollup_markdown` 与累积汇总码 `completed_rollup_code`（如 `A` → `AB` → `BC` → `A2BC` → `A2BCD`）。
   - **安全 Allow 自动解卡与 Owner 决策门禁**：仅当传入经校验的 `apply_receipt`（`ok=true`、`approved=true`、`plan_id` 与目标匹配）且目标处于 `blocked`/`needs_input` 时，对底部当前活动帧的安全权限弹窗（词法与 `resolve` 均落在安全基线/目标 `cwd`/本机只读工具目录 `/opt/homebrew` 含 `symlink -> target` 箭头解析且无越界的 `Allow path access`/`Confirm folder trust`、`Curate Memory`、受信任 MCP 工具确认 `Do you want to use this tool?` 如 `linear` 看板工具与 `ado`/`github` 只读工具，以及单行无元字符只读白名单命令 `CMD_CONFIRM`）自动放行（含记住会话选项 `["down", "enter"]` 或单次 `["enter"]`）；无有效回执时保持纯被动只读观察（`auto_unblock_enabled=false`）。遇到业务/Spec 级 `ask_user`、`Proceed with`、`/` 与 `/Volumes` 与 `~` 主目录根红线/路径越界、未知/写/删除/外发 MCP 工具或非只读命令，**绝不代拍板**，标记 `needs_owner_attention=true`（`blocked_reason="owner_decision_required"`）；目标缺失或发现中断持续重试至超时后返回 `ok=false, all_settled=false, blocked_reason="target_missing_or_unreachable"`。
8. **Grok Bot 省流防烧额度规则（`grok_bot_no_supervision` · Fire-and-Forget）**：当总控运行在 Grok Bot 环境（显式 `--caller-bot grok` / `--no-supervision` / `--no-routine`，或环境变量 `M2_CALLER_BOT`/`BOT_KIND`/`GROK_BOT`/`*_MODEL` 含 `grok`，或当前 `HERDR_PANE_ID` 绑定 `grok-4.7` / `magpie/grok/*` 模型）时，自动启用 `quota_protection_mode: grok_bot_no_supervision`：
   - **严禁启动后台 `--watch` 监督看门狗或定时 Routine 轮询**：仅执行 `--plan` → `ask_user` → `--apply` 即刻结束本轮，`--apply` 强制返回 `watchdog_tasks: []` 与 `watchdog_cmd: null`，等待用户主动查询 `--status`。
   - **误调 `--watch` 0 秒短路保护**：在 Grok Bot 模式下若误调 `--watch` 且未显式传入 `--force-watch`，立即在 0 秒内短路退出（`skipped: true, all_settled: true, polls: 0, reason: "grok_bot_supervision_disabled"`），绝不进入轮询循环。

## 真静默与全局统一汇总

- **True Quiescence Detection**：检查可见尾部最后 8 行（原始行及非空行）；出现 `background /tasks`、`ctrl+c `、`Working`、`Thinking`、`Running `、`Waiting for` 即覆盖 `idle`/`done` 为 `working`（非静默），绝不提前退出 `--watch`。关联 Goal 的任务仍 `in_progress` 且 TL 运行中时同样保持监督；在 `--status` 刷新时，若窗格已处于真静默（`agent_status` 为 `idle`/`done`、尾部无活动标记且无活跃 `in_progress` Goal TL），从 `working` 正常归位为 `idle`/`done`，并对比会话 `last_session_activity_at` 与 `ledger.md` 最新时间戳（**谁新展示谁**）。
- **零空转 `agentStop` / `sessionEnd` Hook（[`m2-board-hook.sh`](scripts/m2-board-hook.sh)）**：通过 `m2-router.py --install-hook`（或 [`install-m2-board-hook.sh`](scripts/install-m2-board-hook.sh)）幂等部署 `~/.copilot/hooks/m2-board.json` 与 `m2-board-hook.sh`（严格保留 `orca.json`、`pew.json`、`pew-copilot-hook.sh`、`herdr-agent-state.sh`）。0ms 排空 stdin 并后台脱离，仅在 `HERDR_ENV=1` 下加 `mkdir` 原子锁跑本地 `--status --no-sync-linear`，经 `--board-fingerprint` SHA-256 指纹防重与 60s 节流门禁后按需同步 `TIA-463`。
- **跨轮次共享账本与跨设备同步中枢（`TIA-463` · 同单 Work / Non-Work 双分区）**：默认 `/tmp/m2-supervision-state.json`，自动合并公开 `herdr agent list` 活跃 M2 与关联 Goal `ledger.md`，保留 Linear 主 Issue/Parent、M1 子阶段进度与 `backlog` 暂缓项（自动关联优先当前可见 Goal，不被旧标题/RAG 路径锁定；显式 `handoff_dir` 仍为准）；`--state-file` 可隔离状态，`--handoff-root`（或 `M2_HANDOFF_ROOT`）可指定台账根目录。通过 `--sync-linear [--hub-issue TIA-463]`（并在 `--apply`、`--watch` 退出、`--gc --approve` 时最佳努力静默触发）按设备分片 `devices.<hostname>` 与 Linear `TIA-463`（`<!-- GTT-M2-SYNC-HUB:v1 -->`）双向幂等同步，在同一个 Issue 内拆分为 **Part 1 · Work（工作业务）** 与 **Part 2 · Non-Work（非工作 / 个人与基建）** 两部分（各含待拍板、在途/常驻、暂缓 Backlog 与最近已完成），保留其他设备分片与富文本行。
- **强制三段式汇报与跨设备感知**：每次后台 `--watch` 唤醒向 Owner 汇报或调用 `--status` / `--plan` 时，直接输出完整 `unified_rollup_markdown`：**Wave 波次与上下游依赖链路** → **1. 🔔 本次完成** → **2. ✅ 累计已完成** → **3. ⏳ 仍在运行中**（含跨设备 `[Host: <device>]` 在途与待拍板项）。逐项包含 M2、pane、Wave/依赖、Linear `TIA-*`、M1 当前进度（含内部 Stage 链路），严禁只报单条而遗漏其他跨轮次在途或 blocked M2。结构化详情见 `in_flight_details` / `wave_dependency_chain` / `remote_devices`。

## CLI

生成计划 JSON（含 `preview_markdown`）并输出终端摘要；随后同步到 Linear Team TIA：

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --plan \
  --message '先检查通知开关，再写篇学习博客' --json > /tmp/m2-plan.json
```

Owner 审阅拍板后派发，后台异步执行 `post_apply_sync_task`（`--sync-herdr-to-issue` 扫描 Herdr 写回 Issue）并按 `watchdog_tasks` 为每个目标 M2 单独挂载后台 `--watch --watch-target`（完成/阻塞后自动将结果写入对应 Issue）：

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --apply \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json --approve --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --sync-herdr-to-issue \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json --hub-issue TIA-463 --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --watch \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json \
  --watch-target sapphire-notification --interval 15 --max-wait 2400 --json
```

随时巡检与跨设备对账：`python3 skills/personal-m2-assistant/scripts/m2-router.py --status [--sync-linear --hub-issue TIA-463]`（加 `--json` 返回结构化全局汇总）。全局回收默认只读预览：`--gc [--approve] [--json]`，仅 Owner 批准后关闭已完成孤儿 M1、重复静默 M2、已完成 Goal 的静默 M2，并清理对应监督槽位与同步跨设备账本。

常用离线参数：`--targets-json`、`--mock-jev-json`、`--mock-magpie-json`、`--domain-map-file`、`--preview-md-file`（显式导出）、`--transport stdout`。完整参数以脚本 `--help` 为准。

## 安全与顺序

- `wave_order` 只表示软顺序；真实上游结果通过 `rag_context_hint`/`context_pass` 传递，不伪造结果。
- Herdr 只向已有 agent prompt；新 M2（`NEW_M2` / `CREATE_NEW_M2`，含同批次多 M2 或同名仓库已有 Space 时）一律通过安全 dispatch wrapper 传入 `--new-workspace --workspace-label <space> -- --model gemini-4-argon --context long_context` 创建独立新 Space，绝不使用 `--workspace <workspace_id> --tab-label <name>` 创建 Copilot Tab，也绝不把调用方 `HERDR_PANE_ID` 作为 NEW_M2 分裂源。dsh 优先 Session Sink，失败时回退到明确目标的 Omni Hub dispatch。
- `--cleanup-duplicates` 只清理安全的重复 idle M2；`--replace-stale-m2` 可关闭安全 idle 旧窗并重开。保护 `HERDR_PANE_ID`、`w1:p1`、`w2J:p1`、Cindy/大总管与 working/blocked 窗格。
- Context Eviction 只有严格 AND 才成立：旧 Goal 当前有效完成且无 review/Owner decision，且 Jev 同时给出 `is_same_goal_continuation < 0.35` 与 `prior_context_value=noise_or_irrelevant`。缺失、无效、部分或冲突语义一律 `REUSE_EXISTING_M2`；Context 使用量、百分比或不同 Issue 单独绝不触发替换。
- `stdout` transport 只输出 receipt，不产生副作用；`auto` 依据发现来源选择适配器。

## 相关节点与验证

- How-To 节点：[`I7 Unified M2 Ingress`](../workflow-hub/references/how-to-nodes.md)。
- 向后兼容入口：[`m2-dispatcher/scripts/m2-router.py`](../m2-dispatcher/scripts/m2-router.py) 仅作 wrapper，权威实现位于本技能 `scripts/m2-router.py`。
- 验证与回归用例：[`eval-cases.json`](references/eval-cases.json)、`python3 -m unittest scripts/test_m2_router.py scripts/test_verify_skills.py`、`python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict`、`./scripts/verify`。
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/eval-cases.json` | `sh skills/personal-m2-assistant/scripts/install-m2-board-hook.sh --help` |
| verification | `references/eval-cases.json` | `skill_contract.py eval --skill personal-m2-assistant --strict` |
