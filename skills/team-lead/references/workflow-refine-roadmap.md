# 工程全景飞轮推进路线图与跨机启动指令

本路线图连接 [`playbook.md`](./playbook.md) 的三大板块与后续执行切片；真正执行时须由 CLI 主控（TL）按 `Goal -> Task` 合同冻结范围、接口、`Owned Paths`、验收命令和 R10 回执。总工作流入口见 [`workflow-hub`](../../workflow-hub/SKILL.md)，看板见 [`linear-board`](../../linear-board/SKILL.md)，复盘与叙事分别由 [`retro-loop`](../../retro-loop/SKILL.md) 和 [`story-craft`](../../story-craft/SKILL.md) 承接。6DQ 采用 **Progressive Discovery Ladder**：先 D1 隔离轻检，再按 L1、L2、G2、L3 + Release Readback 逐步加深；**Not an upfront all-at-once constraint**，不在动工前一刀切阻塞 Agent。

## §1 跨机同步与一键加载指南

以下命令适用于 Mac mini 与 MacBook（旧目录 `goal-target-task` 已移除，只保留 `team-lead`）：

```bash
export USER_SKILLS_ROOT="${USER_SKILLS_ROOT:-$HOME/Development/user-skills}"
git -C "$USER_SKILLS_ROOT" pull --ff-only
for d in "$HOME/.codex/skills" "$HOME/.copilot/skills" "$HOME/.claude/skills"; do
  mkdir -p "$d" && rm -rf "$d/goal-target-task" && rsync -a "$USER_SKILLS_ROOT/skills/team-lead/" "$d/team-lead/"
done
test -f "$HOME/.codex/skills/team-lead/SKILL.md" && test ! -e "$HOME/.codex/skills/goal-target-task"
```

## §2 全景三大板块落地路线图（1:1:1 均衡推进）

| Track | 切片 ID | 核心目标与交付物 |
|---|---|---|
| **Track A：开发与交付底座对齐** | `T-DEV-1` | 统一三类入口（CLI / `Grok Bot` / `Cindy`）通过 `herdr-dispatch.sh --new-workspace --role team_lead` 派生独立 `Steering`（`group/steering`），对齐 `1 Goal = 1 Branch / 最多 1 PR`、本地 `keep / drop`、R8/R10 与单次异构审查 |
| **Track A** | `T-DEV-2` | 本地 `PreToolUse` Worktree 物理写入守卫（拦截主 checkout 直改）＋Herdr `events.subscribe` 零轮询监听契约 |
| **Track A** | `T-DEV-3` | 用 `sync_contract.py` 分批推广 6DQ 质量契约矩阵至核心仓库，配齐 `release_sync.py` 读回对账 |
| **Track A** | `T-DEV-4` | 在 UI 业务仓落地 `docs/design-contract.md`（四态＋双外观），接入 `design_contract.py` 与 `my-designer`（Plan A/C）及多态真实截图闭环 |
| **Track B：双路复盘回馈** | `T-RETRO-A1` | 基于 `shared-telemetry` / `aidata` 建立 PLT P90、FPS、首屏耗时与 DSAT 异常检测规则，告警自动回流性能优化 Task |
| **Track B** | `T-RETRO-A2` | 串联 Crash 聚簇、`postmortem` 根因考古、RED/GREEN 修复 Task 与发布后 Crash-Free 率读回状态机 |
| **Track B** | `T-RETRO-B1` | 基于 H1–H5 五步诊断法与 `stuck-PR` 信号识别流程卡点，经反事实检验后沉淀入三道物理锁 |
| **Track C：总结与叙事成文** | `T-STORY-1` | 基于《四要素叙事素材抽取单》建立脚本流水线，统一 provenance、Quantitative Delta、Pivot、Takeaway 与 Desensitization 字段校验 |
| **Track C** | `T-STORY-2` | 使用真实复盘素材产出性能／Crash 深度技术 Blog 与《带一群 AI 干活》续篇，经双栏阅读器脱敏构建与线上读回 |

### 推荐推进顺序与停止线

1. 先完成 `T-DEV-1` 公共字段与验收矩阵，再推进各 Track 首个可运行切片；`T-DEV-2` 与 `T-DEV-3` 可并行，但门禁/Hook 变更须有运行证据。
2. `T-RETRO-A1`/`A2` 先建最小可观测闭环；`T-STORY-1`/`2` 只消费已确认且完成脱敏与读回的证据。
3. 缺少准确 head、R10 回执、验收命令结果或来源证据时，立即停止宣称完成并转为 `blocked` 或 `changes_requested`。

## §3 可直接复制给 CLI 主控（TL）的启动 Prompt

```text
你是本仓库本次 Goal 的 TL。先加载 team-lead，阅读 playbook.md 与 workflow-refine-roadmap.md：
1. 核对当前分支、基线 SHA、仓库规则与已有变更；明确 Background、Non-goals、Dependencies、Owned Paths、Interfaces、验收命令和 R10 路径。
2. 遵循 1 Goal = 1 Branch / 最多 1 PR（子 Task 本地 keep / drop）；先写测试或可执行验收再实现最小切片。
3. 运行全部验证命令，检查 diff --check、敏感信息、文档链接与 R10 回执，只报告可核实事实与准确 head。
```
