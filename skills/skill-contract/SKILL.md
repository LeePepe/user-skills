---
name: skill-contract
description: "Audit and evolve progressive skills through SKILL.md, eval-cases.json, plugin.json, references/*.md, skill_contract.py, hygiene-facts, and Valve 1–4 budgets."
allowed-tools: Read, Write, Edit, Bash
---

# skill-contract

Use this meta-skill to keep skill packages small, navigable, executable, and self-disclosing.

## Four valves

1. **Sink rules:** deterministic regex, AST, pixel, localization, and geometry gates belong in executable scripts.
2. **Eval drawers:** use [eval-cases.json](references/eval-cases.json); active cases have zero-token assertions and stay at `<=15`. This is a **Script-Only Regression Drawer**: run it through `skill_contract.py eval`; do not view the full JSON into LLM context.
3. **Budgets & version:** descriptions are `<=320` characters; every skill declares semver in `plugin.json`; L0/L1/code budgets are enforced. Markdown prose line width is not penalized; byte and structural line budgets are the gates.
4. **Metabolism:** replace the least useful L1 row One-In-One-Out.

The shared `scripts/skill_runtime/` package provides filesystem guards, bounded CLI output/redaction, Copilot trace pairing, and Git version grounding. Sibling skills may link it with a relative directory symlink; never fork its implementation.

## CLI

```bash
python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict
python3 skills/skill-contract/scripts/skill_contract.py eval --all --strict
python3 skills/skill-contract/scripts/skill_contract.py hygiene-facts --json
```

`script-lines` caps new/refactored `.py`, `.sh`, and `.ts` files under `skills/*/scripts/**` at 1,000 lines; files over 800 lines receive `script-lines-soft` advisories, listed legacy monoliths are ratcheted by `script-monolith-growth`, and listed paths `<=800` lines emit `stale-legacy-cap`. L0 `SKILL.md` and L1 Markdown drawers receive soft byte advisories above 12KB; machine/config drawers over 16KB are marked Script-Only Data Drawers. `hygiene-facts` and `audit` report these as `soft_advisory_exit_0`; only hard findings make strict audit fail.

Use `add-case --id auto` for a self-tested drawer case. Bare, flag-first, and unscoped `audit`/`eval` calls default to `--all`. Use `--repo`, `--root`, `--dir`, `--target-dir`, `--path`, `--skill`, `--skills`, `--drawer`, `--summary`, `--no-cache`, and `--json`. Read `references/valves-and-metabolism.md` after a valve failure; run the Script-Only Regression Drawer with `skill_contract.py eval` and do not view the full JSON. Keep deep procedures in references and deterministic gates in scripts.
