---
name: m2-dispatcher
description: M2-Dispatcher 编排内核：管理交互式入口（M2）与后台 Herdr Goal Steering（M1 TL）的生命周期合同，涵盖 Phase 0 简单 Spec 对齐、开 TL、热追加、巡检解卡与 Goal/Task 完结回收。
---

# m2-dispatcher ↔ M1-Steering (TL)

Canonical skill: `m2-dispatcher`; role **M2-Dispatcher**. Task-level 1-Hop execution belongs to [`team-lead`](../team-lead/SKILL.md).

## Role-Based Routing（按角色唯一必读抽屉 + 唯一必跑 CLI）

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: M2-Dispatcher | `references/pr-dag-auto-heal.md` | `goal-supervisor.py status --check` |
| Role: M1-TL | `../team-lead/references/ledger.md` | `goal-queue.py next-wave --mark-in-progress` |
| Role: Writer | `../team-lead/references/bots-and-agents.md` | `herdr-dispatch.sh --role writer` |
| Role: Reviewer | `../team-lead/references/ledger.md` | `feedback_guard.py pre-review-check` |

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| Goal lifecycle/DAG | `references/pr-dag-auto-heal.md` | `goal-supervisor.py status --check` |
| TL handoff | `../team-lead/references/ledger.md` | `herdr-dispatch.sh --role team_lead` |
| blocked/review recovery | `../team-lead/references/bots-and-agents.md` | `feedback_guard.py pre-review-check` |
| M2 write-boundary / Goal pivot / incremental ledger read | `references/pr-dag-auto-heal.md` | `python3 skills/m2-dispatcher/scripts/m2_preflight.py ledger-delta --handoff-dir <dir> --json` |
| verification | [eval-cases.json](references/eval-cases.json) (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill m2-dispatcher --strict` |

## Boundaries

M2 owns Owner Gate, simple `spec.md`/`design.md`, hot append, monitoring, blocked alerts, and `blocked-<agent>-<ts>.md` alerts; Goal recovery is fail-closed. It may edit only Spec/Handoff files; code, tests, and config go through `herdr-dispatch.sh` into an isolated `worktrees/` workspace. Every route is fail-closed and every event is recorded in `ledger.md`.

### Preflight Locks

- Before any M2 write, run `python3 skills/m2-dispatcher/scripts/m2_preflight.py check-write-path --path <target> --json`; only `handoff/**`, `/tmp/**`, and `.copilot/session-state/**` are allowed (`R2`).
- Use `python3 skills/m2-dispatcher/scripts/m2_preflight.py ledger-delta --handoff-dir <dir> --tail 20 --json` for bounded watermark-tracked ledger deltas instead of full-file reads (`H4`).
- On a Goal pivot, run `python3 skills/m2-dispatcher/scripts/m2_preflight.py supersede-goal --old-handoff <dir> --new-goal <slug> --reason <reason> --apply --json`; it cancels old tasks and records `EVT=GOAL_SUPERSEDED` plus `EVT=GOAL_DONE` (`H1`).
- Before UI Goal closeout, run `python3 skills/m2-dispatcher/scripts/m2_preflight.py check-ui-closeout --handoff-dir <dir> --json` (`check-ui-dispatch`) to enforce M1 `herdr-dispatch.sh` evidence plus R10 rendered PNG, whitespace check, and `magpie/group/designer` Visual Traceback (`EV-M2-015`).

Shared references: [`runtime-profiles.yaml`](../team-lead/references/runtime-profiles.yaml) (Script-Only Data Drawer — query via L2 CLI, never full-file view), [`bots-and-agents.md`](../team-lead/references/bots-and-agents.md), [`ledger.md`](../team-lead/references/ledger.md), and [`m2-domain-map.json`](references/m2-domain-map.json) (Script-Only Data Drawer — query via L2 CLI, never full-file view). Shared scripts: [`goal-queue.py`](../team-lead/scripts/goal-queue.py), [`herdr-dispatch.sh`](../team-lead/scripts/herdr-dispatch.sh), plus `goal-supervisor.py`, `state_store.py`, and `jev_client.py`.

## Flows

- `/m2 <跨M2指令>` and `/personal-m2-assistant` use the canonical cross-M2 plan/apply gate; `/status`, `/ingest <text_or_image>`, `/learn <topic_or_url>`, `/dash`, and `/retro` remain explicit shortcuts. `cli.py digest --date today --aidash` and `retro_story_engine.py agent-efficiency --auto-propose` remain explicit commands.
- `/ingest` runs `vault-ingest` with `--measure-palette`; `/status` runs `goal-supervisor.py status --check`; `/learn`, `/dash`, and `/retro` remain explicit shortcuts.
- Product design follows `design-studio` Entry A then `my-designer` Entry B; no auto-publish or direct multi-M2 broadcast.
- **Goal 边界判定（微调 vs. 独立新 Feature）**：Hot-Append reuses a live `<goal>-goal-tl-r1`; a distinct feature creates a distinct Goal with `handoff/<goal-2>/spec.md`; examples include `good-des` and `gd-e1`; 严禁把两个不同 Feature 塞进同一个 `spec.md`. 跨端 iCloud AI 接单引擎属于独立 Goal，Never spawn a second TL.

## Watchdog and blocked protocol

The Copilot host starts `goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 2400 --json` with `detach: true`, `watch-<goal>`, and `session.idle`; a shell host uses the bounded 600-second watch. `Mid-Flight User Question Rule` checks the existing watch before answering. Scan for `ZOMBIE_LEASE`, `ORPHAN_TL_WAIT`, and `BLOCKED_AGENT`; sub-agent waits use `30–60s` slices and the parent baseline includes `/Volumes/SSD`. 通知桥 ≠ 解卡；GOAL_DONE 关窗与 blocked alerts 两条独立. Blocked notifications are evidence only, not auto-Enter: Tier A path/CMD_CONFIRM may be unblocked; Tier B stays `blocked` and needs Owner.

```bash
if [ "$TL_STATUS" = "idle" ] || [ "$TL_STATUS" = "done" ]; then
  herdr agent send-keys "$GOAL-goal-tl-r1" enter
fi
```

## Closeout

Writer/Reviewer → TL → M2-Dispatcher → Owner is the only escalation chain; 每次只沟通一层. After `PR_MERGED`, clean the child with `git worktree remove`; after `GOAL_DONE`, close only the Goal pane/workspace. `未合并／暂停（HOLD）` worktrees stay preserved and never block pane close. `pr_monitor` and `ci-monitor.py` provide Layer 0 status; do not treat idle as completion.

M2’s local harness remains [`skill-slash-expander.ts`](scripts/skill-slash-expander.ts) and [`install-pi-harness.sh`](scripts/install-pi-harness.sh); canonical shared orchestration stays in the team-lead paths above.
