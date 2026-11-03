# Motion Studio toolchain matrix

The matrix maps the twenty tools and projects used by this skill to the
surface where they are appropriate, how an agent may automate them, and the
token boundary that keeps motion aligned with `my-designer`.

| # | Tool / project | Category | Allowed `surface_mode` | Agent automation | `my-designer` integration |
|---:|---|---|---|---|---|
| 1 | Remotion | Code-to-Video | Video/Offline, Persuade | Direct Code + CLI | Read DesignTokens/Basalt CSS variables in compositions |
| 2 | `@remotion/transitions` | Code-to-Video | Video/Offline, Persuade | Direct Code | Transition presets consume token durations and easings |
| 3 | Motion | Core Motion & Web UI | Operate, Read, Persuade, Experience | Direct Code | Use StrataMotion or token-backed spring/layout values |
| 4 | Anime.js | Core Motion & Web UI | Persuade, Experience | Direct Code | Map stagger, easing, and SVG colors to the selected token set |
| 5 | Lottie | Core Motion & Web UI | Empty states, Experience | Direct Code | Export illustrations using the active palette; never animate dense rows |
| 6 | Three.js | 3D & Physics | Persuade, Experience, Video/Offline | Direct Code | Bind scene materials and camera presets to DesignTokens/Basalt |
| 7 | Postprocessing | 3D & Physics | Persuade, Experience, Video/Offline | Direct Code | Tokenize bloom, DOF, and aberration strengths |
| 8 | Three.quarks | 3D & Physics | Persuade, Experience, Video/Offline | Direct Code | Use token-backed particle colors, rates, and sizes |
| 9 | Jolt Physics | 3D & Physics | Experience, Video/Offline | Direct Code | Serialize stable simulation constants beside motion tokens |
| 10 | GPU Pathtracer | 3D & Physics | Experience, Video/Offline | Direct Code + CLI | Keep render quality and lighting presets in the token contract |
| 11 | MCP for Blender | 3D & Physics | Experience, Video/Offline | MCP / CLI Pipeline | Generate GLTF assets that use the product material palette |
| 12 | Tweakpane | Core Motion & Web UI | Persuade, Experience | Dev-Time Only | Export tuned values into DesignTokens/Basalt before shipping |
| 13 | Manim | Code-to-Video | Video/Offline | Direct Code + CLI | Match typography, colors, and spacing to the design contract |
| 14 | CosyVoice | Audio & Beat Intelligence | Video/Offline | MCP / CLI Pipeline | Voiceover script and caption style use product language tokens |
| 15 | WhisperX | Audio & Beat Intelligence | Video/Offline | MCP / CLI Pipeline | Word timestamps drive tokenized caption components |
| 16 | Librosa | Audio & Beat Intelligence | Video/Offline | Direct Code + CLI | Emit normalized beat cues consumed by tokenized transitions |
| 17 | `@ffmpeg/ffmpeg` | Asset & Export Tooling | Video/Offline | MCP / CLI Pipeline | Encode from the same size, color, and caption presets |
| 18 | FFmpeg CLI | Asset & Export Tooling | Video/Offline | MCP / CLI Pipeline | Render, normalize, and slice reproducibly in CI |
| 19 | OBS Studio | Asset & Export Tooling | Video/Offline | Human GUI Only | Capture reference footage using the approved palette and dimensions |
| 20 | LosslessCut | Asset & Export Tooling | Video/Offline | Human GUI Only | Rough-cut only; final timing and tokens remain in Remotion/FFmpeg |

## Recipe 1: Basalt-backed Remotion cover and transition

```tsx
import {Composition, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import {TransitionSeries, linearTiming, fade} from "@remotion/transitions";
import {basaltTokens} from "@nocoo/basalt";

const Cover = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = spring({frame, fps, config: {damping: 28, stiffness: 400}});
  const opacity = interpolate(progress, [0, 1], [0, 1]);
  return <div style={{background: basaltTokens.surface.page, opacity}} />;
};

export const composition = (
  <Composition id="ContentCover" component={Cover} width={1920} height={1080} fps={30} durationInFrames={90} />
);

export const sequence = (
  <TransitionSeries>
    <TransitionSeries.Sequence durationInFrames={45}><Cover /></TransitionSeries.Sequence>
    <TransitionSeries.Transition timing={linearTiming({durationInFrames: 12})} presentation={fade()} />
    <TransitionSeries.Sequence durationInFrames={45}><Cover /></TransitionSeries.Sequence>
  </TransitionSeries>
);
```

Keep the actual composition and transition in the project’s existing Remotion
entrypoint; the blueprint shows the contract, not a new demo directory.

## Recipe 2: choose UI motion and export tuned tokens

```ts
const preset = surfaceMode === "Operate"
  ? {engine: "Motion", response: 0.30, dampingFraction: 0.84, press: 0.97}
  : {engine: "Anime.js", staggerMs: 42, easing: "easeOutCubic"};

if (import.meta.env.DEV) {
  const pane = new Tweakpane.Pane();
  pane.addBinding(preset, "response", {min: 0.28, max: 0.32});
  pane.on("change", ({value}) => designTokens.motion.springResponse = value);
}
```

Choose Motion for layout/state transitions, Anime.js for authored sequences,
and Lottie only for rare illustration moments. Persist the final values in
DesignTokens/StrataMotion/Basalt and remove or gate the pane for production.

## Recipe 3: voice, timestamps, beats, and headless export

```bash
cosyvoice --text script.md --output voice.wav
whisperx voice.wav --output_dir cues --output_format json
python - <<'PY'
import json, librosa
y, sr = librosa.load("voice.wav", sr=None)
tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
json.dump({"bpm": float(tempo), "beat_frames": beats.tolist()}, open("beats.json", "w"))
PY
npx remotion render src/index.ts Promo out/promo.mp4 --props=beats.json
ffmpeg -i out/promo.mp4 -af loudnorm -c:v libx264 -c:a aac out/promo-final.mp4
```

Normalize cue schemas before consumption, keep captions frame-based, and make
the final FFmpeg command the reproducible export boundary.
