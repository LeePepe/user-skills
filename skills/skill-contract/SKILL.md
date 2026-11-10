---
name: skill-contract
description: "Meta-skill for creating, refining, and auditing progressive-disclosure skill packages."
allowed-tools: Read, Write, Edit, Bash
---

# skill-contract

Use this meta-skill to keep skills small, navigable, and executable. It enforces four valves:

1. **Sink rules**: deterministic regex, AST, pixel, localization, and geometry gates belong in `shared-ci` or `scripts/*.py`, never as private markdown gates.
2. **Coordinate & eval drawers**: case cards in [eval-cases.json](references/eval-cases.json) or `defect-cases/index.json` use `add-case --id auto` with pre-write self-test (`bad_snippet`/`good_snippet`, `cmd`, or `file`) and disclose no more than Top 3 (`<=15` active).
3. **Budgets**: L0 is <=180 lines/16KB (design L0 <=80), L1 <=160 lines/16KB, and code blocks <=25 lines.
4. **Metabolism**: L1 mental-model tables use One-In-One-Out replacement instead of unbounded append-only growth.

## Commands

```bash
python3 skills/skill-contract/scripts/skill_contract.py init --skill example --category design --design
python3 skills/skill-contract/scripts/skill_contract.py add-case --skill example --id auto --title "..." --tags cli --cmd "..."
python3 skills/skill-contract/scripts/skill_contract.py sink-rule --skill example --kind regex-gate --id DC-001
python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict
python3 skills/skill-contract/scripts/skill_contract.py eval --all --strict
```

`refine_skill.py` is a compatibility symlink. Read [valves-and-metabolism.md](references/valves-and-metabolism.md) and [eval-cases.json](references/eval-cases.json) for contract details and live assertions. This tool audits and evaluates the local repository and does not modify existing skills unless `init`, `add-case`, or `sink-rule` explicitly requests it.
