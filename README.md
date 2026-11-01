# user-skills

面向 Claude Code、Codex 等 CLI agent 的公开通用 skills，可独立使用，不依赖特定项目。

[`goal-task`](./skills/goal-task/SKILL.md) 是规划或执行开发工作前加载的 `Goal -> Task` 两层执行合同，适用于 Phase 0 交互共创设计与 Phase 1 自治执行（单会话及 subagent 协作）。它定义范围与验收、唯一写者（`1 Task = 1 Writer = 1 Worktree = 1 PR`）、定义冻结、交接和双轴独立审查，并提供 CLI 分工、嵌套限制及执行台账模板；内附全景手册 [`playbook.md`](./skills/goal-task/references/playbook.md) 与交互式飞轮图 [`ai-native-flywheel.html`](./skills/goal-task/assets/ai-native-flywheel.html)。旧目录名 `goal-target-task` 已移除。

[`bot-tl-orchestration`](./skills/bot-tl-orchestration/SKILL.md) 是常驻域主 Bot 与 Goal-level TL 之间的共用生命周期合同，覆盖开 TL、热追加、巡检解卡，以及 `PR_MERGED` Task 回收与 `GOAL_DONE` Goal 清理；Task-level 1-Hop 规则继续由 `goal-task` 提供。

[`code-review`](./skills/code-review/SKILL.md) 审查分支、PR、工作树或实现交接，分别核对仓库规范与功能意图，支持实现者自检和独立审查。

[`xcode-cli`](./skills/xcode-cli/SKILL.md) 使用 Swift 与 Xcode 命令行工具进行 Apple 平台构建、测试、模拟器运行和结果检查。

[`deep-research`](./skills/deep-research/SKILL.md) 对主题进行多来源调研与交叉核查，输出带引用、按置信度分级的报告。

[`auto-review-merge`](./skills/auto-review-merge/SKILL.md) 为 GitHub 仓库配置基于本地 Claude CLI 的 PR 审查门禁、自托管 runner 和自动合并流程。

[`codebase-design`](./skills/codebase-design/SKILL.md) 提供深模块、接口、接缝和适配器的设计词汇与方法。

[`domain-modeling`](./skills/domain-modeling/SKILL.md) 梳理领域术语，按需维护 `CONTEXT.md` 和架构决策记录。

[`my-designer`](./skills/my-designer/SKILL.md) 将五层设计能力、四种视觉模式和 `agent-design-doc/v1` 规范化为可交接的设计合同。

[`visual-design-modernization`](./skills/visual-design-modernization/SKILL.md) 通过 verbal spec、直接产品实现和真实渲染复核推进视觉现代化。

`codebase-design`、`domain-modeling` 衍生自 [Matt Pocock 的 skills](https://github.com/mattpocock/skills)，按 MIT 许可使用；各目录附上原始版权与许可文本。

## 安装

Claude Code 使用 plugin marketplace：

```text
/plugin marketplace add LeePepe/user-skills
/plugin install goal-task@user-skills
/plugin install code-review@user-skills
/plugin install bot-tl-orchestration@user-skills
/plugin install xcode-cli@user-skills
/plugin install deep-research@user-skills
/plugin install auto-review-merge@user-skills
/plugin install codebase-design@user-skills
/plugin install domain-modeling@user-skills
```

Codex 使用内置 `skill-installer`，在会话中输入：

```text
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/goal-task 安装 goal-task。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/bot-tl-orchestration 安装 bot-tl-orchestration。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/code-review 安装 code-review。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/xcode-cli 安装 xcode-cli。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/deep-research 安装 deep-research。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/auto-review-merge 安装 auto-review-merge。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/codebase-design 安装 codebase-design。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/domain-modeling 安装 domain-modeling。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/my-designer 安装 my-designer。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/visual-design-modernization 安装 visual-design-modernization。
```

安装后重启 Codex，使新 skill 可被发现；规划或执行开发工作前只加载 `goal-task`。旧目录名 `goal-target-task` 已删除，不再保留兼容安装路径。

## 验证

本地开发或提交前可启用仓库 Git hooks 并运行全量校验门禁：

```bash
git config core.hooksPath .githooks
./scripts/verify --all
```
