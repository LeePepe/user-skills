# Offline navigation and evidence contract

Load for `scaffold-gallery --mode app-nav`, known-direction depth, `jev-gate`, or structured Visual Traceback. These are handoff-only HTML explorations, never native production code. `scaffold-gallery` may mount a round to `~/Development/leepepe.github.io/demos/<goal>/` (or an explicit `--blog-repo`); use `--no-blog-mount` for isolated verification. Run `cleanup-demo --goal <goal>` immediately after B3 freeze or Goal completion to remove ephemeral blog and handoff rounds while preserving receipts.

## Bespoke full-app schemes

Supply `--app-flows-json` (JSON or path) with `app_context` and exactly four `schemes`. Each scheme has a nonempty `navigation_model` label, `title`, `screens`, and a complete `architecture`. Labels never certify architecture. Custom labels are unrestricted; the operative primitives compose domain-specific hypotheses rather than four named app templates.

| Field | Executable meaning |
|---|---|
| `screens[].id/name/p0_summary/role` | Unique content or `sheet`/`popover` destinations; overlays do not replace content |
| `actions[]` | `{label, frequency: P0..P3, commit?: domain-key, transition?: edge}`; legacy strings map to indexed edges |
| `transitions[]` | `{type, to}`: pushScreen, popScreen, switchTab, toggleSheet, togglePopover; dismissOverlay needs no target |
| `architecture.kind` | `stack`: sticky back/toolbar + bottom commit; `tabs`: tab-local stacks + separated prominent pill; `lens`: top lens + safe-area dock; `deck`: horizontally revealed destinations + inspector tools |
| `architecture.root/destinations` | Declared content root and visible journey destinations; each tab/root owns its own history |
| `architecture.primary` | `{screen, action: index}` referencing a declared P0 action, not a generic button |
| `architecture.sheet/overflow` | Optional sheet ID and array of declared `{screen, action}` references for rare actions |
| `architecture.collapse_title` | Boolean, default true; collapses large title after content scroll |

Minimal architecture fragment (provide screens/actions and four complete schemes alongside it):

```json
{
  "navigation_model": "capture-first voice journey",
  "architecture": {
    "kind": "lens", "root": "capture",
    "destinations": ["capture", "library"],
    "primary": {"screen": "capture", "action": 0},
    "sheet": "tools",
    "overflow": [{"screen": "tools", "action": 0}],
    "collapse_title": true
  }
}
```

`--app-screens` or `--app-context` alone is rejected: Designer must supply the operative domain schemes, not ask the scaffolder to invent journeys. A no-input smoke call supplies overridable starter schemes only. Unknown/missing architecture, invalid targets/roles/actions and label-only orthogonality reject before writing.

Action frequency controls disclosure: P0/P1 stay inline, P2 is collapsed behind details, P3 content actions must be routed through the declared overflow; overlay actions remain accessible. Action commits update domain counters and visible lists, and feedback survives navigation and overlay dismissal for the current sandbox session. Tabs restore their own pushed screen/history; Back never consumes another tab's history. Sheet and popover actions use the same dispatcher as content and chrome.

## Material and geometry

App HTML is self-contained. Content is always solid. Floating functional chrome uses the signed squircle Snell/Fresnel map, three chromatic displacement channels and CSS rims. Blink applies the SVG filter to the backdrop; WebKit does not support SVG backdrop filters and instead applies it to the caustic veil (`::before`) above solid content, with CSS Fresnel/chromatic rims and a blur backdrop. This documented fallback is not a claim of full optical equivalence to native Liquid Glass.

Medium and large use explicit 45vh/80vh height and max-height, with a grabber and independently scrollable overlay. Mobile controls have minimum 44px dimensions; docks reserve safe-area padding. `surface=solid|warm|dark` and `density=regular|compact|airy` apply to chrome/content. Supported anti-patterns are `no glass on content`/`no content glass` (guaranteed) and `no top capsule`/`no top glass` (removes top floating material). Unknown anti-patterns/style keys reject rather than certify unimplemented preferences; `style_overrides.note` alone is inert.

## Ten-screen breadth and depth

`--direction VS-005..VS-008` preserves the archetype. Every pair differs on >=2 of P0, folding, archetype, content layout, whitespace and glass geometry. Layout changes hero placement/grid; whitespace changes content padding; glass geometry changes width/shape/elevation; folding controls execute accordion/lens/deck state. `--screen-content` reaches the rendered hero in every variant. Breadth remains the default; legacy `tenfold-gallery/v1` is retained.

## Deterministic correspondence versus live Jev

The gallery gate parses per-page `application/json` coordinates, validates the manifest graph, regenerates canonical DOM/CSS/JS from the same versioned renderer, and compares actual artifacts. Copied pages, inert/comment-only bindings, mismatched coordinates and modified assets reject without a browser. Only this renderer's canonical rounds are supported: regenerate after a renderer upgrade; hand-edited third-party HTML is not silently certified. Behavioral Node tests independently execute the renderer; real WebKit is still required for optical/geometry acceptance.

`jev-gate --platform mobile|desktop|web --stage gallery|spec|traceback` is fail-closed. The default performs a bounded local POST to `http://127.0.0.1:19823/v1/systemone`, using all required taxonomy questions plus actual spec/graph evidence and deterministic errors. `--jev-url` is loopback-only; redirects, proxies and remote endpoints are forbidden. `--jev-timeout` is >0 and <=5s (default 1.5s). Falcon auth uses environment credentials or the local skill source-key file, never receipts. Repository JS/CSS and secrets are not included in gallery evidence.

Every required question must contain finite numeric `score` or `noul` in [0,1], >=0.6; booleans, empty dictionaries, invalid strings and contradictions reject. Any explicit negative/unknown `status` or `decision` rejects even with score 1. Top-level success cannot override a failed/missing answer. `--mock-jev-json` validates the same table; `--no-jev` is explicitly deterministic-only and is incompatible with a mock. A failed deterministic structure always rejects final status.

Native spec firewalls process clause-local polarity: “Never use SVG” is compliant but does not suppress “Implement React DOM” in another clause. Native desktop has the same Web isolation. Scope exploratory HTML under `## HTML sandbox`; resume native directives in a separate implementation section. Web anti-pattern prohibitions are not violations; prohibiting required CJK wrapping/refraction is a contradiction.

## Structured Visual Traceback

Use a JSON file or one fenced JSON record with `schema: visual-traceback/v1`, `findings: [...]`. Classification vocabulary/prose alone is rejected with a schema error; neither arbitrary numbers nor the word “declared” determines responsibility.

```json
{
  "schema": "visual-traceback/v1",
  "findings": [{
    "classification": "IMPL_BUG",
    "constraint": {"declared": true, "source": "spec.md:12", "requirement": "48pt targets"},
    "implementation": {"source": "screen.png#button", "observation": "36pt measured", "conforms": false,
      "assertion": {"operator": "minimum", "required": 48, "observed": 36}},
    "route": {"owner": "Writer", "amend_spec_first": false}
  }]
}
```

Explicit declared constraint + failed implementation assertion => `[IMPL_BUG]`, Writer, no spec amendment. Supported assertions are `minimum`, `maximum`, `equal`, `absent` with required/observed values; evidence must agree with `conforms`. An undeclared missing requirement (`declared:false`, `missing_requirement`, specification source) + implementation following declared constraints (`conforms:true`, source + observation) => `[SPEC_GAP]`, Steering, `amend_spec_first:true`. Semantic adequacy of source observations is evaluated by the additional required `q_traceback_evidence_attribution` live answer; deterministic-only does not independently establish the truth of reported measurements.

An explicit clean result uses `findings:[]` plus `clean:{spec_source,implementation_source,checks:[{requirement,assertion}]}`; every check must pass and have meaningful source references. Findings and clean result cannot coexist.
