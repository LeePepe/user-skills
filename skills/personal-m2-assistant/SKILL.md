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
- 业务话题与仓库映射的唯一事实源是 [`m2-domain-map.json`](references/m2-domain-map.json)；每个 topic 的 `space`/`space_aliases` 是 Herdr Space 路由的唯一事实源。无关 user-skills 按 topic 隔离，`my-designer` + `design-studio` 共用专属 `skills-design` Space，同时兼容旧的粗粒度 topic alias。
- 发现阶段读取公开的 `herdr workspace list`；交互式 Herdr 的 `agent_status=done` 仍可作为 `EXISTING_M2` 复用（仅真正 exited/stopped 等状态终止）。

## 默认配置与强制 Owner Gate

1. 只读发现公开的 Herdr `pane/agent list` 与 dsh Omni Hub 状态；不读取 dsh 私有 session 存储。
2. 调用 Jev/Falcon 做枚举选路，再由 Magpie 语义拆解并绑定已有会话；离线或网关不可达时只使用确定性兜底。
3. **默认模型/Context 与 `~` 展开**：所有目标 M2 默认使用 `Gemini 4 Argon · 983K context`（`--model gemini-4-argon --context long_context`）；所有 `cwd`（含 `m2-domain-map.json`、会话发现、新话题候选与派发参数）统一先展开 `~` 为绝对路径再传给 `herdr-dispatch.sh` 或 `dsh create-session`。
4. **Orca 临时 `.md` 计划预览**：由于 `herdr` 不原生支持打开/渲染 `.md` 文件，`--plan` 自动生成 `/tmp/m2-plan-<plan_id>.md`（含完整 `sub_prompt`、wave 表、model/context/cwd、旧窗清理与新域候选）并调用 `orca file open <path> --focus` 聚焦预览（无 GUI/测试时可用 `--no-open-preview` 或自动优雅降级），同时通过 [`preview_text`](scripts/m2-router.py) 输出终端摘要。
5. 使用 `ask_user` 请求 Owner 确认或修改。计划阶段绝不创建会话、关闭窗格或投递消息。
6. 仅 Owner 明确批准后运行 `--apply --plan-file <plan.json> --approve`；未带 `--approve` 必须 fail-closed。
7. **每个 M2 独立后台监督与增量滚动汇总（`--watch --watch-target` + `watchdog_tasks`）**：`--apply` 将回执落盘至 `/tmp/m2-apply-<plan_id>.json`（或 `--apply-receipt-file`），同步维护共享监督状态账本 `m2-supervision-state.json`（或 `--state-file`），并为**每个已派发 M2 单独生成一条** `watchdog_copilot_cli`（汇总于 `watchdog_tasks`，每条 `mode="async", detach=true, shellId="watch-m2-<plan_id>-<slot>-r<seq>"`，带 `--watch-target <slot_id>`）：
   - **单 M2 独立完成即刻唤醒与累积汇总**：每个 M2 由各自独立的 background `bash` 任务运行 `m2-router.py --watch --watch-target <slot_id>`，任一 M2 进入静默态（`idle`/`done`）即立刻退出唤醒并更新账本，立即报告**本次完成项 + 当前所有已完成 M2 的累积汇总（`completed_rollup_code`）**。例如派发 `ABC`：`A` 完成报告 `A`，`B` 完成报告 `AB`；若此时新增 `D` 且向 `A` 追加新任务 `A2`（`A` 槽位转为运行 `A2`），随后 `C` 完成报告 `BC`，`A2` 完成报告 `A2BC`，`D` 完成报告 `A2BCD`。
   - **安全 Allow 自动解卡与 Owner 决策门禁**：仅当传入经校验的 `apply_receipt`（`ok=true`、`approved=true`、`plan_id` 与目标匹配）且目标处于 `blocked`/`needs_input` 时，对底部当前活动帧的安全权限弹窗（词法与 `resolve` 均落在安全基线/目标 `cwd` 且无符号链接越界的完整单路径 `Allow path access`/`Confirm folder trust`、`Curate Memory`，以及单行无元字符的严格只读参数白名单命令 `CMD_CONFIRM`）发送单次 `enter` 放行；无有效回执时保持纯被动只读观察（`auto_unblock_enabled=false`）。遇到业务/Spec 级 `ask_user`、`Proceed with`、路径越界/多路径或非只读命令，**绝不代拍板**，标记 `needs_owner_attention=true`（`blocked_reason="owner_decision_required"`）；目标缺失或发现中断持续重试至超时后返回 `ok=false, all_settled=false, blocked_reason="target_missing_or_unreachable"`。

## CLI

生成计划并在 Orca 打开 `.md` 预览：

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --plan \
  --message '先检查通知开关，再写篇学习博客' --json > /tmp/m2-plan.json
```

Owner 审阅拍板后派发，并按 `watchdog_tasks` 为每个目标 M2 单独挂载后台 `--watch --watch-target`：

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --apply \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json --approve --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --watch \
  --plan-file /tmp/m2-plan.json --apply-receipt-file /tmp/m2-apply.json \
  --watch-target sapphire-notification --interval 15 --max-wait 2400 --json
```

常用离线参数：`--targets-json`、`--mock-jev-json`、`--mock-magpie-json`、`--domain-map-file`、`--preview-md-file`、`--no-open-preview`、`--transport stdout`。完整参数以脚本 `--help` 为准。

## 安全与顺序

- `wave_order` 只表示软顺序；真实上游结果通过 `rag_context_hint`/`context_pass` 传递，不伪造结果。
- Herdr 只向已有 agent prompt；新 M2 通过安全 dispatch wrapper 启动并传入 `-- --model gemini-4-argon --context long_context`。Space 已存在时使用 `--workspace <workspace_id> --tab-label <name>` 创建 Tab，不存在时使用 `--new-workspace --workspace-label <space>`；绝不把调用方 `HERDR_PANE_ID` 作为 NEW_M2 分裂源。dsh 优先 Session Sink，失败时回退到明确目标的 Omni Hub dispatch。
- `--cleanup-duplicates` 只清理安全的重复 idle M2；`--replace-stale-m2` 可关闭安全 idle 旧窗并重开。保护 `HERDR_PANE_ID`、`w1:p1` 与 working/blocked 窗格。
- `stdout` transport 只输出 receipt，不产生副作用；`auto` 依据发现来源选择适配器。

## 相关节点与验证

- How-To 节点：[`I7 Unified M2 Ingress`](../workflow-hub/references/how-to-nodes.md)。
- 向后兼容入口：[`m2-dispatcher/scripts/m2-router.py`](../m2-dispatcher/scripts/m2-router.py) 仅作 wrapper，权威实现位于本技能 `scripts/m2-router.py`。
- 验证：`python3 -m unittest scripts/test_m2_router.py scripts/test_verify_skills.py`、`python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict`、`./scripts/verify`。
