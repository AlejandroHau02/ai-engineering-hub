---
name: backend-engineering
description: Implement and review APIs, domain logic, database integrations and background jobs with distinct authentication, tenant isolation, operation-level authorization and reliability checks.
---

# Backend engineering

Use for API changes, domain behavior, messaging, persistence, integrations, background jobs and multitenancy.

1. Trace actual request/data flow, contracts, identity context, authorization rules and tests before proposing changes.
2. Separate **authentication**, **tenant isolation**, and **permission for this operation on this resource**. A valid login or matching organization ID does not imply the user may modify every row in that organization. Do not defer required authorization to a later release.
3. Do not trust client-supplied organization IDs or object IDs as proof of access. Derive trusted context server-side; scope reads and **writes** to authorized resources. Where permission varies by owner/role/status, enforce that too.
4. Enumerate meaningful failure paths: invalid input, no session, insufficient permission, cross-tenant attempt, retries, duplicates, timeouts, partial failure and concurrency.
5. Prefer compatible API/schema evolution, narrowly scoped atomic updates/transactions, idempotent effects where needed and observable but non-sensitive logs. Avoid leaking other tenants' resource existence.
6. Add focused success/failure tests, including a user from the same tenant **without permission**, not only an attacker from another tenant. Confirm failure does not mutate data.
7. Report verified behavior, assumptions, tests actually run and rollback/deployment implications.

Keep the minimum secure implementation proportionate; do not add a new authorization framework if existing mechanisms suffice.
