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
- 业务话题与仓库映射的唯一事实源是 [`m2-domain-map.json`](references/m2-domain-map.json)。

## 默认配置与强制 Owner Gate

1. 只读发现公开的 Herdr `pane/agent list` 与 dsh Omni Hub 状态；不读取 dsh 私有 session 存储。
2. 调用 Jev/Falcon 做枚举选路，再由 Magpie 语义拆解并绑定已有会话；离线或网关不可达时只使用确定性兜底。
3. **默认模型/Context 与 `~` 展开**：所有目标 M2 默认使用 `Gemini 4 Argon · 983K context`（`--model gemini-4-argon --context long_context`）；所有 `cwd`（含 `m2-domain-map.json`、会话发现、新话题候选与派发参数）统一先展开 `~` 为绝对路径再传给 `herdr-dispatch.sh` 或 `dsh create-session`。
4. **Orca 临时 `.md` 计划预览**：由于 `herdr` 不原生支持打开/渲染 `.md` 文件，`--plan` 自动生成 `/tmp/m2-plan-<plan_id>.md`（含完整 `sub_prompt`、wave 表、model/context/cwd、旧窗清理与新域候选）并调用 `orca file open <path> --focus` 聚焦预览（无 GUI/测试时可用 `--no-open-preview` 或自动优雅降级），同时通过 [`preview_text`](scripts/m2-router.py) 输出终端摘要。
5. 使用 `ask_user` 请求 Owner 确认或修改。计划阶段绝不创建会话、关闭窗格或投递消息。
6. 仅 Owner 明确批准后运行 `--apply --plan-file <plan.json> --approve`；未带 `--approve` 必须 fail-closed。
7. **后台异步巡检与安全 Allow 自动解卡（`--watch`）**：`--apply` 返回 `watchdog_copilot_cli`（`mode="async", detach=true, shellId="watch-m2-<plan_id>"`），在后台运行 `m2-router.py --watch` 轮询被派发的目标 M2 直至全部进入静默态（`idle`/`done`）：
   - 遇到安全权限弹窗（realpath 解析后落在安全基线/目标 `cwd` 下的 `Allow path access`、`Curate Memory`、`Confirm folder trust`，以及显式只读白名单命令确认 `CMD_CONFIRM`），仅以单次有界 `enter` 放行并继续轮询（不自动勾选持久化 remember/allow-all 选项）；
   - 遇到业务/Spec 级 `ask_user` 提问、路径越界/符号链接逃逸或非只读/含元字符命令，**绝不代拍板**，标记 `needs_owner_attention=true`（`blocked_reason="owner_decision_required"`）并在完成汇总中将各目标 M2 的可见结果与待拍板项一并呈交当前会话的 Owner。

## CLI

生成计划并在 Orca 打开 `.md` 预览：

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --plan \
  --message '先检查通知开关，再写篇学习博客' --json > /tmp/m2-plan.json
```

Owner 审阅拍板后派发并挂载后台 `--watch`：

```bash
python3 skills/personal-m2-assistant/scripts/m2-router.py --apply \
  --plan-file /tmp/m2-plan.json --approve --json
python3 skills/personal-m2-assistant/scripts/m2-router.py --watch \
  --plan-file /tmp/m2-plan.json --interval 15 --max-wait 2400 --json
```

常用离线参数：`--targets-json`、`--mock-jev-json`、`--mock-magpie-json`、`--domain-map-file`、`--preview-md-file`、`--no-open-preview`、`--transport stdout`。完整参数以脚本 `--help` 为准。

## 安全与顺序

- `wave_order` 只表示软顺序；真实上游结果通过 `rag_context_hint`/`context_pass` 传递，不伪造结果。
- Herdr 只向已有 agent prompt；新 M2 通过安全 dispatch wrapper 启动并传入 `-- --model gemini-4-argon --context long_context`。dsh 优先 Session Sink，失败时回退到明确目标的 Omni Hub dispatch。
- `--cleanup-duplicates` 只清理安全的重复 idle M2；`--replace-stale-m2` 可关闭安全 idle 旧窗并重开。保护 `HERDR_PANE_ID`、`w1:p1` 与 working/blocked 窗格。
- `stdout` transport 只输出 receipt，不产生副作用；`auto` 依据发现来源选择适配器。

## 相关节点与验证

- How-To 节点：[`I7 Unified M2 Ingress`](../workflow-hub/references/how-to-nodes.md)。
- 向后兼容入口：[`m2-dispatcher/scripts/m2-router.py`](../m2-dispatcher/scripts/m2-router.py) 仅作 wrapper，权威实现位于本技能 `scripts/m2-router.py`。
- 验证：`python3 -m unittest scripts/test_m2_router.py scripts/test_verify_skills.py`、`python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict`、`./scripts/verify`。
