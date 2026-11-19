# Concurrency & SwiftUI Knowledge Cards (`TIA-3` Cards 1–5)

Distilled reference drawer for [`SKILL.md`](../SKILL.md). Every rule maps 1-to-1 to the 5 `TIA-3` cards in `knowledge-vault/concepts/` without leaking personal filesystem paths.

---

## Card 1: `knowledge-vault/concepts/swift-concurrency-expert-coding.md`
- **Source Lineage**: `dimillian-skills/swift-concurrency-expert` + `claude-code-apple-skills/skills/swift/concurrency` & `concurrency-patterns`.
- **Essence**: Swift 6 strict concurrency turns cross-task value transfer, isolation domains, and UI main-thread boundaries into compile-time contracts.
- **Pain-Point FAQs**:
  - *Why can code that compiles in Swift 5 still crash?* Syntax checks do not prove access order on shared mutable state; Swift 6 surfaces cross-actor transfers at compile time.
  - *Is `@MainActor` universal glue?* No. Wrap only UI state and UI entry points; keep network, parsing, and disk work in dedicated actors or background tasks.
  - *How to handle non-`Sendable` third-party types?* Convert to immutable value DTOs at the boundary; use `@unchecked Sendable` only with documented lock/queue proof (`// SAFETY:`) and tests.
- **Immediate Checklist**:
  1. Enable Swift 6 complete concurrency checking (`-strict-concurrency=complete`) and resolve every isolation/`Sendable` warning.
  2. Define explicit `actor` boundaries for networking, persistence, and caches; deliver immutable results to `@MainActor`.
  3. Migrate completion handlers with `withCheckedThrowingContinuation` (guaranteeing single resume) and test cancellation + actor reentrancy.

---

## Card 2: `knowledge-vault/concepts/swift-concurrency-memory-safety.md`
- **Source Lineage**: `claude-code-apple-skills/skills/swift/concurrency`, `swift/memory`, `swift/concurrency-patterns`.
- **Essence**: Concurrency correctness = isolation boundary + lifecycle ownership + cooperative cancellation; `async` alone never prevents task leaks, reentrancy bugs, or retain cycles.
- **Pain-Point FAQs**:
  - *Task keeps mutating state after screen dismissal?* Prefer SwiftUI `.task(id:)` or store the `Task` handle in the owning model and cancel on `deinit`/disappear; propagate `Task.checkCancellation()` into network/storage layers.
  - *Does `actor` eliminate all races?* Only for actor-isolated properties; shared references passed across actors and state assumptions across `await` suspension points still race (actor reentrancy).
  - *How to audit memory + concurrency together?* Inspect task trees, closure captures (forbid `[unowned self]` in async closures), and retain cycles under Thread Sanitizer (TSan).
- **Immediate Checklist**:
  1. Assign every long-running `Task` an explicit owner, cancellation check, and delivery actor (avoid untracked `Task.detached`).
  2. Combine Thread Sanitizer (`-sanitize=thread`) with lifecycle teardown tests.
  3. Audit async closure captures and post-`await` actor state assumptions during code review.

---

## Card 3: `knowledge-vault/concepts/swiftui-architecture-and-performance.md`
- **Source Lineage**: `dimillian-skills/swiftui-view-refactor`, `swiftui-performance-audit`, `claude-code-apple-skills/skills/swiftui/data-flow`.
- **Essence**: SwiftUI jank stems primarily from unstable view-tree identity, tangled state ownership, or heavy work inside `body`—stabilize identity and data flow before micro-optimizing.
- **Pain-Point FAQs**:
  - *Why do smaller subviews render faster?* Narrower observation scope limits invalidation to the exact subtree reading the mutated `@Observable` property.
  - *Why does `List`/`ScrollView` stutter?* Unstable `ForEach` identity (`.indices` or inline `UUID()`), sorting/formatting/image decoding inside `body`, or repeated layout measurement.
  - *Can `@Observable` and `@StateObject` coexist?* Yes, with strict ownership: creator holds `@State` (for `@Observable`) or `@StateObject` (for `ObservableObject`); injected children read plain references or `@Bindable`—never instantiate models inside `body`.
- **Immediate Checklist**:
  1. Split monolithic `body` blocks into semantic subviews with unidirectional data flow and explicit inputs.
  2. Supply stable business/domain IDs to every `ForEach`.
  3. Hoist sorting, `DateFormatter`/`NumberFormatter`, and image decoding out of `body`; verify update counts in Instruments.

---

## Card 4: `knowledge-vault/concepts/swiftui-state-layout-and-view-structure.md`
- **Source Lineage**: `claude-code-apple-skills/skills/swiftui/data-flow`, `swiftui/layout`, `swiftui/toolbars`.
- **Essence**: Declarative UI stays predictable when state mutations touch only subscribing nodes and layouts survive compact/regular widths, Dynamic Type, and platform chrome.
- **Property-Wrapper Ownership Matrix**:

| Scenario | Swift 5.9+ Observation | Legacy Combine | Rule |
|---|---|---|---|
| View owns local value state | `@State private var` | `@State private var` | Always `private`; single source of truth |
| Child mutates parent value | `@Binding var` | `@Binding var` | Two-way projection without ownership |
| View creates & owns reference model | `@State private var model = ...` | `@StateObject private var` | Never initialize inside `body` |
| Child binds properties of `@Observable` | `@Bindable var model` | `@ObservedObject var` | Passed explicitly from parent |
| Cross-tree ambient dependency | `@Environment(Model.self)` | `@EnvironmentObject` | Reserve for true app/scene-wide context |

- **Layout & Component Checklist**:
  1. Write the state ownership table before picking wrappers.
  2. Prefer `ViewThatFits`, `containerRelativeFrame`, or `.onGeometryChange` over raw `GeometryReader` to avoid layout thrash.
  3. Cover narrow width, wide/split width, Accessibility XXXL Dynamic Type, and Dark Mode in `#Preview`, keeping side effects in action/model seams.

---

## Card 5: `knowledge-vault/concepts/apple-design-and-game-feel.md`
- **Source Lineage**: `claude-code-apple-skills/skills/design/game-feel`, `design/animation-patterns`, `design/liquid-glass`.
- **Essence**: Native Apple feel comes from feedback timing and restraint—touch down, state transition, haptic pulse, and material hierarchy must express one coherent physical event.
- **Pain-Point FAQs**:
  - *When should feedback be 0ms instant?* High-frequency or chained actions where decorative motion blocks input or communicates no spatial continuity.
  - *Why do springs snap on gesture interruption?* Starting a fresh static curve discards gesture velocity; hand off live value/velocity into interactive spring APIs.
  - *When does Liquid Glass look muddy?* Nested glass-on-glass layers, poor background contrast, or applying `.glassEffect` on dense content surfaces instead of floating navigation/toolbar chrome.
- **Immediate Checklist**:
  1. Define feedback intent and completion beat before choosing duration, spring bounce, or sensory haptic feedback.
  2. Write state-transition tests for gesture interruption, cancellation, and rapid re-triggering.
  3. Gate non-essential motion with `@Environment(\.accessibilityReduceMotion)` and verify HIG contrast + VoiceOver readability.
