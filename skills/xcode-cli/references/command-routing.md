# Command routing reference

Load only the section selected by `SKILL.md`.

## Classifier options and verify-gate triage

Run the classifier before choosing a build; output is always JSON (`--json` is a no-op).

| Scope | Command/options |
|---|---|
| Local edits; clean committed branch auto-base | `classify_changes.py --repo <repo>` |
| Repository aliases or single directory with `.git` | `--worktree <repo>`, `--repo-dir <repo>`, `--cwd <repo>`, or `<repo>` |
| Merge-base to selected head, plus local edits | `--base <ref> --head <ref>` (aliases `--base-ref`, `--head-ref`) |
| Snapshot / merge-base range, plus local edits | `--range <a..b>` / `--diff-range <a...b>`; do not combine with base/head |
| Only committed changes, even with dirty working tree | `--committed-only`; auto-base still applies |
| Gate inspection without builds or diff discovery | `--repo <repo> --check-verify-gate` |

Explicit repo-relative paths override git discovery. A clean tree uses the first available
`origin/main` or `main` merge-base; no available shared base means no detected branch diff.
A dirty tree without explicit refs stays scoped to local edits. `--head` without `--base`
uses the same auto-base candidates. Invalid syntax, repositories, or refs exit 2 with
three-line fix guidance and pointers to this reference and `build-cases.json`.

`verifyGateHealth` is included in standard JSON and returned alone by gate-check mode.
It inspects `scripts/verify`, `.githooks/pre-push`, and `scripts/hooks/pre-push` without
executing them. The four indicators are:

- `has_verify_script`: the canonical local verify entry exists.
- `prepush_blocks_on_unconditional_xcodebuild`: a literal `xcodebuild test` or
  `test-without-building` is reachable from a hook or its `scripts/verify` call without
  a `RUN_XCODEBUILD`, `RUN_HEAVY`, or `mode=ci` opt-in guard. Function calls, `if/else`,
  and simple `&&` / `||` guards are followed; unused functions/comments are not gates.
- `uses_incremental_layer_selection`: changed-file/layer selection command indicators.
- `has_derived_data_or_build_lock`: explicit DerivedData or build-lock indicators.

`tier` is `blocking_prepush_xcodebuild`, `local_fast_spm`, or `not_detected`.
These are static indicators, not proof of hook safety or lock correctness; dynamic
commands, external script chains, and complex shell control flow require manual review.
Inline, standalone, and exported `RUN_HEAVY=1` / `RUN_XCODEBUILD=1` assignments
or an unconditional `--mode ci` invocation still block pre-push.
Propose local fast SwiftPM checks and CI/explicit opt-in for heavy app tests, but do not
edit hooks or bypass repository gates without owner approval.

## SwiftPM route

Prefer `--package-path` so commands remain reproducible from any working directory:

```bash swift build --package-path Packages/Feature swift test --package-path Packages/Feature swift test --package-path Packages/Feature --filter SuiteName/testName ```

For several affected packages, run each package's build/test independently and attribute failures to its root. Read `Package.swift` first for platform floors, products, plugins, custom target paths, and repository-specific test commands.

Stay on this route when every changed file belongs to package manifests, `Sources/`, `Tests/`, package fixtures, plugins, or package resources. A Swift package importing Apple frameworks is still a SwiftPM package; let `swift` report host/platform limits rather than silently switching tools.

## Xcode route

Select exactly one project/workspace source and discover its schemes when the repository does not specify one:

```bash xcodebuild -list -json -project App.xcodeproj xcodebuild -list -json -workspace App.xcworkspace ```

Build without tying the command to an installed simulator:

```bash xcodebuild build \ -project App.xcodeproj \ -scheme App \ -destination 'generic/platform=iOS Simulator' \ CODE_SIGNING_ALLOWED=NO ```

Run tests on an explicit available destination:

```bash xcodebuild test \ -project App.xcodeproj \ -scheme App \ -destination 'platform=iOS Simulator,id=<UDID>' \ -resultBundlePath <task-path>/Results.xcresult ```

Use `-workspace` instead of `-project` when the workspace is canonical. Add flags such as `-skipPackagePluginValidation`, signing options, or a repository DerivedData path only when project instructions require them. Never guess a team identifier or provisioning option.

Host-based test targets may launch the production app or touch real user data. Honor repository prohibitions and prefer a documented hostless target.

## Mixed route

Run the SwiftPM route for every changed package first. Continue to Xcode only for the app target, project metadata, resources, or integration seam also changed. Do not use one broad `xcodebuild` as a substitute for package-local tests.

## SwiftPM executable preview hosts (`XC-005`)

Opening a bare `Package.swift` in Xcode to preview an executable target (`*PreviewHost`) fails with:
`The executable target “<Target>” needs the build setting “ENABLE_DEBUG_DYLIB” set to “YES” in order to preview.`

- Keep `#Preview` macros in library/framework targets (`*DemoKit`), not executable targets.
- Generate a native `.xcodeproj` via `project.yml` (`xcodegen`) with `ENABLE_DEBUG_DYLIB: YES` on both the `application` (`*PreviewHost`) and `framework` (`*DemoKit`) targets plus shared schemes, then open `.xcodeproj` in Xcode.
- `classify_changes.py` reports `previewHostHealth` and prioritizes `XC-005` in `matchedBuildCases` when a `Package.swift` `*PreviewHost` executable target lacks `ENABLE_DEBUG_DYLIB = YES` in `project.yml` / `*.xcodeproj/project.pbxproj` or when touching `*PreviewHost`.

## Simulator lifecycle

Discover devices as structured data:

```bash xcrun simctl list devices available --json ```

Then use one explicit UDID:

```bash xcrun simctl boot <UDID> xcrun simctl bootstatus <UDID> -b xcrun simctl install <UDID> <path-to.app> xcrun simctl launch <UDID> <bundle-id> ```

Derive the `.app` path from the build settings or task-specific DerivedData; do not select the newest arbitrary build product. Terminate or shut down only the app/device started by the task:

```bash xcrun simctl terminate <UDID> <bundle-id> xcrun simctl shutdown <UDID> ```

## Result bundles

Prefer current `xcresulttool` structured commands supported by the installed Xcode. Inspect its help rather than assuming an older schema:

```bash xcrun xcresulttool --help xcrun xcresulttool get test-results summary --path Results.xcresult xcrun xcresulttool get test-results tests --path Results.xcresult ```

Report the result-bundle path, failed test identifiers, and concise failure messages. Avoid dumping large attachments or full logs unless needed for the next diagnosis step.

## Failure handling

- Package compile/test failure: remain in the package root and report it.
- Scheme/project ambiguity: inspect `xcodebuild -list -json` and repository
docs; do not trial every scheme.
- Destination unavailable: choose from `simctl ... --json`; do not hard-code a
simulator model absent from the machine.
- Signing failure on simulator/build-only work: use documented signing bypass
flags. Device/archive signing requires the user's configured credentials.
- Dependency resolution delay: increase the command timeout while preserving
the same route and command.
