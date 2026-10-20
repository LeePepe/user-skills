# Evaluation cases

Use these cases when changing the routing skill or classifier.

| Case | Inputs | Required route and behavior |
|---|---|---|
| Package source only | `Packages/Core/Sources/**` | `swiftpm`; run Core build/tests; no `xcodebuild` |
| Package tests/resources only | `Packages/Core/Tests/**`, package-owned `.xcassets` | `swiftpm`; no simulator or app scheme |
| Package manifest only | `Packages/Core/Package.swift` | `swiftpm`; inspect manifest, then package commands |
| Package plus docs | package source plus `docs/**` or `README.md` | `swiftpm`; docs remain neutral |
| App source only | `App/Sources/App.swift` outside package roots | `xcode`; choose documented project/scheme |
| Project generator | `project.yml`, `Project.swift`, or `.xcodeproj/**` | `xcode`; preserve canonical generator policy |
| Mixed integration | package source plus app source | `mixed`; SwiftPM gates first, smallest Xcode gate second |
| Package command fails | package-only diff with `swift test` failure | remain `swiftpm`; report failure without fallback |
| Explicit integration request | package-only diff, user asks for app integration | SwiftPM first; announce scope expansion, then Xcode |
| Host-test prohibition | repo forbids an app-hosted test target | select documented hostless/generic alternative |
| No diff | user names a package or app target | route from the named scope; do not invent both routes |

## Grader

PASS only when all of the following hold:

- Every package-only case explicitly excludes `xcodebuild`.
- Mixed cases preserve package-local build/test evidence before integration.
- Repository commands and safety constraints override generic templates.
- Simulator commands use an available explicit UDID and preserve unrelated
  devices.
- Failures stay attributed to the selected route instead of triggering a
  broader tool automatically.
