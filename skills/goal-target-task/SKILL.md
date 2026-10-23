---
name: goal-target-task
description: 使用 CLI agent 规划或执行开发工作前加载，无论是否使用 subagent。
---
# Goal / Target / Task 执行合同

本合同是 CLI 主会话与 subagent 的共同来源。
目标仓指南、固定版本的共享规则、hooks、身份及 required gates 继续有效；读取本合同不授予角色、委派或发布权限。
明确且范围清楚的实现请求不重复索取相同授权；事实问答和诊断不自动变成实施。

## 三层定义与三档复杂度路由

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal | 可观察的最终结果、整体范围和不做项；包含必需的 Targets，列明涉及的文件、接口与代码层 | 外部前提及 Targets 之间的依赖 | 每项整体验收对应验证命令；必需 Targets 全部交付，端到端验收有证据 |
| Target | 属于一个 Goal 的可独立验收子目的；列明文件、接口与代码层，包含一个或多个 Task | 前置 Targets | 每项验收对应验证命令；所有 Task 完成后，整体验收全部有证据 |
| Task | 属于一个 Target，分给一个固定执行者；列明指定输出、文件、接口（`Interfaces: Consumes / Produces`）与代码层，包含一个或多个小而单一目的的 PR | 前置 Tasks 或 Targets | 指定输出齐全，指定验收命令全部通过，所含 PR 全部合法合并 |

每个定义都显式记录范围、父级／代码层、依赖和验收命令；Task 的验收映射到所属 Target，Target 映射到 Goal。
按可验收结果、真实接口和依赖划分 Target，允许跨层，不按 layer 数、文件数或行数机械拆分。
Task 是分配和验收的单位，PR 是审查和合并的单位。
依次判断：执行者不同、验收方式不同、可以并行、或中间结果要交给别人用，任一成立即拆 Task；其余只是步骤不同，由 Task 写者拆 PR，不另立 Task。
使用 issue 跟踪时，Goal 是最高父 issue，Target 是它的子 issue，Task 是 Target 的子 issue；PR 关联所属 Task。

### 三档复杂度路由（Adaptive 3-Tier Complexity Routing）

协调者在启动前按任务复杂度路由到以下三档之一，避免小改动过度流程化或中大型任务缺少接口边界：

- **Lite / Fast 档**（同时满足：仅 1 个 Task、1 个 Writer、1 个 PR、改动 ≤5 个文件、不改公共接口／红线路径／CI 门禁、不新增依赖）：
  免除独立规划目录、免除 `Target` 中间层与单独计划审查；定义方完成 R7 自检后直接派 1 个 Writer + 1 个异构 Reviewer（V1–V5）；与在途 PRs 路径不重叠时不进 R9 串行队列；PR 描述即台账与 R10 回执（记录 Task、验收命令与结果、准确 head、审查结论），验收为仓库 CI＋该 PR 的一次独立审查（V1–V5，覆盖全部验收项）；合法合并并读回后，协调者复用该审查证据确认完成，不另派 Target 验收；R2 冻结、内联裁决与 V2／V3 照常。执行中任一条件不再满足，写者停止并回报，升级至 Standard 或 Epic 档（补独立计划审查）后再继续。
- **Standard 档**（单 PR 或同仓紧密关联特性／修复／重构）：
  采用 **两层扁平结构 `Goal (或 Spec/Plan) -> Tasks`**（省去冗余的 `Target` 中间层，直接将 Goal 映射为 1 个集成 Target），按 **Vertical Tracer-Bullet Slices（垂直曳光弹切片）** 划分 Task——每个切片端到端穿过所需的接口、实现与测试，能独立编译与验证；大范围机械重构除外，采用 `Expand -> Migrate -> Contract` 分步演进。每个 Task 简报写入 `~/Development/handoff/<goal>/briefs/<task>.md`，显式声明 `Interfaces: Consumes / Produces` 精确签名、`Owned Paths` 与验收命令。
- **Epic 档**（跨仓或多独立可交付子目标）：
  启用完整 `Goal -> Target -> Task` 三层结构，维护 `~/Development/handoff/<goal>/ledger.md` 与 `briefs/<task>.md`。

需要计划的工作先形成可审定义，按目标仓规则完成独立计划审查和执行确认；有效受审基线直接复用，不先实施再补审。

## 合同角色

以下为常见角色绑定；定义权仍按 R1，含获宽范围 assignment 的原实现者。

| 合同角色 | 职责 |
|---|---|
| Goal 定义方 | 定义 Goal 和不做项；评估 Target／Goal 变更，按 R2 分级报批 |
| Target 定义方 | 定义 Tasks 与验收命令；Task 增删改按 R2 获批后编制 revision（R1–R5） |
| 协调者 | 分配、依赖、Target 变更评估（R4）与路由决定，审定计划；不兼任 Task 写者或独立验收 |
| Planner | 可由独立 agent 担任 Goal／Target 定义方，按 R1 编制定义与 revision，交协调者审定及独立计划审查 |
| 探查者（Explorer） | 只读核实代码库、API／符号、文件、命令与文档是否实际存在，附 file:line 证据回报事实（R11）；不改代码、只把报告写到 handoff，不提方案、不补定义 |
| Task 写者 | 每个 Task 唯一写者：实现、验证、commit、PR；决定 Task 内 PR 划分 |
| 独立验收 | 按 V1–V5 限定审查 |
| 交付 | 推进 PR 合法合并 |

## 定义、冻结与缺口

- **R1 定义权**：拿到定义 assignment 的人或 agent 可以编制三层定义，包括协调者／CLI 主会话、Target 定义方或获宽范围 assignment 的原实现者；不按工作流划分定义权，编制 revision 不等于决定变更，变更权限按 R2／R4／R5。
  保留已有决定和有效受审基线，只补真正未定的内容；Task-only 执行者不能补定义或扩权。
- **R2 交出即冻结与内联裁决（Rulings, Not Stalls）**：定义一经交给执行方即冻结；执行方严格按获分配的 Task 做，不擅自增加文件、行为或内容；Task 写者决定 Task 内 PR 划分，不因此取得修改冻结定义的权限。
  **协调者内联裁决（`EVT=RULING`）**：当写者在实现中发现顺带缺口（如漏列 1–2 个同目标内的辅助／适配文件、非公共接口微调）且**不触碰 4 条硬红线**（① 改变 Goal 验收、② 安全／隐私／数据丢失、③ 修改 CI 门禁／规则集、④ 破坏性操作）时，写者无需暂停等待重规划循环，回报 `NEEDS_RULING`，由协调者（TL）当场在台账记一条 `EVT=RULING | decision=... | why=... | blast_radius=...` 即可放行继续。
  **超出内联裁决边界的变更**：触碰上述 4 条硬红线或超出顺带缺口边界的 Goal／Target／Task 范围变更须落盘结构化记录（改什么、原因、证据、对 Tasks 及验收的影响、规模等级）并走重规划审批：Target 内 Task 增删改由协调者＋独立 reviewer 批准；Target 变更在负责人预授权额度内（不改变 Goal 验收、Task 增删合计不超过 1 个、不涉及红线／安全／隐私／数据）由协调者＋独立 reviewer 批准并报负责人备案（记录授权来源，同一 Target 累计计数），超额度或 Goal 变更由负责人批准（R4）。
  等待批准只阻塞受影响 Tasks，其余 ready Tasks 继续派发；待负责人决定的事项按批汇总提交。
  按等级获批后，相应定义方编制 revision；独立审查只看定义改动部分；未获 `RULING` 或未备案的范围扩张按 V2 阻塞。
  进度、PR/head、验证证据及下一动作记入执行台账，不修改冻结定义或另设审批。
- **R3 缺口分流（Rulings vs. 重规划）**：发现缺口时先按 R2 判断是否属于可由协调者内联裁决（`EVT=RULING`）的顺带缺口；若属于且不触碰 4 条硬红线，由 TL 记 `EVT=RULING` 当场放行，不暂停重规划。
  超出 `RULING` 边界或破坏性缺口不扩大当前 Target，走 R2／R3 重规划：编译、CI 或依赖坏了，提议前置 Target，当前 Target 等前置 Target 的所有 Task 完成交付且 Target 整体验收通过后继续；验收覆盖不全，由 Goal 定义方／协调者按 R5 提议把缺项移到新 Target，按 R2／R4 获批后由 Target 定义方编制 revision，当前 Target 仅按修订后已完成的范围交付；无关问题记 backlog，由 Goal 定义方／协调者判断是否属于本 Goal；改变 Goal 回负责人。
  对超出 `RULING` 边界的缺口，执行方按 R2 提交结构化变更记录，附失败命令、受影响验收及建议，不自行补适配／测试、删验收或宣称原范围已完成。
- **R4 Target 变更权**：Target 自身的范围、验收、增删改由 Goal 定义方／协调者评估并按 R2 分级批准；Target 定义方据此编制 revision；Target 变更不能改变 Goal，Goal 变更须负责人批准；执行方只能附证据提议。
- **R5 验收迁移**：验收项在 Targets 之间移动时，由 Goal 定义方／协调者评估，按 R2／R4 分级批准；Target 定义方据此重映射 Goal／Target／Task 与验收命令并出新 revision，独立审查只看改动部分。
  台账保留迁出／迁入位置、原因及未交付状态；迁移不等于 Goal 验收完成。
- **R6 1-Hop 扁平调度与磁盘文件指针交接（File-Pointer Handoff）**：
  **1-Hop Flat Tree 优先**：由顶层协调者（TL）统一派发所有 Subagent（Explorer、Planner、Writer、Reviewer）；Task 写者与 Reviewer 默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`），从根源避免 Claude Code 单层 subagent 限制、Codex 只读沙箱嵌套被拒以及 Worker 私自派 Reviewer 重复自审的问题。若特殊场景需对同一 Task 拆分执行分片，由 TL 并行派发执行分片 subagent（使用不重叠路径或独立 worktree），仍由该 Task 唯一写者整合、commit、push、开 PR 并对验收负责；subagent 检查计入自检，不算独立审查。
  **File-Pointer Handoff（防 Context Rot）**：交接不再把冗长的合同条文或历史对话粘贴进 prompt，而是由协调者将任务简报写入 `~/Development/handoff/<goal>/briefs/<task>.md`（含 1 句话背景、`Owned Paths`、`Interfaces: Consumes / Produces` 精确签名、允许操作与验收命令），prompt 仅传该文件指针路径；Subagent 读文件执行，将完整验证日志写入 R10 回执文件，向主会话只返回 `<15` 行精简结论（状态、PR/head、关键命令结果、回执路径）。
- **R7 冻结前一致性**：Target／Task 计划冻结前逐项核对：每个 Task 的验收、文件／区域（owned paths）、依赖及验证命令齐全；引用的命令、API、符号、文件已实际核实存在，不凭假设；可用对应当前基分支 SHA 的 R11 探查证据满足；冻结前清单须列出 owned paths、引用的脚本（含其调用的嵌套检查器）与验收命令，并附其存在且可运行的证据。
  Tasks 之间命令不冲突；可并行（无依赖顺序）的 Tasks 的 owned paths 不重叠；依赖图无环；定义方输出不得含可编译的实现代码；任一失败即阻止冻结。
  首轮独立计划审查须一次覆盖全部一致性项并集中给出 findings，不分轮追加本轮应发现的问题；按 R2 出新 revision 后复核受影响项。
- **R8 DAG 与状态机**：Tasks 显式声明依赖 DAG；台账允许 planned → ready → in_progress → in_review → done，或 in_review → changes_requested → in_progress（仅 V5 的 FAIL 触发修复）；多 PR Task 的非最后一个 PR 获验收后可 in_review → in_progress。
  任意状态可转 blocked／cancelled，须记原因；解除阻塞后 blocked → ready，仍须满足就绪条件；只有全部依赖 done 且 R7 通过才可 ready，只派发 ready 的 Task。
  Target／Goal 状态：planning → frozen → in_progress → accepting → done，可转 blocked／cancelled 并记原因，修订期间回 planning；阻塞解除经协调者核对原因已消除且定义无需修订时回阻塞前状态，否则回 planning；Target 在所含 Tasks 全部 done（或 cancelled 已备案）后进 accepting，按 V1 整体验收通过才 done；Goal 在必需 Targets 全部 done 后进 accepting，端到端证据齐全才 done。
  租约：in_progress／changes_requested 的写者按协调者设定的周期更新心跳（回执或台账）；超时后协调者按 git 实际状态＋R10 回执对账，确认原写者及其 R6 subagent 均已停止，记录交接（现场、head、未完成项）后改派新写者，同一时间仍只有一个写者。
- **R9 漂移与合并队列**：规划记录基分支 SHA 及 owned／依赖文件的 revision；每次派发或恢复前核对当前基分支 SHA 和这些文件；同一 DAG 内上游 Tasks 按计划合并带来的变更不算漂移，只更新记录基线。
  其他漂移先暂停派发受影响 Tasks，按 R11 由 Explorer 复核受影响引用；事实未变则更新基线后继续，事实已变才退回定义方按 R2 重规划。
  同一问题两轮审查仍失败（V3）、经复核的事实漂移、验收不可实现或依赖被取消，均将受影响 Tasks 置为 blocked 并退回定义方重规划；修订基线获审通过前不再派发受影响 Task。
  同一 Target 冻结后已修订 2 次仍需再修订时，不再自行重规划，交负责人决定缩小、拆分或取消。
  owned paths 重叠或有依赖关系的 PRs 由协调者排入串行合并队列，每次仅处理一个；写者轮到时按目标仓规则更新到最新基分支（rebase 或 merge），重跑 Task 验证，待准确 head 通过审查及仓库门禁后合法合并；失败不合并。
  与在途 PRs 路径不相交且无依赖的 PR 可直接走仓库原生 merge queue／自动合并，仍须准确 head 通过 Task 验证、审查及仓库门禁。
- **R10 双 ID 回执**：每个 Task 完成／交回、每次审查及每次 R11 探查均须落盘回执，以 Task ID＋run／agent ID 唯一定位；多轮审查保留各轮文件，记录状态、审查结论、commit SHA／head、验证命令与结果、合同 SHA、实际模型（降级注明原模型）、机器及大致 token；探查回执以 Target／计划 ID 定位，记录基分支 SHA 及逐项事实与证据。
  协调者按回执＋git 实际状态对账，不以终端／pane 输出或 agent 自述代替证据；回执放目标仓允许或协调者指定的位置，台账链接对应文件。
- **R11 只读探查**：定义方在规划前或规划中可派 Explorer，交接列明待核实问题清单（API／符号、文件、命令、文档及版本）和基分支 SHA；Explorer 不改代码：不 edit 仓库文件、不 commit／push／开 PR，不运行会改变状态的命令；唯一允许的写入是把回执／报告写到 handoff 目录。
  逐项回报：存在／不存在／与假设不符，附 file:line、命令及输出或文档 URL／版本；不提实现方案、不补定义、不扩大问题清单，新发现的疑点回报定义方。
  定义方只引用已核实的事实；基分支 SHA 变化后按 R9 复核受影响项。探查不算独立审查，计划审查仍按 V1 核对。

## 角色绑定与交接

角色来自可信调用来源和显式 assignment；独立 CLI 会话、可用工具或父级 Goal 上下文都不授予主 agent 权限。
协调者分配、监督、路由决定和汇报证据；原执行者实现、验证、修复，独立 reviewer 验收，不自签自己的产物。
协调者及 CLI 主会话不兼任 Task 写者或独立验收；只定义或审定 Planner 的定义、分配、监督、路由决定和转述证据。
实现、验证和验收结论由对应 agent 提供；协调者不推翻验收结论，有分歧按 V3 进入复审或交负责人。
采用软行为隔离，保持工具可用；helper 权限不包含同级或下级调度权（1-Hop 扁平调度，写者与 reviewer 默认不派发 subagent）；未知执行结果先对账，不派第二写者。

交接通过 `~/Development/handoff/<goal>/briefs/<task>.md` 磁盘文件指针（Lite 档可在任务描述中内联）携带本合同与目标仓指南、角色／来源／父级绑定、1 句话背景、准确受审 revision 和授权来源、
Goal／Target／Tasks、`Owned Paths`、`Interfaces: Consumes / Produces`、允许操作、唯一写者、验收命令、独立验收责任及预期回报（`<15` 行精简摘要＋R10 回执路径）；交给 subagent 的 Task 同样适用，不假定 skill 自动继承。
交接与台账记录合同路径及 SHA、定义 revision/hash，并分别标明审查基准和执行时实际加载的版本。
缺失或冲突沿原调用者回报，暂停依赖该缺口的动作；执行者不可用时保留现场，按 R8 租约超时对账后改派新写者，协调者不自行接管写入。

**任意层接手**：所有执行入口（CLI 会话或其他平台的团队）适用同一合同与合同角色，平台只决定由谁担任角色；新的协调者或执行团队可在任意时点接手同一 Goal：只有 Goal、已有 Targets，或已有 Tasks（含在途或需修改的 Task）。
接手先按 R10 回执、台账与 git 实际状态对账，确认各层 R8 状态、合同 SHA 与定义 revision 并记录交接；从当前状态继续，不重新规划已冻结的定义。
先看 Goal／Target：为 planning 时，定义方按 R1／R7 编制或补齐，按本合同完成计划审查（Lite 档按其规则以 R7 自检代替单独计划审查）后冻结；已冻结的不重新定义。
父层 Goal 或 Target 为 blocked、cancelled 或 done 时，其下 Task 一律不派发、不恢复，先按 R8 处理父层；父层为 blocked 时，其下 in_progress／changes_requested 的 Task 由协调者通知写者停止，确认原写者及其 R6 subagent 已停止后记录交接（现场、head、未完成项），Task 转 blocked。
父层不处于上述状态时，逐个按 Task 自身状态继续，不以父层状态代替：
- planned：满足 R7、全部依赖 done 并通过 R9 基线核对后转 ready；
- ready：复用有效受审基线，分配写者；
- in_progress／changes_requested：按 R8 租约对账，确认原写者及其 R6 subagent 已停止后改派，同一时间仅一个写者；
- in_review：交独立验收，不重做实现；
- blocked、cancelled、done：保持原状态，按 R8 处理，不因接手恢复、重开或重做。

父层 accepting 时交独立验收按 V1 确认整体验收；已冻结但需改动的，按 R2 落结构化变更记录并分级批准，由定义方出 revision，独立审查只看改动部分。
接手不重置 V3 两轮计数与 R9 冻结后修订计数；满足 Lite 档的小改动同样按本合同执行，不因入口不同而免去定义、冻结或 PR 独立审查。

## 限定审查

- **V1 双轴限定审查单位**：每个 PR 明确拆分为双轴核对：
  - **Axis 1: Spec & Scope Compliance（规格与范围合规）**：核对实际 diff 是否完整实现 `briefs/<task>.md`（或冻结定义）声明的验收项与 `Interfaces: Consumes / Produces`、是否漏项、是否符合该 PR 声明的单一目的、是否在所属 Task 的 `Owned Paths` 授权范围内（含已记录的 `EVT=RULING`）、是否越界。
  - **Axis 2: Code Quality & Redlines（代码质量与红线）**：核对测试真实性与有效性、代码质量与可维护性、该 PR 自身的验证证据，以及安全／隐私／数据丢失与仓库红线。
  Task 的最后一个 PR 还须核对整个 Task 的验收命令，证据覆盖此前各 PR 的累计结果。
  Target 的所有 Task 完成后，由独立验收确认 Target 全部验收项及组合结果（Standard 档集成 Target 与 Lite 档按各自规则确认）。
  计划审查核对相同字段与映射及 R7 全部一致性项；首轮覆盖当前 PR／Task／Target 的完整候选和对应验收，不把其他 Target 纳入。
  reviewer 直接读准确 revision 的定义／简报、diff 和命令证据，不以执行者勾选代替确认；按双轴记录验收项、位置、证据及结论。
  内容 reviewer 不重复执行或裁定 build/test/lint/hooks/CI；执行者和 CI 提供命令结果，缺必要证据不能算通过。
- **V2 按引入来源判定阻塞**：按问题是否由本次 diff 引入区分，不按文件路径区分；本次 diff 引入的任何问题（不论影响哪个文件）、范围内验收失败、红线违规，以及任何安全／隐私／数据丢失问题都阻塞；除上述阻塞项外，本次 diff 未引入的原有问题仅记 backlog，不阻塞，协调者可按 R4 提议新 Target；修改声明范围外的文件且未获 `EVT=RULING` 或未按 R2 备案获批的范围扩张均阻塞。
- **V3 复审与失败自动升档（Failure-Tier Escalation）**：复审核对上一轮的问题和本次修复 diff；修复引入的问题按 V2 阻塞，未改动代码不重新扫描。
  **升档破局规则**：第 1 轮 FAIL 由原 Writer 修复；若第 1 轮修复后复审仍 FAIL（即进入第 2 轮修复），协调者触发 **Failure-Tier Escalation**——改派高一档模型（`writer_escalated`，如 `gpt-6.1-sol` 或 `opus`，见 [references/runtime-profiles.yaml](references/runtime-profiles.yaml)）带新鲜上下文（原简报、前序 diff、两轮 review findings 与验证日志路径）接手第 2 轮修复；两轮修复后复审仍不过才将受影响 Task 置为 blocked 并退回定义方重规划，不开第三轮修复；换 PR、SHA、agent、run 或任务名不重置历史；Target 整体验收另计两轮。
  复审携带原 findings、前后 revision、repair diff 和证据，逐项标记已修复／未修复并列出修复引入的回归；其余发现仍按 V2 判定阻塞或记入 backlog。
  新 SHA 需要该 SHA 的证据，但不扩大复审范围；定义改动仅审 R2／R5 的 delta。
- **V4 CI 范围**：目标仓库的 CI 门禁照常有效；修改门禁本身是独立 Target，须负责人批准。
- **V5 审查结论**：PASS＝通过；CONCERNS＝仅有非阻塞项，记 backlog，不返工；WAIVED＝阻塞项豁免至少须协调者＋提出该项的独立 reviewer 同意，涉及验收项、红线或安全／隐私／数据丢失的豁免还须负责人批准，记录原因及批准人，仓库 required gates 仍须通过；豁免不能替代 R2／R4 的分级批准，未获批的范围扩张只能按 R2 补批，不可 WAIVED；FAIL＝存在未获豁免的 V2 阻塞项，仅此结论触发修复，仍受 V3／R9 重规划约束；每次结论按 R10 落盘。

复用已有、覆盖同一职责的独立审查，不堆叠重复全量审查；实现者现有完整 code-review 自检保留。
实现者回报 Task 输出及限制，独立验收按 V1 分别确认 PR、Task 与 Target；PR URL／准确 head 的实现交接不等于合法合并。
Goal 的发布、实机或真实消费验收分别取证，不能从 Task 勾选、CI 绿或 PR merged 推断完成。

## CLI 用法

全景开发、复盘、反思与故事集飞轮见 [references/playbook.md](references/playbook.md)。

主会话（TL）担任协调者：审定计划、分配与协调；定义方可由独立 Planner agent 担任，主会话也可兼任；用户是负责人。
本合同不绑定具体模型；角色档位、模型、CLI 与启动参数只以 [references/runtime-profiles.yaml](references/runtime-profiles.yaml) 为源头，bot 分工与 CLI agent 生命周期见 [references/bots-and-agents.md](references/bots-and-agents.md)，台账与标准事件见 [references/ledger.md](references/ledger.md)。
硬规则：Reviewer（含 Planner 的 Reviewer）与被审者使用不同家模型；启动显式指定模型，不依赖 CLI 默认值；降级只在同档内换可用模型并记入回执；不用 profile 列出的禁用参数（如 `--yolo`、`--allow-all-*`）；Codex 沙箱按 profile 使用 `danger-full-access`（`-s danger-full-access`），不视为禁用项。
所有 CLI agent（含 Reviewer、Explorer）经 herdr 启动并按 profile 命名，TL 在下属仍 working 时不得结束；每个 Goal 在台账头写明 Owner、协调者 bot、TL 与机器，事件只沿这条线传递；危险操作及明确 hold 例外只由 Owner 放行；普通 PR 合并与发布（04/tag/TestFlight）不需要 Owner GO，按仓库既定 auto-merge 条件合并（见 references/bots-and-agents.md）。
在 `HERDR_ENV=1` 且显式 ID 预检通过后，使用 `herdr pane split --pane "$HERDR_PANE_ID" --cwd "$TASK_WORKTREE" --no-focus` 创建绑定 worktree 的窗格，再按 `common.herdr_kind_map` 用 `herdr agent start <name> --kind <kind> --pane <pane_id> -- <profile_flags>` 启动跨厂商 Subagent；用 `herdr agent prompt <name> "<file-pointer>" --wait` 派发，完成后用 `herdr agent read <name> --source recent-unwrapped` 读取。禁止裸 `--current`，禁止在 `working` 时读 alternate-screen 历史；以 R10 回执和 Git 实际状态为唯一真值。完整预检、命名、等待、阻塞处理与清理模板见 [references/bots-and-agents.md](references/bots-and-agents.md)。
加载本合同后按可信调用来源和显式 assignment 绑定角色，复用已有决定与有效受审基线。

规划前或规划中按需（R11）派只读 Explorer（快速／长上下文只读模型即可），回报落盘后由定义方写入计划。

每个 Task 由主会话（TL）按 1-Hop 扁平调度派一个写者 subagent；写者通过读取 `~/Development/handoff/<goal>/briefs/<task>.md`（含 1 句话背景、`Owned Paths`、`Interfaces: Consumes / Produces`、验收命令）执行，完整日志写入回执文件，向主会话只返回 `<15` 行精简结论。

同一 Target 的多个 Task 可并行，每个 Task 由主会话派一个写者 subagent，owned paths 不重叠（R7）并各用独立 worktree；有依赖的 Tasks 按顺序，主会话只协调。
写者遇到非红线顺带缺口时回报 `NEEDS_RULING`，由主会话在台账记录 `EVT=RULING` 内联放行；各写者实现、验证、commit、push、开 PR，并回报准确 head 与回执路径。

独立验收：每个 PR 由主会话另派一个未接触写者上下文且与写者不同家（cross-vendor）的 Reviewer agent，只读简报／定义、diff 和命令证据，按 V1 双轴（Axis 1: Spec & Scope Compliance + Axis 2: Code Quality & Redlines）审查；Task 最后一个 PR 核对累计验收。
第 1 轮 FAIL 由原写者修复；若第 1 轮修复后复审仍 FAIL，主会话按 V3 触发 Failure-Tier Escalation，改派 `writer_escalated`（高一档模型，带新鲜上下文）执行第 2 轮修复。
所有 Task 完成后，由独立验收确认 Target 全部验收项及组合结果。
审查按 V1–V5 限定范围，写者自检不替代独立验收。
主会话收集并转述写者证据与验收结论，在执行台账记录对应 agent 和会话 ID。

交付：写者按目标仓流程及 R9 合并规则推进 PR 合法合并，仓库支持时启用自动合并，等待并修复 required checks，持续跟进直到真实读回 PR 已合并；卡住时按目标仓的监督机制处理。
主会话汇总台账，Goal 级验收另行取证。

1-Hop 扁平委派与沙箱约束（2026-10-19 / 2026-10-23）：Claude Code 的 subagent 不能再委派；Codex CLI 0.163 在 read-only sandbox 下第二层被拒（Operation not permitted）。
R6 默认采用 1-Hop Flat Tree：所有 Subagent 由主会话（TL）直接派发，Task 写者与 Reviewer 默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`）；若特殊场景需拆分同一 Task 的执行分片，由主会话并行派发、该 Task 唯一写者整合；工具能力不扩大合同授权。

台账为每个 Goal 一本只追加的 `~/Development/handoff/<goal>/ledger.md`（格式与事件见 [references/ledger.md](references/ledger.md)），goal.md 与看板只是派生视图；Lite 档仍以 PR 描述为台账；台账不改冻结定义；仅补进度或验证回执时，优先使用目标仓库已有、获准且不会重复触发 CI 的载体（台账或获准的评论），不为补回执编辑会触发 CI 的 PR 描述。
实际行为、范围、测试说明和 PR 模板仍按仓库规则更新；回执保留准确 head、角色归属、结论和未完成项，评论仍须原有发布授权。
状态列遵循 R8；验收证据另行区分内容批准、执行证据待齐、自动化验收通过、PR 已交接、合法合并并链接 R10 回执；done 须符合 Task 完成定义，发布与实机验收另行取证：

| Target | Task | 依赖 | 写者 | 状态 | PR/head | 验收证据／回执 | 下一步 |
|---|---|---|---|---|---|---|---|
| 待分配 Target | 待分配 Task | — | 待分配写者 | planned | — | — | 通过 R7 并满足依赖后转 ready，再分配交接 |
