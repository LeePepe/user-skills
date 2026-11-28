# Valves and metabolism

## Valve 1 — sink deterministic checks

Regex, AST, geometry, pixel, localization, and schema checks are executable artifacts. Put cross-repository gates in shared CI or local gates in `scripts/*.py`; Markdown explains but does not gate. Canonical repo-audit checks include OS-native appearance hygiene (`custom-theme-plumbing`: forbidding manual `.designTheme(` or `.environment(\.theme, Theme(... isDark: ...))` view-tree plumbing in audited Apple app `Apps/`, `Sources/`, and `Packages/` Swift sources while honoring `// design-contract-allow` and ignoring `Tests/`, pointing to `skills/my-designer/references/appearance-ledger.md`).

## Valve 2 — coordinate and eval drawers

`references/eval-cases.json` is a Script-Only Regression Drawer: run it with `skill_contract.py eval` and do not view the full JSON into LLM context. Defect drawers use auto IDs and zero-token assertions. Keep `active_cases <= 15`; use `trigger_evals` for routing and `negative_cmd` for failure boundaries. `score_case_assertions` enforces 1-Verify-1-Assertion and requires evidence plus `why_soft` for soft assertions.

## Valve 3 — budgets

Every `SKILL.md` starts with strictly valid YAML frontmatter (`--- ... ---`): double-quote any scalar value containing `: `, trailing `:`, ` #`, or leading YAML syntax indicators (`invalid-frontmatter` fails strict audit). Every `SKILL.md` description is non-empty and `<=320` characters. Every skill declares semantic `X.Y.Z` in `.claude-plugin/plugin.json`; missing or invalid manifests emit `missing-version`; fingerprints use `v<commit_count>@<subtree_sha>`. Markdown prose line width is not penalized. L0 is `<=180` lines/16KB, L1 is `<=160` lines/16KB, and fenced code is `<=25` lines; single-line triple-backtick fences (` ```lang cmd ``` `) in `SKILL.md` or `references/*.md` emit `malformed-code-fence` (`soft_advisory_exit_0`). L0 and L1 Markdown above 12KB receive soft byte advisories; machine/config drawers over 16KB receive `data-drawer-bytes-soft` with a Script-Only Data Drawer hint.

Executable `.py`, `.sh`, and `.ts` files under `skills/*/scripts/**` above 800 lines receive `script-lines-soft` when they are not legacy-ratcheted; Python `ast` hygiene also emits `ast-namespace-pollution` (`soft_advisory_exit_0`) for `globals().setdefault` / `__dict__.setdefault` circular injection or duplicate helper functions across sibling `scripts/*_core/*.py` modules. `script-lines` rejects new or refactored scripts above 1,000 lines. `LEGACY_SCRIPT_LINE_CAPS` ratchets existing monoliths, including `herdr-dispatch.sh` and `skill-slash-expander.ts`; exceeding a cap emits `script-monolith-growth`, and any listed path reduced to `<=800` lines emits `stale-legacy-cap` (`soft_advisory_exit_0`) so maintainers graduate it out of `LEGACY_SCRIPT_LINE_CAPS`. `skill_contract.py`, `skill_retro.py`, and `agent_retro.py` are not legacy-exempt: they must remain under the hard cap.

## Valve 4 — sharing and metabolism

S1 reports absolute or missing `broken-symlink` targets. S2 includes sibling documents for `orphan-reference`. S3 reports divergent same-named non-wrapper scripts; ignore `__init__.py` and relative directory symlinks. `boilerplate-table-decay` (`soft_advisory_exit_0`) flags `references/*.md` tables (`>=8` data rows) where `>=50%` of rows repeat an identical `>=20`-character cell. Replace the least useful L1 row One-In-One-Out.

### Role & Domain Purity Rule (`sink-rule` / 落实到skill)

When sinking a lesson (`sink-rule` / "落实到skill"), enforce strict role and domain purity:
- Route pure UX, visual, taxonomy, and specification rules (`design.md`/`spec.md`, atomic `Variant A/B`, composite screen reuse) strictly to Designer skills (`my-designer`, `design-studio`).
- Route compiler flags, Xcode build settings, project manifests, and concrete source-code implementation rules strictly to Worker skills (`xcode-cli`, `ios-*`, `team-lead`).
- Never pollute a design skill with code or build mechanics, and never store skill-boundary rules in ephemeral chat memory instead of the skill/doc files themselves.

## Self-disclosing audit findings

Audit/eval JSON includes `guidance_drawer`, `reference`, and `hint`; stderr points to this drawer. Parser failures use at most three lines and end with `Reference:`. `hygiene-facts` is `soft_advisory_exit_0` unless `--strict`; repeated audit/version reads use a 60-second watermark cache.

## Repo-bound skills

Business repositories may colocate skills under `<repo>/skills/*` or `<repo>/.agents/skills/*`. `audit --repo <path> --strict` and `eval --repo <path> --strict` discover them; `repo_bound=True` waives private identifiers while budgets, drawers, and sharing remain enforced.

## Falcon Star backflow

`sync-jev-stars` reads SQLite read-only, applies source fallbacks, deduplicates markers, validates JSON snapshots, and preserves IDs/order. `--dry-run` preserves drawer bytes and mtimes. `description-eval --mock-jev-json` is deterministic and fails closed for missing, unknown, malformed, or out-of-range answers; live Jev failures use deterministic fallback.

Regression fixtures must supply fixed mock answers or offline injection, never ambient Jev settings. Live requests receive skill name, actual description, and query only.
