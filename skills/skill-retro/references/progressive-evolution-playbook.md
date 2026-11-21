# Progressive Self-Evolution Playbook (`Probe -> Jev Triage -> Scoped 6D Deep Reasoning`)

> Core Philosophy: Treat runtime friction and `R1 -> R2` convergence as **runtime progressive disclosure**. Keep `SKILL.md` focused on direction and hard redlines; rely on L2 scripts and L1 drawers to disclose details on demand. Owner acts as **Judge** (selecting options), never **Detective** (reading raw traces).

## 1. Three-Stage Execution Architecture (`1 M2 -> 1 Cheap Probe Worker -> K (0..3) Parallel M1 TLs -> Rollup to Single M2`)

| Stage | Command / Engine | Role & Model Cost Tier (`runtime-profiles.yaml`) | Output & Routing Contract |
|---|---|---|---|
| **Stage 1 · Ultra-Lightweight Script Probe** | `skill_retro.py probe [--skill <sk>] [--aspect <dim>] --fast --json` | `0` Token · `< 1s` deterministic script | Supports **Global Sweep Mode** (all 28 skills) and **Direct Single-Skill Mode (`--skill <sk>`)** (millisecond probe of only that skill, bypassing global scan). |
| **Stage 2 · Cheap Worker + Jev Fast-Thinking Triage (`1 M2`)** | `skill_retro.py triage [--skill <sk>] [--pick-top 3 \| --pick-one] [--use-jev] [--emit-briefs <dir>] --json` | **1 Cheap Worker (`--role explorer` / `--role mechanical`, `worker` tier `mai-code-2-flash` / `gpt-5.6-luna` + Jev `timeout=1.5s`) under `1 M2` — NEVER `group/steering`!** | Picks `K` (`0..3` honest cap) disjoint skills: if `K=0`, reports `HEALTHY_NO_ISSUES` (`0` M1 TLs spawned); if `K=1..3`, writes `K` per-skill M1 Goal specs (`<dir>/skill-retro-<sk>/spec.md`) + `m2_dispatch_plan` and returns to M2. |
| **Stage 3 · `1 M2 -> K Parallel M1 TLs -> Single M2 Rollup`** | `skill_retro.py emit-briefs [--skill <sk>] [--pick-top 3 \| --pick-one] --mode full\|partial [--aspects ...] --emit-briefs <dir> --json` + `skill_retro.py rollup` | **`1 Independent M1-TL per Skill` (`K=1..3` parallel `skill-retro-<sk>-goal-tl-r1`) -> (`Writer -> Reviewer` if needed) -> Rollup to `1 M2`** | Deep reasoning belongs to **M1**! Each M1 TL investigates its 1 assigned skill in `~/Development/worktrees/skill-retro-<sk>`: if healthy, M1 TL writes a `HEALTHY_NO_ISSUES` receipt (`0` Writers/Reviewers); if real fix needed, M1 TL runs `Writer -> Reviewer`, merges PR, and all `K` M1s roll up receipts to `1 M2` via `skill_retro.py rollup`. |

### Stage 2 Jev Decision & Deterministic Fallback Rules
- **Questions sent to `jev_decide`**:
  - `route_mode` (`choice`): `skip` (healthy or low-sample noise) | `partial` (localized anomaly in 1–3 aspects) | `full` (multi-layer drift, creative homogenization, or `>=3` aspects triggered).
  - `noul` (`noul`): Companion applicability confidence in `[0.0, 1.0]`. `noul > 0.70` confirms action; `< 0.30` forces `skip`; `0.30..0.70` or offline Jev uses deterministic aspect rules:
    - Only CLI `exit=2`, rubber-stamp, or slow script -> `partial` (`--aspects l2`)
    - Unwired drawer (`exit=1` without reading ref) + script friction -> `partial` (`--aspects l1,l2`)
    - Repeated manual shell probes (`>=5x`) -> `partial` (`--aspects l3`)
    - Design variant homogenization or `>=3` aspects triggered -> `full` (`--aspects l0,l1,l2,l3,creative,eval`).

---

## 2. Six Problem-Discovery Dimensions (`l0, l1, l2, l3, creative, eval`)

| Aspect Key | Dimension | Diagnostic Signal (Multi-Angle Cross-Check) | Mandatory Evolution Action |
|---|---|---|---|
| **`l0`** | **Dim 1 · L0 Direction vs Progressive Convergence** | Separate **true directional drift** (`r3+` failures, `CHANGE_REQUEST`, wrong repo/architecture) and **trigger misses** (editing domain files without loading the skill) from **healthy `R1 -> R2` detail convergence**; detect **0-action loads** and L0 bloat (`>140` lines). | Adjust `SKILL.md` frontmatter trigger keywords or top routing table only on true drift/miss; never bloat L0 to force 100% R1 perfection on edge cases. |
| **`l1`** | **Dim 2 · L1 Scenario-Aware Reference Metabolism** | Cross-check `references/*.md` hits against `scenario_sessions`:<br>• **Hot Drawer** (`hit_rate >= 60%`, `>=3` sessions)<br>• **On-Demand Correction Drawer** (`exit=1 -> read drawer -> exit=0`)<br>• **Unwired Drawer** (`domain_failures >= 1`, `0` reads, unlinked in script stderr)<br>• **Protected Low-Frequency (`<5` sessions) vs True Deadwood (`>=5` sessions, `0` hits)** | • Sink repeated hot-drawer commands into scripts<br>• Preserve on-demand drawers as healthy disclosure<br>• Wire **Self-Disclosing Error** stderr pointer (`<=3` lines + `skills/<sk>/references/<doc>.md`)<br>• Never delete 0-hit drawers when `scenario_sessions < 5`. |
| **`l2`** | **Dim 3 · L2 Script Efficacy & 4-Angle Speed Deep-Dive** | Classify scripts into:<br>1. **True Catcher** (`fail -> pass` in session)<br>2. **Ceremonial Rubber-Stamp** (`100% exit=0` while Reviewer catches escapes)<br>3. **Rigid Roadblock** (`exit=2` CLI argument friction + redacted failing samples)<br>4. **Latency Sink** (`>180s` or `P50 >= 30s`) | • Soften `exit=2` roadblocks with flag aliases & positional defaults<br>• Deepen rubber-stamp assertions or merge<br>• Apply **4-Angle Speedup** on slow scripts:<br>  (a) `external_wait` vs local CPU truth<br>  (b) eliminate redundant `nested_reruns`<br>  (c) `git diff --name-only` path-scoped selection<br>  (d) multi-core parallel DAG sharding & content-hash watermarks. |
| **`l3`** | **Dim 4 · L3 Wild Pattern Soft Crystallization** | Scan cross-session bash history for repeated manual probes (`>=5x` `python3 -c`, `sqlite3`, `jq`, `git`, `sips`) and recurring review feedback. | Crystallize under **3 Soft-Script Principles**:<br>1. Compute objective facts only (never hard-lock subjective taste)<br>2. Print self-disclosing error pointers to `references/*.md`<br>3. Use **Hard Lock (`exit=1`)** for physical redlines vs **Soft Advisory (`exit=0`)** for heuristics. |
| **`creative`** | **Dim 5 · Cross-Session Creative & Product Spectrum** | Inspect `preview-selection.json` stars, rejections, notes, and variant titles across sessions to detect **Variant Homogenization / Collapse**. | Enforce **3-Tier Creative Spectrum**:<br>• **Tier 1 Universal Invariants** (alignment, anti-truncation, platform navigation firewall)<br>• **Tier 2 Product Persona & Intent Weighting** (primary anchor vs muted secondary budget per product)<br>• **Tier 3 Controlled Exploration (`2+1+1` Quota)**: `2` persona-deepening + `1` cross-domain borrow + `1` bold wildcard. |
| **`eval`** | **Dim 6 · 3-Tier Eval, Context Uptake Triad & Flywheel Guardrails** | Audit **L1 Baseline Eval** (`eval-cases.json`), **L2 Diversity Eval**, **L3 Human Taste Eval** (`jev-star`), and the **Trigger Quality -> Context Uptake (`references/uptake_map.json`) -> Outcome Delta** triad (`uptake-check`: 4-state `passed` / `failed` / `not_applicable` / `unavailable`, engagement gating for negative checks, `skills_unavailable` vs `skills_available_unmentioned` vs `skills_available_mentioned`). | Enforce Dual-Flywheel anti-bias:<br>• **Fast Flywheel (`N=1`) Scope Triage** + **Positive & Counter-Case Dual Replay**<br>• **Slow Flywheel (`N>=50` sessions)** Wide-Probe Multi-Angle Diagnosis + Golden Benchmark Replay. |

---

## 3. Fast Flywheel Scope Triage (3 Questions) & Dual-Case Replay

Before any single-session feedback modifies a skill or script, `Writer` and `Reviewer` must answer:
1. **Is it an objective global bug or CLI `exit=2` roadblock?** -> Fix global script/alias and add **Positive + Counter-Case Replay**.
2. **Is it a scenario/persona-specific requirement (e.g., "make this dense cockpit compact")?** -> **NEVER mutate global defaults!** Encode as a **scenario-conditioned branch** (e.g., `density == "compact"`), replay **Positive Case** (compact scene passes) + **Counter-Case** (standard breathing-room scene remains unchanged).
3. **Is evidence ambiguous (`<3` occurrences, subjective anecdote)?** -> Fix local project code only and hold in `observation_pool` until `>=3` sessions confirm the pattern.

---

## 4. Cross-Skill Cooperation (Host/Guest Relay Protocol) & Parallel Anti-Collision Locks

### 4.1 Host/Guest Cross-Skill Protocol
Each deep reasoning task focuses on **1 Host Skill** (`skills/<host>/**` + `scripts/test_<host>_*.py`). When a **Guest Skill** is implicated:
1. **Weak Coupling (Adapter / Alias)**: Add CLI compatibility aliases or fallback adapters inside the Host Skill without touching `skills/<guest>/**`.
2. **Cross-Skill Contract Change (Wave 2 Relay)**: Run `skill_retro.py emit-relay --from <host> --to <guest> --requirement "..." --emit-briefs <handoff_dir>` (or pass `--relay-to <guest> --relay-requirement "..."` to `emit-briefs`). This writes a structured **Cross-Skill Relay Brief** (`cross_skill_relays`) scheduled for **Wave 2** targeting `<guest>`, strictly forbidding out-of-bounds edits to `skills/<guest>/**` inside `<host>`'s worktree.
3. **Tightly Coupled Pair (`--coupled-with <guest>`)**: Only when explicitly declared via `--coupled-with <guest>`, lock both skills into the same single worktree/task (`Owned Paths`: `skills/<host>/**`, `skills/<guest>/**`, and their dedicated tests) so no parallel agent touches either skill.

### 4.2 Multi-Skill Parallel Anti-Collision Locks (`--pick-top 3` Max Cap · `1 Skill = 1 Independent M1 TL` · Honest `0..3` Zero-Issue Reporting)
- **Zero Owner Gate + Never Force Fixes (`0..3` Honest Upper Cap)**: In daily autonomous mode, `skill-retro` requires **NO Owner approval before fixing**, with an **upper cap of 3 disjoint skills per day (`--pick-top 3`) — NEVER a forced quota**:
  - If `0` skills cross the evidence threshold in Stage 2, **spawn `0` M1 TLs** and honestly report `status="HEALTHY_NO_ISSUES"` (`"healthy / 0 issues found (no changes needed)"`).
  - If `K` (`1..3`) disjoint skills are picked, the **single M2** spawns **`K` separate M1 TL agents in parallel** (`skill-retro-<sk>-goal-tl-r1`, each reading `~/Development/handoff/skill-retro-<sk>/spec.md` in `~/Development/worktrees/skill-retro-<sk>`).
  - If an M1 TL's deep reasoning reveals its assigned skill's signal is benign (e.g. healthy `R1->R2` convergence), **that M1 TL directly writes a `HEALTHY_NO_ISSUES` receipt and returns to M2 without spawning any Writer or Reviewer**!
- **Lock 1 · Strict Disjoint Owned-Paths**: Each M1 TL / Writer may ONLY edit `skills/<skill>/**` and its dedicated test file `scripts/test_<skill>_*.py` — **never** shared root files (`README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`) or monolithic shared test suites during per-skill retro.
- **Lock 2 · Active Lease Exclusion**: `skill_retro.py` scans active `~/Development/worktrees/*` and `~/Development/handoff/*/ledger.md` (`in_progress`) plus pending relay targets, automatically excluding leased skills from parallel selection.
- **Lock 3 · Disjoint Wave Scheduling**: **Wave 1** dispatches the `0..3` mutually disjoint per-skill M1 TLs in parallel (leveraging ~1.3s path-scoped `./scripts/verify`); any **Cross-Skill Relay** or shared-file update is scheduled into **Wave 2** after Wave 1 merges.

---

## 5. `1 M2 -> 1 Probe Worker -> K (0..3) Parallel M1 TLs -> Single M2 Rollup` Flow

- **`1 M2` Coordinator + `1 Cheap Probe Worker` (`--role explorer` / `--role mechanical`)**: A single M2 spawns 1 Cheap Probe Worker (`worker` tier `mai-code-2-flash` / `gpt-5.6-luna` + `jev_client.py` `jev_decide`, `0` steering tokens) to run **Stage 1 + Stage 2** (`probe --fast` -> `triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff`). It selects `K` (`0..3` honest cap) disjoint skills, writes `K` isolated per-skill M1 Goal specs (`~/Development/handoff/skill-retro-<sk>/spec.md`, `ledger.md`, and `briefs/*`) plus `m2_dispatch_plan`, and exits.
- **`K` Parallel `M1-TL` Slots (`1 Skill = 1 Independent M1 TL`, `K = 1..3`)**: Deep reasoning belongs to **M1**! Using `m2_dispatch_plan`, the single M2 dispatches **`K` separate M1 TL agents in parallel** (`herdr-dispatch.sh --new-workspace --role team_lead --name skill-retro-<sk>-goal-tl-r1 --kind claude`), each operating in its own isolated worktree `~/Development/worktrees/skill-retro-<sk>`:
  - **If M1 TL's deep investigation finds the skill is healthy (`HEALTHY_NO_ISSUES`)**: M1 TL writes `receipts/m1-tl-receipt.json` (`status="HEALTHY_NO_ISSUES"`, `writers_spawned=0`) and returns to M2 **without spawning any Writer or Reviewer**.
  - **If M1 TL confirms a genuine issue**: M1 TL dispatches its own `Writer -> Reviewer` (`skills/<sk>/**` + `scripts/test_<sk>_*.py`), runs `feedback_guard.py pre-review-check` + Positive & Counter-Case Replay + `./scripts/verify`, merges the PR, and writes its R10 receipt (`status="FIXED_AND_MERGED"` or `"CROSS_SKILL_RELAY"`).
- **Final Rollup to the Single M2 (`skill_retro.py rollup`)**: All `K` M1 TLs report back to the **same single M2**, which runs `python3 skills/skill-retro/scripts/skill_retro.py rollup --handoff-root ~/Development/handoff --json` (or `multica_skills_autopilot.py file-issue`) to aggregate the `K` M1 receipts (`FIXED_AND_MERGED`, `HEALTHY_NO_ISSUES`, `CROSS_SKILL_RELAY`, plus `observation_pool`) into the single daily `[Skill Retro YYYY-MM-DD]` summary issue.
