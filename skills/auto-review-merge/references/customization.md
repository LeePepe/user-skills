# Customization — every knob

## Installer params (scripts/install.sh)

| Param | Required | Meaning | Default |
|---|---|---|---|
| `--repo-dir` | yes | Target git repo root | — |
| `--runner-label` | yes | Unique self-hosted runner label / name (e.g. `acme-mac`). Also used in the sticky-comment marker and runner dir. | — |
| `--ci-check` | yes* | Exact name of the existing CI check to keep required (as GitHub reports it, e.g. `build + test (macOS 26)`). Pass `""` if the repo has no CI yet. | — |
| `--project-name` | no | Name used in the review prompt | repo dir name |
| `--review-dims-file` | no | Text file whose contents become the numbered blocker-dimensions in the prompt | `references/review-dimensions.default.md` |
| `--runner-dir` | no | Where the runner installs | `$HOME/actions-runner-<label>` |

\* You can pass `""` and hand-edit `main-protection.json` afterward.

## Decisions to make WITH the user (AskUserQuestion)

1. **Does the LLM review block merge, or only comment?**
   - *Block* (recommended): `claude-review` is a required check; blockers → red → no merge.
   - *Comment-only*: don't add `claude-review` to `required_status_checks`. auto-merge then gates
     on CI alone; the review is advisory. Runner offline never wedges PRs.

2. **Runner-offline behavior** (only if review blocks):
   - Required check + runner down = PRs wait (nothing merges unreviewed). This is the safe default.
   - If unacceptable, make it comment-only, or add a scheduled fallback that reports the check.

3. **Review dimensions.** Tailor `--review-dims-file` to the repo. Examples:
   - Layered SPM/monorepo: "no reverse layer imports; changes must include tests".
   - Web app: "no secrets in client bundle; no `dangerouslySetInnerHTML` without sanitize".
   - Library: "no breaking public API without a migration note".

4. **Runner host.** Personal Mac (simplest) vs a dedicated low-priv machine (safer for public repos
   taking external PRs). See security-model.md residual risks.

## Model / effort

The review uses whatever the runner's `claude` is logged into. To pin a model or cap turns, add
flags in `scripts/ci/claude-review.sh` where it calls `claude -p` (e.g. `--model claude-sonnet-5`,
`--max-turns 8`). Cheaper model = cheaper reviews; the default inherits the CLI's session model.

## Not on macOS?

`setup-runner.sh` handles Darwin + Linux platform detection. On Linux, `svc.sh install` uses
systemd and typically needs `sudo`; run the runner as a dedicated user that has `claude` logged in.

## Required checks without approving reviews

Keep `required_approving_review_count` at `0` and `require_code_owner_review` at `false`.
Every non-Draft PR gets auto-merge enabled on opening and merges when required checks pass;
human or CODEOWNERS approval is not a merge prerequisite. Repository settings and ruleset
changes still require separately authorized administration.

## Have an ANTHROPIC_API_KEY and want cloud-only?

You don't need the self-hosted runner. Replace `claude-review.yml`'s job with a `ubuntu-latest`
job using `anthropics/claude-code-action@v1` and the key as a secret, reusing this skill's prompt
and `--json-schema` verdict logic from `claude-review.sh` to keep the deterministic gate.
