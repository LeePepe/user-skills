---
name: helix
description: Figma-to-code toolkit — compare designs vs code (fit-finish), generate code from Figma, and initialize design-system guides. Routes to the right workflow based on user intent.
---

# Helix

## Intent Routing

Determine which workflow to run:

| User intent | Workflow |
|---|---|
| Compare Figma vs code, find mismatches, QA UI parity | **Fit-Finish** (Section A) |
| Generate / implement code from Figma | **Gen-Code** (Section B) |
| Create or regenerate `.github/design-system-guide.md` | **Design-System Init** (Section C) |

If ambiguous between fit-finish and gen-code, ask one clarifying question.

Before delegating, extract **focusAreas** from the user prompt if present (e.g., `"typography, colors"`). Pass to the chosen workflow — it filters domains to reduce scope.

---

## Shared Pre-flight Checks

All workflows require these before proceeding.

### MCP Gate

Run `./references/mcp-precheck.md`. Do NOT proceed without a working Figma MCP connection.

If MCP tools are unavailable:
1. Check `.vscode/mcp.json` — create with Figma Desktop (`http://127.0.0.1:3845/mcp`) or Remote (`https://mcp.figma.com/mcp`) config if missing.
2. Ensure Figma Desktop has MCP enabled (Preferences → Allow MCP connections), or verify remote URL is reachable.
3. Ask user to reload VS Code / Claude Code, then re-verify tools.
4. **No fallback mode** — do NOT proceed without MCP.

### Design System Guide (Fit-Finish & Gen-Code only)

Search for `.github/design-system-guide.md` from cwd up to git root. Accept user-specified path as override.

- **Missing** → run Design-System Init (Section C) first, then return.
- **Exists but looks incomplete** → ask whether to regenerate.

### Figma Input

For input format details, see `./references/figma-input.md`.

---

## Section A — Fit-Finish

### Inputs

- **Figma target** (required): Figma Desktop selection, or Figma URL(s) with `node-id`
- **Code file path(s)** (required): absolute or repo-relative path(s)

If code path is missing, ask for it.

### Session Initialization

Create `/tmp/helix-{timestamp}/` and write `session.json`:

```json
{
  "figmaTarget": "<figma URL(s) or 'desktop-selection'>",
  "codePaths": ["<path1>", "<path2>"],
  "guidePath": "<resolved path to design-system-guide.md>",
  "focusAreas": "<from user prompt or empty>",
  "reportDir": ".github/helix/reports",
  "componentName": "<inferred from Figma or code file name>",
  "timestamp": "<ISO timestamp>"
}
```

### Phase 1 — Parallel Subagents

**CRITICAL: Invoke BOTH Agent tool calls in the same response for true parallel execution.**

**Agent A** — `subagent_type: "helix-design-system-analyzer"`:
```
You are the helix-design-system-analyzer agent.
session_dir: {session_dir}
guide_path: {guidePath}
focusAreas: {focusAreas}
```

**Agent B** — `subagent_type: "helix-code-analyzer"`:
```
You are the helix-code-analyzer agent.
session_dir: {session_dir}
code_paths: {codePaths}
guide_path: {guidePath}
```

Wait for both. Error handling:
- Design System Analyzer returns `{ "error": "design-system-guide not found" }` → stop, run Design-System Init first.
- Code Analyzer returns all missing files → stop, ask user to verify paths.

### Phase 2 — Figma Collection

Read `{session_dir}/phase1-design-system.json`, extract `domains` array.

Launch `subagent_type: "helix-figma-collector"`:
```
You are the helix-figma-collector agent.
session_dir: {session_dir}
figma_target: {figmaTarget}
domains: {domains_json}
```

If Figma MCP error → re-run MCP precheck.

### Phase 3 — Comparison

Launch `subagent_type: "helix-comparer"`:
```
You are the helix-comparer agent.
session_dir: {session_dir}
focus_areas: {focusAreas}
report_dir: .github/helix/reports
component_name: {componentName}
```

Read `{session_dir}/phase3-compare.json`. Display match rate and high-severity count. Tell user where the report was saved.

### Phase 4 — Optional Fix

Ask: **"Apply fixes to the code now? (yes / no)"**

If yes, launch `subagent_type: "helix-code-generator"`:
```
You are the helix-code-generator agent.
mode: FIX
session_dir: {session_dir}
```

Display which files were modified.

### Fallback (no Agent tool)

Run sequentially using reference docs:
1. `./references/design-system-analyzer.md` + `./references/code-analyzer.md`
2. `./references/figma-collector.md`
3. `./references/fit-finish.md`

---

## Section B — Gen-Code

### Inputs

- **Figma target** (required): Figma Desktop selection, or Figma URL(s) with `node-id`
- **Output file path** (optional): where to write generated code
- **Framework / platform hint** (optional): e.g., `"SwiftUI"`, `"React"`, `"Flutter"`
- **Focus areas** (optional): comma-separated design aspects

### Session Initialization

Create `/tmp/helix-{timestamp}/` and write `session.json`:

```json
{
  "figmaTarget": "<figma URL(s) or 'desktop-selection'>",
  "outputPath": "<optional user-provided path>",
  "frameworkHint": "<optional framework>",
  "guidePath": "<resolved path to design-system-guide.md>",
  "focusAreas": "<from user prompt or empty>",
  "timestamp": "<ISO timestamp>"
}
```

### Phase 1 — Design System Analysis

Launch `subagent_type: "helix-design-system-analyzer"`:
```
You are the helix-design-system-analyzer agent.
session_dir: {session_dir}
guide_path: {guidePath}
focusAreas: {focusAreas}
```

If error → stop, run Design-System Init first.

### Phase 2 — Figma Collection

Read `{session_dir}/phase1-design-system.json`, extract `domains` and `frameworkInfo`.

Launch `subagent_type: "helix-figma-collector"`:
```
You are the helix-figma-collector agent.
session_dir: {session_dir}
figma_target: {figmaTarget}
domains: {domains_json}
```

### Phase 3 — Planning

Launch `subagent_type: "helix-planner"`:
```
You are the helix-planner agent.
session_dir: {session_dir}
output_path: {outputPath}
framework_hint: {frameworkHint}
focus_areas: {focusAreas}
```

### Phase 4 — Code Generation

Launch `subagent_type: "helix-code-generator"`:
```
You are the helix-code-generator agent.
mode: BUILD
session_dir: {session_dir}
output_path: {outputPath}
```

Show: generated file path, token mapping summary, any `// TODO: add design token` items.

### Fallback (no Agent tool)

Run sequentially using reference docs:
1. `./references/design-system-analyzer.md`
2. `./references/figma-collector.md`
3. `./references/gen-code.md`

---

## Section C — Design-System Init

### When to Use

- `.github/design-system-guide.md` does not exist
- Existing guide is stale / incomplete / user requests regeneration
- Fit-finish or gen-code is blocked by missing guide

### Phase 1 — Execution

Use `./references/design-system-rules-prompt.md` as orchestrator.

**1.1 Fetch Figma design system rules**

Load `./references/design-system-figma-rules.md` for details. Call `mcp_figma-desktop_create_design_system_rules` (or `mcp_figma_create_design_system_rules`).

**1.2 Analyze the codebase**

Load `./references/design-system-codebase-analysis.md` for details. Scan for:
- Design tokens (colors, typography, spacing, sizing)
- Component patterns and file conventions
- Accessibility patterns
- Localization patterns
- Framework / platform detection

**1.3 Synthesize the guide**

Combine Figma rules (1.1) and codebase analysis (1.2) into `.github/design-system-guide.md`:
- Quick Reference
- Token Mapping (Figma → Code)
- Code Examples
- Accessibility Guidelines
- Localization Patterns
- Best Practices

**1.4 Summary**

Report what was generated and flag sections needing human review.

---

## Cleanup

After any workflow, optionally remove `{session_dir}`. Ask user if they want to keep it for debugging.