# Valves and metabolism

## Valve 1 — sink deterministic checks

Regex, AST, geometry, pixel, localization, and schema checks are executable artifacts. Put cross-repository gates in shared CI or local gates in `scripts/*.py`; Markdown explains but does not gate.

## Valve 2 — coordinate and eval drawers

`references/eval-cases.json` and defect drawers use auto IDs and zero-token assertions: regex bad/good snippets, CLI exit/output assertions, or contract-file contents. Keep `active_cases <= 15`; use `trigger_evals` for description routing and `negative_cmd` for failure boundaries.

## Valve 3 — budgets

Every `SKILL.md` frontmatter description is non-empty and `<=320` characters. L0 is `<=180` lines and 16KB (design L0 `<=80`); L1 is `<=160` lines and 16KB; fenced code is `<=25` lines.

## Valve 4 — sharing and metabolism

S1 checks package-local links and reports absolute or missing `broken-symlink` targets. S2 includes sibling skill documents when deciding `orphan-reference`, so shared canonical drawers are valid. S3 reports `duplicate-script-fork` when same-named non-wrapper Python scripts diverge; thin wrappers delegate through `_CANONICAL_PATH`.

When adding an L1 mental-model row, replace the least useful row in the same table (One-In-One-Out), preserving a stable heading and recording the replacement.

## Repo-bound skills

Business repositories may colocate skills under `<repo>/skills/*` or `<repo>/.agents/skills/*`. `audit --repo <path> --strict` and `eval --repo <path> --strict` discover them; `repo_bound=True` waives cross-project private identifiers while budgets, drawers, and sharing checks remain enforced.

## Falcon Star backflow

`sync-jev-stars` reads starred rows from an injected SQLite database (default Falcon path), maps source names, writes `jev_starred_id`, `jev_state`, `jev_questions`, `mock_jev_response`, and an executable assertion, and is safe with `--dry-run`. Updates are idempotent and archive the oldest non-star case when the 15-case cap is exceeded.
