# Agent-Consumable Design Document Specification (`agent-design-doc-spec.md`)

> **Version**: `1.0.0` (`agent-design-doc/v1`)
> **Authority Alignment**: `~/Development/shared-design-system/ai/DESIGN-CONTRACT.md` · `docs/strata-theme-v0.2.md` · `~/Development/demoUI/design/north-star.md` · `goal-task` (Phase 0 `Explorer -> Designer` & Phase 1 `Writer -> CI Gate -> Reviewer`)
> **Target Consumers**: `Explorer` (read-only fact producer) · `Designer` (`handoff/<goal>/design.md` author) · `Writer` (code & snapshot implementer) · `shared-ci/scripts/context/design_contract.py` (static/visual linter) · `Reviewer` (cross-vendor V1–V5 verifier)

---

## 1. Design Philosophy: Why Prose PRDs Fail Coding Agents

Coding agents are autoregressive token predictors without native visual perception during code generation. Unstructured prose design briefs trigger **six deterministic failure modes**:

| # | Failure Mode | Root Cause in Prose Docs | Structural Cure in `agent-design-doc/v1` |
|---|---|---|---|
| 1 | **Context Rot & Middle Forgetting** | Constraints buried in narrative paragraphs (`>30k` context decay) | Top YAML machine header + fixed-order deterministic Markdown tables |
| 2 | **AI Slop Regression** | Vague adjectives ("modern, clean, sleek") collapse to pretraining averages (purple gradients, nested cards, eyebrow labels, Glass-on-Glass, double toolbar capsules) | Locked **Visual Mode** enum + explicit **Scenario → Primitive Matrix** + **Anti-Pattern Ban List** |
| 3 | **Happy-Path Bias** | Only `populated` state described; inactive bottom bars hidden with `.opacity(0)` leaving dead space | Mandatory **4-State Matrix** (`populated/empty/loading/error`) + **Zero-Height Inactive Slot Rule** |
| 4 | **Light/Dark Flip Collapse** | Pairing surface text (`primaryText`) with solid accent fill (`interactiveAccent`) or inverting shadows in Dark mode | Mandatory **Semantic Token & Dual-Appearance Contrast Table** (`light` × `dark` ≥ 4.5:1 AA) |
| 5 | **Numeric Ellipsis (`12…`) & Title Squeeze** | Idealized short strings (`"42%"`) used instead of worst-case production fixtures (`"16.81 / 24.00 GB"`) | **Top-Down Width Arithmetic Formula** + **3-Tier Text Budget Table** + worst-case fixture strings |
| 6 | **Fixed-Width Host Clipping** | Writing `.frame(width: W).padding(P)` inside `NSHostingView`/`NSPopover`, expanding intrinsic width to `W + 2P` | Mandatory `host_geometry.modifier_order: ".padding(P).frame(width: W)"` + `sips -g pixelWidth == W * scale` gate |

---

## 2. Three-Stage Pipeline Handoff Contract (`Explorer -> Designer -> Writer -> Reviewer`)

```
[Stage 1: Explorer]  ──(receipts/explore-<task>.md)──►  [Stage 2: Designer]
  Read-only repo scan:                                    Writes handoff/<goal>/design.md ONLY.
  • Package.resolved / package.json SHA                   NEVER reads business logic or writes demo code.
  • Upstream Strata/Basalt primitive parity               Produces YAML frontmatter + 7 deterministic sections.
  • Host container geometry (W, safe areas)               If >15 lines local glue or new variant needed:
  • Worst-case data fixtures & .xcstrings keys            MUST halt & split upstream shared-design-system Task.
                                                               │
                                                               ▼ (handoff/<goal>/design.md)
[Stage 4: CI Gate & Cross-Vendor Reviewer]  ◄──────────  [Stage 3: Writer]
  • design_contract.py (zero raw hex/RGB, sections)       Consumes frozen design.md in isolated worktree.
  • sips -g pixelWidth == W * scale                       Implements only Owned Paths using semantic tokens.
  • 8-state/appearance + max-density snapshots            Exports Light/Dark × 4-state + max-density snapshots.
  • V2 P1 block on any numeric '…' or unequal card height Completes /35 self-score & 8-cell boolean receipt.
```

### 2.1 Stage 1 Input Prerequisite (`Explorer -> Designer` Snapshot Schema)
Before `Designer` is dispatched, `Explorer` MUST provide four factual tables in `receipts/explore-<task>.md` (with `file:line` citations):
1. **Upstream Pin & Primitive Parity**: Target repo's exact `shared-design-system` / `DesignTokens` / `@nocoo/basalt` revision, and whether each needed component is `AVAILABLE`, `UNRELEASED_IN_CHECKOUT`, or `MISSING_NEEDS_UPSTREAM_TASK`.
2. **Host Container Geometry**: Exact host type (`NSPopover`, `NSHostingView`, `NavigationSplitView`, iOS `NavigationStack`, Web container), fixed width `W` or breakpoint range, and system chrome insets.
3. **Worst-Case Data Fixtures**: Maximum character lengths for CJK/EN strings, maximum numeric precision (e.g., `16.81 / 24.00 GB`, `999.9 kg × 99`), and existing `.xcstrings` / i18n key namespaces.
4. **Incumbent Debt & Test Baseline**: Presence/absence of `docs/design-contract.md`, raw color leaks, local custom components (`>15` lines), and existing UI tests that assert legacy layout geometry.

---

## 3. Normative Template: `handoff/<goal>/design.md` (Copy-Pasteable Designer Output)

Every `Designer` subagent MUST output `~/Development/handoff/<goal>/design.md` adhering strictly to the following structure. Prose paragraphs outside table cells are forbidden except for 1-sentence rationale notes.

```markdown
---
schema: agent-design-doc/v1
goal: <goal-id>
task: <task-id>
contract_rev: goal-task@<sha>
upstream_authority:
  design_contract: ~/Development/shared-design-system/ai/DESIGN-CONTRACT.md
  theme_spec: ~/Development/shared-design-system/docs/strata-theme-v0.2.md
  upstream_pin_repo: ~/Development/shared-design-system
  upstream_pin_sha: <40-char-sha>
  token_version: DesignTokens@0.2.0
  web_package: "@nocoo/basalt@2.1.8" # omit if native-only
visual_mode: Strata-Core | Strata-Glass | Independent-Glass | Independent-Custom
surface_mode: Operate | Read | Persuade | Experience
brand_seed: strata-blue | strata-green | strata-pink | strata-yellow | strata-purple | ocean | graphite
macro_tiers:
  - Compact-Portrait   # iOS portrait 375–430pt / mobile web <640px
  - Wide-Touch         # iOS landscape / iPad split / tablet 640–1024px
  - Desktop-Pointer    # macOS window / popover / desktop web >1024px
host_geometry:
  platform: macOS | iOS | Web | Multiplatform
  container_type: NSPopover | NSHostingView | UIHostingController | NavigationStack | WebViewport
  sizing_mode: fixed_width | fluid_breakpoints
  fixed_width_pt: 380                           # required if sizing_mode == fixed_width
  outer_padding_pt: 16
  modifier_order: ".padding(16).frame(width: 380)" # MUST apply padding BEFORE fixed frame
  scale_factors: [1, 2]
  expected_pixel_widths: [380, 760]             # verified via `sips -g pixelWidth`
upstream_escalation:
  needed: false                                 # true if >15 lines local glue or new reusable primitive needed
  upstream_task_id: null                        # required if needed == true; blocks downstream Writer
  max_local_glue_lines: 15
---

# [Screen / Feature Name] — Agent Design Specification

## 1. Intent, Visual Mode & Scenario-to-Primitive Mapping

- **Primary User Job (1 sentence)**: <What the user accomplishes in <3 seconds on this surface>
- **Single Focal Anchor**: <The single highest-weight element in the viewport; max 1 Primary CTA>
- **Surface Spacing Class**: `Class A (High-Density Telemetry: 4–6pt row gap, 2–4pt cell pad)` | `Class B (Standard Productivity: 8–12pt row gap, 8–12pt card pad)` | `Class C (Editorial/Hero: 16–32pt gap)`

| Sub-Surface / Region | DESIGN-CONTRACT Scenario Archetype | Upstream Component / Primitive (`import Strata` / `@nocoo/basalt`) | Explicit Variant & Props | StrataMotion Choreography | Forbidden Anti-Patterns (Hard Bans) |
|---|---|---|---|---|---|
| Navigation / Header | System Toolbar / Sticky Section | `StrataSectionHeader` / `StrataStickySectionHeader` | `maxHeight: 44pt`, vertical pad `<=4pt` | `fade` on Reduce Motion | Never wrap `.toolbar` item in custom `.background(..., in: Capsule())` (Rule 1 Double-Chrome) |
| Primary Content Grid | Live Telemetry / Bento Summary | `StrataCard` + `StrataProgressRing` + `StrataStatusMetricBadge` | `style: .solid`, `cornerRadius: 16pt`, equal-height row lock | `snappy` (`0.28s, bounce 0.08`) + `.contentTransition(.numericText())` | No Glass-on-Glass; no nested card-in-card; no >1px colored left borders |
| Data Entry / List | Dense Single-Hand Entry / Expandable Rows | `StrataList` / `StrataTextField` / `StrataSwitch` | `.strataInputBaseline` alignment, `44×44pt` hit target (`28×28pt` macOS) | Spring (`response: 0.30, damping: 0.84`), zero layout jump | Never repeat full-width recommendation banners per row (use inline placeholder/hint) |
| Bottom Chrome | Bottom Inset Arbitration | Single `.safeAreaInset(edge: .bottom)` stack | Order: `Content -> Snackbar -> DockedBar -> SafeArea` | Slide + crossfade (`150ms`) | Never stack multiple `.overlay(alignment: .bottom)`; never hide inactive slot with `.opacity(0)` (Rule 6 & 12) |

---

## 2. Deterministic Layout & Component Tree (Pseudo-AST)

> Writer MUST translate this tree 1:1 into view hierarchy without inventing intermediate wrapper cards or reversing modifier order.

```text
RootHostContainer [modifier_order: .padding(16).frame(width: 380), bg: theme.canvas]
├── TopHeaderRow [HStack(alignment: .firstTextBaseline, spacing: 8), maxHeight: 36pt]
│   ├── LeadingIcon [SF Symbol / Lucide, frame(width: 20), foreground: theme.secondaryForeground]
│   ├── TitleLabel [Tier 3 Dynamic Title, .headline, ViewThatFits 1-line -> 2-line]
│   ├── Spacer(minLength: 8)
│   └── TrailingAction [StrataButton(variant: .toolbar, size: .sm)]
├── MainBentoRow [HStack(alignment: .top, spacing: 10), equalHeight: true (delta <= 1px)]
│   ├── LeftMetricCard [StrataCard(theme: theme, style: .solid), width: 169pt, innerPadding: 12pt, radius: 16pt]
│   │   ├── SchemaHeader [Tier 1 Label, CJK <=4 / EN <=6 chars, .caption, theme.secondaryForeground]
│   │   └── PrimaryReadout [Tier 2 Metric, .title3.monospacedDigit(), .layoutPriority(1), theme.foreground]
│   └── RightMetricCard [StrataCard(theme: theme, style: .solid), width: 169pt, innerPadding: 12pt, radius: 16pt]
└── BottomSafeAreaStack [.safeAreaInset(edge: .bottom, spacing: 0)]
    ├── ConditionalSnackbar [if state.hasBanner -> StrataBanner; else EmptyView() (height: 0)]
    └── PrimaryDockedBar [if Compact-Portrait -> StrataDockedBar; hidden on Desktop-Pointer]
```

### 2.1 Cross-Platform Macro-Tier Adaptation Matrix
| Macro Tier | Viewport / Host Trigger | Navigation & CTA Topology | Grid & Density Adaptation | Hit Target & Input Rules |
|---|---|---|---|---|
| **Compact-Portrait** | iOS Portrait (`375–430pt`) / Mobile Web (`<640px`) | Bottom thumb-zone `DockedBar` or TabBar (`<=5` items); modal Done in `.topBarTrailing` | Single column or 2-up Bento (`169pt` cards); sticky header `<=44pt` | `>=44×44pt` touch target; custom keypad keys use `4–8pt` (`compactCellRadius`), never `16pt` |
| **Wide-Touch** | iOS Landscape / iPad (`640–1024px`) | Split view or top toolbar; **never stretch bottom CTA full-width** (cap `<=400pt`) | 2–3 column adaptive grid; preserve `45–75ch` reading measure | `>=44×44pt` touch target; support Dynamic Type `200%` (`HStack -> VStack` reflow) |
| **Desktop-Pointer** | macOS (`NSPopover 380pt` / `NavigationSplitView`) / Web (`>1024px`) | Top toolbar (`<=3` groups) or bottom-right form action; **no bottom floating bar** | High-density table/rail; `24–28pt` row height; hover states + `⌘K` / `Esc` shortcuts | `>=28×28pt` (`20×20pt` min) pointer target; `:focus-visible` ring (`>=3:1`) |

---

## 3. Container-Driven Width Budget & 3-Tier Text Rules (Rule 13)

### 3.1 Top-Down Horizontal Width Arithmetic
> Formula: `Available Text Width = Container Width (W) - Outer Padding (2P) - Inter-Column Gaps - Fixed Chrome/Icons/Controls`

| Container / Row ID | Total Width `W` | Outer + Card Padding | Column / Item Gaps | Fixed Icons & Controls | Net Text Budget | Budget Allocation Proof |
|---|---|---|---|---|---|---|
| `RootHostContainer` | `380pt` | `32pt` (`16×2`) | `10pt` (between 2 cards) | `0pt` | `338pt` (`169pt` per card) | `380 - 32 - 10 = 338pt` (`2 × 169pt`) |
| `LeftMetricCard.Row` | `169pt` | `24pt` (`12×2`) | `12pt` (`6pt` icon gap + `6pt` label-readout gap) | `16pt` (`StrataProgressRing`) | `117pt` | `169 - 24 - 12 - 16 = 117pt >= Label(33pt) + Readout(84pt)` |
| `DenseEntryRow` | `348pt` | `24pt` (`12×2`) | `8pt × 3 = 24pt` | `28pt` (badge) + `44pt` (action) | `228pt` | `348 - 24 - 24 - 72 = 228pt` |

### 3.2 Three-Tier Text Constraints & Worst-Case Fixture Table
| Element ID | Text Tier | Character / Format Cap | Font & Layout Modifiers | Overflow / Reflow Strategy | Worst-Case Verification Fixture |
|---|---|---|---|---|---|
| `card.schemaLabel` | **Tier 1: Schema Label** | CJK `<=4` chars / EN `<=6–8` chars | `.caption`, `theme.secondaryForeground`, `lineLimit(1)` | Fixed vocabulary; move secondary descriptors (`1/5/15m`) to subtitle | `"核心负载"` / `"CPU Load"` |
| `card.metricValue` | **Tier 2: Metric Readout** | Deterministic format template (`"%.2f / %.2f GB"`) | `.body.monospacedDigit()` (`tabular-nums`), **`.layoutPriority(1)`** | **Zero truncation (`…` is a P1 V2 blocker)**; unit deduplicated (`GB`, never `GB GB`) | `"16.81 / 24.00 GB"` / `"11.3 / 27.4 / 25.1"` |
| `row.itemTitle` | **Tier 3: Dynamic Title** | Unbounded user/system text (`+35%` i18n expansion) | `.subheadline` / `.headline`, `theme.foreground` | Dedicated full-width row OR `ViewThatFits(in: .horizontal)` (`1-line -> 2-line`, `lineLimit(2)`, `minimumScaleFactor(0.85)`) | `"哑铃上斜卧推 (Incline Dumbbell Press - 慢速离心)"` |

---

## 4. Semantic Token & Dual-Appearance Contrast Matrix (`light` × `dark`)

> **Hard Rule**: Zero raw `#RRGGBB`, `Color(red:green:blue:)`, `UIColor`, or arbitrary Tailwind palette classes in product views. Dark mode uses **Surface Luminance Elevation (`L0 < L1 < L2`) + 1px top highlight (`9%–16%` white)**, never inverted shadows.

| UI Element / Slot | Semantic Role | Strata / Basalt Token Symbol | Light Appearance Value / Ref | Dark Appearance Value / Ref | Paired Surface / Background | WCAG 2.2 AA Contrast Target |
|---|---|---|---|---|---|---|
| Window / Page Canvas | L0 Base Canvas | `theme.canvas` (`product.surface.canvas` / `--bg-canvas`) | Warm Alabaster `#F8FAFC` | Obsidian Slate `#0B0F17` | — | Base reference |
| Primary Card Surface | L1 Raised Surface | `theme.surface` (`product.surface.raised` / `--bg-surface`) | Pure White `#FFFFFF` + contact shadow | Elevated Slate `#151C28` + `1px` `rgba(255,255,255,0.09)` border | `theme.canvas` | Surface distinction + `1px` hairline |
| Primary Reading Copy | Primary Foreground | `theme.foreground` (`product.text.primary` / `--text-primary`) | Deep Ink `#0F172A` | Crisp Slate `#F1F5F9` | `theme.surface` / `theme.canvas` | `>= 7.0:1` (AAA body target, `>=4.5:1` hard floor) |
| Schema Labels & Units | Secondary Foreground | `theme.secondaryForeground` (`product.text.secondary` / `--text-secondary`) | Muted Slate `#475569` | Muted Silver `#94A3B8` | `theme.surface` | `>= 4.5:1` |
| Primary CTA Fill | Interactive Accent | `theme.accent` (`DesignTokens.brandColor` / `--accent-primary`) | Brand Seed (e.g. `strata-blue`) | Calibrated Dark Brand Seed | `theme.surface` | `>= 3.0:1` non-text control boundary |
| Primary CTA Label | On-Accent Foreground | `theme.onAccent` (`--text-on-accent`) | `#FFFFFF` / high-contrast ink | `#FFFFFF` / calibrated ink | `theme.accent` | **`>= 4.5:1` (NEVER pair `theme.foreground` on `theme.accent`)** |
| Destructive / Error | Critical Status | `theme.critical` + `theme.onCritical` (`status.critical`) | Semantic Crimson (brand-independent) | Calibrated Dark Crimson | `theme.surface` / self | `>= 4.5:1` (independent of brand seed) |
| DataViz Series | Chart Palette | `chart-1` … `chart-6` + neutral grey | Highlight series + muted context grey | Highlight series + muted context grey | `theme.surface` | `>= 3.0:1` graphical marks; independent of brand seed |

---

## 5. Four-State Behavioral & Visual Completeness Matrix (`populated` / `empty` / `loading` / `error`)

> Every data-bearing surface MUST implement and snapshot all 4 states across both `light` and `dark` appearances (`4 × 2 = 8` mandatory state-appearance cells, plus `max-density` composite).

| State | Visual & Structural Contract | Required Strata Primitive | Copy & Localization Key Rule (`.xcstrings`) | User Action / Recovery Control | Mandatory Snapshot Artifact ID |
|---|---|---|---|---|---|
| **`populated`** | Full hierarchy; 1 focal anchor; `tabular-nums` aligned; equal-height sibling cards (`<=1px` delta) | Domain composition of `StrataCard` / `StrataList` | Verified localized keys; zero raw key leaks (e.g. `menubar.*`) | Primary workflow interactions | `<screen>-populated-light.png`<br>`<screen>-populated-dark.png` |
| **`empty`** | Preserves outer container geometry; centered instructional block with icon + reason + **1 primary CTA** | `StrataStateView(theme:kind: .empty)` | Explains *why* empty and *how* to start (never bare `"暂无数据"`) | Single Primary CTA (`"创建首个…"`, `"重置筛选"`) | `<screen>-empty-light.png`<br>`<screen>-empty-dark.png` |
| **`loading`** | **Same-geometry skeleton (`CLS = 0`)** matching `populated` card/row heights; no full-screen blocking spinner | `StrataStateView(theme:kind: .loading)` or `.redacted(reason: .placeholder)` | Optional calm status label; disable duplicate form submission | Cancel / non-blocking navigation | `<screen>-loading-light.png`<br>`<screen>-loading-dark.png` |
| **`error`** | Inline or card-scoped error state; **never silently fall back to fake `0` values or demo data on API failure** | `StrataStateView(theme:kind: .error)` | Human-readable failure cause + actionable recovery label | Inline `StrataButton` (`"重试"` / `"检查权限"`) | `<screen>-error-light.png`<br>`<screen>-error-dark.png` |

---

## 6. Component Reuse Boundary & Upstream Escalation Contract

### 6.1 Upstream Primitives Consumed Unmodified
| Component Symbol | Source Layer | Target File(s) Consuming It | Notes |
|---|---|---|---|
| `StrataTheme`, `StrataSurfaceModifier` | `Strata` Foundation | `<AppUI/ScreenView.swift>` | Explicit `try StrataTheme(theme:brand:)` injection; propagated to detached hosts (`NSPopover`, `inputView`) |
| `StrataButton`, `StrataCard`, `StrataStateView` | `Strata` Controls/Components | `<AppUI/ScreenView.swift>` | Zero local restyling of internal padding/radii |

### 6.2 Local Composition Glue Budget (`<= 15` Lines Hard Ceiling)
- **Allowed Local Glue (`<= 15` lines per view file)**: Domain-specific `HStack`/`VStack` arrangement, binding view-model properties to Strata component props, and top-level `.padding(P).frame(width: W)` host framing.
- **Upstream Split Trigger (`> 15` lines or Reusable Variant)**:
  - If implementing this screen requires **> 15 lines** of custom chrome, custom `Shape`/`RoundedRectangle` bezel, custom alignment guide, or a new component variant used in `>= 2` flows:
  - **STOP**: Set `upstream_escalation.needed: true` in the YAML header, define the upstream `shared-design-system` Task ID, and block the app-repo `Writer` until the upstream primitive is merged and pinned.

### 6.3 Obsolete / Contradictory Test Assertions to Update
| Test File & Line (`repo:path:line`) | Legacy Assertion | Required Update for New Design Contract |
|---|---|---|
| `<Tests/UISnapshotTests.swift:42>` | Asserted legacy `320pt` width or raw string | Update to assert `380 * scale` pixelWidth and localized `.xcstrings` key |

---

## 7. Writer & Reviewer Mechanical Verification Checklist (Gate Contract)

Both `Writer` (before push) and `Reviewer` (during V1/V2 review) MUST execute and record the following checks in the R10 receipt:

### 7.1 Automated Static & Geometry Gates
```bash
# 1. Static Design Contract Gate (zero raw hex/RGB, required contract sections)
python3 ~/Development/shared-ci/scripts/context/design_contract.py \
  --root <repo-root> \
  --contract docs/design-contract.md \
  --ui-path <Owned-UI-Path>

# 2. Fixed-Width Host Pixel Geometry Gate (for NSPopover / NSHostingView / fixed snapshots)
sips -g pixelWidth -g pixelHeight <snapshot-dir>/*.png
# Assert: pixelWidth == fixed_width_pt * scale_factor (e.g. 380 * 2 = 760px)
```

### 7.2 8-Cell State × Appearance + Max-Density Boolean Table
| State \ Appearance | `light` Snapshot + Contrast (`>=4.5:1`) | `dark` Snapshot + Surface Elevation | Zero `…` Truncation | Equal Card Height (`<=1px`) |
|---|---|---|---|---|
| `populated` | `[ ] PASS` (`<path>`) | `[ ] PASS` (`<path>`) | `[ ] PASS` | `[ ] PASS` |
| `empty` | `[ ] PASS` (`<path>`) | `[ ] PASS` (`<path>`) | `[ ] PASS` | `[ ] N/A` |
| `loading` (`CLS=0`) | `[ ] PASS` (`<path>`) | `[ ] PASS` (`<path>`) | `[ ] PASS` | `[ ] PASS` |
| `error` | `[ ] PASS` (`<path>`) | `[ ] PASS` (`<path>`) | `[ ] PASS` | `[ ] N/A` |
| `max-density` fixture | `[ ] PASS` (`<path>`) | `[ ] PASS` (`<path>`) | `[ ] PASS` | `[ ] PASS` |

### 7.3 Reviewer V2 P1 Hard-Fail Blockers (Non-Waivable as `CONCERNS`)
A Reviewer MUST mark `FAIL` (never downgrade to `CONCERNS`, even if Writer followed a flawed brief formula) if ANY of the following occur:
1. Any numeric readout or schema label displays an ellipsis (`…`) in the worst-case fixture.
2. Exported snapshot `pixelWidth != fixed_width_pt * scale` (host clipping from `.frame(width: W).padding(P)`).
3. Side-by-side Bento cards in the same row have height delta `> 1px`.
4. Duplicate unit labels (`lb lb`, `GB GB`) or raw localization keys (`menubar.*`) appear in rendered UI.
5. Foreground/background contrast falls below `4.5:1` for body/button copy (e.g., `primaryText` on `interactiveAccent` fill) or `.toolbar` exhibits double-capsule bezel in iOS 26 / macOS 26.
```
