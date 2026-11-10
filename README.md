# user-skills

面向 Claude Code、Codex 等 CLI agent 的公开通用 skills，可独立使用，不依赖特定项目。

[`workflow-hub`](./skills/workflow-hub/SKILL.md) 是 1-Hub + Open Capability Graph：
当前挂载 `linear-board`、Phase 0 Co-Design、Autonomous Build, Review & Merge、
Product Delivery/Release，以及 `retro-loop` + `story-craft` 等能力节点。节点按
证据和状态任意直连，不要求固定顺序；它用 4-layer Product/Output Matrix 串起应用、
共享底座、研发工作流和知识叙事，并把复盘结论回流到 TIA。

自治调度采用 [`workflow-hub`](./skills/workflow-hub/SKILL.md) 的 Cron / `launchd`
**Precheck-First, Wake-on-Actionable** 心跳：先做零 Token 预检，只有存在可执行
事件时才唤醒对应 Bot 或 Sticky TL。

[`team-lead`](./skills/team-lead/SKILL.md) 是规划或执行开发工作前加载的 `Goal -> Task` 两层执行合同，适用于 Phase 0 交互共创设计与 Phase 1 自治执行（单会话及 subagent 协作）。它定义范围与验收、唯一写者（`1 Task = 1 Writer = 1 Worktree = 1 PR`）、定义冻结、交接和双轴独立审查，并提供 CLI 分工、嵌套限制及执行台账模板；内附全景手册 [`playbook.md`](./skills/team-lead/references/playbook.md) 与交互式飞轮图 [`ai-native-flywheel.html`](./skills/team-lead/assets/ai-native-flywheel.html)。旧目录名 `goal-target-task` 已移除。

[`linear-board`](./skills/linear-board/SKILL.md) 负责 TIA Idea Inbox、`1 Goal = 1 Issue`
全局状态看板、Task checklist 和 Idea promotion；[`retro-loop`](./skills/retro-loop/SKILL.md)
负责双路复盘与三道物理锁；[`story-craft`](./skills/story-craft/SKILL.md) 负责四元素
素材抽取、脱敏、Story Series、Tech Deep-Dive Blog 与 Deck 发布读回。

[`m2-dispatcher`](./skills/m2-dispatcher/SKILL.md)是 `M2-Dispatcher`（常驻域主 Bot、`Cindy` 与交互式 CLI 入口）与 Goal-level TL 之间的共用生命周期合同，覆盖开 TL、热追加、巡检解卡，以及 `PR_MERGED` Task 回收与 `GOAL_DONE` Goal 清理；Task-level 1-Hop 规则继续由 `team-lead` 提供。

### `m2-dispatcher` 与 `team-lead` 脚本及参考文档职责划分（100% 路径兼容）

| 类别 | 规范入口与兼容软链路径 | 包含文件与职责 |
|---|---|---|
| **Shared M2+M1 Orchestration Scripts**（M2 调度与 M1 主控共用） | `skills/m2-dispatcher/scripts/<name>` ⇄ `skills/team-lead/scripts/<name>` | `herdr-dispatch.sh`（Workspace/Pane 启动与模型路由）、`goal-supervisor.py`（0-Token `watch`/`scan` 巡检与解卡告警）、`goal-queue.py`（批量/热追加波次队列）、`state_store.py`（SQLite 状态与耐用效果队列）、`jev_client.py`（Jev 决策客户端） |
| **Shared M2+M1 References**（M2 与 M1 共用事实源） | `skills/m2-dispatcher/references/<name>` ⇄ `skills/team-lead/references/<name>` | `runtime-profiles.yaml`（模型、机器与白名单档位）、`bots-and-agents.md`（角色、启动、监控与回收规则）、`ledger.md`（Goal 台账事件与 R10 证据格式） |
| **M2-Only Harness Scripts**（仅 M2 入口扩展与安装器） | `skills/m2-dispatcher/scripts/<name>` | `skill-slash-expander.ts`（Pi/Cindy 裸 `/skill` 展开 + `M2` 仅写 Spec 的 `tool_call` 硬门禁）、`install-pi-harness.sh`（同步扩展并更新 `cindy-pi-package-fingerprint-v1`） |
| **M1 Task Execution Scripts**（M1 `team-lead` 切片执行与收口） | `skills/team-lead/scripts/<name>` | `ci-monitor.py`（Layer-0 远端 PR/Workflow 监控与合并 GC）、`git-gc.py`（Worktree 清理）、`feedback_guard.py`（反馈验收映射守卫）、`gtt-telemetry-export.py`（遥测导出）、`linear-sync.py`（Linear TIA 同步）、`jev-compact.py`、`jev-skill-router.py` |

[`code-review`](./skills/code-review/SKILL.md) 审查分支、PR、工作树或实现交接，分别核对仓库规范与功能意图，支持实现者自检和独立审查。

[`xcode-cli`](./skills/xcode-cli/SKILL.md) 使用 Swift 与 Xcode 命令行工具进行 Apple 平台构建、测试、模拟器运行和结果检查。

[`deep-research`](./skills/deep-research/SKILL.md) 对主题进行多来源调研与交叉核查，输出带引用、按置信度分级的报告。

[`codebase-design`](./skills/codebase-design/SKILL.md) 提供深模块、接口、接缝和适配器的设计词汇与方法。

[`domain-modeling`](./skills/domain-modeling/SKILL.md) 梳理领域术语，按需维护 `CONTEXT.md` 和架构决策记录。

[`my-designer`](./skills/my-designer/SKILL.md) 是双入口设计流的 **Entry B（Product Design, Modernization & Post-Impl Visual Traceback）**：采用渐进式披露（`L0 -> L1 -> L2`）、三端平台防火墙、Plan A 五大透镜、6 维存量界面现代化改版（`No Code Demo`）与 Plan C 缺陷演进机制，产出 `design.md`/`spec.md` 并在实现后结合真实截图执行 `[IMPL_BUG]` vs `[SPEC_GAP]` 视觉回溯；底层 `references/` 与 `scripts/` 与 `design-studio` 100% 软链互通。

[`design-studio`](./skills/design-studio/SKILL.md) 是双入口设计流的 **Entry A（Ingest, Stockpile & Video）**：专管外部设计/交互/动效灵感、截图色板测量（`--measure-palette`）、交互模式（`IP-*/MP-*`）与共性缺陷卡（`DC-*`）分类入库至共享 JSON 抽屉，并在 `--surface-mode video` 时按需披露 Remotion 与 FFmpeg 视频流水线。

`codebase-design`、`domain-modeling` 衍生自 [Matt Pocock 的 skills](https://github.com/mattpocock/skills)，按 MIT 许可使用；各目录附上原始版权与许可文本。

## 安装

Claude Code 使用 plugin marketplace：

```text
/plugin marketplace add LeePepe/user-skills
/plugin install workflow-hub@user-skills
/plugin install linear-board@user-skills
/plugin install team-lead@user-skills
/plugin install m2-dispatcher@user-skills
/plugin install code-review@user-skills
/plugin install m2-dispatcher@user-skills
/plugin install xcode-cli@user-skills
/plugin install deep-research@user-skills
/plugin install codebase-design@user-skills
/plugin install domain-modeling@user-skills
/plugin install my-designer@user-skills
/plugin install design-studio@user-skills
/plugin install retro-loop@user-skills
/plugin install story-craft@user-skills
```

Codex 使用内置 `skill-installer`，在会话中输入：

```text
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/team-lead 安装 team-lead。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/workflow-hub 安装 workflow-hub。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/linear-board 安装 linear-board。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/m2-dispatcher 安装 m2-dispatcher。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/m2-dispatcher 安装 m2-dispatcher。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/code-review 安装 code-review。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/xcode-cli 安装 xcode-cli。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/deep-research 安装 deep-research。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/codebase-design 安装 codebase-design。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/domain-modeling 安装 domain-modeling。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/my-designer 安装 my-designer。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/design-studio 安装 design-studio。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/retro-loop 安装 retro-loop。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/story-craft 安装 story-craft。
```

安装后重启 Codex，使新 skill 可被发现；规划或执行开发工作前只加载 `team-lead`。旧目录名 `goal-target-task` 已删除，不再保留兼容安装路径。

## 验证

本地开发或提交前可启用仓库 Git hooks 并运行全量校验门禁：

```bash
git config core.hooksPath .githooks
./scripts/verify --all
```
