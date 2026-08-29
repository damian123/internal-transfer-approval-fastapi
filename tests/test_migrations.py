from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_initial_migration_upgrades_and_downgrades(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-test.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url))
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "approval_decisions",
        "execution_attempts",
        "transfer_requests",
    }
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("transfer_requests")
    } == {"ck_transfer_amount_positive", "ck_transfer_status_valid"}
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("approval_decisions")
    } == {"ck_approval_decision_valid"}
    assert {
        constraint["name"] for constraint in inspector.get_unique_constraints("execution_attempts")
    } == {"uq_execution_idempotency_key", "uq_execution_transfer_id"}

    command.check(config)

    command.downgrade(config, "base")
    inspector = inspect(create_engine(database_url))
    assert inspector.get_table_names() == ["alembic_version"]


def test_environment_database_url_accepts_percent_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "migration%25encoded.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    assert "transfer_requests" in inspect(create_engine(database_url)).get_table_names()
    command.downgrade(config, "base")


def test_enum_data_is_normalized_across_the_constraint_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "enum-migration-test.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260828_01")

    transfer_id = uuid4().hex
    with create_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO transfer_requests (
                    id, client_request_id, requester, source_account,
                    destination_account, currency, amount, purpose, status,
                    created_at, updated_at
                ) VALUES (
                    :id, 'enum-migration', 'requester', 'source',
                    'destination', 'USD', 1, 'migration coverage', 'SUBMITTED',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": transfer_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO approval_decisions (
                    id, transfer_id, approver, decision, reason, decided_at
                ) VALUES (
                    :id, :transfer_id, 'approver', 'APPROVE',
                    'migration coverage', CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": uuid4().hex, "transfer_id": transfer_id},
        )

    command.upgrade(config, "head")
    with create_engine(database_url).connect() as connection:
        assert connection.scalar(text("SELECT status FROM transfer_requests")) == "submitted"
        assert connection.scalar(text("SELECT decision FROM approval_decisions")) == "approve"

    command.downgrade(config, "20260828_01")
    with create_engine(database_url).connect() as connection:
        assert connection.scalar(text("SELECT status FROM transfer_requests")) == "SUBMITTED"
        assert connection.scalar(text("SELECT decision FROM approval_decisions")) == "APPROVE"

    command.downgrade(config, "base")
