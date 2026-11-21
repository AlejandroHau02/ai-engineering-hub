---
name: code-review
description: Independently examine diffs and PRs for concrete defects, security issues, regressions, edge cases and missing tests.
---

# Evidence-based code review

Use for PRs, risky changes, post-implementation verification and regression investigations.

1. Inspect the actual diff, callers, contracts and relevant tests, not just its summary.
2. Seek concrete failures in control flow, permissions, state transitions, concurrency, data loss, backward compatibility and accessibility.
3. Report findings by severity with file/line when possible, trigger, impact and practical fix.
4. Separate verified defects from hypotheses and stylistic opinions. Do not manufacture findings.
5. Report missing coverage and unverified assumptions.

A review-only request does not authorize modifications. Never claim to have run tests without running them.
