# Dual-Zone Issue Format Specification (`issue-brief`)

Every automated or agent-authored Multica / Linear issue must separate **Owner decision clarity** (Upper Zone) from **AI execution evidence** (Lower Zone).

## 1. Document Anatomy

```markdown
<!-- multica-cursor:skills-roi-autopilot-2026-11-19 -->

**一句话结论**：今日技能巡检发现 3 个高频技能返工偏高，勾选对应项后即可由后台总控自动加锁修复。

- [ ] 1. 收敛 `team-lead` 常见漏检 — 把高频返工规则下沉为确定性脚本检查，减少重复打回。
- [ ] 2. 补齐 `howto` 回归用例 — 为零通过率环节补齐可执行抽屉断言，避免口头约束失效。
- [ ] 3. 精简超长入口文档 — 拆分超标技能说明，降低冷启动阅读噪音。

<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>

- `dedup_key`: `skills-roi-autopilot-2026-11-19`
- Exact metrics, Markdown tables, file paths, and CLI verification commands...

</details>
```

## 2. Upper Zone Rules (`上半区 · 给人看`)

1. **Line Budget (`human_zone_lines <= 45`)**:
   - Count non-empty lines appearing **before** `<details>`, excluding HTML comments (`<!-- ... -->`) and horizontal rules (`---`).
   - Practical ceiling: `45` lines by default (`max_human_lines: int = 45`, overridable by callers) so executive summaries and compact Markdown tables fit naturally.
2. **Encouraged Concrete Content**:
   - Concrete numbers, percentages (`83.39%`), currency amounts (`¥86,358.24`), fund codes (`017811`), issue/PR identifiers (`TIA-15`, `#196`), file/module names, and **compact Markdown tables** (such as 5-quadrant portfolio tables and FIFO unlock tables) are explicitly allowed and encouraged in the Upper Zone.
3. **Required Structure & Anti-Boilerplate Gate (`duplicate_boilerplate_items`)**:
   - **Plain-Chinese TL;DR**: A sentence starting with `**一句话结论**：` or `**核心结论**：`.
   - **Distinct Checkable Decision Items**: Concise `- [ ]` or `- [x]` items where every item names a **distinct concrete target** and a **specific metric/reason**. Repeating identical targets or copy-pasted generic rationales across 2+ items is rejected with `duplicate_boilerplate_items`.
4. **Forbidden Noise in Human Zone**:
   - **Internal Metric / Stage Codes**: `S1`..`S12`, `M1`..`M5`, `EV-001` (or `EV-\d+`), `DC-001` (or `DC-\d+`), `H1`..`H5`, `R10`, `V1`..`V5`.
   - **Raw Hashes & UUIDs**: Commit SHAs (`\b[0-9a-f]{7,40}\b` hex with letters+digits or 40-char SHA) and UUIDs (`\b[0-9a-f]{8}-[0-9a-f]{4}-...`).
   - **Fenced Code & CLI Invocations**: Multi-line fences (` ``` ` or `~~~`) and CLI commands (`python3 ... --flag`, `multica issue ...`, `./scripts/verify`).

## 3. Lower Zone & Watermark Rules (`下半区 · 给 AI 看`)

1. **Exact Collapsible Wrapper**:
   - Opening tag must include `<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>` and close with a balanced `</details>`.
2. **Cursor / Dedup Watermarks**:
   - Every Dual-Zone issue must carry `<!-- multica-cursor:<cursor> -->` (plus optional legacy dedup comments such as `<!-- multica-skills-autopilot:<key> -->` or `<!-- GTT-LINEAR-DEDUP:<key> -->`).
3. **Rich AI Payload**:
   - Store `topic_key`, `dedup_key`, `S1..S12` / `M1..M5` telemetry tables, JSON payloads, file paths, and exact CLI remediation & verification commands inside `<details>...</details>`.

## 4. Python & CLI Contract (`verify_issue_format.py`)

- `render_dual_zone_issue(*, tldr, items, cursor, ai_context_md="", ai_payload=None, extra_comments=None, heading=None, footer=None) -> str`
- `verify_dual_zone_issue(markdown_text, *, max_human_lines=45, require_cursor=True, allow_compact_table=True) -> dict[str, Any]`
- CLI exit codes:
  - `0`: Compliant Dual-Zone Markdown (`{"ok": true, ...}`).
  - `2`: Contract violation or unreadable input (`{"ok": false, "violations": [...]}`).
