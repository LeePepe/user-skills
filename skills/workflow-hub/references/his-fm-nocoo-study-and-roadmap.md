# 学习沉淀与演进路线图：his.fm《把 AI 当团队来带》& nocoo 开源全家桶

- **更新日期**：2026-11-05
- **状态**：Phase 0 学习资料沉淀 + 跨机器推进交接基线（Wave 1 已确认待启动）
- **关联中枢**：[`SKILL.md`](../SKILL.md)、[`seven-rings-and-progressive-6dq.md`](seven-rings-and-progressive-6dq.md)
- **Linear 绑定**：
  - 个人 Workflow 演进总池（`R01–R20`，Project: `Agent Workflow`）：[TIA-85](https://linear.app/tianpeili/issue/TIA-85/todo-个人-workflow-hub-演进候选池接入-jev-第-12-类-与借鉴-dsh-magpie-hisfm-r01-r20)
  - 延后业务项（Sapphire DSAT + Jev，Project: `Bing`）：[TIA-84](https://linear.app/tianpeili/issue/TIA-84/todo-sapphire-dsat-用户反馈接入-jev-system-one-海量初筛归因与-sde-级联提取)

---

## 1. 原始资料与一手 GitHub 仓库索引

### 1.1 播客与博客原文
- **播客单集**：[把 AI 当团队来带：主理人的 agent 管理学（his.fm EP.01）](https://his.fm/episodes/conversations/agent-team-like-a-company)
  - 完整音频直链：`https://f.no.mt/productions/fb084279-04b5-453e-8ebf-3f6fb8a7d59d/audio-2-411c27d9-4a0c-45cd-9634-a07e12d9b3dc.mp3`
- **配套博客**：
  - [当 Harness 变薄，人类还剩下什么？（lizheng.blog）](https://lizheng.blog/2026/09/when-the-harness-gets-thinner)
  - [对 AI 的信任，是一念之差（lizheng.blog）](https://lizheng.blog/2026/11/trust-ai-at-your-own-pace)
  - [执行已经很快了，信任还是老速度（lizheng.blog）](https://lizheng.blog/2026/10/execution-fast-trust-slow)

### 1.2 `github.com/nocoo` 对应核心仓库一手文档
| 仓库 | 定位 | 关键架构/规则文档 |
|---|---|---|
| [`nocoo/pi-superagent`](https://github.com/nocoo/pi-superagent) | 从 `dsh-superagent` 抽取领域内核、基于 Pi SDK 的 CLI-first 多任务 Superagent（去除了 DSH 与飞书依赖） | `docs/000-philosophy.md` ~ `docs/011-basalt-dashboard.md`、`prompts/{orchestrator,assess,work}.md` |
| [`deepseek-ai/dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype) | DeepSeek Harness 上的仓库级主动式 Super-Agent 原型 | `docs/000-philosophy.md` ~ `docs/008-cookbook.md` |
| [`nocoo/falcon`](https://github.com/nocoo/falcon) | macOS 原生 Jev HTTP/MCP 代理与 7 天决策可观测应用（端口 `19823`） | `AGENTS.md`、`docs/01-product.md`、`docs/03-protocol.md`、`docs/06-research.md`、`docs/09-relay.md` |
| [`yetone/magpie`](https://github.com/yetone/magpie) / [`nocoo/magpie`](https://github.com/nocoo/magpie) | 本机统一模型网关、跨订阅配额调度、分组路由与隐私脱敏（端口 `3425`） | `README.md`、`docs/subsystems/gateway-routing.md`、[Intent Routing](https://usemagpie.ai/docs/intent) |
| [`nocoo/raven`](https://github.com/nocoo/raven) | 底层 GitHub/GHE Copilot 协议转换与 `/responses` 自动 Shim 代理节点（端口 `7024`，可本地+云端 VPS 组网） | `CLAUDE.md`、`docs/11`, `14`, `17`, `20`, `23`, `24`, `25`, `26` |
| [`nocoo/his.fm`](https://github.com/nocoo/his.fm) | 播客 CMS 与版本化 Prompt-Slot 演播室（纯外部 Agent 凭 Scoped Token 驱动） | `AGENTS.md`、`docs/production.md`、`docs/prompts.md`、`docs/editorial-policy.md`、`docs/api-onboarding.md` |
| [`nocoo/eagle`](https://github.com/nocoo/eagle) | 跨机器 Herdr Space 实时态势看板、证据校验与 23:59 逐时引用 AI 日报 | `README.md`、`docs/AGENT.md`、`docs/DAILY-REPORTS.md` |
| [`nocoo/steed`](https://github.com/nocoo/steed) | 个人系统拓扑平台（按 `Work`/`Life`/`Learning` Lane 与信任域管理 Host、Agent、数据源） | `README.md`、`docs/16-agent-workflow.md`、`docs/19-phase1-topology-model.md` |
| [`nocoo/clip`](https://github.com/nocoo/clip) | 从 `clip.yaml` 一键生成带 `~/.clip` 凭据管理的 TypeScript/Bun CLI | `README.md` |
| [`nocoo/meowth`](https://github.com/nocoo/meowth) | 将 5 种 Coding CLI（Claude/Codex/Copilot/Hermes/Pi）桥接为统一 HTTP/NDJSON 接口的 Go 守护进程 | `README.md`、`docs/02-agent-workflow.md` |
| [`nocoo/octo`](https://github.com/nocoo/octo) | API/MCP 优先的私人记忆与知识库（去重/迁移/摘要由外部 Bot 执行） | `README.md` |
| [`nocoo/base-ci`](https://github.com/nocoo/base-ci) | 6DQ 可复用 GitHub Actions 工作流（强制 40 位完整 Commit SHA 固定） | `README.md` |
| [`typesafe-ai/skills`](https://github.com/typesafe-ai/skills) | TypeSafe 官方 Jev System One 集成指南与写题规范 | `skills/typesafe-ai/SKILL.md` |

---

## 2. 播客 6 个 Section 逐节精读与对照

| Section | 原文核心方法论（结合 `nocoo` 源码还原） | 当前 `workflow-hub` 对应现状 | 核心差距与启发 |
|---|---|---|---|
| **1. 像管公司一样管 agent** | • **组织层级**：M3（人类主理人）→ M2（Grok Bot 域协调者）→ M1（Herdr 机器主控 TL）→ IC（写代码 Subagent）。<br>• **三组模型路由**：`steering`（掌舵）、`reviewing`（审查）、`coding`（写码），Agent 只认本地网关分组名（如 `magpie/group/coding`）。<br>• **预算倒置**：写代码用又快又便宜的模型（如 `MAI-Code-2-Flash`），最贵的钱花在 `reviewing`。<br>• **瓶颈**：真正的瓶颈不在技术，而在管理者的注意力（`000-philosophy.md`）。 | 已有 `Owner -> 常驻 Bot (Hotty/Joy/Atlas/flower/...) -> Herdr Goal TL -> Writer/Reviewer`；有 `runtime-profiles.yaml` 的 `strong/mid/worker` 分档。 | 模型映射仍硬编码在 `runtime-profiles.yaml` 与 `herdr-dispatch.sh` 启动参数中，未在本地网关层（Magpie + Raven）抽象成 `group/steering`、`group/reviewing`、`group/coding` 虚拟模型组。 |
| **2. 什么是 bot，什么时候该拆一个** | • **Bot 四要素**：模型 + 持续上下文 + 记忆 + 触发器。<br>• **三条拆分准则**：① 握有独有数据；② 有独有技能/权限 Scope；③ 把上下文控制在刚好范围（Agent 的 Work-Life Balance）。<br>• **沟通铁律**：Bot 之间严禁私聊（No P2P DMs），正式流转走状态机，群聊只做临时对齐。 | 有七域常驻 Bot + CLI 层 1-Hop 铁律。 | 常驻 Bot 偏“大域全包”，缺少像 `his.fm` 那样按细粒度 Token Scope（`write` vs `submit` vs `approve` vs `publish`）拆分轻量专项 Bot 的机制。 |
| **3. 省 token 的办法：确定的事交给脚本** | • Agent loop 很费 token；**Skill 的精华是背后的确定性脚本**。<br>• **三层漏斗**：脚本定时拉状态（0 Token）→ 便宜的判断模型（Jev）给下一步分类 → 贵的模型只负责领活改代码。<br>• 对 Agent 说出**“状态机”**三个字，做出来的东西就清晰得多。 | 环 3/5 已有 `Clockwork 0-Token Precheck`（`precheck.py` / `goal-supervisor.py`）和 `goal-queue.py`。 | 已有 Layer 0（0-Token 脚本）和 Layer 2（CLI 大模型），但中间缺了 **Layer 1（Jev 毫秒级 System One 判断层）**，导致非空评论/告警直接唤醒重型 CLI。 |
| **4. 数据和触发器：`his.fm` 是怎么做出来的** | • **“有什么样的数据，就有什么样的场景；只要 CLI 能拿到数据，bot 就能拿到”**（配套 `nocoo/clip` 从 YAML 生成 CLI）。<br>• `his.fm` 网站本身不内置任何 Agent/TTS/队列，全靠外部 Bot 围绕 `draft -> brief_review -> scripting -> script_review -> production -> final_review -> ready` 7 态状态机和乐观锁 `version` 协作，整条流水线仅靠两个触发器驱动。 | 环 7 → 环 1 有遥测/反馈巡检，环 5 有 PR 盯梢。 | 跨环状态流转可进一步收敛为“极少量顶层触发器 + 单写者乐观锁状态机”，并用 `clip` 模式把内部数据源统一包装成标准 CLI JSON 输出。 |
| **5. 架构、代码与灰色地带** | • **测试表达不了架构**：必须用**编号文档（`000..008`）+ 依赖门禁（`dependency-cruiser` / `docs.spec.ts`）锁死架构**，再用高规格 Reviewer 把不听话的 Agent 揪回来；主理人不再看代码。<br>• **模板同质化陷阱**：`@nocoo/basalt` 等模板库让产品稳定，但也让产品长得一样。<br>• **核心法则**：「**执行层锁死，判断层留白**」——大模型的惊喜来自规则没覆盖的灰色地带。 | `Raven` 有编号文档与架构门禁；`workflow-hub` 有 Progressive 6DQ 与 UI Anti-Gaming 门禁。 | `goal-task` 的 `SKILL.md`（63.7 KB）积累了过多手把手的 SOP 与死板 UI 参数（“精装房过满”）；需要把硬约束下沉为测试脚本（`docs.spec.ts` / `model-surface.spec.ts`），同时给设计/叙事留白。 |
| **6. 把活交出去以后，人还剩什么** | • 定义 Bot、定工作流、写好分层全局提示词（`Global > Category > Intent > Sources`）就是团队的**企业文化**。<br>• **The Human's Two Moves（`009-bootstrap.md`）**：① Say what to do（定方向、做三五件关键决策、说“不”/删东西）；② Accept the result（直接审最终成果、付账单）。<br>• **往外看**：看新闻让自己被触发，找到自己的数据，和人交流。PR 多是结果，不是目标。 | Phase 0 共创 + Phase 1 自治已大幅解放编码精力。 | 后续维护重心应从“补更多规则”转向“删掉过时 SOP、维护 Prompt Slots 优先级与物理边界”。 |

---

## 3. 已深挖的三大核心技术专题（Jev / DSH / Magpie+Raven）

### 3.1 Jev 与 `Falcon`（System One 毫秒级决策层）
1. **核心范式（“Judgments from Jev, decisions in code”）**：
   - Jev（`POST /v1/systemone`）是 **System One 快思考分类/打分模型**（70–500ms，成本约为大模型的 1/100）。
   - 三种题型：`Choice`（多选一 + 概率分布 + `confidence`）、`Score`（2–10 级量表 + 各级概率 + `confidence`）、`Noul`（0~1 真值概率）。
   - **写题黄金法则**：
     - 不要问 Jev “该怎么办”，要问可观察的客观判断，把决策写成代码里的真值表（Truth Table）；
     - 一题只问一件事（禁 `or`/`and`）；
     - 同一份 `state` 的多个问题打包在一次请求里并发问完（**Speculative Fan-Out**）；
     - `Choice` 必配伴生 `Noul` 处理“都不匹配（Null Case）”；
     - `Noul` 默认以 `> 0.7`（Yes）、`< 0.3`（No）、`0.3 ~ 0.7`（灰区转人工/大模型）作为起始阈值。
2. **`nocoo/falcon` 本机可观测代理（`127.0.0.1:19823`）**：
   - 为每个本地 Agent 签发独立的 `FALCON_SOURCE_KEY`，同时提供 HTTP `/v1/systemone` 和 MCP `/mcp`（`jev_decide` 工具）。
   - 本地 SQLite 保存最近 7 天所有 Jev 请求的 `state`、题目定义、概率分布、耗时与 Token；支持快捷键 `⌘Return` 一键审阅下一条，加星记录永久保留作为回归评测样本。

### 3.2 DSH (`dsh-superagent` / `pi-superagent`) 架构精髓
1. **注意力经济学（`000-philosophy.md`）**：
   - *"Take as little human attention as possible, and give as much value as possible."*
   - 面板和 `status` 命令第一屏永远只回答三个问题：① 现在有什么事需要我？（`needs a person`）；② 系统健康吗？；③ 某件事从 Trigger 到 Ticket 的完整证据链是什么？健康状态不占版面。
2. **核心领域模型（`001-domain.md`）**：
   - `Source -> Trigger -> Thread -> Eval (Gate + Assessment) -> Mandate -> Ticket -> Assignment -> Executor`。
   - **`Mandate`（不可变授权凭证）**：记录接单时的 `policyVersion` hash、强度（`explicit` 明确指派 vs `inferred` 主动推断）、允许范围（`scope`）和匹配的规则；每次恢复执行前必须 `revalidate(mandate)`。
3. **Subscription Fan-out 与 `handback`（`002-authorization.md`）**：
   - Ticket 一旦创建就会订阅对应的 PR / 会话 Thread；后续人类追问直接扇出给该 Ticket 的 Worker，**无需再经主控评估**。
   - 若追问超出了当前 Ticket 的 Mandate 范围，Worker 必须在动手前调用 `handback(reason)` 退回给主控重新立项。
4. **Observe-First 渐进放权（`002-authorization.md`）**：
   - 主动规则默认跑在 `observe` 模式：只跑完评估并记录 `ignore (would accept)` 及拟创建的标题/范围，不实际触发执行；人工核对无误后再切 `auto`。
5. **执行与恢复保障（`003-execution.md` & `007-architecture.md`）**：
   - **5 秒后台化**：主控会话禁止 `sleep` 和长等待；Worker 的命令超过 5 秒自动转后台 Job，完成时再唤醒同一槽位。
   - **Human Priority Slot**：在 `maxWorkers` 并发池外，额外预留 1 个专用槽位给人类消息或状态查询，确保后台跑满时人类追问永不排队。
   - **Durable Effects & Outbox**：所有数据库状态变更在一个同步 SQLite 事务内完成，外部动作（启动 Session、发消息、投递 Input）写入 `effects` / `outbox` 表，在 `afterCommit` 后带指数退避重试执行。

### 3.3 网关层架构：上层 `Magpie (:3425)` + 底层 `Raven (:7024)` + 旁路 `Falcon (:19823)`

```text
[Tier 1: 编程 Agent 层 (Claude Code / Codex / Pi / Copilot CLI)]
    │  配置只认逻辑分组名：magpie/group/steering | group/reviewing | group/coding
    ▼
[Tier 2: 上层逻辑调度网关 — Magpie (本机 127.0.0.1:3425)]
    │  • 逻辑组解耦：把 group/coding 等映射到底层多个 Provider 节点池
    │  • Turn 首句意图路由 (Intent Routing，调 Jev/Flash 分类并设 effort)
    │  • 会话缓存粘性 (Affinity，保 Prompt Cache + 隔离 Subagent)
    │  • 跨账号/跨节点配额与速度调度 (smart / pace / order / fast) + 失败自动 Failover
    │  • 响应模型名回写 (replyNamingOf，伪装回 Agent 请求的模型名) + 出站隐私双向脱敏
    ├────────────────────────────┬─────────────────────────────┐
    ▼                            ▼                             ▼
[Tier 3A: 本机 Raven 节点]   [Tier 3B: 云端 Raven 节点群]   [Tier 3C: 其他直连订阅与 Falcon]
 127.0.0.1:7024             jp1 / us2 等多台云端 VPS (:7024)  • Claude / ChatGPT 原生本机订阅桥
 (绑定本机 GitHub/GHE 账号)   • Cloudflare Tunnel 零端口暴露    • Kimi / GLM / DeepSeek API Key
                            • SOCKS5 住宅代理隐藏机房 IP      • Falcon (127.0.0.1:19823) 专管 Jev
```

- **已拍板落地方案**：
  1. **底层保持 `Raven (:7024)` 纯粹**：继续用 `Raven` 做本机（及可选云端 VPS / GHE 账号）的 Copilot 协议转换与 `/responses` 自动 Shim 节点，对外提供标准 `rk-...` API Key。
  2. **上层架设 `Magpie (:3425)` 收拢全局路由**：把本机/云端 `Raven`、Claude/Codex 原生订阅、第三方 API Key 全部挂入 `Magpie`，对外统一暴露 `group/steering`、`group/reviewing`、`group/coding`。
  3. **顶层极简化 `runtime-profiles.yaml`**：`herdr-dispatch.sh` 和各环 Skill 只认这三个逻辑组名，彻底解耦具体模型型号与配额脚本。

---

## 4. 个人 `workflow-hub` 全量改进候选池（`R01–R20`）与 4 波推进计划

> **边界说明**：本清单严格聚焦于**你自己的个人 Workflow（`workflow-hub` 7 环、`goal-task`、`bot-tl-orchestration`、`Raven`/Magpie/Falcon）与个人项目（`WebIQSearch`、`VitalStride`、`VoxPocket`）**。
> 涉及公司业务的 **Sapphire DSAT / 用户反馈海量初筛与非结构化工单 SDE 级联提取** 已单独剥离至 Linear Backlog：[TIA-84](https://linear.app/tianpeili/issue/TIA-84/todo-sapphire-dsat-用户反馈接入-jev-system-one-海量初筛归因与-sde-级联提取)。

### 4.1 推荐的 4 波推进顺序（Wave 1 已确认优先推进）

- **🔥 Wave 1（零外部依赖 · 纯 `user-skills` 仓库内功升级，`R11–R16`）**：
  - 完全不需要安装新软件或配置外部 API Key，直接修改 `user-skills` 仓库内的 `goal-supervisor.py`、`goal-queue.py`、`test_verify_skills.py` 及对应 `SKILL.md`，改完即可用 `./scripts/verify --all` 闭环验收。
- **⚡ Wave 2（双层网关底座切换：`R17` Magpie + Raven）**：
  - 安装配置 `Magpie (:3425)` 对接底层 `Raven (:7024)`，将 `runtime-profiles.yaml` 切换为 `group/steering`、`group/reviewing`、`group/coding`。
- **🧠 Wave 3（Jev + Falcon 毫秒级判断层接入：`R10` + `R01–R08`）**：
  - 部署 `Falcon (:19823)`，依次落地 `R01`（Clockwork 预检分流）、`R02`（Skill 动态预筛）、`R03`（Keep-or-Drop 无损压缩）、`R05`（UI `Box` 门禁）、`R06`（引用核查）、`R08`（死循环看门狗）。
- **🏗️ Wave 4（深层演进：`R18–R20` + `R09`）**：
  - 单写者 `state.sqlite` + `Effects` 队列（`R18`）、Prompt Slots + 乐观锁版本（`R19`）、三缺陷灰色地带自举靶场（`R20`）、`WebIQSearch` 重排（`R09`）。

### 4.2 `R01–R20` 明细表

| 编号 | 波次 | 优先 | 改造项 | 落地位置（具体文件） | 核心改动摘要 |
|---|---|---|---|---|---|
| **R01** | Wave 3 | P0 | **Clockwork 三层漏斗：Jev 毫秒级预判层** | `precheck.py` / `goal-supervisor.py` | 在 `0-Token 脚本` 与 `CLI 大模型` 之间插 150ms Jev (`/v1/systemone`) 投机扇出，过滤无需改代码的评论/告警。 |
| **R02** | Wave 3 | P0 | **Skill 与 MCP 工具动态预筛 (Skill Suggestion)** | 新增 `scripts/jev-skill-router.py` | 每轮开始前用 Jev `Choice` + 伴生 `Noul` 从 30+ 个个人 Skill 中挑 1–2 个按需挂载，省首屏 Context。 |
| **R03** | Wave 3 | P0 | **无损上下文压缩 (Keep-or-Drop Compaction)** | 长会话历史裁剪脚本 / Hook | 用 Jev `Noul` 逐条判定历史工具输出去留：要留的一字不改（保住行号/SHA），不要的整块删掉。 |
| **R04** | Wave 3 | P1 | **长期记忆准入守卫 (Memory Gatekeeper)** | 记忆写入前置检查脚本 | 入库前并发问 3 个 `Noul`（是否长期规范、是否非显而易见、是否无敏感信息），防记忆库腐化。 |
| **R05** | Wave 3 | P1 | **视觉 UI 截图门禁 + `Box` 缺陷框选** | `shared-ci` / `design_contract.py` + Reviewer V2 | 用 `jev-1.14` 图像输入 + `Noul` + `Box` 150ms 扫完四态 × 双外观 8 张图，直接框出文字截断 `…` 或重叠控件。 |
| **R06** | Wave 3 | P1 | **复盘/调研/R10 引用真实性核查 (Citation Check)** | `retro-loop` / `deep-research` / R10 校验脚本 | 对结论与所引 `file:line`/日志配对发 `Choice(supports\|contradicts\|unrelated)`，抓虚假引用。 |
| **R07** | Wave 3 | P1 | **语义级代码规范 Linter (Semantic Linting)** | Stage 2/4 辅助检查脚本 | 对 Git Diff 新增函数并发问 AST 抓不到的语义规则（如是否违反 Immutability、是否跨层直调）。 |
| **R08** | Wave 3 | P1 | **子代理死循环实时看门狗 (Stuck-Loop)** | `goal-supervisor.py` 巡检循环 | 每 5 步检查 Worker 最近工具调用是否陷入重复失败死循环（`Noul > 0.8` 立即打断纠偏，不等 30m TTL）。 |
| **R09** | Wave 4 | P2 | **个人项目检索重排 (Reranking)** | `~/Development/WebIQSearch`、`deep-research` | 对召回 Top-30 结果并发跑 1 个 `Score` 请求，百毫秒内完成高精度语义重排。 |
| **R10** | Wave 3 | P1 | **本机部署 `Falcon` Jev 可观测代理 (`:19823`)** | `/Applications/Falcon.app` + MCP 配置 | 给每个本机 Agent 发独立 `FALCON_SOURCE_KEY`，留存 7 天 Jev 决策与概率分布，支持加星沉淀测试集。 |
| **R11** | **Wave 1** | P0 | **`model-surface` 体积预算与文档拓扑门禁** | `scripts/test_verify_skills.py` + 瘦身 `goal-task/SKILL.md` | 强制限制各角色加载的 Skill/Prompt 体积上限并检查无前向依赖/死链，倒逼把冗余 SOP 下沉为脚本。 |
| **R12** | **Wave 1** | P0 | **主动巡检 `observe` 模式 (`"would accept"` 预演)** | `goal-supervisor.py`、`linear-sync.py` | 新增 `--mode off\|observe\|auto`：新巡检规则默认跑 `observe` 记录 `"would accept"`，验证后再切 `auto`。 |
| **R13** | **Wave 1** | P0 | **`goal-supervisor.py status` 注意力优先视图** | `skills/goal-task/scripts/goal-supervisor.py` | 第一屏永远只列 `needs a person`（按 `answer/review/blocked` 分组，直白写明等谁做什么），健康状态压成 1 行，支持 `--check` 退出码。 |
| **R14** | **Wave 1** | P1 | **追问直达扇出 (Fan-out) 与越界 `handback`** | `bot-tl-orchestration/SKILL.md`、`goal-queue.py` | 同 PR 范围追问直达活跃 Writer；超出 `Owned Paths` 时 Writer 必须在动手前调 `handback` 退回 TL。 |
| **R15** | **Wave 1** | P1 | **Task `Mandate` 授权凭证与 `spec_sha` 校验** | `goal-queue.py`、`herdr-dispatch.sh` | 记录不可变 `mandate`（`spec_sha`, `strength`, `owned_paths`），恢复前校验 `spec_sha` 并在变更时通知 Writer。 |
| **R16** | **Wave 1** | P1 | **人类插队槽位 (`maxWorkers+1`) 与主控 `<5s` 铁律** | `goal-queue.py`、`bot-tl-orchestration/SKILL.md` | 并发池外为 Owner 追问预留 `maxWorkers + 1` 抢占槽位；主控会话严禁 `>5s` 阻塞。 |
| **R17** | Wave 2 | P1 | **上层 `Magpie (:3425)` + 底层 `Raven (:7024)` 组合网关** | `runtime-profiles.yaml`、`herdr-dispatch.sh` | 底层保持 `Raven` 纯粹节点，上层架设 `Magpie` 暴露 `group/steering`、`group/reviewing`、`group/coding`。 |
| **R18** | Wave 4 | P1 | **单写者 `state.sqlite` 状态机 + `Effects` 队列** | `goal-queue.py` + `goal-supervisor.py` | 将 `ledger.md`、`queue.json`、`pr-watch-state.json` 收敛进单文件 `state.sqlite`，外部动作进 `effects` 表重试。 |
| **R19** | Wave 4 | P2 | **「执行层锁死，判断层留白」：Prompt Slots + 乐观锁** | `workflow-hub`、`story-craft`、`my-designer` | 提示词改为 `Global -> Category Slot -> Intent -> Sources` 四级结构；`spec.md`/`design.md` 引入 `version` 乐观锁。 |
| **R20** | Wave 4 | P2 | **三缺陷自举靶场（含 1 个灰色地带 Bug）+ JSON `Inbox`** | `tests/fixtures/ledger-target/` | 建含 1 个灰色地带无解 Bug 的微型靶场测试 Agent 是否主动触发 `NEED_OWNER_DECISION`；统一 JSON 事件输入。 |

---

## 5. 待在新机器继续学习的剩余专题清单（A / B / D）

### 5.1 专题组 A：你最初列的 5 大重点与播客章节中待深挖对比的 3 项
1. **重点 1：怎么拆 Agent 或 Bot（播客 Section 1 & 2 vs. 你的 `bot-tl-orchestration` 七域常驻 Bot）**
   - 逐一对照你的七域 Bot（`Hotty` / `Joy` / `Atlas` / `flower` / `艺术家` / `ana` / `笔者`）与他的三条拆分准则（独有数据、独有权限 Scope、上下文 Work-Life Balance），评估哪些常驻 Bot 职责过宽该拆、哪些可降级为脚本触发器。
2. **重点 2：架构、测试与“灰色地带”（播客 Section 5 vs. 你的 Progressive 6DQ + `goal-task` UI Gate）**
   - 对照他的编号文档（`000..008`）+ `docs.spec.ts` + 「执行层锁死，判断层留白」，梳理你现有 `goal-task` / `my-designer` 中哪些属于“把判断层锁得太死的精装房条款”应当删减留白，哪些属于“执行层物理边界”应当下沉为自动化测试。
3. **播客 Section 4 & 6（双触发器内容流水线 & 人的位置：做决策、说不、往外看）**
   - 对照你的环 7（`retro-loop` + `story-craft`）与环 1（`linear-board`），设计极简双触发器闭环。

### 5.2 专题组 B：播客原文「本期提到（16 个项目）」中尚未细看的 9 个
- **M2 常驻个人助理层 4 种形态对比**：`Grok Bot`、[Hermes Agent](https://github.com/NousResearch/hermes-agent)、[OpenClaw](https://github.com/openclaw/openclaw)、[Cindy](https://github.com/makecindy/cindy)
- **极简高缓存命中率 Harness**：[pi (`badlogic/pi-mono`)](https://github.com/badlogic/pi-mono)
- **多机 Herdr 态势看板与 23:59 日报**：[`nocoo/eagle`](https://github.com/nocoo/eagle)
- **多 Agent 公司化自行分工协作**：[Multica (`multica-ai/multica`)](https://github.com/multica-ai/multica)
- **监控告警 Webhook 自动唤醒修复 + 手机推送闭环**：[Uptime Kuma](https://github.com/louislam/uptime-kuma) + [Bark](https://github.com/Finb/Bark)

### 5.3 专题组 D：`github.com/nocoo` 开源全家桶其余宝藏项目
- **可观测与配置治理**：[`nocoo/steed`](https://github.com/nocoo/steed)（拓扑平台）、[`nocoo/otter`](https://github.com/nocoo/otter)（多目录 Skill/配置快照 Diff）、[`nocoo/octo`](https://github.com/nocoo/octo)（私人记忆库）、[`nocoo/kite`](https://github.com/nocoo/kite) & [`nocoo/pika`](https://github.com/nocoo/pika)（执行轨迹回放与会话归档）
- **协作与通知**：[`nocoo/arena`](https://github.com/nocoo/arena)（多 Agent 方案辩论 + 人类 Checkpoint 看板）、[`nocoo/codo`](https://github.com/nocoo/codo)（菜单栏通知 + AI Guardian 降噪）、[`nocoo/meowth`](https://github.com/nocoo/meowth)（5 CLI 统一 Go 守护进程）、[`nocoo/goalx`](https://github.com/nocoo/goalx)
- **门禁、CLI 生成与演示**：[`nocoo/base-ci`](https://github.com/nocoo/base-ci)（6DQ 可复用 Actions）、[`nocoo/clip`](https://github.com/nocoo/clip)（`clip.yaml` 生成 CLI）、[`nocoo/showtime`](https://github.com/nocoo/showtime)（Agent 导演录制产品演示 MP4）、[`nocoo/mico`](https://github.com/nocoo/mico)（人机共编无限画布）
