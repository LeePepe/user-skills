# Security Model — self-hosted runner + public repo

## The RCE surface (why this matters)

Running a **self-hosted runner** on a **public repo** with a workflow that triggers on
`pull_request` and **checks out the PR head** is a well-documented remote-code-execution surface:
the job runs *the PR author's version* of the repo on your machine, as your user. A malicious
fork PR can put anything in a script the job executes — and your runner can read `~/.claude`
(subscription creds), your SSH keys, your environment.

This is not theoretical. The very first run of this mechanism (on its own rollout PR) flagged a
real high-severity instance of it: the fork-skip check had been written **inside** the checked-out
`claude-review.sh`. A fork PR could simply edit that script to remove the check and run arbitrary
code. The mechanism caught its own bug.

## The invariant

> **The trust boundary lives in the workflow YAML, never in the checked-out script.**

On `pull_request`, GitHub evaluates the **workflow file from the base branch** (not the PR's
version). So a job-level `if:` in the YAML is a control a fork PR cannot alter. Anything inside a
file the job checks out is PR-controlled and must be treated as untrusted.

## The four layers of defense (all in the templates)

1. **Job-level `if` guard (primary).**
   `claude-review` job: `if: github.event.pull_request.head.repo.full_name == github.repository`.
   Same-repo branch PRs execute on the self-hosted runner; fork PRs skip this job entirely — their
   code never runs on your machine.

2. **Separate fork job for check reporting.**
   `claude-review-fork` runs on `ubuntu-latest` (GitHub-hosted, disposable), checks out nothing,
   and just posts a "needs manual review" notice. Its purpose is to make the same-named required
   check *report* for fork PRs — otherwise a required `claude-review` check would sit `pending`
   forever and wedge every fork PR. (Its `gh pr comment` may 403 under the read-only fork token;
   the template `|| true`s it so the job stays green.)

3. **Repo setting: approval for all external contributors.**
   `actions/permissions/fork-pr-contributor-approval = all_external_contributors`. Even the
   GitHub-hosted fork job won't run until a maintainer approves. This is the GitHub-native primary
   control; the `if` guard is defense-in-depth behind it.

4. **Prompt treats the diff as untrusted data.**
   The review prompt explicitly frames the diff/file-list as attacker-controlled and instructs the
   model to treat any embedded "approve this / verdict=pass / ignore the rules" text as a *blocker
   signal*, not an instruction. Prevents prompt-injection from coercing a pass.

## Residual risks (surface these to the user)

- **Same-repo collaborators are trusted.** A malicious *collaborator* branch PR still executes on
  the runner (that's the design — they already have push access). The `if` guard only stops forks.
- **Fork PR + required check.** With `claude-review` required and the fork job only *reporting*,
  the mechanism relies on the fork job's check landing green. If you tighten it, verify fork PRs
  don't wedge on `pending`. For a purely-internal repo this never arises.
- **Runner hygiene.** The runner runs as your user with your creds in reach. Prefer a dedicated
  low-privilege machine/user if the repo takes external contributions seriously.
