---
name: code-review
description: Review a branch, PR, worktree, or implementation handoff against both repository standards and feature intent. Use for implementer self-review or independent review when the target repo may contain Spec Kit artifacts and repo-kit files; preserves separate Standards and Spec axes, fans Standards review out by changed layer, checks cross-layer dependency direction, and reports cited findings without editing code.
---

# Code Review

Run a read-only, two-axis review of one exact change surface. This skill distills Matt Pocock's `code-review` structure and consumes the repository facts produced by `repo-kit`.

> **Dual-Entry Review Smell Drawer & Anti-Explosion Contract ([review-smells.json](references/review-smells.json) & [eval-cases.json](references/eval-cases.json)):**
> `discover_review_context.py` slices the indexed review smell drawer (`RS-001..004`) by changed file extensions and active layers, returning at most `Top-3` `matched_review_smells`. Run `python3 scripts/discover_review_context.py --flow-map` to inspect drawer inventory and reference line budgets (`<= 160` lines), or `--register-smell` / `skill_contract.py add-case --skill code-review --id auto` to promote recurring review findings without bloating `SKILL.md`.

## 1. Pin the review surface

Run the deterministic discovery script from this skill directory:

```bash
python3 scripts/discover_review_context.py --repo <target-repo> [--base <ref>] [--spec <feature-dir-or-spec.md>] [--committed-only]
```

- Omit `--base` to derive the default branch and its merge-base with `HEAD`.
- Pass `--spec` when a worktree contains more than one changed Spec Kit feature.
- Omit `--committed-only` for implementer self-review so staged and unstaged changes are included.
- Stop on an invalid base or empty diff. Never review an inferred surface after discovery fails.

## 2. Read context progressively

Use discovery output as a routing map, not as a substitute for source documents.

1. Read the applicable `AGENTS.md`/`CLAUDE.md` index.
2. Read the constitution/red-line source.
3. Read the active Spec Kit `spec.md`, `plan.md`, and `tasks.md`. Follow linked contracts or research only when needed for a changed interface.
4. Read top-level technical context for cross-layer changes.
5. For each changed layer, read only that layer's `tech-context.md`/`CONTEXT.md` frontmatter and relevant body.

If Spec Kit or layer metadata is absent, declare the missing coverage and continue in degraded mode. Never invent requirements, layers, dependencies, roles, red lines, or test commands.

## 3. Build isolated review packets

Read [reviewer-prompts.md](references/reviewer-prompts.md), then prepare:

- one **Spec packet** containing the full diff, commits, issue context, and active Spec Kit artifacts;
- one **Standards packet per changed layer** containing only that layer's diff plus constitution, repository standards, and layer context;
- one **Cross-layer packet** when two or more layers change or any path is unmapped.

Every changed file must appear in exactly one layer packet or the explicit `unmapped` packet.

## 4. Review in parallel

When the runtime exposes native subagents, dispatch the Spec reviewer, every
layer Standards reviewer, and the optional Cross-layer reviewer consecutively
in one batch. Keep their context isolated and record every returned child id.

- Standards reviewers check cited repository rules, layer `red_lines`, `depends_on`, `roles`, ownership, explicitly required test artifacts, and code-quality smells.
- The Cross-layer reviewer checks dependency direction, changed-layer impact, Spec Kit task boundaries, and whether work that crosses layers was explicitly planned and independently verifiable.
- The Spec reviewer checks intent, acceptance, scope, plan constraints, contracts, and task completion across the complete diff.

Join the batch before aggregation:

1. Call `collaboration.wait_agent` with a multi-minute timeout.
2. Repeat until every recorded child is terminal and every final result has
   been received exactly once. A timeout is a wait result, not child failure.
3. After two consecutive timeouts, inspect child status once. Re-wait for live
   children; declare the missing packet as a coverage gap only when the child
   is terminal without a retrievable result.
4. On task resume, reconcile the recorded child ids first and rerun only a
   packet whose terminal result is genuinely unavailable.

`functions.wait` resumes a yielded `functions.exec` cell; it does not join an
agent. Collaboration tools are direct tools and are intentionally absent from
`functions.exec` and `ALL_TOOLS`. Use the join above rather than shell sleeps,
no-op commands, or tool-list polling. Child completion may wait in the mailbox
until the parent reaches a tool/message boundary; `collaboration.wait_agent`
creates that boundary.

If native subagents are unavailable, review the already-built packets as
isolated sequential passes and declare `execution_mode: sequential-fallback`.
Preserve separate packet inputs and axes; never claim parallel execution.

Review only. Do not edit, commit, push, create issues, or merge. This step is
complete only when every planned packet has one captured result or one explicit
coverage gap.

## 5. Aggregate without masking axes

Start aggregation only after the join completion criterion above is satisfied.
Read [output-contract.md](references/output-contract.md). Validate every returned finding against the diff and cited source, then:

1. Deduplicate within the same axis and layer.
2. Keep Standards and Spec findings separate; do not collapse them into one score.
3. Preserve the highest severity supported by evidence, not by reviewer wording.
4. Report coverage gaps and unmapped files before findings.
5. End with counts and the worst finding in each axis. A clean result requires both axes to pass and no unexplained coverage gap.

Use [eval-cases.md](references/eval-cases.md) when changing this skill or checking whether a runtime follows its routing decisions.

## Invariants

- Evidence comes from the pinned diff and repository-owned sources.
- Review is content-only. Discovery and source inspection are allowed; build, test, lint, hook, simulator, hardware, and CI execution or gate-state judgment are outside this skill.
- Test code is reviewed only when a cited spec, task, constitution, or repository rule requires that artifact or behavior. Whether FS ran tests and whether gates passed are not review findings.
- Constitution overrides spec; spec defines intent; tech-context defines implementation constraints.
- Layer is the Standards review scope unit. Cross-layer work is reviewed explicitly, never hidden inside a generic whole-repo pass.
- Missing context lowers declared coverage; it never becomes a silent pass.

## UI review gate & Post-Implementation Visual Traceback
For UI diffs, independently run `shared-ci/scripts/context/design_contract.py` with `--audit-code --platform <desktop|mobile|web>`, `--audit-l10n --locales en,zh-Hans`, and applicable `--check-edge-clipping <png-or-dir>` / `--audit-geometry <geometry.json>`. `xcode-cli` remains build/test routing only.

When reviewing UI changes with rendered screenshots (from the Writer's R10 receipt), load `my-designer` alongside `code-review` to execute **Post-Implementation Visual Traceback (`实现后截图回溯`)** across both the code diff and the rendered screenshots. Tag every visual or interaction finding with its attribution class:
- **`[IMPL_BUG]` (Implementation Deviation)**: The implementation violates an explicit clause in `design.md` or `design_contract.py` → route directly back to the `Writer` to fix against the existing `design.md`.
- **`[SPEC_GAP]` (Phase 0 Spec Omission / Unforeseen Reality)**: The `Writer` faithfully followed `design.md`, but the rendered screenshots expose crowding, CJK wrapping, column drift, or hierarchy conflict missed in Phase 0 → **never blame the `Writer` or ask the `Writer` to guess undeclared rules**; route back to `Steering` to **amend `design.md` first (and re-stamp `spec_sha`)** with concrete layout parameters before dispatching the `Writer`.
