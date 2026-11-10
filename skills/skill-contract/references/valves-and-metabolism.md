# Valves and metabolism

## Valve 1 — sink deterministic checks

Regex, AST, geometry, pixel, localization, and schema checks are executable artifacts. Put them in `shared-ci/scripts/context/design_contract.py` when cross-repository, or a skill's `scripts/*.py` when local. Markdown may explain the rule and show the command, but must not contain a private project identifier or act as the gate.

## Valve 2 — coordinate drawers

A case card is a small JSON object with an auto-assigned stable id and three coordinates:
`platforms`, `dimensions`, and `containers`. Use `--top 3` when disclosing cards. Keep platform names equal to `desktop`, `mobile`, and `web` where the skill supports all three.

## Valve 3 — budgets

L0: <=180 lines and 16KB; design L0: <=80 lines. L1 references: <=160 lines and 16KB. Fenced code blocks: <=25 lines. Move deep procedures, matrices, and examples to references and load them on demand.

## Valve 4 — One-In-One-Out

When adding an L1 mental model, replace the least useful row in the same table. Preserve a stable heading and record the replacement in the change description; do not create a second competing table.

## Audit scope

`audit --all` checks local `skills/*`; it also checks `Skills/skills/meta` and `Skills/skills/repo` when those directories exist. `--skill` narrows to one package. `--json` emits machine-readable findings. `--strict` turns findings into a failing exit status.

## Description-eval (opt-in)

Not part of `audit`. Scores the `SKILL.md` frontmatter `description` against `evals/description-eval.json` with a lexical overlap proxy (not a model). Cases are `{id, prompt, should_trigger}`. `--apply` may add missing positive-cue tokens, writes only when the pass count does not fall, then runs the same checks as `audit --skill X --strict`. Any finding restores the previous file.
