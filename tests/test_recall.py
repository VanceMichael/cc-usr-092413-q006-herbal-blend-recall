"""定向召回主流程: 提案/独立复核/限量处置/已售通知/范围缩小与扩大。"""

from conftest import build_genealogy, must, run_recall


def test_targeted_recall_freezes_only_affected_shares(client):
    g = build_genealogy(client)
    _, approved = run_recall(client, g)

    # 影响范围: b(拆分余量40)与 f1(在库70+已售30=100); 无关的 f9 不冻结
    b = must(client, "get", f"/batches/{g['b']}")
    assert b["qty_frozen"] == "40" and b["qty_available"] == "0"
    f1 = must(client, "get", f"/batches/{g['f1']}")
    assert f1["qty_frozen"] == "70" and f1["qty_available"] == "0"
    f9 = must(client, "get", f"/batches/{g['f9']}")
    assert f9["qty_frozen"] == "0" and f9["qty_available"] == "78"

    # 已售 30kg 生成客户通知责任
    notifs = approved["notifications"]
    assert len(notifs) == 1
    assert notifs[0]["customer"] == "客户甲" and notifs[0]["qty"] == "30"

    # 冻结份额不可出库
    blocked = client.post("/batches/outbound", json={
        "flow_no": "O2", "batch_id": g["f1"], "qty": "1",
        "customer": "客户乙", "operator": "仓管员"})
    assert blocked.status_code == 422

    # 无关成品出库不受影响
    ok = must(client, "post", "/batches/outbound", json={
        "flow_no": "O9", "batch_id": g["f9"], "qty": "10",
        "customer": "客户丙", "operator": "仓管员"})
    assert ok["shipment"]["qty"] == "10"


def test_approval_requires_independent_reviewer(client):
    g = build_genealogy(client)
    must(client, "post", "/inspections", json={
        "batch_id": g["r1"], "method": "HPLC含量测定", "sample_scope": "3袋",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    must(client, "post", "/calibrations/revoke", json={
        "calibration_version": "CAL-1", "revoked_by": "质量负责人", "reason": "校准错误"})
    job = must(client, "post", "/propagation-jobs",
               json={"root_batch_ids": [g["r1"]], "max_steps": 100})
    p = must(client, "post", "/impact-proposals/from-job", json={
        "job_id": job["id"], "basis": "校准撤销", "proposed_by": "质检员甲"})

    # 未提交不可批准
    early = client.post(f"/impact-proposals/{p['id']}/approve",
                        json={"approver": "复核员乙"})
    assert early.status_code == 409
    must(client, "post", f"/impact-proposals/{p['id']}/submit")
    # 提出人不能批准自己的方案
    self_approve = client.post(f"/impact-proposals/{p['id']}/approve",
                               json={"approver": "质检员甲"})
    assert self_approve.status_code == 403
    # 独立复核人批准; 重复批准被拒绝
    must(client, "post", f"/impact-proposals/{p['id']}/approve",
         json={"approver": "复核员乙"})
    again = client.post(f"/impact-proposals/{p['id']}/approve",
                        json={"approver": "复核员乙"})
    assert again.status_code == 409


def test_disposal_limited_to_approved_qty(client):
    g = build_genealogy(client)
    _, approved = run_recall(client, g)
    orders = {o["batch_id"]: o for o in approved["disposal_orders"]}
    order_b = orders[g["b"]]
    assert order_b["approved_qty"] == "40"

    # 超过获批数量被拒绝
    over = client.post(f"/disposals/{order_b['id']}/execute", json={
        "qty": "41", "action": "DESTROY", "executor": "处置员"})
    assert over.status_code == 422

    # 分批执行, 累计不得超限
    part = must(client, "post", f"/disposals/{order_b['id']}/execute", json={
        "qty": "15", "action": "DESTROY", "executor": "处置员"})
    assert part["status"] == "PARTIAL" and part["executed_qty"] == "15"
    done = must(client, "post", f"/disposals/{order_b['id']}/execute", json={
        "qty": "25", "action": "RETURN", "executor": "处置员"})
    assert done["status"] == "DONE"
    beyond = client.post(f"/disposals/{order_b['id']}/execute", json={
        "qty": "1", "action": "DESTROY", "executor": "处置员"})
    assert beyond.status_code == 409

    b = must(client, "get", f"/batches/{g['b']}")
    assert b["qty_total"] == "0" and b["status"] == "CLOSED"


def test_supersede_shrinks_scope_with_new_inspection(client):
    g = build_genealogy(client)
    p1, approved = run_recall(client, g)
    orders = {o["batch_id"]: o for o in approved["disposal_orders"]}
    notif_id = approved["notifications"][0]["id"]

    # 替代检验(新校准版本)证明成品合格
    ins2 = must(client, "post", "/inspections", json={
        "batch_id": g["f1"], "method": "HPLC含量测定", "sample_scope": "成品2袋100g",
        "calibration_version": "CAL-2", "result": "PASS", "inspector": "检验员"})
    assert ins2["version"] == 1

    # 新版本方案缩小范围: 只保留 b
    p2 = must(client, "post", "/impact-proposals", json={
        "cause": {"type": "REINSPECTION", "ref": ins2["id"]},
        "scope": {g["b"]: "40"}, "basis": "替代检验 CAL-2 合格",
        "proposed_by": "质检员甲", "supersedes": p1["id"]})
    assert p2["version"] == 2
    must(client, "post", f"/impact-proposals/{p2['id']}/submit")
    must(client, "post", f"/impact-proposals/{p2['id']}/approve",
         json={"approver": "复核员乙"})

    # f1 解冻, 未受影响份额恢复可用
    f1 = must(client, "get", f"/batches/{g['f1']}")
    assert f1["qty_frozen"] == "0" and f1["qty_available"] == "70"
    # 旧处置单被替代, 不可再执行
    stale = client.post(f"/disposals/{orders[g['f1']]['id']}/execute", json={
        "qty": "1", "action": "DESTROY", "executor": "处置员"})
    assert stale.status_code == 409
    # 未完成的通知任务随范围缩小取消
    notif = must(client, "get", "/notifications", params={"batch_id": g["f1"]})
    assert notif[0]["cancelled"] is True
    done = client.post(f"/notifications/{notif_id}/complete",
                       json={"notified_by": "客服"})
    assert done.status_code == 409
    # 旧方案状态
    old = must(client, "get", f"/impact-proposals/{p1['id']}")
    assert old["proposal"]["status"] == "SUPERSEDED"


def test_supersede_expands_scope(client):
    g = build_genealogy(client)
    p1, _ = run_recall(client, g)

    # 新证据显示相邻批次也受影响 -> 扩大范围
    p2 = must(client, "post", "/impact-proposals", json={
        "cause": {"type": "REINSPECTION", "ref": "扩大排查"},
        "scope": {g["b"]: "40", g["f1"]: "100", g["f9"]: "10"},
        "basis": "扩大排查发现交叉污染",
        "proposed_by": "质检员甲", "supersedes": p1["id"]})
    must(client, "post", f"/impact-proposals/{p2['id']}/submit")
    approved2 = must(client, "post", f"/impact-proposals/{p2['id']}/approve",
                     json={"approver": "复核员乙"})

    f9 = must(client, "get", f"/batches/{g['f9']}")
    assert f9["qty_frozen"] == "10" and f9["qty_available"] == "68"
    new_orders = {o["batch_id"] for o in approved2["disposal_orders"]}
    assert g["f9"] in new_orders


def test_supersede_requires_approved_predecessor(client):
    g = build_genealogy(client)
    p1 = must(client, "post", "/impact-proposals", json={
        "cause": {"type": "MANUAL"}, "scope": {g["b"]: "40"},
        "basis": "排查", "proposed_by": "质检员甲"})
    # p1 仍是草案, 不能被替代
    resp = client.post("/impact-proposals", json={
        "cause": {"type": "MANUAL"}, "scope": {g["b"]: "40"},
        "basis": "排查", "proposed_by": "质检员甲", "supersedes": p1["id"]})
    assert resp.status_code == 422
