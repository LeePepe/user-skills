# 工程全景飞轮推进路线图与跨机启动指令

这份路线图把全景飞轮图、`playbook.md` 的三大板块和后续执行切片连接起来。它只定义可交接的方向与启动方式；每个方向真正开始时，仍须由 CLI 主控（TL）按 `Goal -> Task` 合同冻结范围、接口、Owned Paths、验收命令和 R10 回执。

## §1 跨机同步与一键加载指南

以下命令适用于 Mac mini 与 MacBook。把 `<repository-url>` 替换为实际仓库地址；不要把本机绝对路径写进任务 brief、回执或仓库文档。

### 1.1 拉取或更新仓库

首次使用：

```bash
export USER_SKILLS_ROOT="$HOME/Development/user-skills"
git clone <repository-url> "$USER_SKILLS_ROOT"
cd "$USER_SKILLS_ROOT"
```

已有工作副本：

```bash
export USER_SKILLS_ROOT="$HOME/Development/user-skills"
cd "$USER_SKILLS_ROOT"
git pull --ff-only
```

### 1.2 同步三套 CLI skills

```bash
export USER_SKILLS_ROOT="${USER_SKILLS_ROOT:-$HOME/Development/user-skills}"
mkdir -p "$HOME/.codex/skills" "$HOME/.copilot/skills" "$HOME/.claude/skills"

rsync -a "$USER_SKILLS_ROOT/skills/goal-task/" "$HOME/.codex/skills/goal-task/"
rsync -a "$USER_SKILLS_ROOT/skills/goal-task/" "$HOME/.copilot/skills/goal-task/"
rsync -a "$USER_SKILLS_ROOT/skills/goal-task/" "$HOME/.claude/skills/goal-task/"
```

旧目录名 `goal-target-task` 已移除。若本机仍留有该目录，删除后再继续，只保留 `goal-task`：

```bash
rm -rf "$HOME/.codex/skills/goal-target-task" \
  "$HOME/.copilot/skills/goal-target-task" \
  "$HOME/.claude/skills/goal-target-task"
```

### 1.3 同步后快速核验

```bash
test -f "$HOME/.codex/skills/goal-task/SKILL.md"
test -f "$HOME/.copilot/skills/goal-task/references/workflow-refine-roadmap.md"
test -f "$HOME/.claude/skills/goal-task/assets/ai-native-flywheel.html"
test ! -e "$HOME/.codex/skills/goal-target-task"
test ! -e "$HOME/.copilot/skills/goal-target-task"
test ! -e "$HOME/.claude/skills/goal-target-task"
```

完成同步后，重启对应 CLI，使新增或更新的 skill 被重新发现。后续执行只加载 `goal-task`。

## §2 全景三大板块落地路线图

三条 Track 按 `1:1:1` 均衡推进：每条 Track 先建立一个可验收 Goal，再拆成独立 Task；不要把路线图条目直接当作已经批准的实现任务。跨 Track 的共享契约、遥测字段、发布门禁或素材抽取格式必须显式写进 `Interfaces: Consumes / Produces`。

### Track A：开发与交付底座对齐

**目标**：让规划、实现、审查、质量门禁和发布读回共用同一套两层模型与证据契约。

- **`T-DEV-1`：对齐 `agent-skills` 工作流（01–06）**
  - 将 01–06 的规划、CLI/Herdr、PR、发布、遥测和维护入口对齐到最新 `goal-task` 两层模型。
  - 统一 `1 Task = 1 PR`、状态机、R9 漂移处理、R10 回执和跨厂商独立审查字段。
  - 交付：每个工作流的输入、输出、依赖和验收矩阵，附一条可运行的端到端示例。

- **`T-DEV-2`：本地写入守卫与零轮询监听**
  - 在本地 `PreToolUse` Hook 中启用 Worktree 物理写入守卫，拦截主 checkout 直接写代码，并给出可行动的 worktree 路径提示。
  - 沉淀 Herdr `events.subscribe` 的零轮询监听工具，明确订阅、取消订阅、超时、重连和回执落盘契约。
  - 交付：守卫规则测试、监听工具使用说明和失败时的安全停止行为。

- **`T-DEV-3`：推广 6DQ 质量契约**
  - 使用 `sync_contract.py` 将 6DQ 质量契约矩阵分批推广至核心产品与共享库仓库。
  - 每批记录基线 SHA、契约版本、变更范围、阻塞项、豁免和 `release_sync.py` 读回结果。
  - 交付：可重复的批次清单、失败重试策略和发布摘要对账证据。

- **`T-DEV-4`：落地项目《设计契约》与设计解耦工作流**
  - 在各 UI 业务仓（如 `VitalStride`、`VoxPocket`、`AIDash`、`demoUI`）落地 `docs/design-contract.md`，明确项目身份、Token／组件白名单、`populated/empty/loading/error` 四态和 `light/dark` 双外观。
  - 接入 `shared-ci` 的 `design_contract.py` 静态门禁，拦截裸 Hex／RGB 色值与缺漏章节，推行 `Explorer -> Designer -> Writer` 解耦工作流。
  - 交付：各项目契约、门禁验证证据，以及由 `Explorer` 事实回执、`Designer` handoff、`Writer` 多态截图组成的可追溯设计闭环。

### Track B：项目级与工程级双路复盘回馈

**目标**：把可定位的性能、Crash、Workflow、PR 和基建事实转成下一轮可验收 Task，而不是停留在建议或叙事。

- **`T-RETRO-A1`：Telemetry 自动发现 Performance 退化**
  - 基于 `shared-telemetry` / `aidata` 建立 PLT P90、FPS、首屏耗时与 DSAT 异常检测规则。
  - 规则须记录版本、市场／设备／客户端范围、基线水位、置信区间、最早承重分岔点和误报处理。
  - 告警自动生成带基线水位的性能优化 Task，并回流到 §1 的 Goal/Task 验收。

- **`T-RETRO-A2`：Crash 自动诊断与修复闭环**
  - 串联 Crash 堆栈聚簇、`postmortem` 根因考古、RED/GREEN 修复 Task 和发布后 Crash-Free 率读回。
  - 每个簇保留样本窗口、版本边界、根因证据、修复 head、验证命令和发布后的反事实比较。
  - 交付：从发现到关闭的状态机，以及无法自动归因时的人工升级边界。

- **`T-RETRO-B1`：Workflow / PR / 基建复盘闭环**
  - 基于 H1–H5 五步诊断法与 `stuck-PR` 信号，持续识别流程卡点、依赖漂移、证据缺口和门禁失效。
  - 经过反事实检验后，将确认项沉淀为 Skill 条款、Hook 守卫或 6DQ 门禁；未确认项保留为 backlog，不直接改冻结合同。
  - 交付：工程反馈记录、回流 Task 模板和三道物理锁的覆盖矩阵。

### Track C：总结、信息抽取与叙事成文

**目标**：从已验收的项目与工程证据中抽取可迁移机制，完成脱敏、构建、发布和反馈回流。

- **`T-STORY-1`：建立素材包抽取流水线**
  - 基于 `playbook.md` §3 的《四要素叙事素材抽取单》和三大体裁模板，建立脚本或工作流。
  - 输入覆盖项目攻关（Performance/Crash）与工程演进（Workflow/Herdr/6DQ），输出统一 provenance、Quantitative Delta、Counter-Intuitive Pivot、Takeaway Mechanism 和 Desensitization 字段。
  - 交付：可重复运行的素材包、字段校验、敏感信息扫描和来源回指。

- **`T-STORY-2`：产出首批作品并双栏读回**
  - 使用真实复盘素材产出首批新文章，覆盖项目性能／Crash 深度技术 Blog 与《带一群 AI 干活》续篇。
  - 通过双栏阅读器完成构建、静态资源哈希、去标识化检查和发布后页面读回。
  - 交付：作品版本、证据索引、读者反馈入口和回流 §2 的改进项。

### 2.1 推荐推进顺序与停止线

1. 先完成 `T-DEV-1` 的公共字段与验收矩阵，再让 Track A/B/C 各自建立首个可运行切片。
2. `T-DEV-2` 与 `T-DEV-3` 可并行，但任何门禁或 Hook 变更必须有独立验收，不得用文档阅读代替运行证据。
3. `T-RETRO-A1`、`T-RETRO-A2` 先产出最小可观测闭环，再扩展市场、设备、版本和自动派单覆盖。
4. `T-STORY-1` 只消费已确认的 §1/§2 证据；`T-STORY-2` 只发布完成脱敏与构建读回的素材。
5. 任一 Task 缺少准确 head、R10 回执、验收命令结果或来源证据时，停止宣称完成，转为 `blocked` 或 `changes_requested`。

## §3 可直接复制给 CLI 主控（TL）的启动 Prompt

```text
你是本仓库本次 Goal 的 TL。先加载 goal-task，并阅读其 playbook.md 与 workflow-refine-roadmap.md；不要派发 subagent，当前会话直接完成协调与执行。

目标：从工程全景飞轮路线图中选择一个 Track 或一个明确的 Task，形成可验收的 Goal -> Task 切片。

执行要求：
1. 先核对当前分支、基线 SHA、仓库规则和已有变更；不得覆盖未交接的工作。
2. 明确 Background、Non-goals、Dependencies、Owned Paths、Interfaces: Consumes / Produces、验收命令和 R10 回执路径。
3. 严格遵循 1 Task = 1 Writer = 1 Worktree = 1 PR；不扩大范围，不改 CI 门禁或公共契约，除非获得相应批准。
4. 先写测试或可执行验收，再实现最小切片；所有输入在边界校验，所有失败显式记录。
5. 运行 brief 中列出的全部验证命令，保存完整输出、准确 commit/head、合同版本和未完成项。
6. 完成前检查 diff --check、敏感信息、镜像一致性、文档链接和回执；只报告可核实的事实。

输出：更新后的文件清单、每条验收命令及结果、准确 head、R10 回执路径、阻塞项和下一步。不要执行 git commit 或 git push，除非当前任务另有明确授权。
```
