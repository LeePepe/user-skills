# Modernization Dimensions

Modernization is subtractive as well as additive. The deliverable is product code, never a **No Code Demo**.

| Dimension | Question |
|---|---|
| hierarchy | Is the first task visible without explanation? |
| navigation | Is the return path native and stable? |
| state | Are loading, empty, error, retry, and success explicit? |
| density | Does spacing protect the P0 and numeric content? |
| platform | Does desktop differ from mobile where conventions require it? |
| accessibility | Do contrast, Dynamic Type, keyboard, and Reduce Motion pass? |

Write concrete Negative DOM/View Assertions and an Obsolete Test Manifest in `design.md`.

For pre-implementation `audit-spec --strict-spec-gaps`, scope the test decision to a `Deprecated Test Clauses` / `Obsolete Test Manifest` inline clause (one physical line) or heading body (ending before the next Markdown heading, at any level). Cite a concrete test artifact (`LegacyTests.swift:42`, `legacy_spec.ts`), identifier (`test_removeLegacy`, `testLegacyNavigation`, or a backticked assertion), or an explicit scoped N/A/none/greenfield decision. An empty section, unrelated `Analytics: N/A`, or vague `tests TBD` is not evidence. These gap checks are advisory without `--strict-spec-gaps`; existing subtractive hard rules still require concrete obsolete-test references.
