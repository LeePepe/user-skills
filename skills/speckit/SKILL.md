---
name: speckit
description: Run a repository's native Spec Kit planning workflow. Use when Planner Lead must create, clarify, plan, task, analyze, or refine `spec.md`, `plan.md`, and `tasks.md` in a repository with `.specify`; do not use for implementation.
---

# Spec Kit

Use the target repository's installed Spec Kit as the planning engine. The repository-owned constitution, templates, scripts, extensions, and active feature artifacts are authoritative; this skill selects stages and preserves their contracts.

## Workflow

1. Locate the task-owned repository and active feature directory. Read `AGENTS.md`/`CLAUDE.md`, the constitution, existing Spec Kit artifacts, and repository planning instructions before changing artifacts.
2. Inspect `.specify` and use its installed commands, templates, and scripts. Do not replace repository customizations with global Spec Kit defaults.
3. Run stages individually so their prerequisites, hooks, gates, and outputs remain visible:
   - **Specify** when intent, user outcomes, acceptance, or non-goals are missing. Keep `spec.md` technology-agnostic and complete its repository quality checklist before Plan.
   - **Clarify** when a material product or contract ambiguity prevents a deterministic plan. Escalate decisions that repository evidence cannot resolve.
   - **Plan** when architecture, interfaces, data flow, layers, dependencies, or verification strategy are incomplete. Resolve every `NEEDS CLARIFICATION` through internal research subagents; produce `research.md`, `data-model.md`, applicable `contracts/`, and `quickstart.md`; run constitution checks before and after design; and update only the Spec Kit marker block in the repository's agent-context file.
   - **Tasks** only after the required Plan artifacts exist. Apply `to-tickets` so each user-story phase remains a vertical slice while every `Txxx` implementation row remains layer-scoped and valid Spec Kit syntax.
   - **Analyze** only after a complete `tasks.md`. Keep Analyze read-only. Route each finding back to Specify, Clarify, Plan, or Tasks, then rerun Analyze after the owning stage repairs it.
4. Treat stock Specify and Plan review gates as Planner validation checkpoints. Pause for Team Lead only when the repository workflow explicitly requires an external approval or a product/authority decision.
5. Run all enabled mandatory extension hooks and repository-declared freshness, consistency, frontmatter, dependency, and task checks. Treat every failure as evidence to repair or report.
6. Keep candidate planning work in repository artifacts. After Analyze is clean enough for review, pin the exact revision and complete artifact set for AI Reviewer. Publish or reconcile tracker children only after that exact revision receives a passing verdict.

## Completion criteria

- `spec.md`, `plan.md`, `tasks.md`, and every required Plan artifact agree on scope, terminology, acceptance, contracts, validation, and dependencies.
- Every acceptance criterion maps to at least one vertical slice and verification surface.
- Every implementation task maps to one declared layer or is explicitly classified as planning/integration-only work.
- Cross-layer contracts and dependency direction are explicit.
- Analyze has run read-only against complete Tasks, its blocking findings have been repaired through owning stages, and repository planning checks pass with fresh evidence.

If `.specify` is absent, report the missing planning system to Team Lead. Do not initialize or replace repository planning infrastructure without explicit authority.
