# Limitations

This service demonstrates approval state, idempotent execution records, and database-backed concurrency controls. It is not a payment system.

## Security boundary

There is no authentication, tenancy, or role-based authorization. Actor names and approver identities come from request bodies. Do not expose the API as a funds-movement control plane without authenticated identities, authorization policy, managed secrets, and database roles that prevent the application from mutating audit rows.

## Execution boundary

There is no transactional outbox, message broker, provider adapter, retry worker, or reconciliation loop. Concurrency guarantees cover accepted database state only. They do not claim exactly-once delivery to a bank, exchange, or custody provider.

The execution endpoint generates a reference inside the database transaction. A real adapter would atomically persist an execution intent and outbox record, then a durable worker would call the provider idempotently.

## Local versus CI

SQLite ignores `SELECT ... FOR UPDATE`. Race invariants are exercised on PostgreSQL in CI. Production verification would also cover isolation configuration, query plans, backups, and recovery.

## Not in scope

- Moving real funds.
- A specific exchange, custody, bank, KYC, or AML integration.
- Production scale, latency, availability, or regulatory compliance claims.

## Production extensions

- Authentication, tenancy, RBAC, database privilege hardening, and managed secrets.
- Transactional outbox and a durable worker for provider execution.
- Provider timeout classification, status queries, and reconciliation.
- Immutable actor identity from authentication rather than request bodies.
- Structured logs, traces, business metrics, alerting, and operator runbooks.
