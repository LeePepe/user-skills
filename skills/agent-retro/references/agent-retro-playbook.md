# Agent Retro Playbook (`5-Layer Multi-Agent Trajectory Retro & Role-to-Skill Upstream Attribution`)

> Core Philosophy: **Symptom in Agent, Cure in Skill.** Grade final outcomes first (**Outcome-First Gate**), treat `R1 -> R2` (`exit=1 -> disclose -> exit=0`) as healthy runtime progressive disclosure, and trace downstream rework back to the **first upstream role and skill** that introduced the defect.

## 1. Three-Stage Execution Architecture (`1 M2 -> 1 Cheap Probe Worker -> K (0..3) Parallel M1 TLs -> M2 Rollup`)

| Stage | Command / Engine | Role & Model Cost Tier (`runtime-profiles.yaml`) | Output & Routing Contract |
|---|---|---|---|
| **Stage 1 · Sub-Second Trajectory Probe** | `agent_retro.py probe [--role <role>] [--layer <layers>] [--days <N>] --fast --json` | `0` Token · `< 1.5s` deterministic script | Scans `events.jsonl`, `handoff/*/{ledger.md,spec.md,design.md,receipts/*}`, and optional `warehouse.db` (with `SF_DATALESS` / `/Volumes/` non-blocking guards); bounded `<16KB` summary output by default. |
| **Stage 2 · Cheap Worker + Jev Triage (`1 M2`)** | `agent_retro.py triage [--role <role>] [--pick-top 3 \| --pick-one] [--use-jev] [--mock-jev-json <json>] [--emit-briefs <dir>] --json` | **1 Cheap Probe Worker (`--role explorer` / `--role mechanical`, `worker` tier `mai-code-2-flash` / `gpt-5.6-luna` + Jev `1.5s`, `0` steering tokens)** | Zero Owner gate before fixing; honest `0..3` upper cap (`K=0` -> `HEALTHY_NO_ISSUES`, `0` M1 TLs spawned). Emits `K` disjoint per-skill M1 Goal specs (`<dir>/agent-retro-<sk>/spec.md`) + `m2_dispatch_plan`. |
| **Stage 3 · `1 M2 -> K Parallel M1 TLs -> M2 Rollup`** | `agent_retro.py diagnose --skill <sk> --json` + `emit-briefs` + `rollup --handoff-root <dir> --issue-brief --json` + `file-issue` / `setup-autopilot` | **`1 Independent M1-TL per Skill` (`agent-retro-<sk>-goal-tl-r1`) -> (`Writer -> Reviewer` if needed) -> M2 Rollup** | If benign after deep reasoning, M1 TL writes `HEALTHY_NO_ISSUES` (`writers_spawned=0`); if real fix needed, M1 TL enforces 1 of **3 Physical Locks** + **Positive & Counter-Case Dual Replay**, merges PR, and rolls up into **`[Agent Retro YYYY-MM-DD]`**. |

---

## 2. Outcome-First Filter & Runtime Progressive Disclosure Exemption

1. **Outcome-First Gate (Anthropic Principle)**:
   - A Goal/Task that completes cleanly in `R1` or `R2` (`DONE`/`PR_MERGED`), stays within P90 token/latency budgets, has faithful R10 receipts, and zero human interrupts is graded **`OUTCOME_HEALTHY`** — intermediate trajectory steps are not penalized.
   - Trajectory profiling activates only when a Goal/Task is: (a) drifted/abandoned (`H1`), (b) falsely completed / ungrounded (`E4`), (c) over P90 token/turn budget (`R3`, `I3`), (d) in `R3+` review ping-pong (`E2`), or (e) interrupted by human stalls/aborts/corrections (`F1`–`F3`).
2. **Runtime Progressive Disclosure Exemption (`R1 -> R2` & `exit=1 -> disclose -> exit=0`)**:
   - When an agent hits `exit=1` on a deterministic L2 script/test, reads the pointed L1 `references/*.md` drawer, edits code, and passes (`exit=0`), it is recorded as **`progressive_catch_rate` (Healthy)** and **exempted** from `E1_blind_spin_loop` and `E2_r3_plus_divergence`.
   - Likewise, a 2-round review (`R1 FAIL -> R2 PASS`) is healthy convergence and never flagged as `E2`.

---

## 3. Five-Layer Metric Formulas & Upstream First-Fault Credit Assignment

| Layer | Metric ID | Detection Formula & Threshold | Upstream Responsible Skill (`Cure in Skill`) | Physical Lock Remediation |
|---|---|---|---|---|
| **`role` (`L-Role`)** | `R1_m3_prompt_prelock` | M3 session pre-baking Phase 0 design choices or Hot-Append implementation details before M2/M1 exploration | `personal-m2-assistant` | Hook/Script gate on M3 dispatch prompt templates |
| **`role` (`L-Role`)** | `R2_m2_direct_code_edit` | M2 session (`m2-dispatcher`) executing `edit`/`create`/`apply_patch` on non-handoff code/test/config paths | `m2-dispatcher` | `skill-slash-expander.ts` / M2 `tool_call` path allowlist lock |
| **`role` (`L-Role`)** | `R3_m1_inline_overwork` | M1 TL (`group/steering`) editing `>3` source files or running `>60` turns without spawning a `Writer` | `team-lead` | `feedback_guard.py` / `goal-supervisor.py` inline-file budget check |
| **`role` (`L-Role`)** | `R4_session_skill_pollution` | Single session loading `>3` distinct domain skills | `m2-dispatcher` / `personal-m2-assistant` | Wave splitter / skill-scope router lock |
| **`role` (`L-Role`)** | `R5_reviewer_same_vendor` | Reviewer running on identical model/vendor family as Writer or M1 TL | `code-review` / `team-lead` | `runtime-profiles.yaml` cross-vendor reviewer pin |
| **`handoff` (`L-Handoff`)** | `H1_zombie_drifted_goals` | Goal in `in_progress`/`planning` with stale lease and `0` active agents | `m2-dispatcher` | `goal-supervisor.py` zombie lease sweep & auto-archive |
| **`handoff` (`L-Handoff`)** | `H2_initial_sha_false_lock` | `INPUT_BLOCKED` (`spec_sha`/`brief_sha` drift) fired on 0-diff initial dispatch | `team-lead` | Baseline SHA snapshot normalization in `herdr-dispatch.sh` |
| **`handoff` (`L-Handoff`)** | `H3_r10_receipt_gap_or_dirty_schema` | `done` task missing R10 receipt or containing unparsed dirty fields/paths | `team-lead` | `feedback_guard.py pre-review-check` receipt schema validator |
| **`handoff` (`L-Handoff`)** | `H4_compaction_and_handoff_loss` | Context compaction, `>=3x` full-file re-reads, **MAST Reasoning-Action Mismatch**, or **Handoff Information Withholding** (M2 Owner constraints missing from `spec.md`/`briefs/*.md`) | `m2-dispatcher` / `team-lead` | Spec-to-brief constraint diff gate & `view_range` enforcement |
| **`invoke` (`L-Invoke`)** | `I1_cold_start_greedy_drawer_read` | Reading `>=3` L1 `references/*.md` files in turns `0..2` before any script/error trigger | Loaded Skill (`skill-contract` / target skill) | Trim L0 eager links; move to When-to-Read table |
| **`invoke` (`L-Invoke`)** | `I2_mandatory_l2_cli_skip` | Loading a skill that declares a mandatory L2 CLI but running `0` calls to its `scripts/*` | Target Skill (`team-lead` / `skill-contract` / etc.) | Pre-review L2 execution receipt check |
| **`invoke` (`L-Invoke`)** | `I3_l2_exit2_and_output_bloat` | L2 script `exit=2` CLI roadblocks (post-watermark `vN@sha`) or single tool call `>20KB` stdout (`tool_output_bloat`) | Target Skill owning the script | Add `--dir`/`--handoff-dir`/`--handoff-root` aliases, `--summary` cap (`<16KB`), and `<=3`-line stderr pointer |
| **`invoke` (`L-Invoke`)** | `I4_manual_poll_loop` | `>=3x` manual `herdr pane read`, `sleep`, or `gh pr view` polling calls instead of `goal-supervisor.py watch` / `ci-monitor.py` | `team-lead` | `ci-monitor.py` / `goal-supervisor.py watch` wrapper lock |
| **`exec` (`L-Exec`)** | `E1_blind_spin_loop_vs_progressive_catch` | `>=3x` consecutive identical-error retries without reading a reference drawer (`blind_spin_loop`), vs healthy `exit=1 -> disclose -> exit=0` (`progressive_catch_rate`) | Target Skill / `team-lead` | Wire `<=3`-line self-disclosing `references/*.md` pointer on `exit=1` |
| **`exec` (`L-Exec`)** | `E2_r3_plus_divergence` | Task reaching `R3+` review rounds or `CHANGE_REQUEST` directional reset (`R1->R2` exempted) | Upstream skill via First-Fault Credit Assignment (`my-designer` / `team-lead` / `m2-dispatcher`) | Sink missing upstream constraint into `spec_gate.py` / `design_contract.py` |
| **`exec` (`L-Exec`)** | `E3_gate_escape_and_uncrystallized_spec_gap` | Local verify `exit=0` followed by remote CI failure, or `[SPEC_GAP]` recurring across `>=3` goals | `repo-kit` (CI escape) / `my-designer` (`[SPEC_GAP]`) | Sink `[SPEC_GAP]` into `design_contract.py` / `verify_design_cases.py` |
| **`exec` (`L-Exec`)** | `E4_unfaithful_receipt_and_cancelled_noise` | R10 receipt claiming `pytest`/`swiftlint`/`verify`/`PR_MERGED` with `0` matching `exit=0` calls in `events.jsonl`, or superseded `CANCELLED` CI runs logged as `PR_CHECK_FAILED` | `team-lead` | `feedback_guard.py` trace-grounding & `ci-monitor.py` `CANCELLED` filter |
| **`friction` (`L-Friction`)** | `F1_permission_stall_count` | `permission.requested` stalls on safe paths or unanswered shutdowns | `team-lead` / `m2-dispatcher` | `operator-policy.json` / `runtime-profiles.yaml` allowlist |
| **`friction` (`L-Friction`)** | `F2_owner_abort_rate` | Sessions ending in `abort` or `session.error` | Active role skill (`m2-dispatcher` / `team-lead`) | Watchdog timeout & fail-fast recovery |
| **`friction` (`L-Friction`)** | `F3_mid_flight_owner_correction` | Mid-flight Owner user messages correcting an active autonomous execution | `personal-m2-assistant` / `m2-dispatcher` / `team-lead` | Phase 0 spec alignment checklist |

---

## 4. Version Watermark Delta (`vN-1@sha -> vN@sha`) & Dual-Flywheel Guardrails

- **Version Watermark Split**: Every event is compared against the target skill's `resolve_skill_version()` (`v<commit_count>@<subtree_sha>` and `last_modified_epoch`). Events older than the current version watermark are bucketed into `pre_fix` (`watermarked_resolved`), while only `post_fix` (`>= watermark`) anomalies trigger new Stage 2/3 dispatches.
- **Fast (`N=1`) vs. Slow (`N>=50`) Flywheel**:
  - **Objective Bug / `exit=2` Roadblock / Ungrounded Receipt**: Fix immediately in Fast Flywheel with **Positive + Counter-Case Dual Replay**.
  - **Scenario-Specific Need**: Encode as a **scenario-conditioned branch**; never mutate global defaults.
  - **Low-Sample Anecdote (`<3` occurrences)**: Hold in `observation_pool` until `>=3` occurrences or Slow Flywheel cohort (`N>=50` sessions) confirms.
  - **Low-Frequency Scenario Protection**: Never prune drawers or rules for scenarios with `scenario_sessions < 5`; apply **One-In-One-Out** when at budget cap.

---

## 5. Independent M3 Rollup (`[Agent Retro YYYY-MM-DD]`) & Multica Autopilot

- **`rollup --issue-brief` & `file-issue`**: Aggregates all `K` M1 receipts (`FIXED_AND_MERGED`, `HEALTHY_NO_ISSUES`, `CROSS_SKILL_RELAY`), 5-layer anomaly counts, and `observation_pool` into a Dual-Zone Markdown issue (`<=45` human lines, plain-Chinese `**一句话结论**：...`, concrete non-boilerplate `- [ ]` items, zero forbidden internal codes/UUIDs/SHAs/CLI commands in Human Zone, and full JSON/CLI evidence inside `<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>`) verified by `verify_issue_format.py`.
- **`setup-autopilot`**: Configures or updates the Multica scheduled `run_only` autopilot (`Daily Agent Trajectory Retro Autopilot`, cron `40 4 * * *` `Asia/Shanghai`) without touching `skill-retro`'s `04:20` schedule.
