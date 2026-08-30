# Internal transfer approval

A FastAPI service for internal funds-transfer requests that need two independent approvals, a durable audit trail, and execution retries that cannot insert a second database row.

Portfolio project using fictional data. It is not connected to an employer, client, or production system.

```mermaid
flowchart LR
    Operator[Treasury operator] -->|Submit request| API[FastAPI boundary]
    Approver1[Risk approver] -->|Decision| API
    Approver2[Finance approver] -->|Decision| API
    API --> Service[Application service]
    Service -->|Transaction + constraints| DB[(PostgreSQL)]
    Service -->|Execution row| Record[No outbound payment call]
    DB --> Audit[Approval and execution audit history]
```

Routes own HTTP contracts. The application service owns state transitions. Database constraints backstop the concurrency-sensitive invariants.

## Capabilities

- Submit a transfer with a stable client request ID; replay is duplicate-safe, and a changed payload on the same ID is a conflict.
- Require exactly two independent approvals. The requester cannot approve their own request. Rejection is final.
- Execute with an `Idempotency-Key`. A second call returns the same result and `Idempotent-Replay: true`.
- Enforce a unique execution row per transfer, so switching keys cannot create another attempt.
- Lock the transfer row on PostgreSQL (`SELECT ... FOR UPDATE`) and keep uniqueness, enum, and positive-amount constraints in the schema.

## Run

```bash
poetry install --no-interaction
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs`. The default database is SQLite so the API can start without Docker.

PostgreSQL 17 path:

```bash
docker compose up --build
```

Walk the HTTP flow without a server:

```bash
poetry run python scripts/demo.py
```

Typical sequence: `POST /transfers`, two `POST /transfers/{id}/decisions` from different approvers, then `POST /transfers/{id}/execute` with an `Idempotency-Key`. Repeat execute to see the replay header.

## Verification

```bash
poetry install --no-interaction
poetry run alembic upgrade head
poetry run alembic check
poetry run ruff check app migrations scripts tests
poetry run mypy app scripts
poetry run pytest
poetry run pip-audit --local --skip-editable
```

Default pytest uses SQLite and skips the PostgreSQL-only concurrency module. CI starts PostgreSQL 17 and runs all 18 tests (10 API, 3 migration, 5 concurrency/constraint), plus migration-drift and dependency-vulnerability checks.

To run the same set locally, point both URLs at a dedicated database whose name ends in `_test`:

```bash
export DATABASE_URL=postgresql+psycopg://approval:approval@localhost:5432/approval_test
export POSTGRES_TEST_DATABASE_URL="$DATABASE_URL"
poetry run alembic upgrade head
poetry run pytest
```

## Design

- PostgreSQL row locks serialize decisions and execution; unique constraints remain the last backstop if a race slips through.
- A client request ID is bound to its original payload. An execution idempotency key is globally bound to one transfer.
- The execution endpoint writes a reference inside the same database transaction. It does not call a bank or custody provider.
- SQLite is for fast local tests only and does not provide the row-locking behavior under test. Alembic owns schema changes; the container applies pending migrations before serving.

## Limitations

No authentication, no payment adapter, no outbox. Actor names arrive in the request body and must not be trusted as identity. See [LIMITATIONS.md](LIMITATIONS.md).
