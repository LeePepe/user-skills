# 顶层 Workflow Hub 学习沉淀与子工作流（Sub-Workflow）演进总纲

- **更新日期**：2026-11-05
- **定位**：仅负责**最顶层 `workflow-hub` 的全局认知整理、整体可扩展架构设计与跨子工作流（Sub-Workflow）接口定义**；已通过 6 个独立 Herdr Space（`w2P`–`w2V`）并行完成 **Sub-Workflow 0–5 的 Phase 0 深度调研**，全部产出带精确 `file:line` 证据的 `spec.md`。
- **范围边界**：
  - 聚焦个人整体 Workflow（网关底座、工程门禁、编排内核、看板与复盘交付）；
  - **常驻 Bot（七域分工、M2 个人助理层）统一归入 Backlog**，不阻塞当前网关与架构主线；
  - **三套 Skills 仓库（`~/Development/user-skills`、`~/Development/agent-skills`、`~/Development/Skills`）保持分离**，通过四端目录映射与快照工具协同；
  - 公司业务项（Sapphire DSAT）独立归入 [TIA-84](https://linear.app/tianpeili/issue/TIA-84/todo-sapphire-dsat-用户反馈接入-jev-system-one-海量初筛归因与-sde-级联提取)，不在本总纲范围内。
- **关联中枢**：[`SKILL.md`](../SKILL.md)、[`seven-rings-and-progressive-6dq.md`](seven-rings-and-progressive-6dq.md)、Linear [TIA-85](https://linear.app/tianpeili/issue/TIA-85/todo-个人-workflow-hub-演进候选池接入-jev-第-12-类-与借鉴-dsh-magpie-hisfm-r01-r20)

---

## 1. 全量一手资料地图（播客 + 3 篇博客 + 16 个提及项目 + `nocoo` 全家桶）

### 1.1 播客与 3 篇博客的底层架构哲学
| 资料来源 | 核心命题 | 提炼出的顶层 Workflow 设计准则 |
|---|---|---|
| **播客**：[把 AI 当团队来带：主理人的 agent 管理学（his.fm EP.01）](https://his.fm/episodes/conversations/agent-team-like-a-company) | M3/M2/M1/IC 分层；模型分 `steering`/`reviewing`/`coding` 三组；预算倒置（写码求快省、审查花重金）；三层省 Token 漏斗；双触发器状态机；执行层锁死、判断层留白。 | 1. **模型与编排解耦**：子工作流与脚本不硬编码具体模型型号或配额逻辑，统一走本机网关逻辑分组。<br>2. **确定性脚本 + 毫秒级判断 + 重型 CLI 三层漏斗**：能在 Layer 0（脚本）和 Layer 1（Jev）拦截的绝不唤醒 Layer 2（CLI）。 |
| **随笔 1**：[当 Harness 变薄，人类还剩下什么？（2026-09-16）](https://lizheng.blog/2026/09/when-the-harness-gets-thinner) | 模型变强后，“精装房”式的冗长 SOP 会占用上下文并制造规则冲突；Harness 应退回“毛坯房”，只留 **7 层承重墙**，人的核心职责从“写代码”变成**“删代码 / 删过时 SOP”**。 | **Thin Harness 七层承重墙**：① 可查询状态（SQLite/Ledger）、② 工具与环境、③ 权限与隔离（Worktree/Scoped Token）、④ 验证与真实墙反馈（6DQ）、⑤ 私有事实与业务约束、⑥ 可观测性与审计、⑦ 目标与评价。删掉手把手教模型怎么走的冗余文本。 |
| **随笔 2**：[执行已经很快了，信任还是老速度（2026-10-15）](https://lizheng.blog/2026/10/execution-fast-trust-slow) | 执行时钟以小时计，信任时钟以月年计；一步步点权限确认会同时拖垮人和模型；敢常开自动执行的前提是先把**“它搞砸了也没关系”**的机制做实。 | **容错四件套**：环境可随时丢弃（独立 Worktree）、密钥绝不进工作区（网关与 `~/.config` `0600` 代持）、改动可一键回滚、结果可事后按回执独立核对。 |
| **随笔 3**：[对 AI 的信任，是一念之差（2026-11-02）](https://lizheng.blog/2026/11/trust-ai-at-your-own-pace) | 不做拒绝派也不做降临派；用 **Trust, but verify** 机制接住产出；AI 时代执行不再稀缺，顺序反过来：**“先让事情发生，再说不”**。 | 1. **Observe-First 渐进放权**：新巡检/新自动化先跑 `observe`（记录 `would accept`），验证后再切 `auto`。<br>2. **The Human's Two Moves**：人类只做 Move 1（定方向/拍板 3–5 个关键 Checkpoint）和 Move 2（审最终成果/说“不”删东西）。 |

### 1.2 播客提及项目与 `github.com/nocoo` 全家桶全景索引
| 分类 | 项目 / 仓库 | 一句话定位与核心机制 | 对顶层 Workflow 的取舍结论 |
|---|---|---|---|
| **网关与快思考层** | [`nocoo/raven`](https://github.com/nocoo/raven) (`:7024`) | 本机/VPS Copilot 与自定义上游转换代理；**`v3.2.0` 已原生支持 `POST /v1/systemone` (`docs/30-systemone.md`)、Key-Bound Routing (`docs/28`) 与 GHE Copilot (`b5a55cf`)**。 | **核心底座**：升级本机 `~/Development/raven` 至 `v3.2.0`，提供底层协议转换与原生 `/v1/systemone` 能力。 |
| | [`yetone/magpie`](https://github.com/yetone/magpie) / [`nocoo/magpie`](https://github.com/nocoo/magpie) (`:3425`) | 上层本机模型网关：对外暴露 `group/steering`、`group/reviewing`、`group/coding`，负责意图路由、会话缓存粘性（Affinity）、跨节点配额调度与模型名回写（`replyNamingOf`）。 | **核心底座**：架在 `Raven (:7024)` 之上，收拢全工作流模型路由与配额管理。 |
| | [`nocoo/falcon`](https://github.com/nocoo/falcon) (`:19823`) + [`typesafe-ai/skills`](https://github.com/typesafe-ai/skills) | 本机 Jev (`POST /v1/systemone`) HTTP/MCP 可观测代理：按 Agent 签发 `FALCON_SOURCE_KEY`，SQLite 留存 7 天请求与概率分布，支持 `⌘Return` 审阅与加星沉淀回归样本。 | **核心底座**：作为全工作流 Layer 1（毫秒级判断层）统一入口，上游对接本机 `Raven :7024/v1/systemone`。 |
| **编排与状态机内核** | [`nocoo/pi-superagent`](https://github.com/nocoo/pi-superagent) & [`dsh-superagent`](https://github.com/deepseek-ai/dsh-superagent-prototype) | 注意力经济学（`status` 第一屏置顶 `needs a person`）、`Observe -> Auto` 预演、不可变 `Mandate`（`spec_sha` 锁）、Subscription Fan-out + `handback`、单写者 SQLite + `Effects` 队列。 | **全面吸收内核机制**：直接注入 `goal-task`（Sub-Workflow 3）与 `linear-board`（Sub-Workflow 1）。 |
| | [`multica-ai/multica`](https://github.com/multica-ai/multica) | 人机同看板协作平台（Web/Desktop + Go Backend + Postgres + 本机 `multica daemon` 驱动 26 种 CLI），支持 **Live Steering（运行中实时注入指令）**、**Inbox-Only 通知**、**Execution Log + 单次 Run 成本归因**。 | **与 `nocoo` 演进路径一致（去壳留核）**：`nocoo` 曾在 2026.04–09.15 用 Multica 跑了近 6000 单（`STU-xxxx` / `SDE-01..04`），随后同样弃用其重型 Postgres/Squad/Issue 业务壳，仅抽取其零依赖 Go `server/pkg/agent/` SDK 放入 `meowth`，转向 `Herdr + pi-superagent`（详见 1.3 节）。 |
| | [`nocoo/goalx`](https://github.com/nocoo/goalx) | 本地持久自治框架：Master 拆解 typed obligations、独立 worktree 经显式 `keep` 边界合回、支持 **`--intent evolve` 预算内持续自主演进**。 | **吸收特性**：在 `goal-task` 中引入 `--intent evolve` 预算内持续打磨循环与 `keep/discard` 实验分支机制。 |
| | [`nocoo/arena`](https://github.com/nocoo/arena) | 本机 `~/.arena/arena.db` 多 Agent 方案辩论看板 + 人类 Checkpoint CLI（`arena push / status / pop`）。 | **吸收进 Phase 0（环 2）**：将多方案比选与人类拍板标准化为 `Proposal -> Checkpoint -> Pop` 协议。 |
| | [`badlogic/pi-mono`](https://github.com/badlogic/pi-mono) | 极简高缓存命中率 Coding Harness（省去内置 plan/subagent，交由外层控制）。 | **借鉴缓存原则**：不替换现有 `claude`/`codex`/`copilot`，但借鉴其固定短前缀以提升 Prompt Cache 命中率。 |
| | [`nocoo/meowth`](https://github.com/nocoo/meowth) (`:7040`) | 将 5 种 CLI 封装为本机统一 HTTP/NDJSON 接口的 Go 守护进程。 | **暂不引入**：当前已有 `herdr` 管理终端分屏，仅作纯无头 API 调用备选参考。 |
| **架构门禁与契约** | [`nocoo/base-ci`](https://github.com/nocoo/base-ci) | 6DQ 可复用 GitHub Actions（强制 40 位 SHA 固定 + `release-source` 制品溯源校验）。 | **与 `shared-ci` 同构**：对比补强 `shared-ci` 的制品溯源与跨仓门禁（Sub-Workflow 4）。 |
| | [`nocoo/his.fm`](https://github.com/nocoo/his.fm) | 零内置 AI 的 7 态乐观锁（`version`）内容状态机 + **`Global > Category Slot > Intent > Sources` 四级提示词槽位**。 | **吸收进环 2 / 环 7**：用于 `spec.md`/`design.md` 乐观锁版本控制与设计/叙事四级 Prompt Slots。 |
| | [`nocoo/steed`](https://github.com/nocoo/steed) | 按供应商分层、按物理位置划定信任域（Trust Boundary）的个人系统拓扑平台（带类密钥属性值拦截）。 | **借鉴拓扑模型**：在 `workflow-hub` 维护机器可读的本机/跨机网关与服务拓扑定义。 |
| **可观测、配置与交付** | [`nocoo/otter`](https://github.com/nocoo/otter) | macOS 多配置源目录（Git 仓、本地目录、4 端 CLI Skills 软链、LaunchAgents）不可变快照、`snapshot diff` 与恢复导出。 | **高契合直接可用**：解决本机 `user-skills` / `agent-skills` / `Skills` 三仓分离与四端 CLI 目录的快照比对与防漂移。 |
| | [`nocoo/eagle`](https://github.com/nocoo/eagle) | 跨机 Herdr Space 态势看板、端口监听（`watchPorts: 7024`）、真实证据校验（`idle/done` 不算完成）与 **23:59 `eagle-daily-zh-v2` 24 小时逐时强制引用日报**。 | **吸收进环 3/7**：将 `eagle-daily-zh-v2` 逐时证据引用 Schema 吸收进 `retro-loop` 与遥测日报。 |
| | [`nocoo/clip`](https://github.com/nocoo/clip) & [`base-cli`](https://github.com/nocoo/base-cli) | 从 `clip.yaml` 一键生成带 `~/.clip/<alias>/credentials.json` (`0600`) 凭据隔离的 Bun CLI。 | **工具化范式**：新增内部 API 数据源时统一生成 JSON CLI，践行“CLI 能拿到数据，Agent 就能拿到”。 |
| | [`nocoo/showtime`](https://github.com/nocoo/showtime) | 原生 macOS (Swift 6 + WebKit) 演示浏览器，支持 Agent CLI/MCP 编排镜头/光标/透明图层并录制 4K 60fps MP4。 | **环 6/7 交付利器**：用于产品发布与 `story-craft` 自动录制可重复的产品演示视频。 |
| | [`nocoo/pew`](https://github.com/nocoo/pew) / [`codo`](https://github.com/nocoo/codo) / [`mico`](https://github.com/nocoo/mico) / [`kite`](https://github.com/nocoo/kite) / [`pika`](https://github.com/nocoo/pika) / [`owl`](https://github.com/nocoo/owl) / [`octo`](https://github.com/nocoo/octo) | `pew`（9 CLI Token 热力图）、`codo`（macOS 横幅通知 + AI Guardian 降噪）、`mico`（接 `Magpie :3425` 的人机共编画布 + `WAIT` 协议）、`kite`/`pika`（轨迹/会话归档）、`owl`（系统日志异常检测）、`octo`（API/MCP 记忆库）。 | **按需选配**：`pew` 查多 CLI 账单；`codo` 做桌面 `needs a person` 提醒；借鉴 `mico` 的 `WAIT` 空增量抑制协议。 |
| **Backlog（延后）** | `Grok Bot` / [`hermes-agent`](https://github.com/NousResearch/hermes-agent) / [`openclaw`](https://github.com/openclaw/openclaw) / [`cindy`](https://github.com/makecindy/cindy) | M2 常驻个人助理与跨设备伴侣层。 | **归入 Backlog**：与现有七域常驻 Bot 改造一并延后，先完成底层网关与核心工作流升级。 |

### 1.3 一手考古还原：`nocoo` 自己是怎么用 `Multica`、最后又为什么弃壳留核的？
通过检索 `github.com/nocoo` 全量 Commit、PR、Issue 与架构文档，可以完整还原他使用 `Multica` 的真实演进轨迹（与你本机 `multica-cleanup` 下线 NAS Multica 转向 `Linear + Herdr + goal-task` 的路径惊人一致）：
1. **重度使用期（2026-04 至 2026-09-15，累计近 6,000 单 `STU-xxxx`）**：
   - **工号化 Agent 编队**：他在自建 Multica（Workspace 前缀 `STU`）里配置了 4 个固定工号的 Agent 员工：`SDE-01 <sde-01@multica.local>`、`SDE-02 <sde-02@multica.ai>`、`SDE-03 <sde-03@multica.ai>`、`SDE-04 <sde-04@multica.ai>`。
   - **`[🥝 choko]` 巡检 → GitHub Issue → Multica `STU-xxxx` 自动消单流水线**：用自研巡检脚本 `🥝 choko` 扫描名下 20+ 个仓库（`raven`、`bat`、`otter`、`ellie`、`surety`、`zhe`、`frogie` 等），以幂等标题 `[🥝 choko][deps] <pkg> <cur> → <target>` 自动开 GitHub Issue；Multica 接单生成 `STU-3400` ~ `STU-5962`，由本机 Daemon 在 `~/multica_workspaces/<workspace-id>/<task-id>/codex-home/` 隔离目录下拉起 Codex/Claude 跑完 `pre-push` 6DQ 门禁并提 PR。
   - **用量采集打通（`nocoo/pew` commit `6793c22`）**：专门在 `pew` 中写了 `discoverMulticaCodexDirs()`，扫描 `~/multica_workspaces/*/*/codex-home/sessions/` 把 Multica 派生的会话并入个人 Token 账本。
2. **断舍离拐点（2026-09-15 停用重型 Multica，次日写下《当 Harness 变薄》）**：
   - 2026-09-15（`STU-5962`）之后，`nocoo` 所有仓库彻底停止出现 `STU-xxxx` 与 `SDE-01..04@multica` 提交。
   - **他在源码与播客中写明的弃用原因**：
     - 在 `nocoo/meowth/docs/01-project-overview.md` §3「非目标」中明确写下：**“❌ 不做 issue / squad / autopilot / workspace 等产品层（multica 的业务侧不继承）；❌ 零云依赖，不要求外部 Postgres / Redis”**；
     - 在 `pi-superagent/docs/000-philosophy.md` 与播客 §2 中反思：**“Do not use it to produce volume. Mass pull requests, broadcast messages and busywork cost the team more than they give.”**（多 Agent 像公司一样互相派单/群聊极易制造 busywork 噪音，耗尽人类注意力）。
3. **“去壳留核”的最终遗产**：
   - **抽走底层进程桥接 SDK（`nocoo/meowth/docs/architecture/01-agent-sdk-pump-from-multica.md`）**：他把 `multica-ai/multica` 仓库中**零第三方依赖、纯 Go 标准库实现**的 `server/pkg/agent/`（共 61 个文件，封装了 `claude`/`codex`/`copilot`/`hermes`/`pi` 的 `Backend.Execute` 管道、进程组回收与 `stderr_tail`）原样 vendor 进 `meowth/daemon/pkg/agent/`，扔掉了整个 Web/Postgres/Issue 壳；
   - **顶层全面转向 `Herdr + Eagle + pi-superagent`**：终端用 `herdr` 分屏管会话，跨机用 `eagle` 看态势与 23:59 日报，调度层用 `pi-superagent`（单写者 SQLite + `needs a person` 极简注意力视图）。

---

## 2. 顶层 `workflow-hub` 整体可扩展架构蓝图（三大全局机制）

在进入各个 Sub-Workflow 之前，顶层 `workflow-hub` 确立以下三条跨环全局架构契约，所有子工作流按此接口对齐：

### 2.1 全局模型与决策网关拓扑（`Magpie :3425` + `Raven v3.2 :7024` + `Falcon :19823`）
```text
[各 Sub-Workflow: goal-task / shared-ci / deep-research / my-designer / Clockwork]
        │
        ├── 1. 常规生成与编码推理 (OpenAI / Anthropic 兼容协议)
        │      只传逻辑分组名：group/steering | group/reviewing | group/coding
        ▼
[上层网关: Magpie (127.0.0.1:3425)]
        │  • 逻辑分组映射 + 首句意图路由 (Intent Routing) + 会话缓存粘性 (Affinity)
        │  • 跨订阅/跨节点配额水位调度 (替代手写 Claude 周配额脚本) + replyNamingOf 回写
        ▼
[底层节点: Raven v3.2.0 (127.0.0.1:7024)]  (+ Claude/Codex 原生订阅 & 其他 Provider)
        │  • Copilot / GHE OAuth 令牌管理与 /responses 自动 Shim
        │  • 原生暴露 POST /v1/systemone (microsoft-decision-1)
        ▲
        └── 2. 毫秒级 System One 判断 (70-500ms Choice / Noul / Score / Box)
               ▲
[旁路可观测代理: Falcon (127.0.0.1:19823)]
        • 为各 Sub-Workflow 签发独立 FALCON_SOURCE_KEY，提供 /v1/systemone 与 MCP jev_decide
        • 本地 SQLite 留存 7 天决策分布，支持加星沉淀回归评测集
```

### 2.2 全工作流三层执行漏斗（Three-Tier Execution Funnel）
任何环路的自动化触发（Clockwork 定时任务、PR 监控、状态巡检、上下文管理）统一遵循：
1. **Layer 0 — 确定性脚本（0 Token，`<50ms`）**：纯 Python/Shell 检查 Git SHA、状态水位线、文件锁与 JSON Schema；`pending == 0` 直接退出。
2. **Layer 1 — Jev System One 快思考（经 `Falcon :19823 -> Raven :7024/v1/systemone`，`150ms`）**：对非空事件并发提问（`Choice` + 伴生 `Noul` 或 `Score`），过滤无需改代码的评论/噪音、预筛 1–2 个所需 Skill、核查引用真实性；代码用确定性真值表（Truth Table）承接概率输出。
3. **Layer 2 — 重型 CLI 执行器（经 `Magpie :3425` 路由 `group/steering | reviewing | coding`）**：仅在明确需要规划、写代码或双轴审查时拉起 Herdr CLI 会话。

### 2.3 Thin Harness 边界法则（「执行层锁死，判断层留白」）
- **执行层锁死（下沉到脚本与 CI）**：Worktree 隔离、`Owned Paths` 冲突检测、`Mandate` (`spec_sha`) 校验、分层依赖审计、快照物理像素尺寸、禁止裸色值、引用存在性——全部由 Python/TS 测试脚本硬性拦截，不在 `SKILL.md` 里写冗长说教。
- **判断层留白（退回毛坯房）**：删掉通用 `SKILL.md` 中代替模型思考的步骤清单和特定 App 遗留的死板像素参数；采用 `Global Redlines > Category Slot > Task Intent > Sources` 四级槽位，给架构权衡、UI 视觉性格与文章叙事留出创造空间。
- **Design Studio 参考层**：`design-studio` 的 `IP-*` 交互联动、`MP-*` 动效与 `T-*` 工具目录属于 Designer 的可检索灵感层；Writer 只执行冻结在 `design.md` 中的选择与行为，不把参考目录误当成僵化模板。

---

## 3. 按 Sub-Workflow 拆分的改进清单、跨模块适配接口与专项启动 Prompt

> **Phase 0 调研完成状态（2026-11-05）**：6 个子工作流均已在各自 Herdr Space 完成 Phase 0 只读深度调研并落盘 `spec.md`，可在对应 Herdr Space 直接审阅拍板进入 Phase 1：
> - **SW0（网关三件套）**：Space `w2P` (`sw0-gateway-goal-tl-r1`) → `~/Development/handoff/sw0-gateway-research/spec.md` (68 KB)
> - **SW1（`linear-board`）**：Space `w2Q` (`sw1-linear-goal-tl-r1`) → `~/Development/handoff/sw1-linear-research/spec.md` (46 KB)
> - **SW2（Phase 0 共创与调研）**：Space `w2R` (`sw2-codesign-goal-tl-r1`) → `~/Development/handoff/sw2-codesign-research/spec.md` (91 KB)
> - **SW3（`goal-task` 编排内核）**：Space `w2S` (`sw3-goaltask-goal-tl-r1`) → `~/Development/handoff/sw3-goaltask-research/spec.md` (87 KB)
> - **SW4（`shared-ci` 与 PR 审查）**：Space `w2T` (`sw4-sharedci-goal-tl-r1`) → `~/Development/handoff/sw4-sharedci-research/spec.md` (53 KB)
> - **SW5（`AIDash` 统一展示与环 6/7）**：Space `w2V` (`sw5-aidash-goal-tl-r1`) → `~/Development/handoff/sw5-aidash-research/spec.md` (78 KB)

---

### Sub-Workflow 0：Agent 网关与 System One 基础设施（`Raven` + `Magpie` + `Falcon`）
- **涉及路径**：`~/Development/raven`、`~/.codex/config.toml`、`~/.claude/settings.json`、本机 `Magpie (:3425)` 与 `Falcon (:19823)` 配置
- **关联编号**：`R10`、`R17`
- **基本信息与现状差距**：
  - 本机 `~/Development/raven` 正在 `:7024` 运行，但停留在旧提交，落后 `origin/main`（`v3.2.0`）86 个 commits；
  - `origin/main` 已包含 `cbf5ad3`（原生 `POST /v1/systemone` 代理 `microsoft-decision-1`，见 `docs/30-systemone.md`）、`docs/28-key-bound-routing.md`（按 Key 绑定路由规则与配额池）与 `b5a55cf`（GHE Copilot 支持）；
  - 本机尚未配置上层 `Magpie (:3425)` 的三组逻辑路由（`group/steering`、`group/reviewing`、`group/coding`）及旁路 `Falcon (:19823)`。
- **向上游/下游 Sub-Workflow 暴露的适配接口**：
  1. **给 `goal-task`（Sub-Workflow 3）与 `shared-ci`（Sub-Workflow 4）的推理接口**：统一在 `http://127.0.0.1:3425/v1` 暴露 `group/steering`（原 `strong`）、`group/reviewing`（原跨厂商 `mid` reviewer）、`group/coding`（原 `worker` writer/explorer），利用 Magpie 的 `replyNamingOf` 与配额调度屏蔽底层模型切换。
  2. **给全工作流脚本的 System One 接口**：统一在 `http://127.0.0.1:19823/v1/systemone`（Falcon，上游转发 `127.0.0.1:7024/v1/systemone`）暴露 `microsoft-decision-1` 的 `Choice`/`Noul`/`Score`/`Box` 能力，并提供标准库 Python 客户端契约 `jev_decide(state, questions, source_key)`。
- **📋 专项会话启动 Prompt（复制到新会话执行，调研先行）**：
  ```text
  加载 /workflow-hub、/deep-research 与 /goal-task skill，先阅读 ~/Development/user-skills/skills/workflow-hub/references/his-fm-nocoo-study-and-roadmap.md 中的「2.1 全局模型与决策网关拓扑」与「Sub-Workflow 0：Agent 网关与 System One 基础设施」。

  当前处于 Phase 0 只读深度调研阶段（严禁直接重启 :7024 服务、严禁改动任何配置或泄露密钥）。请先把以下 4 块事实与一手源码调研清楚，给出带精确 file:line 与配置字段的调研报告和落地设计方案：

  1. 【底层节点 Raven (:7024) 深度摸底】
     - 检查本机 ~/Development/raven 当前运行进程（PID/端口 7024/Caddy 映射/LaunchAgent 或启动方式）、本地分支及落后 origin/main (v3.2.0) 的 86 个 commits；
     - 精读 origin/main 的 docs/28-key-bound-routing.md、docs/30-systemone.md、docs/26-agent-operations.md 及 b5a55cf (GHE Copilot 支持)、afeb3b1 (model capabilities)，梳理：
       a) 升级到 v3.2.0 时的 SQLite 迁移要求（keys 绑定默认 Copilot rule、PRAGMA foreign_keys 等）与零停机验证方式；
       b) Raven 原生 POST /v1/systemone (microsoft-decision-1) 的请求/响应格式、鉴权与配额结算机制；
       c) Raven 自身的 Key-Bound Routing + 时段 + 配额池能力边界。

  2. 【上层网关 Magpie (:3425) 一手源码与能力调研】
     - 通过 GitHub API 深入调研 yetone/magpie 与 nocoo/magpie（及 usemagpie.ai 文档、nocoo/mico 等消费仓对 127.0.0.1:3425/v1 的调用方式）：
       a) Magpie 的安装/运行形态（Mac App vs CLI/Daemon）、配置文件结构与端口 3425 协议；
       b) 如何将本机 Raven (:7024)、云端 Raven 节点、Claude/Codex 原生订阅等挂载为 Magpie 上游；
       c) 如何定义 `group/steering`、`group/reviewing`、`group/coding` 三个逻辑模型组，以及 Intent Routing（首句意图分类设 effort）、Session Affinity（保 Prompt Cache 与隔离 Subagent）、配额调度（smart/pace/order/fast）与 `replyNamingOf`（伪装回客户端请求模型名）的确切配置项；
       d) 对比厘清「Magpie (:3425) 上层路由」与「Raven v3.2 Key-Bound Routing (:7024) 底层路由」的职责切分，避免双层重复配置。

  3. 【旁路快思考代理 Falcon (:19823) 与 Jev 集成调研】
     - 通过 GitHub API 深入调研 nocoo/falcon（AGENTS.md、docs/01-product.md、docs/03-protocol.md、docs/09-relay.md）与 typesafe-ai/skills：
       a) Falcon 的安装构建方式、SQLite 7 天留存机制、FALCON_SOURCE_KEY 签发管理；
       b) Falcon 如何将本机 Raven (127.0.0.1:7024/v1/systemone) 配置为上游 Relay；
       c) HTTP `/v1/systemone` 与 MCP `/mcp` (`jev_decide`) 的精确 Schema（Choice / Noul / Score / Box）。

  4. 【对下游 Sub-Workflow 的适配契约盘点】
     - 盘点本机各消费方当前是怎么调模型的，以及切到 Magpie + Raven + Falcon 后分别要改哪些字段：
       a) 本机 CLI 配置：~/.codex/config.toml (当前 base_url = http://127.0.0.1:7024/v1)、~/.claude/settings.json、~/.copilot/config.json；
       b) Sub-Workflow 3 (goal-task)：~/Development/user-skills/skills/goal-task/references/runtime-profiles.yaml 与 scripts/herdr-dispatch.sh 如何从硬编码模型名 + Claude 周配额脚本平滑切换为网关逻辑组；
       c) Sub-Workflow 4 (shared-ci)：~/Development/shared-ci/scripts/review/{codex-review.sh,kimi-review.sh} 如何对接网关。

  请先完成上述只读调研，输出一份结构清晰的《Agent 网关三件套（Magpie + Raven + Falcon）调研报告与适配契约草案》，等我确认后再进入实施。
  ```

---

### Sub-Workflow 1（环 1）：`linear-board` 看板、主动巡检预演与单次运行成本归因
- **涉及路径**：`~/Development/user-skills/skills/linear-board/`、`~/Development/user-skills/skills/goal-task/scripts/linear-sync.py`、`~/Development/user-skills/skills/goal-task/tests/test_linear_sync.py`
- **关联编号**：`R01`（环 1 部分）、`R12`（Linear 端）、Multica 机制吸收
- **基本信息与可扩展点**：
  - **借鉴 `pi-superagent` (`002-authorization.md`)**：主动同步与自动建单增加 `--mode off|observe|auto`；在 `observe` 模式下只输出 `decision: "observe (would accept)"` 与拟建/拟改的 Issue 载荷，不实际写远端 Linear，跨过信任磨合期后再切 `auto`。
  - **借鉴 `Multica`（Run Cost & Transcript Attribution）**：在 Task 收口同步 Linear Issue 时，支持附带该 Task 的真实 Token 消耗、耗时与 R10 回执摘要，使 Linear Issue 成为完整的人机协作账本。
  - **借鉴 `nocoo/clip`**：保持凭据严格隔离在 `~/.config/linear/` (`0600`)，所有子命令输出确定性 JSON。
- **与其它 Sub-Workflow 的适配接口**：
  - 消费 Sub-Workflow 3（`goal-task`）的 `ledger.md` / `state.sqlite` 状态变更与 R10 成本摘要；
  - 接收 Sub-Workflow 5（`retro-loop` / `AIDash`）在 `observe/auto` 模式下回流的巡检 Idea。
- **📋 专项会话启动 Prompt（复制到新会话执行，调研先行）**：
  ```text
  加载 /workflow-hub、/linear-board 与 /goal-task skill，先阅读 ~/Development/user-skills/skills/workflow-hub/references/his-fm-nocoo-study-and-roadmap.md 中的「Sub-Workflow 1（环 1）：linear-board 看板、主动巡检预演与单次运行成本归因」。

  当前处于 Phase 0 只读深度调研阶段（严禁修改业务代码或向远端 Linear 写入测试垃圾数据）。请先把以下事实与一手资料调研清楚，输出带精确 file:line 的调研与设计报告至 ~/Development/handoff/sw1-linear-research/spec.md：
  1. 精读 ~/Development/user-skills/skills/goal-task/scripts/linear-sync.py 与 tests/test_linear_sync.py，梳理当前 idea / ensure-goal / sync-ledger 的全部触发路径、锁机制与 GraphQL 变更点；
  2. 对照 nocoo/pi-superagent 的 docs/002-authorization.md，设计 R12 `--mode off|observe|auto`（含环境变量 GTT_LINEAR_MODE）在 linear-sync.py 中的精确返回 Schema（observe 模式下如何完整计算 diff 并返回 `"decision": "observe (would accept)"` 及拟执行的 GraphQL mutation 预演清单而不发真实写请求）；
  3. 对照 multica-ai/multica 的 Issue Run Attribution 与 nocoo/clip 的凭据/JSON CLI 规范，设计如何将每个 Task 的 R10 回执摘要、耗时与网关（Magpie/Raven）Token 成本增量聚合写回 Linear Issue Checklist / 里程碑评论；
  4. 梳理与 Sub-Workflow 3 (goal-queue.py) 和 Sub-Workflow 5 (retro-loop / AIDash) 的接口契约。
  ```

---

### Sub-Workflow 2（环 2）：Phase 0 共创设计、架构比选与调研（`my-designer` / `deep-research` / `codebase-design`）
- **涉及路径**：`~/Development/user-skills/skills/{my-designer,visual-design-modernization,deep-research,codebase-design,domain-modeling}`、`~/Development/Skills/skills/design/`、`~/Development/shared-design-system`
- **关联编号**：`R05`、`R06`、`R09`、`R19`（设计侧）、`nocoo/arena` 机制吸收
- **基本信息与可扩展点**：
  - **借鉴 `nocoo/arena`（多方案辩论 + 人类 Checkpoint）**：Phase 0 遇到架构或设计关键分歧时，不再把备选方案散落在长对话里，而是输出结构化的 `proposals` 对比表并等待人类记录 `Checkpoint`（对应博客《The Human's Two Moves》中的 Move 1：只拍板三五件关键决定，对不合适的方案直接说“不”）。
  - **借鉴 `nocoo/his.fm`（四级 Prompt Slots + 乐观锁）**：将 `my-designer` 的提示词拆为 `Global Redlines（客观防截断/无裸色值） -> Category Slot（macOS StatusBar / iOS Single-Hand / Web SaaS） -> Task Intent -> Upstream Tokens`，并在 `spec.md`/`design.md` 头部引入 `version` 与 `spec_sha`。
  - **接入 Sub-Workflow 0 的 Jev 能力**：
    - `deep-research` 接入 `R06`（Citation Check：并发发 `Choice(supports|contradicts|unrelated)` 核查每条发现与原文是否一致）与 `R09`（`Score` 语义重排）；
    - UI 验收接入 `R05`（用 `jev-1.14` 视觉 `Noul` + `Box` 在 150ms 内框出四态 × 双外观截图中的文字截断 `…` 或控件碰撞）。
- **📋 专项会话启动 Prompt（复制到新会话执行，调研先行）**：
  ```text
  加载 /workflow-hub、/my-designer、/deep-research 与 /codebase-design skill，先阅读 ~/Development/user-skills/skills/workflow-hub/references/his-fm-nocoo-study-and-roadmap.md 中的「Sub-Workflow 2（环 2）：Phase 0 共创设计、架构比选与调研」。

  当前处于 Phase 0 只读深度调研阶段（严禁直接修改业务仓或技能文件）。请先把以下 4 块内容调研清楚，将完整调研与设计方案写入 ~/Development/handoff/sw2-codesign-research/spec.md：
  1. 通过 GitHub API 调研 nocoo/arena 与 nocoo/his.fm (docs/prompts.md, docs/editorial-policy.md)，结合本机 codebase-design / domain-modeling，设计 Phase 0 的「多方案 Proposal -> 人类 Checkpoint 拍板」标准结构化协议，以及 spec.md / design.md 的 version 乐观锁与 spec_sha 字段规范；
  2. 盘点 ~/Development/user-skills/skills/{my-designer,visual-design-modernization}、~/Development/Skills/skills/design/ 与 ~/Development/shared-design-system 的现有条款，按「执行层锁死，判断层留白」逐条分类：哪些属于客观物理边界（保留并下沉脚本），哪些属于过度死板的单一产品像素教条（剥离并重构为 Global > Category Slot > Intent > Sources 四级槽位）；
  3. 盘点 deep-research 现有五阶段流水线，设计接入本机 `/v1/systemone` 的 R06 Citation Check（防伪造引用）与 R09 Cross-Encoder Reranking 的具体题型（Choice + Noul / Score）与真值表；
  4. 设计 UI 四态 × 双外观截图接入 Jev 视觉 `Noul + Box` (R05) 自动框选截断 `…` 与控件重叠的协议。
  ```

---

### Sub-Workflow 3（环 3/4）：`goal-task` 编排内核、网关适配与 Thin Harness 瘦身
- **涉及路径**：`~/Development/user-skills/skills/goal-task/`、`~/Development/user-skills/skills/bot-tl-orchestration/`、`~/Development/user-skills/scripts/{test_verify_skills.py,test_goal_queue.py}`
- **关联编号**：`R02`、`R03`、`R08`、`R11`、`R12`、`R13`、`R14`、`R15`、`R16`、`R17`（消费侧）、`R18`、`R20`
- **基本信息与可扩展点（核心重镇）**：
  1. **适配 Agent 网关（对接 Sub-Workflow 0，`R17`）**：
     - 将 `references/runtime-profiles.yaml` 与 `scripts/herdr-dispatch.sh` 中硬编码的具体模型（`opus`/`gemini-4-argon`/`gpt-5.6-luna`/`mai-code-2-flash`）与 Mac mini Claude 周配额 shell 预检，平滑改造为对接本机 `Magpie (:3425)` 的三个逻辑模型组：`group/steering`（TL / 裁决）、`group/reviewing`（Planner Reviewer / Code Reviewer）、`group/coding`（Writer / Designer / Explorer / `pr_monitor`），同时保留显式 profile 回退兼容。
  2. **Thin Harness 瘦身与 `model-surface` 门禁（`R11`）**：
     - 将 `skills/goal-task/SKILL.md`（当前 65.2 KB）瘦身至 `<= 36 KB`：删掉与 `bots-and-agents.md` 双份复制的长命令串及特定健身 App 的死板像素条款（`34–36pt` 等），在 `scripts/test_verify_skills.py` 新增 `model-surface` 体积上限与单向引用门禁。
  3. **吸收 `pi-superagent` 与 `Multica` 编排内核（`R12–R16`、`R18`）**：
     - **`R13` 注意力优先 `status`**：`goal-supervisor.py status [--check]` 第一屏只列 `needs a person`（按 `blocked` / `decision` / `stale` 分组，写明等谁做什么），健康 Goal 压成 1 行；
     - **`R12` 巡检 `observe` 模式**：`goal-supervisor.py scan/watch --mode off|observe|auto`；
     - **`R14` Live Steering 追问直达扇出 + `handback`**：吸收 Multica 的 Steer a running agent，同 PR/同 `Owned Paths` 追问直接注入当前活跃 Writer；越界时 Writer 调 `goal-queue.py set-status --status handback` 退回 TL；
     - **`R15` Task `Mandate` 不可变凭证**：入队时记录 `spec_sha` 与 `owned_paths`，恢复/派发前自动校验 `spec_sha` 是否漂移；
     - **`R16` 人类插队槽位 (`maxWorkers + 1`) 与主控 `<5s` 非阻塞铁律**；
     - **`R18` 单写者 `state.sqlite` + `effects` 队列**（分步演进，把 `ledger.md` 变为派生只读视图）。
  4. **吸收 `goalx` 持续演进模式与 Jev 辅助（`R02`、`R03`、`R08`）**：
     - 支持 `--intent evolve` 预算内持续改进模式；接入 Jev 动态 Skill 预筛（`R02`）、Keep-or-Drop 无损历史压缩（`R03`）与每 5 步死循环看门狗（`R08`）。
- **📋 专项会话启动 Prompt（复制到新会话执行，调研先行）**：
  ```text
  加载 /workflow-hub 与 /goal-task skill，先阅读 ~/Development/user-skills/skills/workflow-hub/references/his-fm-nocoo-study-and-roadmap.md 中的「Sub-Workflow 3（环 3/4）：goal-task 编排内核、网关适配与 Thin Harness 瘦身」。

  当前处于 Phase 0 只读深度调研阶段（严禁修改 user-skills 源码或破坏现有测试）。请结合 nocoo/pi-superagent (docs/000..007)、multica-ai/multica、nocoo/goalx 的一手文档与本机 goal-task 现状，完成深度调研并将完整方案落盘至 ~/Development/handoff/sw3-goaltask-research/spec.md：
  1. 逐行盘点 skills/goal-task/SKILL.md (65KB)、references/bots-and-agents.md (46KB)、bot-tl-orchestration/SKILL.md (24KB) 与 scripts/test_verify_skills.py，列出所有双份复制段落、已过时冲突条款（如 bot-tl-orchestration:29）与特定 App 死板像素规则，给出 R11 model-surface 体积预算门禁与 SKILL.md 瘦身（<=36KB）的精确裁剪清单；
  2. 精读 skills/goal-task/scripts/{goal-supervisor.py,goal-queue.py,herdr-dispatch.sh}，设计 R12 (`--mode off|observe|auto`)、R13 (`status [--check]` 第一屏置顶 `needs a person`)、R14 (Live Steering 同范围追问直达扇出 + 越界 `handback`)、R15 (不可变 `Mandate` + `spec_sha` 恢复校验)、R16 (`maxWorkers + 1` 人类插队槽位) 的精确 CLI 参数、JSON 数据结构与状态转移表；
  3. 设计 R17 网关适配方案：runtime-profiles.yaml 与 herdr-dispatch.sh 如何对接 Magpie (:3425) 的 `group/steering`、`group/reviewing`、`group/coding` 逻辑组并兼容回退；
  4. 给出 R18（单写者 `state.sqlite + effects` 队列）、`goalx` `--intent evolve` 演进模式以及 Jev R02/R03/R08（动态 Skill 预筛、Keep-or-Drop 压缩、死循环看门狗）的分阶段演进路线与接口定义。
  ```

---

### Sub-Workflow 4（环 5）：PR 审查、CI 门禁与跨仓架构锁（`shared-ci` + `code-review` + `auto-review-merge`）
- **涉及路径**：`~/Development/shared-ci`、`~/Development/user-skills/skills/{code-review,auto-review-merge}`、本机 `~/Library/LaunchAgents/com.leepepe.wf03-prmon-heartbeat.plist`
- **关联编号**：`R01`、`R05`、`R07`、`R17`（CI 审查侧）、对标 `nocoo/base-ci` & `nocoo/raven`
- **基本信息与可扩展点**：
  - **PR 巡检三层漏斗（`R01`）**：在 `wf03-prmon` / `stuck_pr` 的 0-Token GitHub API 轮询与唤醒 CLI 修复之间，插入 `Falcon :19823 -> Raven :7024/v1/systemone` 150ms 判断层（并发问 `Choice: requires_code_change | question_only | ack_or_noise` + 伴生 `Noul`），避免感谢类或无需改代码的评论拉起昂贵 Worker。
  - **网关逻辑组适配（`R17`）**：将 `shared-ci/scripts/review/{codex-review.sh,kimi-review.sh}` 统一支持路由至本机网关 `group/reviewing`。
  - **语义 Linter 与 UI 视觉门禁（`R05`、`R07`）**：在 `shared-ci` Stage 2/4 增加可选的 Jev Semantic Linter（检查 AST 抓不到的跨层/可变性违规）与 `design_contract.py` 截图 `Box` 框选检查。
  - **对标 `nocoo/base-ci`**：核对发布制品溯源校验（`release-source`：验证 workflow/branch/commit/status 完全匹配，缺失绝不回退）。
- **📋 专项会话启动 Prompt（复制到新会话执行，调研先行）**：
  ```text
  加载 /workflow-hub、/code-review 与 /auto-review-merge skill，先阅读 ~/Development/user-skills/skills/workflow-hub/references/his-fm-nocoo-study-and-roadmap.md 中的「Sub-Workflow 4（环 5）：PR 审查、CI 门禁与跨仓架构锁」及 ~/Development/shared-ci/AGENTS.md。

  当前处于 Phase 0 只读深度调研阶段（严禁修改 shared-ci 源码或触发远端工作流）。请先把以下 4 块内容调研清楚，输出带精确 file:line 的调研与设计报告至 ~/Development/handoff/sw4-sharedci-research/spec.md：
  1. 全面盘点 ~/Development/shared-ci 的现有 10 层架构（scripts/context, lint, quality, select, policy, review, contracts, schemas, stuck_pr, ruleset）与本机 PR 监控链路（com.leepepe.wf03-prmon-heartbeat.plist 等），对比 nocoo/base-ci 与 nocoo/raven 的门禁实现，找出优势与缺口；
  2. 设计 R01 PR 评论/CI 告警三层漏斗：在 0-Token GitHub 状态预检之后、唤醒重型 CLI 之前，如何调用本机 `/v1/systemone` 并发判定评论性质（Choice + Noul 真值表），并支持 `--mode observe` ("would accept") 预演；
  3. 盘点 shared-ci/scripts/review/{codex-review.sh,kimi-review.sh,render_prompt.py,verdict.py}，设计对接本机 Magpie (:3425) `group/reviewing` 逻辑路由的适配方案；
  4. 设计在 scripts/context/design_contract.py 与 scripts/quality/ 中接入 Jev R05（UI 截图 Box 缺陷框选）与 R07（AST 抓不到的架构/语义 Linter）的可选扩展契约。
  ```

---

### Sub-Workflow 5（环 6/7 & 全局展示）：结合 `AIDash` 的统一态势/日报中心、三仓快照与叙事飞轮
- **涉及路径**：`~/Development/AIDash`（`aidata` Python 数据层 + `aidash` CLI + XPC + Menubar/Main Window + iCloud 同步 iPhone/iPad）、`~/Development/user-skills/skills/{retro-loop,story-craft}`、三套 Skills 仓（`user-skills` / `agent-skills` / `Skills`）
- **关联编号**：`R04`、`R06`、`R13`（展示层）、`R19`（叙事侧）、对标 `nocoo/{eagle,pew,codo,falcon,otter,showtime,steed}`
- **基本信息与可扩展点（重点：以 `AIDash` 为核心整合展示方式 vs. 独立工具选型）**：
  - **核心发现**：你本机已有成熟的原生全平台个人 AI 简报与状态中心 **`~/Development/AIDash`**（上游 Python `aidata` L1–L5 数据生产 → `aidash` Swift CLI → XPC → `AIDash.app` macOS 菜单栏与主窗口 → SwiftData + CloudKit 自动同步 iPad/iPhone + `mark done` / `star` 用户事件回写）。
  - **将 `nocoo` 多个独立看板的精华收拢进 `AIDash`（避免碎片化 Web 看板）**：
    1. **整合 `dsh/pi-superagent` + `codo` → `AIDash` 菜单栏「Needs a Person」注意力置顶卡**：
       - 上游 `goal-supervisor.py status --json` 输出当前 `blocked` / `NEED_OWNER_DECISION` / `in_review` 事项，经 `aidash` CLI 推送到 macOS 菜单栏与 iPhone；在 `AIDash` 点 `mark done` 可回流消警，结合 `codo` 的 AI Guardian 降噪思路避免无效弹窗。
    2. **整合 `nocoo/eagle` (`eagle-daily-zh-v2`) → `AIDash` 23:59 逐时强制证据引用日报**：
       - 在 `AIDash/aidata` 与 `retro-loop` 中吸收 `eagle-daily-zh-v2` 契约：24 小时桶、有活动小时强制引用 1–3 个真实证据 ID（R10/Git SHA/PR）、空小时写“无采集数据”，并用 Jev `R06` Citation Check 拦截虚假引用，生成每日 AI 团队日报卡片推送至 `AIDash`。
    3. **整合 `nocoo/pew` + `Raven/Magpie/Falcon` → `AIDash` 模型网关配额与多 CLI Token 账单卡片**：
       - 采集本机 Claude/Codex/Copilot 及 `Magpie (:3425)` / `Raven (:7024)` / `Falcon (:19823)` 的 Token 消耗、缓存命中率、配额水位与 Jev 决策统计，在 `AIDash` 展示趋势；利用 `AIDash` 原生的 `star`（加星）回写能力，直接对标 `Falcon` 的“加星沉淀 Jev 回归样本”。
    4. **对比评估何时用 `AIDash` 原生承载、何时用独立轻量工具**：
       - **配置快照防漂移（`nocoo/otter`）**：针对保持分离的 `user-skills`、`agent-skills`、`Skills` 三仓与四端 CLI 目录（`~/.agents`、`~/.copilot`、`~/.codex`、`~/.claude`），用 `otter` CLI 做底层快照 diff，仅把“发现配置漂移告警”摘要推入 `AIDash`。
       - **产品演示录制（`nocoo/showtime`）**：环 6/7 独立调用 `Showtime.app` AI Director 录制 4K MP4 演示视频，供 `story-craft` 与发布使用。
- **📋 专项会话启动 Prompt（复制到新会话执行，调研先行）**：
  ```text
  加载 /workflow-hub、/retro-loop 与 /story-craft skill，先阅读 ~/Development/user-skills/skills/workflow-hub/references/his-fm-nocoo-study-and-roadmap.md 中的「Sub-Workflow 5（环 6/7 & 全局展示）：结合 AIDash 的统一态势/日报中心、三仓快照与叙事飞轮」及 ~/Development/AIDash/AGENTS.md。

  当前处于 Phase 0 只读深度调研阶段（严禁修改 AIDash 或 user-skills 业务代码）。请先把以下 4 块内容彻底调研清楚，输出带精确 file:line 的调研报告与架构设计方案至 ~/Development/handoff/sw5-aidash-research/spec.md：
  1. 【AIDash 现有架构与数据链路摸底】
     - 精读 ~/Development/AIDash 的 README.md、AGENTS.md、tech-context.md、aidata/tech-context.md、specs/001-core-briefing-cli/{spec.md,data-model.md,contracts/cardtype-payloads.md,contracts/cli-surface.md} 与 docs/daily-digest-and-aidash-push-chain.md；
     - 摸清当前 aidata (L1-L5) -> aidash CLI -> XPC -> SwiftData/CloudKit -> macOS Menubar / Main Window / iOS 的卡片类型（CardType payloads）、推送链路及 UserEvent (`mark done` / `star`) 回写机制。
  2. 【AIDash vs. nocoo 全家桶（Eagle / Pew / Codo / Falcon / Steed）展示方式深度对标】
     - 通过 GitHub API 调研 nocoo/eagle (docs/DAILY-REPORTS.md, docs/RESOURCES.md)、nocoo/pew、nocoo/codo、nocoo/falcon、nocoo/steed；
     - 逐项评估并设计：哪些展示能力应该直接融入 `AIDash`（例如：① `goal-supervisor.py status` 的 `needs a person` 置顶决策卡；② `eagle-daily-zh-v2` 风格的 23:59 24h 强制证据引用日报卡；③ `pew + Magpie/Raven` 的多 CLI Token/缓存命中率/配额水位卡片；④ 复用 AIDash 的 `done`/`star` 交互回流驱动 Jev 样本加星或任务消警），哪些能力适合保留独立 CLI/App（如 `otter` 配置快照、`Showtime` 4K 演示录制、`codo` 瞬时横幅），给出清晰的《展示层分工矩阵与 AIDash Card Payload 扩展草案》。
  3. 【环 7 复盘与叙事流水线升级（retro-loop + story-craft）】
     - 设计如何将 `eagle-daily-zh-v2` 强制证据 ID 引用 Schema 与 Jev R06 Citation Check 注入 retro-loop 与 gtt-telemetry-export.py；
     - 对照 his.fm 的双触发器乐观锁状态机与四级 Prompt Slots，设计 retro-loop -> story-craft -> AIDash / linear-board 的自动化闭环。
  4. 【三仓 Skills 与四端 CLI 配置快照治理（nocoo/otter 对标）】
     - 针对本机保持分离的 user-skills、agent-skills、Skills 三仓与 ~/.agents、~/.copilot、~/.codex、~/.claude 四端目录，调研 nocoo/otter 的快照与 diff 机制，给出跨机器防漂移体检方案。
  ```

---

## 4. `R01–R20` 与各 Sub-Workflow 归属映射总表

| 编号 | 改造项 | 归属 Sub-Workflow | 当前推荐次序 |
|---|---|---|---|
| **R10** | 本机部署 `Falcon (:19823)` 对接 `Raven v3.2 (:7024/v1/systemone)` | **Sub-Workflow 0（网关底座）** | Stage 1 |
| **R17** | 上层 `Magpie (:3425)` + 底层 `Raven v3.2 (:7024)` 暴露三组逻辑路由 (`steering`/`reviewing`/`coding`) | **Sub-Workflow 0 → 适配 Sub-Workflow 3 & 4** | Stage 1 |
| **R11** | `model-surface` 体积预算门禁 + `goal-task/SKILL.md` 拆精装房瘦身 | **Sub-Workflow 3（`goal-task`）** | Stage 1 |
| **R12** | 主动巡检与同步 `--mode off\|observe\|auto` (`"would accept"` 预演) | **Sub-Workflow 1 (`linear-board`) & Sub-Workflow 3 (`goal-task`)** | Stage 1 |
| **R13** | `goal-supervisor.py status --check` 注意力优先视图（第一屏置顶 `needs a person`） | **Sub-Workflow 3（`goal-task`）** | Stage 1 |
| **R14** | 吸收 Multica Live Steering：同范围追问直达扇出 + 越界 `handback` | **Sub-Workflow 3（`goal-task`）** | Stage 2 |
| **R15** | Task `Mandate` 不可变授权凭证与 `spec_sha` 恢复校验 | **Sub-Workflow 3（`goal-task`）** | Stage 2 |
| **R16** | `maxWorkers + 1` 人类插队槽位与主控 `<5s` 非阻塞铁律 | **Sub-Workflow 3（`goal-task`）** | Stage 2 |
| **R01** | PR 评论/告警三层漏斗：插入 150ms Jev `/v1/systemone` 预判层 | **Sub-Workflow 4（`shared-ci` / PR 监控）** | Stage 2 |
| **R02** | 动态 Skill 预筛（Skill Suggestion，150ms 按需挂载 1–2 个 Skill） | **Sub-Workflow 3（`goal-task`）** | Stage 2 |
| **R03** | Keep-or-Drop 无损上下文压缩（保住精确行号与 SHA） | **Sub-Workflow 3（`goal-task`）** | Stage 2 |
| **R05** | 视觉 UI 截图门禁 + `jev-1.14` `Box` 缺陷框选 | **Sub-Workflow 2（设计）& Sub-Workflow 4（`shared-ci`）** | Stage 3 |
| **R06** | 复盘/调研/R10 引用真实性核查（Citation Check） | **Sub-Workflow 2（调研）& Sub-Workflow 5（复盘）** | Stage 3 |
| **R07** | 语义级代码规范 Linter（Semantic Code Linting） | **Sub-Workflow 4（`shared-ci`）** | Stage 3 |
| **R08** | 子代理死循环实时看门狗（每 5 步 Stuck-Loop 检查） | **Sub-Workflow 3（`goal-task`）** | Stage 3 |
| **R04** | 长期记忆准入守卫（Memory Gatekeeper 3 `Noul`） | **Sub-Workflow 5（全局支撑）** | Stage 3 |
| **R18** | 单写者 `state.sqlite` 状态机 + `Effects` 持久队列 | **Sub-Workflow 3（`goal-task`）** | Stage 4 |
| **R19** | Prompt Slots 四级结构 + `spec.md`/`design.md` `version` 乐观锁 | **Sub-Workflow 2（设计）& Sub-Workflow 5（叙事）** | Stage 4 |
| **R20** | 三缺陷灰色地带自举靶场 + 标准 JSON `Inbox` | **Sub-Workflow 3（`goal-task`）** | Stage 4 |
| **R09** | 个人项目检索重排（`WebIQSearch` / `deep-research` Reranking） | **Sub-Workflow 2（调研与应用）** | Stage 4 |
| **Backlog** | 常驻 Bot 拆分（七域 Bot vs. 拆 Bot 三准则、M2 四形态 `Grok Bot`/`Hermes`/`OpenClaw`/`Cindy`、`his.fm` Scoped Tokens） | **Backlog（`bot-tl-orchestration`，待网关与核心环升级完成后再议）** | Backlog |
