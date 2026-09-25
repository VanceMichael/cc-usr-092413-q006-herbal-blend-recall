"""谱系事件: 守恒、单位、可用份额、版本锁、哈希链。"""

from conftest import build_genealogy, must


def test_full_chain_and_hash_chain(client):
    build_genealogy(client)
    events = must(client, "get", "/events")
    assert len(events) == 10
    # 每条事件都记录数量、单位、损耗与操作版本
    for e in events:
        assert e["unit"] and e["op_version"] and e["operator"]
    verify = must(client, "get", "/events/verify")
    assert verify == {"valid": True, "events": 10}


def test_merge_conservation_enforced(client):
    r1 = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    r2 = must(client, "post", "/batches/receive", json={
        "flow_no": "R2", "material": "甘草", "qty": "50", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    bad = client.post("/batches/merge", json={
        "flow_no": "M1",
        "inputs": [{"batch_id": r1, "qty": "60"}, {"batch_id": r2, "qty": "50"}],
        "output_material": "混料", "output_qty": "105", "loss_qty": "4",
        "unit": "kg", "op_version": "MIX-1", "operator": "班长"})
    assert bad.status_code == 422
    assert "守恒" in bad.json()["message"]
    good = must(client, "post", "/batches/merge", json={
        "flow_no": "M2",
        "inputs": [{"batch_id": r1, "qty": "60"}, {"batch_id": r2, "qty": "50"}],
        "output_material": "混料", "output_qty": "105", "loss_qty": "5",
        "unit": "kg", "op_version": "MIX-1", "operator": "班长"})
    assert good["batches"][0]["qty_total"] == "105"


def test_split_conservation_enforced(client):
    r1 = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    bad = client.post("/batches/split", json={
        "flow_no": "S1", "input": {"batch_id": r1, "qty": "100"},
        "outputs": ["60", "39"], "loss_qty": "0", "unit": "kg",
        "op_version": "SPL-1", "operator": "班长"})
    assert bad.status_code == 422
    ok = must(client, "post", "/batches/split", json={
        "flow_no": "S2", "input": {"batch_id": r1, "qty": "100"},
        "outputs": ["60", "39"], "loss_qty": "1", "unit": "kg",
        "op_version": "SPL-1", "operator": "班长"})
    assert [b["qty_total"] for b in ok["batches"]] == ["60", "39"]


def test_unit_mismatch_rejected(client):
    r1 = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    resp = client.post("/batches/split", json={
        "flow_no": "S1", "input": {"batch_id": r1, "qty": "100"},
        "outputs": ["60", "40"], "loss_qty": "0", "unit": "g",
        "op_version": "SPL-1", "operator": "班长"})
    assert resp.status_code == 422


def test_insufficient_available_rejected(client):
    r1 = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    resp = client.post("/batches/split", json={
        "flow_no": "S1", "input": {"batch_id": r1, "qty": "150"},
        "outputs": ["100", "50"], "loss_qty": "0", "unit": "kg",
        "op_version": "SPL-1", "operator": "班长"})
    assert resp.status_code == 422


def test_outbound_requires_finished(client):
    r1 = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "10", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    resp = client.post("/batches/outbound", json={
        "flow_no": "O1", "batch_id": r1, "qty": "1",
        "customer": "客户甲", "operator": "仓管员"})
    assert resp.status_code == 422


def test_expected_version_lock(client):
    r1 = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    stale = client.post("/batches/split", json={
        "flow_no": "S1", "input": {"batch_id": r1, "qty": "100"},
        "outputs": ["60", "40"], "loss_qty": "0", "unit": "kg",
        "op_version": "SPL-1", "operator": "班长",
        "expected_versions": {r1: 5}})
    assert stale.status_code == 409
    ok = must(client, "post", "/batches/split", json={
        "flow_no": "S2", "input": {"batch_id": r1, "qty": "100"},
        "outputs": ["60", "40"], "loss_qty": "0", "unit": "kg",
        "op_version": "SPL-1", "operator": "班长",
        "expected_versions": {r1: 0}})
    assert ok["reused"] is False
    batch = must(client, "get", f"/batches/{r1}")
    assert batch["version"] == 1 and batch["status"] == "CONSUMED"
