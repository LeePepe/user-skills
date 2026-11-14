---
name: skill-contract
description: "Meta-skill for creating, refining, auditing, and evaluating progressive-disclosure skill contracts and package drawers."
allowed-tools: Read, Write, Edit, Bash
---

# skill-contract

Use this meta-skill to keep skills small, navigable, and executable. Four valves apply:

1. **Sink rules**: deterministic regex, AST, pixel, localization, and geometry gates belong in executable scripts.
2. **Eval drawers**: use `references/eval-cases.json`; active cases have zero-token assertions and stay at `<=15`.
3. **Budgets**: descriptions are `<=320` characters; L0 is `<=180` lines/16KB, L1 `<=160` lines/16KB, and code blocks `<=25` lines.
4. **Metabolism**: replace the least useful L1 row One-In-One-Out.

## Commands

Use `add-case --id auto` to register a self-tested drawer case.

```bash
python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict
python3 skills/skill-contract/scripts/skill_contract.py eval --all --strict
python3 skills/skill-contract/scripts/skill_contract.py description-eval --skill <skill> --strict
python3 skills/skill-contract/scripts/skill_contract.py sync-jev-stars --skill <skill> [--db-path <sqlite>] [--dry-run]
```

`audit` enforces S1/S2/S3 sharing checks: broken symlinks, explicit cross-skill orphan references, and duplicate script forks. `description-eval` reports precision/recall and per-case decision sources; `--mock-jev-json` accepts inline JSON or a file keyed by case ID, query, or **1-based** dictionary index (lists use positional order). Boolean-only mocks remain supported; canonical `choice` answers require companion `noul`. Applicability `>0.70` permits Yes, `<0.30` rejects it, and inclusive `0.30–0.70` uses a reported deterministic fallback; No is never overridden. Invalid or incomplete mocks fail rather than contacting Jev. Live Jev receives skill/description/query and both questions with a 1.5s timeout, falling back when unavailable/invalid/grey.

`add-case --negative-cmd` is a standalone pre-write self-test; it requires a genuine nonzero exit, optionally exact `--expect-fail-exit` and output checks. Launch errors and empty commands fail. `sync-jev-stars` preserves snapshot data and existing assertions on refresh, validates file or safe `--cmd-template` assertions, and plans the same changes in `--dry-run`. It archives only the oldest ordinary case at the 15-case cap; IDs or `jev-star` tags protect golden cases from eviction.

Read [valves-and-metabolism.md](references/valves-and-metabolism.md) for repo-bound governance and drawer details. Keep deep procedures in references and deterministic gates in scripts.
