"""双向追溯。

向上: 从成品批次沿谱系事件回溯全部投入批次与各批次的检验依据。
向下: 从问题批次列出每个下游去向、出库销售、处置结果与未完成责任
(已获批未执行的处置、未完成的客户通知)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .errors import NotFoundError
from .models import (
    AssessmentStatus,
    Batch,
    Disposition,
    EventType,
    Inspection,
    NotificationTask,
    QuantityLock,
    Shipment,
    TransformationEvent,
)
from .store import Store


@dataclass
class BackwardTrace:
    root: str
    batches: dict[str, Batch] = field(default_factory=dict)
    events: list[TransformationEvent] = field(default_factory=list)
    inspections: dict[str, list[Inspection]] = field(default_factory=dict)


@dataclass
class ForwardTrace:
    root: str
    batches: dict[str, Batch] = field(default_factory=dict)
    events: list[TransformationEvent] = field(default_factory=list)
    shipments: list[Shipment] = field(default_factory=list)
    dispositions: list[Disposition] = field(default_factory=list)
    notifications: list[NotificationTask] = field(default_factory=list)
    active_locks: list[QuantityLock] = field(default_factory=list)
    outstanding: list[str] = field(default_factory=list)


class TraceService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def backward(self, batch_id: str) -> BackwardTrace:
        with self.store.lock:
            self._require(batch_id)
            trace = BackwardTrace(root=batch_id)
            seen_events: set[str] = set()
            stack = [batch_id]
            while stack:
                bid = stack.pop()
                if bid in trace.batches:
                    continue
                trace.batches[bid] = self.store.batches[bid]
                for event in self.store.events:
                    if event.event_id in seen_events:
                        continue
                    if any(o.batch_id == bid for o in event.outputs):
                        seen_events.add(event.event_id)
                        trace.events.append(event)
                        stack.extend(p.batch_id for p in event.inputs)
            trace.events.sort(key=lambda e: e.seq)
            trace.inspections = {
                bid: [i for i in self.store.inspections.values() if i.batch_id == bid]
                for bid in trace.batches
            }
            return trace

    def forward(self, batch_id: str) -> ForwardTrace:
        with self.store.lock:
            self._require(batch_id)
            trace = ForwardTrace(root=batch_id)
            seen_events: set[str] = set()
            stack = [batch_id]
            while stack:
                bid = stack.pop()
                if bid in trace.batches:
                    continue
                trace.batches[bid] = self.store.batches[bid]
                for event in self.store.events:
                    if event.event_id in seen_events:
                        continue
                    if event.event_type is EventType.STOCK_IN:
                        continue
                    if any(p.batch_id == bid for p in event.inputs):
                        seen_events.add(event.event_id)
                        trace.events.append(event)
                        stack.extend(o.batch_id for o in event.outputs)
            trace.events.sort(key=lambda e: e.seq)
            involved = set(trace.batches)
            trace.shipments = [
                s for s in self.store.shipments.values() if s.batch_id in involved
            ]
            trace.dispositions = [
                d for d in self.store.dispositions if d.batch_id in involved
            ]
            trace.notifications = [
                t
                for t in self.store.notifications.values()
                if t.batch_id in involved and not t.cancelled
            ]
            trace.active_locks = [
                l
                for l in self.store.locks.values()
                if l.active and l.batch_id in involved
            ]
            trace.outstanding = self._outstanding(involved)
            return trace

    def _outstanding(self, involved: set[str]) -> list[str]:
        items: list[str] = []
        for assessment in self.store.assessments.values():
            if assessment.status is not AssessmentStatus.APPROVED:
                continue
            executed: dict[str, Decimal] = {}
            for d in self.store.dispositions:
                if d.assessment_id == assessment.assessment_id:
                    executed[d.batch_id] = executed.get(d.batch_id, Decimal("0")) + d.quantity
            for item in assessment.items:
                if item.batch_id not in involved:
                    continue
                remaining = item.quantity - executed.get(item.batch_id, Decimal("0"))
                if remaining > 0:
                    items.append(
                        f"批次 {item.batch_id} 待处置 {remaining} "
                        f"(评估 {assessment.assessment_id} v{assessment.version})"
                    )
        for task in self.store.notifications.values():
            if task.batch_id in involved and not task.done and not task.cancelled:
                items.append(
                    f"客户 {task.customer} 待通知: 批次 {task.batch_id} 数量 {task.quantity} "
                    f"(任务 {task.task_id})"
                )
        return items

    def _require(self, batch_id: str) -> None:
        if batch_id not in self.store.batches:
            raise NotFoundError(f"批次不存在: {batch_id}")
