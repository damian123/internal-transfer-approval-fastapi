# Internal Transfer Approval Service

Synthetic FastAPI portfolio demonstration for an internal treasury-style workflow. It shows how a small service can make separation of duties, approval state, idempotent execution records, data constraints, and audit history explicit.

This is not a production payment system and does not represent work performed for an employer or client.

## Business problem

A treasury team needs to request internal funds transfers. A request must receive exactly two independent approvals, the requester cannot approve their own request, rejection is final, and execution retries must never create a second database execution record.

## Implemented controls

- Typed FastAPI/Pydantic request and response contracts.
- Exact decimal amounts and explicit three-letter currencies.
- PostgreSQL-ready SQLAlchemy models with positive-amount, enum, uniqueness, and foreign-key constraints.
- Explicit Alembic schema migration with upgrade and downgrade verification.
- Stable client request IDs for duplicate-safe submission.
- Conflict detection when a request ID is reused with different transfer data, including the uniqueness-race recovery path.
- PostgreSQL row locking for atomic decisions, final rejection, and exactly two accepted approvals.
- Separation of duties and two-person approval backed by a unique transfer/approver constraint.
- An API workflow that only appends approval decisions; production database roles would also forbid direct mutation.
- Idempotency keys for duplicate-safe replay plus a unique transfer constraint that prevents a second execution under another key.
- Structured domain errors and 18 tests: ten SQLite API tests, three migration tests, and five PostgreSQL concurrency/constraint tests.
- CI migration-drift and dependency-vulnerability checks.
- Docker Compose path using PostgreSQL 17.

## Run locally

```bash
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the generated OpenAPI interface. The local default is SQLite for a fast demonstration.

For PostgreSQL:

```bash
docker compose up --build
```

## Verify

```bash
poetry install --no-interaction
poetry run alembic upgrade head
poetry run alembic check
poetry run ruff check app migrations scripts tests
poetry run mypy app scripts
poetry run pytest
poetry run pip-audit --local --skip-editable
```

The default test run uses SQLite and skips the PostgreSQL-only concurrency module. CI starts a dedicated PostgreSQL 17 service and runs all 18 tests. For the same coverage locally, point both variables at a dedicated database whose name ends in `_test`:

```bash
export DATABASE_URL=postgresql+psycopg://approval:approval@localhost:5432/approval_test
export POSTGRES_TEST_DATABASE_URL="$DATABASE_URL"
poetry run alembic upgrade head
poetry run pytest
```

## API flow

1. `POST /transfers` with a stable `client_request_id`.
2. `POST /transfers/{id}/decisions` from two different approvers.
3. `POST /transfers/{id}/execute` with an `Idempotency-Key` header.
4. Repeat the execution request to observe the same result and `Idempotent-Replay: true`.

Run the complete synthetic walkthrough without starting a server:

```bash
poetry run python scripts/demo.py
```

Each line is structured JSON showing the HTTP result, transfer state, approval count, execution count, and whether the final call was an idempotent replay.

## Architecture

```mermaid
flowchart LR
    Operator[Treasury operator] -->|Submit request| API[FastAPI boundary]
    Approver1[Risk approver] -->|Decision| API
    Approver2[Finance approver] -->|Decision| API
    API --> Service[Application service]
    Service -->|Transaction + constraints| DB[(PostgreSQL)]
    Service -->|Completed synthetic intent| Provider[Generated demo reference - no outbound call]
    DB --> Audit[Approval and execution audit history]
```

The route layer owns HTTP contracts, the application service owns state transitions and transaction intent, and database constraints backstop concurrency-sensitive invariants. This demo does not call a provider and does not implement an outbox or worker.

## Important decisions

- PostgreSQL `SELECT ... FOR UPDATE` locks serialize decisions and execution for each transfer; database constraints remain the final backstop.
- The execution table is unique by both idempotency key and transfer ID, so changing the key cannot create another execution row.
- A client request ID is bound to its original payload, and an execution idempotency key is globally bound to one transfer; conflicting reuse returns a conflict.
- Provider observations would be stored separately from accepted internal state in a production integration.
- The execution endpoint only generates a synthetic reference inside the database transaction. It provides no exactly-once guarantee for a real external side effect. A real adapter would atomically persist an execution intent and transactional outbox record, then use a durable, idempotent worker.
- SQLite is used only for quick local tests and does not provide the row-locking behavior under test. CI exercises the race invariants on PostgreSQL; production verification would additionally cover isolation configuration, query plans, backups, and recovery.
- The application does not create tables during startup. Alembic owns schema changes; the container applies pending migrations before serving.

## Security and delivery boundary

There is no authentication, tenancy, or role-based authorization in this demo. Actor names and approver identities come from request bodies and must not be trusted as identity claims. The API must not be exposed as a payment control plane without authenticated identities, authorization policy, managed secrets, and database roles that prevent application-level mutation of audit rows.

There is also no transactional outbox, message broker, provider adapter, retry worker, or reconciliation loop. The concurrency guarantees in this repository cover accepted database state only; they do not claim exactly-once delivery to a bank, exchange, or custody provider.

## Production extensions

- Authentication, tenancy, role-based authorization, database privilege hardening, and managed secrets.
- Broader PostgreSQL integration coverage for configured isolation levels and operational failure modes.
- Transactional outbox and durable worker for provider execution.
- Provider timeout classification, status queries, and reconciliation.
- Immutable actor identity from authentication rather than request bodies.
- Structured logs, traces, business metrics, alerting, and operator runbooks.
- Retention, encryption, backup/restore tests, and controlled override workflows.

## Non-goals

- Moving real funds.
- Implementing a specific exchange, custody, bank, KYC, or AML integration.
- Claiming production scale, latency, availability, or regulatory compliance.

## Provenance

Artifact owner: Lars Schouw. Repository account: [`damian123`](https://github.com/damian123). Commits may use the display name Damian; `EVIDENCE.json` records this mapping explicitly.
