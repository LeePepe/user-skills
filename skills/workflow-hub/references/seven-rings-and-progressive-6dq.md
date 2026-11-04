# 7-Ring Workflow Hub Reference

## Ring contract

| Ring | Owner contract | Consumes | Produces |
|---|---|---|---|
| 1. Linear Board | `linear-board` | Idea、TIA state、Goal checklist | promoted Goal、状态与 blocker evidence |
| 2. Co-Design | research/domain/design skills | facts、constraints、open decisions | frozen `spec.md`/`design.md` |
| 3. Bot/TL | `bot-tl-orchestration` | Goal handoff、queue、alerts | TL lifecycle、R8 state、handoff receipts |
| 4. Build | `goal-task` | frozen spec、brief、Owned Paths | commit、PR、tests、R10 |
| 5. Review/Merge | review skills、`pr_monitor` | diff、checks、R10 | V1–V5 conclusion、merged head |
| 6. Delivery/Release | product and shared foundations | merged head、contracts、release inputs | published build、readback |
| 7. Retro/Story | `retro-loop`、`story-craft` | telemetry、timeline、quantitative delta | locks、narrative、new Idea |

## 自治脉搏：Cron / Clockwork 定时调度（Precheck-First, Wake-on-Actionable）

Clockwork / `launchd` 以 `StartInterval=300` 等时间窗任务启动预检，运行结果写入
`Results/<job_id>/runs/<run_id>/` 并保留最近 50 次。每次先运行 0 Token 的
`precheck.py` 或 `goal-supervisor.py scan`：`pending == 0` 时直接退出且不唤醒
LLM；只有 `pending > 0` 才通过 webhook 或 `herdr agent prompt` 精准唤醒对应
Bot / Sticky TL。该心跳分别驱动环 5 的 PR 盯梢、环 3/4 的 Goal 租约巡检，以及
环 7 → 环 1 的遥测反馈回流。

## Progressive 6DQ

The ladder is intentionally progressive. **6DQ helps an Agent discover problems
from shallow to deep during development; it is not an upfront all-at-once
constraint.** The stages are:

1. **D1** — confirm branch, worktree path, dependency and environment isolation.
2. **L1** — run focused unit tests, syntax checks and static checks after the
   smallest useful slice exists.
3. **L2** — replace mocks incrementally with local real serialization, storage and
   boundary integration.
4. **G2** — before PR handoff, run secret scanning, permission review,
   `design_contract.py` and `sync_contract.py --check`.
5. **L3 + Release Readback** — verify the key E2E journey, `release_sync.py
   readback`, `/api/live`, `SHA256SUMS`, TestFlight and FlightBook evidence.

Each stage records the command, evidence, first failure and next smallest repair.
Passing an earlier stage never implies that later delivery readback has passed.

## 4-layer product/output matrix

| Layer | Product/output concern | Required readback |
|---|---|---|
| Products & Apps | user journey, performance, crash, satisfaction | E2E, PLT/FPS, Crash-Free, DSAT |
| Shared Foundations | DesignKit/Basalt, telemetry, CI and release contracts | contract, component, telemetry and digest checks |
| Engineering Workflows | Goal/Task, PR, CI, release and R10 | accurate head, checks, status and receipt |
| Knowledge & Narrative | retro, story, blog and deck | provenance, desensitization, build and publication readback |

## Return path

Ring 7 sends a confirmed mechanism to `linear-board` as a TIA Idea or active Goal.
Unconfirmed hypotheses remain backlog material and never silently rewrite a frozen
`goal-task` contract.
