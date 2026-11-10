# Goal 台账（ledger）与标准事件

台账是一个 Goal 的唯一正式状态源：谁在什么时候做了什么、现在卡在哪。`goal.md`、看板、TL 报告都是从它派生的视图，不另存状态。合同规则（R2、R8、R10）见 [SKILL.md](../SKILL.md)，责任线见 [bots-and-agents.md](bots-and-agents.md)。

## 位置与文件指针简报

- **台账路径**：`~/Development/handoff/<goal>/ledger.md`，一个 Goal 一本，放 handoff，不放目标仓库（Lite 档免独立台账，以 PR 描述兼作台账与 R10 回执）。`<goal>` 目录名即 Goal 短名；以 `_` 开头的目录（如 `_watch`）不是 Goal。
- **Task 磁盘文件指针简报（File-Pointer Briefs）**：`~/Development/handoff/<goal>/briefs/<task>.md`，含 1 句话背景、`Owned Paths`、`Interfaces: Consumes / Produces`、允许操作与验收命令；TL 派发 Subagent 时只传文件路径，Subagent 将详情写入 R10 回执并向主会话返回 `<15` 行结论。若修改 `.github/workflows/*`，简报提示使用 `git push git@github.com:<owner>/<repo>.git HEAD:<branch>` 规避 OAuth `workflow` scope 限制。
- **跨机器输入**：放 `~/Development/handoff/<goal>/inputs/`，附清单和每个文件的 SHA-256；缺文件或 SHA 不符时记 `INPUT_BLOCKED`。

## 格式（v1：只追加事件，快照表可更新）

```markdown
# Goal: <goal>
- 目标: … / 验收: <逐条可检查标准>
- Owner: tianpei · 协调者 bot: <bot> · TL: <herdr 名> / 模型 <model> · 机器: Mac mini | MacBook
- Linear: TIA-xx (<url>) · 合同版本: team-lead@<sha>

## Tasks
| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |
|---|---|---|---|---|---|

## Events
- 2026-10-21T15:00:00+08:00 | EVT=TASK_BLOCKED | id=E7 | task=TK1a | from=merge-tk1a-writer-r1 | to=merge-goal-tl-r1 | ref=handoff/merge/r1.md | note=缺 Figma 导出
- 2026-10-23T11:30:00+08:00 | EVT=RULING | id=E8 | task=TK1b | from=merge-goal-tl-r1 | ref=handoff/merge/briefs/TK1b.md | note=decision=允许更新 adapter.ts; why=顺带非公共适配; blast_radius=单文件
- 2026-10-26T22:55:00+08:00 | EVT=RESOLVED | id=B1 | from=joy | to=merge-goal-tl-r1 | note=旁路解卡：Allow path access on /tmp; send-keys enter
```

- `## Tasks` 状态表为快照（取值按 R8，历史 `Target` 直接映射为 `Task`）；`## Events` 以下**只追加、不改旧行**。新台账合同版本只写 `team-lead@<sha>`（旧名 `goal-target-task` 已移除）。
- **事件字段**：① **时间**（必填，写入时刻真实系统时钟 ISO 8601 带时区 `+08:00` 或 `Z`，严禁估算或回填）；② `EVT`（必填）；③ `id`（必填，TL／`goal-queue.py` 用 `E1`…，Bot 旁路巡检／解卡用 `B1`… 或 `J1`…，不混号段，关闭复用同 id）；④ `task`、`from`/`to`、`ref`、`model`/`machine`/`contract`/`tokens`（回执必填）、`note`（值中不含 ` | `）。

## 标准事件与决定权

| EVT | 含义 | 谁发 → 接收者 | 决定／关闭权 |
|---|---|---|---|
| `DISPATCHED` / `READY` | 派发并确认 `working` / Task 满足 R7 与依赖可派 | TL／协调者 | 记录 |
| `HOT_APPEND` | 运行期通过 `goal-queue.py enqueue` 热追加新 Task 并自动计算并行／串行依赖边 | 协调者 bot／TL → TL | 记录（波次收口前 `next-wave` 排空） |
| `TASK_BLOCKED` / `INPUT_BLOCKED` | 任务卡住 / 缺跨机器输入或 SHA 不符 | 执行者 → TL → 协调者 | TL／协调者（补 inputs 或按 R2 裁决） |
| `RULING` | 对不触碰 4 条硬红线的顺带缺口当场放行（`note` 含 `decision + why + blast_radius`） | TL／协调者 → 写者 | TL／协调者（仅限非红线顺带缺口） |
| `CHANGE_REQUEST` | 超出 `RULING` 边界或触碰硬红线，需改范围／合同 | 执行者／TL → 协调者 | 按 R2／R4 分级；Goal 变更只有 Owner |
| `REVIEW_PASS` / `REVIEW_FAIL` | 审查通过（V5 PASS／CONCERNS） / 审查不通过 | Reviewer → TL | 记录 / TL 按 V3 修复或升档 |
| `PR_MONITOR` | 回报 PR CI／auto-merge 终态（`PR_MERGED`、`PR_CHECKS_GREEN`、`PR_CHECK_FAILED`、`PR_CONFLICT`） | `ci-monitor.py`／`pr_monitor` → TL | 绿灯/合并收口；失败由 TL 派 Writer 修复 |
| `NEED_GO` / `NEED_OWNER_DECISION` | 危险操作、hold 例外或需 Owner 拍板 | TL／协调者 → Owner | **只有 Owner**（`note` 记原话，bot 转述不算） |
| `DONE` / `RESOLVED` / `ACK` | Task 完成且回执落盘 / 已处理关闭同 id（再次追加则重开） | 执行者／有决定权者 | 记录 / 关闭同 id |

普通 PR 按既定 auto-merge 条件合并及常规发布（04/tag/TestFlight）不新建 `NEED_GO`。

### CLI 原生 Goal 引擎挂载与会话内追问守卫（Sticky TL）

`ledger.md`、`briefs/<task>.md` 与 Git 是唯一事实源；各 CLI 原生引擎同步投影：

| `ledger.md` R8 状态 / 事件 | Codex CLI（`create_goal` / `update_goal`） | Copilot CLI（`sql` `todos` + `todo_deps`） | Claude Code（`TodoWrite`） |
|---|---|---|---|
| 启动 / 派发（`EVT=DISPATCHED`） | `create_goal(objective="...")` 绑定 `ledger.md` 或 `briefs/<task>.md` | 写入 `todos`（`pending`/`in_progress`）与 `todo_deps` | 投影到 `TodoWrite` |
| 执行与审查中（`in_progress` / `in_review`） | 保持 active goal 持续推进 | 更新 `status = 'in_progress'` | 更新 `in_progress` |
| 完成并落盘回执（`done` / `EVT=DONE`） | 核实验收、PR 与 R10 回执后 `update_goal(status="complete")` | 更新 `status = 'done'` 解锁下游 | 更新 `completed` |
| 阻塞或需裁决（`blocked` / `NEED_GO` / V3 败） | 立即 `update_goal(status="blocked")` 释放循环 | 更新 `status = 'blocked'` | 标注阻塞原因 |

- **Sticky TLRule**：进入 `team-lead` 或存在活动 `ledger.md` 后，用户追问或口语化反馈自动继承合同；`≤3` 文件轻量修正由独立 Worktree 内的 `Steering` 直改，大范围改动派 `Writer` 并记 `EVT=DISPATCHED`／`EVT=DONE`。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

[`scripts/goal-queue.py`](../scripts/goal-queue.py) 以 `~/Development/handoff/<goal>/.queue.lock`（`fcntl.flock`）保护 `queue.json` 与 `ledger.md` 的原子同步：
1. **姿势 1（Batch Mode）**：`goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json ...` 对照 `(repo, paths)` 自动推导冲突依赖（`auto_dependencies`），不相交任务入**并行波次**，重叠任务入**串行链**，更新 `## Tasks` 并记 `EVT=READY`；`TL` 循环 `next-wave --mark-in-progress` 直至 `drained: true`。
2. **姿势 3（Hot-Append Mode）**：存在 `in_progress` 任务时，Bot 收到新任务**不另起重复 TL**，直接调用 `goal-queue.py enqueue` 写入 `queue.json` 并记 `EVT=HOT_APPEND`；若 `TL` 处于 `idle` 则 `herdr agent prompt <goal>-goal-tl-r1 ...` 唤醒，每波收口前必跑 `next-wave` 排空队列。

### Feedback-tracked queue contract

反馈型 Goal 使用 `goal-queue.py enqueue --feedback-manifest <json>`（schema 1）：

```json
{
  "schema_version": 1,
  "entries": [{"feedback_id": "FB-001", "acceptance": [{"acceptance_id": "FB-001-A1", "text": "Criterion"}]}],
  "task_mappings": [{"task_id": "TK1", "feedback_ids": ["FB-001"], "acceptance_mapping": [{"feedback_id": "FB-001", "acceptance_ids": ["FB-001-A1"]}], "common_root_cause_evidence": {"summary": "Multi-FB evidence", "references": ["test://rc"]}}]
}
```

队列在每次 `enqueue`、`next-wave`、`set-status` 时校验 `feedback_id` 与 acceptance 100% 覆盖及唯一终态归属（无 manifest 的旧队列返回 `feedback_validation=unverified_legacy`；含跟踪字段却剥离 manifest 则拒绝）。`enqueue --replacement-of <Task ID>` 用于 1:1 替代，须继承全部原始 `feedback_ids` 与 acceptance。终态 `done`／`cancelled` 须传 `--evidence-json`：`done` 要求全部 `status=passed`；替代要求 `--superseded-by`、`status=replaced` 与匹配 `replacement_task_id`；授权取消要求 `--authorization` 与 `status=cancelled`。

## Telemetry, Linear Sync & Attribution

- **Telemetry hooks & export**：`gtt-telemetry-export.py` 导出脱敏 `TelemetryEnvelope v1` JSONL（`--handoff-dir ~/Development/handoff --machine-id <id> --out <jsonl> [--incremental --state-file <path>]`）；配置 `GTT_AUTO_NOTIFY=1` 或 `~/.config/aidata-sync/config.json` 时队列变更发送非阻塞 `--notify`。
- **Linear state & attribution**：`linear-sync.py sync-ledger --handoff-dir <dir>` 保留 `<!-- GTT-LINEAR-SYNC:START -->` 之上备注，重写 Goal Summary、Task checklist 与 `## Execution & Cost Attribution`（汇总 R10 时长、token 与成本下界），映射 R8 状态为 `Todo / In Progress / In Review / Done / Canceled`，并为阻塞事件与里程碑生成去重评论。支持 `GTT_LINEAR_MODE=off|observe|auto`（凭据存于 `~/.config/linear/credentials.json`）。
