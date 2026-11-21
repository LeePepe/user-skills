---
name: skill-retro
description: 3-stage Skill Progressive Self-Evolution: sub-second wide-probe script + Jev fast-thinking triage into single-skill Partial (--aspects l0,l1,l2,l3,creative,eval) or Full 6-Dimension deep reasoning via M1 TL -> Writer -> Reviewer. Not for product crash/TestFlight telemetry or narrative blog extraction.
---
# Skill Retro（三段式渐进式自演进：Fast Probe -> Jev Triage -> Scoped 6D Deep Reasoning）

`skill-retro` runs a 3-stage loop so Owner never reads raw traces and agents never over-correct global rules from single-scene anecdotes:
1. **Stage 1 · Sub-Second Probe (`probe [--skill <name>] [--aspect <dim>] --fast`)**: Supports **Global Sweep Mode** (`<1s` across all skills) and **Direct Single-Skill Mode (`--skill <name>`)** (millisecond probe of only that skill, bypassing global scan).
2. **Stage 2 · Cheap Worker + Jev Triage (`--role explorer` / `--role mechanical` under `1 M2` + `triage [--skill <name>] [--pick-top 3 | --pick-one] --use-jev`)**: Executed by a **Cheap Worker slot** (`worker` tier `mai-code-2-flash` / `gpt-5.6-luna` + `jev_client.py` `jev_decide`, `0` steering tokens) under **1 M2 Coordinator** with **ZERO Owner approval gate before fixing** and a **`0..3` Honest Upper Cap (`--pick-top 3` max, NEVER a forced quota)**: picks `K` (`0..3`) disjoint skills, emits `K` isolated per-skill M1 Goal specs (`handoff/skill-retro-<skill>/spec.md`), and returns to M2.
3. **Stage 3 · `1 M2 -> K Parallel M1 TLs -> M2 Rollup` (`1 Skill = 1 Dedicated M1 TL`)**: Deep reasoning belongs to **M1**! For `K` (`1..3`) picked skills, the **single M2** dispatches **`K` separate M1 TL agents in parallel** (`skill-retro-<sk>-goal-tl-r1`, each with its own `handoff/skill-retro-<sk>/spec.md` and `worktrees/skill-retro-<sk>`). Each M1 TL performs the deep investigation for its single skill: if healthy after deep reasoning, M1 reports `HEALTHY_NO_ISSUES` **without spawning a Writer or Reviewer**; if a real fix is needed, M1 runs its own `Writer -> Reviewer`, merges the PR, and all `K` M1s roll up their final receipts (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) back to the **single M2** for the daily `[Skill Retro YYYY-MM-DD]` summary issue.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| Stage 1 wide or single-skill probe | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py probe [--skill <name>] --fast --json` |
| Stage 2 Jev triage (`--pick-top 3` / `--pick-one`) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py triage [--pick-top 3 \| --pick-one] --use-jev --emit-briefs ~/Development/handoff/<goal> --json` |
| Stage 3 partial / full check | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py emit-briefs [--skill <name>] --pick-one --mode full --emit-briefs ~/Development/handoff/<goal> --json` |
| Cross-skill Host->Guest relay | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py emit-relay --from <host> --to <guest> --requirement "<spec>" --emit-briefs ~/Development/handoff/<goal> --json` |
| verification | `references/eval-cases.json` | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill skill-retro --strict` |

## Role-Based Routing (Role-to-Stage Cost Split)

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Explorer / Mechanical (Stage 1+2 Cheap Probe Worker: `mai-code-2-flash` / `worker` tier + Jev) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff/<goal> --json` |
| Role: M2-Dispatcher | `references/progressive-evolution-playbook.md` | `skills/team-lead/scripts/herdr-dispatch.sh --role explorer --name skill-retro-probe-r1` |
| Role: M1-TL (Stage 3 Deep Reasoning only) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py emit-briefs --skill <name> --mode full --emit-briefs ~/Development/handoff/<goal> --json` |
| Role: Writer | `references/progressive-evolution-playbook.md` | `python3 skills/skill-contract/scripts/skill_contract.py audit --skill <name> --strict` |
| Role: Reviewer | `references/eval-cases.json` | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill <name> --strict` |

## The 6 Problem-Discovery Dimensions (`l0,l1,l2,l3,creative,eval`)

1. **`l0` · L0 Direction vs Healthy `R1->R2` Progressive Convergence**: Separate true directional drift (`r3+` / `CHANGE_REQUEST`), trigger misses (domain edits without loading the skill), and L0 over-stuffing from healthy `R1->R2` detail convergence.
2. **`l1` · L1 Scenario-Aware Reference Metabolism**: Cross-check `references/*` hits against scenario occurrence (`hits` vs `scenario_sessions`): **Hot Drawers** (sink top lines), **On-Demand Correction Drawers** (healthy `exit=1 -> read -> pass`), **Unwired Drawers** (failed without reading -> add Self-Disclosing Error pointer), and **Protected Low-Frequency (`<5` sessions) vs True Deadwood (`>=5` sessions, `0` hits)**.
3. **`l2` · L2 Script Efficacy & 4-Angle Slow Script Deep-Dive**: Classify **True Catchers (`fail->pass`)**, **Ceremonial Rubber-Stamps (`100% exit=0` with escapes)**, **Rigid Roadblocks (`exit=2` CLI friction with redacted failing samples)**, and **Latency Sinks (`>180s` or `P50>=30s`)** attributed across **4 angles**: (a) external remote wait vs local CPU, (b) redundant nested re-runs, (c) `git diff` path-scoped selection, (d) parallel DAG sharding & watermark cache.
4. **`l3` · L3 Wild Pattern Soft Crystallization**: Detect manual shell probes (`>=5x`) and crystallize under **3 Soft-Script Principles**: facts-only (no subjective taste lock), self-disclosing error (`<=3` lines + `references/*.md`), and Hard Lock (`exit=1`) vs Soft Advisory (`exit=0`).
5. **`creative` · Cross-Session Creative & Product Spectrum**: Audit `preview-selection.json` stars/notes/rejections for **Variant Homogenization** and enforce **Tier 1 Universal Invariants** + **Tier 2 Product Persona Intent Weighting** + **Tier 3 `2+1+1` Exploration Quota** (`2` persona-deepening + `1` cross-domain + `1` wildcard).
6. **`eval` · 3-Tier Eval & Fast/Slow Flywheel Guardrails**: Audit **L1 Baseline / L2 Diversity / L3 Human-Taste Eval** coverage; enforce **Fast Flywheel (`N=1`) 3-Question Scope Triage + Positive & Counter-Case Dual Replay** vs **Slow Flywheel (`N>=50`) Wide-Probe Multi-Angle Diagnosis + Golden Benchmark Replay**.

## Canonical 3-Stage Prompt Templates & Cross-Skill Anti-Collision Rules

- **Stage 1+2 Prompt (`1 M2` -> Cheap Probe Worker `--role explorer` / `--role mechanical` -> Emit `K=0..3` Per-Skill M1 Specs)**:
  > Under `1 M2`, dispatch a Cheap Probe Worker via `herdr-dispatch.sh --role explorer` (`worker` tier `mai-code-2-flash` / `gpt-5.6-luna`; NEVER burn `group/steering` tokens on Stage 1+2). Run `skill_retro.py probe [--skill <sk>] --fast --json` and `skill_retro.py triage [--skill <sk>] [--pick-top 3 | --pick-one] --use-jev --emit-briefs ~/Development/handoff --json`. With **zero Owner approval gate before fixing**, select `K` (`0..3` upper cap, NEVER a forced quota) disjoint skills, emit each skill's isolated `~/Development/handoff/skill-retro-<sk>/spec.md`, and return to M2.
- **Stage 3A Prompt (`1 M2 -> K Parallel M1 TLs` — Partial Check `--mode partial --aspects <list>`)**:
  > For each picked skill (`K=1..3`), the single M2 dispatches 1 dedicated M1 TL (`herdr-dispatch.sh --new-workspace --role team_lead --name skill-retro-<sk>-goal-tl-r1`) reading `~/Development/handoff/skill-retro-<sk>/spec.md` in `~/Development/worktrees/skill-retro-<sk>`. M1 conducts deep reasoning for `<sk>`: if healthy, M1 records `HEALTHY_NO_ISSUES` (`0` Writers spawned); if real fix needed, M1 dispatches `Writer -> Reviewer` scoped strictly to `skills/<sk>/**` + `scripts/test_<sk>_*.py`, merges the PR, and rolls up to M2.
- **Stage 3B Prompt (`1 M2 -> K Parallel M1 TLs` — Full 6-Dimension Check & Host/Guest Relay `--mode full`)**:
  > Each dedicated M1 TL (`1 M1 per skill`) executes full 6-Dimension (`l0,l1,l2,l3,creative,eval`) deep reasoning for `<sk>`. If healthy, M1 reports `HEALTHY_NO_ISSUES` (`0` Writers); if a Guest Skill needs changes without `--coupled-with`, M1 emits a Wave 2 Relay (`emit-relay --from <host> --to <guest>`); all `K` M1s roll up their final receipts (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) back to the single M2 for the daily `[Skill Retro YYYY-MM-DD]` summary issue.

See [progressive-evolution-playbook.md](references/progressive-evolution-playbook.md) and [eval-cases.json](references/eval-cases.json).
