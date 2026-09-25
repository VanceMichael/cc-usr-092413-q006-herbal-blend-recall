"""影响范围: 质量提报、独立复核批准、处置限获批数量、新版本缩小或扩大范围。"""

from decimal import Decimal

import pytest

from app.domain.errors import ApprovalError, StateError
from app.domain.models import AffectedItem, AssessmentStatus, BatchStatus

from conftest import build_chain


def _propose(state, items, basis="校准撤销 CAL-1.2", by="qa01"):
    return state.impact.propose(
        root_batch_id="R1",
        items=[AffectedItem(bid, Decimal(q)) for bid, q in items],
        basis=basis,
        proposed_by=by,
    )


def test_approver_must_be_independent(state):
    build_chain(state)
    assessment = _propose(state, [("R1b", "60")])
    with pytest.raises(ApprovalError):
        state.impact.approve(assessment.assessment_id, approved_by="qa01")
    approved = state.impact.approve(assessment.assessment_id, approved_by="qa02")
    assert approved.status is AssessmentStatus.APPROVED
    assert approved.approved_by == "qa02"


def test_approval_locks_only_affected_share_and_notifies_sold(state):
    build_chain(state)
    # 传播结果: R1b 60 全在库; P1 受影响 36(在库 60 中的 24 + 已售 30 中的 12)
    assessment = _propose(state, [("R1b", "60"), ("P1", "36")])
    state.impact.approve(assessment.assessment_id, approved_by="qa02")

    batches = state.store.batches
    assert batches["R1b"].frozen == Decimal("60")
    assert batches["R1b"].status is BatchStatus.FROZEN
    assert batches["P1"].frozen == Decimal("24")
    assert batches["P1"].status is BatchStatus.PARTIALLY_FROZEN
    # 未受影响份额不冻结, 仍可出库
    assert batches["P1"].available == Decimal("36")
    # 已售受影响份额转为通知责任
    tasks = list(state.store.notifications.values())
    assert len(tasks) == 1
    assert tasks[0].customer == "华东药房"
    assert tasks[0].quantity == Decimal("12")
    assert not tasks[0].done


def test_disposition_limited_to_approved_quantity(state):
    build_chain(state)
    assessment = _propose(state, [("R1b", "60")])
    state.impact.approve(assessment.assessment_id, approved_by="qa02")
    with pytest.raises(StateError):
        state.impact.execute(assessment.assessment_id, "R1b", Decimal("61"), "销毁", "wh01")
    state.impact.execute(assessment.assessment_id, "R1b", Decimal("40"), "销毁", "wh01")
    with pytest.raises(StateError):
        state.impact.execute(assessment.assessment_id, "R1b", Decimal("21"), "销毁", "wh01")
    state.impact.execute(assessment.assessment_id, "R1b", Decimal("20"), "销毁", "wh01")
    batch = state.store.batches["R1b"]
    assert batch.quantity == 0 and batch.frozen == 0


def test_disposition_requires_approved_assessment(state):
    build_chain(state)
    pending = _propose(state, [("R1b", "60")])
    with pytest.raises(StateError):
        state.impact.execute(pending.assessment_id, "R1b", Decimal("1"), "销毁", "wh01")


def test_new_version_shrinks_and_expands_scope(state):
    build_chain(state)
    v1 = _propose(state, [("R1b", "60"), ("P1", "36")])
    state.impact.approve(v1.assessment_id, approved_by="qa02")
    assert state.store.batches["P1"].frozen == Decimal("24")

    # 替代检验合格 -> 缩小范围: 只保留 R1b
    v2 = _propose(state, [("R1b", "60")], basis="替代检验 INS-NEW 合格")
    assert v2.version == 2
    state.impact.approve(v2.assessment_id, approved_by="qa03")
    assert state.store.assessments[v1.assessment_id].status is AssessmentStatus.SUPERSEDED
    assert state.store.batches["P1"].frozen == 0
    assert state.store.batches["R1b"].frozen == Decimal("60")
    # 旧版本未完成的通知任务被取消
    assert all(
        t.cancelled or t.done
        for t in state.store.notifications.values()
        if t.assessment_id == v1.assessment_id
    )

    # 新证据 -> 扩大范围: P1 重新纳入 9(在库 60 中按比例冻结 6)
    v3 = _propose(state, [("R1b", "60"), ("P1", "9")], basis="上游召回扩展")
    state.impact.approve(v3.assessment_id, approved_by="qa02")
    assert state.store.batches["P1"].frozen == Decimal("6")


def test_shrink_cannot_go_below_executed(state):
    build_chain(state)
    v1 = _propose(state, [("R1b", "60")])
    state.impact.approve(v1.assessment_id, approved_by="qa02")
    state.impact.execute(v1.assessment_id, "R1b", Decimal("25"), "销毁", "wh01")
    v2 = _propose(state, [("R1b", "10")])
    with pytest.raises(StateError):
        state.impact.approve(v2.assessment_id, approved_by="qa03")
