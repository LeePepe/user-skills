# AI-Native 工程飞轮全景手册

> **用途**：把一次 Goal 从开发执行、证据复盘、规则进化到可公开叙事，串成一个可重复、可审查、可复制的闭环。
>
> **适用范围**：使用 `Goal -> Task` 两层合同（目录名兼容保留 `goal-target-task`，`1 Task = 1 Writer = 1 Worktree = 1 PR`）、Herdr 临时 CLI Agent、共享质量门禁或证据驱动复盘的工程任务。
>
> **阅读方式**：先看 §1 的全景，再按当前象限跳到 §2–§5；命令中的大写变量必须由当前协调者填入，禁止把个人路径、账号或密钥写进仓库。

## 快速导航

| 需要解决的问题 | 直接阅读 |
|---|---|
| 这套方法如何闭环、谁负责什么 | §1 |
| 如何分流任务、启动 Agent、等待和回收 | §2.1–§2.2 |
| 如何做 6DQ、契约同步和发布读回 | §2.3 |
| 如何判断“为什么没交付”而不是凭感觉归因 | §3 |
| 如何把教训变成不可回归的规则 | §4 |
| 如何把工程事实脱敏为 Blog／故事集 | §5 |

---

## §1 顶层全景视图：The 4-Stage Flywheel & Two-Layer Architecture

### 1.1 四阶段飞轮

```text
                 ┌──────────────────────────────┐
                 │  1. Build／开发执行            │
                 │  定义、分流、派发、验证、交付    │
                 └──────────────┬───────────────┘
                                │ 产出代码、R10、Git/CI证据
                                ▼
                 ┌──────────────────────────────┐
                 │  2. Retro／复盘取证            │
                 │  锁边界、还原时间线、检验H1–H5  │
                 └──────────────┬───────────────┘
                                │ 产出最早承重分岔点与责任面
                                ▼
                 ┌──────────────────────────────┐
                 │  3. Evolve／反思进化           │
                 │  把教训落到 Skill、Hook、CI     │
                 └──────────────┬───────────────┘
                                │ 产出可验证的规则与门禁
                                ▼
                 ┌──────────────────────────────┐
                 │  4. Story／叙事外化            │
                 │  脱敏、抽象、编排为 Blog/故事集 │
                 └──────────────┬───────────────┘
                                │ 公开反馈、反例、下一轮约束
                                └───────────────► 回到 Build
```

四阶段不是线性项目管理，而是一个有证据闭环的控制系统：

1. **Build** 只承诺可验收的产出，不用“看起来在忙”替代交付。
2. **Retro** 只接受可观察证据，不把模型行为、工具故障或人的意图当作未经检验的根因。
3. **Evolve** 不允许以“下次注意”结案，必须产生至少一个可自动检查的规则、Hook 或门禁。
4. **Story** 不泄露内部信息；把真实分岔点提炼成读者可复用的机制，再把外部反馈带回下一轮规则。

### 1.2 双层协同架构

| 层 | 成员 | 主要职责 | 典型生命周期 | 不做什么 |
|---|---|---|---|---|
| 常驻 Bot 层 | `Hotty` | 跨 Goal 总管：看整体计划、台账和卡点 | 长期 | 不接管具体 Goal，不做 Task 写者 |
| 常驻 Bot 层 | `Joy` | MacBook 上 Goal 的协调者 | 长期 | 不操作另一台机器 |
| 常驻 Bot 层 | `Atlas` | Retro、AIDash 与相关合并线协调 | 长期 | 不把推断当事实 |
| 常驻 Bot 层 | `flower` | Workflow 01/03/06：需求、PR 交付、周期维护 | 长期 | 不绕过既定交付监督 |
| 常驻 Bot 层 | `fonder` | 环境、工具链、CLI、Herdr 基础维护 | 长期 | 不替具体 Goal 作业务决策 |
| Herdr 临时 CLI Agent 层 | `TL` | 审定定义、调度、协调、对账 | 一个 Goal | 不兼任写者或独立验收 |
| Herdr 临时 CLI Agent 层 | `Explorer` | 只读核实文件、符号、命令和版本 | 一次探查 | 不改仓库、不补方案 |
| Herdr 临时 CLI Agent 层 | `Planner` | 编制 Goal／Task DAG 定义和验收映射 | 一轮计划 | 不写可编译实现 |
| Herdr 临时 CLI Agent 层 | `Writer` | 唯一 Task 写者：实现、验证、回执 | 一个 Task | 不递归派发 Agent |
| Herdr 临时 CLI Agent 层 | `writer_escalated` | V3 第二轮或高复杂度场景的升档写者 | 一轮修复／高复杂度 Task | 不跳过原有审查证据 |
| Herdr 临时 CLI Agent 层 | Cross-Vendor Reviewer | 按 V1–V5 独立验收 | 一轮审查 | 不替写者执行修复 |

**责任线**：

```text
Writer／Reviewer／Explorer → TL → 协调者 Bot → Owner
```

TL 结束前必须收齐下属 R10 回执；Herdr 的 `idle`、`done` 只是终端提示，不能替代磁盘回执和 Git 实际状态。

### 1.3 四大权威源（SSOT）

| SSOT | 负责什么 | 典型内容 | 变更后的回读 |
|---|---|---|---|
| `user-skills` | GTT 执行合同与本 Playbook | R1–R11、V1–V5、复杂度路由、回执和边界 | 读回 Skill、相关 brief 和 R10 |
| `agent-skills` | 01–06 工作流与团队角色 | 需求、实现、PR 接管、自动化发布、遥测、周期维护 | 按对应 workflow 的验证命令回读 |
| `shared-ci` | 6DQ 门禁、契约同步、发布读回 | L1/L2/L3/G2/D1、`sync_contract.py`、`release_sync.py` | CI 绿、契约 check、线上版本与校验和一致 |
| 站点故事集 | `ai-workflow` 对外叙事规范 | 01–08 已发布文章及脱敏风格 | 文章事实回指工程证据，公开稿不含内部标识 |

冲突处理：先保留更高层 SSOT 的硬约束；无法判定时停止受影响动作，记录 `CHANGE_REQUEST` 或 `NEED_OWNER_DECISION`，不靠口头解释覆盖冻结定义。

---

## §2 第一象限：怎么开发（Build & Orchestrate — Macro & Micro SOP）

### 2.1 两档自适应执行模式与任务拆分四谓词（`1 Task = 1 Writer = 1 Worktree = 1 PR`）

#### 两档执行模式（Adaptive Scaling by Task Count）

| 模式 | 同时满足的条件 | 结构 | 交付方式 |
|---|---|---|---|
| **Lite / Single-Task Mode**（`1 Goal = 1 Task = 1 PR`） | 1 个 Task、1 个 Writer、1 个 PR、≤5 个文件；不改公共接口、红线路径、CI 门禁；不新增依赖 | 不设独立 `ledger.md` 台账与单独计划审查 | R7 自检后派 1 Writer + 1 Cross-Vendor Reviewer；PR 描述兼台账与 R10 回执 |
| **Standard / Multi-Task DAG Mode**（`1 Goal = N Tasks = N PRs`） | 多文件特性、跨层改动或多 PR／跨仓工作 | 两层 `Goal → Tasks` DAG（`1 Task = 1 Writer = 1 Worktree = 1 PR`）；按垂直曳光弹切片 | 维护 `ledger.md` 与 `briefs/<task>.md`；每个 Task 对应 1 个 PR 单次审查（V1–V5），全部 `done` 后跑 Goal 端到端验收 |

机械性大范围重构采用 `Expand → Migrate → Contract`：先扩展兼容接口，再迁移调用方，最后收缩旧接口（每个步骤作为 Goal DAG 中的独立 `Task` / `PR`）；不要把跨阶段风险压在一个不可回滚 PR 中。
*旧版兼容说明：历史台账或 issue 中的 `Target` 条目直接映射为单个 `Task`（或同一 `Goal` 下的一组 `Tasks`）。*

#### 四谓词拆分检查

对候选边界依次问四个问题，**任意一个为“是”就拆成独立 Task（即独立 PR）**：

1. **执行者不同？** 需要不同角色、机器或权限才能完成。
2. **验收方式不同？** 例如代码测试与线上读回不能只靠同一个命令证明。
3. **可以并行？** 没有数据、文件或定义依赖，拆开能减少等待。
4. **中间结果交给别人消费？** 例如契约、脚本、设计或证据要交给另一个执行者。

不要使用“一个页面等于一个任务”作为偷懒规则。真正的 Task 边界应围绕接口和验收，而不是 UI 文件数量。拆分前必须区分：

- **源码定位未穷尽**：继续在当前范围内查找，不扩大 Goal。
- **R7 技术冻结前提**：补齐已声明范围内的文件、脚本、命令和依赖证据。
- **真正的架构分叉**：会改变验收、公共接口、安全／隐私、CI 门禁或破坏性操作，按 R2/R4 升级给协调者或 Owner。

每个 Task brief 至少声明：

```text
Background: 一句话说明为什么做
Owned Paths: 允许修改的路径
Interfaces: Consumes / Produces（精确签名、文件或命令）
Dependencies: 前置 Tasks
Acceptance: 可复制的验证命令与通过标准
Handoff: R10 回执路径、准确 head、未完成项
```

### 2.2 CLI + Herdr 操控全细节：七步标准 SOP

以下 SOP 适用于所有 Herdr CLI Agent；变量只代表当前运行时值。禁止使用裸 `--current`，禁止把真实个人绝对路径、账号或令牌写入仓库。

#### Step 1 — 环境与显式 ID 预检

```sh
test "${HERDR_ENV:-}" = 1 &&
  test -n "${HERDR_SOCKET_PATH:-}" &&
  test -n "${HERDR_WORKSPACE_ID:-}" &&
  test -n "${HERDR_TAB_ID:-}" &&
  test -n "${HERDR_PANE_ID:-}"

herdr pane list --workspace "$HERDR_WORKSPACE_ID"
```

从输出核对控制器的精确 `pane_id`、`tab_id`、`workspace_id` 与环境变量一致；不一致就停止，不猜 ID。

#### Step 2 — 布局感知分屏并绑定专用 Worktree

```sh
herdr pane layout --pane "$HERDR_PANE_ID"

SPLIT_JSON="$(
  herdr pane split \
    --pane "$HERDR_PANE_ID" \
    --direction right \
    --cwd "$TASK_WORKTREE" \
    --no-focus
)"
NEW_PANE_ID="$(printf '%s' "$SPLIT_JSON" | jq -er '.result.pane.pane_id')"
```

宽窗格用 `right`，窄／高窗格用 `down`。始终使用返回 JSON 的 `pane_id`，不复用旧 pane，不触碰 Owner 手开的窗格。

#### Step 3 — 具名启动与 `common.herdr_kind_map`

命名格式：`<goal>-<task>-<role>-r<round>`，名称满足 `[a-z][a-z0-9_-]{0,31}`。`herdr --kind` 使用 `runtime-profiles.yaml` 的映射：`codex → codex`、`copilot → copilot`、`cursor-agent → cursor`。

**Codex Writer 模板**（按当前 profile 替换模型）：

```sh
WRITER_NAME="${GOAL_SLUG}-${TASK_ID}-writer-r1"
herdr agent start "$WRITER_NAME" \
  --kind codex \
  --pane "$NEW_PANE_ID" \
  -- codex -m gpt-5.6-luna -s danger-full-access -a never
```

**Copilot Gemini Cross-Vendor Reviewer 模板**：

```sh
REVIEWER_NAME="${GOAL_SLUG}-${TASK_ID}-reviewer-r1"
herdr agent start "$REVIEWER_NAME" \
  --kind copilot \
  --pane "$NEW_PANE_ID" \
  -- copilot --model gemini-4-argon \
    --deny-tool write --deny-tool shell --no-ask-user
```

Reviewer 必须与被审者使用不同模型厂商；每次启动显式指定模型，不依赖 CLI 默认值。不得使用 `--yolo`、`--force`、`--allow-all-*` 或其他 profile 禁用参数。

#### Step 4 — 文件指针派发、等待与 Socket 零轮询监听

单任务同步派发：

```sh
herdr agent prompt "$WRITER_NAME" \
  "Read $TASK_BRIEF; implement only Owned Paths; write the full R10 receipt to $R10_RECEIPT." \
  --wait --timeout 600000
```

多个无依赖 Task 先派发再等待：

```sh
herdr agent prompt "$TASK_A_NAME" "Read $TASK_A_BRIEF; write $TASK_A_RECEIPT."
herdr agent prompt "$TASK_B_NAME" "Read $TASK_B_BRIEF; write $TASK_B_RECEIPT."
herdr agent get "$TASK_A_NAME"
herdr agent get "$TASK_B_NAME"
herdr agent wait "$TASK_A_NAME" --timeout 600000
herdr agent wait "$TASK_B_NAME" --timeout 600000
```

不要把长合同粘进 prompt；只传 brief 和回执指针。需要监听状态时，通过 Herdr socket 订阅事件，避免固定间隔轮询。以下 Node.js 片段只消费状态事件，收到终态后由协调者再按 R10/Git 对账：

```js
import net from "node:net";

const socketPath = process.env.HERDR_SOCKET_PATH;
const paneId = process.env.HERDR_PANE_ID;
if (!socketPath || !paneId) {
  throw new Error("HERDR_SOCKET_PATH and HERDR_PANE_ID are required");
}

const request = {
  jsonrpc: "2.0",
  id: 1,
  method: "events.subscribe",
  params: {
    subscriptions: [
      { type: "pane.agent_status_changed", pane_id: paneId },
    ],
  },
};

const socket = net.createConnection(socketPath);
let buffer = "";
socket.on("connect", () => socket.write(`${JSON.stringify(request)}\n`));
socket.on("data", (chunk) => {
  buffer += chunk.toString();
  const lines = buffer.split("\n");
  buffer = lines.pop() ?? "";
  for (const line of lines.filter(Boolean)) {
    try {
      const event = JSON.parse(line);
      const status = event?.params?.status ?? event?.result?.status;
      const agent = event?.params?.agent_name ?? event?.params?.agent;
      if (status) console.log(`[herdr] ${agent ?? "agent"}: ${status}`);
    } catch {
      console.error("[herdr] ignored malformed event");
    }
  }
});
socket.on("error", (error) => {
  console.error("[herdr] socket error:", error.message);
  process.exitCode = 1;
});
```

监听器不是完成证明；它只负责唤醒下一步。完成证明仍是 R10 回执、准确 Git SHA 和目标仓库 required checks。

#### Step 5 — 执行中动态调控：RULING 与升档

**`EVT=RULING` 内联裁决**：

1. Writer 发现同目标内的 1–2 个辅助／适配文件漏列，或非公共接口微调缺口。
2. Writer 回报 `NEEDS_RULING`，列出文件、原因、影响面和验证命令。
3. TL 判断不触碰四条硬红线：改变 Goal 验收；安全／隐私／数据丢失；修改 CI 门禁／规则集；破坏性操作。
4. 协调者在台账追加 `EVT=RULING | decision=... | why=... | blast_radius=...` 后放行。
5. 触碰红线或改变范围时，不内联放行，走 `CHANGE_REQUEST` / R2 重规划。

**V3 第二轮失败自动升档**：

```text
第 1 轮审查 FAIL → 原 Writer 修复
修复后复审仍 FAIL → 改派 writer_escalated（gpt-6.1-sol 或 opus）
第二轮修复后复审仍 FAIL → Task blocked，退回定义方重规划
```

升档上下文必须包含原 brief、前序 diff、两轮 findings 和验证日志路径；不因换 PR、SHA、agent 或任务名而重置失败计数。

#### Step 6 — 安全读取、blocked 处理与 R10 真值对账

运行中只读可见画面：

```sh
herdr agent read "$WRITER_NAME" --source visible --lines 40
```

进入 `idle`／`done` 后读取稳定输出：

```sh
herdr agent read "$WRITER_NAME" --source recent-unwrapped --lines 80
```

禁止在 `working` 时读取 alternate-screen 历史。若为 `blocked`：

```sh
herdr agent get "$WRITER_NAME"
herdr agent read "$WRITER_NAME" --source visible --lines 40
herdr agent send-keys "$WRITER_NAME" esc
# 仅在确认需要终止当前输入时：
herdr agent send-keys "$WRITER_NAME" ctrl+c
```

判断顺序固定为：**磁盘 R10 回执 → Git 实际 SHA／工作树 → CI／发布读回 → Herdr 状态**。任何顺序相反都可能把终端提示误当作完成。

#### Step 7 — 子窗格回收与 Worktree 保护清理

收齐回执、确认 agent 不再 `working` 后，只关闭本轮创建的 pane：

```sh
herdr pane close "$NEW_PANE_ID"
```

Worktree 删除前逐项确认：分支已 push；PR 已合法合并或现场已交接；`git status --short` 为空；没有未保存的回执或输入。未满足任一项就保留 Worktree，不执行清理。不得停止共享 Herdr 服务，不得触碰其他 Goal 的 pane。

### 2.3 仓库级 6DQ 质量门禁与发布读回

#### 6DQ 五维矩阵

| 维度 | 检查目标 | 最低证据 | 常见失败 | 处置 |
|---|---|---|---|---|
| **L1** | 单测、四项覆盖率、静态检查 | 测试命令、覆盖率报告、lint/typecheck 输出 | 只测 happy path；覆盖率虚高 | 补边界与失败路径，拒绝伪测试 |
| **L2** | 本地真实边界集成 | 使用真实序列化、存储、网络边界的集成结果 | 全部 mock，契约未穿透 | 选最小真实边界集，固定可重复夹具 |
| **L3** | 端到端用户旅程 | 从入口到结果的 E2E 证据 | 只验证中间函数 | 选择一条关键旅程，记录环境与版本 |
| **G2** | 安全与契约审计 | 权限、输入、输出、兼容性、秘密扫描 | 越权、注入、敏感信息泄漏、契约漂移 | 阻塞合并，修复后全量复验 |
| **D1** | Worktree 与环境隔离 | 当前分支、路径、依赖、环境变量、清理记录 | 主目录改代码、串台、脏工作树 | 立即停写，对账并恢复隔离 |

6DQ 是组合门槛，不是五个可随意选的建议。L1 绿不能推断 L3 绿；G2 或 D1 失败时，即使功能测试通过也不得交付。

#### 多仓契约同步

在包含共享契约的仓库根目录执行：

```sh
python3 scripts/context/sync_contract.py --check
```

发现 drift 且本次变更应更新契约时：

```sh
python3 scripts/context/sync_contract.py --sync
python3 scripts/context/sync_contract.py --check
```

回执记录：检查范围、同步前后的摘要、生成或更新的文件、第二次 `--check` 的结果。`--sync` 不得代替人工确认；先确认变更属于当前 Goal，避免把无关仓库文件带入 Task。

#### 跨语言发布读回

发布候选版本时：

```sh
python3 scripts/quality/release_sync.py version-sync
python3 scripts/quality/release_sync.py readback
```

`version-sync` 核对各语言／客户端版本；`readback` 必须同时核对：

- `/api/live` 返回的版本、构建 SHA 与期望版本一致；
- `SHA256SUMS` 中产物摘要与下载产物一致；
- GitHub Release 的 tag、资产和摘要与本次准确 head 一致。

若读回不一致，状态为 `blocked`，记录哪个层次漂移、第一次发现的命令和最小修复范围；不得以“发布页面看起来正常”结案。

---

## §3 第二象限：怎么复盘（Retro — Evidence-Led Diagnosis SOP）

### 3.1 触发条件与停止线

以下任一情况触发 Retro：

- Goal 结项，尤其是“完成”前有多次重试；
- V3 升档、两轮 FAIL 或退回重规划；
- 会话跑了多轮却产生 **0 个产品提交**；
- CI 门禁、版本同步、发布读回或线上旅程事故；
- 任何人提出“工具坏了”“模型失能”“只是偶发”作为结论。

Retro 的停止线：没有证据就标记“未知”；不能为了形成漂亮故事而补写不存在的时间线、意图或因果。

### 3.2 五步证据链诊断法（G-GTT-AUDIT）

#### 1. 锁定证据边界

建立只读证据包，至少包含：

```text
ledger.md
Task brief 与其 revision
R10 receipts（Writer／Reviewer／Explorer）
plan／contract 版本
会话 rollout JSONL、Herdr 状态事件
Git log、diff、CI／发布读回
```

对每一份输入计算 SHA-256，记录采集时间、来源、是否完整。原始证据只读保存；分析笔记另存，不能覆盖原始文件。

#### 2. 还原可观察时间线并找最早承重分岔点

按时间排序记录事实：命令、状态、文件变化、回执、审核结论。找出第一个同时满足以下条件的节点，称为 **Earliest Load-Bearing Fork（最早承重分岔点）**：

1. 从此处开始，后续异常具有可观察的方向性；
2. 如果改正该节点，至少一个后续失败或空转会被反事实地解除；
3. 该节点在当时可由某个角色或门禁发现；
4. 不是更早节点的纯粹重复表现。

“最后一次报错”通常不是最早承重分岔点；“Agent 看起来很忙”也不是证据。

#### 3. 用 H1–H5 检验竞争假设

| 假设 | 关注面 | 若为真，应看到的预测证据 | 正证 | 反证／淘汰标准 |
|---|---|---|---|---|
| **H1 定义／路由缺陷** | Goal、Task 边界、复杂度分流 | brief 缺验收或 Owned Paths；四谓词本应拆分却被收束；R7 前提缺失 | 计划文本、diff 和失败验收相互吻合 | 定义完整、依赖清楚且写者仍未执行，则不能归 H1 |
| **H2 交接／调度缺陷** | TL、Writer、Reviewer、R10 | prompt 未指向正确 brief；pane／worktree 串台；依赖未满足却派发；回执缺失 | Herdr 事件、Git 路径、回执时间线可复现 | 指针、路径、依赖和回执均正确，则排除 H2 |
| **H3 工具／运行时缺陷** | CLI、Herdr、网络、沙箱 | 命令明确失败、socket 断连、权限拒绝，且同一输入在替代路径恢复 | 原始 stderr、状态事件、重试对照 | 失败前已有定义／交接缺口，或替代命令同样失败，则不得归因 H3 |
| **H4 外部输入／环境缺陷** | 依赖、凭据、服务、版本、工作树 | 输入缺失／SHA 不符、依赖服务不可用、环境与 brief 声明不一致 | 输入清单、环境快照、服务响应 | 本地可复现且不依赖外部输入，则排除 H4 |
| **H5 验收／产品错配** | 真实目标、测试、发布读回 | 测试全绿但真实旅程失败；验收命令未覆盖 Goal；发布读回漂移 | L3、release readback、用户旅程证据 | 真实旅程和发布读回均通过，则不得用 H5 解释功能空转 |

检验顺序：先写每个假设的预测，再逐项找正反证；不能先选一个喜欢的根因，再挑证据。若多个假设同时成立，按“最早承重”排序，责任面可以重叠但修复动作必须有优先级。

#### 4. 反事实门槛核对（Counterfactual Gate Check）

一次只改变一个变量，问：

```text
如果在最早承重分岔点前增加这一条检查／指针／门禁，
是否能让同一 Goal 在相同输入下继续走到可验收结果？
```

可选单变量包括：补一个验收命令、锁定一个 `Owned Path`、改一个 prompt 指针、补一个 Herdr 状态检查、启用一个同步脚本。不能一次改变角色、模型、任务边界和测试，否则无法知道哪一项有效。

#### 5. 划分责任面与停止边界

| 责任面 | 可以确认的事实 | 不应越界推断 |
|---|---|---|
| 定义方 | 定义是否完整、验收是否可实现、路由是否符合四谓词 | 不凭结果倒推写者意图 |
| TL | 是否按依赖、指针、状态和 R9/R10 调度 | 不把 pane 状态当完成 |
| Explorer | 目标事实是否存在及其 file:line 证据 | 不给实现方案或替定义方做决定 |
| Writer | 实际修改、命令结果、未完成项 | 不把局部绿灯宣称 Goal 完成 |
| Reviewer | V1 双轴 findings 和 V5 结论 | 不替代 CI、发布或实机验收 |
| 外部输入 | 服务、依赖、凭据、版本的可观察状态 | 不把不可观测意图写成根因 |

### 3.3 Retro 输出模板

```markdown
# Retro: <goal>
## Evidence boundary
- Inputs and SHA-256:
- Collection time:
- Missing evidence:

## Observable timeline
| Time | Actor | Fact | Evidence |

## Earliest Load-Bearing Fork
- Node:
- Why load-bearing:
- Counterfactual:

## H1–H5 verdict
| Hypothesis | Status: confirmed / weakened / eliminated | Evidence |

## Responsibility and stop boundary

## Physical regression locks
- Skill:
- Hook:
- CI:

## Story candidate
- Five-act spine:
- Desensitization notes:
```

---

## §4 第三象限：怎么反思进化（Reflect — Distill into Rules, Hooks & Gates）

### 4.1 物理防回归铁律

Retro 不得以“下次注意”“加强沟通”“让 Agent 更聪明”收尾。每个已确认的分岔点至少落到一个**可执行、可验证、可回读**的改动。三道锁形成从软约束到硬门禁的梯度：

| 锁 | 作用 | 适合拦截什么 | 输出与验证 |
|---|---|---|---|
| **Skill 锁** | 把认知规则写进合同、brief、角色和验收 | 规划／拆分／调度认知漏洞 | 更新 GTT 或 workflow 条款；读回引用、R7 检查和 R10 |
| **Hook 锁** | 在动作发生前阻止越界 | 本地路径、工具、交接和危险操作 | `PreToolUse` 或 Herdr 自动化拒绝错误动作；验证正反例 |
| **CI 锁** | 在合并／发布前自动阻断 | 质量、契约、版本、发布漂移 | 6DQ、`sync_contract.py`、`release_sync.py`；验证失败样例和绿灯样例 |

### 4.2 三锁演进映射

| Retro 发现 | 首选 Skill 条款 | Hook／自动化 | CI／发布门禁 | 例子 |
|---|---|---|---|---|
| 任务被“一个页面=一个 Task”收束 | 增加四谓词、Vertical Slice 与 R7 检查 | brief 生成器拒绝空 `Interfaces` | 检查 Task 验收覆盖 | 页面、API、E2E 分开但保留依赖 |
| 交接指针错误或 pane 串台 | 强化 File-Pointer Handoff 和 R9 | Herdr split 校验显式 pane／worktree | 检查回执路径与准确 head | 只允许当前 Worktree 写入 |
| Writer 空转、0 产品提交 | 明确 Writer 交付定义与 R10 | `events.subscribe` 在终态唤醒对账 | 无提交或无验收证据不得完成 | 状态事件只触发对账，不宣称完成 |
| Review 两轮失败仍重复修 | 固化 V3 Failure-Tier Escalation | 记录 round 与模型／厂商 | required checks 保持原门禁 | 第二轮换 `writer_escalated` |
| 质量／版本／发布漂移 | 明确 6DQ、契约和读回要求 | 发布脚本拒绝未同步版本 | `sync_contract.py` + `release_sync.py` | `/api/live`、摘要和 Release 一致 |
| 主目录直接改代码或秘密泄漏 | 强化 D1、G2 与红线 | `PreToolUse` 阻断路径／秘密模式 | secret scan、隔离检查 | 错误路径立即停写并对账 |

落地顺序：

1. **先锁事实**：引用 Retro 的证据和最早承重分岔点。
2. **选最靠前的锁**：能在 Skill 阶段发现，就不要只等 CI；能在 Hook 阶段阻断，就不要只写说明。
3. **补第二道兜底**：重要规则至少有一个运行前或合并前自动检查。
4. **写失败样例**：验证门禁真的会阻断错误输入，而不是只在绿灯样例中通过。
5. **回读并链接**：把文件、命令、输出和版本写入回执；没有回读不算进化完成。

---

## §5 第四象限：怎么扩展外化（Storytelling — Blog &《带一群 AI 干活》故事集）

### 5.1 一鱼两吃流水线

同一个 Goal 的三类工程资产可以复用，但不能原样公开：

```text
ledger.md + R10 回执
          + Retro 报告（H1–H5 与反事实）
          + 防回归 PR diff
          ↓ 事实抽取
        内部工程资产（可追责、可复现）
          ↓ 去身份化／去路径／去敏感字段／抽象组织名
        对外技术故事（可理解、可复用、可验证）
```

内稿先完成事实核对，再做脱敏；不要为了故事顺滑而删掉关键反证。公开稿可以改变叙事顺序，但不能改变因果结论。

### 5.2 五段式叙事结构

对齐《带一群 AI 干活》01–08 篇的节奏：

1. **时间锚点与反常现象**：用具体但不敏感的时间切入，例如“某月某日晚上某点，终端里已经跑了很多轮，却没有一个产品提交”。
2. **直觉／旧规则导致卡死**：展示当时合理但不够用的规则，例如把一个页面误当一个 Task，或把 `idle` 当完成。
3. **翻开回执找到真正分岔点**：用 ledger、R10、事件流和 Git 对账，呈现 H1–H5 的排除过程。
4. **砍繁文缛节换极简机制**：只保留一个能物理阻断复发的改变，例如四谓词、文件指针、Socket 订阅或三锁映射。
5. **真实跑通与读回余味**：展示命令、准确 head、CI／发布读回和仍然未知的部分；不把一次成功包装成永久保证。

每段至少有一个可回指的内部证据；对外只暴露抽象后的证据形状，不暴露原始标识。

### 5.3 隐私脱敏硬红线

| 原始信息 | 对外替换 | 绝不保留 |
|---|---|---|
| 公司或组织名 | “某大厂”“某团队” | 法人、租户、内部组织树 |
| 个人姓名、账号、bot 私名 | “同事”“TL”“Reviewer” | 用户名、邮箱、机器名 |
| 内部项目／仓库 | `Project-<代号>` | 私有仓库 URL、Issue、PR、分支前缀 |
| 私有管理平台 | “协作平台”“内部看板” | 租户 ID、工作区 ID、截图中的导航信息 |
| 本机绝对路径 | `~/...` 或相对路径 | 任何用户目录绝对路径、家目录用户名、挂载点 |
| 凭据、令牌、密钥 | “环境变量中的凭据” | 值、文件名、令牌路径、日志片段 |
| 内部服务地址 | `/api/live` 等抽象接口名 | 域名、端口、内部 DNS、请求头 |

公开工具名可以保留：Herdr、Codex CLI、Claude Code、GitHub Copilot、SwiftUI 等。保留工具不等于公开内部配置；命令中的模型、路径和身份仍应使用占位符。

### 5.4 故事集构建与演进目录

**第一季已发布脉络（01–08）**：按站点现有文章顺序回读主题、冲突、机制和读者可复用结论；若文章元数据发生变化，以站点目录为准，不在 Playbook 中复制内部链接或个人标识。

**第二季素材映射**：

| 篇章 | 工程素材 | 核心机制 | 必须保留的反常点 |
|---|---|---|---|
| 09《一个页面不等于一个任务》 | 四谓词、Vertical Slice、R7 | 用验收与接口拆 Task | 页面完成了，Goal 却没有端到端证据 |
| 10《分屏里的指挥官》 | 七步 Herdr SOP、显式 pane、Socket 订阅 | 状态事件触发对账，非轮询猜测 | Agent 看似工作，真实 Worktree 没有产品提交 |
| 11《裁决代替停工》 | `EVT=RULING`、四条硬红线、V3 升档 | 小缺口快速裁决，红线缺口重规划 | 不是每个发现都值得重开整个计划 |

### 5.5 发布前故事验收清单

- [ ] 时间线和因果都能回指内部证据，未知项明确标注。
- [ ] 没有真实个人绝对路径、账号名、组织名、令牌或私有平台标识。
- [ ] 至少有一个失败样例和一个可验证的新机制。
- [ ] 五段结构完整，但没有把工具状态冒充产品结果。
- [ ] 工程改动、Skill／Hook／CI 三锁和公开结论彼此一致。
- [ ] 公开稿脱离内部上下文仍能让读者复制方法，而不是只能围观事故。

---

## 附录：一页式执行卡

```text
BUILD
  1. 选 Lite（1 Goal = 1 Task = 1 PR）或 Standard（1 Goal = N Tasks = N PRs）
  2. 用四谓词划 Task（1 Task = 1 Writer = 1 Worktree = 1 PR），写清 Interfaces / Owned Paths / Acceptance
  3. 按 Herdr 七步 SOP 启动、派发、监听、读取、回收
  4. 跑 6DQ、契约同步、发布读回；以 R10 + Git + CI 为真值

RETRO
  5. 锁证据边界，算 SHA-256
  6. 找 Earliest Load-Bearing Fork
  7. 用 H1–H5 正反证检验，做单变量反事实

EVOLVE
  8. 把结论落到 Skill / Hook / CI 三道锁
  9. 写失败样例并回读验证

STORY
 10. 从 ledger + Retro + diff 抽事实
 11. 按五段式重排，执行隐私脱敏
 12. 回指证据、保留未知、发布后收集反例
```
