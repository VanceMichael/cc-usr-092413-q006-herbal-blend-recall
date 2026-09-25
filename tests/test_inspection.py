"""检验绑定方法/样品范围/校准版本, 版本替代与校准撤销。"""

from conftest import must


def _receive(client, flow="R1", qty="100"):
    return must(client, "post", "/batches/receive", json={
        "flow_no": flow, "material": "黄芪", "qty": qty, "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]


def test_inspection_binds_method_scope_calibration(client):
    bid = _receive(client)
    ins = must(client, "post", "/inspections", json={
        "batch_id": bid, "method": "HPLC含量测定", "sample_scope": "3袋混样200g",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    assert ins["method"] == "HPLC含量测定"
    assert ins["sample_scope"] == "3袋混样200g"
    assert ins["calibration_version"] == "CAL-1"
    assert ins["calibration_revoked"] is False
    assert ins["version"] == 1 and ins["supersedes"] is None


def test_superseding_inspection_versions(client):
    bid = _receive(client)
    v1 = must(client, "post", "/inspections", json={
        "batch_id": bid, "method": "HPLC含量测定", "sample_scope": "3袋",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    v2 = must(client, "post", "/inspections", json={
        "batch_id": bid, "method": "HPLC含量测定", "sample_scope": "3袋",
        "calibration_version": "CAL-2", "result": "FAIL", "inspector": "复核员"})
    assert v2["version"] == 2 and v2["supersedes"] == v1["id"]
    # 基于过期版本的替代被拒绝
    stale = client.post("/inspections", json={
        "batch_id": bid, "method": "HPLC含量测定", "sample_scope": "3袋",
        "calibration_version": "CAL-3", "result": "PASS", "inspector": "检验员",
        "supersedes": v1["id"]})
    assert stale.status_code == 409
    # 旧版本保留可查
    records = must(client, "get", "/inspections", params={"batch_id": bid})
    assert [r["version"] for r in records] == [1, 2]


def test_revoked_calibration_rejected_and_idempotent(client):
    bid = _receive(client)
    must(client, "post", "/inspections", json={
        "batch_id": bid, "method": "HPLC含量测定", "sample_scope": "3袋",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    rev = must(client, "post", "/calibrations/revoke", json={
        "calibration_version": "CAL-1", "revoked_by": "质量负责人",
        "reason": "校准曲线错误"})
    assert rev["reused"] is False
    assert rev["affected_batches"] == [bid]
    # 撤销幂等
    again = must(client, "post", "/calibrations/revoke", json={
        "calibration_version": "CAL-1", "revoked_by": "质量负责人",
        "reason": "校准曲线错误"})
    assert again["reused"] is True and again["affected_inspections"] == []
    # 已撤销校准不得再用于新检验
    blocked = client.post("/inspections", json={
        "batch_id": bid, "method": "HPLC含量测定", "sample_scope": "3袋",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    assert blocked.status_code == 422
    # 历史检验标记为校准已撤销
    records = must(client, "get", "/inspections", params={"batch_id": bid})
    assert records[0]["calibration_revoked"] is True
