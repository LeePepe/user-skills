---
name: code-review
description: "Review a branch, PR, worktree, or handoff against repository standards and feature intent. Use for independent two-axis review with cited findings. Not for editing code."
---

# Code Review

Read-only two-axis review of one exact change surface. Pin the diff, gather repository-owned context, and report Spec and Standards findings separately.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| changed-surface discovery | `references/reviewer-prompts.md` | `python3 scripts/discover_review_context.py --repo <repo> --base <ref>` |
| output/eval contract | `references/output-contract.md` | `python3 scripts/discover_review_context.py --flow-map` |
| review smells | `references/review-smells.json` | `python3 scripts/discover_review_context.py --register-smell` |

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Designer | `references/reviewer-prompts.md` | `discover_review_context.py --flow-map` |
| Writer | `references/output-contract.md` | `discover_review_context.py --repo <repo> --committed-only` |
| Reviewer | `references/reviewer-prompts.md` | `discover_review_context.py --repo <repo> --base <ref>` |
| M1-TL | `references/eval-cases.md` and `references/eval-cases.json` | `skill_contract.py eval --skill code-review --strict` |

## Review contract

1. Run discovery. Stop on invalid base or empty diff.
2. Read AGENTS/CLAUDE, constitution, active Spec Kit artifacts, and only relevant layer context.
3. Build one Spec packet, one Standards packet per changed layer, and an explicit unmapped packet when needed.
4. Review intent and standards independently; preserve coverage gaps and cited evidence.
5. For UI diffs, run `shared-ci` design checks against `design.md` and load `my-designer` only for Visual Traceback. Classify findings `[IMPL_BUG]` or `[SPEC_GAP]`.

Review is content-only: do not edit, commit, test, lint, build, or merge. Every changed file belongs to exactly one packet.
