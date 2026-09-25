"""设备重复上报: 相同内容沿用原记录, 内容不同隔离。"""

from conftest import must

RECEIVE = {
    "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
    "op_version": "RCV-1", "operator": "仓管员",
}


def test_same_flow_same_payload_reuses_record(client):
    first = must(client, "post", "/batches/receive", json=RECEIVE)
    second = must(client, "post", "/batches/receive", json=dict(RECEIVE))
    assert second["reused"] is True
    assert second["event"]["id"] == first["event"]["id"]
    assert second["batches"][0]["id"] == first["batches"][0]["id"]
    # 台账与库存不重复记账
    assert len(must(client, "get", "/events")) == 1
    batches = must(client, "get", "/batches")
    assert len(batches) == 1 and batches[0]["qty_total"] == "100"


def test_same_flow_different_payload_isolated(client):
    first = must(client, "post", "/batches/receive", json=RECEIVE)
    conflict = client.post("/batches/receive", json={**RECEIVE, "qty": "999"})
    assert conflict.status_code == 409
    isolation_id = conflict.json()["details"]["isolation_id"]

    isolated = must(client, "get", "/isolated-reports")
    assert len(isolated) == 1
    assert isolated[0]["id"] == isolation_id
    assert isolated[0]["flow_no"] == "R1"
    assert isolated[0]["existing_event_id"] == first["event"]["id"]

    # 隔离不污染谱系与库存
    assert len(must(client, "get", "/events")) == 1
    batches = must(client, "get", "/batches")
    assert len(batches) == 1 and batches[0]["qty_total"] == "100"

    # 相同内容再次上报仍沿用原记录
    again = must(client, "post", "/batches/receive", json=dict(RECEIVE))
    assert again["reused"] is True


def test_conflicting_report_itself_idempotent(client):
    must(client, "post", "/batches/receive", json=RECEIVE)
    bad = {**RECEIVE, "qty": "999"}
    c1 = client.post("/batches/receive", json=bad)
    c2 = client.post("/batches/receive", json=bad)
    assert c1.status_code == c2.status_code == 409
    assert c1.json()["details"]["isolation_id"] == c2.json()["details"]["isolation_id"]
    assert len(must(client, "get", "/isolated-reports")) == 1
