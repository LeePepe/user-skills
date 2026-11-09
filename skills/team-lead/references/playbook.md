## §0 Panorama & 4-Layer Product/Output Matrix

本手册由 [`workflow-hub`](../../workflow-hub/SKILL.md) 统一编排；环 1 使用 [`linear-board`](../../linear-board/SKILL.md)，环 7a 使用 [`retro-loop`](../../retro-loop/SKILL.md)，环 7b 使用 [`story-craft`](../../story-craft/SKILL.md)。采用按需渐进展开（Progressive Disclosure）防止上下文膨胀：
- **§1.5 Herdr 7-step SOP 与 Socket 监听**：[`playbook-herdr-sop.md`](./playbook-herdr-sop.md)
- **§2.2–2.5 双路复盘模板与三道物理防回归锁**：[`playbook-retro-loop.md`](./playbook-retro-loop.md)
- **§3.1–3.3 四元素叙事抽取单与脱敏流水线**：[`playbook-narrative-extract.md`](./playbook-narrative-extract.md)
- **配套资产**：[`交互式全景飞轮图`](../assets/ai-native-flywheel.html) · [`跨机推进路线图`](./workflow-refine-roadmap.md)

自治调度遵循 [`workflow-hub` 的 Cron / `launchd` 心跳](../../workflow-hub/SKILL.md)：**Precheck-First, Wake-on-Actionable**，先做零 Token 确定性预检，只有发现可执行事件时才唤醒 Bot 或 Sticky TL。

### 0.1 三大板块闭环与四层产品产出矩阵

1. **§1 Build & Ship**（`Goal → Task → CLI/Herdr → 6DQ → 发布读回`）：只以可验收产出证明进展。
2. **§2 Retro & Dual Feedback Loop**（项目回路 A ＋工程回路 B）：可定位事实、时间线与反事实检验转化为 Task、底座升级或三锁。
3. **§3 Summarize, Extract & Narrate**（抽取单 → 三种叙事 → 脱敏发布）：保留量化证据、去标识化、抽象机制并按发布门禁读回。

| 层 | 产品／系统范围 | 核心产出 | 验收读回与回流入口 |
|---|---|---|---|
| **层 1：业务与产品应用层** | 原生与全栈应用（音频、健康、财务、看板等） | 功能切片、PLT/FPS、Crash-Free、用户体验指标 | E2E 证据、PLT P50/P90/P99、Crash-Free → 项目回路 A |
| **层 2：共享工程与设计底座** | `shared-design-system`、`shared-telemetry`、`shared-ci`、`AppleUITesting` | 跨端一致性、遥测数据流、6DQ 门禁、可复用组件 | 契约检查、组件快照/UI 测试、6DQ 结果 → 底座升级 Task 与三锁 |
| **层 3：研发与协同工作流** | `01–06` 工作流与 `Goal -> Task` 两层任务合同 | 高吞吐 PR 交付链、准确 head、R10 证据回执 | `briefs/<task>.md`、R10、Git SHA、发布读回 → 工程回路 B |
| **层 4：知识复利与叙事产出** | 工程故事集、技术深潜 Blog、专题 Deck、复盘档案 | 可复用工程认知、公开叙事作品、下一轮约束 | 四元素抽取单、隐私脱敏检查、线上页面读回 → 三种叙事模板 |

---

## §1 Build & Ship — 从两层切片、CLI/Herdr 到 6DQ 发布读回

### 1.1 `Goal -> Task` 两层模型与 Linear 对齐

- **Pre-Phase-0 Idea triage**：未冻结 Idea 记入 Linear Team `TIA`（`linear-sync.py idea`）；Phase 1 调 `ensure-goal` 绑定 `TIA-xx`，以本地 `ledger.md` 为唯一权威源。
- **Goal（`planning → in_progress → accepting → done | blocked | cancelled`）**：声明最终可观察结果、不做项、端到端验收命令、依赖 DAG 与红线路径；`1 Goal = 1 Branch / 最多 1 PR`。
- **Task（`planned → ready → in_progress → in_review → done | changes_requested | blocked | cancelled`）**：垂直演进切片（或 `Expand → Migrate → Contract`），brief 含 `Background / Owned Paths / Interfaces / Dependencies / Acceptance / Handoff`。

### 1.2 设计驱动开发与三档弹性执行模式

- **三层设计契约**：上游 `shared-design-system/ai/DESIGN-CONTRACT.md` → 项目 `docs/design-contract.md`（四态＋双外观）→ 静态门禁 `shared-ci/scripts/context/design_contract.py`。`Steering` 加载 `my-designer` 产出 `design.md`（`writes: handoff-only`），`Writer` 直接在产品代码中实现（Zero Demo）。
- **三档弹性模式**：**Inline Mode**（`≤3` 文件轻量直改，TL 在独立 Worktree 内直接改完验证）· **Single-Stream Mode**（同 Goal 共用 1 个分支，派 1 个 `Writer` 连续推进，收口 1 次跨厂商审查）· **Parallel Wave Mode**（无冲突并发子 Worktree 验证后本地 `keep` 合回）。
- **冻结与裁决**：同目标内顺带文件遗漏或非公共接口微调且不触四红线时追加 `EVT=RULING`；第 1 轮 V5 `FAIL` 原写者修，第 2 轮升档 `writer_escalated`，仍败转 `blocked`。
- **CLI 原生 Goal 引擎挂载与 Sticky TL**：`ledger.md` 为跨进程事实源，同步挂载 Codex `create_goal`/`update_goal`、Copilot `sql` `todos`/`todo_deps` 或 Claude `TodoWrite`。Sticky TL 会话内追问自动继承合同，只读诊断上限 `≤3` 次。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

通过 [`scripts/goal-queue.py`](../scripts/goal-queue.py) 在 `~/Development/handoff/<goal>/.queue.lock` 排他锁下同步维护 `queue.json` 与 `ledger.md`：
1. **姿势 1（Batch Mode）**：`goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json <json>` 自动推导 `(repo, paths)` 并行/串行波次（`EVT=READY`）；TL 循环 `next-wave --mark-in-progress` 派发并 `set-status --status done` 直至 `drained: true`。
2. **姿势 3（Hot-Append Mode）**：运行中追加任务绝不开第二个 TL，直接 `enqueue` 记 `EVT=HOT_APPEND`；若 `<goal>-goal-tl-r1` 处于 `idle` 则 `herdr agent prompt` 唤醒，波次退出前重跑 `next-wave`。

### 1.5 CLI/Herdr 7-step SOP（摘要与按需展开）

完整七步命令流、显式 allowlist 与 Node.js Socket 订阅脚本见 [`playbook-herdr-sop.md`](./playbook-herdr-sop.md)（推荐直接调 [`scripts/herdr-dispatch.sh`](../scripts/herdr-dispatch.sh)）：
- **Step 1–4 分屏与派发**：核对 `HERDR_*` ID → `herdr pane split --pane "$HERDR_PANE_ID" --direction right|down --cwd "$TASK_WORKTREE" --no-focus` → `herdr agent start`（`--` 后严禁重复传 `codex`/`copilot` 二进制名）→ 文件指针派发。
- **Socket 阻塞告警与解卡边界**：`goal-supervisor.py notify-bridge` 仅在 `status == "blocked"` 时写入 `handoff/<goal_folder>/alerts/blocked-<agent>-<ts>.md` 供协调者 bot 读取。**通知桥 ≠ 解卡**（不自动发按键，不处理 `working`/`idle`/`done`）；**GOAL_DONE 关窗与 blocked alerts 两条独立**。
- **Step 5–7 对账与回收**：对账顺序 `磁盘 R10 回执 → Git SHA → CI/发布读回 → Herdr 状态`。PR 开启后由 Layer-0 `ci-monitor.py`（失败诊断回退 `pr_monitor`）做 Job 级 Fail-Fast。`PR_MERGED` 且 R10 落盘后执行 `git worktree remove "$TASK_WORKTREE"` 清理已合并工作树；记录 `EVT=GOAL_DONE` 后关闭 TL pane/workspace（保留未合并/`HOLD` Worktree 不阻断关窗）。

### 1.6 6DQ 质量门禁与自动化发布读回

6DQ 是帮助 Agent 在开发过程中由浅入深、渐进式发现问题的诊断阶梯（**Progressive Discovery Ladder**），而不是在一开始就束缚 Agent 的前置一刀切教条（**Not an upfront all-at-once constraint**）：

1. **Stage 1 — D1（动工轻检：环境与 Worktree 隔离）**：秒级分支、路径与隔离确认。
2. **Stage 2 — L1（内环快反：单测与静态检查）**：最小代码切片后立刻跑局部单测与 lint/typecheck。
3. **Stage 3 — L2（边界探查：本地真实集成）**：对接真实序列化、存储和接口边界夹具。
4. **Stage 4 — G2（交接扫描：安全与契约对齐）**：提 PR 前运行秘密扫描、`design_contract.py` 与 `sync_contract.py --check`。
5. **Stage 5 — L3 + Release Readback（外环终验：端到端旅程与线上读回）**：验证关键 E2E、`release_sync.py version-sync|readback`、`/api/live`、`SHA256SUMS` 与 TestFlight 真实读回。

---

## §2 Retro & Dual Feedback Loop（摘要 → [`playbook-retro-loop.md`](./playbook-retro-loop.md)）

- **双路总览与停止线**：**回路 A（项目与产品反馈：Telemetry / Crash / 体验巡检）** 与 **回路 B（工程体系反馈：Workflow / PR / Infra 五步证据链）** 平行运行。先冻结证据边界、找 **Earliest Load-Bearing Fork**、做 **H1–H5** 可反驳假设检验与反事实门槛核对，严禁把“模型偷懒”当根因。
- **三道物理防回归锁**：结论必须落为可执行 Task 或三锁之一（**锁 1** `user-skills` 认知合同 · **锁 2** 本地 Hook/Herdr 守卫 · **锁 3** `shared-ci` 6DQ/契约/组件门禁）。完整诊断表、记录模板与结果分级见 [`playbook-retro-loop.md`](./playbook-retro-loop.md) 及 [`retro-loop`](../../retro-loop/SKILL.md)。

---

## §3 Summarize, Extract & Narrate（摘要 → [`playbook-narrative-extract.md`](./playbook-narrative-extract.md)）

- **四元素抽取单与三大体裁**：从交付事实与复盘结论抽取 **《Story & Tech Extraction Sheet》**（`Hook Scene` · `Quantitative Delta` · `Counter-Intuitive Pivot` · `Takeaway Mechanism` + `Desensitization`），按受众进入 **Story Series**、**Tech Deep-Dive Blog** 或 **Sharing / Deck**。
- **脱敏硬红线与发布读回**：公开稿严禁出现个人姓名、账号、机器名、私有域名、绝对路径、密钥或客户数据；经 `ai-workflow/lab/_build.py` 构建与线上读回后归档。完整抽取单模板与脱敏清单见 [`playbook-narrative-extract.md`](./playbook-narrative-extract.md) 及 [`story-craft`](../../story-craft/SKILL.md)。
