"""谱系: 事件链不可覆盖, 每次变换记录数量、单位、损耗与操作版本。"""

from decimal import Decimal

from app.domain.models import BatchKind, BatchStatus, EventType

from conftest import build_chain, cmd, receive


def test_chain_records_quantity_unit_loss_and_operation_version(state):
    build_chain(state)
    events = state.store.events
    assert [e.event_type for e in events] == [
        EventType.RECEIVE,
        EventType.RECEIVE,
        EventType.SPLIT,
        EventType.MERGE,
        EventType.PROCESS,
        EventType.STOCK_IN,
        EventType.SHIP,
    ]
    merge = next(e for e in events if e.event_type is EventType.MERGE)
    assert merge.unit == "kg"
    assert merge.loss == Decimal("5")
    assert merge.operation_version == "SOP-2.1"
    assert {p.batch_id: p.quantity for p in merge.inputs} == {
        "R1a": Decimal("40"),
        "R2": Decimal("60"),
    }
    assert merge.outputs[0].batch_id == "M1"
    # 每个批次都能指到形成它的事件
    assert state.store.batches["M1"].created_by == merge.event_id


def test_events_are_append_only_and_batches_track_current_state(state):
    build_chain(state)
    snapshot = [(e.event_id, e.seq, e.payload_hash) for e in state.store.events]
    receive(state, "R3", "10")
    # 已有事件不被新事件影响
    assert [(e.event_id, e.seq, e.payload_hash) for e in state.store.events[:7]] == snapshot
    assert len({e.event_id for e in state.store.events}) == len(state.store.events)
    assert [e.seq for e in state.store.events] == sorted(e.seq for e in state.store.events)

    batches = state.store.batches
    assert batches["R1"].quantity == 0 and batches["R1"].status is BatchStatus.CONSUMED
    assert batches["R1b"].quantity == Decimal("60")
    assert batches["M1"].status is BatchStatus.CONSUMED
    assert batches["P1"].kind is BatchKind.FINISHED
    assert batches["P1"].quantity == Decimal("60")  # 90 - 出库30
    assert batches["P1"].status is BatchStatus.ACTIVE


def test_versions_increment_on_every_mutation(state):
    build_chain(state)
    assert state.store.batches["R1"].version == 2  # 收货 + 拆分
    p1_version = state.store.batches["P1"].version
    state.genealogy.record(
        cmd(EventType.SHIP, "SHP-2", inputs=[("P1", "5")], customer="华南药房")
    )
    assert state.store.batches["P1"].version == p1_version + 1


def test_stock_in_requires_unfrozen_full_batch(state):
    receive(state, "R9", "10")
    state.inventory.lock("R9", Decimal("4"), reason="HOLD")
    import pytest

    from app.domain.errors import StateError

    with pytest.raises(StateError):
        state.genealogy.record(
            cmd(
                EventType.STOCK_IN,
                "STK-9",
                inputs=[("R9", "10")],
                outputs=[("R9", "MAT-RAWM", BatchKind.FINISHED, "10")],
            )
        )
