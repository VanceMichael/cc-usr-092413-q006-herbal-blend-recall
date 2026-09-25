import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def must(client, method, url, **kw):
    resp = client.request(method, url, **kw)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def build_genealogy(client):
    """两条独立 lineage: r1(黄芪)/r2(甘草) 系与 r3(当归) 系, 用于验证定向影响。

    r1 100kg -> 拆分 a 60 / b 40; a+r2 -> 合并 m1 105(损5); m1 -> 炮制 p1 100(损5);
    p1 -> 入库 f1 100; f1 出库 30kg 给客户甲(在库 70)。
    r3 80kg -> 炮制 p9 78(损2) -> 入库 f9 78(与问题批次无关)。
    """
    ids = {}
    ids["r1"] = must(client, "post", "/batches/receive", json={
        "flow_no": "R1", "material": "黄芪", "qty": "100", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    ids["r2"] = must(client, "post", "/batches/receive", json={
        "flow_no": "R2", "material": "甘草", "qty": "50", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    sp = must(client, "post", "/batches/split", json={
        "flow_no": "S1", "input": {"batch_id": ids["r1"], "qty": "100"},
        "outputs": ["60", "40"], "loss_qty": "0", "unit": "kg",
        "op_version": "SPL-1", "operator": "班长"})
    ids["a"], ids["b"] = sp["batches"][0]["id"], sp["batches"][1]["id"]
    mg = must(client, "post", "/batches/merge", json={
        "flow_no": "M1",
        "inputs": [{"batch_id": ids["a"], "qty": "60"},
                   {"batch_id": ids["r2"], "qty": "50"}],
        "output_material": "黄芪甘草混料", "output_qty": "105", "loss_qty": "5",
        "unit": "kg", "op_version": "MIX-2024-2", "operator": "班长"})
    ids["m1"] = mg["batches"][0]["id"]
    pr = must(client, "post", "/batches/process", json={
        "flow_no": "P1", "inputs": [{"batch_id": ids["m1"], "qty": "105"}],
        "outputs": [{"material": "黄芪甘草饮片(中间品)", "qty": "100"}],
        "loss_qty": "5", "unit": "kg", "op_version": "PAO-2024-3", "operator": "炮制工"})
    ids["p1"] = pr["batches"][0]["id"]
    wh = must(client, "post", "/batches/warehouse", json={
        "flow_no": "W1", "input": {"batch_id": ids["p1"], "qty": "100"},
        "output_qty": "100", "output_material": "黄芪甘草饮片", "loss_qty": "0",
        "unit": "kg", "op_version": "WH-1", "operator": "仓管员"})
    ids["f1"] = wh["batches"][0]["id"]
    must(client, "post", "/batches/outbound", json={
        "flow_no": "O1", "batch_id": ids["f1"], "qty": "30",
        "customer": "客户甲", "operator": "仓管员"})
    ids["r3"] = must(client, "post", "/batches/receive", json={
        "flow_no": "R3", "material": "当归", "qty": "80", "unit": "kg",
        "op_version": "RCV-1", "operator": "仓管员"})["batches"][0]["id"]
    p9 = must(client, "post", "/batches/process", json={
        "flow_no": "P9", "inputs": [{"batch_id": ids["r3"], "qty": "80"}],
        "outputs": [{"material": "当归饮片(中间品)", "qty": "78"}],
        "loss_qty": "2", "unit": "kg", "op_version": "PAO-2024-3", "operator": "炮制工"})
    ids["p9"] = p9["batches"][0]["id"]
    w9 = must(client, "post", "/batches/warehouse", json={
        "flow_no": "W9", "input": {"batch_id": ids["p9"], "qty": "78"},
        "output_qty": "78", "output_material": "当归饮片", "loss_qty": "0",
        "unit": "kg", "op_version": "WH-1", "operator": "仓管员"})
    ids["f9"] = w9["batches"][0]["id"]
    return ids


def run_recall(client, g):
    """检验(错误校准) -> 撤销校准 -> 传播 -> 提案 -> 提交 -> 独立复核批准。"""
    must(client, "post", "/inspections", json={
        "batch_id": g["r1"], "method": "HPLC含量测定", "sample_scope": "3袋混样200g",
        "calibration_version": "CAL-1", "result": "PASS", "inspector": "检验员"})
    must(client, "post", "/calibrations/revoke", json={
        "calibration_version": "CAL-1", "revoked_by": "质量负责人",
        "reason": "含量校准曲线错误"})
    job = must(client, "post", "/propagation-jobs",
               json={"root_batch_ids": [g["r1"]], "max_steps": 100})
    assert job["status"] == "COMPLETED"
    p = must(client, "post", "/impact-proposals/from-job", json={
        "job_id": job["id"], "basis": "校准撤销 CAL-1", "proposed_by": "质检员甲"})
    must(client, "post", f"/impact-proposals/{p['id']}/submit")
    approved = must(client, "post", f"/impact-proposals/{p['id']}/approve",
                    json={"approver": "复核员乙"})
    return p, approved
