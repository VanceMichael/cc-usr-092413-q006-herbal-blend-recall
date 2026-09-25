"""双向追溯: 成品 -> 投入与检验依据; 问题批次 -> 去向/处置/未完成责任。"""

from conftest import build_genealogy, must, run_recall


def test_backward_trace_lists_inputs_and_inspection_basis(client):
    g = build_genealogy(client)
    must(client, "post", "/inspections", json={
        "batch_id": g["r1"], "method": "HPLC含量测定", "sample_scope": "3袋混样200g",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    must(client, "post", "/calibrations/revoke", json={
        "calibration_version": "CAL-1", "revoked_by": "质量负责人", "reason": "校准错误"})

    tr = must(client, "get", f"/batches/{g['f1']}/trace/backward")
    input_ids = {b["id"] for b in tr["inputs"]}
    assert input_ids == {g["p1"], g["m1"], g["a"], g["r2"], g["r1"]}
    assert g["b"] not in input_ids  # 拆分出的另一支未进入该成品

    ins = tr["inspections"][g["r1"]][0]
    assert ins["calibration_version"] == "CAL-1"
    assert ins["calibration_revoked"] is True
    assert ins["method"] == "HPLC含量测定" and ins["sample_scope"] == "3袋混样200g"


def test_forward_trace_lists_destinations_disposals_and_pending(client):
    g = build_genealogy(client)
    _, approved = run_recall(client, g)

    tr = must(client, "get", f"/batches/{g['r1']}/trace/forward")
    dest = {b["id"] for b in tr["destinations"]}
    assert dest == {g["a"], g["b"], g["m1"], g["p1"], g["f1"]}

    # 已销售去向保留
    assert len(tr["shipments"]) == 1
    assert tr["shipments"][0]["customer"] == "客户甲"
    assert tr["shipments"][0]["qty"] == "30"

    # 处置结果与未完成责任
    assert len(tr["disposals"]) == 2
    pend = tr["pending_responsibilities"]
    assert len(pend["disposal_orders"]) == 2
    assert len(pend["notifications"]) == 1

    # 完成全部处置与通知后责任清零
    for o in tr["disposals"]:
        must(client, "post", f"/disposals/{o['id']}/execute", json={
            "qty": o["remaining_qty"], "action": "DESTROY", "executor": "处置员"})
    for n in tr["notifications"]:
        must(client, "post", f"/notifications/{n['id']}/complete",
             json={"notified_by": "客服"})

    tr2 = must(client, "get", f"/batches/{g['r1']}/trace/forward")
    pend2 = tr2["pending_responsibilities"]
    assert pend2 == {"disposal_orders": [], "notifications": [], "awaiting_approval": []}
    done = {o["status"] for o in tr2["disposals"]}
    assert done == {"DONE"}


def test_responsibilities_endpoint(client):
    g = build_genealogy(client)
    run_recall(client, g)
    resp = must(client, "get", f"/batches/{g['r1']}/responsibilities")
    assert set(resp["descendants"]) == {g["a"], g["b"], g["m1"], g["p1"], g["f1"]}
    assert len(resp["pending"]["disposal_orders"]) == 2
    assert len(resp["pending"]["notifications"]) == 1
