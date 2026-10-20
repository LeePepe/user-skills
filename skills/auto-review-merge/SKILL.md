---
name: auto-review-merge
description: Use when a user wants automated PR code review plus hands-off auto-merge on a GitHub repo — "review my PRs automatically", "auto-merge when CI passes", "gate merges on a Claude review", "set up PR automation". Installs a self-hosted-runner mechanism that runs a local subscription `claude` CLI as a deterministic merge gate (no ANTHROPIC_API_KEY), wires GitHub auto-merge, and codifies a branch ruleset. Framework/language-agnostic; the review dimensions are configurable per repo.
allowed-tools: Read, Write, Edit, Bash
---

# Auto Review + Auto Merge

Install a **two-part PR automation** on a GitHub repo:

1. **Auto review** — every PR gets a code review from a **local subscription `claude` CLI** running on a **self-hosted runner**. The verdict is deterministic: find a critical/high issue → the job exits non-zero → a required status check goes red → merge is blocked. **No `ANTHROPIC_API_KEY`** — it uses the maintainer's already-logged-in Claude subscription.
2. **Auto merge** — every non-draft PR gets squash auto-merge enabled on creation. It merges the moment the machine gates (CI + Claude review) are all green.

The design principle: **decouple judgment from merge.** LLM review is the *judgment* gate (catches what machines can't — naming, intent, layer violations, injected secrets); CI + the review's exit code are *machine* gates that deterministically decide merge. A branch **ruleset** makes both checks required so nothing lands ungated.

**Announce at start:** "I'm using the auto-review-merge skill."

## When to use

- User wants PRs reviewed automatically and/or merged automatically once gates pass.
- User wants a merge gate backed by an LLM review but **has no Anthropic API key** (has a Claude subscription).
- Any GitHub repo, any language — review dimensions are configured per repo, not hardcoded.

## When NOT to use

- User wants review only, no auto-merge → still usable, just skip the auto-merge workflow + don't require the check.
- User has an `ANTHROPIC_API_KEY` and wants cloud-only (no self-hosted runner) → simpler to use `anthropics/claude-code-action@v1` with the key; this skill's self-hosted path is specifically for the no-API-key / subscription case. Point them there instead, or adapt `assets/ci/claude-review.sh`'s prompt+schema into a hosted job.
- Repo is private on a **free** plan → GitHub rulesets return 403. The repo must be **public** or on a paid plan. Surface this before starting (see references/prerequisites.md).

## What gets installed (per target repo)

```
.github/workflows/auto-merge.yml     # enable squash auto-merge on every non-draft PR
.github/workflows/claude-review.yml  # self-hosted review job (gate) + fork no-op job
scripts/ci/claude-review.sh          # the deterministic review: claude -p --json-schema
scripts/ci/setup-runner.sh           # one-time self-hosted runner registration
scripts/rulesets/main-protection.json# ruleset source of truth (required checks etc.)
scripts/rulesets/apply               # idempotent create-or-update of the ruleset
docs/ci-gates.md                     # human-facing explanation of the three gate layers
```

All are **templates** in `assets/`. The installer `scripts/install.sh` copies them into the
target repo, substituting per-repo values (runner label, review dimensions, existing CI check
name). Read `references/customization.md` for every knob.

## How to run it

1. **Gather the repo's facts first** (do NOT assume): default branch, whether it's public/paid,
   the exact name(s) of existing required CI checks, and what review dimensions matter for this
   codebase (e.g. "no layer violations", "tests must accompany code", framework-specific rules).
   `AskUserQuestion` for the review-strength decision (does the LLM review *block* merge, or only
   comment?) and the runner-offline behavior. See references/customization.md.

2. **Run the installer** from the skill dir, pointing at the target repo:
   ```bash
   scripts/install.sh --repo-dir <path> --runner-label <slug>-mac \
       --ci-check "<existing CI check name>" [--review-blocks true|false]
   ```
   It copies+substitutes templates, `chmod +x`es scripts, and prints the exact go-live steps.
   It writes files only; it never pushes, merges, or mutates GitHub. You drive git + `gh`.

3. **Follow the GO-LIVE ORDER below.** This ordering is the whole ballgame — get it wrong and
   you either lock every PR in `pending` forever or ship an unenforced gate.

## GO-LIVE ORDER (do not reorder)

The trap: if `claude-review` becomes a **required** check before a runner exists to report it,
**every PR is blocked forever** (only admin-merge clears it). So required-gate is LAST.

1. **Make repo public** (if private+free) — rulesets need public or a paid plan. Scan for
   secrets in tracked files first; making a repo public publishes all code + full history.
2. **Enable repo auto-merge**: `gh api -X PATCH repos/<slug> -f allow_auto_merge=true -f delete_branch_on_merge=true`.
3. **Merge the workflows to the default branch** via a normal PR (only the *existing* CI check
   is required at this point). Workflows on `pull_request`/`pull_request_target` only take effect
   once they're on the base branch.
4. **Register + start the self-hosted runner**: `scripts/ci/setup-runner.sh` then
   `svc.sh install && svc.sh start`. Confirm `gh api repos/<slug>/actions/runners` shows `online`.
5. **Prove the review reports once green** — open a trivial test PR, watch `claude-review` run on
   the runner and post its verdict comment and go green.
6. **Only now** add `claude-review` to `required_status_checks` in `main-protection.json` and run
   `scripts/rulesets/apply`. This flips it into a hard gate.
7. **Harden fork execution** (public repos): set
   `gh api -X PUT repos/<slug>/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors`.

Reference `references/go-live-checklist.md` for the copy-paste command sequence with verification.

## The security invariant (learned the hard way)

self-hosted runner + `pull_request` + checkout-of-PR-head = a well-known **RCE surface**: the step
runs the PR's version of the code, and a fork PR can be anything. This mechanism was itself caught
by its own first review for getting this wrong. The rule, encoded in the templates:

> **The trust boundary lives in the workflow YAML, never in the checked-out script.**
> On `pull_request`, GitHub evaluates the workflow file from the **base branch**, so a fork can't
> alter it. The `if: head.repo.full_name == github.repository` on the executing job is the real
> guard. A fork check written *inside* `claude-review.sh` is worthless — that file is PR-controlled.

Fork PRs are routed to a separate `ubuntu-latest` job that runs no PR code and just reports the
same-named check. Plus: repo setting requires approval for all external contributors, and the
review prompt declares the diff **untrusted data** so injected "verdict=pass" text can't coax a pass.
Full write-up: `references/security-model.md`.

## Key design facts

- **Deterministic gate**: `claude -p --output-format json --json-schema <schema>` returns a
  validated `{verdict, blockers[], notes[]}`. `blockers` non-empty → `exit 1`. Parsed from the
  CLI's top-level `.structured_output` (fallback `.result | fromjson`).
- **Fail-closed**: any CLI error / unparseable output → `exit 1` (block), never silent-pass.
- **Sticky comment**: one comment per PR, PATCHed on each push (no comment spam).
- **Runner offline = no merge**: the review is a required check; if the runner is down the check
  never reports and the PR waits. This is intended (nothing merges unreviewed) — confirm the user
  accepts it, or make the check non-required (review-only) instead.
