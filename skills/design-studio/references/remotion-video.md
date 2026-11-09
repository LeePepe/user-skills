# Track B — Remotion video

Load only for `--surface-mode video`. Prefer reusable Remotion transitions and real Basalt/React tokens. Standard presets: 1920x1080, 1080x1920, and 1080x1440.

Drive frames deterministically with `useCurrentFrame()`, `useVideoConfig()`, `spring()`, and `interpolate()`; never use CSS keyframes or `requestAnimationFrame` for headless choreography. Route proofs to Manim and compose with `OffthreadVideo` when useful.

## Offline pipeline
1. CosyVoice creates voiceover.
2. WhisperX creates word-level caption timestamps.
3. Librosa creates onset/BPM/beat cue JSON.
4. Remotion snaps transitions to cues.
5. FFmpeg renders MP4/WebM/ProRes/GIF with `loudnorm` and segment slicing.

OBS and LosslessCut are human-only capture tools, never render-loop dependencies.
