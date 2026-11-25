# Valves and metabolism

## Valve 1 — sink deterministic checks

Regex, AST, geometry, pixel, localization, and schema checks are executable artifacts. Put cross-repository gates in shared CI or local gates in `scripts/*.py`; Markdown explains but does not gate.

## Valve 2 — coordinate and eval drawers

`references/eval-cases.json` and defect drawers use auto IDs and zero-token assertions. Keep `active_cases <= 15`; use `trigger_evals` for routing and `negative_cmd` for failure boundaries. `score_case_assertions` enforces 1-Verify-1-Assertion and requires evidence plus `why_soft` for soft assertions.

## Valve 3 — budgets

Every `SKILL.md` description is non-empty and `<=320` characters. Every skill declares semantic `X.Y.Z` in `.claude-plugin/plugin.json`; missing or invalid manifests emit `missing-version`; fingerprints use `v<commit_count>@<subtree_sha>`. L0 is `<=180` lines/16KB, L1 is `<=160` lines/16KB, and fenced code is `<=25` lines.

`script-lines` rejects new or refactored `.py` files under `skills/*/scripts/**` above 1,000 lines. `LEGACY_SCRIPT_LINE_CAPS` ratchets existing monoliths; exceeding a cap emits `script-monolith-growth`. `skill_contract.py` is a facade and is not legacy-exempt.

## Valve 4 — sharing and metabolism

S1 reports absolute or missing `broken-symlink` targets. S2 includes sibling documents for `orphan-reference`. S3 reports divergent same-named non-wrapper scripts; ignore `__init__.py` and relative directory symlinks. Replace the least useful L1 row One-In-One-Out.

## Self-disclosing audit findings

Audit/eval JSON includes `guidance_drawer`, `reference`, and `hint`; stderr points to this drawer. Parser failures use at most three lines and end with `Reference:`. `hygiene-facts` is `soft_advisory_exit_0` unless `--strict`; repeated audit/version reads use a 60-second watermark cache.

## Repo-bound skills

Business repositories may colocate skills under `<repo>/skills/*` or `<repo>/.agents/skills/*`. `audit --repo <path> --strict` and `eval --repo <path> --strict` discover them; `repo_bound=True` waives private identifiers while budgets, drawers, and sharing remain enforced.

## Falcon Star backflow

`sync-jev-stars` reads SQLite read-only, applies source fallbacks, deduplicates markers, validates JSON snapshots, and preserves IDs/order. `--dry-run` preserves drawer bytes and mtimes. `description-eval --mock-jev-json` is deterministic and fails closed for missing, unknown, malformed, or out-of-range answers; live Jev failures use deterministic fallback.

Regression fixtures must supply fixed mock answers or offline injection, never ambient Jev settings. Live requests receive skill name, actual description, and query only.
