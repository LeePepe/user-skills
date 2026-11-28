---
name: save-tool
description: "Use when users share skills, tools, repositories, or links to save for later, or query collected tools. Extract, deduplicate, file topic notes, and search collected-tools via Hybrid RAG. Does not install or run tools."
user-invocable: true
---

# Save Tool

Turn a pasted message (a video script, tweet, list of links, or "check out these tools" dump) into durable, deduplicated notes under one stockpile folder, and retrieve saved tools via Hybrid RAG — while strictly maintaining **Zero Execution** (never installing, cloning, or running any collected tool).

## When-to-Read L1 Drawer & Mandatory L2 CLI Routing

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| search saved tools / dedupe | `SKILL.md` (`## Steps`) | `python3 skills/save-tool/scripts/tool_vault_search.py search --query "<tool/capability>" --json` |
| regression evaluation | `[eval-cases.json](references/eval-cases.json)` (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill save-tool --strict` |

## The Stockpile Folder

`${COLLECTED_TOOLS_DIR:-~/Development/Personal/collected-tools}/`

- `README.md` — the index table (`file | kind | one-line summary`) plus usage policy.
- One `.md` file per distinct tool/skill/repo (kebab-case filename), or one grouped `.md` file for a themed set of similar links.
- Memory holds at most one pointer note (`collected-tools-folder`), never one memory entry per tool.

## Steps

1. **Search & Dedupe First.** Before filing a new note or answering "do we already have a tool for X?", run Hybrid RAG over `collected-tools`:
   ```bash
   python3 skills/save-tool/scripts/tool_vault_search.py search --query "<tool/domain/capability>" --top-k 5 --json
   ```
   Also check `README.md` by repo URL or domain. If a matching topic file already exists, **append** to it rather than creating a duplicate.
2. **Extract.** Scan the pasted content for GitHub repos (`github.com/owner/repo`), websites/galleries/docs links, and named tools/skills (mark missing URLs as `unknown — needs lookup`), keeping the user's own surrounding description.
3. **Group.** Give each distinct tool/skill/repo its own `.md` file; group themed link sets (e.g., 8 gallery sites) into a single `.md` table.
4. **Light Enrich (Optional, Read-Only).** If a repo's purpose is unclear from the user's message, you may `WebFetch` its page once for a one-line summary. Skip if already clear.
5. **Write Note & Update Index.** Record what it is, repo/link URL, form (`CLI / skill / plugin / website / library`), documented usage notes, and an empty `実際に使ったメモ` section; add or refresh one row in `collected-tools/README.md`.
6. **Report.** Summarize new vs. appended notes and unresolved links. Offer a next step if helpful, without acting on it unless explicitly asked.

## Redlines & Boundaries

- **Zero Execution (Hard Redline):** Save and index only. Never run `npm install`, `pip install`, `brew install`, `git clone`, `subprocess`, or execute any script inside `collected-tools/`.
- **Vault Isolation:** `tool_vault_search.py` queries `corpus_scope="collected-tools"` via `skill_runtime.hybrid_rag` (`<=12KB` stdout) and rejects cross-vault `design-studio` scopes with `VaultIsolationError`.
- **Preserve User Context:** Retain the user's phrasing on why each item caught their eye.
