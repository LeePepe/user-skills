# user-skills

面向 Claude Code、Codex 等 CLI agent 的公开通用 skills，可独立使用，不依赖特定项目。

[`goal-target-task`](./skills/goal-target-task/SKILL.md) 是规划或执行开发工作前加载的 Goal / Target / Task 执行合同，适用于单会话及 subagent 协作。它定义范围与验收、唯一写者、定义冻结、交接和独立审查，并提供 CLI 分工、嵌套限制及执行台账模板。

## 安装

Claude Code 使用 plugin marketplace：

```text
/plugin marketplace add LeePepe/user-skills
/plugin install goal-target-task@user-skills
```

Codex 使用内置 `skill-installer`，在会话中输入：

```text
请用 skill-installer 从 https://github.com/LeePepe/user-skills/tree/main/skills/goal-target-task 安装 goal-target-task。
```

安装后重启 Codex，使新 skill 可被发现；规划或执行开发工作前加载 `goal-target-task`。
