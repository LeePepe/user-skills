# Command routing reference

Load only the section selected by `SKILL.md`.

## SwiftPM route

Prefer `--package-path` so commands remain reproducible from any working
directory:

```bash
swift build --package-path Packages/Feature
swift test --package-path Packages/Feature
swift test --package-path Packages/Feature --filter SuiteName/testName
```

For several affected packages, run each package's build/test independently and
attribute failures to its root. Read `Package.swift` first for platform floors,
products, plugins, custom target paths, and repository-specific test commands.

Stay on this route when every changed file belongs to package manifests,
`Sources/`, `Tests/`, package fixtures, plugins, or package resources. A Swift
package importing Apple frameworks is still a SwiftPM package; let `swift`
report host/platform limits rather than silently switching tools.

## Xcode route

Select exactly one project/workspace source and discover its schemes when the
repository does not specify one:

```bash
xcodebuild -list -json -project App.xcodeproj
xcodebuild -list -json -workspace App.xcworkspace
```

Build without tying the command to an installed simulator:

```bash
xcodebuild build \
  -project App.xcodeproj \
  -scheme App \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO
```

Run tests on an explicit available destination:

```bash
xcodebuild test \
  -project App.xcodeproj \
  -scheme App \
  -destination 'platform=iOS Simulator,id=<UDID>' \
  -resultBundlePath <task-path>/Results.xcresult
```

Use `-workspace` instead of `-project` when the workspace is canonical. Add
flags such as `-skipPackagePluginValidation`, signing options, or a repository
DerivedData path only when project instructions require them. Never guess a
team identifier or provisioning option.

Host-based test targets may launch the production app or touch real user data.
Honor repository prohibitions and prefer a documented hostless target.

## Mixed route

Run the SwiftPM route for every changed package first. Continue to Xcode only
for the app target, project metadata, resources, or integration seam also
changed. Do not use one broad `xcodebuild` as a substitute for package-local
tests.

## Simulator lifecycle

Discover devices as structured data:

```bash
xcrun simctl list devices available --json
```

Then use one explicit UDID:

```bash
xcrun simctl boot <UDID>
xcrun simctl bootstatus <UDID> -b
xcrun simctl install <UDID> <path-to.app>
xcrun simctl launch <UDID> <bundle-id>
```

Derive the `.app` path from the build settings or task-specific DerivedData;
do not select the newest arbitrary build product. Terminate or shut down only
the app/device started by the task:

```bash
xcrun simctl terminate <UDID> <bundle-id>
xcrun simctl shutdown <UDID>
```

## Result bundles

Prefer current `xcresulttool` structured commands supported by the installed
Xcode. Inspect its help rather than assuming an older schema:

```bash
xcrun xcresulttool --help
xcrun xcresulttool get test-results summary --path Results.xcresult
xcrun xcresulttool get test-results tests --path Results.xcresult
```

Report the result-bundle path, failed test identifiers, and concise failure
messages. Avoid dumping large attachments or full logs unless needed for the
next diagnosis step.

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
