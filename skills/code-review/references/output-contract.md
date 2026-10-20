# Output Contract

## Finding schema

```json
{
  "axis": "standards | spec",
  "severity": "P0 | P1 | P2",
  "layer": "<layer-name> | cross-layer | unmapped | global",
  "path": "relative/path:line",
  "title": "imperative, specific summary",
  "source": "source/document.md:line or frontmatter field",
  "evidence": "what the pinned diff does and why it violates the source",
  "remediation": "smallest change that resolves the finding"
}
```

Reject a finding when it lacks diff evidence or a repository/spec source. Generic preference is not a finding.

## Severity

- **P0:** constitutional, security, privacy, data-loss, destructive-behavior, or architectural red-line breach.
- **P1:** incorrect or missing required acceptance, behavior, contract, explicitly required test artifact, layer dependency, or material scope.
- **P2:** repository-backed, actionable maintainability or documentation work that policy explicitly permits deferring. It must name the follow-up action and owner; otherwise reject it.

Do not hunt for optional improvements. A material product or architecture opportunity discovered incidentally without current authority is a **decision candidate**, not a finding. Keep it outside both axes and the verdict; include its evidence, affected scope, and unresolved choice for the requester to carry to Team Lead. Omit style, taste, and generic best-practice suggestions entirely.

## Final report

```markdown
# Code Review

Review surface: <fixed-point>..<target> (<mode>)
Context: spec-kit=<full|degraded>, layers=<full|degraded>

## Coverage gaps
- <missing source or unmapped path; omit section when none>

## Standards
### <layer>
- [P1] path:line — title
  - Source: ...
  - Evidence: ...
  - Remediation: ...

### Cross-layer
...

## Spec
...

## Decision candidates
- <material unresolved product/architecture opportunity for Team Lead; omit when none>

## Summary
Standards: <count>, worst=<severity|none>
Spec: <count>, worst=<severity|none>
```

Do not turn the two axis summaries into one averaged score. `PASS` requires zero P0/P1 findings in both axes and no unexplained coverage gap; P2 findings remain visible follow-ups.

Verdict mapping for the owning agent:

- any P0 → `FAIL`;
- no P0 and any P1 → `CHANGES REQUESTED`;
- no P0/P1 and any actionable P2 → `PASS WITH FOLLOW-UP`;
- no P0/P1/P2 → `PASS`;
- an unpinned revision or missing required authority → `INCONCLUSIVE`.

Test execution and gate state never affect this mapping. Shipping gates are owned outside review.
