---
name: skill-retro
description: "3-stage Skill Progressive Self-Evolution: sub-second wide-probe script + Jev fast-thinking triage under 1 M2 into K (0..3) parallel per-skill M1 TL deep-reasoning investigations (each M1 TL runs Writer -> Reviewer only if needed) and rolls up receipts to 1 M2. Not for product crash telemetry or blog extraction."
---
# Skill Retro（三段式渐进式自演进：Fast Probe -> Jev Triage -> `1 M2 -> K (0..3) Parallel M1 TLs -> M2 Rollup`）

`skill-retro` runs a 3-stage loop so Owner never reads raw traces and agents never over-correct global rules from single-scene anecdotes:
1. **Stage 1 · Sub-Second Probe (`probe [--skill <name>] [--aspect <dim>] --fast`)**: Supports **Global Sweep Mode** (`<1s` across all skills) and **Direct Single-Skill Mode (`--skill <name>`)** (millisecond probe of only that skill, bypassing global scan).
2. **Stage 2 · Cheap Worker + Jev Triage (`--role explorer` / `--role mechanical` under `1 M2` + `triage [--skill <name>] [--pick-top 3 | --pick-one] --use-jev`)**: Executed by **1 Cheap Probe Worker** (`worker` tier `mai-code-2-flash` / `gpt-5.6-luna` + `jev_client.py` `jev_decide`, `0` steering tokens) under **1 M2 Coordinator** with **ZERO Owner approval gate before fixing** and a **`0..3` Honest Upper Cap (`--pick-top 3` max, NEVER a forced quota)**: `K == 0` reports `HEALTHY_NO_ISSUES` (`0` M1 TLs spawned); `K = 1..3` disjoint skills emit isolated M1 Goal specs (`handoff/skill-retro-<skill>/spec.md`) + `m2_dispatch_plan`.
3. **Stage 3 · `1 M2 -> K (0..3) Parallel M1 TLs -> Rollup to Single M2` (`1 Skill = 1 Independent M1 TL`)**: Deep reasoning belongs to **M1**! The **single M2** spawns **`K` parallel M1 TLs** (`skill-retro-<sk>-goal-tl-r1`, isolated in `~/Development/worktrees/skill-retro-<sk>`). If healthy after deep reasoning, **M1 TL writes `HEALTHY_NO_ISSUES` without spawning Writer or Reviewer**; if a real fix is confirmed, M1 TL runs `Writer -> Reviewer` (filtering blind-review claims through `skill_retro.py verify-findings`), merges its PR, and all `K` M1 TLs roll up receipts (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) to the **single M2** via `skill_retro.py rollup` for `[Skill Retro YYYY-MM-DD]`.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| Stage 1 wide or single-skill probe | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py probe [--skill <name>] --fast --json` |
| Stage 2 Jev triage (`--pick-top 3` / `--pick-one`) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py triage [--pick-top 3 \| --pick-one] --use-jev --emit-briefs ~/Development/handoff --json` |
| Cross-repo gate & slow `verify` self-driven loop (`repo-kit`) | `references/progressive-evolution-playbook.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py loop-check --dev-root ~/Development --json` |
| Stage 3 emit `K` per-skill M1 specs | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py emit-briefs [--skill <name>] [--pick-top 3 \| --pick-one] --mode full --limit-sessions 60 --emit-briefs ~/Development/handoff --json` |
| Cross-skill Host->Guest relay | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py emit-relay --from <host> --to <guest> --requirement "<spec>" --emit-briefs ~/Development/handoff --json` |
| Blind-review Repro-Filter (`verify-findings`) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py verify-findings --findings-json <file_or_inline> --json` |
| Decoupled Context Uptake check | `references/uptake_map.json` (Script-Only Data Drawer — query via L2 CLI, never full-file view) | `python3 skills/skill-retro/scripts/skill_retro.py uptake-check [--skill <name>] [--target-dir <dir>] --json` |
| Rollup `K` M1 receipts to single M2 | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py rollup --handoff-root ~/Development/handoff --json` |
| verification | `references/eval-cases.json` (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill skill-retro --strict` |

## Role-Based Routing (Role-to-Stage Cost Split)

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Explorer / Mechanical (Stage 1+2 Cheap Probe Worker: `mai-code-2-flash` / `worker` tier + Jev) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff --json` |
| Role: M2-Dispatcher (`1 M2` Coordinator: Probe -> `0..3` M1 TLs -> Rollup) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py rollup --handoff-root ~/Development/handoff --json` |
| Role: M1-TL (`1 M1 TL per skill`: Stage 3 Deep Reasoning in `worktrees/skill-retro-<name>`) | `references/progressive-evolution-playbook.md` | `python3 skills/skill-retro/scripts/skill_retro.py diagnose --skill <name> --mode full --json` |
| Role: Writer | `references/progressive-evolution-playbook.md` | `python3 skills/skill-contract/scripts/skill_contract.py audit --skill <name> --strict` |
| Role: Reviewer | `references/progressive-evolution-playbook.md` | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill <name> --strict` |

## The 6 Problem-Discovery Dimensions (`l0,l1,l2,l3,creative,eval`)

1. **`l0` · L0 Direction vs Healthy `R1->R2` Progressive Convergence (`S0`/`S1`)**: Separate true directional drift (`r3+` / `CHANGE_REQUEST`), trigger misses (domain edits without loading the skill), and L0 over-stuffing from **healthy `R1->R2` detail convergence (exempted from `priority_score`)**.
2. **`l1` · L1 Scenario-Aware Reference Metabolism & Progressive Disclosure Exemption (`S1`)**: Cross-check `references/*` hits against `scenario_sessions`: **On-Demand Correction Drawers** (`progressive_catch_count`: healthy `exit=1 -> read -> pass`, never penalized), **Wasted Cold-Start Pre-Reads (`wasted_preread_rate`: `>=3` drawers before any script/edit or `0` downstream action)**, **Hot Drawers**, **Unwired Drawers** (`domain_failures > 0`, `hits == 0`, missing script pointer), and **Protected Low-Frequency (`<5` sessions) vs True Deadwood (`>=5` sessions, `0` hits)**.
3. **`l2` · L2 Script Efficacy, S2 Internal Reality Check, ACI Bloat (`>20KB`) & 4-Angle Speed (`S2`/`S3`)**: Classify **True Catchers (`fail->pass`)**, **Ceremonial Rubber-Stamps (`100% exit=0` with escapes)**, **Rigid Roadblocks (`exit=2` CLI friction)**, **S2 Internal Reality (`internal_doc_cli_drift` scanning `.md` + `scripts/**/*.py` string literals across local/cross-skill `argparse` AST + `internal_broken_refs`)**, **ACI Output Bloat (`aci_output_bloat_count`: stdout `>20KB`, remediated via `--summary`)**, **Cross-Repo Gate Drift (`RG-001..006` via `repo_gate_auditor.py`)**, and **Latency Sinks (`>180s` or `P50>=30s`)** across **4 angles**: (a) external wait vs local CPU, (b) redundant nested re-runs, (c) `git diff` path-scoped selection, (d) parallel DAG & watermark cache.
4. **`l3` · L3 Wild Pattern Soft Crystallization**: Detect manual shell probes (`>=5x`) under **3 Soft-Script Principles**: facts-only (no subjective taste lock), self-disclosing error (`<=3` lines + `references/*.md`), and Hard Lock (`exit=1`) vs Soft Advisory (`exit=0`).
5. **`creative` · Cross-Session Creative & Product Spectrum**: Audit `preview-selection.json` stars/notes/rejections for **Variant Homogenization** and enforce **Tier 1 Universal Invariants** + **Tier 2 Product Persona Intent Weighting** + **Tier 3 `2+1+1` Exploration Quota** (`2` persona-deepening + `1` cross-domain + `1` wildcard).
6. **`eval` · 3-Tier Eval, S4 Counter-Case Ratio (`>=25%`), Escape Gap & Rollup Receipt Grounding (`S4`)**: Audit **L1/L2/L3 Eval** coverage, `eval_counter_case_ratio` (`bad_snippet` / `negative_cmd`, target `>=0.25`), `eval_escape_gap` (`domain_failures + skill_escapes >= 3` with `eval_case_weekly_delta == 0`), **Receipt-to-Trace Grounding** (`receipt_grounded` on `FIXED_AND_MERGED` `commit_sha`/`pr_url` in `rollup`), and **Fast Flywheel (`N=1`) Dual Replay** vs **Slow Flywheel (`N>=50`)**.

## 3-Layer Architecture Audit & Zero-Owner-Referee Flow (`verify-findings`)

- **Layer A/B (Soft AST, Package & Eval Hygiene -> Periodic `skill-retro` + `eval`)**: Surface `soft_quality_advisories` (`malformed-code-fence`, `ast-namespace-pollution`, `boilerplate-table-decay`, `vacuous-good-snippet`, `fake-prose-echo-test` / `prose_only_test_advisories` — all `soft_advisory_exit_0`) alongside `.py`/`.md` AST `internal_reality_check` and `>=25%` counter-case replay without blocking commits.
- **Layer C (`verify-findings` 3-Gate Repro-Filter)**: Gate 1 (`No Repro, No Fix`, `<=10s` `repro_cmd`) drops unreproduced claims (`FALSE_POSITIVE_UNREPRODUCED` -> `AUTO_DROP`); Gate 2 routes reproduced `contract_breach` -> **Class A `AUTO_FIX`**, `architecture_smell` -> **Class B `AUTO_DROP`**, and reproduced `tradeoff` -> **Class C `OWNER_DECISION_BRIEF`**.

## Canonical 3-Stage Prompt Templates & Cross-Skill Anti-Collision Rules

- **Stage 1+2 Prompt (`1 M2 -> 1 Cheap Probe Worker` -> Emit `K=0..3` Per-Skill M1 Goal Specs + `m2_dispatch_plan`)**:
  > Under `1 M2`, dispatch 1 Cheap Probe Worker (`--role explorer` / `--role mechanical`, `worker` tier `mai-code-2-flash` / `gpt-5.6-luna`; NEVER `group/steering`). Run `skill_retro.py probe [--skill <sk>] --fast --json` and `skill_retro.py triage [--skill <sk>] [--pick-top 3 | --pick-one] --use-jev --emit-briefs ~/Development/handoff --json`. With **zero Owner approval gate**, select `K` (`0..3` cap, NEVER a forced quota) disjoint skills (`K=0` -> `HEALTHY_NO_ISSUES`, `0` M1 TLs); else emit `K` specs (`~/Development/handoff/skill-retro-<sk>/spec.md`) + `m2_dispatch_plan`.
- **Stage 3A Prompt (`1 M2 -> K Parallel M1 TLs` — Partial Check `--mode partial --aspects <list>`)**:
  > For each picked skill (`K=1..3`), the single M2 spawns 1 independent M1 TL (`herdr-dispatch.sh --new-workspace --role team_lead --name skill-retro-<sk>-goal-tl-r1 --cwd ~/Development/user-skills`) reading `~/Development/handoff/skill-retro-<sk>/spec.md` in `~/Development/worktrees/skill-retro-<sk>`. If healthy, M1 TL writes `HEALTHY_NO_ISSUES` (`0` Writers/Reviewers); if confirmed, M1 TL runs `Writer -> Reviewer` scoped strictly to `skills/<sk>/**` + `scripts/test_<sk>_*.py`, merges the PR, and writes its R10 receipt.
- **Stage 3B Prompt (`1 M2 -> K Parallel M1 TLs -> Single M2 Rollup` — Full 6-Dimension Check & Host/Guest Relay `--mode full`)**:
  > Each M1 TL (`1 M1 TL per skill`) runs full 6-Dimension (`l0,l1,l2,l3,creative,eval`) deep reasoning for `<sk>`. If healthy, write `HEALTHY_NO_ISSUES` (`0` Writers); if a Guest Skill needs changes without `--coupled-with`, emit a Wave 2 Relay (`emit-relay --from <host> --to <guest>`). Once all `K` M1 TLs finish, the single M2 runs `skill_retro.py rollup --handoff-root ~/Development/handoff --json` to aggregate `FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY` into `[Skill Retro YYYY-MM-DD]`.

## Runtime and execution guardrails

`diagnose` defaults to `--limit-sessions 60`; pass `--deep-all` only for explicit historical scans. Generated `spec.md` and `briefs/*.md` embed redacted failing samples, exact `SKILL.md` ↔ argparse drift, and script paths so M1 never reverse-engineers the scorer. M1 TL must not read `skills/skill-retro/scripts/**` or pre-read full target scripts before dispatching Writer; dispatch via `herdr-dispatch.sh --role writer` with an explicit coding model (never inherited `group/steering`) and expanded absolute paths rather than volatile `/tmp` relative paths.

See [progressive-evolution-playbook.md](references/progressive-evolution-playbook.md), [uptake_map.json](references/uptake_map.json) (Script-Only Data Drawer — query via L2 CLI, never full-file view), and [eval-cases.json](references/eval-cases.json) (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view).
