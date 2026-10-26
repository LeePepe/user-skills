# Goal 台账（ledger）与标准事件

台账是一个 Goal 的唯一正式状态源：谁在什么时候做了什么、现在卡在哪。`goal.md`、看板、TL 报告都是从它派生的视图，不另存状态。合同规则（R2、R8、R10）见 [SKILL.md](../SKILL.md)，责任线见 [bots-and-agents.md](bots-and-agents.md)。

## 位置

- `~/Development/handoff/<goal>/ledger.md`，一个 Goal 一本，放 handoff，不放目标仓库。两台机器各放各自 Goal 的台账（Lite 档免独立台账，以 PR 描述兼作台账与 R10 回执）。
- `<goal>` 目录名即 Goal 短名；以 `_` 开头的目录（如 `_watch`）不是 Goal。
- **Task 磁盘文件指针简报（File-Pointer Briefs）**：`~/Development/handoff/<goal>/briefs/<task>.md`，存放每个 Task 的交接简报（含 1 句话背景、`Owned Paths`、`Interfaces: Consumes / Produces` 精确签名、允许操作与验收命令）；TL 派发 Subagent 时只传该文件指针路径，避免长 prompt 导致 Context Rot。Task 若修改 `.github/workflows/*`，简报默认提示 Writer 使用 `git push git@github.com:<owner>/<repo>.git HEAD:<branch>`，避免 `gh` OAuth token 缺少 `workflow` scope。
- 跨机器输入放 `~/Development/handoff/<goal>/inputs/`，附清单和每个文件的 SHA-256；缺文件或 SHA 不符时记 `INPUT_BLOCKED`，不凭记忆补。
- 回执（R10）放 `handoff/<goal>/` 下或目标仓允许的位置，台账只放路径；Subagent 向主会话只返回 `<15` 行精简结论。

## 格式（v1）

规则：**只追加，不改旧行**；纠正就追加新事件。不放代码、长报告全文（只放路径）、密钥、讨论过程。

### 文件头（人读）

```markdown
# Goal: <goal>
- 目标: …
- 验收: <可检查的验收标准，逐条>
- Owner: tianpei
- 协调者 bot: <bot>
- TL: <herdr 名> / 模型 <model>
- 机器: Mac mini | MacBook
- 合同版本: goal-task@<sha>（兼容 goal-target-task@<sha>）

## Tasks
| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |
|---|---|---|---|---|---|

## Events
```

`## Tasks` 状态表是快照，可改写（状态取值按 R8，`1 Task = 1 Writer = 1 Worktree = 1 PR`；历史台账中的 `Target` 条目直接映射为 `Task`）；`## Events` 以下只追加。

### 事件行（机器可解析）

一行一条，以 `- ` 开头，字段用 ` | ` 分隔；第一个字段是带时区的 ISO 8601 时间，其余为 `key=value`：

```
- 2026-10-21T15:00:00+08:00 | EVT=TASK_BLOCKED | id=E7 | task=TK1a | from=merge-tk1a-writer-r1 | to=merge-goal-tl-r1 | ref=handoff/merge/r1.md | note=缺 Figma 导出
- 2026-10-21T15:12:00+08:00 | EVT=RESOLVED | id=E7 | from=merge-goal-tl-r1 | note=已放到 inputs/
- 2026-10-23T11:30:00+08:00 | EVT=RULING | id=E8 | task=TK1b | from=merge-goal-tl-r1 | ref=handoff/merge/briefs/TK1b.md | note=decision=允许顺带更新 adapter.ts 签名; why=同目标内非公共适配文件漏列; blast_radius=仅 TK1b 单文件无红线影响
- 2026-10-26T22:55:00+08:00 | EVT=RESOLVED | id=B1 | from=joy | to=merge-goal-tl-r1 | note=旁路解卡：Allow path access on /tmp; send-keys enter
```

| 字段 | 必填 | 说明 |
|---|---|---|
| 时间 | 是 | 写入时刻的真实系统时钟，ISO 8601 带时区（`date -Iseconds` 或 `date '+%Y-%m-%dT%H:%M:%S%z'`；`+08:00` 或 `Z`）。严禁估算、顺延或回填 |
| `EVT` | 是 | 事件类型，见下表 |
| `id` | 是 | 命名空间内唯一。TL／`goal-queue.py` 任务事件用 `E1`…（脚本只扫描 `id=E<N>`）；协调者 Bot 旁路巡检／解卡用 `B1`… 或 `J1`…。关闭用同一 id |
| `task` | 建议 | Task ID（`1 Task = 1 PR`） |
| `from` / `to` | 建议 | 发出者 / 该接的人（herdr 名或 bot 名） |
| `ref` | 否 | 分支、PR、SHA、报告路径 |
| `model` / `machine` / `contract` / `tokens` | 回执必填 | 实际模型（降级时写 `原→新`）、机器、合同 SHA、大致 token |
| `note` | 否 | 一句话，值中不出现 ` | ` |

时间取写入当时的时钟，不事后补。TL 推进事件与 Bot 旁路事件分号段：`E*` 与 `B*`／`J*` 不要混用，避免 `E5` 与另一条 `E5` 撞号。Bot 事件不占用 `goal-queue.py` 的 `E*` 计数。

## 标准事件与决定权

| EVT | 含义 | 谁发 | 接收者 | 谁有权决定／关闭 |
|---|---|---|---|---|
| `DISPATCHED` | 派发并确认 `working` | TL／协调者 | — | 记录 |
| `READY` | Task 满足 R7 与依赖，可派 | TL | — | 记录 |
| `HOT_APPEND` | TL 运行期间通过 `goal-queue.py enqueue` 热追加新 Task 并自动计算与在途任务的并行／串行依赖边 | 协调者 bot／TL | TL | 记录（TL 在波次收口前 `next-wave` 接续排空） |
| `TASK_BLOCKED` | 任务卡住 | 任何执行者 | TL → 协调者 | TL／协调者；涉及范围按 R2 |
| `INPUT_BLOCKED` | 缺跨机器输入或 SHA 不符 | 任何执行者 | 协调者 | 协调者（补 inputs） |
| `RULING` | 协调者内联裁决：写者回报 `NEEDS_RULING` 后，对不触碰 4 条硬红线的同目标顺带缺口（如漏列 1–2 个辅助／适配文件、非公共接口微调）当场放行，免重规划（`note` 含 `decision + why + blast_radius`） | TL／协调者 | 写者 | TL／协调者（仅限非红线顺带缺口） |
| `CHANGE_REQUEST` | 超出 `RULING` 边界或触碰硬红线，需要改范围／合同 | 执行者／TL | TL → 协调者 | 按 R2／R4 分级；Goal 变更只有 Owner |
| `REVIEW_PASS` | 审查通过（V5 PASS／CONCERNS） | Reviewer | TL | 记录 |
| `REVIEW_FAIL` | 审查不通过 | Reviewer | TL | TL 按 V3 决定修复或回定义方 |
| `PR_MONITOR` | `pr_monitor` Subagent 回报 PR CI／auto-merge 终态（`PR_MERGED`、`PR_CHECKS_GREEN`、`PR_CHECK_FAILED`、`PR_CONFLICT`）及 R10 回执 | `pr_monitor` | TL | `PR_MERGED`／`PR_CHECKS_GREEN` 记录收口；`PR_CHECK_FAILED`／`PR_CONFLICT` 由 TL 派 Writer 修复 |
| `NEED_GO` | 需要 Owner 放行：危险操作或明确 hold 例外 | TL／协调者 | 协调者 → Owner | **只有 Owner** |
| `NEED_OWNER_DECISION` | 需要 Owner 拍板 | 协调者 | Owner | **只有 Owner** |
| `DONE` | agent／Task 完成，回执已落盘 | 执行者／TL | TL | 记录 |
| `RESOLVED` / `ACK` | 已处理／有人在跟 | 有决定权者 | — | 关闭同 id |

- `NEED_GO`、`NEED_OWNER_DECISION` 只能由 Owner 的答复关闭，在 `note` 记 Owner 原话或消息来源；bot 转述不算放行。普通 PR 不因按既定 auto-merge 条件合并而新建 `NEED_GO`；普通合并与发布（04/tag/TestFlight）不因该原因新建 `NEED_GO`。此前有效的 Owner 授权可按其原始来源复用并记录，不因 bot 转述而产生新授权。
- 同一 id 关闭后又追加需处理事件 = 重新打开。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`ledger.md`、`briefs/<task>.md` 与 Git 实际状态是跨进程唯一事实源（Single Source of Truth）；各 CLI 的原生 Goal／Todo 引擎作为进程内持续推进引擎挂载，其状态必须与 `ledger.md` 的 `## Tasks` 表和 `## Events` 同步：

| `ledger.md` R8 状态 / 事件 | Codex CLI（`create_goal` / `update_goal`） | Copilot CLI（`sql` `todos` + `todo_deps`） | Claude Code（`TodoWrite` / `TaskCreate`） |
|---|---|---|---|
| Phase 1 启动 / Task 派发（`EVT=DISPATCHED`） | 首个动作调用 `create_goal(objective="...")` 绑定 `ledger.md`（TL）或 `briefs/<task>.md`（Writer／Reviewer） | 将 `## Tasks` 与依赖 DAG 镜像写入 `todos`（`pending`/`in_progress`）与 `todo_deps` | 将活动 `Tasks` 投影到 `TodoWrite`（`pending`/`in_progress`） |
| 执行与审查中（`in_progress` / `in_review` / `changes_requested`） | 保持 active goal 持续推进，不提前调用 `update_goal(status="complete")` | 同步更新对应行 `status = 'in_progress'` | 同步更新对应项 `in_progress` |
| 完成交接并落盘回执（`done` / `EVT=DONE`） | 核实全部验收命令、PR 交接／合并及磁盘 R10 回执落盘后，调用 `update_goal(status="complete")` | 更新对应行 `status = 'done'` 并解锁下游依赖 Task | 更新对应项 `completed` |
| 阻塞或需裁决（`blocked` / `EVT=TASK_BLOCKED` / `NEED_GO` / `NEED_OWNER_DECISION` / V3 2 轮失败） | 立即调用 `update_goal(status="blocked")` 释放循环，严禁留在活动态空转 | 更新对应行 `status = 'blocked'` | 保留未完成并标注阻塞原因 |

### 会话内追问与 Bug 反馈守卫（Sticky TL & 3-Probe Diagnostic Cap）

- 一旦会话进入 `goal-task` 或存在活动的 `~/Development/handoff/<goal>/ledger.md`，后续每轮用户追问或口语化 Bug 反馈（如 `"还是点不动"`、`"还没有找到原因吗"`、`"改一下配色"`）自动继承 `TL (Coordinator)` 约束，无需用户再次输入 `/goal-task`。
- TL 在会话内最多执行 `≤3` 次只读探查定位受影响 Task 与文件路径；若需超过 3 次工具调用的深入逆向排查或**任何文件修改**（含 `handoff/` 原型、规格说明或仓库业务代码），TL 必须通过 `herdr` 派发 `Explorer` 或 `Writer`（`<goal>-<task>-writer-rN`），并在 `ledger.md` 追加 `EVT=DISPATCHED` 与后续 `EVT=DONE`／`EVT=TASK_BLOCKED` 记录，严禁在 TL 主会话内直接编辑文件。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

[`scripts/goal-queue.py`](../scripts/goal-queue.py) 以 `~/Development/handoff/<goal>/.queue.lock`（`fcntl.flock`）保护 `queue.json` 与 `ledger.md` 的原子同步：

1. **姿势 1（Batch Mode）**：`goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json ...` 对照在途与待办任务的 `(repo, paths)` 自动推导冲突依赖（`auto_dependencies`），将跨仓／路径不相交任务分入**并行波次（Parallel Wave）**，同仓路径重叠任务串入**串行链（Sequential Chain）**，更新 `## Tasks` 表并追加 `EVT=READY`；`TL` 循环执行 `goal-queue.py next-wave --mark-in-progress` 直至 `drained: true`。
2. **姿势 3（Hot-Append Mode）**：当存在 `in_progress` 任务时，常驻域主 Bot（`Joy`／`Atlas`／`flower`）收到新任务**不另起重复 TL**，直接调用 `goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> ...` 写入 `queue.json` 并在 `ledger.md` 记录 `EVT=HOT_APPEND`；若 `TL` 处于 `idle`，Bot 用 `herdr agent prompt <goal>-goal-tl-r1 ...` 唤醒 `TL`，且 `TL` 在每波收口退出前必跑一次 `goal-queue.py next-wave`，确保热追加队列排空至零。

### Feedback-tracked queue contract

反馈型 Goal 使用 `goal-queue.py enqueue --feedback-manifest <json-string-or-file>`。Manifest schema 1 的最小结构如下：

```json
{
  "schema_version": 1,
  "entries": [
    {
      "feedback_id": "FB-001",
      "acceptance": [
        {"acceptance_id": "FB-001-A1", "text": "Synthetic acceptance criterion"}
      ]
    }
  ],
  "task_mappings": [
    {
      "task_id": "TK1",
      "feedback_ids": ["FB-001"],
      "acceptance_mapping": [
        {"feedback_id": "FB-001", "acceptance_ids": ["FB-001-A1"]}
      ],
      "common_root_cause_evidence": {
        "summary": "Required for multiple feedback IDs in one Task",
        "references": ["test://synthetic/root-cause"]
      }
    }
  ]
}
```

The queue validates exact Task, feedback, and acceptance coverage plus unique terminal ownership on every `enqueue`, `next-wave`, and `set-status` call. Supersession ownership is resolved through the complete `superseded_by` chain independent of manifest order; cycles, invalid links, and a chain with no active owner are rejected unless the terminal cancellation has explicit authorization. A multi-feedback Task requires declared grouping evidence; this is structural evidence only and does not establish semantic common-root-cause truth. A queue without `feedback_manifest` remains legacy-compatible and reports `feedback_validation=unverified_legacy`; a null or stripped manifest on a queue containing tracked task fields is rejected and must not be described as verified.

Use `enqueue --replacement-of <Task ID>` for a new 1:1 replacement. The replacement must carry the same original feedback ID(s) and every original acceptance ID/text; later manifests may not delete, rewrite, or remap existing entries or another Task's acceptance. Automatic path-serialization edges are recorded as `auto_depends_on` and may unblock after cancellation; explicit functional dependencies remain blocked until the dependency or its completed replacement is delivered. Reopening a Task clears stale terminal metadata and acceptance evidence.

Tracked `done` and `cancelled` transitions require `--evidence-json` with one structured item per original acceptance. `done` requires `status=passed` for all items. Supersession requires `--superseded-by`, `status=replaced`, matching `replacement_task_id` values, and a replacement manifest that carries every original acceptance. Authorized cancellation requires `--authorization` and `status=cancelled`. The local validator does not verify GitHub merge state or remote PR facts.

## 超时提醒（暂不启用）

- 现行做法：Owner 问时，由 Hotty 扫各 Goal 台账，列出未被 `RESOLVED`／`ACK` 关闭的 `TASK_BLOCKED`、`INPUT_BLOCKED`、`CHANGE_REQUEST`、`REVIEW_FAIL`、`NEED_GO`、`NEED_OWNER_DECISION` 及等待时长。
- 自动巡检脚本已备在 `~/Development/handoff/_watch/`（规则：未关闭 ≥10 分钟提醒，同一事件最多 3 次、间隔 ≥30 分钟，23:00–08:00 Asia/Shanghai 不发），**未安装**；启用需 Owner 另行同意。
