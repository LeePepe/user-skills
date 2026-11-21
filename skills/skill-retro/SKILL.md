---
name: skill-retro
description: 3-stage Skill Progressive Self-Evolution: sub-second wide-probe script + Jev fast-thinking triage into single-skill Partial (--aspects l0,l1,l2,l3,creative,eval) or Full 6-Dimension deep reasoning via M1 TL -> Writer -> Reviewer. Not for product crash/TestFlight telemetry or narrative blog extraction.
---
# Skill Retro（三段式渐进式自演进：Fast Probe -> Jev Triage -> Scoped 6D Deep Reasoning）

`skill-retro` runs a 3-stage loop so Owner never reads raw traces and agents never over-correct global rules from single-scene anecdotes:
1. **Stage 1 · Sub-Second Probe (`probe [--skill <name>] [--aspect <dim>] --fast`)**: Supports **Global Sweep Mode** (`<1s` across all skills) and **Direct Single-Skill Mode (`--skill <name>`)** (millisecond probe of only that skill, bypassing global scan).
2. **Stage 2 · Cheap Worker + Jev Triage (`--role explorer` / `--role mechanical` + `triage [--skill <name>] [--pick-top 3 | --pick-one] --use-jev`)**: Executed by a **Cheap Worker slot** (`worker` tier `mai-code-2-flash` / `gpt-5.6-luna` + `jev_client.py` `jev_decide`, `0` steering tokens) with **ZERO Owner approval gate before fixing** and a **`0..3` Honest Upper Cap (`--pick-top 3` max, NEVER a forced quota)**: if only 1–2 skills have real evidence-backed issues, pick only 1–2; if `0` issues are found (including when inspecting a single `--skill <name>`), spawn `0` Writers and report `HEALTHY_NO_ISSUES` with the inspected telemetry.
3. **Stage 3 · Autonomous Execution, PR Merge & `[Skill Retro YYYY-MM-DD]` Summary (`M1 TL -> 0..3 Writers -> 0..3 Reviewers`)**: Executes only the `1..3` evidence-backed disjoint skills end-to-end (`M1 TL -> parallel Writers -> Reviewers -> PR merge`), schedules any Guest Skill contract change as a Wave 2 **Cross-Skill Relay Brief (`emit-relay`)**, and writes the completed results (or `HEALTHY_NO_ISSUES`) into the daily `[Skill Retro YYYY-MM-DD]` summary issue.

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

- **Stage 1+2 Prompt (Cheap Probe Worker `--role explorer` / `--role mechanical` -> Zero-Gate Daily `0..3` Upper Cap or Targeted `--pick-one`)**:
  > Dispatch via `herdr-dispatch.sh --role explorer` (or `--role mechanical`, `worker` tier `mai-code-2-flash` / `gpt-5.6-luna`; NEVER burn `group/steering` tokens on Stage 1+2). Run `skill_retro.py probe [--skill <sk>] --fast --json` and `skill_retro.py triage [--skill <sk>] [--pick-top 3 | --pick-one] --use-jev --emit-briefs ~/Development/handoff/<goal> --json`. With **zero Owner approval gate before fixing**, select `0..3` disjoint skills that have real evidence-backed issues (`3` is an upper cap, NEVER a forced quota: if `0` issues exist, spawn `0` Writers and report `HEALTHY_NO_ISSUES`); otherwise emit their isolated briefs so `M1 -> Writers -> Reviewers` fixes, merges PRs, and logs the completed results into the daily `[Skill Retro YYYY-MM-DD]` summary issue.
- **Stage 3A Prompt (Single-Skill Partial Check — `--mode partial --aspects <list>`)**:
  > Run `skill_retro.py emit-briefs [--skill <sk>] --pick-one --mode partial --aspects <aspects> --emit-briefs ~/Development/handoff/skill-retro-<sk> --json`. Dispatch `M1 TL -> Writer -> Reviewer` scoped strictly to `skills/<sk>/**` and `scripts/test_<sk>_*.py` (never shared `README.md`/`marketplace.json`), enforcing Self-Disclosing Errors and Positive + Counter-Case Replay.
- **Stage 3B Prompt (Single-Skill Full 6-Dimension Check & Host/Guest Relay — `--mode full`)**:
  > Run `skill_retro.py emit-briefs [--skill <sk>] --pick-one --mode full [--coupled-with <guest>] --emit-briefs ~/Development/handoff/skill-retro-<sk> --json`. Execute full 6-Dimension (`l0,l1,l2,l3,creative,eval`) diagnosis; if a Guest Skill needs changes without `--coupled-with`, emit a Wave 2 Relay (`emit-relay --from <host> --to <guest> --requirement "..."`) instead of editing `skills/<guest>/**` out-of-bounds.

See [progressive-evolution-playbook.md](references/progressive-evolution-playbook.md) and [eval-cases.json](references/eval-cases.json).
