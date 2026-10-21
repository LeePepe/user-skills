# GTT 角色与模型推荐

本文件仅为当前环境的推荐配置，可随价格与可用性调整；角色权限、审批与审查规则以 [SKILL.md](../SKILL.md) 为准，工具能力不扩大合同授权。

| 角色 | 首选 | 备选 | 说明 |
|---|---|---|---|
| TL（协调者） | Claude Code `opus` | Copilot `gpt-6.1-sol` → `gemini-4-argon` | 错判成本最高，用强模型；不兼任写者或独立验收 |
| Planner（定义方） | codex `gpt-6.1-sol` | — | 编制定义与 revision，交 TL 审定及独立计划审查 |
| Reviewer（独立验收） | codex `gpt-6.1-sol`，独立会话 | 分歧时 `cursor-agent` `grok-4.7-high` 作 tiebreaker | 未接触写者上下文；按 V1–V5 |
| FS（Task 写者） | codex `gpt-5.6-luna` | `cursor-agent` `composer-2.5` | token 消耗最大的角色，用便宜模型 |
| Explorer（只读探查） | codex `gpt-5.6-luna` | `cursor-agent` `composer-2.5` | R11，只读 |
| 机械性工作 | `gpt-5.6-luna`（low effort） | `composer-2.5` | 格式化、批量替换、台账整理等 |
| 额外意见 | Kimi | — | 仅在需要第二意见时使用，不担任固定角色 |

## 启动约定

- 写者：在各自 worktree 内以 `codex -s danger-full-access -a never -m <model>` 启动（read-only 或 workspace-write 沙箱无法写 git worktree 元数据）。
- Explorer：`codex exec -s read-only -m gpt-5.6-luna` 或 `cursor-agent --mode ask --model composer-2.5`。
- Reviewer：`codex exec -s read-only -m gpt-6.1-sol`，每轮新会话。
- Cursor CLI 一律用 `cursor-agent`，不要用裸 `agent`（可能解析到其他 CLI）。
