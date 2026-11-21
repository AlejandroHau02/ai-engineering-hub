---
name: architecture-review
description: Review software architecture, module boundaries, component coupling, API contracts, scalability and design tradeoffs before significant changes.
---

# Architecture review

Use for cross-component changes, new infrastructure, domain boundaries or major refactors; not minor isolated edits.

1. Inspect architecture, dependencies, data paths, contracts and project-specific constraints.
2. Establish functional and nonfunctional goals; make assumptions explicit.
3. Compare the simplest viable approach with a meaningful alternative when stakes justify it. Consider complexity, coupling, security, cost, operations, migration and reversibility.
4. Recommend a maintainable approach, including what not to build yet.
5. State decision, rationale, risks and validation steps.

Do not claim measured performance or security without evidence.
