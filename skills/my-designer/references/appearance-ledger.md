# Appearance Ledger & Per-App `AppTheme.swift` Contract

Every Apple app shares **Apple HIG + Liquid Glass two-layer craft**, while owning a **distinct visual personality, brand palette, typography scale, and curated `ShipSwift` component pairing**. Never force a monolithic cross-repo UI wrapper package onto every app; instead, verify the **Cross-App Appearance Ledger** below so no two apps look identical, and generate a self-contained single-file `AppTheme.swift` (`<80` lines) inside the target product repository.

---

## 1. Cross-App Appearance Ledger (Anti-Clone Registry & Spatial Budget)

Before locking a new app or major redesign in `design.md`, inspect this ledger to pick a **non-colliding Style Archetype (`VS-001..008`) and Brand Seed (`CL-001..008`)**.

> **CRITICAL — Component Palette ≠ Mandatory Flat Checklist (`DC-001` & `DC-002`):**
>1. **Spatial & Progressive Disclosure Budget**: The rightmost column lists the *candidate component menu* for the entire product, **NOT a checklist to stack onto one screen**. Any single viewport or split-view pane is capped at **at most 2–3 visual container groups** (`<= 900pt` vertical height budget on desktop panes; `aspect_ratio <= 2.6`). Any 4th+ module MUST be folded into a **Segmented Lens** (`Overview | Breakdown | Accounts`), a **3D `ZStack` pocket stack** (`SWWallet`), or a **Sheet / Inspector drill-down**.
>2. **No Duplicate Data Representation (`DC-002`)**: Never render the same dataset twice in one viewport (e.g., a `SWDonutChart` with a 6-row category legend AND a redundant 8-row category `ProgressView` bar list below it). Pick ONE representation or toggle them via a Segmented Picker.
>3. **`ShipSwift` Spatial Signature Preservation (`DC-001`)**: When adopting a `ShipSwift` spatial component such as `SWWallet`, preserve its `ZStack` + `zIndex` + `offset(y:)` 3D pocket geometry (~220pt collapsed height that fans out on tap). **Never flatten `SWWallet` into a plain `VStack { ForEach }` list.**

| Product / Repo | Platform | Style Archetype (`VS-*`) | Palette & Surface Polarity (`CL-*`) | Typography Signature (`TC-*`) | Candidate Glass / Shader & `ShipSwift` Palette (Pick `<=2` per Viewport; Fold via Lens/ZStack/Sheet) |
|---|---|---|---|---|---|
| **VitalStride (`Workout`)** | iOS / watchOS | `VS-001` Kinetic Athletic Telemetry (or Cross-Domain Remix `VS-005..008` below) | `CL-001` Athletic Volt or `CL-006..008` Luminous Paper / Aurora / Chrono (`CL-005` zero-black-dead-zone rule) | SF Pro Rounded Display + `.monospacedDigit()` heavy tabular numbers | `.glassEffect(.regular)` top mode switcher & rest bar; 5×4 grid keypad; `SWActivityHeatmap`, `SWRingChart`, `SWConfetti` |
| **Financial (`FinUI`)** | iOS / macOS | `VS-002` Warm Editorial & Swiss Ledger (Apple Wallet / Wise / Readwise paper craft) | `CL-002` Warm Ivory Paper (`#FAF7F2` Light / `#121316` Dark) + Deep Forest Ink (`#0F5132`) + Brass Accent | New York Serif headlines + SF Pro body + tabular currency digits | Functional glass toolbar only; solid tactile ledger cards; `SWWallet`, `SWDonutChart`, `SWAreaChart` |
| **VoxPocket** | iOS / macOS (`NSPopover`) | `VS-003` Precision Acoustic & AI Studio (Flighty / Overcast / Linear dark instrument) | `CL-003` Deep Slate (`#0E1117`) + Sonic Cyan (`#38BDF8`) + Recording Coral (`#F43F5E`) | SF Pro + SF Mono for CLI templates, shortcuts, and timestamps | Floating `MenuBarExtra` / quick-capture `.glassEffect(.regular.interactive())`; `SWThinkingIndicator`, `SWLiquidMetal`, `SWShimmer` |
| **MenuBarMijia** | macOS (`MenuBarExtra`) | `VS-004` Ambient Home Tactile Glass (Apple Home / Calm atmospheric control) | `CL-004` Translucent Warm Amber (`#F59E0B`) + Soft Sage (`#10B981`) over adaptive desktop wallpaper | SF Pro Compact 13pt / 11pt secondary labels, 4pt BAMA grid (`Space`) | `NSPopover` glass chrome + tactile slider tiles; `SWAnimatedMeshGradient`, `SWStepper`, `SWStatusBadge` |
| **Herdr / AIDash** | macOS (`NSWindow`) | `VS-007` Swiss Chrono & Avionics Telemetry (`Linear / Flighty / Raycast` cockpit) | `CL-008` Swiss Alabaster (`#F8FAFC`) / Avionics Navy (`#0E131F`) + Linear Indigo (`#5E6AD2`) + Route Blue (`#0A84FF`) | SF Pro 13pt + SF Mono 12pt metrics, zero decorative bounce (`dampingRatio: 1.0`) | `NavigationSplitView` + `.unifiedCompact` toolbar; `SWKPICard`, `SWLineChart`, `SWBarChart`, `SWNetworkGraph` |
| **New Consumer / Lifestyle App Slot** | iOS / iPadOS / Duo | `VS-004` Playful Sticker & Tactile Bento (or `VS-006` Mindful Aurora) | `CL-004` Sunlit Cream or `CL-007` Butter Cream Aurora (`#FFF7E7` + `#FF6B35`) | SF Pro Rounded bold titles + expressive `.symbolEffect(.bounce)` | `GlassEffectContainer` floating pills + `SWFoil` / `SWParticleTransition` / `SWOnboardingView` |

---

## 2. Cross-Domain Archetype Remix Matrix（跨界风格混搭矩阵 — 以 Fitness 训中记录页为例）

Never trap a vertical app inside its category cliché (e.g., "every Fitness app must be pitch-black with neon green"). By extracting DNA from `awesome-ios-design-md` across **Productivity (`Things 3`, `Bear`, `Notion`, `Linear`)**, **Mindfulness (`Calm`, `Headspace`)**, **Avionics (`Flighty`)**, and **Editorial (`Nike Run Club`, `Apple Music`, `Cash App`)**, the exact same **VitalStride In-Workout Logging Screen** can remix into 4 distinct archetypes while strictly obeying `CL-005` (Hue-Arc Luminous Gradient Rule — zero `#000000` muddy dead zones):

| Remix Archetype (`VS-*`) | Source Apps (`awesome-ios-design-md`) | Paired Palette (`CL-*`) | Target Fitness Surface Mode | Signature UI & SwiftUI Component Graft |
|---|---|---|---|---|
| **`VS-005` · 禅意留白与纸感清单母体** *(Zen Paper Checklist)* | `productivity/things-3` + `productivity/bear` + `productivity/notion` | **`CL-006`** Pure Paper (`#FFFFFF` / `#F5F6F8`) + Things Sky Blue (`#4F97FF`) + Bear Sage (`#57C98B`) + Warm Gold (`#FFD60A`) | **平铺版训中计划表** *(Tiled Workout Plan — `Operate / Read`)* | Grafts `ThingsCheckbox` (`22pt` circle with `scaleEffect(0.001 -> 1.0)` center-out fill & `.sensoryFeedback(.impact(weight: .soft))`), borderless `#F5F6F8` exercise group pills, soft strikethrough/dimming on completed sets, and `MagicPlusButton` (`#4F97FF`). Turning heavy resistance sets into an effortless GTD checklist. |
| **`VS-006` · 呼吸感正念专注母体** *(Mindful Focus Recovery)* | `fitness/calm` + `fitness/headspace` | **`CL-007`** Headspace Butter Cream (`#FFF7E7`) + Aurora Hue Arc (`#FFB89E -> #FFD25C -> #FF6B35`) or Calm Sky Hue Arc (`#38BDF8 -> #2A6FD6 -> #4F46E5`) | **专注版训中大卡 & 组间休息倒计时** *(Focus Card & Rest Recovery — `Experience / Operate`)* | Grafts `BreathingSphere` / `BreatheBubble` (`4s` inhale · `4s` hold · `6s` exhale radial hue-arc expansion), `Lora` / `SF Pro Rounded` calm cues ("Lower heart rate · Next: 100 kg × 5"), and translucent `.glassEffect(.regular)` rest controls over a luminous hue-arc canvas (`CL-005` zero black dead zone). |
| **`VS-007` · 瑞士精密计时码表母体** *(Swiss Chrono Telemetry)* | `productivity/linear` + `travel/flighty` | **`CL-008`** Swiss Alabaster (`#F8FAFC`) / Avionics Navy (`#0E131F`) + Linear Purple (`#5E6AD2`) + Flighty Blue (`#0A84FF`) + On-Pace Green (`#30D158`) | **高密度容量/PR 遥测台** *(High-Density Telemetry — `Operate / Read`)* | Grafts `Flighty` boarding-pass header + `FlightArc` live volume trajectory curve, `Linear` `StatusGlyph` half-filled set rings, `SF Mono` set identifiers (`SET-01`, `e1RM 132.5`), `StatusChip` capsules (`ON PACE`, `PR +2.5KG`), and `dampingRatio: 1.0` zero-bounce keyboard entry. |
| **`VS-008` · 杂志画报与大胆色块母体** *(Editorial Poster Block)* | `fitness/nike-run-club` + `music/apple-music` + `finance/cash-app` | **`CL-008` / Editorial** Pure White (`#FFFFFF`) + Apple Music Coral (`#FA2D48`) + Solar Volt (`#CCFF00`) + Cobalt (`#0A84FF`) | **画报级大字冲刺/PR 专注态** *(Poster Hero Focus — `Operate / Persuade`)* | Grafts `Nike Run Club` `64–88pt` `.width(.condensed)` heavy tabular numerals, `Cash App` `AmountKeypad` auto-scaling digit entry (`96pt -> 64pt -> 52pt` with zero truncation), and `ApplePlayButton` (`0.92` spring press) on high-key color-blocked cards. |

---

## 3. Single-File Per-App `AppTheme.swift` Template (`<80` Lines)

Each app owns its theme locally in `Sources/<App>/Theme/AppTheme.swift` (zero external design-system lock-in). Copy and tailor the archetype tokens below:

```swift
import SwiftUI

/// Per-App Brand & Spatial Tokens (Archetype: <VS-ID> / <AppName>)
public enum AppTheme {
    // 1. 4pt Grid Scale (BAMA layout-grid standard)
    public enum Space {
        public static let xs: CGFloat = 4
        public static let sm: CGFloat = 8
        public static let md: CGFloat = 12
        public static let lg: CGFloat = 16
        public static let xl: CGFloat = 20
        public static let xxl: CGFloat = 24
        public static let section: CGFloat = 32
    }

    // 2. Concentric Radii (prefer OS 26/27 .rect(corner: .containerConcentric) / NSView.cornerConfiguration; fallback tokens below)
    public enum Radius {
        public static let xs: CGFloat = 6     // Compact cells / 5x4 keypad keys
        public static let sm: CGFloat = 10    // Controls & pills
        public static let md: CGFloat = 16    // Standard content cards
        public static let lg: CGFloat = 24    // Outer containers / sheets
    }

    // 3. Semantic Brand & Surface Palette (Asset Catalog or adaptive Light/Dark)
    public static let accent = Color.accentColor
    public static let surfacePrimary = Color(
        #if os(macOS)
        nsColor: .windowBackgroundColor
        #else
        uiColor: .systemBackground
        #endif
    )
    public static let surfaceCard = Color(
        #if os(macOS)
        nsColor: .controlBackgroundColor
        #else
        uiColor: .secondarySystemGroupedBackground
        #endif
    )

    // 4. Motion Signatures (Emil Kowalski frequency rule: 1.0 critically damped default)
    public static let snappySpring = Animation.spring(response: 0.26, dampingFraction: 1.0)
    public static let gestureSpring = Animation.spring(response: 0.32, dampingFraction: 0.80)
}

/// Consolidated Typography Modifier (rshankras AppTextStyle pattern — never scatter .tracking/.textCase)
public struct AppTextStyle: ViewModifier {
    public enum Role { case heroMetric, sectionTitle, bodyPrimary, captionMeta }
    let role: Role

    public func body(content: Content) -> some View {
        switch role {
        case .heroMetric:
            content.font(.system(.largeTitle, design: .rounded, weight: .bold)).monospacedDigit()
        case .sectionTitle:
            content.font(.headline.bold())
        case .bodyPrimary:
            content.font(.body)
        case .captionMeta:
            content.font(.subheadline).foregroundStyle(.secondary).monospacedDigit()
        }
    }
}

/// Signature Tactile ButtonStyle (awesome-ios-design-md press feedback)
public struct AppPressableButtonStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1.0)
            .animation(AppTheme.snappySpring, value: configuration.isPressed)
    }
}
```
