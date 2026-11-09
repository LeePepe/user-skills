---
name: xcode-cli
description: Route Apple-platform builds, tests, simulator runs, and result inspection through Swift and Xcode command-line tools. Use when Codex needs to build or test a Swift package, Xcode project, or workspace; diagnose Apple build failures; run an iOS simulator; choose between swift build/test and xcodebuild; or parse xcresult output. Package-only changes stay on SwiftPM commands and escalate to xcodebuild only when the diff includes app/Xcode integration work or the user explicitly requests integration verification. Not for Xcode MCP or GUI automation.
---

# Xcode CLI

Use the narrowest command-line layer that owns the changed files.

## Workflow

1. Read the repository's `AGENTS.md`, constitution, package context, and build
   instructions before choosing commands. Repository rules override templates
   in this skill.
2. Inspect the requested scope and changed paths. Run:

   ```bash
   python3 <skill-dir>/scripts/classify_changes.py --repo <repo> --base <base>
   ```

   Omit `--base` for uncommitted changes. Confirm every reported package root
   against its `Package.swift`, especially repositories combining a root
   package with app targets.
3. Follow exactly one route:
   - `swiftpm`: run `swift build` and `swift test` for each affected package.
     This route is a hard scope gate: keep `xcodebuild`, XcodeGen, simulators,
     signing, and app schemes out of the verification.
   - `xcode`: use the repository's project/workspace, scheme, and destination
     with `xcodebuild`.
   - `mixed`: verify each affected package with SwiftPM first, then run the
     smallest Xcode integration build/test required by the non-package files.
   - `none`: discover the intended target from the request; do not invent a
     build.
4. Read [command-routing.md](references/command-routing.md) only for the chosen
   route, simulator lifecycle, signing, or result-bundle commands.
5. Execute with bounded output and a timeout appropriate for dependency
   resolution. Preserve the first actionable compiler/test failure and the
   exact command.
6. Report the selected route, affected package roots or Xcode target, commands,
   and results. State explicitly when a package-only route intentionally did
   not run `xcodebuild`.

## Routing invariants

- Treat source, tests, fixtures, resources, and manifests owned by one or more
  Swift packages as `swiftpm` when all changed files remain inside those
  package roots. Package-owned asset catalogs, storyboards, and model resources
  remain on this route.
- Treat app targets, project/workspace metadata, schemes, build settings,
  entitlements, XcodeGen/Tuist definitions, non-package resources, and
  non-package platform entry points as Xcode integration.
- Treat documentation, agent instructions, and CI metadata as neutral. They do
  not expand a package-only route into `mixed`.
- Treat a package failure as a package failure. Report an unsupported platform
  or manifest limitation instead of silently substituting `xcodebuild`.
- Escalate a package-only task only when the user explicitly requests an app
  integration check or the repository's authoritative instructions require
  one. Call out that expansion before running it.
- Use command-line tools only: `swift`, `xcodebuild`, `xcrun simctl`, and
  `xcrun xcresulttool`. Keep Xcode GUI and MCP outside this workflow.

## Safety

- Prefer a generic simulator destination for build-only checks; choose a named
  or explicit simulator only for tests or runs that need one.
- Reuse a repository-provided DerivedData policy. Otherwise use a task-specific
  path; never delete broad user DerivedData directories.
- Preserve user simulators. Boot or create only the requested/dedicated device,
  and shut down only devices started by this task.
- Keep generated project files derived from their canonical XcodeGen/Tuist
  source. Follow repository rules before regenerating them.
- Respect hook-driven verification. Do not manually repeat suites that the
  repository explicitly delegates to commit or push hooks.
