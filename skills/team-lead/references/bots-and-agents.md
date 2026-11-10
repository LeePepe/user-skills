# Bot 与 CLI agent：分工与生命周期

本文件定义本环境（Owner tianpei）的分工表与 CLI agent 生命周期规则；合同角色、权限与审查以 [SKILL.md](../SKILL.md) 为准，模型与启动参数只看 [runtime-profiles.yaml](runtime-profiles.yaml)，台账与事件见 [ledger.md](ledger.md)，完整 CLI 命令配方见 [dispatch-recipes.json](../../team-lead/references/dispatch-recipes.json)。`M2-Dispatcher` 与 Goal-level TL 的生命周期合同另见 [`m2-dispatcher`](../../m2-dispatcher/SKILL.md)。

## 两层与 Linear 绑定

| 层 | 是什么 | 谁开 | 活多久 |
|---|---|---|---|
| bot 层 | 有独立聊天的常驻 bot，各自固定一台机器 | Owner | 长期 |
| CLI agent 层 | bot 或 TL 在 herdr 里启动的 CLI 会话（TL、Reviewer、写者、Explorer） | 协调者 bot / TL | 用完即关 |

Linear `TIA` Idea 收集、Goal 绑定与状态同步由 `linear-sync.py` 实现，本地 `ledger.md` 保持唯一权威源（见 [`linear-board`](../../linear-board/SKILL.md) 与 [`ledger.md`](ledger.md)）。

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

Phase 0 只读探查、Owner 对齐与 `spec.md` 冻结候选不得创建业务 Worktree 或派 Writer；Owner 明确 GO 后进入 Phase 1。Phase 1 中，常驻 `Grok Bot`、`Cindy` 或交互式 CLI 均作为 **`M2-Dispatcher`** 遵守**只写简单 Spec 边界（仅可改 `spec.md`/`design.md` 及 handoff 简报，严禁改任何代码或其他仓库文件）**，凡涉及代码或其他文件修改一律通过 `herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude` 在后台拉起绑定 `Magpie (:3425)` `group/steering` 的独立 Goal `Steering`（`<goal>-goal-tl-r1`），在独立 Worktree（`~/Development/worktrees/<task>`）中由 `Steering`（`≤3` 文件轻量改动）或 `Writer`（`--role writer` → `group/coding`）完成修改与提交（Cindy 中须同步运行 `goal-supervisor.py watch --interval 15 --max-wait 600 --json` 盯完解卡与完结）。

**Single-Steering per Goal rule:** once `<goal>-goal-tl-r1` is running (or when a local CLI session executes a `≤3`-file inline task directly), that active `Steering` (TL) session is the sole `Steering` (TL) for the Goal and never spawns a second `Steering` or standalone `Planner` subagent. `M2-Dispatcher` starts at most one `<goal>-goal-tl-r1` in Herdr (reusing/waking it idempotently if already alive); that `Steering` then dispatches leaf `Writer`／`Reviewer` agents as needed, with optional read-only `Explorer` support for noisy searches.

<a id="design-routing-machine-split"></a>
<a id="bot-与-cli-的设计路由-owner-2026-10-24"></a>

### Multi-Goal Lease Watchdog and Contract Telemetry

跨 Goal 监督使用 `goal-supervisor.py scan`，按角色 TTL 检测 `BLOCKED_AGENT`、`ZOMBIE_LEASE` 与 `ORPHAN_TL_WAIT`；`check-pins` 与 `telemetry --write` 负责 upstream drift 和合同遥测。等待子 Agent 只用 30–60s 有界轮询（如 `herdr agent wait <name> --timeout 60000`），并以 `ledger.md`、agent 状态和 `receipts/` 对账。监督关联同时考虑 `-goal-tl-` 短名、台账身份和 Goal handoff cwd（如 `good-des-*`／`gd-e1-*` → `good-designer`）；`/Applications/Xcode.app` 与 `/Volumes/SSD` 必须在 allowlist。

**Blocked notify bridge**：主触发是 `pane.agent_status_changed` 且 `status=blocked`，巡逻保底是 `goal-supervisor.py scan --write-alerts` 的 `BLOCKED_AGENT`；两者写入 `handoff/<goal_folder>/alerts/blocked-<agent>-<ts>.md`。`ledger.md` 的 `协调者 bot` MUST 在巡逻或入站信号时读取 alert、检查 visible prompt，并作 Tier A/A′ 决定或 Tier B `NEED_GO`。**通知桥 ≠ 解卡**：桥只通知，严禁 auto-Enter 或把 `blocked` 当作完成。**GOAL_DONE 关窗与 blocked alerts 两条独立**。

### UI 设计与技术栈 Skill 挂载（Steering / Writer 直接加载，无独立设计子代理）

UI/UX design is handled by `Steering` during Phase 0 co-design and by `Writer` during Phase 1 implementation. Steering loads `my-designer` (`L0 -> L1 -> L2`) directly when authoring `design.md`/`spec.md` (`Package.resolved` 与 `shared-design-system` upstream primitives 按需核对)；Writer implements directly in product or `shared-design-system` code, runs `shared-ci/scripts/context/design_contract.py`, and exports low-cost real screenshots (Apple: `XCTest` snapshot / `xcrun simctl io booted screenshot` / `ImageRenderer` 4 states × 2 appearances PNG; Web: Playwright headless screenshot) into the R10 receipt (Writer implements declared constraints only, never guessing undeclared rules). The CI Design Gate (`design_contract.py`) and Cross-Vendor `Reviewer` (`code-review` + `my-designer` **Visual Traceback**) verify the diff and rendered screenshots (including `sips -g pixelWidth`, zero numeric truncation `…`, and the `followed the brief verbatim` V2 P1 rule): `[IMPL_BUG]` routes directly back to Writer, whereas `[SPEC_GAP]` requires `Steering` to amend `design.md` (`spec_sha`) first before Writer implements the delta (sinking via `skill_contract.py sink-rule` / `register-case --id auto` only when cross-project generic, zero private symbols, and recurring on the same container).

- **Spec-First Requirement Change Rule（规格先行）**：已有代码与测试的仓库一旦修改产品需求或细化 UI/UX，`spec.md`／`design.md`（及仓库 `specs/` 或 `docs/design-contract.md`）就是唯一上游权威；unit／snapshot tests 是派生物，不得锁定 obsolete UI 或行为。`Steering`（或按需 `Explorer`）映射当前实现和冲突 legacy test assertions，产出带精确 `file:line` 的 **Obsolete Test Manifest**，并记录 **Deprecated Test Clauses to Remove/Rewrite**；随后 `Code Writer` 先迁移获授权的 deprecated assertions，再改代码，`Code/UI Reviewer` 保留非 deprecated regression tests。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

常驻域主 Bot 与 `TL` 通过 [`scripts/goal-queue.py`](../scripts/goal-queue.py) 协同处理批量输入与运行中热追加；队列以 `.queue.lock` 原子更新 `queue.json`／`ledger.md`，按 `(repo, paths)` 冲突矩阵自动形成并行或串行波次：
1. **Batch Mode**：`enqueue --batch-json` → TL 循环 `next-wave --mark-in-progress` → `set-status --status done`，直至 `drained: true`。
2. **Hot-Append Mode**：已有 TL 时绝不重复开 TL；使用 `enqueue` 记录 `EVT=HOT_APPEND`。TL 为 `idle`／`done` 时 prompt 唤醒后若仍未工作，仅补一次 `herdr agent send-keys "$GOAL-goal-tl-r1" enter`，并在波次收口前重跑 `next-wave`。

### 两级完成与清理边界（`1 Goal = 1 分支 / 最多 1 PR` + 本地 `keep / drop`）

- **1-Hop Communication & Lifecycle Ownership**：独立 Goal TL 存在时，Bot 只沟通、唤醒、解卡并在 `GOAL_DONE` 时回收 TL pane/workspace；TL 独占下属 Subagent 的派发、更新与回收。
- **同 Goal 共用分支与按需子 Worktree**：同 Goal 下多个 Tasks 默认在同一分支连续 commit；仅在多 Writer 真并发或实验探索时建临时子 Worktree，验证后本地 `keep`（merge）合回 Goal 分支并立即清理临时 Worktree。
- **Writer 干完即退与 `PR_MERGED` 清理**：`Writer` 完成本地验证与交付后即退出并关闭自己的任务 pane。当临时子 Worktree 已本地 `keep` 合回或最终 PR 已读回 `PR_MERGED` 后，通过 Layer 0 `ci-monitor.py pr` 与 `git-gc.py task-clean`（执行 `git worktree remove "$TASK_WORKTREE"`、`git worktree prune` 与 `git branch -D "$TASK_BRANCH"`）完成清理。未合并／暂停（HOLD）或 dirty Worktree 保留并报告 Owner。
- **Goal / `GOAL_DONE`**：ledger 记录 `EVT=GOAL_DONE` 后，关窗与 Worktree 处置分开验收。**A（MUST）**：关闭该 Goal 的 TL pane 并清空其 herdr workspace（`herdr workspace close "$GOAL_WORKSPACE"`；TL 可延迟自闭 pane，未自闭则协调 Bot 回退关闭），不触碰 `w1`、Owner 会话或活动 Goal。**B（与 A 正交）**：未合并、HOLD 或 dirty Worktree 保留并报告 Owner；保留 Worktree MUST NOT 阻断 A。

### 会话粘性、上下文污染度阈值与原生 Goal 引擎挂载

- **Sticky TL & Context-Noise Threshold**：存在活动 ledger 时追问继续继承合同。已知模块或中小改动（`≤8` 个文件）由 Steering 直接读取诊断并写 `spec.md`／`briefs/<task>.md`；跨仓搜索、全量盘点 `Obsolete Test Manifest` 或长日志超过 8 个文件时，经 `herdr-dispatch.sh` 派只读 `Explorer`（返回 `<15` 行摘要与 R10 receipt）。`≤3` 个文件轻量修正由 `Steering` 直改，大范围或耗时构建交 `Writer`。
- **Native Goal Continuation Binding**：`ledger.md`、`briefs/<task>.md` 与 Git 是跨进程唯一事实源；Codex `create_goal`／`update_goal`、Copilot `sql` `todos`／`todo_deps`、Claude `TodoWrite` 仅作进程内投影。验收与 R10 完成后标 complete；遇阻塞或 V3 两轮失败立即标 `blocked`。

## 每个 Goal 的责任线、Skill 路由与网关角色

台账文件头写明 **Owner、协调者 bot、TL（herdr 名 + 模型）、机器、合同 SHA**，信号按 `写者/Reviewer/Explorer → TL → 协调者 bot → Owner` 传递。Owner 是危险操作及明确 hold 例外的唯一放行人；普通 PR 合并与发布在 required checks 与独立审查通过后按 auto-merge 流程合并，由 `ci-monitor.py` 读回。下属遵循 **1-Hop Flat Tree**（`You Do Not Dispatch Subagents`）。

| 任务技术栈 | Required Skills |
|---|---|
| SwiftUI 图表、数据联动与可视化 | `my-designer` + `design-studio`（按平台防火墙穿透 `apple-design-skill` 与 `impeccable` Native 子集） |
| SwiftUI 界面、布局与材质（iOS 26／macOS 26） | `my-designer` + `design-studio`（禁载 Web 专属 `taste-skill` 与 Tailwind/Shadcn 规则） |
| UI 视觉现代化与客观评审 | `visual-design-modernization` + `my-designer` |
| macOS / iOS SwiftPM 打包、测试与构建 | `xcode-cli` |

所有配额水位、缓存粘性与轻重意图路由由本机网关 **`Magpie (:3425)`** 的三组逻辑模型统一接管（不可达时按 [runtime-profiles.yaml](runtime-profiles.yaml) 回退）：

| 角色 | 逻辑组 / 档位 | 谁开 | 生命周期 |
|---|---|---|---|
| Steering / Team lead (`team_lead`) | `group/steering` (`strong`) | Owner / 协调者 bot | 整个 Goal（探查、规划、UI 设计、`≤3` 文件直改、调度与 `EVT=RULING`） |
| 写者（`writer`） / 升档写者（`writer_escalated`） | `group/coding` (`worker`) / `group/coding-escalated` (`mid`) | Steering | 多文件或耗时构建切片（干完即退；`writer_escalated` 仅限 V3 第 2 轮） |
| 代码 Reviewer (`code_reviewer`) | `group/reviewing` (`mid`，**与写者不同家**） | Steering | Goal 收口单次审查（若仓库 CI 已有 `codex-review-target` 则直接复用） |
| Explorer（可选） | `group/coding` (`worker`) | Steering | 仅 `>8` 文件高噪声跨仓搜索时按需启动 |
| CI / 发布监控（`ci-monitor.py` / `pr_monitor`） | 默认由 Layer 0 `ci-monitor.py`（0 Token）处理；失败诊断回退 `worker.pr_monitor` | Steering | 异步监控 PR CI、auto-merge 与发布流水线（Job 级 Fail-Fast） |

## 生命周期（herdr 7-Step 要点，完整命令见 [dispatch-recipes.json](../../team-lead/references/dispatch-recipes.json)）

```sh
set -euo pipefail
SPLIT_JSON="$(herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$TASK_WORKTREE" --no-focus)"
NEW_PANE_ID="$(printf '%s' "$SPLIT_JSON" | jq -er '.result.pane.pane_id')" && test -n "$NEW_PANE_ID"
skills/team-lead/scripts/herdr-dispatch.sh --name "merge-tk1a-writer-r1" --kind copilot --cwd "$TASK_WORKTREE" --direction right \
  --prompt "Read briefs/<task>.md; implement only Owned Paths; write R10 receipt to <receipt-path> and return <15 lines." --wait --timeout 600000 -- --model gpt-5.6-luna
herdr pane close "$NEW_PANE_ID"
git -C "$MAIN_REPO" worktree remove "$TASK_WORKTREE" && git -C "$MAIN_REPO" worktree prune && git -C "$MAIN_REPO" branch -D "$TASK_BRANCH"
```

1. **优先调用 [`scripts/herdr-dispatch.sh`](../scripts/herdr-dispatch.sh)**：自动校验 `--name`（`^[a-z][a-z0-9_-]{0,31}$`）、分屏（必传 `--direction right|down`，禁用裸 `--current`）、校验 `NEW_PANE_ID` 非空、注入 `trustedFolders`/`config.toml` 与叠加去重 `--add-dir`（含 `$CWD`、`~/Development`、`/Volumes/SSD` 及 handoff 父目录 realpath、`HERDR_EXTRA_ADD_DIRS`，满足 Parent ≥ Child），并处理 `agent_pane_busy` 重试与 5s stall 自动补 Enter。`--` 后只传 `[AGENT_ARG]...`，严禁重复传 `codex`／`copilot` 二进制名。
2. **文件指针派发与 PR 状态监控默认走 Layer 0（0 Token）**：Prompt 只传 `briefs/<task>.md` 与 R10 回执路径。PR 进入 CI／auto-merge／发布时，TL 后台异步启动 `python3 skills/team-lead/scripts/ci-monitor.py workflow|pr ...`，不为纯状态轮询启动 LLM；仅失败需诊断时回退 `worker.pr_monitor`。Writer 严禁 `gh run watch` 或 `sleep >= 30`。Job 级 Fail-Fast 盯 required job 的 `failure`／`timed_out`／`action_required`；Run 仍 `in_progress` 时用 `gh api repos/<owner>/<repo>/actions/jobs/<failed_job_id>/logs`（不用 `gh run view --log-failed`）。
3. **输出读取、Tier A/A′ 解卡与 R10 对账**：`working` 仅读 `--source visible --lines 40`；`idle`／`done` 读 `--source recent-unwrapped --lines 80`。发现 `blocked` 严禁直接 break 退出：立即读 visible 界面——**Tier A（Allow path access）** 属于临时目录、`/Volumes/SSD` 基线或 `extra_add_dirs` 时发 `herdr agent send-keys <name> enter` 解卡；**Tier A′（`CMD_CONFIRM`）** 仅当命令属白名单（`gh`、非破坏性 `git`、`python3`、`jq`、`herdr` 不含 `server stop`、`shasum`、`date`、`sleep`、`env`）时发 Enter（或 Down+Enter 选 remember）并记 `EVT=UNBLOCK`；敏感或破坏性操作保持 hold 并记 `EVT=NEED_GO`／`NEED_OWNER_DECISION`（`--no-ask-user` 不抑制此类弹窗，不开启 `--yolo`）。完成以磁盘 R10 回执与 Git SHA 对账为准。
4. **结束、Worktree 清理与 Shared UI 门禁**：收齐 R10 记 `DONE` 后 `herdr pane close "$NEW_PANE_ID"`；`PR_MERGED` 后执行 `git worktree remove "$TASK_WORKTREE"` 清理；`EVT=GOAL_DONE` 必关 Goal workspace/TL pane（保留未合并／暂停（HOLD）Worktree 不阻断关窗，禁跑 `herdr server stop`）。UI 内环验证统一走 `shared-ci/scripts/context/design_contract.py`（`--audit-code`、`--audit-l10n`、`--check-edge-clipping`、`--audit-geometry`），不走 `xcode-cli`。
