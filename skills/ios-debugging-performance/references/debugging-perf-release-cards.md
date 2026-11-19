# Debugging, Performance & Release Gate Cards (`TIA-3` Cards 6–9)

Distilled reference drawer for [`SKILL.md`](../SKILL.md). Every rule maps 1-to-1 to Cards 6–9 in `knowledge-vault/concepts/`.

---

## Card 6: `knowledge-vault/concepts/ios-debugger-review-swarm.md`
- **Source Lineage**: `dimillian-skills/ios-debugger-agent`, `bug-hunt-swarm`, `review-swarm`.
- **Essence**: Expert debugging separates reproduction, code path tracing, regression origin, and minimal proof experiments so another engineer can deterministically replay every conclusion.
- **Pain-Point FAQs**:
  - *Why does editing code immediately make bugs worse?* Without locking down trigger conditions (device, OS, input, expected vs actual) and regression boundaries, patches mask symptoms and introduce secondary regressions.
  - *How do parallel review swarms avoid noisy opinion dumps?* Assign one orthogonal axis per reviewer—(1) behavioral regression, (2) reliability/performance, (3) security/privacy, (4) test contract—and keep only findings backed by `file:line` and a proof path.
  - *What is a minimal proof experiment?* A fast binary check (e.g., freezing view identity, bypassing cache, or injecting a deterministic clock) that falsifies one of two competing root-cause hypotheses.
- **Immediate Checklist**:
  1. Record minimal reproduction steps + environment matrix before touching production code; eliminate silent `try?` swallowing on state/persistence paths.
  2. Run diff review, bug-hunt, and test-gap passes along independent axes.
  3. Ship a regression test that fails on the pre-fix code alongside the fastest local verification command.

---

## Card 7: `knowledge-vault/concepts/instruments-performance-and-swiftui-debugging.md`
- **Source Lineage**: `claude-code-apple-skills/skills/performance/profiling`, `performance/swiftui-debugging`, `dimillian-skills/swiftui-performance-audit`.
- **Essence**: Decompose frame drops and hangs into five measurable buckets—**CPU**, **Memory/Allocations**, **Layout**, **I/O**, and **SwiftUI View-Body Update Frequency**—before changing APIs.
- **Pain-Point FAQs**:
  - *Why can adding a cache make performance worse?* Unbounded caches (`NSCache` without `countLimit`/`totalCostLimit` or lock-contended dictionaries) raise memory pressure, eviction churn, and lock contention; always measure hit rate, allocations, and main-thread time together.
  - *How to diagnose excessive SwiftUI updates?* Inspect `Self._printChanges()` / SwiftUI Instruments template for view-body evaluations, unstable identity, and broad observation granularity, then cross-check Time Profiler and Allocations.
  - *When should you NOT optimize?* When the hotspot is off the user-critical path or complexity outweighs measurable frame-time/memory gains against target thresholds.
- **Immediate Checklist**:
  1. Record cold-start, scroll hitch, interaction latency, and peak memory baselines first.
  2. Modify exactly one variable per experiment and archive before/after trace numbers.
  3. Encode numeric regression budgets (frame budget `<= 16.6ms` / `8.3ms` ProMotion, peak memory ceiling) into automated checks.

---

## Card 8: `knowledge-vault/concepts/macos-swiftpm-menubar-packaging.md`
- **Source Lineage**: `dimillian-skills/macos-spm-app-packaging`, `macos-menubar-tuist-app`.
- **Essence**: Shipping a macOS MenuBar `.app` without `.xcodeproj` requires a deterministic pipeline: `swift build` -> `.app` bundle structure & resource copy -> `Info.plist` (`LSUIElement`) -> Developer ID signing + notarization + stapling -> launch smoke test.
- **Pain-Point FAQs**:
  - *Why does `swift run` work locally while double-clicking `.app` crashes?* Missing `Bundle.module` / `Contents/Resources` copy, missing `Info.plist`, single-arch mismatch, or code assuming `FileManager.default.currentDirectoryPath` points to the git repo.
  - *What do MenuBar apps miss most often?* `LSUIElement = true` (causing stray Dock icons), status-bar item retention lifecycle, menu keyboard/quit handling, and clean `Store / Client / View` separation.
  - *When should signing and notarization run?* Inside the same repeatable packaging script right after bundle assembly—never deferred until release day.
- **Immediate Checklist**:
  1. Automate `swift build -c release` -> bundle assembly -> non-repo-directory launch smoke test in one script.
  2. Verify `CFBundleIdentifier`, `CFBundleShortVersionString`, `LSUIElement`, copied resource bundles, and `lipo -archs`.
  3. Inject signing identity / notarization profile via environment variables so CI and local builds run identical gates.

---

## Card 9: `knowledge-vault/concepts/app-store-release-security-rejection-gates.md`
- **Source Lineage**: `claude-code-apple-skills/skills/app-store/rejection-handler`, `release-review`, `security/privacy-manifests`.
- **Essence**: App Store rejections rarely come from a syntax bug; they come from mismatches across runtime behavior, `NS*UsageDescription` prompts, `PrivacyInfo.xcprivacy`, account lifecycle flows, and App Store Connect metadata.
- **Pain-Point FAQs**:
  - *Why do working features still get rejected?* Vague permission strings ("Need camera access"), undeclared Required Reason APIs (`UserDefaults`, file timestamps, disk space, boot time), missing in-app account deletion when sign-up exists (Guideline 5.1.1(v)), or screenshot/feature drift.
  - *What privacy boundaries must align?* Every first-party & third-party SDK `PrivacyInfo.xcprivacy` Required Reason API, tracking domain, permission trigger timing, and live HTTPS privacy policy URL.
  - *How to make release review repeatable?* Audit the exported Release `.xcarchive` / `.ipa` rather than Debug simulator builds.
- **Immediate Checklist**:
  1. Run release review strictly against the final Release archive/IPA.
  2. Cross-check `Info.plist` permission strings, `PrivacyInfo.xcprivacy` categories, account deletion flow, and store screenshots.
  3. Log every rejection risk with artifact evidence and owner sign-off before upload.
