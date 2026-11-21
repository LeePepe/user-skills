---
name: xcode-cli
description: "Use when editing .swift, .entitlements, .xcodeproj or Package.swift; running swift build, swift test or xcodebuild; triaging Apple pre-push verify gates; or inspecting simulator/xcresult output. Classify scope first: SwiftPM for packages, Xcode for app integration. Not for Xcode GUI automation."
---

# Xcode CLI

Use the narrowest command-line layer that owns the changed files.

> **Dual-Entry Build Drawer & Anti-Explosion Contract ([build-cases.json](references/build-cases.json) & [eval-cases.json](references/eval-cases.json)):**
> `classify_changes.py` slices the indexed Apple CLI build/test/simulator/concurrency drawer (`XC-001..004`) by active `route` (`swiftpm | xcode | mixed`) and returns at most `Top-3` `matchedBuildCases`. Run `python3 <skill-dir>/scripts/classify_changes.py --flow-map` to inspect drawer inventory and reference line budgets (`<= 160` lines), or `--register-case` / `skill_contract.py add-case --skill xcode-cli --id auto` to promote new build/concurrency lessons without bloating `SKILL.md`. See [eval-cases.md](references/eval-cases.md) and [eval-cases.json](references/eval-cases.json) for routing grading and automated regression assertions.

## Workflow

1. Read the repository's `AGENTS.md`, constitution, package context, and build
   instructions before choosing commands. Repository rules override templates
   in this skill.
2. Inspect the requested scope and changed paths. Run:

   ```bash
   python3 <skill-dir>/scripts/classify_changes.py --repo <repo> --base <base>
   ```

   Without `--base`, staged/unstaged/untracked files define the scope; a clean
   tree automatically compares the `origin/main` (fallback `main`) merge-base
   to `HEAD`. Use explicit refs/ranges to override; `--committed-only` excludes
   local edits. Confirm roots against `Package.swift`, including custom targets.
   `--worktree` / `--repo-dir` / `--cwd`, `--base-ref`, `--head-ref`,
   `--diff-range`, and `--json` are compatible aliases/options.
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
- Before Apple pre-push gate triage, run `classify_changes.py --repo <repo>
  --check-verify-gate`. Standard classification also includes `verifyGateHealth`.
  Report blocking unconditional `xcodebuild test`, incremental layer selection,
  and DerivedData/build-lock indicators. This is advisory static inspection:
  never run hooks or weaken gates automatically. Propose fast local SwiftPM
  checks plus heavy app tests in CI or explicit `RUN_XCODEBUILD=1` / `RUN_HEAVY=1`;
  gate changes require repository-owner approval.

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Writer | `references/build-cases.json` | `python3 skills/xcode-cli/scripts/classify_changes.py --repo <repo>` |
| Role: Reviewer | `references/command-routing.md` | `python3 skills/xcode-cli/scripts/classify_changes.py --repo <repo> --check-verify-gate` |

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| `.swift`, `.entitlements`, `.xcodeproj`, `Package.swift`; `swift build`, `swift test`, `xcodebuild` | `references/build-cases.json` | `python3 skills/xcode-cli/scripts/classify_changes.py --repo <repo>` |
| Apple pre-push verify gate / slow hook triage | `references/command-routing.md` | `python3 skills/xcode-cli/scripts/classify_changes.py --repo <repo> --check-verify-gate` |
| classifier regression verification | `references/eval-cases.json` | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill xcode-cli --strict` |
