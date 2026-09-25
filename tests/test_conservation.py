"""守恒: 拆分/合并/炮制必须满足 投入 = 产出 + 损耗, 违规不改变任何库存。"""

from decimal import Decimal

import pytest

from app.domain.errors import ConservationViolation
from app.domain.models import BatchKind, EventType

from conftest import cmd, receive


def test_merge_must_conserve(state):
    receive(state, "R1", "40")
    receive(state, "R2", "60")
    with pytest.raises(ConservationViolation):
        state.genealogy.record(
            cmd(
                EventType.MERGE,
                "MRG-BAD",
                inputs=[("R1", "40"), ("R2", "60")],
                outputs=[("M1", "MAT", BatchKind.IN_PROCESS, "95")],
                loss="4",  # 95 + 4 != 100
            )
        )
    assert "M1" not in state.store.batches
    assert state.store.batches["R1"].quantity == Decimal("40")
    assert state.store.batches["R2"].quantity == Decimal("60")
    assert len(state.store.events) == 2  # 只有两笔收货


def test_split_must_conserve(state):
    receive(state, "R1", "100")
    with pytest.raises(ConservationViolation):
        state.genealogy.record(
            cmd(
                EventType.SPLIT,
                "SPL-BAD",
                inputs=[("R1", "100")],
                outputs=[("A", "MAT", BatchKind.RAW, "60"), ("B", "MAT", BatchKind.RAW, "60")],
            )
        )
    assert state.store.batches["R1"].quantity == Decimal("100")


def test_negative_loss_rejected(state):
    receive(state, "R1", "100")
    with pytest.raises(ConservationViolation):
        state.genealogy.record(
            cmd(
                EventType.PROCESS,
                "PRC-BAD",
                inputs=[("R1", "100")],
                outputs=[("P", "MAT", BatchKind.IN_PROCESS, "100")],
                loss="-1",
            )
        )


def test_process_with_loss_conserves(state):
    receive(state, "R1", "100")
    state.genealogy.record(
        cmd(
            EventType.PROCESS,
            "PRC-OK",
            inputs=[("R1", "100")],
            outputs=[("P", "MAT", BatchKind.IN_PROCESS, "92")],
            loss="8",
        )
    )
    assert state.store.batches["P"].quantity == Decimal("92")
    assert state.store.batches["R1"].quantity == 0
