"""API 冒烟: 幂等、隔离、评估审批与追溯端到端。"""

from fastapi.testclient import TestClient

from app.main import create_app


def _client():
    return TestClient(create_app())


def _receive_payload(key="RCV-1", qty="100"):
    return {
        "event_type": "RECEIVE",
        "idempotency_key": key,
        "source": "MES-01",
        "operation_version": "SOP-2.1",
        "unit": "kg",
        "actor": "op01",
        "outputs": [
            {"batch_id": "R1", "material_code": "MAT", "kind": "RAW", "quantity": qty}
        ],
    }


def test_health():
    assert _client().get("/health").json() == {"status": "ok"}


def test_idempotent_replay_and_quarantine_over_http():
    client = _client()
    first = client.post("/api/transformations", json=_receive_payload())
    assert first.status_code == 200
    assert first.json()["status"] == "APPLIED"

    replay = client.post("/api/transformations", json=_receive_payload())
    assert replay.json()["status"] == "REPLAYED"
    assert replay.json()["event"]["event_id"] == first.json()["event"]["event_id"]

    conflict = client.post("/api/transformations", json=_receive_payload(qty="200"))
    assert conflict.status_code == 409
    quarantine_id = conflict.json()["quarantine_id"]
    listed = client.get("/api/quarantine").json()
    assert [q["quarantine_id"] for q in listed] == [quarantine_id]

    batch = client.get("/api/batches/R1").json()
    assert batch["quantity"] == "100"  # 冲突上报未改变库存


def test_conservation_violation_is_422():
    client = _client()
    client.post("/api/transformations", json=_receive_payload())
    bad_split = {
        "event_type": "SPLIT",
        "idempotency_key": "SPL-1",
        "source": "MES-01",
        "operation_version": "SOP-2.1",
        "unit": "kg",
        "actor": "op01",
        "inputs": [{"batch_id": "R1", "quantity": "100"}],
        "outputs": [
            {"batch_id": "A", "material_code": "MAT", "kind": "RAW", "quantity": "60"},
            {"batch_id": "B", "material_code": "MAT", "kind": "RAW", "quantity": "60"},
        ],
    }
    assert client.post("/api/transformations", json=bad_split).status_code == 422


def test_assessment_flow_and_trace_over_http():
    client = _client()
    client.post("/api/transformations", json=_receive_payload())
    ship = {
        "event_type": "SHIP",
        "idempotency_key": "SHP-1",
        "source": "WMS",
        "operation_version": "SOP-2.1",
        "unit": "kg",
        "actor": "op02",
        "inputs": [{"batch_id": "R1", "quantity": "30"}],
        "customer": "华东药房",
    }
    assert client.post("/api/transformations", json=ship).status_code == 200

    assessment = client.post(
        "/api/assessments",
        json={
            "root_batch_id": "R1",
            "items": [{"batch_id": "R1", "quantity": "100"}],
            "basis": "校准撤销 CAL-1.2",
            "proposed_by": "qa01",
        },
    ).json()
    assert assessment["status"] == "PENDING_APPROVAL"

    # 提报人不能自审
    assert (
        client.post(
            f"/api/assessments/{assessment['assessment_id']}/approve",
            json={"reviewer": "qa01"},
        ).status_code
        == 422
    )
    approved = client.post(
        f"/api/assessments/{assessment['assessment_id']}/approve",
        json={"reviewer": "qa02"},
    ).json()
    assert approved["status"] == "APPROVED"

    batch = client.get("/api/batches/R1").json()
    assert batch["frozen"] == "70"      # 在库 70 全部冻结
    assert batch["available"] == "0"

    trace = client.get("/api/trace/forward/R1").json()
    assert trace["shipments"][0]["customer"] == "华东药房"
    assert trace["notifications"][0]["quantity"] == "30"
    assert any("待处置" in item for item in trace["outstanding"])
