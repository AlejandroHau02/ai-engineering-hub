---
name: backend-engineering
description: Implement and review APIs, domain logic, database integrations, messaging and background jobs with security, reliability and testability.
---

# Backend engineering

Use for service or API changes, data persistence, async jobs, integrations and multitenancy.

1. Trace request/data flow, existing contracts, authorization and test coverage.
2. Enumerate happy path and relevant failure modes: invalid input, retries, duplicates, timeouts, partial failures.
3. Consider authentication, tenant boundaries, validation, transaction consistency, idempotency, concurrency, safe logging and observability.
4. Prefer compatible API/schema evolution and limited, reversible migrations.
5. Add focused tests for success and failure paths. Verify what actually ran.
6. Report changes, remaining risks and deployment/rollback implications.

Do not add infrastructure without evidence it is needed.
