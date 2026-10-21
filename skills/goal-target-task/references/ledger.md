# Goal 台账（ledger）与标准事件

台账是一个 Goal 的唯一正式状态源：谁在什么时候做了什么、现在卡在哪。`goal.md`、看板、TL 报告都是从它派生的视图，不另存状态。合同规则（R2、R8、R10）见 [SKILL.md](../SKILL.md)，责任线见 [bots-and-agents.md](bots-and-agents.md)。

## 位置

- `~/Development/handoff/<goal>/ledger.md`，一个 Goal 一本，放 handoff，不放目标仓库。两台机器各放各自 Goal 的台账。
- `<goal>` 目录名即 Goal 短名；以 `_` 开头的目录（如 `_watch`）不是 Goal。
- 跨机器输入放 `~/Development/handoff/<goal>/inputs/`，附清单和每个文件的 SHA-256；缺文件或 SHA 不符时记 `INPUT_BLOCKED`，不凭记忆补。
- 回执（R10）放 `handoff/<goal>/` 下或目标仓允许的位置，台账只放路径。

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
- 合同版本: goal-target-task@<sha>

## Targets / Tasks
| ID | 类型 | 标题 | 负责 | 状态 |
|---|---|---|---|---|

## Events
```

Targets／Tasks 状态表是快照，可改写（状态取值按 R8）；`## Events` 以下只追加。

### 事件行（机器可解析）

一行一条，以 `- ` 开头，字段用 ` | ` 分隔；第一个字段是带时区的 ISO 8601 时间，其余为 `key=value`：

```
- 2026-10-21T15:00+08:00 | EVT=TASK_BLOCKED | id=E7 | task=TK1a | from=merge-tk1a-writer-r1 | to=merge-goal-tl-r1 | ref=handoff/merge/r1.md | note=缺 Figma 导出
- 2026-10-21T15:12+08:00 | EVT=RESOLVED | id=E7 | from=merge-goal-tl-r1 | note=已放到 inputs/
```

| 字段 | 必填 | 说明 |
|---|---|---|
| 时间 | 是 | ISO 8601，带时区（`+08:00` 或 `Z`） |
| `EVT` | 是 | 事件类型，见下表 |
| `id` | 是 | 台账内唯一事件号（`E1`…）；关闭用同一 id |
| `task` | 建议 | Target／Task ID |
| `from` / `to` | 建议 | 发出者 / 该接的人（herdr 名或 bot 名） |
| `ref` | 否 | 分支、PR、SHA、报告路径 |
| `model` / `machine` / `contract` / `tokens` | 回执必填 | 实际模型（降级时写 `原→新`）、机器、合同 SHA、大致 token |
| `note` | 否 | 一句话，值中不出现 ` | ` |

## 标准事件与决定权

| EVT | 含义 | 谁发 | 接收者 | 谁有权决定／关闭 |
|---|---|---|---|---|
| `DISPATCHED` | 派发并确认 `working` | TL／协调者 | — | 记录 |
| `READY` | Task 满足 R7 与依赖，可派 | TL | — | 记录 |
| `TASK_BLOCKED` | 任务卡住 | 任何执行者 | TL → 协调者 | TL／协调者；涉及范围按 R2 |
| `INPUT_BLOCKED` | 缺跨机器输入或 SHA 不符 | 任何执行者 | 协调者 | 协调者（补 inputs） |
| `CHANGE_REQUEST` | 需要改范围／合同 | 执行者／TL | TL → 协调者 | 按 R2／R4 分级；Goal 变更只有 Owner |
| `REVIEW_PASS` | 审查通过（V5 PASS／CONCERNS） | Reviewer | TL | 记录 |
| `REVIEW_FAIL` | 审查不通过 | Reviewer | TL | TL 按 V3 决定修复或回定义方 |
| `NEED_GO` | 需要放行：合并、发布、危险操作 | TL／协调者 | 协调者 → Owner | **只有 Owner** |
| `NEED_OWNER_DECISION` | 需要 Owner 拍板 | 协调者 | Owner | **只有 Owner** |
| `DONE` | agent／Task 完成，回执已落盘 | 执行者／TL | TL | 记录 |
| `RESOLVED` / `ACK` | 已处理／有人在跟 | 有决定权者 | — | 关闭同 id |

- `NEED_GO`、`NEED_OWNER_DECISION` 只能由 Owner 的答复关闭，在 `note` 记 Owner 原话或消息来源；bot 转述不算放行。
- 同一 id 关闭后又追加需处理事件 = 重新打开。

## 超时提醒（暂不启用）

- 现行做法：Owner 问时，由 Hotty 扫各 Goal 台账，列出未被 `RESOLVED`／`ACK` 关闭的 `TASK_BLOCKED`、`INPUT_BLOCKED`、`CHANGE_REQUEST`、`REVIEW_FAIL`、`NEED_GO`、`NEED_OWNER_DECISION` 及等待时长。
- 自动巡检脚本已备在 `~/Development/handoff/_watch/`（规则：未关闭 ≥10 分钟提醒，同一事件最多 3 次、间隔 ≥30 分钟，23:00–08:00 Asia/Shanghai 不发），**未安装**；启用需 Owner 另行同意。
