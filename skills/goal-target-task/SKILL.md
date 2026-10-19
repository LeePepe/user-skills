---
name: goal-target-task
description: 使用 CLI agent 规划或执行开发工作前加载，无论是否使用 subagent。
---
# Goal / Target / Task 执行合同

本合同是 CLI 主会话与 subagent 的共同来源。
目标仓指南、固定版本 shared 合同、hooks、身份及 required gates 继续有效；读取本合同不授予角色、委派或发布权限。
明确且范围清楚的实现请求不重复索取相同授权；事实问答和诊断不自动变成实施。

## 三层定义

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal | 可观察的最终结果、整体范围和不做项；包含必需的 Targets，列明涉及的文件、接口与代码层 | 外部前提及 Targets 之间的依赖 | 每项整体验收对应验证命令；必需 Targets 全部交付，端到端验收有证据 |
| Target | 属于一个 Goal 的可独立验收交付物，通常一个 PR、一个写者；列明文件、接口与代码层，包含内部 Tasks | 前置 Targets | 每项验收对应验证命令；本 Target 的验收全部有证据，PR 合法合并 |
| Task | 属于一个 Target 的执行步骤；列明指定输出、文件、接口与代码层 | 前置 Tasks 或 Targets | 指定输出齐全，指定验证命令全部通过 |

每个定义都显式记录范围、父级／代码层、依赖和验收命令；Task 的验收映射到所属 Target，Target 映射到 Goal。
按可验收结果、真实接口和依赖划分 Target，允许跨层，不按 layer 数、文件数或行数机械拆分。
一个实现 issue 对应一个 Target；Tasks 留在其内部清单，不逐 Task 建 issue、PR、换写者或启动完整审查。
小改动可在现有 issue／会话中定义一个 Target 和一个 Task，不另建规划目录或主管层。
需要计划的工作先形成可审定义，按目标仓规则完成独立计划审查和执行确认；有效受审基线直接复用，不先实施再补审。

## 合同角色

以下为常见角色绑定；定义权仍按 R1，含获宽范围 assignment 的原实现者。

| 合同角色 | 职责 |
|---|---|
| Goal 定义方 | 定义 Goal 和不做项；Goal 变更回 负责人 |
| Target 定义方 | 定义 Targets、Tasks、验收命令及修订（R1／R2／R5） |
| 协调者 | 分配、依赖、新增 Targets（R4）及决定路由 |
| Target 写者 | 每个 Target 唯一写者：实现、验证、commit、PR |
| 独立验收 | 按 V1–V3 限定审查 |
| 交付 | 推进 PR 合法合并 |

## 定义、冻结与缺口

- **R1 定义权**：拿到定义 assignment 的人或 agent 可以编制三层定义，包括协调者／CLI 主会话、Target 定义方 或获宽范围 assignment 的原实现者；不按工作流划分定义权，新增 Target 仍遵守 R4。
  保留已有决定和有效受审基线，只补真正未定的内容；Task-only 执行者不能补定义或扩权。
- **R2 交出即冻结**：定义一经交给执行方即冻结；执行方严格按获分配的 Tasks 做，不增加文件、行为或内容，只有定义方能出新 revision，独立审查只看定义改动部分。
  进度、PR/head、验证证据及下一动作记入执行台账，不修改冻结定义或另设审批。
- **R3 缺口另立**：缺口不扩大当前 Target；编译、CI 或依赖坏了，提议前置 Target，当前 Target 等它合法合并后继续；验收覆盖不全，由定义方按 R5 把缺项移到新 Target，当前 Target 仅按修订后已完成的范围交付；无关问题记 backlog，由定义方判断是否属于本 Goal；改变 Goal 回 负责人。
  执行方提供失败命令、受影响验收及建议，不自行补适配／测试、删验收或宣称原范围已完成。
- **R4 新增 Target 权**：只有协调者或负责人可以新增 Target；新 Target 不能改变 Goal，执行方只能附证据提议。
- **R5 验收迁移**：验收项在 Targets 之间移动时，由定义方重映射 Goal／Target／Task 与验收命令并出新 revision，独立审查只看改动部分。
  台账保留迁出／迁入位置、原因及未交付状态；迁移不等于 Goal 验收完成。
- **R6 subagent 委派**：Target 写者和独立验收可将自己的 Tasks 或审查分片交给 subagent，自行决定是否委派、数量及是否并行，无须另请批准。
  subagent 接收冻结 Task、所属 owned paths 子集及验证命令，只执行该 Task，缺口回报委派者。
  Target 写者仍是唯一写者；仅写者整合、commit、push、开 PR 并对验收负责；subagent 只执行交接列明的允许操作，结果交回委派者，不联系其他角色。
  并行 subagent 使用不重叠路径，或各用独立 worktree。
  委派仅一层，subagent 不再委派。
  subagent 检查计入委派者自检，不算独立审查，结论仍由委派者负责。

## 角色绑定与交接

角色来自可信调用来源和显式 assignment；独立 CLI 会话、可用工具或父级 Goal 上下文都不授予主 agent 权限。
协调者分配、监督、路由决定和汇报证据；原执行者实现、验证、修复，独立 reviewer 验收，不自签自己的产物。
采用软行为隔离，保持工具可用；helper 权限不包含同级调度权，R6 委派自己的 Tasks 不属于同级调度；未知执行结果先对账，不派第二写者。

交接携带本合同与目标仓指南、角色／来源／父级绑定、原始意图及已有决定、准确受审 revision 和授权来源、
Goal／Target／Tasks、owned paths、允许操作、唯一写者、验收命令、独立验收责任及预期回报；交给 subagent 的 Task 同样适用，不假定 skill 自动继承。
缺失或冲突沿原调用者回报，暂停依赖该缺口的动作；执行者不可用时保留角色和现场，不自行接管。

## 限定审查

- **V1 只审当前 Target**：独立审查只检查三件事：diff 是否在定义范围内、验收命令是否通过、是否违反仓库红线。
  计划审查核对相同字段与映射；首轮覆盖当前 Target 的完整候选和全部验收，不把其他 Target 纳入。
  reviewer 直接读准确 revision 的定义、diff 和命令证据，不以执行者勾选代替确认；记录验收项、位置、证据及结论。
  内容 reviewer 不重复执行或裁定 build/test/lint/hooks/CI；执行者和 CI 提供命令结果，缺必要证据不能算通过。
- **V2 按引入来源判定阻塞**：按问题是否由本次 diff 引入区分，不按文件路径区分；本次 diff 引入的任何问题（不论影响哪个文件）、范围内验收失败、红线违规，以及任何安全／隐私／数据丢失问题都阻塞；除上述阻塞项外，本次 diff 未引入的原有问题仅记 backlog，不阻塞，协调者可按 R4 将其变成新 Target；修改声明范围外的文件仍阻塞。
- **V3 复审与上限**：复审核对上一轮的问题和本次修复 diff；修复引入的问题按 V2 阻塞，未改动代码不重新扫描；同一问题两轮仍不过即交回定义方，不开第三轮，换 agent、run、SHA 或任务名不重置历史。
  复审携带原 findings、前后 revision、repair diff 和证据，逐项标记已修复／未修复并列出修复引入的回归；其余发现仍按 V2 判定阻塞或记入 backlog。
  新 SHA 需要该 SHA 的证据，但不扩大复审范围；定义改动仅审 R2／R5 的 delta。
- **V4 CI 范围**：目标仓库的 CI 门禁照常有效；修改门禁本身是独立 Target，须负责人批准。

复用已有、覆盖同一职责的独立审查，不堆叠重复全量审查；实现者现有完整 code-review 自检保留。
实现者回报 Tasks 输出及限制，独立验收确认 Target；PR URL／准确 head 的实现交接不等于合法合并。
Goal 的发布、实机或真实消费验收分别取证，不能从 Task 勾选、CI 绿或 PR merged 推断完成。

## CLI 用法

主会话担任定义方与协调者；用户是负责人。
加载本合同后按可信调用来源和显式 assignment 绑定角色，复用已有决定与有效受审基线。

小 Target：主会话自任写者，按 R6 把 Task 交给 subagent，自己整合提交。
交接列明冻结 Task、owned paths、允许操作与验证命令，结果交回主会话。

大或可并行的 Target：每个 Target 派一个写者 subagent，各用独立 worktree；主会话只协调。
各写者实现、验证、commit、push、开 PR，并回报准确 head 与命令证据。

独立验收：另开审查 subagent，不复用写者上下文，只读定义、diff 和命令证据。
审查按 V1–V3 限定范围，写者自检不替代独立验收。

嵌套（协调者实测，10-19）：Claude Code 的 subagent 不能再派 subagent，所以 Claude Code 下写者 subagent 不能再委派。
Codex CLI 0.163 在 danger-full-access 下 subagent 可再派一层，read-only sandbox 下第二层被拒（Operation not permitted）。
R6 仍只允许写者委派一层；工具能力不扩大合同授权。

台账存放在目标仓库允许的位置或 PR 描述，不改冻结定义：

| Target | 写者 | 状态 | PR/head | 验收证据 | 下一步 |
|---|---|---|---|---|---|
| 待分配 Target | 待分配写者 | 待开始 | — | — | 分配并交接 |
