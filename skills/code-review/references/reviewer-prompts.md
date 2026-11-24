# Reviewer Prompt Contracts

Use these contracts as prompt payloads. Include exact diff commands or bounded diff text, commit list, and source paths. Ask reviewers for concrete findings only; no praise or summary prose.

## Spec reviewer

Review the complete change against the supplied issue and Spec Kit artifacts.

Return every instance of:

- required intent or acceptance that is missing, partial, or implemented incorrectly;
- behavior or files outside declared scope;
- plan/contract constraints contradicted by the implementation;
- `tasks.md` items claimed complete without corresponding implementation artifacts.

For each finding return the output-contract fields. Quote the requirement source. Do not apply repository-style preferences unless they are also specification requirements.

## Layer Standards reviewer

Review only the supplied layer diff. Treat the constitution, repository standards, and this layer's tech-context as authoritative.

Check:

- every `red_line` projected onto the layer;
- imports/dependencies against `depends_on`;
- intra-layer dependencies against `roles` and top-level `canonical_roles`;
- changes outside the layer's `owns` responsibility;
- required test artifacts or coverage explicitly named by supplied authority;
- security, privacy, correctness, failure paths, documentation, and code-quality smells supported by cited repository rules.

Return no finding that requires inspecting another layer. Instead return a cross-layer concern with evidence for the aggregator.

Inspect content only. Do not execute build, test, lint, hook, simulator, hardware, or CI commands; do not evaluate whether FS ran local tests or whether a gate passed. Do not emit optional improvements or generic preferences.

## Cross-layer reviewer

Review only relationships between changed layers and unmapped/top-level files.

Check:

- package dependency direction and `depends_on` consistency;
- whether changed lower layers account for every `depended_by` consumer affected by their interface change;
- whether each layer maps to an explicit Spec Kit task and can be independently verified;
- whether a cross-layer change that should be split has been hidden in one task/commit;
- whether an unmapped file is intentionally top-level or indicates missing/stale layer context.

Do not repeat layer-local style findings.

## UI Visual Traceback (`code-review` + `my-designer`)

When the review surface includes UI changes and rendered screenshots (recorded in the Writer's R10 receipt via Apple `XCTest`/`simctl`/`ImageRenderer` or Web Playwright), load `my-designer` and inspect both the code diff and the rendered screenshots. Prefix every visual/interaction finding `title` or `body` with its mandatory attribution tag:

- **`[IMPL_BUG]`**: The Writer violated an explicit requirement in `design.md`/`spec.md` or a deterministic `design_contract.py` gate → route directly to the Writer to fix against the existing `design.md`.
- **`[SPEC_GAP]`**: The Writer implemented `design.md` as written, but the rendered pixels reveal Phase 0 omissions (crowding, CJK line-wrapping, column misalignment, or visual hierarchy conflict) → do not ask the Writer to guess undeclared constraints; require `Steering` to amend `design.md` first (re-stamping `spec_sha`) before the Writer implements the delta.

## Clean-room common contract

This is a clean-room review. Use only the supplied immutable packet and authoritative files. Do not inspect Writer/TL transcripts, R10 prose, comments, mutable repository state, or other lane packets. Do not modify files or communicate with Writer. Return only the JSON array required below; do not emit praise, a verdict, or summary prose.

## Four independent review lanes

### Spec-Fidelity lane

Input: full diff, commits, issue, and authoritative `spec.md`/`design.md`/`plan.md`/`tasks.md`. Check only intent, acceptance, scope, deprecated clauses, and contract compliance. Cite the requirement source for each finding. Do not infer repository style or security concerns unless the supplied specification requires them.

### Adversarial/Security/Performance lane

Input: code diff, constitution/red-lines, layer context, and deterministic dependency/static results; no Writer rationale. Check authentication/authorization, privacy, injection, data loss, failure paths, concurrency, resource bounds, abuse resistance, and material performance regressions. Return only evidence-backed findings; do not restate Spec-Fidelity findings.

### Design-Traceback lane

Input: design authority, rendered screenshots, geometry/localization/design-contract results, and the relevant UI diff; no Writer receipt prose. Check visual hierarchy, clipping, wrapping, localization, interaction state, and geometry. Prefix every finding with `[IMPL_BUG]` when code violates an explicit contract or `[SPEC_GAP]` when the design authority omitted the needed rule.

### Layer/Cross-layer lane

Input: per-layer diffs, layer contexts, dependency map, and Spec Kit task mapping. Check ownership, `depends_on`/`depended_by`, dependency direction, unmapped files, and whether the change can be independently verified. Do not emit layer-local style or security findings; route those to the appropriate lane.

## Reviewer return format

Return a JSON array of finding objects matching `output-contract.md`. Return `[]` when there are no findings. Do not produce a verdict; aggregation owns axis results.
