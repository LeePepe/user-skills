# Web SaaS Data Workspace

## slot: web-saas-data-workspace

Use `Independent-Custom` with `@nocoo/basalt` AppShell and ContentIsland. Keep a
single CSS entry, a `1200px` maximum workspace, `tabular-nums` for metrics, and
separate Recharts 3 `chart-1..6` series colors from semantic status colors.

For Dashboard and data-workspace scenarios, `Designer` may query
`design-studio/scripts/design_catalog.py search` / `recommend` for `IP-*`
patterns (chart cross-filtering, orthogonal filter/sort, master-detail linkage,
scoped empty recovery, and status-to-summary aggregation). Adapt the reference
to the product while preserving the trigger, linkage, and recovery behavior
chosen in `design.md`; downstream `Writer` executes that specification
strictly, without replacing linked states with static placeholders.
