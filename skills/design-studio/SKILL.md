---
name: design-studio
description: 'Use when designing or implementing Web/App interactions, data-linkage patterns, micro-interactions, landing-page timeline choreography (Motion, Anime.js, Lottie, Three.js), or code-driven cover animations, transition templates, and product promo videos with Remotion and FFmpeg. Grok Bot, Cindy, and local CLI users must call design_catalog.py ingest --text "..." to normalize and archive external design/interaction/motion/layout prompts. Bridges DesignTokens/StrataMotion/Basalt tokens into interactive design and automated motion pipelines.'
allowed-tools: Read, Write, Edit, Bash, Agent, Skill
---

# design-studio

Use this skill when an interaction, data linkage, motion choreography, or
rendered asset is part of the product surface. Start by reading the
`my-designer` handoff and selecting exactly one `surface_mode` (`Operate`,
`Read`, `Persuade`, or `Experience`) and one `visual_mode`
(`Strata-Core`, `Strata-Glass`, `Independent-Glass`, or `Independent-Custom`).
Treat `DesignTokens`, `Strata`/`StrataMotion`, and `@nocoo/basalt` as token
authorities; do not invent a parallel interaction or motion language.

## Three-skill synergy and routing

The handoff flows `my-designer` → `visual-design-modernization` →
`design-studio`. Consume the selected `visual_mode`, `surface_mode`, and
`motion_tier`: M1 is Micro-Feedback, M2 is Spatial & Gesture Choreography, M3
is Narrative/3D Hero, and M4 is Code-to-Video. Use
`python3 skills/design-studio/scripts/design_catalog.py` to search and
recommend catalog entries before implementation. The catalog stores `IP-*`
Interaction/Data-Linkage Patterns, `MP-*` Motion Patterns, and `T-*` Tools.

The **Shared** layer owns physical state machines
(`idle -> drag -> threshold -> commit/cancel -> spring settle`),
DesignTokens/StrataMotion timing and spring semantics, Lottie, MCP for Blender
GLTF/USDZ assets, and Remotion/FFmpeg promo outputs. The **Web** layer owns
Motion, Anime.js, Three.js with Postprocessing/Three.quarks/Jolt Physics/GPU
Pathtracer, and DEV-only Tweakpane. The **Mobile-Native** iOS/macOS SwiftUI
layer owns StrataMotion, `matchedGeometryEffect`, `KeyframeAnimator`,
`PhaseAnimator`, Canvas, `ShaderLibrary` Metal effects, `SensoryFeedback`, and
`.contentTransition(.numericText())`.

### Two-Stage Inspiration Vault (`desktop` / `mobile` / `web-backlog` × 8 Dimensions)

Unlabeled design inspirations (color palettes, visual styles, native components, Swift Charts, Liquid Glass materials, motion, layout, or copy) are automatically classified and routed into platform-specific vaults (`references/vault/desktop-macos.json`, `references/vault/mobile-ios.json`, and frozen `references/vault/web-backlog.json`) using `[taxonomy-v2.json](references/taxonomy-v2.json)`:

1. **Stage 1 · Jev Fast Classify (`http://127.0.0.1:19823/v1/systemone` + Taxonomy v2):**
   - Rejects non-design noise (`noul < 0.40`).
   - Scores platform (`desktop` vs `mobile` vs `web`) and 8 orthogonal dimensions (`color-palette` `CL`, `visual-style` `VS`, `component` `CP`, `chart-dataviz` `DV`, `glass-material` `GM`, `motion-interaction` `MI`, `layout-navigation` `LN`, `typography-copy` `TC`).
2. **Stage 2 · Independent LLM Fallback & Self-Learning Writeback:**
   - Triggered automatically when `platform_confidence < 0.70`, `dimension_confidence < 0.70`, or the prompt mixes multiple dimensions (`>=2` strong dimensions).
   - Decomposes compound prompts into atomic inspiration cards with pure Apple Native (`desktop_macos_implementation`, `mobile_ios_implementation`) mappings and writes newly discovered domain keywords back into `taxonomy-v2.json`.

```bash
# Classify unlabeled raw inspiration (Jev fast path -> LLM fallback with keyword learning)
python3 skills/design-studio/scripts/design_catalog.py classify --text "<raw inspiration>" --learn

# Ingest into platform-separated vaults (desktop-macos.json / mobile-ios.json / web-backlog.json)
python3 skills/design-studio/scripts/design_catalog.py vault-ingest --text "<raw inspiration>"

# Progressive disclosure search (queries Apple desktop/mobile by default; web stays in backlog)
python3 skills/design-studio/scripts/design_catalog.py vault-search \
  --platform desktop --dimension glass-material --query "侧边栏毛玻璃" --top 5
```

### Extensible catalog workflow

1. **Grok Bot, Cindy, and local CLI users:** when an external design,
   interaction, motion, or layout prompt should be normalized and archived,
   call `python3 skills/design-studio/scripts/design_catalog.py ingest --text "..."`.
   Use `--file <UTF-8-path>` instead of `--text` for a file, or
   `--image <path>` for text-only local macOS Vision OCR (Chinese/English;
   no network or token-bearing OCR service). These inputs are mutually
   exclusive. Markdown bullets,
   numbered items (`1.`, `1、`, `①`, `(1)`), and `**title**: description`
   paragraphs are split in source order; surrounding prose is context and is
   excluded. Use `--no-split` to force one segment.
2. Inspect the JSON proposal with `--dry-run`: ingestion classifies each
   segment into an IP/MP category, assigns successive numeric IDs, and fills
   the source, canonical prompt, state machine, web/mobile implementations,
   and token guardrails using deterministic local templates. Jev is queried at
   `http://127.0.0.1:19823/v1/systemone` by default; use `--jev-url` to
   override or `--no-jev` for deterministic offline fallback. Jev noise and
   malformed/unreachable responses never fail ingest. Override `--id`,
   `--category`, `--platforms`, `--surface-modes`, or `--motion-tier` when
   needed. Similar patterns block with exit code 2 and matching IDs; mixed
   created/duplicate batches return 0, while an all-duplicate batch returns 2.
   Use `--force` only for an intentional variant. `--compact` prints one line
   per created or duplicate result. Without `--dry-run`, ingestion saves the
   catalog and re-renders the matrix once.
   For manually authored entries or tools, retain `search`, `add-pattern`,
   and `add-tool`.
3. Select motion with `recommend --platform <web|mobile> --surface-mode <mode>`.
4. Close the taste loop with `record-use --id <ID> --context "<repo/screen>"`,
   recording `liked`, `neutral`, or `disliked` feedback.

For Dashboard, data-workspace, and interactive-list scenarios, search for
`IP-*` patterns before choosing a design. Preserve the selected trigger,
linkage, context-preservation, and recovery behavior in `design.md`; the
catalog is a continuously growing reference library, not a rigid visual
template.

## Track A · Product UI and landing-page motion

### Operate / Read

These are high-frequency tools, forms, tables, and dashboards:

- For `100+/day` actions, use a `100–120ms` ease-out pointer-down
  `scale(0.97–0.98)` with zero spatial bounce.
- For changes made tens of times per day, use a critically damped spring:
  `response: 0.28–0.32, dampingFraction: 0.82–0.86` or
  `stiffness: 380–420, damping: 28–32`.
- Restrict `Lottie` to empty-state illustrations and rare milestone
  celebrations; never place it on high-frequency rows.
- Always provide `prefers-reduced-motion` / Reduce Motion behavior, normally
  an opacity plus `2px` blur crossfade or `.numericText()` transition.
- Do not put Anime.js multi-step timelines or Three.js canvases inside data
  tables or forms.

### Persuade / Experience

Marketing heroes, interactive stories, and bespoke Mode D web surfaces may
use Motion for layout transitions, scroll-linked parallax, and shared-layout
morphs. Use Anime.js for multi-element staggered timelines, SVG line drawing
or morphing, and kinetic typography. Use Three.js as the 3D foundation with
Postprocessing, Three.quarks, Jolt Physics, GPU Pathtracer, and MCP for
Blender where their respective roles are justified.

Tweakpane is a dev-only bridge. Mount it behind a development flag to tune
spring stiffness, damping, shader uniforms, and particle rates; serialize
final constants into DesignTokens or Basalt, then strip or gate the panel
before production delivery.

## Track B · Code-driven video, covers, and transitions

Prefer animated content covers (`<Still>` or a short-loop `<Composition>`) and
reusable `@remotion/transitions` templates for rapid wins, then scale to full
release or promo videos. Reuse real Basalt/React components and DesignTokens
CSS variables inside Remotion compositions. Standard output presets are
`1920x1080` (16:9), `1080x1920` (9:16), and `1080x1440` (3:4 social cover).

Drive every animation deterministically from `useCurrentFrame()`,
`useVideoConfig()`, `spring()`, and `interpolate()`. Never use CSS keyframes or
`requestAnimationFrame` for headless-rendered choreography. Route formula
derivations, geometric proofs, and algorithm visualizations to Manim, either
as clips composited with `<OffthreadVideo>` or as Manim-inspired SVG/Canvas.

The zero-GUI audio and export loop is:

1. CosyVoice synthesizes voiceover from script markdown.
2. WhisperX emits word-level JSON timestamps for frame-accurate captions.
3. Librosa emits onset strength, BPM, and beat frames as a JSON cue sheet.
4. Remotion snaps transitions and spring triggers to those cues.
5. FFmpeg renders MP4/WebM/ProRes/GIF, applies `loudnorm`, and slices
   segments. OBS Studio and LosslessCut are human-only capture/rough-cut
   tools, never autonomous render-loop dependencies.

See [toolchain-20-matrix.md](references/toolchain-20-matrix.md) for the full
taxonomy, decision matrix, and copy-pasteable recipes.

## Delivery checklist

- Confirm the surface and visual modes before choosing an animation library.
- Reuse token authorities and keep dev controls out of production.
- Cover populated, loading, empty, and error states where motion changes state.
- Implement reduced-motion fallbacks and deterministic frame choreography.
- Validate the real rendered product or headless output, not a throwaway demo.
