---
name: ios-debugging-performance
description: "Isolate reproducible iOS/macOS bugs with multi-role review swarms, profile SwiftUI hitches in Instruments, package signed/notarized pure-SwiftPM macOS MenuBar apps, and enforce pre-submission App Store privacy/rejection gates. Not for SwiftData schema modeling or Swift 6 Sendable design."
---

# ios-debugging-performance

Evidence-driven iOS/macOS debugging, Instruments performance engineering, pure-SwiftPM macOS `.app` packaging, and App Store pre-submission rejection gate skill distilling **4 `TIA-3` Knowledge Vault cards**.

## `/howto` P7 Global 5-Tuple

| Dimension | Contract |
|---|---|
| **Inbound** | Bug reproduction specs, Swift/macOS source files, Instruments baseline metrics, pure-SwiftPM macOS MenuBar bundle specs (`Info.plist`, `LSUIElement`, signing/notarization), or archive/IPA release-readiness manifests (`PrivacyInfo.xcprivacy`, permission purpose strings, account deletion). |
| **Internal** | `scripts/audit_debug_perf_release.py`: deterministic source & release-hygiene scanner (`audit-source`), MenuBar `.app` & App Store submission manifest verifier (`verify-bundle-spec`), 4-card catalog (`list-cards`), and 100% card-coverage checker (`check-card-coverage`). |
| **Calls** | Zero-token Layer-0 Python CLI (`audit_debug_perf_release.py`); pairs with `xcode-cli` for `swift build` / `xcodebuild` / `.xcresult` extraction and `code-review` for two-axis review. |
| **PointsTo** | Reproducible root-cause evidence chains (`file:line` + minimal proof experiment + fail-first regression test), single-variable Instruments delta reports, verified `.app` bundle layouts, and zero-surprise App Store submission checklists. |
| **Trigger & Notify** | Invoked on bug hunts, scroll/startup hitch investigations, `.xcodeproj`-less macOS MenuBar packaging, or App Store / TestFlight release readiness; exits `2` under `--strict` on blocking defects. |

## When-to-Read & Progressive Disclosure Routing

- **Layer 0 (Deterministic Gate First)**: Run `scripts/audit_debug_perf_release.py audit-source` or `verify-bundle-spec` before manual debugging or release sign-off.
- **Layer 1 (4-Card Playbooks, FAQs & Checklists)**: Read [`references/debugging-perf-release-cards.md`](references/debugging-perf-release-cards.md) for criminal-scene bug isolation, 5-bucket Instruments profiling, pure-SwiftPM `.app` assembly/notarization, and App Store privacy/rejection gates.
- **Layer 2 (Regression & Trigger Drawer)**: Inspect [`references/eval-cases.json`](references/eval-cases.json) when adding regression cases or running `skill_contract.py`.
- **Sibling Routing**:
  - Swift 6 `Sendable` / actor isolation, task cancellation ownership, SwiftUI `@Observable` state matrices, or spring/Liquid Glass feel -> `ios-concurrency-swiftui`.
  - State-driven navigation/deep links, SwiftData `VersionedSchema` migrations, characterization/snapshot test contracts, CloudKit/Push seams, or Apple Intelligence fallbacks -> `ios-architecture-testing`.

## Complete 4-Card Coverage Matrix (`TIA-3`)

| # | Knowledge Vault Card (`knowledge-vault/concepts/`) | Core Engineering Contract | Deterministic Script Rules |
|---|---|---|---|
| 6 | `ios-debugger-review-swarm.md` | Separate minimal repro + environment matrix, call-chain logs, minimal proof experiment, single-axis review swarm (`behavior`, `reliability/perf`, `security/privacy`, `test contract`), fail-first regression test | `DP-001` (`SILENT_TRY_OPTIONAL_SWALLOW`), `DP-002` (`FORCE_TRY_OR_FATAL_CRASH_PATH`) |
| 7 | `instruments-performance-and-swiftui-debugging.md` | Decompose hitches into 5 buckets (CPU, Allocations, Layout, I/O, View-Body update frequency), record baseline before single-variable change, enforce numeric regression thresholds | `DP-003` (`UNBOUNDED_CACHE_WITHOUT_COST_LIMIT`), `DP-004` (`MAIN_THREAD_SYNC_IMAGE_OR_DATA_IO`) |
| 8 | `macos-swiftpm-menubar-packaging.md` | Repeatable `swift build` -> `.app` bundle assembly -> `Info.plist` / `LSUIElement` / resource & arch check -> Developer ID `codesign` + notarization + stapling -> launch smoke test | `DP-005` (`CWD_ASSUMPTION_IN_MACOS_BUNDLE`), `DP-006` (`MENUBAR_BUNDLE_SPEC_INCOMPLETE`) |
| 9 | `app-store-release-security-rejection-gates.md` | Audit final archive/IPA (never Debug): permission purpose strings, Required Reason API `PrivacyInfo.xcprivacy`, in-app account deletion when account creation exists, store metadata parity | `DP-007` (`REQUIRED_REASON_API_WITHOUT_MANIFEST`), `DP-008` (`RELEASE_REJECTION_GATE_FAILED`) |

## Core Engineering Guardrails

1. **Criminal-Scene Bug Isolation**: Never patch by hunch. Lock down device/OS/input repro steps, run a minimal proof experiment that separates competing hypotheses, and keep a fail-first regression test.
2. **Profile Before Optimizing**: Record baseline launch, scroll, interaction, and peak memory metrics in Instruments (Time Profiler, Allocations, SwiftUI template). Change one variable at a time and verify cache hit rate vs memory pressure.
3. **Scripted SwiftPM `.app` Assembly**: Never hand-assemble macOS `.app` bundles in Finder. Validate `Contents/MacOS`, `Contents/Resources`, `Info.plist` (`LSUIElement`), universal/arm64 slices, `codesign --verify --deep --strict`, notarization staple, and non-repo-cwd launch smoke tests.
4. **End-to-End Archive Release Review**: Inspect the signed Release archive/IPA for `NS*UsageDescription` completeness, `PrivacyInfo.xcprivacy` Required Reason API declarations, account deletion parity, and live privacy policy URLs.

## CLI Quick Reference

```bash
# List all 4 mapped TIA-3 cards and deterministic rule IDs
python3 skills/ios-debugging-performance/scripts/audit_debug_perf_release.py list-cards

# Verify 100% 4-card coverage across SKILL.md, references/, and script rules
python3 skills/ios-debugging-performance/scripts/audit_debug_perf_release.py check-card-coverage

# Audit Swift source for swallowed errors, main-thread sync I/O, cwd assumptions, or undeclared Required Reason APIs
python3 skills/ios-debugging-performance/scripts/audit_debug_perf_release.py audit-source --snippet 'let _ = try? save()' --strict

# Verify pure-SwiftPM macOS MenuBar bundle + App Store release-readiness spec JSON
python3 skills/ios-debugging-performance/scripts/audit_debug_perf_release.py verify-bundle-spec --spec-json '{"mode":"app_store","build_configuration":"Release","privacy_manifest_present":true,"required_reason_apis":["NSPrivacyAccessedAPICategoryUserDefaults"],"declared_reason_apis":["NSPrivacyAccessedAPICategoryUserDefaults"],"permissions_used":["NSCameraUsageDescription"],"info_plist":{"NSCameraUsageDescription":"Scan QR codes to pair your device."},"supports_account_creation":true,"supports_account_deletion":true,"privacy_policy_url":"https://example.com/privacy"}' --strict
```
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/debugging-perf-release-cards.md` | `python3 skills/ios-debugging-performance/scripts/audit_debug_perf_release.py --help` |
| verification | `references/debugging-perf-release-cards.md` | `skill_contract.py eval --skill ios-debugging-performance --strict` |
