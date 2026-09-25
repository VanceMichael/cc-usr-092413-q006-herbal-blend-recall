"""传播作业: 中断后从已确认边界续跑。"""

from conftest import build_genealogy, must


def test_propagation_resume_from_confirmed_boundary(client):
    g = build_genealogy(client)

    job1 = must(client, "post", "/propagation-jobs",
                json={"root_batch_ids": [g["r1"]], "max_steps": 1})
    assert job1["status"] == "INTERRUPTED"
    assert job1["confirmed"] == [g["r1"]]
    assert job1["frontier"]

    job2 = must(client, "post", f"/propagation-jobs/{job1['id']}/resume",
                json={"max_steps": 2})
    assert job2["status"] == "INTERRUPTED"
    assert len(job2["confirmed"]) == 3
    assert job2["confirmed"][0] == g["r1"]  # 已确认边界保持在前

    job3 = must(client, "post", f"/propagation-jobs/{job1['id']}/resume",
                json={"max_steps": 100})
    assert job3["status"] == "COMPLETED"
    # 不重复处理: 确认列表无重复, 步数等于批次数
    assert len(job3["confirmed"]) == len(set(job3["confirmed"])) == 6
    assert job3["steps"] == 6
    # 影响数量 = 在库 + 已售; 无关 lineage 不受影响
    assert job3["affected"][g["b"]] == "40"
    assert job3["affected"][g["f1"]] == "100"
    assert g["f9"] not in job3["affected"]

    # 完成后 resume 幂等
    job4 = must(client, "post", f"/propagation-jobs/{job1['id']}/resume",
                json={"max_steps": 1})
    assert job4["status"] == "COMPLETED" and job4["steps"] == 6


def test_proposal_from_unfinished_job_rejected(client):
    g = build_genealogy(client)
    job = must(client, "post", "/propagation-jobs",
               json={"root_batch_ids": [g["r1"]], "max_steps": 1})
    assert job["status"] == "INTERRUPTED"
    resp = client.post("/impact-proposals/from-job", json={
        "job_id": job["id"], "basis": "校准撤销", "proposed_by": "质检员甲"})
    assert resp.status_code == 409
