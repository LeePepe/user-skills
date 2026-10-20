# user-skills

面向 Claude Code、Codex 等 CLI agent 的公开通用 skills，可独立使用，不依赖特定项目。

[`goal-target-task`](./skills/goal-target-task/SKILL.md) 是规划或执行开发工作前加载的 Goal / Target / Task 执行合同，适用于单会话及 subagent 协作。它定义范围与验收、唯一写者、定义冻结、交接和独立审查，并提供 CLI 分工、嵌套限制及执行台账模板。

[`code-review`](./skills/code-review/SKILL.md) 审查分支、PR、工作树或实现交接，分别核对仓库规范与功能意图，支持实现者自检和独立审查。

[`xcode-cli`](./skills/xcode-cli/SKILL.md) 使用 Swift 与 Xcode 命令行工具进行 Apple 平台构建、测试、模拟器运行和结果检查。

[`deep-research`](./skills/deep-research/SKILL.md) 对主题进行多来源调研与交叉核查，输出带引用、按置信度分级的报告。

[`auto-review-merge`](./skills/auto-review-merge/SKILL.md) 为 GitHub 仓库配置基于本地 Claude CLI 的 PR 审查门禁、自托管 runner 和自动合并流程。

[`codebase-design`](./skills/codebase-design/SKILL.md) 提供深模块、接口、接缝和适配器的设计词汇与方法。

[`domain-modeling`](./skills/domain-modeling/SKILL.md) 梳理领域术语，按需维护 `CONTEXT.md` 和架构决策记录。

[`speckit`](./skills/speckit/SKILL.md) 遵循目标仓库已有的 Spec Kit 规划阶段、模板和验收约束。

[`helix`](./skills/helix/SKILL.md) 面向 Figma 与代码对照、界面生成和设计系统指南的工作流。

## 安装

Claude Code 使用 plugin marketplace：

```text
/plugin marketplace add LeePepe/user-skills
/plugin install goal-target-task@user-skills
/plugin install code-review@user-skills
/plugin install xcode-cli@user-skills
/plugin install deep-research@user-skills
/plugin install auto-review-merge@user-skills
/plugin install codebase-design@user-skills
/plugin install domain-modeling@user-skills
/plugin install speckit@user-skills
/plugin install helix@user-skills
```

Codex 使用内置 `skill-installer`，在会话中输入：

```text
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/goal-target-task 安装 goal-target-task。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/code-review 安装 code-review。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/xcode-cli 安装 xcode-cli。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/deep-research 安装 deep-research。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/auto-review-merge 安装 auto-review-merge。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/codebase-design 安装 codebase-design。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/domain-modeling 安装 domain-modeling。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/speckit 安装 speckit。
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/helix 安装 helix。
```

安装后重启 Codex，使新 skill 可被发现；规划或执行开发工作前加载 `goal-target-task`。
