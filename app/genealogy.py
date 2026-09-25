"""批次谱系核心: 事件登记、守恒校验、幂等与冲突隔离、防篡改哈希链。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .errors import ConflictError, ValidationError
from .models import (
    Batch,
    BatchEvent,
    BatchKind,
    BatchStatus,
    Edge,
    EventType,
    IsolatedReport,
    OutboundShipment,
    Portion,
    new_id,
)
from .store import require_batch

if TYPE_CHECKING:
    from .store import Store


@dataclass
class OutputSpec:
    """变换产出的规格; material 为 None 时继承首个投入批次的物料。"""
    material: str | None
    qty: Decimal


@dataclass
class EventResult:
    event: BatchEvent
    batches: list[Batch]          # 本事件新产生的批次
    shipment: OutboundShipment | None
    reused: bool                  # True 表示设备重复上报, 沿用原记录


# 需要满足 投入 = 产出 + 损耗 的事件类型
_CONSERVED_TYPES = {EventType.SPLIT, EventType.MERGE, EventType.PROCESS, EventType.WAREHOUSE}


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _hash_event(prev_hash: str, payload_hash: str) -> str:
    return hashlib.sha256(f"{prev_hash}:{payload_hash}".encode()).hexdigest()


def record_event(
    store: Store,
    *,
    flow_no: str,
    event_type: EventType,
    inputs: list[Portion],
    outputs: list[OutputSpec],
    loss_qty: Decimal = Decimal("0"),
    unit: str,
    op_version: str,
    operator: str,
    customer: str | None = None,
    expected_versions: dict[str, int] | None = None,
) -> EventResult:
    """登记一条批次变换事件。

    - 设备流水号幂等: 相同流水号相同内容 -> 沿用原记录; 内容不同 -> 隔离, 不进谱系。
    - 拆分/合并/炮制/入库必须满足 投入 = 产出 + 损耗。
    - 投入不得超过批次可用份额(冻结份额受保护)。
    - expected_versions 提供批次乐观锁, 并发作业按实际用量串行。
    """
    payload = {
        "flow_no": flow_no,
        "type": event_type.value,
        "inputs": [{"batch_id": p.batch_id, "qty": str(p.qty)} for p in inputs],
        "outputs": [{"material": o.material, "qty": str(o.qty)} for o in outputs],
        "loss_qty": str(loss_qty),
        "unit": unit,
        "op_version": op_version,
        "operator": operator,
        "customer": customer,
    }
    payload_hash = _hash_payload(payload)

    with store.lock:
        existing = store.events_by_flow.get(flow_no)
        if existing is not None:
            if existing.payload_hash == payload_hash:
                return EventResult(
                    existing,
                    [store.batches[p.batch_id] for p in existing.outputs],
                    _shipment_for_event(store, existing.id),
                    reused=True,
                )
            isolated = _isolate(store, flow_no, payload, payload_hash, existing)
            raise ConflictError(
                "设备流水号已存在但上报内容不一致, 已隔离待人工核对",
                isolation_id=isolated.id,
                existing_event_id=existing.id,
            )

        _validate_shape(event_type, inputs, outputs, loss_qty, customer)
        in_batches = [require_batch(store, p.batch_id) for p in inputs]
        for b in in_batches:
            if b.unit != unit:
                raise ValidationError(
                    "事件单位与批次单位不一致",
                    batch_id=b.id, batch_unit=b.unit, event_unit=unit,
                )
        if event_type == EventType.OUTBOUND and in_batches[0].kind != BatchKind.FINISHED:
            raise ValidationError("仅成品批次可出库销售", batch_id=in_batches[0].id)

        for bid, ver in (expected_versions or {}).items():
            b = require_batch(store, bid)
            if b.version != ver:
                raise ConflictError(
                    "库存版本已变化, 请刷新后按最新版本重试",
                    batch_id=bid, expected=ver, actual=b.version,
                )

        if event_type in _CONSERVED_TYPES:
            total_in = sum((p.qty for p in inputs), Decimal("0"))
            total_out = sum((o.qty for o in outputs), Decimal("0"))
            if total_in != total_out + loss_qty:
                raise ValidationError(
                    "投入、产出与损耗不守恒",
                    total_in=str(total_in), total_out=str(total_out), loss=str(loss_qty),
                )

        for p, b in zip(inputs, in_batches):
            if p.qty > b.available:
                raise ValidationError(
                    "批次可用数量不足(部分份额已冻结或已消耗)",
                    batch_id=b.id, available=str(b.available), requested=str(p.qty),
                )

        # 生效: 扣减投入, 生成产出批次
        for p, b in zip(inputs, in_batches):
            b.qty_total -= p.qty
            b.version += 1
            if b.qty_total == 0:
                b.status = BatchStatus.CONSUMED

        event_id = new_id("evt")
        created: list[Batch] = []
        out_portions: list[Portion] = []
        for spec in outputs:
            material = spec.material or in_batches[0].material
            batch = Batch(
                id=new_id("bat"),
                material=material,
                kind=_output_kind(event_type, in_batches),
                unit=unit,
                qty_total=spec.qty,
                created_by_event=event_id,
            )
            store.batches[batch.id] = batch
            created.append(batch)
            out_portions.append(Portion(batch.id, spec.qty))

        shipment = None
        if event_type == EventType.OUTBOUND:
            shipment = OutboundShipment(
                id=new_id("ship"), batch_id=inputs[0].batch_id,
                qty=inputs[0].qty, customer=customer, event_id=event_id,
            )
            store.shipments[shipment.id] = shipment

        event = BatchEvent(
            id=event_id, flow_no=flow_no, type=event_type,
            inputs=list(inputs), outputs=out_portions, loss_qty=loss_qty,
            unit=unit, op_version=op_version, operator=operator,
            payload_hash=payload_hash, prev_hash=store.last_hash,
            hash=_hash_event(store.last_hash, payload_hash),
        )
        store.last_hash = event.hash
        store.events.append(event)
        store.events_by_flow[flow_no] = event

        for p in inputs:
            for op in out_portions:
                edge = Edge(
                    from_batch=p.batch_id, to_batch=op.batch_id,
                    qty=_edge_qty(inputs, out_portions, p, op), event_id=event_id,
                )
                store.edges_from[p.batch_id].append(edge)
                store.edges_to[op.batch_id].append(edge)

        return EventResult(event, created, shipment, reused=False)


def verify_chain(store: Store) -> bool:
    """重放哈希链, 验证台账未被覆盖或篡改。"""
    from .store import GENESIS_HASH

    prev = GENESIS_HASH
    for e in store.events:
        if e.prev_hash != prev or e.hash != _hash_event(prev, e.payload_hash):
            return False
        prev = e.hash
    return prev == store.last_hash


def _validate_shape(event_type, inputs, outputs, loss_qty, customer) -> None:
    if loss_qty < 0:
        raise ValidationError("损耗不能为负")
    for p in inputs:
        if p.qty <= 0:
            raise ValidationError("投入数量必须为正", batch_id=p.batch_id)
    for o in outputs:
        if o.qty <= 0:
            raise ValidationError("产出数量必须为正")
    n_in, n_out = len(inputs), len(outputs)
    match event_type:
        case EventType.RECEIVE:
            if n_in != 0 or n_out != 1:
                raise ValidationError("收货事件须为 0 投入 1 产出")
            if loss_qty != 0:
                raise ValidationError("收货不允许登记损耗")
            if not outputs[0].material:
                raise ValidationError("收货须指定物料")
        case EventType.SPLIT:
            if n_in != 1 or n_out < 2:
                raise ValidationError("拆分须为 1 投入且至少 2 产出")
        case EventType.MERGE:
            if n_in < 2 or n_out != 1:
                raise ValidationError("合并须为至少 2 投入 1 产出")
            if not outputs[0].material:
                raise ValidationError("合并须指定产出物料")
        case EventType.PROCESS:
            if n_in < 1 or n_out < 1:
                raise ValidationError("炮制须至少 1 投入 1 产出")
            if any(not o.material for o in outputs):
                raise ValidationError("炮制须指定产出物料")
        case EventType.WAREHOUSE:
            if n_in < 1 or n_out != 1:
                raise ValidationError("成品入库须为至少 1 投入 1 产出")
        case EventType.OUTBOUND:
            if n_in != 1 or n_out != 0:
                raise ValidationError("出库须为 1 投入 0 产出")
            if loss_qty != 0:
                raise ValidationError("出库不允许登记损耗")
            if not customer:
                raise ValidationError("出库须指定客户去向")


def _output_kind(event_type, in_batches) -> BatchKind:
    match event_type:
        case EventType.RECEIVE:
            return BatchKind.RAW
        case EventType.SPLIT:
            return in_batches[0].kind
        case EventType.MERGE | EventType.PROCESS:
            return BatchKind.INTERMEDIATE
        case EventType.WAREHOUSE:
            return BatchKind.FINISHED
        case _:
            return in_batches[0].kind


def _edge_qty(inputs, outputs, in_portion, out_portion) -> Decimal:
    """边上记录的数量仅用于展示; 传播计算使用批次实际数量。"""
    if len(outputs) == 1:
        return in_portion.qty
    if len(inputs) == 1:
        return out_portion.qty
    return in_portion.qty


def _isolate(store, flow_no, payload, payload_hash, existing) -> IsolatedReport:
    for iso in store.isolated.values():
        if iso.flow_no == flow_no and iso.payload_hash == payload_hash:
            return iso
    iso = IsolatedReport(
        id=new_id("iso"), flow_no=flow_no,
        reason="相同流水号上报内容不一致",
        payload=payload, payload_hash=payload_hash,
        existing_event_id=existing.id,
    )
    store.isolated[iso.id] = iso
    return iso


def _shipment_for_event(store, event_id: str) -> OutboundShipment | None:
    for s in store.shipments.values():
        if s.event_id == event_id:
            return s
    return None
