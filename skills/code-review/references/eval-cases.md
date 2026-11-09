# Eval Cases — Code Review

Use blind, discriminating fixtures. Grade the produced discovery JSON, review packets, and final report rather than accepting claims that the process was followed.

| Family | Decision under test | Core case |
|---|---|---|
| B | fixed point and review surface | B1 derived merge-base includes worktree changes |
| S | Spec Kit intent | S1 missing acceptance is found and cited |
| L | layer-local standards | L1 red-line/depends-on violation stays in the owning layer packet |
| X | cross-layer reasoning | X1 unplanned two-layer change gets a cross-layer finding |
| D | degraded coverage | D1 missing Spec Kit/layer context is declared, not fabricated |
| C | restraint | C1 clean layered change produces no unsupported finding |
| J | subagent join | J1 staggered children are joined without dummy polling |
| R | resume durability | R1 completed child results survive parent resume |
| W | wait-tool selection | W1 agent join never uses the exec-cell wait tool |

## B1 — Derived surface

Fixture: branch `001-feature` is ahead of `main`; committed, unstaged, and untracked files changed. No base argument.

PASS when discovery resolves the `main` merge-base and includes all three surfaces in worktree mode. FAIL when it reviews only `HEAD`, guesses `HEAD~1`, or omits unstaged/untracked files.

## S1 — Missing acceptance

Fixture: `spec.md` requires validation of an empty identifier; implementation accepts empty input; `tasks.md` marks validation complete.

PASS when the Spec axis emits a cited P1/P0 finding. FAIL when only style/tests are discussed or the task checkbox is trusted over the diff.

## L1 — Layer violation

Fixture: `core/tech-context.md` declares `depends_on: []` and red line `core never imports ui`; the diff adds a `ui` import in `core`.

PASS when the `core` Standards packet emits a cited layer finding. FAIL when the violation is placed only in a generic whole-repo report or assigned to `ui`.

## X1 — Cross-layer task boundary

Fixture: `core` and `ui` both change, but Spec Kit has one task scoped only to `ui`; the core interface change is not planned.

PASS when the Cross-layer reviewer reports the undeclared core work and layer/task mismatch. FAIL when two locally clean layer reports produce an overall pass.

## D1 — Explicit degradation

Fixture: a small repository has no Spec Kit, no constitution, and no layer contexts.

PASS when the report lists all three coverage gaps and confines review to observable diff correctness. FAIL when it invents a spec/layer or silently claims full coverage.

## C1 — Clean restraint

Fixture: one layer changes within `owns`, obeys red lines/dependencies, implements every acceptance item, and adds the declared layer test.

PASS when both axes have no P0/P1 findings. FAIL on generic preferences, uncited smells, or demands for unrelated layers/tests.

## J1 — Staggered parallel join

Fixture: Spec and Standards reviewers finish at different times while the
parent is active. Both return distinct cited findings.

PASS when all reviewers are spawned in one consecutive batch, the parent calls
`collaboration.wait_agent` until both are terminal, and each result is aggregated
exactly once. FAIL when dispatch is serialized, aggregation starts after the
first result, or the parent uses shell sleeps/no-op commands to create polling
boundaries.

## R1 — Resume with completed child

Fixture: one child finishes after the parent turn is interrupted; the parent
resumes with the child id and mailbox result available.

PASS when the parent reconciles the recorded id, consumes the existing result,
and reruns no completed packet. FAIL when it loses the packet, duplicates the
review, or silently passes with reduced coverage.

## W1 — Correct wait primitive

Fixture: both `functions.wait` and `collaboration.wait_agent` are visible, and
the review batch is still running.

PASS when the parent calls `collaboration.wait_agent` directly with a
multi-minute timeout. FAIL when it calls `functions.wait`, searches
`functions.exec`/`ALL_TOOLS` for collaboration tools, polls with `list_agents`,
or emits dummy exec commands such as `true`, `sleep`, or `echo waiting`.

## Automated discovery checks

Run:

```bash
python3 scripts/test_discover_review_context.py
```

The test covers B1, layer mapping, active Spec Kit selection, and D1. Use blind subagents for S1/L1/X1/C1 because those grade review judgment rather than deterministic discovery.
