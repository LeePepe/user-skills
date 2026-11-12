---
name: m2-dispatcher
description: M2-Dispatcher 编排内核：管理交互式入口（M2）与后台 Herdr Goal Steering（M1 TL）的生命周期合同，涵盖 Phase 0 简单 Spec 对齐、开 TL、热追加、巡检解卡与 Goal/Task 完结回收。
---
# M2-Dispatcher ↔ M1-Steering (TL) 编排合同（`m2-dispatcher`）

> Canonical skill: `m2-dispatcher`；角色统一名 **`M2-Dispatcher`**。Task-level 1-Hop 执行由 [`team-lead`](../team-lead/SKILL.md) 负责。

共享事实源与 L1 参考：
- [`runtime-profiles.yaml`](../team-lead/references/runtime-profiles.yaml) · [`bots-and-agents.md`](../team-lead/references/bots-and-agents.md) · [`ledger.md`](../team-lead/references/ledger.md)
- [`goal-queue.py`](../team-lead/scripts/goal-queue.py) · [`herdr-dispatch.sh`](../team-lead/scripts/herdr-dispatch.sh) · [`pr-dag-auto-heal.md`](references/pr-dag-auto-heal.md)
- **脚本归属（双路径兼容）**：① **M2+M1 共享编排**（`scripts/` ⇄ `../team-lead/scripts/`）：`herdr-dispatch.sh`、`goal-supervisor.py`、`goal-queue.py`、`state_store.py`、`jev_client.py`；② **M2 专属 Harness**：[`skill-slash-expander.ts`](scripts/skill-slash-expander.ts)、[`install-pi-harness.sh`](scripts/install-pi-harness.sh)；③ **M1 任务执行**（`../team-lead/scripts/`）：`ci-monitor.py`、`git-gc.py`、`feedback_guard.py`、`gtt-telemetry-export.py`、`linear-sync.py`、`jev-compact.py`、`jev-skill-router.py`。

## 0. Interactive CLI trigger flows (high visibility)

Use either trigger to enter the same Owner-gated cross-M2 flow:

- `/m2 <跨M2指令>` is the short universal macro. It expands to `m2-dispatcher`, runs `m2-router.py --plan`, and shows the per-target wave, repo, `EXISTING_M2`/`NEW_M2`, transport, and full sub-prompt table.
- `/m2-dispatcher <跨M2指令>` is the explicit skill trigger and follows the same plan-first flow.
- The AI must call `ask_user` for Owner approval or edits after displaying the preview. Only an explicit approval may continue with `m2-router.py --apply --plan-file <approved-plan.json> --approve`; neither trigger may dispatch before that gate.

Examples:

```text
/m2 先让 shared-ci 更新设计门禁，再让 AIDash 和 bing-growth-sapphire-app 同步接入
/m2-dispatcher 把 FlightBook 的结论同步给 AIDash
```

## 三层分离

1. **Layer A — Shared Flow Contract**：本入口规定 `M2-Dispatcher ↔ TL` Goal 生命周期；[`team-lead`](../team-lead/SKILL.md) 规定 `TL ↔ Subagent` 1-Hop 执行。
2. **Layer B — Private Bot Identity & Memory**：外层入口私有记忆留在宿主自身，不复制花名册或模型表。
3. **Layer C — Product Auto-review Gate**：安全门禁阻止 `herdr` 时 fail-closed 上报 Owner，不退回 `M2-Dispatcher` 改代码。

## 1. 开 TL、热追加与路由

- **两径简单 Spec 对齐**：Owner 确认（`ask_user`）始终留在 `M2-Dispatcher`。**路径 A（清晰边界）**：`M2-Dispatcher` 快速只读探查、用 `ask_user` 确认 1–3 个决策并落盘 `~/Development/handoff/<goal>/spec.md`（`design.md`）；**路径 B（跨仓/复杂需求）**：先调 `herdr-dispatch.sh` 让后台 `<goal>-goal-tl-r1`（M1）只读梳理 `spec.md` 草案与 `NEED_GO`，再由 `M2-Dispatcher` 与 Owner 拍板回传 M1。
- **灵感/截图/色板一键摄取（`m2-dispatcher` ↔ `design-studio`）**：收到单条灵感、参考 App 或截图时，`M2-Dispatcher` 直接前台调 `python3 skills/design-studio/scripts/design_catalog.py vault-ingest --text "..."`（或 `--image <shot.png> --measure-palette --archetype-ref <VS-ID>` 做 Vision OCR + BAMA 测色板；动效用 `ingest`）入库；批量母体聚类或落地项目走 `herdr-dispatch.sh --new-workspace --role team_lead` 派后台 `Steering`。
- **跨端统一快捷口令（Universal Shortcut Macros — Copilot / Claude / Codex / Pi / Cindy）**：由 [`skill-slash-expander.ts`](scripts/skill-slash-expander.ts) 展开并经 [`install-pi-harness.sh`](scripts/install-pi-harness.sh) 同步至 `~/.pi/agent` 与 `CindyGlobal/pi-package-home`（重算 `cindy-pi-package-fingerprint-v1`）：

| 口令 | 目标路由 | 执行动作 |
|---|---|---|
| `/m2 <跨M2指令>` | `m2-dispatcher` | 先运行 `m2-router.py --plan` 展示预览并 `ask_user` 拍板；确认后才运行 `--apply --approve` |
| `/status` | `m2-dispatcher` | 直接运行 `python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py status --check` 展示 `needs a person` 置顶视图 |
| `/ingest <text_or_image>` | `design-studio` | 调 `design_catalog.py vault-ingest`（图片路径自动附带 `--measure-palette`） |
| `/learn <topic_or_url>` | `knowledge-digest` | 执行双通道拆解归档至 `~/Development/Personal/knowledge-vault/` 并同步 `knowledge-vault/index.md` |
| `/dash` | `m2-dispatcher` | 触发 `~/Development/AIDash/aidata` 刷新并推送今日 `AIDash` 简报（`python3 cli.py digest --date today --aidash`） |
| `/retro` | `retro-loop` | 运行 `retro_story_engine.py agent-efficiency --auto-propose` 与 `discover` |
- **拍板后硬切换与 `M2` 只写简单 Spec 门禁（外层 M2 入口通用）**：
  - **`M2` 写边界**：`M2-Dispatcher` 只可写简单 Spec/Handoff（`spec.md`、`design.md`、`ledger.md`、`plan.md`、`handoff/**`、`specs/*.md`、`briefs/*.md`），**严禁改任何代码、测试、配置或其他文件**（外层工具 Adapter 由 `skill-slash-expander.ts` 在 M2 主会话首轮自动挂载 `m2-dispatcher` 并通过 `tool_call` 拦截非 Spec 写入，`install-pi-harness.sh` 负责同步）。
  - **强制 Worktree 派发与宿主看门狗**：凡改代码或其他文件，`M2` 落盘 `spec.md` 后必经 `herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude --cwd <repo_or_dev_root> --prompt "..."` 派后台 `Steering` 在独立 Worktree（`~/Development/worktrees/<task>` 或 `herdr worktree create`）修改提交，绝不在主 checkout `main` 直改（macOS 项目允许本地 `main` 合入后构建安装 `/Applications/<App>.app`，首次经 `ask_user` 确认记录）。同步 shell 宿主启动后运行 `python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 600 --json`；**Copilot CLI 宿主**必须用 `bash(command="python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 2400 --json", mode="async", detach=true, shellId="watch-<goal>")`，立即结束 Turn 进入 `session.idle`，让用户继续对话，退出时由 `<system_notification>` 唤醒。`--cwd` 传仓根／worktree 或 `~/Development`／`/Volumes/SSD/Development/handoff`，禁传 `handoff/<goal>`。
- **幂等复用与热追加**：`<goal>-goal-tl-r1` 为 `working`、`idle` 或 `done` 时绝不再开第二个 TL；改用 [`goal-queue.py`](../team-lead/scripts/goal-queue.py) `enqueue` 记录 `EVT=HOT_APPEND`，`idle`／`done` 时唤醒。
- **产品 UI/UX 设计与模型路由**：在本 Goal 内按 [`bots-and-agents.md`](../team-lead/references/bots-and-agents.md) 运行设计流水线；模型由 [`runtime-profiles.yaml`](../team-lead/references/runtime-profiles.yaml) 与 `herdr-dispatch.sh` 调度：`--kind claude` 走 `direct_quota_gated`（`~/.config/magpie/quotas.json` `<80%` 且 OAuth 正常时原生 `opus` 直连；超限或 `401/403/429` 自动剥离 `--allowedTools` 改派 `copilot --model magpie/group/steering`），`codex`/`copilot` 绑定 `Magpie (:3425)` `group/steering`。
- **Parent ≥ Child Allowlist**：父 TL 传入基线 `~/Development`、`~/.copilot`、`~/.agents`、`~/.codex`、`~/orca`、`/Volumes/SSD`、`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders` 并合并 `extra_add_dirs`。Mac mini 上 `BASELINE_ADD_DIRS` 须带 `/Volumes/SSD` 以覆盖 `/Volumes/SSD/.../handoff/<goal>` 兄弟目录 realpath；handoff 父目录不在基线时自动补进 trust 与 `--add-dir`。

## 1.5 Unified M2 Ingress（`m2-router.py`）

跨 Repo 或跨会话输入应先走唯一入口脚本 [`scripts/m2-router.py`](scripts/m2-router.py)，不要直接把自然语言广播给多个 M2：

```bash
python3 skills/m2-dispatcher/scripts/m2-router.py --plan \
  --message '把 repo-a 的结论同步给 repo-b' --json > plan.json
# Owner 审阅 wave、sub_prompt、EXISTING_M2/NEW_M2 后：
python3 skills/m2-dispatcher/scripts/m2-router.py --apply \
  --plan-file plan.json --approve --json
```

`--plan` 先让 Jev/Falcon (`:19823`) 在 [`references/m2-domain-map.json`](references/m2-domain-map.json) 的 23 个 `topic_id` 与 `NEW_UNMAPPED_DOMAIN` 中 `choice`，并以 `noul` 判断 `has_unmapped_domain`；再让 Magpie (`:3425`) 语义拆解并绑定会话。计划为每个 target 填 `topic_id`、`MAPPED_DOMAIN`/`NEW_DOMAIN_CANDIDATE`，并在预览与 `new_domain_candidates` 展示新领域提案。支持 `--domain-map-file`、`--mock-jev-json`、`--mock-magpie-json`、`--targets-json`；离线时才确定性兜底，不创建 session、不发送 prompt。

`--apply` 未带 `--approve` 会 fail-closed，按 wave 串行投递。只有 `--apply --approve --register-new-domains` 才会把 Owner 批准的候选追加到映射表；stdout 仅 dry-run，RAG 为显式 hook。

计划 schema 与技术细节见 `~/Development/handoff/m2-unified-ingress/spec.md`；该入口对应 How-To 节点 I7（Unified M2 Ingress）。

## 2. 监控与解卡

- **并发巡检与关联**：运行 `goal-supervisor.py scan`，按角色 TTL（Explorer/`pr_monitor` 10m、Designer/Reviewer 15m、Writer 30m、TL 40m）处理 `BLOCKED_AGENT`、`ZOMBIE_LEASE` 与 `ORPHAN_TL_WAIT`。Agent 通过 `-goal-tl-` 短名（`good-des-goal-tl-r1`）、台账 `TL:`／`from`／`to` 及 cwd 落在 Goal handoff 下绑定目录 `good-designer`（`good-des-*` 与 `gd-e1-*` 均计入 `BLOCKED_AGENT`）。用 `check-pins` 查 drift，`GOAL_DONE` 用 `telemetry --write` 写 `contract-telemetry.json`。子 Agent 等待拆成 30–60s slices（`herdr agent wait <name> --timeout 60000`）。Copilot allowlist 含 `/Applications/Xcode.app` 与 `/Volumes/SSD/Development`。
- **Blocked notify bridge（Channel A / B）**：Herdr `pane.agent_status_changed`（`agent_status=blocked`）与 `goal-supervisor.py scan --write-alerts` 均写入 `handoff/<goal_folder>/alerts/blocked-<agent>-<ts>.md`。`M2-Dispatcher` 读取 alert 后检查可见提示做 Tier A / A′ 决定或按 Tier B 记 `NEED_GO`。**通知桥 ≠ 解卡**：桥只落盘通知，严禁 auto-Enter 或把 `blocked` 当完成。**GOAL_DONE 关窗与 blocked alerts 两条独立**。
- **看门狗与途中插话续挂（Mandatory Watchdog & Mid-Flight User Question Rule）**：**Copilot CLI** 用 `bash(command="python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 2400 --json", mode="async", detach=true, shellId="watch-<goal>")`，立即进入 `session.idle`；退出仍由 `<system_notification>` 唤醒，随后 `read_bash(shellId="watch-<goal>", delay=5)` 处理并按需续挂。**Mid-Flight User Question Rule**：Copilot CLI 先 `list_bash` 检查 `watch-<goal>`；仍 `running` 就回答并结束 Turn，不重复开 watch；已退出或缺失则同一 Turn `read_bash` 并以 `mode="async", detach=true, shellId="watch-<goal>"` 续挂。同步 shell 宿主（如 Cindy）回答后同一 Turn 同步运行 `python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 600 --json` 直至 `EVT=GOAL_DONE` 或解卡回收。
- **双源对账与三级解卡**：同时读 [`ledger.md`](../team-lead/references/ledger.md) 与 `herdr agent get <tl-name>` 的 `agent_status`；`M2-Dispatcher` 事件用系统时钟与 `id=B<N>`／`id=J<N>`（禁撞 TL `id=E<N>`）。`blocked` 时先 `herdr agent read <name> --source visible --lines 40`：
  - **Tier A（路径权限）**：临时路径、基线路径（含 `/Volumes/SSD` 及其下 realpath）或已声明 `extra_add_dirs` 才可 `herdr agent send-keys <name> enter` 并记 `EVT=UNBLOCK`。
  - **Tier A′（`CMD_CONFIRM`）**：核对命令与高亮项，仅白名单（`gh`、不含 `commit`/`push`/`merge`/`rebase`/`reset`/`checkout` 的只读 `git`、`python3`、`jq`、不含 `server stop` 的 `herdr`、`shasum`、`date`、`sleep`、`env`）可发 `enter`（或 `down`+`enter` 选 remember）并记 `EVT=UNBLOCK`。`--no-ask-user` 不批准 `CMD_CONFIRM`；MacBook `team_lead` 不带 `--no-ask-user`，禁开启 `--yolo`。
  - **Tier B（越界/敏感/破坏性）**：保持 hold，记 `EVT=NEED_GO` 或 `NEED_OWNER_DECISION` 交 Owner。

## 3. 两级完成与回收

- **1-Hop Communication & Lifecycle Ownership**：每次只沟通一层 Agent。`M2-Dispatcher` 只沟通、唤醒、解卡并在 `GOAL_DONE` 时关闭 TL 的 Goal pane/workspace；绝不越级创建、prompt 或关闭 TL 下属 Explorer／Writer／Reviewer／`pr_monitor`。TL 全权负责下属 Subagent 派发与关窗回收。
- **Task / `PR_MERGED`**：TL 启动 `ci-monitor.py`（或回退 `pr_monitor`），cwd 用主仓或 `~/Development/handoff/<goal>`（不占 `$TASK_WORKTREE`）。确认 `PR_MERGED` 与 R10 回执后关子窗格、清 `__pycache__`，执行 `git worktree remove "$TASK_WORKTREE"`、`git worktree prune`、`git branch -D "$TASK_BRANCH"`；未合并、hold 或 dirty 现场必须保留并报 Owner。
- **Goal / `GOAL_DONE`**：全部 Task `done` 且队列 `drained: true` 后记 `EVT=GOAL_DONE`，拆成两条正交验收：
  - **A. Pane／workspace 关闭（MUST）**：关闭该 Goal 的 TL pane 并清空 workspace（`herdr workspace close "$GOAL_WORKSPACE"`；TL 可延迟自闭 `nohup sh -c 'sleep 2 && herdr pane close "$HERDR_PANE_ID"' >/dev/null 2>&1 &`，未自闭时 `M2-Dispatcher` MUST 回退关窗）。绝不触碰共享 `w1`、Owner 手开窗格或其他在途 Goal。
  - **B. Worktree 处置（与 A 正交）**：已合并 Task 按 `PR_MERGED` 回收；Owner Freeze 或未合并/hold/dirty 现场保留 Worktree，MUST NOT 阻断 A。

## 4. 交接边界、固定操作顺序与 Fail-closed 原则

- **交接边界**：`M2-Dispatcher` 管 Goal 生命周期；Task 实现、R10 与 V1–V5 审查归 [`team-lead`](../team-lead/SKILL.md)。异常沿 `Writer/Reviewer → TL → M2-Dispatcher → Owner` 回报。
- **固定操作顺序**：① 读 ledger 确认 `Owner`、协调者、TL、版本与状态 → ② 读 `herdr agent get` 与 ledger 对账 → ③ 先判是否有 live TL 再选 `enqueue` 或开 TL → ④ UI/UX 设计在本 Goal 内按 [`bots-and-agents.md`](../team-lead/references/bots-and-agents.md) 运行 → ⑤ 入队保留 `task_id`、`repo`、`paths`、`brief` 与依赖 → ⑥ 解卡/hold/完成以真实系统时钟记入 [`ledger.md`](../team-lead/references/ledger.md) → ⑦ 磁盘回执、Git 与 Agent 状态三方一致才发下一步提示（完结须齐备 `PR_MERGED`、R10、`drained: true`、`EVT=GOAL_DONE`）。
- **Fail-closed**：不以 `idle` 或口头输出替代证据；不因权限弹窗改安全门禁或代码；不关未合并/dirty/hold Worktree；不在私有记忆复制共享模型表或脚本规则。

## 5. PR DAG Supervision & Auto-Heal Loop（摘要）

多 PR 拓扑（`pr-dag.json`）、Active Wave、`pr-watch-state.json` 三独立水位线、S1（`CONFLICT`）／S2（`UNRESOLVED_COMMENTS`）／S3（`CI_FAILURE`）／S4（`PR_MERGED_ADVANCE_DAG`）四类信号闭环及幂等 Reply + Resolve 完整脚本模板见 [`pr-dag-auto-heal.md`](references/pr-dag-auto-heal.md)。热追加与 `idle`/`done` 唤醒最小形态：

```bash
python3 skills/team-lead/scripts/goal-queue.py enqueue --handoff-dir "$GOAL_HANDOFF" \
  --task-id "$TASK_ID" --title "$TITLE" --repo "$REPO" --paths "$PATHS" --brief "briefs/$TASK_ID.md" --depends-on "$DEPENDS_ON"
herdr agent prompt "$GOAL-goal-tl-r1" "Hot-append $TASK_ID; read $GOAL_HANDOFF/briefs/$TASK_ID.md; run goal-queue.py next-wave" && sleep 2
TL_STATUS=$(herdr agent get "$GOAL-goal-tl-r1" | jq -r '.. | .agent_status? // empty' | head -n1)
if [ "$TL_STATUS" = "idle" ] || [ "$TL_STATUS" = "done" ]; then
  herdr agent send-keys "$GOAL-goal-tl-r1" enter
fi
```
