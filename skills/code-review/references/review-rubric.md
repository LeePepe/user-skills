# Review Rubric — Surface Pinning & Context Routing

## 1. Pinning the exact review surface

Always pin one deterministic diff range before reading implementation files:

- **Branch / worktree review**: `discover_review_context.py --repo <repo> [--base <ref>]` resolves the `merge-base` against `origin/HEAD` / `main` and unions committed, staged, unstaged, and non-ignored untracked changes (`mode: "worktree"`).
- **Committed-only / range review**: pass `--committed-only` or `--range <base>..<head>` (`--diff-range <base>...<head>`) to restrict inspection to exact git commits (`mode: "committed-only"`).
- **Post-merge / single-commit fallback**: when `--base` and `--range` are omitted, `--strict-non-empty` is not set, and `HEAD == base_ref` produces an empty surface, discovery falls back to `HEAD~1` and emits `"fallback_base": "HEAD~1 (HEAD == base_ref)"`.
- **Empty surface exit semantics**:
  - Pass `--empty-ok` (`--allow-empty`) to inspect repository context with `"empty_surface": true` (`exit=0`).
  - Otherwise an empty diff exits with domain code `1` (never `exit=2`, which is reserved for invalid CLI flags or unresolvable git refs).

## 2. Dual-mode `CONTEXT.md` & `tech-context.md` resolution

Repositories declare ownership and architectural red lines in two frontmatter formats:

1. **JSON frontmatter (`CONTEXT.md` / `CONTEXT.*.md`)**:
   - `kind: "index"` routers declare `routes` and `exclusions` (`patterns` + `reason`).
   - `kind: "leaf"` modules declare `layer`, `parent`, `scope` globs, `test_paths`, `dependencies`, `dependents`, `red_lines`, and `gates`.
   - Patterns in `scope` and `test_paths` resolve relative to the `parent` router directory (or leaf directory when `parent` is absent) with `**` directory-crossing globs.
2. **YAML frontmatter (`tech-context.md` / `CONTEXT.md`)**:
   - Declares `layer`, `paths` / `owns`, `test_paths`, `depends_on`, `depended_by`, `red_lines`, `roles`, and `test` / `gate`.
   - Support and generated exclusions (`exclusions`, `support`, `support_excludes`, `generated_excludes`) prevent root governance or generated artifacts from being misclassified as unmapped layer gaps.

## 3. Two-axis grading discipline

Grade **Spec** and **Standards** independently and validate final findings via `--validate-findings <json|->` (see `references/output-contract.md`):

- **Spec axis**: missing, partial, or contradicted requirements in `spec.md`, `plan.md`, `tasks.md`, or `design.md`; never trust task checkboxes over diff evidence.
- **Standards axis**: layer `red_lines`, upward `depends_on` leaks, intra-layer `roles` stereotype inversions, unmapped production files, and matched `references/review-smells.json` rules.
- **Restraint**: every finding must cite both pinned diff evidence (`path:line`) and an authoritative source (`source`). Keep optional architecture ideas under `Decision candidates`, outside `findings` and the verdict.
