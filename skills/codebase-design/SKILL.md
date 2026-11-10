---
name: codebase-design
description: Shared vocabulary and discipline for designing deep modules and sharpening domain models. Use when designing or improving module interfaces, finding deepening opportunities, deciding seam placement, making code more testable or AI-navigable, refining ubiquitous language, writing or editing CONTEXT.md, or recording an ADR.
---

# Codebase Design & Domain Modeling

Design **deep modules** — substantial behaviour behind a small interface, placed at a clean seam, testable through that interface — while actively sharpening the project's **domain model** (`CONTEXT.md` and `docs/adr/`). The aim is leverage for callers, locality for maintainers, unambiguous ubiquitous language, and testability for everyone.

## Architectural Glossary

Use these terms exactly — don't substitute "component," "service," "API," or "boundary."

- **Module** — anything with an interface and an implementation (function, class, package, or slice). _Avoid_: unit, component, service.
- **Interface** — everything a caller must know to use the module correctly: type signature, invariants, ordering constraints, error modes, configuration, and performance characteristics. _Avoid_: API, signature.
- **Implementation** — the body of code inside a module. Distinct from **Adapter**: reach for "adapter" when the seam is the topic, "implementation" otherwise.
- **Depth** — leverage at the interface: the amount of behaviour a caller or test exercises per unit of interface learned. **Deep** = large behaviour behind a small interface; **shallow** = interface nearly as complex as the implementation.
- **Seam** _(Michael Feathers)_ — a place where you can alter behaviour without editing in that place; the *location* where a module's interface lives. _Avoid_: boundary (overloaded with DDD bounded context).
- **Adapter** — a concrete thing that satisfies an interface at a seam (describes *role*, not internal substance).
- **Leverage** — what callers get from depth: more capability per unit of interface learned across N call sites and M tests.
- **Locality** — what maintainers get from depth: changes, bugs, and verification concentrate in one place.

## Deep vs Shallow & Principles

```text
Deep Module (Prefer)                  Shallow Module (Avoid)
┌─────────────────────┐               ┌─────────────────────────────────┐
│   Small Interface   │ ← Few methods │         Large Interface         │
├─────────────────────┤               ├─────────────────────────────────┤
│ Deep Implementation │ ← Hidden logic│       Thin Pass-Through         │
└─────────────────────┘               └─────────────────────────────────┘
```

- **Depth is a property of the interface, not implementation line ratio.** A deep module can have internal seams (private to its implementation) as well as the external seam at its interface.
- **The deletion test.** If deleting a module makes complexity vanish, it was a pass-through. If complexity reappears across N callers, it earned its keep.
- **The interface is the test surface.** Callers and tests cross the same seam.
- **One adapter = hypothetical seam; two adapters = real seam.** Don't introduce a seam unless something varies across it.

## Designing for Testability

1. **Accept dependencies, don't create them**; **return results, don't produce side effects**; keep **small surface area**.

```typescript
// Testable: injected seam + pure result
function processOrder(order: Order, paymentGateway: PaymentPort): Receipt {}
function calculateDiscount(cart: Cart): Discount {}

// Hard to test: hidden instantiation + in-place mutation
function processOrderBad(order: Order): void {
  const gateway = new StripeGateway();
  order.total -= discount;
}
```

## Domain Modeling (`CONTEXT.md` & ADRs)

Actively sharpen the domain model as you design: challenge conflicting terms, sharpen vague/overloaded words ("Customer vs User?"), stress-test relationships with concrete edge-case scenarios, and cross-reference user claims against the code.

```text
Single-context repo:                  Multi-context repo (CONTEXT-MAP.md):
/                                     /
├── CONTEXT.md                        ├── CONTEXT-MAP.md
├── docs/adr/0001-slug.md             ├── docs/adr/          ← system-wide ADRs
└── src/                              └── src/<context>/     ← CONTEXT.md + docs/adr/
```

- **Create files lazily** only when the first term or ADR is resolved.
- **Decision placement**: canonical terminology (`CONTEXT.md` — glossary only, zero implementation details) uses [CONTEXT-FORMAT.md](references/CONTEXT-FORMAT.md); hard-to-reverse trade-offs (`docs/adr/`) use [ADR-FORMAT.md](references/ADR-FORMAT.md); feature-local investigation lives in `research.md`; outcomes in `spec.md`; implementation design in `plan.md`.
- **Offer ADRs sparingly** — only when all three hold: (1) **Hard to reverse**, (2) **Surprising without context**, and (3) **Result of a real trade-off**.

## Going Deeper

- **Deepening a cluster given its dependencies** — see [DEEPENING.md](references/DEEPENING.md): dependency categories, seam discipline, and replace-don't-layer testing.
- **Exploring alternative interfaces** — see [DESIGN-IT-TWICE.md](references/DESIGN-IT-TWICE.md): parallel sub-agent exploration compared on depth, locality, and seam placement.
- **Domain glossary & ADR templates** — see [CONTEXT-FORMAT.md](references/CONTEXT-FORMAT.md) and [ADR-FORMAT.md](references/ADR-FORMAT.md).
- **Phase 0 disagreements & CAS locks** — use the [Proposal -> Checkpoint -> Pop protocol](references/PHASE0-PROPOSAL-CHECKPOINT.md) and `scripts/spec_gate.py` (`push`, `checkpoint`, `pop`, `stamp`, `verify-lock`).
