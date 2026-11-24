---
name: skill-contract
description: "Audit and evolve progressive skills through SKILL.md, eval-cases.json, plugin.json, references/*.md, skill_contract.py, hygiene-facts, and Valve 1–4 budgets."
allowed-tools: Read, Write, Edit, Bash
---

# skill-contract

Use this meta-skill to keep skill packages small, navigable, executable, and self-disclosing.

## Four valves

1. **Sink rules:** deterministic regex, AST, pixel, localization, and geometry gates belong in executable scripts.
2. **Eval drawers:** use `references/eval-cases.json`; active cases have zero-token assertions and stay at `<=15`.
3. **Budgets & version:** descriptions are `<=320` characters; every skill declares semver in `plugin.json`; L0/L1/code budgets are enforced.
4. **Metabolism:** replace the least useful L1 row One-In-One-Out.

## CLI

```bash
python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict
python3 skills/skill-contract/scripts/skill_contract.py eval --all --strict
python3 skills/skill-contract/scripts/skill_contract.py hygiene-facts --json
```

Use `add-case --id auto` for a self-tested drawer case. Bare, flag-first, and unscoped `audit`/`eval` calls default to `--all`. Use `--root`, `--dir`, `--target-dir`, `--skills`, `--drawer`, `--summary`, `--no-cache`, and `--json` for compatibility and bounded automation. `hygiene-facts` reports diff and JSON facts; `--strict` turns advisory findings into exit 1.

Read `references/eval-cases.json` for executable cases and `references/valves-and-metabolism.md` after a valve failure. Keep deep procedures in references and deterministic gates in `scripts/`.
