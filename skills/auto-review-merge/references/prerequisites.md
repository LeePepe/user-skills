# Prerequisites

Before installing, confirm all of these. Surface any gap to the user before starting.

## On the target repo / account
- **GitHub rulesets available**: repo is **public**, OR on a paid plan (Pro/Team/Enterprise).
  Private + free → `gh api repos/<slug>/rulesets` returns **403**. This is the #1 blocker.
- **Admin access** via `gh` (`gh auth status`): needed to create rulesets, register runners,
  and change Actions settings.
- **Know the existing CI check name(s)** exactly as GitHub reports them
  (`gh api repos/<slug>/commits/<branch>/check-runs --jq '.check_runs[].name'`). The ruleset
  references checks by their exact context string.

## On the runner machine (the maintainer's computer)
- **`claude` CLI logged in with a subscription**: the review uses it headless
  (`claude -p --output-format json --json-schema`). Verify: `claude --version`, and that
  `claude -p "say hi" --output-format json` returns JSON without an auth prompt.
- **`gh`, `git`, `jq`, `curl`, `tar`** installed.
- **Runs as the user that owns `~/.claude`**: the runner service must run as that user, or it
  can't read the subscription credentials. `svc.sh` on macOS uses user-launchd (no sudo) — correct.
- **Machine is on when reviews are needed**: if `claude-review` is a required check, PRs wait
  while the runner is offline. Accept this, or choose comment-only mode.

## Conceptual
- **auto-merge only truly gates when a PR is required.** The ruleset's `pull_request` rule forces
  all changes through PRs so required checks actually run before merge. Direct pushes to a
  protected default branch are blocked by that rule.
- **Making a private repo public publishes all code + full history.** Scan for secrets first
  (see go-live-checklist step 1). Deleted-but-committed secrets remain in history.
