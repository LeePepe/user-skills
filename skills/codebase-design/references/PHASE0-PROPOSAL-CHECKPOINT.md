# Phase 0: Proposal -> Checkpoint -> Pop

Phase 0 is a human decision seam for disagreements about domain language, a deep
module's Interface or Seam, an ADR-qualified trade-off, or a Visual Mode. Agents
write competing proposals; only the owner creates a Checkpoint. `pop` is a
non-destructive read and succeeds only when its `proposals_sha` still matches the
current proposal set.

```text
spec_gate.py push       -> handoff/<goal>/proposals.json
human checkpoint        -> latest_checkpoint.proposals_sha
spec_gate.py pop        -> release the approved decision, or exit 1 as stale
```

Each proposal records its Interface summary, Seam placement, dependency
category, agent, model, and recommendation. A new or edited proposal invalidates
an older Checkpoint. Cross-topic proposal IDs are rejected rather than silently
re-associated.

## Optimistic lock contract

`spec.md` and `design.md` carry a positive integer `version`. Their digest is
computed by `spec_gate.py` from canonical JSON frontmatter (excluding
`spec_sha`, `design_sha`, and `updated_at`) plus a normalized Markdown body.
This removes the self-reference cycle: writing the digest back does not change
the digest input.

`stamp --expected-version N --expected-sha H` performs a file-locked compare and
swap. A mismatch returns a deterministic `409 SPEC_VERSION_CONFLICT` JSON result
and does not write. The lock is `<handoff-dir>/.queue.lock`; replacement uses a
temporary file followed by `os.replace`.

`verify-lock` checks the directional chain:

```text
spec.version/spec_sha -> design.parent_spec_version/parent_spec_sha
                       -> Task Mandate spec_sha/design_sha
```

Older downstream artifacts are retained for auditability but reported as
`STALE_DESIGN` or `STALE_MANDATE` and must not release a Writer.
