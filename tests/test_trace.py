"""双向追溯: 成品 -> 全部投入与检验依据; 问题批次 -> 去向、处置与未完成责任。"""

from decimal import Decimal

from app.domain.models import AffectedItem, EventType

from conftest import build_chain


def test_backward_from_finished_product(state):
    build_chain(state)
    trace = state.trace.backward("P1")
    assert set(trace.batches) == {"P1", "M1", "R1a", "R1", "R2"}
    # R1b 由 R1 拆分而来, 但不是 P1 的投入
    assert "R1b" not in trace.batches
    merge = next(e for e in trace.events if e.event_type is EventType.MERGE)
    assert merge.loss == Decimal("5")
    # 检验依据随批次一起给出
    p1_inspections = trace.inspections["P1"]
    assert len(p1_inspections) == 1
    assert p1_inspections[0].method == "HPLC-2020"
    assert p1_inspections[0].calibration_version == "CAL-1.2"


def test_forward_lists_whereabouts_dispositions_and_outstanding(state):
    build_chain(state)
    assessment = state.impact.propose(
        root_batch_id="R1",
        items=[AffectedItem("R1b", Decimal("60")), AffectedItem("P1", Decimal("36"))],
        basis="校准撤销 CAL-1.2",
        proposed_by="qa01",
    )
    state.impact.approve(assessment.assessment_id, approved_by="qa02")
    state.impact.execute(assessment.assessment_id, "R1b", Decimal("10"), "销毁", "wh01")

    trace = state.trace.forward("R1")
    assert {"R1", "R1a", "R1b", "M1", "P1"} <= set(trace.batches)
    # 已售去向保留
    assert [(s.customer, s.quantity) for s in trace.shipments] == [
        ("华东药房", Decimal("30"))
    ]
    # 处置结果可见
    assert [(d.batch_id, d.quantity, d.action) for d in trace.dispositions] == [
        ("R1b", Decimal("10"), "销毁")
    ]
    # 未完成责任: R1b 还剩 50 待处置, P1 36 未处置, 客户通知未完成
    outstanding = "\n".join(trace.outstanding)
    assert "批次 R1b 待处置 50" in outstanding
    assert "批次 P1 待处置 36" in outstanding
    assert "华东药房" in outstanding and "待通知" in outstanding

    # 通知完成后责任消失
    for task in trace.notifications:
        state.impact.complete_notification(task.task_id)
    outstanding_after = "\n".join(state.trace.forward("R1").outstanding)
    assert "待通知" not in outstanding_after
    assert "批次 R1b 待处置 50" in outstanding_after  # 处置责任仍在
