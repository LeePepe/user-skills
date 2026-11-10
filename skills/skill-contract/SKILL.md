---
name: skill-contract
description: "Meta-skill for creating, refining, and auditing progressive-disclosure skill packages. Use when running skill-contract audit, sink-rule, or opt-in description-eval."
allowed-tools: Read, Write, Edit, Bash
---

# skill-contract

Use this meta-skill to keep skills small, navigable, and executable. It enforces four valves:

1. **Sink rules**: deterministic regex, AST, pixel, localization, and geometry gates belong in `shared-ci` or `scripts/*.py`, never as private markdown gates.
2. **Coordinate drawers**: case cards use `--id auto` and JSON `platforms`, `dimensions`, and `containers`; disclose no more than Top 3.
3. **Budgets**: L0 is <=180 lines/16KB (design L0 <=80), L1 <=160 lines/16KB, and code blocks <=25 lines.
4. **Metabolism**: L1 mental-model tables use One-In-One-Out replacement instead of unbounded append-only growth.

## Commands

```bash
python3 skills/skill-contract/scripts/skill_contract.py init --skill example --category design --design
python3 skills/skill-contract/scripts/skill_contract.py sink-rule --skill example --kind regex-gate --id DC-001
python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict
python3 skills/skill-contract/scripts/skill_contract.py description-eval --skill example
python3 skills/skill-contract/scripts/skill_contract.py description-eval --skill example --apply
```

`refine_skill.py` is a compatibility symlink. Read [valves-and-metabolism.md](references/valves-and-metabolism.md) for the contract details. `audit` never writes. `description-eval` is opt-in and dry-run unless `--apply`; apply writes only if the trigger score does not regress and `audit --strict` on that package is clean, otherwise it rolls back.
