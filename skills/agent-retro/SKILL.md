---
name: agent-retro
description: v2.1.2 6-layer Multi-Agent Trajectory Retro (role, handoff, invoke, exec, friction, horizon) with zero-token probe, Cheap Worker/Jev triage, post-fix watermarks, bounded M2-to-M1 rollup, and inspect-json soft/hard replay locks. Not for product crash telemetry or blog extraction.
---
# Agent Retro v2.1.2（六层多智能体轨迹复盘与首错向上归因：`Probe -> Cheap Worker + Jev Triage -> 1 M2 -> K (0..3) Parallel M1 TLs -> M2 Rollup`）

`agent-retro` collects bounded multi-agent execution telemetry and inspects multi-agent execution trajectories (`M3 personal-m2-assistant` -> `M2 m2-dispatcher` -> `M1 team-lead` -> `Designer my-designer` / `Writer` / `Reviewer` / `CI-Monitor`), computes the **6-Layer Progressive-Disclosure-Aligned Agent Metrics (`role`, `handoff`, `invoke`, `exec`, `friction`, `horizon`)**, attributes anomalies upstream to the exact responsible Skill (`Symptom in Agent, Cure in Skill`), and drives the **3-Stage Progressive Self-Evolution Flow** into an independent **`[Agent Retro YYYY-MM-DD]`** Dual-Zone M3 issue.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| Stage 0 bounded collection | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py collect [--output /tmp/multica-agent-retro-collect.json] --days 3 --json` |
| Stage 1 wide or role/layer probe | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py probe [--role <role>] [--layer <layers>] --fast --json` |
| Stage 2 Cheap Worker + Jev triage (`--pick-top 3` / `--pick-one`) | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py triage [--role <role>] [--pick-top 3 \| --pick-one] --use-jev --emit-briefs ~/Development/handoff --json` |
| Stage 3 deep skill attribution diagnosis | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py diagnose --skill <name> [--days 30] --json` |
| Layer 3 JSON fact crystallization (`soft_advisory_exit_0` / `--strict`) | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py inspect-json <path...> [--path <p>] [--keys a.b] [--head 3] [--strict] --json` |
| Stage 3 emit `K (0..3)` per-skill M1 specs & briefs | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py emit-briefs [--skill <name>] [--pick-top 3 \| --pick-one] --emit-briefs ~/Development/handoff --json` |
| Rollup `K` M1 receipts to `[Agent Retro YYYY-MM-DD]` | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py rollup --handoff-root ~/Development/handoff [--issue-brief] --json` |
| Publish or schedule Multica `[Agent Retro YYYY-MM-DD]` issue | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py file-issue [--dry-run] --json` |
| verification | `references/eval-cases.json` (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill agent-retro --strict` |

## Role-Based Routing (Role-to-Stage Cost Split)

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Explorer / Mechanical (Stage 1+2 Cheap Probe Worker: `mai-code-2-flash` / `gpt-5.6-luna` + Jev, `0` steering tokens) | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff --json` |
| Role: M2-Dispatcher (`1 M2` Coordinator: spawns Probe -> `0..3` M1 TLs -> Rollup) | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py rollup --handoff-root ~/Development/handoff --issue-brief --json` |
| Role: M1-TL (`1 M1 TL per skill`: Stage 3 Deep Reasoning in `worktrees/agent-retro-<skill>`) | `references/agent-retro-playbook.md` | `python3 skills/agent-retro/scripts/agent_retro.py diagnose --skill <name> --json` |
| Role: Writer | `references/agent-retro-playbook.md` | `python3 skills/skill-contract/scripts/skill_contract.py audit --skill <name> --strict` |
| Role: Reviewer | `references/agent-retro-playbook.md` | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill <name> --strict` |

## 12 Core Methodologies & Guardrails

1. **Outcome-First Gate, Trajectory-Second Profiler (Anthropic Principle)**: Grade final Goal/Task outcome first; only profile intermediate trajectories on Goals that drifted/abandoned, falsely completed, breached P90 token/latency budgets, hit `R3+`, or triggered human interrupts.
2. **Runtime Progressive Disclosure (`R1 -> R2` Convergence is Healthy)**: `exit=1` from L2 scripts/tests followed by reading a pointed L1 drawer and passing in R2 (`fail -> disclose -> pass`) is scored positively as `progressive_catch_rate`, never penalized.
3. **Upstream First-Fault Credit Assignment (`Symptom in Agent, Cure in Skill`)**: Trace downstream Writer/Reviewer rework back to the first upstream role/skill (`M3` prompt pre-lock -> `personal-m2-assistant`; `M2` direct code edit or zombie Goal -> `m2-dispatcher`; `M1` missing brief constraint -> `team-lead`; `[SPEC_GAP]` -> `my-designer`).
4. **MAST Alignment Checks**: Detect `Reasoning-Action Mismatch` (assistant promises delegation/verification while editing directly or skipping checks) and `Handoff Information Withholding` (Owner decisions in M2 dropped from `spec.md`/`briefs/*.md`).
5. **ACI Output Noise & Roadblock Control (SWE-agent Principle)**: Detect `>20KB` single-call tool outputs (`tool_output_bloat`) and `exit=2` CLI argument roadblocks; remediate via `--summary`/pagination, flag aliases (`--dir`/`--handoff-dir`/`--handoff-root`), and `<=3`-line self-disclosing stderr pointers.
6. **Receipt-to-Trace Faithfulness Grounding (Agent-EvalKit Principle)**: Cross-check R10 receipt claims (`pytest`/`swiftlint`/`PR_MERGED`/`verify`) against actual `exit=0` `tool.execution_complete` records in `events.jsonl`, and separate superseded `CANCELLED` CI runs from true `PR_CHECK_FAILED`.
7. **Fast (`N=1`) vs. Slow (`N>=50`) Dual Flywheel + 3-Question Scope Triage + Positive/Counter-Case Dual Replay** (`observation_pool` for `<3` occurrences).
8. **Scenario-Aware Normalization (`hits / scenario_sessions`) & `<5` Low-Frequency Protection + One-In-One-Out**.
9. **3 Physical Locks**: Every skill fix must land in a Hook/Permission lock, L2 Script default/self-disclosing check, or CI/Config (`runtime-profiles.yaml`) lock—never prose reminders in `SKILL.md`.
10. **Honest `0..3` Upper Cap & Role Cost Split**: Stage 1 (`0` token `<1.5s` probe) -> Stage 2 (`1` Cheap Worker `explorer`/`mechanical` + Jev `1.5s`, `0` steering tokens) -> Stage 3 (`K=0..3` independent M1 TLs; `K=0` reports `HEALTHY_NO_ISSUES`).
11. **Version Watermark Delta (`vN-1@sha -> vN@sha`)**: Split pre-fix vs post-fix anomalies across skill versions so resolved issues do not re-trigger.
12. **Independent M3 Rollup Issue (`[Agent Retro YYYY-MM-DD]`) & Multica Autopilot Compatibility**: Emit Dual-Zone Issue Format (`<=45` human lines + `<details>` JSON passing `verify_issue_format.py`) and support Multica scheduled `run_only` execution (`setup-autopilot`).

## 6-Layer Multi-Agent Trajectory Metrics（`role,handoff,invoke,exec,friction,horizon`）

- **`role` (`L-Role` · Role Purity & Boundary)**:
  - `R1_m3_prompt_prelock` -> `personal-m2-assistant` | `R2_m2_direct_code_edit` -> `m2-dispatcher` | `R3_m1_inline_overwork` (`>3` files or `>60` turns without Writer) -> `team-lead` | `R4_session_skill_pollution` (`>3` domain skills) -> `m2-dispatcher` | `R5_reviewer_same_vendor` -> `code-review` / `team-lead`.
- **`handoff` (`L-Handoff` · Cross-Agent Handoff & State Closure)**:
  - `H1_zombie_drifted_goals` -> `m2-dispatcher` | `H2_initial_sha_false_lock` (`INPUT_BLOCKED` on 0-diff initial dispatch) -> `team-lead` | `H3_r10_receipt_gap_or_dirty_schema` -> `team-lead` | `H4_compaction_and_handoff_loss` (compaction, `>=3x` full-file re-reads, reasoning-action mismatch, handoff withholding) -> `m2-dispatcher` / `team-lead`.
- **`invoke` (`L-Invoke` · Skill Invocation & ACI Hygiene)**:
  - `I1_cold_start_greedy_drawer_read` (`>=3` L1 drawers read in turns 0–2 before error trigger) | `I2_mandatory_l2_cli_skip` (`0` script calls after loading skill with mandatory L2 CLI) | `I3_l2_exit2_and_output_bloat` (`exit=2` roadblocks & `>20KB` `tool_output_bloat`, attributed to script/path owner or first invoked upstream skill) | `I4_manual_poll_loop` (`>=3x` manual `herdr pane read`/`sleep`/`gh pr view` polling) -> `team-lead`.
- **`exec` (`L-Exec` · Progressive Convergence & Verification Faithfulness)**:
  - `E1_blind_spin_loop_vs_progressive_catch` (preserve `exit=1 -> disclose -> exit=0` as `progressive_catch_rate`; flag `>=3x` consecutive identical-error `blind_spin_loop`) | `E2_r3_plus_divergence` (exempt `R1->R2`; flag `R3+`) | `E3_gate_escape_and_uncrystallized_spec_gap` (`[SPEC_GAP]` -> `my-designer`, CI gate escape -> `repo-kit`) | `E4_unfaithful_receipt_and_cancelled_noise` (ungrounded R10 receipt or `CANCELLED` false `PR_CHECK_FAILED` -> `team-lead`).
- **`friction` (`L-Friction` · Human-Agent Interrupt & Friction)**:
  - `F1_permission_stall_count` (`permission.requested` stalls on safe paths / unanswered shutdowns) | `F2_owner_abort_rate` (`abort` / `session.error`) | `F3_mid_flight_owner_correction` (mid-flight Owner steering corrections).
- **`horizon` (`L-Horizon` · Long-Horizon Optimization)**: `HZN1` context carry, `HZN2` wakeup/parallelism, `HZN3` rework/verify tax, `HZN4` fatigue inflection, and `HZN5` steering overkill; profile successful long runs without LLM tokens.

## Canonical 3-Stage Prompts & M3 Rollup Contract

- **Stage 1+2 (`1 M2 -> 1 Cheap Probe Worker`)**: Dispatch `--role explorer` (`mai-code-2-flash` / `gpt-5.6-luna`, `0` steering tokens) to run `agent_retro.py probe --fast --json` and `agent_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff --json`. With **zero Owner approval gate before fixing**, select `K` (`0..3` honest cap, never a forced quota) disjoint upstream skills (`K=0` -> `HEALTHY_NO_ISSUES`).
- **Stage 3 (`1 M2 -> K Parallel M1 TLs -> M2 Rollup`)**: For each picked skill (`K=1..3`), spawn `agent-retro-<sk>-goal-tl-r1` in `~/Development/worktrees/agent-retro-<sk>`. If deep reasoning finds the signal is benign (e.g. `R1->R2` convergence), record `HEALTHY_NO_ISSUES` (`0` Writers/Reviewers); if confirmed, run `Writer -> Reviewer` with 1 of the **3 Physical Locks** + **Positive & Counter-Case Dual Replay**, merge PR, and roll up via `agent_retro.py rollup --handoff-root ~/Development/handoff --issue-brief --json` into **`[Agent Retro YYYY-MM-DD]`**.

## v2.1.0 Parity + Long-Horizon Layer

- **Parity**: `probe` resolves per-target `vN@sha` watermarks and post-fix cohorts; `triage` ranks recurring post-fix anomalies by default (`--include-pre-fix` is historical replay). It performs the zero-token H5 AST contract/warehouse read, detects I5 cross-agent shell glue, and supports `--use-jev` with deterministic fallback.
- **Compatibility aliases**: `diagnose` accepts `diagnose <skill>` / `--goal-id` / worktree-name fallback (`agent-retro-<skill>` or `skill-retro-<skill>`), and `probe`/`diagnose` accept harmless `--mode` + `--aspects` bridge flags (mapped to `--layer` when `--layer` is absent).
- **M1 contracts**: `record-m1-receipt --skill <sk> --goal-id <gid> --status <FIXED_AND_MERGED|HEALTHY_NO_ISSUES|CROSS_SKILL_RELAY>` and `emit-relay --from <host> --to <guest> --requirement "..."` are local, bounded, and rollup grounds merge SHAs.
- **Horizon**: `horizon --days 7 --min-turns 40 --json` profiles successful long sessions with HZN1 context carry, HZN2 wakeup/parallelism, HZN3 rework/verify tax, HZN4 fatigue inflection, and HZN5 steering overkill. `--layer horizon` is accepted by probe/triage/rollup.

See [agent-retro-playbook.md](references/agent-retro-playbook.md) and [eval-cases.json](references/eval-cases.json).
