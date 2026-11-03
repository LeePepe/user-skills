---
name: motion-studio
description: 'Use when designing or implementing Web/App UI micro-interactions, landing-page timeline choreography (Motion, Anime.js, Lottie, Three.js), or code-driven cover animations, transition templates, and product promo videos with Remotion and FFmpeg. Bridges DesignTokens/StrataMotion/Basalt tokens into interactive motion and automated video pipelines.'
allowed-tools: Read, Write, Edit, Bash, Agent, Skill
---

# motion-studio

Use this skill when motion is part of the product surface or when a rendered
asset must be reproducible from code. Start by reading the `my-designer`
handoff and selecting exactly one `surface_mode` (`Operate`, `Read`,
`Persuade`, or `Experience`) and one `visual_mode`
(`Strata-Core`, `Strata-Glass`, `Independent-Glass`, or `Independent-Custom`).
Treat `DesignTokens`, `Strata`/`StrataMotion`, and `@nocoo/basalt` as token
authorities; do not invent a parallel motion language.

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
