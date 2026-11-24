---
name: code-review
description: "Review a branch, PR, worktree, or handoff against repository standards and feature intent. Use for independent two-axis review with cited findings. Not for editing code."
---

# Code Review

Read-only two-axis review of one exact change surface. Pin the diff, gather repository-owned context, and report Spec and Standards findings separately.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| changed-surface discovery & context routing | `references/review-rubric.md` | `python3 skills/code-review/scripts/discover_review_context.py --repo <repo> [--base <ref> \| --range <base>..<head>]` |
| packet prompts & UI Visual Traceback | `references/reviewer-prompts.md` | `python3 skills/code-review/scripts/discover_review_context.py --flow-map` |
| findings validation & verdict mapping | `references/output-contract.md` | `python3 skills/code-review/scripts/discover_review_context.py --validate-findings <json\|->` |
| review smells | `references/review-smells.json` | `python3 skills/code-review/scripts/discover_review_context.py --register-smell` |

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Designer | `references/reviewer-prompts.md` | `discover_review_context.py --flow-map` |
| Writer | `references/output-contract.md` | `discover_review_context.py --repo <repo> --committed-only` |
| Reviewer | `references/review-rubric.md` | `discover_review_context.py --repo <repo> --base <ref>` |
| M1-TL | `references/eval-cases.md` and `references/eval-cases.json` | `skill_contract.py eval --skill code-review --strict` |

## Review contract

1. Run discovery (`--base <ref>`, `--range <base>..<head>`, or `--empty-ok`). Empty review surfaces exit with domain code `1` (or fall back to `HEAD~1` when `HEAD == base_ref` without `--strict-non-empty`).
2. Read AGENTS/CLAUDE, constitution, active Spec Kit artifacts, and only touched layer `CONTEXT.md` / `tech-context.md` leaves (both JSON and YAML frontmatter).
3. Build and verify a sealed packet before dispatch:

```bash
python3 scripts/review_manifest.py build --repo <target-repo> --output <review-manifest.json> \
  --writer-model <model> --writer-vendor <vendor> --writer-receipt <r10.md>
python3 scripts/review_manifest.py verify --manifest <review-manifest.json>
python3 scripts/review_manifest.py clean-room --manifest <review-manifest.json> --worktree <detached-review-wt>
```

The builder records immutable `head_sha`, `diff_sha256`, `spec_sha256`, and lane packet hashes while excluding Writer R10 prose. The detached worktree is an exact-commit provenance anchor, not a shared review input. A strict Reviewer dispatch must use the manifest and its lane packet as `--cwd`.

4. Read [reviewer-prompts.md](references/reviewer-prompts.md), then prepare four independent, immutable lane packets:
   - **Spec-Fidelity:** full diff, commits, issue context, and active Spec Kit artifacts;
   - **Adversarial/Security/Performance:** code diff, constitution/red-lines, layer context, and deterministic dependency/static results;
   - **Design-Traceback:** design authority, relevant UI diff, screenshots, and deterministic geometry/localization/design-contract results;
   - **Layer/Cross-layer:** per-layer diffs, layer context, dependency map, and Spec Kit task mapping.

Every packet must contain no Writer/TL transcript, R10 prose, comments, mutable worktree, or other lane packet. Every changed file belongs to exactly one layer packet or the explicit `unmapped` packet.

5. Dispatch every applicable lane consecutively in one batch, keep packet contexts isolated, and record each child id plus packet hash. Spec-Fidelity checks intent and acceptance; Adversarial/Security/Performance checks security, privacy, failure, concurrency, abuse, and material resource/performance risks; Design-Traceback checks rendered UI against design authority; Layer/Cross-layer checks ownership, dependency direction, task boundaries, and independent verification.
6. Join the batch before aggregation. Use the native agent join until every child is terminal and every result is received exactly once; declare missing packets as coverage gaps. If native subagents are unavailable, preserve separate packet inputs and axes and declare `execution_mode: sequential-fallback`.
7. Validate findings JSON and compute the verdict via `--validate-findings`; keep Spec and Standards counts separate. Review is content-only: do not edit, commit, test, lint, build, or merge. Every planned packet must have one result or one explicit coverage gap.

## Invariants

- Evidence comes from the pinned diff and repository-owned sources.
- Build, test, lint, hook, simulator, hardware, and CI execution or gate-state judgment are outside this skill.
- Constitution overrides spec; spec defines intent; tech-context defines implementation constraints.
- Layer is the Standards review scope unit. Cross-layer work is reviewed explicitly, never hidden inside a generic whole-repo pass.
- Missing context lowers declared coverage; it never becomes a silent pass.

## UI review gate & Post-Implementation Visual Traceback

For UI diffs, independently run `shared-ci/scripts/context/design_contract.py` with `--audit-code --platform <desktop|mobile|web>`, `--audit-l10n --locales en,zh-Hans`, and applicable `--check-edge-clipping <png-or-dir>` / `--audit-geometry <geometry.json>`. `xcode-cli` remains build/test routing only.

When reviewing UI changes with rendered screenshots, load `my-designer` alongside `code-review` and inspect the code diff plus screenshots. Tag visual findings `[IMPL_BUG]` when code violates an explicit contract, or `[SPEC_GAP]` when design authority omitted the needed rule; route the latter to Steering for a `design.md` amendment and new `spec_sha` before Writer changes.
