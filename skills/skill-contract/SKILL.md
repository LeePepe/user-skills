---
name: skill-contract
description: "Meta-skill for creating, refining, and auditing progressive-disclosure skill packages."
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

`audit` enforces S1/S2/S3 sharing checks: broken symlinks, cross-skill orphan references, and duplicate script forks. `description-eval` checks trigger and near-miss queries from `trigger_evals`; `negative_cmd` verifies expected command failures. `sync-jev-stars` imports synthetic or Falcon starred decisions idempotently and evicts the oldest non-star case when the active cap is full.

Read [valves-and-metabolism.md](references/valves-and-metabolism.md) for repo-bound governance and drawer details. Keep deep procedures in references and deterministic gates in scripts.
