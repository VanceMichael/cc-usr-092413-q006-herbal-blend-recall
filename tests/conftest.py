"""共享夹具与场景: 一条完整的 收货->拆分->合并->炮制->检验->入库->出库 链。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.genealogy import TransformationCommand
from app.domain.models import BatchKind, EventType, InspectionResult, OutputSpec, Portion
from app.services import DomainState, build_state

D = Decimal


@pytest.fixture
def state() -> DomainState:
    return build_state()


def cmd(
    event_type: EventType,
    key: str,
    *,
    inputs: list[tuple[str, str]] | None = None,
    outputs: list[tuple[str, str, BatchKind, str]] | None = None,
    loss: str = "0",
    source: str = "MES-01",
    operation_version: str = "SOP-2.1",
    actor: str = "op01",
    customer: str | None = None,
    expected_versions: dict[str, int] | None = None,
    unit: str = "kg",
) -> TransformationCommand:
    return TransformationCommand(
        event_type=event_type,
        idempotency_key=key,
        source=source,
        operation_version=operation_version,
        unit=unit,
        actor=actor,
        inputs=tuple(Portion(bid, D(qty)) for bid, qty in (inputs or [])),
        outputs=tuple(
            OutputSpec(bid, mat, kind, D(qty)) for bid, mat, kind, qty in (outputs or [])
        ),
        loss=D(loss),
        customer=customer,
        expected_versions=expected_versions,
    )


def receive(state: DomainState, batch_id: str, qty: str, key: str | None = None) -> None:
    state.genealogy.record(
        cmd(
            EventType.RECEIVE,
            key or f"RCV-{batch_id}",
            outputs=[(batch_id, "MAT-RAWM", BatchKind.RAW, qty)],
        )
    )


def build_chain(state: DomainState) -> None:
    """R1(100) 拆成 R1a(40)/R1b(60); R1a 与 R2(60) 合并成 M1(95, 损耗5);
    M1 炮制成 P1(90, 损耗5); P1 检验合格后入库, 出库 30 给华东药房。"""
    g = state.genealogy
    receive(state, "R1", "100")
    receive(state, "R2", "60")
    g.record(
        cmd(
            EventType.SPLIT,
            "SPL-1",
            inputs=[("R1", "100")],
            outputs=[
                ("R1a", "MAT-RAWM", BatchKind.RAW, "40"),
                ("R1b", "MAT-RAWM", BatchKind.RAW, "60"),
            ],
        )
    )
    g.record(
        cmd(
            EventType.MERGE,
            "MRG-1",
            inputs=[("R1a", "40"), ("R2", "60")],
            outputs=[("M1", "MAT-RAWM", BatchKind.IN_PROCESS, "95")],
            loss="5",
        )
    )
    g.record(
        cmd(
            EventType.PROCESS,
            "PRC-1",
            inputs=[("M1", "95")],
            outputs=[("P1", "MAT-DECOCT", BatchKind.IN_PROCESS, "90")],
            loss="5",
        )
    )
    state.inspections.record(
        batch_id="P1",
        method="HPLC-2020",
        sample_scope="P1 上中下三点抽样",
        calibration_version="CAL-1.2",
        result=InspectionResult.PASS,
        inspector="qc01",
    )
    g.record(
        cmd(
            EventType.STOCK_IN,
            "STK-1",
            inputs=[("P1", "90")],
            outputs=[("P1", "MAT-DECOCT", BatchKind.FINISHED, "90")],
        )
    )
    g.record(
        cmd(
            EventType.SHIP,
            "SHP-1",
            inputs=[("P1", "30")],
            customer="华东药房",
        )
    )
