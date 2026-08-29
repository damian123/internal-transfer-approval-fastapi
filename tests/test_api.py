from fastapi.testclient import TestClient

TRANSFER = {
    "client_request_id": "treasury-2026-08-28-001",
    "requester": "treasury.operator",
    "source_account": "operating-jpy",
    "destination_account": "custody-usd",
    "currency": "USD",
    "amount": "250000.00",
    "purpose": "Synthetic portfolio demonstration",
}


def create_transfer(client: TestClient) -> dict[str, object]:
    response = client.post("/transfers", json=TRANSFER)
    assert response.status_code == 201
    return response.json()


def approve(client: TestClient, transfer_id: str, approver: str) -> dict[str, object]:
    response = client.post(
        f"/transfers/{transfer_id}/decisions",
        json={"approver": approver, "decision": "approve", "reason": "Policy checks passed"},
    )
    assert response.status_code == 200
    return response.json()


def test_creation_is_idempotent_by_client_request_id(client: TestClient) -> None:
    first = client.post("/transfers", json=TRANSFER)
    replay = client.post("/transfers", json=TRANSFER)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]


def test_request_id_cannot_be_reused_with_different_payload(client: TestClient) -> None:
    first = client.post("/transfers", json=TRANSFER)
    conflicting = client.post(
        "/transfers",
        json={**TRANSFER, "amount": "250001.00"},
    )

    assert first.status_code == 201
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "idempotency_payload_mismatch"


def test_requester_cannot_self_approve(client: TestClient) -> None:
    transfer = create_transfer(client)
    response = client.post(
        f"/transfers/{transfer['id']}/decisions",
        json={
            "approver": TRANSFER["requester"],
            "decision": "approve",
            "reason": "Should be blocked",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "separation_of_duties"


def test_two_independent_approvals_are_required(client: TestClient) -> None:
    transfer = create_transfer(client)
    after_first = approve(client, str(transfer["id"]), "risk.approver")
    after_second = approve(client, str(transfer["id"]), "finance.approver")

    assert after_first["status"] == "submitted"
    assert after_second["status"] == "approved"
    assert len(after_second["approvals"]) == 2


def test_third_approval_is_not_accepted(client: TestClient) -> None:
    transfer = create_transfer(client)
    approve(client, str(transfer["id"]), "risk.approver")
    approve(client, str(transfer["id"]), "finance.approver")

    response = client.post(
        f"/transfers/{transfer['id']}/decisions",
        json={
            "approver": "operations.approver",
            "decision": "approve",
            "reason": "A third decision must not be appended",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "transfer_not_pending"
    observed = client.get(f"/transfers/{transfer['id']}").json()
    assert len(observed["approvals"]) == 2


def test_rejection_is_final(client: TestClient) -> None:
    transfer = create_transfer(client)
    rejected = client.post(
        f"/transfers/{transfer['id']}/decisions",
        json={
            "approver": "compliance.approver",
            "decision": "reject",
            "reason": "Destination review failed",
        },
    )
    second_decision = client.post(
        f"/transfers/{transfer['id']}/decisions",
        json={
            "approver": "finance.approver",
            "decision": "approve",
            "reason": "Attempt after rejection",
        },
    )

    assert rejected.json()["status"] == "rejected"
    assert second_decision.status_code == 409
    assert second_decision.json()["code"] == "transfer_not_pending"


def test_execution_requires_approval(client: TestClient) -> None:
    transfer = create_transfer(client)
    response = client.post(
        f"/transfers/{transfer['id']}/execute",
        headers={"Idempotency-Key": "execution-001"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "transfer_not_approved"


def test_execution_replay_is_duplicate_safe(client: TestClient) -> None:
    transfer = create_transfer(client)
    approve(client, str(transfer["id"]), "risk.approver")
    approve(client, str(transfer["id"]), "finance.approver")

    first = client.post(
        f"/transfers/{transfer['id']}/execute",
        headers={"Idempotency-Key": "execution-001"},
    )
    replay = client.post(
        f"/transfers/{transfer['id']}/execute",
        headers={"Idempotency-Key": "execution-001"},
    )

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert len(first.json()["executions"]) == 1
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    assert len(replay.json()["executions"]) == 1


def test_different_execution_key_cannot_create_a_second_attempt(client: TestClient) -> None:
    transfer = create_transfer(client)
    approve(client, str(transfer["id"]), "risk.approver")
    approve(client, str(transfer["id"]), "finance.approver")

    first = client.post(
        f"/transfers/{transfer['id']}/execute",
        headers={"Idempotency-Key": "execution-primary"},
    )
    conflicting = client.post(
        f"/transfers/{transfer['id']}/execute",
        headers={"Idempotency-Key": "execution-different"},
    )

    assert first.status_code == 200
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "transfer_already_executed"
    observed = client.get(f"/transfers/{transfer['id']}").json()
    assert len(observed["executions"]) == 1


def test_execution_key_cannot_cross_transfers(client: TestClient) -> None:
    first = create_transfer(client)
    approve(client, str(first["id"]), "risk.approver")
    approve(client, str(first["id"]), "finance.approver")
    client.post(
        f"/transfers/{first['id']}/execute",
        headers={"Idempotency-Key": "execution-shared"},
    )

    second_payload = {**TRANSFER, "client_request_id": "treasury-2026-08-28-002"}
    second = client.post("/transfers", json=second_payload).json()
    approve(client, str(second["id"]), "risk.approver")
    approve(client, str(second["id"]), "finance.approver")
    response = client.post(
        f"/transfers/{second['id']}/execute",
        headers={"Idempotency-Key": "execution-shared"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"
