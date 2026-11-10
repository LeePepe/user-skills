# 顶层 Workflow Hub 学习沉淀与子工作流（Sub-Workflow）演进总纲

- **更新日期**：2026-11-10
- **定位**：负责顶层 `workflow-hub` 认知整理、可扩展架构设计与跨子工作流（SW0–SW5）接口定义；SW0–SW5 的 Phase 0 规格、接口契约与启动 Prompt 已归档至 L2 抽屉 [`sub-workflow-specs.json`](sub-workflow-specs.json)。
- **范围边界**：聚焦个人 Workflow（网关底座、工程门禁、编排内核、看板与复盘交付）；常驻 Bot 归入 Backlog；三层 Skills 仓库（`user-skills`、`Skills`、`job skills`）通过四端目录映射与快照工具协同；公司业务项归入 [TIA-84](https://linear.app/tianpeili/issue/TIA-84)。
- **关联中枢**：[`SKILL.md`](../SKILL.md)、[`seven-rings-and-progressive-6dq.md`](seven-rings-and-progressive-6dq.md)、[`sub-workflow-specs.json`](sub-workflow-specs.json)、Linear [TIA-85](https://linear.app/tianpeili/issue/TIA-85)

## 1. 全量一手资料地图（播客 + 3 篇博客 + 16 个提及项目 + `nocoo` 全家桶）

### 1.1 播客与 3 篇博客的底层架构哲学
| 资料来源 | 核心命题 | 提炼出的顶层 Workflow 设计准则 |
|---|---|---|
| **播客**：[把 AI 当团队来带（his.fm EP.01）](https://his.fm/episodes/conversations/agent-team-like-a-company) | M3/M2/M1/IC 分层；模型分 `steering`/`reviewing`/`coding` 三组；预算倒置；三层省 Token 漏斗；双触发器状态机；执行层锁死、判断层留白。 | 1. **模型与编排解耦**：子工作流与脚本不硬编码具体模型，统一走本机网关逻辑分组。<br>2. **确定性脚本 + 毫秒级判断 + 重型 CLI 三层漏斗**：Layer 0（脚本）和 Layer 1（Jev）拦截，不盲目唤醒 Layer 2（CLI）。 |
| **随笔 1**：[当 Harness 变薄，人类还剩下什么？（2026-09-16）](https://lizheng.blog/2026/09/when-the-harness-gets-thinner) | 冗长 SOP 占用上下文并制造冲突；Harness 退回“毛坯房”，只留 **7 层承重墙**，人的职责变成**“删代码 / 删过时 SOP”**。 | **Thin Harness 七层承重墙**：① 可查询状态（SQLite/Ledger）、② 工具与环境、③ 权限与隔离（Worktree/Scoped Token）、④ 验证与真实墙反馈（6DQ）、⑤ 私有事实与业务约束、⑥ 可观测性与审计、⑦ 目标与评价。 |
| **随笔 2**：[执行已经很快了，信任还是老速度（2026-10-15）](https://lizheng.blog/2026/10/execution-fast-trust-slow) | 执行以小时计，信任以月年计；点权限确认拖垮人和模型；常开自动执行的前提是把**“它搞砸了也没关系”**的机制做实。 | **容错四件套**：环境可随时丢弃（独立 Worktree）、密钥绝不进工作区（网关与 `~/.config` `0600` 代持）、改动可一键回滚、结果按回执独立核对。 |
| **随笔 3**：[对 AI 的信任，是一念之差（2026-11-02）](https://lizheng.blog/2026/11/trust-ai-at-your-own-pace) | 用 **Trust, but verify** 接住产出；AI 时代执行不再稀缺，顺序反过来：**“先让事情发生，再说不”**。 | 1. **Observe-First 渐进放权**：新自动化先跑 `observe`（记录 `would accept`），验证后再切 `auto`。<br>2. **The Human's Two Moves**：Move 1（拍板 3–5 个 Checkpoint）和 Move 2（审成果/说“不”删东西）。 |

### 1.2 播客提及项目与 `github.com/nocoo` 全家桶全景索引
| 分类 | 项目 / 仓库 | 一句话定位与核心机制 | 对顶层 Workflow 的取舍结论 |
|---|---|---|---|
| **网关与快思考层** | [`nocoo/raven`](https://github.com/nocoo/raven) (`:7024`) | Copilot 与自定义上游转换代理；`v3.2.0` 原生支持 `POST /v1/systemone`、Key-Bound Routing 与 GHE Copilot。 | **核心底座**：升级 `~/Development/raven` 至 `v3.2.0`，提供协议转换与原生 `/v1/systemone`。 |
| | [`yetone/magpie`](https://github.com/yetone/magpie) / [`nocoo/magpie`](https://github.com/nocoo/magpie) (`:3425`) | 上层本机网关：暴露 `group/steering`、`group/reviewing`、`group/coding`，负责意图路由、缓存粘性、配额调度与 `replyNamingOf`。 | **核心底座**：架在 `Raven (:7024)` 之上，收拢全工作流模型路由与配额管理。 |
| | [`nocoo/falcon`](https://github.com/nocoo/falcon) (`:19823`) + [`typesafe-ai/skills`](https://github.com/typesafe-ai/skills) | Jev (`POST /v1/systemone`) HTTP/MCP 可观测代理：签发 `FALCON_SOURCE_KEY`，SQLite 留存 7 天分布，支持加星沉淀回归样本。 | **核心底座**：全工作流 Layer 1（毫秒级判断层）统一入口，上游对接 `Raven :7024/v1/systemone`。 |
| **编排与状态机内核** | [`nocoo/pi-superagent`](https://github.com/nocoo/pi-superagent) & [`dsh-superagent`](https://github.com/deepseek-ai/dsh-superagent-prototype) | 注意力优先（`status` 置顶 `needs a person`）、`Observe -> Auto` 预演、不可变 `Mandate`（`spec_sha`）、Fan-out + `handback`、单写者 SQLite + `Effects`。 | **全面吸收内核机制**：直接注入 `team-lead`（SW3）与 `linear-board`（SW1）。 |
| | [`multica-ai/multica`](https://github.com/multica-ai/multica) | 人机同看板平台，支持 Live Steering（实时注入指令）、Inbox-Only 通知、Execution Log + 单次 Run 成本归因。 | **去壳留核**：弃用 Postgres/Squad/Issue 业务壳，仅吸收 Live Steering 与成本归因（见 1.3 节）。 |
| | [`nocoo/goalx`](https://github.com/nocoo/goalx) / [`nocoo/arena`](https://github.com/nocoo/arena) | `goalx`（独立 worktree 显式 `keep` 合回 + `--intent evolve`）；`arena`（多 Agent 方案辩论 + Checkpoint CLI）。 | **吸收特性**：`team-lead` 引入 `--intent evolve` 与 `keep/discard`；Phase 0 标准化为 `Proposal -> Checkpoint -> Pop`。 |
| | [`badlogic/pi-mono`](https://github.com/badlogic/pi-mono) / [`nocoo/meowth`](https://github.com/nocoo/meowth) (`:7040`) | `pi-mono` 极简高缓存命中率 Coding Harness；`meowth` 将 5 种 CLI 封装为 HTTP/NDJSON 接口的 Go 守护进程。 | **借鉴缓存与备选**：借鉴 `pi-mono` 固定短前缀提升 Prompt Cache；用 `herdr` 管终端，`meowth` 作无头备选。 |
| **架构门禁与契约** | [`nocoo/base-ci`](https://github.com/nocoo/base-ci) / [`nocoo/his.fm`](https://github.com/nocoo/his.fm) / [`nocoo/steed`](https://github.com/nocoo/steed) | `base-ci`（6DQ + `release-source` 溯源）；`his.fm`（7 态乐观锁 `version` + 四级提示词槽位）；`steed`（物理位置信任域与密钥拦截）。 | **同构与吸收**：补强 `shared-ci` 制品溯源（SW4）；`spec.md`/`design.md` 乐观锁与四级 Slots（SW2/SW5）；维护网关拓扑。 |
| **可观测、配置与交付** | [`nocoo/otter`](https://github.com/nocoo/otter) / [`eagle`](https://github.com/nocoo/eagle) / [`clip`](https://github.com/nocoo/clip) / [`showtime`](https://github.com/nocoo/showtime) / [`pew`](https://github.com/nocoo/pew) / [`codo`](https://github.com/nocoo/codo) / [`mico`](https://github.com/nocoo/mico) | `otter`（三层 Skills 与四端 CLI 快照 diff）；`eagle`（`eagle-daily-zh-v2` 24h 强制证据日报）；`clip`（`0600` JSON CLI）；`showtime`（4K 录屏）；`pew`/`codo`/`mico`（账单/降噪/`WAIT`）。 | **整合进 `AIDash` 与工具链**：`otter` 管三层 Skills 防漂移；`eagle` 日报、`pew` 账单与 `needs a person` 收拢进 `AIDash`（SW5）；`showtime` 供环 6/7 录屏。 |
| **Backlog（延后）** | `Grok Bot` / [`hermes-agent`](https://github.com/NousResearch/hermes-agent) / [`openclaw`](https://github.com/openclaw/openclaw) / [`cindy`](https://github.com/makecindy/cindy) | M2 常驻个人助理与跨设备伴侣层。 | **归入 Backlog**：与七域常驻 Bot 改造一并延后，先完成底层网关与核心工作流升级。 |

### 1.3 一手考古还原：`nocoo` 自己是怎么用 `Multica`、最后又为什么弃壳留核的？
1. **重度使用期（2026-04–09-15，近 6,000 单 `STU-xxxx`）**：配置 `SDE-01..04` 工号 Agent，由 `[🥝 choko]` 巡检 20+ 仓开 Issue → Multica Daemon 在 `~/multica_workspaces/<ws>/<task>/codex-home/` 跑 6DQ 提 PR，并用 `pew` `discoverMulticaCodexDirs()` 采集账单。
2. **断舍离拐点（2026-09-15 停用 Multica，次日写《当 Harness 变薄》）**：`STU-5962` 后停用；`meowth/docs/01-project-overview.md` 写明“不做 issue/squad/autopilot/workspace 业务壳，零 Postgres/Redis”，`pi-superagent/docs/000-philosophy.md` 反思“Do not use it to produce volume. Mass pull requests, broadcast messages and busywork cost the team more than they give.”
3. **“去壳留核”遗产**：仅将 `multica` 零依赖 Go `server/pkg/agent/` SDK 抽入 `meowth`，顶层转向 `Herdr + Eagle + pi-superagent`（与本机下线 Multica 转向 `Linear + Herdr + team-lead` 一致）。

## 2. 顶层 `workflow-hub` 整体可扩展架构蓝图（三大全局机制）

### 2.1 全局模型与决策网关拓扑（`Magpie :3425` + `Raven v3.2 :7024` + `Falcon :19823`）
```text
[Sub-Workflows: team-lead / shared-ci / deep-research / my-designer / launchd]
  ├── 1. 常规推理 (group/steering | group/reviewing | group/coding)
  │     ▼ Magpie (127.0.0.1:3425: 意图路由 + Session Affinity + 配额调度 + replyNamingOf)
  │     ▼ Raven v3.2.0 (127.0.0.1:7024: Copilot/GHE OAuth + /responses Shim + /v1/systemone)
  └── 2. 毫秒级 System One 判断 (70-500ms Choice / Noul / Score / Box)
        ▼ Falcon (127.0.0.1:19823: FALCON_SOURCE_KEY + SQLite 7d 留存 + MCP jev_decide) -> Raven :7024
```

### 2.2 全工作流三层执行漏斗（Three-Tier Execution Funnel）
1. **Layer 0 — 确定性脚本（0 Token，`<50ms`）**：纯 Python/Shell 查 Git SHA、水位线、文件锁与 Schema；`pending == 0` 直接退出。
2. **Layer 1 — Jev System One 快思考（`Falcon :19823 -> Raven :7024/v1/systemone`，`150ms`）**：并发提问（`Choice` + `Noul`/`Score`），过滤非代码噪音、预筛 1–2 个 Skill、核查引用，由确定性真值表承接。
3. **Layer 2 — 重型 CLI 执行器（`Magpie :3425` 路由 `group/steering | reviewing | coding`）**：仅在需规划、写代码或双轴审查时拉起 Herdr CLI 会话。

### 2.3 Thin Harness 边界法则（「执行层锁死，判断层留白」）
- **执行层锁死（下沉脚本与 CI）**：Worktree 隔离、`Owned Paths`、`Mandate` (`spec_sha`)、分层依赖、像素尺寸、禁裸色值、引用存在性——均由 Python/TS 测试硬拦截，不在 `SKILL.md` 写说教。
- **判断层留白（退回毛坯房）**：删掉代替模型思考的步骤清单和特定 App 死板像素参数；采用 `Global Redlines > Category Slot > Task Intent > Sources` 四级槽位；`design-studio` 仅作灵感层，Writer 只执行 `design.md` 冻结契约。

## 3. Sub-Workflow 0–5 汇总与 L2 抽屉索引（详见 [`sub-workflow-specs.json`](sub-workflow-specs.json)）

| ID | 子工作流 (`Space` / `TL`) | 涉及路径 | 核心改造与跨模块适配接口（Produces / Consumes） | Phase 0 规格 |
|---|---|---|---|---|
| **SW0** | 网关与 System One (`w2P` / `sw0-gateway-goal-tl-r1`) | `~/Development/raven`、`~/.codex`、`~/.claude` | **Produces**: `:3425/v1` (`group/steering\|reviewing\|coding`) + `:19823/v1/systemone` (`jev_decide`) | `sw0-gateway-research/spec.md` |
| **SW1** | `linear-board` 预演与归因 (`w2Q` / `sw1-linear-goal-tl-r1`) | `skills/linear-board/`、`linear-sync.py` | **Produces**: `--mode off\|observe\|auto` 预演 + R10/Token 成本归因；**Consumes**: SW3/SW5 | `sw1-linear-research/spec.md` |
| **SW2** | Phase 0 共创与调研 (`w2R` / `sw2-codesign-goal-tl-r1`) | `skills/{my-designer,deep-research,codebase-design}` | **Produces**: `Proposal->Checkpoint->Pop`、四级 Slots + `spec_sha`、R06/R09 重排、R05 截图 `Box` | `sw2-codesign-research/spec.md` |
| **SW3** | `team-lead` 编排与瘦身 (`w2S` / `sw3-goaltask-goal-tl-r1`) | `skills/{team-lead,m2-dispatcher}` | **Produces**: R11 体积门禁、R13 `needs a person` 置顶、R14 Live Steering + `handback`、R15 `Mandate`、R18 SQLite | `sw3-goaltask-research/spec.md` |
| **SW4** | `shared-ci` 与 PR 审查 (`w2T` / `sw4-sharedci-goal-tl-r1`) | `~/Development/shared-ci`、`skills/code-review` | **Produces**: R01 PR 评论三层漏斗、R17 `group/reviewing`、R05/R07 视觉/语义 Linter、`release-source` | `sw4-sharedci-research/spec.md` |
| **SW5** | `AIDash` 态势、快照与叙事 (`w2V` / `sw5-aidash-goal-tl-r1`) | `~/Development/AIDash`、`skills/{retro-loop,story-craft}` | **Produces**: `AIDash` 置顶卡/`eagle-daily-zh-v2` 日报卡/配额卡、`otter` 三层 Skills 快照、`Showtime` 录屏 | `sw5-aidash-research/spec.md` |

## 4. `R01–R20` 与各 Sub-Workflow 归属映射总表

| 编号 | 改造项 | 归属 Sub-Workflow | 次序 |
|---|---|---|---|
| **R10** | 部署 `Falcon (:19823)` 对接 `Raven v3.2 (:7024/v1/systemone)` | **SW0（网关底座）** | Stage 1 |
| **R17** | `Magpie (:3425)` + `Raven v3.2 (:7024)` 暴露 `steering`/`reviewing`/`coding` | **SW0 → SW3 & SW4** | Stage 1 |
| **R11** | `model-surface` 体积预算门禁 + `team-lead/SKILL.md` 瘦身 | **SW3（`team-lead`）** | Stage 1 |
| **R12** | 主动巡检与同步 `--mode off\|observe\|auto` (`"would accept"` 预演) | **SW1 & SW3** | Stage 1 |
| **R13** | `goal-supervisor.py status --check` 第一屏置顶 `needs a person` | **SW3（`team-lead`）** | Stage 1 |
| **R14** | Multica Live Steering：同范围追问直达扇出 + 越界 `handback` | **SW3（`team-lead`）** | Stage 2 |
| **R15** | Task `Mandate` 不可变授权凭证与 `spec_sha` 恢复校验 | **SW3（`team-lead`）** | Stage 2 |
| **R16** | `maxWorkers + 1` 人类插队槽位与主控 `<5s` 非阻塞铁律 | **SW3（`team-lead`）** | Stage 2 |
| **R01** | PR 评论/告警三层漏斗：插入 150ms Jev `/v1/systemone` 预判层 | **SW4（`shared-ci`）** | Stage 2 |
| **R02** | 动态 Skill 预筛（Skill Suggestion，150ms 按需挂载 1–2 个 Skill） | **SW3（`team-lead`）** | Stage 2 |
| **R03** | Keep-or-Drop 无损上下文压缩（保住精确行号与 SHA） | **SW3（`team-lead`）** | Stage 2 |
| **R05** | 视觉 UI 截图门禁 + `jev-1.14` `Box` 缺陷框选 | **SW2 & SW4** | Stage 3 |
| **R06** | 复盘/调研/R10 引用真实性核查（Citation Check） | **SW2 & SW5** | Stage 3 |
| **R07** | 语义级代码规范 Linter（Semantic Code Linting） | **SW4（`shared-ci`）** | Stage 3 |
| **R08** | 子代理死循环实时看门狗（每 5 步 Stuck-Loop 检查） | **SW3（`team-lead`）** | Stage 3 |
| **R04** | 长期记忆准入守卫（Memory Gatekeeper 3 `Noul`） | **SW5（全局支撑）** | Stage 3 |
| **R18** | 单写者 `state.sqlite` 状态机 + `Effects` 持久队列 | **SW3（`team-lead`）** | Stage 4 |
| **R19** | Prompt Slots 四级结构 + `spec.md`/`design.md` `version` 乐观锁 | **SW2 & SW5** | Stage 4 |
| **R20** | 三缺陷灰色地带自举靶场 + 标准 JSON `Inbox` | **SW3（`team-lead`）** | Stage 4 |
| **R09** | 个人项目检索重排（`WebIQSearch` / `deep-research` Reranking） | **SW2（调研与应用）** | Stage 4 |
| **Backlog** | 常驻 Bot 拆分（七域 Bot、M2 四形态 `Grok`/`Hermes`/`OpenClaw`/`Cindy`、Scoped Tokens） | **Backlog（`m2-dispatcher`）** | Backlog |
