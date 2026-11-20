---
name: ios-architecture-testing
description: "Design state-driven iOS navigation/migrations, SwiftData VersionedSchema persistence, behavior/characterization test contracts, bounded batch refactors, CloudKit/Push protocol seams, and Apple Intelligence/multi-platform fallbacks. Not for Instruments profiling or App Store submission gates."
---

# ios-architecture-testing

End-to-end iOS/macOS architecture, SwiftData persistence migration, behavior-contract testing, controlled batch refactoring, and Apple platform capability integration skill distilling **6 `TIA-3` Knowledge Vault cards**.

## `/howto` P7 Global 5-Tuple

| Dimension | Contract |
|---|---|
| **Inbound** | iOS/macOS navigation & deep-link models, SwiftData `@Model` / `VersionedSchema` definitions, test suites, batch-refactor work packages (`Owned Paths` + DAG), CloudKit/Push/Widget integrations, or Apple Intelligence / Core ML / multi-platform capability matrices. |
| **Internal** | `scripts/audit_arch_testing.py`: deterministic architecture/SwiftData/testing source scanner (`audit-source`), platform capability & refactor work-package validator (`check-capability-matrix`), 6-card catalog (`list-cards`), and 100% card-coverage verifier (`check-card-coverage`). |
| **Calls** | Zero-token Layer-0 Python CLI (`audit_arch_testing.py`); coordinates with `codebase-design` for deep seams/`CONTEXT.md`, `xcode-cli` for `swift test` / `xcodebuild test`, and `skill-contract` for skill deduplication audits. |
| **PointsTo** | State-driven router & accessibility contracts, migration-tested SwiftData schemas, non-brittle behavior/characterization gates, conflict-free refactor waves, protocol-isolated system capabilities, and zero-crash device fallback matrices. |
| **Trigger & Notify** | Invoked when planning or auditing iOS navigation, SwiftData schema evolution, TDD/refactor guardrails, CloudKit/Push generators, or Apple Intelligence / watchOS / visionOS feature boundaries; exits `2` under `--strict` on violations. |

## When-to-Read & Progressive Disclosure Routing

- **Layer 0 (Deterministic Gate First)**: Run `scripts/audit_arch_testing.py audit-source` or `check-capability-matrix` before approving architecture, schema, or refactor plans.
- **Layer 1 (6-Card Blueprints, FAQs & Checklists)**: Read [`references/architecture-data-testing-cards.md`](references/architecture-data-testing-cards.md) for state-driven navigation + accessibility, SwiftData migration fixtures, test contracts, bounded refactor packages, system capability seams, and Apple Intelligence fallbacks.
- **Layer 2 (Regression & Trigger Drawer)**: Inspect [`references/eval-cases.json`](references/eval-cases.json) when adding regression cases or running `skill_contract.py`.
- **Sibling Routing**:
  - Swift 6 strict concurrency, `Sendable` / actor boundaries, task cancellation, SwiftUI `@Observable` view trees, or spring/haptic/Liquid Glass feel -> `ios-concurrency-swiftui`.
  - Bug-hunt swarms, Instruments profiling, pure-SwiftPM macOS MenuBar `.app` signing/notarization, or App Store rejection/privacy manifests -> `ios-debugging-performance`.

## Complete 6-Card Coverage Matrix (`TIA-3`)

| # | Knowledge Vault Card (`knowledge-vault/concepts/`) | Core Engineering Contract | Deterministic Script Rules |
|---|---|---|---|
| 10 | `ios-architecture-navigation-migration.md` | State-driven `NavigationStack` / sheet / tab routing & deep-link projection, macOS vs iOS navigation divergence (`#if os(macOS)` `NavigationSplitView`/Commander toolbar + `⌘1..⌘4` vs `#if os(iOS)` bottom floating glass capsule dock + single-column `NavigationStack`), OS API deprecation inventory, VoiceOver + Dynamic Type + keyboard acceptance | `AT-001` (`DEPRECATED_NAVIGATION_VIEW`), `AT-002` (`ICON_ONLY_CONTROL_WITHOUT_A11Y_LABEL`), `AT-011` (`MACOS_IOS_NAVIGATION_NOT_DIVERGED`) |
| 11 | `swiftdata-persistence-and-migration.md` | Explicit `@Relationship(deleteRule:)`, composition over fragile `@Model` inheritance, `VersionedSchema` + `SchemaMigrationPlan` fixtures, user-visible recovery paths | `AT-003` (`SWIFTDATA_RELATIONSHIP_MISSING_DELETE_RULE`), `AT-004` (`SILENT_MODEL_CONTAINER_CRASH_OR_SWALLOW`) |
| 12 | `test-contracts-and-regression-gates.md` | Fail-first behavior contracts, characterization tests before refactoring, combined snapshot + flow/integration + mutation/coverage ratchet gates | `AT-005` (`ASSERTIONLESS_TEST_CASE`), `AT-006` (`SLEEP_BASED_FLAKY_TEST_SYNC`) |
| 13 | `refactor-review-and-skill-audit.md` | Disjoint `Owned Paths` + explicit input/output & verify commands for parallel refactor packages, reversible commits, pre-creation skill deduplication audit | `AT-007` (`OVERLAPPING_REFACTOR_WORK_PACKAGES`) |
| 14 | `feature-generators-and-apple-platform-integration.md` | CloudKit / Push / Deep-Link / Offline capabilities behind injected protocol seams, tested for permission-denied, offline retry, and launch recovery | `AT-008` (`DIRECT_SYSTEM_SINGLETON_WITHOUT_SEAM`), `AT-009` (`MISSING_OFFLINE_OR_PERMISSION_DENIED_PATH`) |
| 15 | `apple-intelligence-and-special-platforms.md` | Explicit OS/device/permission/model capability matrix, mandatory non-AI fallback when unavailable, background cancellable Core ML inference with timeout & memory budget | `AT-010` (`APPLE_INTELLIGENCE_OR_COREML_WITHOUT_FALLBACK`) |

## Core Engineering Guardrails

1. **Navigation, Platform Divergence & Accessibility Are Structural**: Model push, sheet, and tab destinations as explicit value state so deep links and state restoration never fight the back stack; split cross-platform root navigation chrome via `#if os(macOS)` (`NavigationSplitView` or top Commander `ToolbarItem` + multi-column/inspector layout + `⌘1..⌘4` `.keyboardShortcut`, zero mobile bottom capsule dock) vs `#if os(iOS)` (bottom floating glass capsule dock / `TabView` + single-column `NavigationStack`) (`AT-011`); verify VoiceOver order, Dynamic Type, and keyboard focus on every core flow.
2. **Schema Evolution Precedes UI**: Declare SwiftData invariants, uniqueness, and `@Relationship(deleteRule:)` upfront; keep historical `VersionedSchema` fixtures and surface actionable user recovery instead of `try! ModelContainer(...)`.
3. **Contract Tests & Bounded Refactors**: Lock behavior with characterization tests before refactoring, split parallel refactor waves strictly along non-overlapping `Owned Paths`, and audit existing skills before minting overlapping ones.
4. **Seam-Isolated Capabilities & Device Fallbacks**: Wrap CloudKit, Push, and FoundationModels/Core ML behind protocol adapters with explicit permission-denied, offline, timeout, memory-budget, and unsupported-device fallback paths.

## CLI Quick Reference

```bash
# List all 6 mapped TIA-3 cards and deterministic rule IDs
python3 skills/ios-architecture-testing/scripts/audit_arch_testing.py list-cards

# Verify 100% 6-card coverage across SKILL.md, references/, and script rules
python3 skills/ios-architecture-testing/scripts/audit_arch_testing.py check-card-coverage

# Audit Swift architecture, SwiftData models, system singletons, and XCTest/Swift Testing suites
python3 skills/ios-architecture-testing/scripts/audit_arch_testing.py audit-source --snippet 'NavigationView { Text("Legacy") }' --strict

# Validate capability fallback matrix & disjoint batch-refactor work packages
python3 skills/ios-architecture-testing/scripts/audit_arch_testing.py check-capability-matrix --matrix-json '{"capabilities":[{"name":"apple_intelligence_summary","availability_checked":true,"fallback_mode":"deterministic_extractive_summary","permission_denied_handled":true,"offline_or_timeout_budget_ms":3000,"memory_budget_mb":120}],"work_packages":[{"id":"WP1","owned_paths":["Sources/Navigation"],"verify_cmd":"swift test --filter NavigationTests"},{"id":"WP2","owned_paths":["Sources/Persistence"],"verify_cmd":"swift test --filter PersistenceTests"}],"skill_audit_checked_existing":true}' --strict
```
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/architecture-data-testing-cards.md` | `python3 skills/ios-architecture-testing/scripts/audit_arch_testing.py --help` |
| verification | `references/architecture-data-testing-cards.md` | `skill_contract.py eval --skill ios-architecture-testing --strict` |
