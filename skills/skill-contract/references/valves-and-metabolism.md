# Valves and metabolism

## Valve 1 — sink deterministic checks

Regex, AST, geometry, pixel, localization, and schema checks are executable artifacts. Put them in `shared-ci/scripts/context/design_contract.py` when cross-repository, or a skill's `scripts/*.py` when local. Markdown may explain the rule and show the command, but must not contain a private project identifier or act as the gate.

## Valve 2 — coordinate & eval drawers

A case card in `references/eval-cases.json` or `references/defect-cases/index.json` has an auto-assigned ID (`EV-001`.. or `DC-001`..), coordinates (`platforms`/`dimensions`/`containers` or `tags`/`routes`/`layers`), and at least one zero-token assertion:
1. **Pattern + positive/negative snippets** (`--forbidden-regex` + `bad_snippet` + `good_snippet`): `bad_snippet` must match; `good_snippet` must not match.
2. **CLI assertion** (`cmd` + `expect_exit` + `expect_stdout_contains` / `expect_json_subset`).
3. **Contract file assertion** (`file` + `must_contain` / `must_not_contain`).

`add-case --id auto` runs `evaluate_case` and the private-identifier guard before writing to disk.

## Valve 3 — budgets

L0: <=180 lines and 16KB; design L0: <=80 lines. L1 references: <=160 lines and 16KB. Fenced code blocks: <=25 lines. Move deep procedures, matrices, and examples to references and load them on demand.

## Valve 4 — One-In-One-Out

When adding an L1 mental model, replace the least useful row in the same table. Preserve a stable heading and record the replacement in the change description; do not create a second competing table.

## Audit & Eval scope

`audit --all --strict` checks structural budgets, orphan references, and drawer caps (`<= 15`). `eval --all --strict` deduplicates symlinked drawers by `path.resolve()` and executes all active regex/CLI/contract assertions across every skill.
