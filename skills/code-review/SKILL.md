---
name: code-review
description: Independently review changed code for concrete defects, authorization gaps, frontend accessibility, state regressions, security and missing validation.
---

# Evidence-based code review

Use for PRs, risky changes, post-implementation validation and regression investigations. For trivial changes, keep it focused and short.

1. Examine the **actual diff**, relevant callers, contracts, tests, runtime environment and user impact; do not rely on a generated summary.
2. Look for authorization failures (including same-tenant insufficient role), state-machine bugs, stale async results, idempotency, concurrency, data loss, compatibility and unsafe DOM rendering.
3. When reviewing UI, check keyboard/focus/history behavior, error/empty states, forced-colors, short-height and zoom layouts, long content and accessibility. Use a real browser where available; DOM simulation alone cannot establish layout or focus behavior.
4. Cite file/line and a concrete reproduction for each finding. Separate **confirmed** defects from plausible risks and stylistic preferences. Prioritize severity and propose the smallest effective fix.
5. State exactly which checks you executed. Do not infer a pass for devices, screen readers or browsers you could not test, and do not invent failures to fill a quota.
6. If an independent second model could materially reduce risk, recommend read-only cross-review; avoid routine multi-model ceremony and costs for small tasks.

A review-only request does not authorize edits, merges or deployment.
