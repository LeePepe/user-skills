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

`sync-jev-stars` reads an injected SQLite database read-only (default Falcon path), applies the canonical source fallback map, and deduplicates all three Star markers across joined reviews. It stores `jev_starred_id`, JSON-compatible `jev_state`, nonempty dict/list `jev_questions`, and nonempty dict `mock_jev_response`; question IDs must have corresponding answers when available. `eval` revalidates stored snapshots as well as file/command assertions.

`--cmd-template` parses CLI quoting first, then substitutes `{mock_json}` within one argument (bare or quoted placeholder, never shell execution) and runs the assertion before writing. Without a new template, refresh preserves existing `cmd`, `file`, content checks and expectations. Only cases without assertions get a default actual repo-relative `SKILL.md` content check (including `.agents/skills` and relative symlink packages). This checks snapshot integrity, not downstream decision correctness: supply a regression command for that stronger guarantee. Updates retain IDs, order, dates and metadata; at 15, only the oldest ordinary case is archived. A `jev_starred_id` or `jev-star` tag protects a golden case; golden saturation skips new Stars. `--dry-run` preserves drawer/DB bytes and mtimes.

`description-eval --mock-jev-json` accepts inline JSON or a file. Dictionaries use case ID, query, or **1-based** indices (`"1"`, `"2"`); lists use positional order. Missing entries, unknown keys, malformed answers and nonfinite/out-of-range scores fail closed. Boolean-only per-case mocks (also `should_trigger`/`trigger` boolean objects) remain compatible. Numeric `noul`/`score`/`confidence` answers use the same thresholds. Canonical `choice` answers require companion `noul`, including nested `{"answers":{"choice":{"choice":"yes"},"noul":{"noul":0.9}}}` responses. Yes triggers only for `noul >0.70`; `<0.30` rejects and inclusive `0.30–0.70` uses deterministic token matching, reported as fallback. No is never overridden by a high score.

Live canonical Jev receives skill name, actual description and query (never expected labels), with a closed-world Yes/No criteria question and companion applicability noul, `timeout_sec=1.5`. Missing/unavailable/invalid/grey live answers fall back deterministically; supplied mocks never invoke live Jev. Negative-only `add-case --negative-cmd` self-tests before writing: any genuine nonzero exit passes by default; exact `--expect-fail-exit` and stdout/stderr checks are opt-in. Zero exits, empty commands and launch errors always fail.
