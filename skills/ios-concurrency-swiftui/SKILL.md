---
name: ios-concurrency-swiftui
description: "Audit and engineer Swift 6 strict concurrency, Sendable/actor isolation, task lifecycle cancellation, SwiftUI Observation state/layout identity, and native spring/haptic/Liquid Glass interaction feel. Not for Instruments profiling, App Store rejection gates, or SwiftData schema migrations."
---

# ios-concurrency-swiftui

Expert iOS/macOS concurrency and SwiftUI engineering contract distilling **5 `TIA-3` Knowledge Vault cards** into deterministic source audits, state-ownership matrices, and interaction-feel guardrails.

## `/howto` P7 Global 5-Tuple

| Dimension | Contract |
|---|---|
| **Inbound** | Swift/SwiftUI source files or snippets containing `async/await`, `actor`, `Sendable`, `@MainActor`, `Task`, `@Observable`, `@State`, `ForEach`, `GeometryReader`, springs, haptics, or Liquid Glass modifiers. |
| **Internal** | `scripts/audit_concurrency_swiftui.py`: deterministic AST/token-masked source scanner (`audit-source`), 5-card catalog (`list-cards`), and 100% card-coverage verifier (`check-card-coverage`). |
| **Calls** | Zero-token Layer-0 Python CLI (`audit_concurrency_swiftui.py`); escalates to `xcode-cli` for `swift test` / Thread Sanitizer runs or `my-designer` for Visual Traceback. |
| **PointsTo** | Structured JSON/Markdown findings with `rule_id`, `card_slug`, `severity`, `line`, and concrete remediation for concurrency races, task leaks, unstable SwiftUI identity, and unguarded motion/glass modifiers. |
| **Trigger & Notify** | Invoked before/during Swift 6 concurrency migration, SwiftUI view decomposition, or native interaction polish; emits fail-closed exit code `2` under `--strict` when blocking violations remain. |

## When-to-Read & Progressive Disclosure Routing

- **Layer 0 (Deterministic Gate First)**: Run `scripts/audit_concurrency_swiftui.py audit-source` on changed `.swift` files before writing manual review prose.
- **Layer 1 (5-Card Rules, FAQs & Checklists)**: Read [`references/concurrency-and-swiftui-cards.md`](references/concurrency-and-swiftui-cards.md) when designing actor boundaries, migrating completion handlers, choosing SwiftUI property wrappers, or tuning native spring/haptic/Liquid Glass restraint.
- **Layer 2 (Regression & Trigger Drawer)**: Inspect [`references/eval-cases.json`](references/eval-cases.json) when registering new anti-patterns or running `skill_contract.py eval/description-eval`.
- **Sibling Routing**:
  - Frame-drop profiling with Instruments, bug-hunt swarms, macOS SwiftPM `.app` packaging, or App Store rejection/privacy manifests -> `ios-debugging-performance`.
  - State-driven navigation/deep links, SwiftData migrations, characterization/snapshot tests, CloudKit/Push seams, or Apple Intelligence fallbacks -> `ios-architecture-testing`.

## Complete 5-Card Coverage Matrix (`TIA-3`)

| # | Knowledge Vault Card (`knowledge-vault/concepts/`) | Core Engineering Contract | Deterministic Script Rules |
|---|---|---|---|
| 1 | `swift-concurrency-expert-coding.md` | Swift 6 strict concurrency, `Sendable` DTOs, narrow `@MainActor` UI entry, actor-isolated I/O, `async/await` cancellation & reentrancy tests | `CS-001` (`UNCHECKED_SENDABLE_WITHOUT_PROOF`), `CS-002` (`MAINACTOR_BLOCKING_IO`) |
| 2 | `swift-concurrency-memory-safety.md` | Explicit `Task` owner & cancellation points, actor reentrancy state re-check after `await`, retain-cycle & closure capture audits, TSan | `CS-003` (`DETACHED_TASK_WITHOUT_OWNER`), `CS-004` (`UNOWNED_ASYNC_CAPTURE`) |
| 3 | `swiftui-architecture-and-performance.md` | Stable domain `ForEach` identity, `@Observable` / `@StateObject` ownership without `body` re-init, expensive sort/format/decode kept out of `body` | `CS-005` (`UNSTABLE_FOREACH_INDEX_ID`), `CS-006` (`MODEL_INIT_OR_HEAVY_WORK_IN_BODY`) |
| 4 | `swiftui-state-layout-and-view-structure.md` | State ownership matrix (`@State` vs `@Binding` vs `@Environment`), bounded layout instead of unbounded `GeometryReader`, 4-axis Preview matrix | `CS-007` (`UNBOUNDED_GEOMETRY_READER`), `CS-008` (`SERVICE_CREATION_IN_VIEW_BODY`) |
| 5 | `apple-design-and-game-feel.md` | Native interaction timing (instant 0ms vs spring), continuous gesture velocity handoff, haptics alignment, Liquid Glass contrast/layer restraint, `accessibilityReduceMotion` | `CS-009` (`SPRING_OR_REPEAT_WITHOUT_REDUCE_MOTION`), `CS-010` (`STACKED_LIQUID_GLASS_LAYERS`) |

## Core Engineering Guardrails

1. **Compile-Time Isolation Over Blanket `@MainActor`**: Keep `@MainActor` strictly on UI state and view-facing entry points. Move networking, JSON decoding, and disk persistence into dedicated `actor` or nonisolated `Sendable` pipelines; never slap `@unchecked Sendable` on mutable reference types without an inline thread-safety proof (`// SAFETY:`).
2. **Task Lifecycle & Reentrancy Discipline**: Bind long-lived tasks to SwiftUI `.task(id:)` or cancel stored `Task` handles on teardown. Re-validate actor invariants after every `await` suspension point.
3. **Predictable SwiftUI Trees**: Give every `ForEach` a stable domain `ID` (never `.indices` or `UUID()` generated inside `body`), hoist sorting/`DateFormatter` allocation out of `body`, and pass minimal value inputs + action closures to leaf views.
4. **Restrained Native Feel**: Pair continuous gestures with velocity-preserving springs, respect `@Environment(\.accessibilityReduceMotion)`, and keep Liquid Glass to a single elevated chrome layer with verified contrast.

## CLI Quick Reference

```bash
# List all 5 mapped TIA-3 cards and deterministic rule IDs
python3 skills/ios-concurrency-swiftui/scripts/audit_concurrency_swiftui.py list-cards

# Verify 100% 5-card coverage across SKILL.md, references/, and script rules
python3 skills/ios-concurrency-swiftui/scripts/audit_concurrency_swiftui.py check-card-coverage

# Audit Swift/SwiftUI source files or inline snippet (fails with exit 2 on --strict violations)
python3 skills/ios-concurrency-swiftui/scripts/audit_concurrency_swiftui.py audit-source --snippet 'Task.detached { self.reload() }' --strict
```
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/concurrency-and-swiftui-cards.md` | `python3 skills/ios-concurrency-swiftui/scripts/audit_concurrency_swiftui.py --help` |
| verification | `references/concurrency-and-swiftui-cards.md` | `skill_contract.py eval --skill ios-concurrency-swiftui --strict` |
