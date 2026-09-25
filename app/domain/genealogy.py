"""批次谱系服务: 把收货、拆分、合并、炮制、成品入库、出库串成不可覆盖的事件链。

不变量:
- 事件只增不改; 批次当前状态由事件推导, 纠正只能通过新事件。
- 同一(来源, 流水号)重复上报且内容一致 -> 沿用原记录(REPLAYED);
  内容不同 -> 隔离(IdempotencyConflict), 不改变任何库存。
- 拆分/合并/炮制必须满足 投入 = 产出 + 损耗, 且损耗非负。
- 投入数量不得超过批次可用量(在库 - 已冻结); 调用方可携带预期库存版本,
  与当前版本不一致时拒绝(VersionConflict), 由调用方重读后重试。
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from .errors import (
    ConservationViolation,
    IdempotencyConflict,
    InsufficientQuantity,
    NotFoundError,
    StateError,
    VersionConflict,
)
from .models import (
    Batch,
    BatchKind,
    EventType,
    OutputSpec,
    Portion,
    QuarantinedReport,
    Shipment,
    TransformationEvent,
    refresh_status,
)
from .store import Store

_CONSERVATION_TYPES = {EventType.SPLIT, EventType.MERGE, EventType.PROCESS}


@dataclass(frozen=True)
class TransformationCommand:
    event_type: EventType
    idempotency_key: str
    source: str
    operation_version: str
    unit: str
    actor: str
    inputs: tuple[Portion, ...] = ()
    outputs: tuple[OutputSpec, ...] = ()
    loss: Decimal = Decimal("0")
    customer: str | None = None                    # SHIP 必填
    expected_versions: dict[str, int] | None = None  # 投入批次的预期库存版本
    note: str = ""


class RecordStatus(str, enum.Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True)
class RecordResult:
    status: RecordStatus
    event: TransformationEvent


def _canonical(cmd: TransformationCommand) -> str:
    """命令内容的规范 JSON 表示, 用于幂等哈希。"""

    def norm(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, dict):
            return {k: norm(value[k]) for k in sorted(value)}
        if isinstance(value, (list, tuple)):
            return [norm(v) for v in value]
        return value

    return json.dumps(norm(asdict(cmd)), sort_keys=True, separators=(",", ":"))


class GenealogyService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def record(self, cmd: TransformationCommand) -> RecordResult:
        payload_hash = hashlib.sha256(_canonical(cmd).encode()).hexdigest()
        key = (cmd.source, cmd.idempotency_key)
        with self.store.lock:
            existing_id = self.store.idempotency.get(key)
            if existing_id is not None:
                existing = self._event(existing_id)
                if existing.payload_hash == payload_hash:
                    return RecordResult(RecordStatus.REPLAYED, existing)
                quarantine_id = self.store.next_id("QAR")
                self.store.quarantine[quarantine_id] = QuarantinedReport(
                    quarantine_id=quarantine_id,
                    source=cmd.source,
                    idempotency_key=cmd.idempotency_key,
                    payload_hash=payload_hash,
                    conflicting_event_id=existing_id,
                    payload=json.loads(_canonical(cmd)),
                )
                raise IdempotencyConflict(quarantine_id)
            self._validate(cmd)
            event = self._apply(cmd, payload_hash)
            self.store.idempotency[key] = event.event_id
            return RecordResult(RecordStatus.APPLIED, event)

    def _event(self, event_id: str) -> TransformationEvent:
        for event in self.store.events:
            if event.event_id == event_id:
                return event
        raise NotFoundError(f"事件不存在: {event_id}")

    def _validate(self, cmd: TransformationCommand) -> None:
        s = self.store
        t = cmd.event_type
        if cmd.loss < 0:
            raise ConservationViolation("损耗不能为负")
        for p in cmd.inputs:
            if p.quantity <= 0:
                raise StateError("投入数量必须为正")
        for o in cmd.outputs:
            if o.quantity <= 0:
                raise StateError("产出数量必须为正")
        input_ids = [p.batch_id for p in cmd.inputs]
        if len(set(input_ids)) != len(input_ids):
            raise StateError("同一批次在一次变换中只能出现一次")

        if t is EventType.RECEIVE:
            if cmd.inputs:
                raise StateError("收货不允许有投入")
            if not cmd.outputs:
                raise StateError("收货必须有产出批次")
            if cmd.loss != 0:
                raise ConservationViolation("收货不允许有损耗")
        elif t is EventType.SHIP:
            if len(cmd.inputs) != 1 or cmd.outputs:
                raise StateError("出库需要且仅需要一个投入批次, 且无产出批次")
            if cmd.loss != 0:
                raise ConservationViolation("出库不允许有损耗")
            if not cmd.customer:
                raise StateError("出库必须指定客户(销售去向)")
        elif t is EventType.STOCK_IN:
            if len(cmd.inputs) != 1 or len(cmd.outputs) != 1:
                raise StateError("成品入库需要一对一批次")
            if cmd.outputs[0].batch_id != cmd.inputs[0].batch_id:
                raise StateError("成品入库不改变批次号")
            if cmd.outputs[0].quantity != cmd.inputs[0].quantity or cmd.loss != 0:
                raise ConservationViolation("成品入库不允许数量变化或损耗")
        elif t is EventType.SPLIT:
            if len(cmd.inputs) != 1 or not cmd.outputs:
                raise StateError("拆分需要一个投入批次和至少一个产出批次")
        elif t is EventType.MERGE:
            if len(cmd.inputs) < 2 or not cmd.outputs:
                raise StateError("合并需要至少两个投入批次和一个产出批次")
        elif t is EventType.PROCESS:
            if not cmd.inputs or not cmd.outputs:
                raise StateError("炮制需要投入批次和产出批次")

        if t in _CONSERVATION_TYPES:
            total_in = sum((p.quantity for p in cmd.inputs), Decimal("0"))
            total_out = sum((o.quantity for o in cmd.outputs), Decimal("0"))
            if total_in != total_out + cmd.loss:
                raise ConservationViolation(
                    f"不守恒: 投入 {total_in} != 产出 {total_out} + 损耗 {cmd.loss}"
                )

        for p in cmd.inputs:
            batch = s.batches.get(p.batch_id)
            if batch is None:
                raise NotFoundError(f"批次不存在: {p.batch_id}")
            if batch.unit != cmd.unit:
                raise StateError(f"单位不一致: 批次 {p.batch_id} 为 {batch.unit}")
            if cmd.expected_versions and p.batch_id in cmd.expected_versions:
                if batch.version != cmd.expected_versions[p.batch_id]:
                    raise VersionConflict(
                        f"批次 {p.batch_id} 库存版本已变化, 请重读后重试"
                    )
            if t is EventType.STOCK_IN:
                if batch.kind is BatchKind.FINISHED:
                    raise StateError("批次已是成品")
                if batch.frozen != 0 or p.quantity != batch.quantity:
                    raise StateError("成品入库需整批且未被冻结")
            elif p.quantity > batch.available:
                raise InsufficientQuantity(
                    f"批次 {p.batch_id} 可用 {batch.available} 不足 {p.quantity}"
                )

        if t is not EventType.STOCK_IN:
            seen: set[str] = set()
            for o in cmd.outputs:
                if o.batch_id in s.batches or o.batch_id in seen:
                    raise StateError(f"产出批次号已存在: {o.batch_id}")
                seen.add(o.batch_id)

    def _apply(self, cmd: TransformationCommand, payload_hash: str) -> TransformationEvent:
        s = self.store
        seq = s.next_seq()
        event_id = f"EVT-{seq:06d}"
        t = cmd.event_type

        if t is EventType.STOCK_IN:
            batch = s.batches[cmd.inputs[0].batch_id]
            batch.kind = BatchKind.FINISHED
            batch.version += 1
            refresh_status(batch)
        else:
            for p in cmd.inputs:
                batch = s.batches[p.batch_id]
                batch.quantity -= p.quantity
                batch.version += 1
                refresh_status(batch)
            for o in cmd.outputs:
                s.batches[o.batch_id] = Batch(
                    batch_id=o.batch_id,
                    material_code=o.material_code,
                    kind=o.kind,
                    unit=cmd.unit,
                    quantity=o.quantity,
                    initial_quantity=o.quantity,
                    version=1,
                    created_by=event_id,
                )
        if t is EventType.SHIP:
            shipment_id = s.next_id("SHP")
            s.shipments[shipment_id] = Shipment(
                shipment_id=shipment_id,
                event_id=event_id,
                batch_id=cmd.inputs[0].batch_id,
                quantity=cmd.inputs[0].quantity,
                customer=cmd.customer or "",
            )

        event = TransformationEvent(
            event_id=event_id,
            seq=seq,
            event_type=t,
            idempotency_key=cmd.idempotency_key,
            source=cmd.source,
            operation_version=cmd.operation_version,
            unit=cmd.unit,
            inputs=list(cmd.inputs),
            outputs=[Portion(o.batch_id, o.quantity) for o in cmd.outputs],
            loss=cmd.loss,
            actor=cmd.actor,
            payload_hash=payload_hash,
            note=cmd.note,
        )
        s.events.append(event)
        return event
