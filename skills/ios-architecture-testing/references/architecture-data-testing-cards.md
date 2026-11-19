# Architecture, SwiftData, Testing & Platform Integration Cards (`TIA-3` Cards 10–15)

Distilled reference drawer for [`SKILL.md`](../SKILL.md). Every rule maps 1-to-1 to Cards 10–15 in `knowledge-vault/concepts/`.

---

## Card 10: `knowledge-vault/concepts/ios-architecture-navigation-migration.md`
- **Source Lineage**: `claude-code-apple-skills/skills/ios/coding-best-practices`, `navigation-patterns`, `migration-patterns`, `accessibility-audit`.
- **Essence**: Expert iOS engineering unifies state architecture, `NavigationStack`/sheet/tab routing, OS-version API migrations, and accessibility into one verifiable product chain.
- **Pain-Point FAQs & Checklist**:
  - *Why model navigation as state first?* Push paths, sheets, and tabs are projections of route state; explicit route enums prevent deep links and state restoration from colliding.
  - *Why scan OS deprecations before migrating?* Inventory deprecated APIs (e.g., `NavigationView` -> `NavigationStack`), behavioral shifts, and deployment targets before editing so interaction semantics do not silently drift.
  - *Why diverge macOS and iOS root navigation (`AT-011`)?* Cross-platform SwiftUI apps targeting both `macOS` and `iOS` MUST split root navigation chrome via `#if os(macOS)` / `#if os(iOS)`: iOS uses a bottom floating glass capsule dock / bottom `TabView` + single-column `NavigationStack` drill-down; macOS uses a desktop `NavigationSplitView` sidebar or top Commander quick-switcher `ToolbarItem` + master-detail multi-column/inspector layout + `⌘1..⌘4` `.keyboardShortcut` bindings—never place a mobile bottom floating capsule dock on a macOS desktop window.
  - *Why bake accessibility in upfront?* Dynamic Type scaling, VoiceOver reading order, `accessibilityLabel` on icon-only buttons, and minimum hit targets reshape layout containers.
  - **Checklist**: (1) Draw state/route graph + deep-link parser and split `#if os(macOS)` vs `#if os(iOS)` root navigation (`AT-011`); (2) track OS API migration inventory with behavioral regression tests; (3) verify VoiceOver, Dynamic Type, and hardware keyboard navigation (`⌘1..⌘4`) on every primary flow.

---

## Card 11: `knowledge-vault/concepts/swiftdata-persistence-and-migration.md`
- **Source Lineage**: `claude-code-apple-skills/skills/macos/swiftdata-architecture`, `swiftdata/inheritance`, `generators/persistence-setup`.
- **Essence**: SwiftData longevity lives or dies by schema evolution—invariants, `@Relationship(deleteRule:)`, inheritance vs composition, and tested `SchemaMigrationPlan` paths precede any View code.
- **Pain-Point FAQs & Checklist**:
  - *Why do demo `@Model` classes fail in production?* Missing `@Attribute(.unique)` constraints, implicit `.nullify` vs `.cascade` delete rules, cyclic relationships, and unversioned schema edits corrupt real user stores.
  - *When is `@Model` inheritance justified?* Only when the base model carries stable shared query semantics and a documented migration plan; prefer composition otherwise.
  - *Minimum migration test bar?* Load a real V1 SQLite/store fixture, migrate via `SchemaMigrationPlan` to V2, verify relationship integrity + delete rules + nil boundaries, and surface user-facing recovery UI instead of `try! ModelContainer(...)`.

---

## Card 12: `knowledge-vault/concepts/test-contracts-and-regression-gates.md`
- **Source Lineage**: `claude-code-apple-skills/skills/testing/test-contract`, `tdd-refactor-guard`, `flow-walkthrough`.
- **Essence**: High-leverage tests lock user-visible behavior, data contracts, and fault boundaries rather than chasing line coverage or coupling to private view hierarchies.
- **Pain-Point FAQs & Checklist**:
  - *Why aren't snapshots sufficient for UI correctness?* Snapshots catch pixel shifts but miss interaction state machines, Dynamic Type reflow, VoiceOver semantics, and cross-device flows—pair them with flow/integration tests.
  - *How to refactor legacy code safely?* Capture current behavior with characterization tests first, migrate behind the contract, then tighten assertions; avoid `Task.sleep` sync hacks in favor of deterministic clocks/continuations.
  - **Checklist**: (1) Write fail-first behavior contracts (`#expect` / `XCTAssert*`); (2) preserve characterization tests across refactors; (3) combine snapshot, integration, and mutation/coverage ratchets proportional to risk.

---

## Card 13: `knowledge-vault/concepts/refactor-review-and-skill-audit.md`
- **Source Lineage**: `dimillian-skills/orchestrate-batch-refactor`, `review-and-simplify-changes`, `project-skill-audit`.
- **Essence**: Large refactors stay safe when decomposed into bounded work packages with disjoint `Owned Paths`, explicit DAG dependencies, independent verification commands, and pre-creation skill deduplication audits.
- **Pain-Point FAQs & Checklist**:
  - *When can refactor packages run in parallel?* Only when `Owned Paths` are non-overlapping and shared core interfaces are frozen first in a serial prerequisite wave.
  - *What does a simplify/skill audit check?* Dead abstractions, duplicated logic, naming clarity, and existing skill coverage—never mint a duplicate skill when an existing package drawer can absorb the rule.
  - **Checklist**: (1) Declare `owned_paths`, inputs/outputs, and `verify_cmd` per package; (2) keep commits atomic/reversible and run contract review before merge; (3) audit existing skills for overlap before creating new ones.

---

## Card 14: `knowledge-vault/concepts/feature-generators-and-apple-platform-integration.md`
- **Source Lineage**: `claude-code-apple-skills/skills/generators`, `ci-cd-setup`, `cloudkit-sync`, `push-notifications`.
- **Essence**: Production capability scaffolding (`CloudKit`, `UNUserNotificationCenter`, deep links, widgets, offline sync) must wire permissions, background lifecycle, offline retry, and protocol test seams together.
- **Pain-Point FAQs & Checklist**:
  - *Why review generated capability code?* Scaffolds cover happy paths; auth tokens, conflict resolution, error recovery, and actor isolation must still bind cleanly through injected protocols instead of scattered `CKContainer.default()` / `UNUserNotificationCenter.current()` calls.
  - *Most frequently missed integration edges?* Permission-denied UX, background refresh expiration, offline queue replay, deep-link cold-start restoration, data export, and accessibility.
  - **Checklist**: (1) Test compile + permission-denied + offline + recovery paths immediately; (2) isolate CloudKit/Push/network behind protocol seams; (3) keep a minimal example, failure fixture, and upgrade note per capability.

---

## Card 15: `knowledge-vault/concepts/apple-intelligence-and-special-platforms.md`
- **Source Lineage**: `claude-code-apple-skills/skills/apple-intelligence/foundation-models`, `core-ml`, `watchos`, `visionos/spatial-design`.
- **Essence**: Platform capabilities (`FoundationModels`, `Core ML`, `MapKit`, `watchOS`, `visionOS`) require an upfront capability matrix—OS, hardware eligibility, user permission, timeout/memory budgets, and a deterministic fallback—so unsupported devices degrade gracefully.
- **Pain-Point FAQs & Checklist**:
  - *Why write the non-AI fallback before calling `LanguageModelSession`?* Hardware eligibility, region, Apple Intelligence toggle state, and context windows vary at runtime; missing fallbacks turn capability gaps into blank screens or crashes.
  - *How to keep Core ML / Vision off the main thread?* Load models, preprocess inputs, and run inference inside cancellable background tasks with explicit timeout and peak-memory ceilings.
  - *Cross-platform reuse rule*: Share domain models and protocol contracts across iPhone, iPad, Mac, Watch, and Vision, never raw interaction density or window lifecycle assumptions.
