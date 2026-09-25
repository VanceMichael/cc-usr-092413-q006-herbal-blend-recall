"""设备重复上报: 同内容沿用原记录, 内容不同则隔离且不改变库存。"""

from decimal import Decimal

import pytest

from app.domain.errors import IdempotencyConflict
from app.domain.genealogy import RecordStatus
from app.domain.models import BatchKind, EventType

from conftest import cmd, receive


def test_same_flow_same_content_replays_original(state):
    receive(state, "R1", "100")
    first = state.genealogy.record(
        cmd(
            EventType.SPLIT,
            "SPL-1",
            inputs=[("R1", "100")],
            outputs=[("R1a", "MAT", BatchKind.RAW, "40"), ("R1b", "MAT", BatchKind.RAW, "60")],
        )
    )
    again = state.genealogy.record(
        cmd(
            EventType.SPLIT,
            "SPL-1",
            inputs=[("R1", "100")],
            outputs=[("R1a", "MAT", BatchKind.RAW, "40"), ("R1b", "MAT", BatchKind.RAW, "60")],
        )
    )
    assert first.status is RecordStatus.APPLIED
    assert again.status is RecordStatus.REPLAYED
    assert again.event.event_id == first.event.event_id
    # 库存没有被重复扣减
    assert state.store.batches["R1"].quantity == 0
    assert state.store.batches["R1a"].quantity == Decimal("40")
    assert len(state.store.events) == 2


def test_same_flow_different_content_is_quarantined(state):
    receive(state, "R1", "100")
    state.genealogy.record(
        cmd(
            EventType.SPLIT,
            "SPL-1",
            inputs=[("R1", "100")],
            outputs=[("R1a", "MAT", BatchKind.RAW, "40"), ("R1b", "MAT", BatchKind.RAW, "60")],
        )
    )
    with pytest.raises(IdempotencyConflict) as exc_info:
        state.genealogy.record(
            cmd(
                EventType.SPLIT,
                "SPL-1",
                inputs=[("R1", "100")],
                outputs=[("R1a", "MAT", BatchKind.RAW, "50"), ("R1b", "MAT", BatchKind.RAW, "50")],
            )
        )
    quarantine_id = exc_info.value.quarantine_id
    report = state.store.quarantine[quarantine_id]
    assert report.source == "MES-01"
    assert report.idempotency_key == "SPL-1"
    # 冲突上报未进入谱系, 库存保持原状
    assert state.store.batches["R1a"].quantity == Decimal("40")
    assert len(state.store.events) == 2


def test_different_source_same_key_is_independent(state):
    receive(state, "R1", "100")
    state.genealogy.record(
        cmd(EventType.SHIP, "FLOW-7", inputs=[("R1", "10")], customer="甲", source="WMS-A")
    )
    # 另一系统使用相同流水号不冲突
    result = state.genealogy.record(
        cmd(EventType.SHIP, "FLOW-7", inputs=[("R1", "20")], customer="乙", source="WMS-B")
    )
    assert result.status is RecordStatus.APPLIED
    assert state.store.batches["R1"].quantity == Decimal("70")
