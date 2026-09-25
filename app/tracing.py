"""双向追溯: 成品 -> 投入与检验依据; 问题批次 -> 去向、处置结果与未完成责任。"""

from .models import DisposalStatus, ProposalStatus
from .serialize import (
    batch_dict,
    disposal_dict,
    inspection_dict,
    notification_dict,
    q,
    shipment_dict,
)
from .store import Store, require_batch


def _ancestors(store: Store, root: str) -> list[str]:
    seen, order, stack = set(), [], [root]
    while stack:
        bid = stack.pop()
        for e in store.edges_to.get(bid, []):
            if e.from_batch not in seen:
                seen.add(e.from_batch)
                order.append(e.from_batch)
                stack.append(e.from_batch)
    return order


def _descendants(store: Store, root: str) -> list[str]:
    seen, order, stack = set(), [], [root]
    while stack:
        bid = stack.pop()
        for e in store.edges_from.get(bid, []):
            if e.to_batch not in seen:
                seen.add(e.to_batch)
                order.append(e.to_batch)
                stack.append(e.to_batch)
    return order


def backward_trace(store: Store, batch_id: str) -> dict:
    """从成品反向追溯: 全部投入批次 + 各批次的检验依据(含校准是否已撤销)。"""
    require_batch(store, batch_id)
    ancestors = _ancestors(store, batch_id)
    inspections = {}
    for bid in [batch_id, *ancestors]:
        records = sorted(
            (i for i in store.inspections.values() if i.batch_id == bid),
            key=lambda i: (i.method, i.version),
        )
        if records:
            inspections[bid] = [
                inspection_dict(i, i.calibration_version in store.revocations)
                for i in records
            ]
    return {
        "batch": batch_dict(store.batches[batch_id]),
        "inputs": [batch_dict(store.batches[b]) for b in ancestors],
        "inspections": inspections,
    }


def forward_trace(store: Store, batch_id: str) -> dict:
    """从问题批次正向追溯: 下游去向、销售发货、处置结果与未完成责任。"""
    require_batch(store, batch_id)
    descendants = _descendants(store, batch_id)
    involved = [batch_id, *descendants]
    return {
        "batch": batch_dict(store.batches[batch_id]),
        "destinations": [batch_dict(store.batches[b]) for b in descendants],
        "shipments": [shipment_dict(s) for s in store.shipments.values()
                      if s.batch_id in involved],
        "disposals": [disposal_dict(o) for o in store.disposals.values()
                      if o.batch_id in involved],
        "notifications": [notification_dict(n) for n in store.notifications.values()
                          if n.batch_id in involved],
        "pending_responsibilities": _pending(store, involved),
    }


def responsibilities(store: Store, batch_id: str) -> dict:
    """批次及其下游的未完成责任清单。"""
    require_batch(store, batch_id)
    descendants = _descendants(store, batch_id)
    involved = [batch_id, *descendants]
    return {
        "batch_id": batch_id,
        "descendants": descendants,
        "pending": _pending(store, involved),
    }


def _pending(store: Store, involved: list[str]) -> dict:
    involved_set = set(involved)
    orders = [
        {
            "order_id": o.id,
            "batch_id": o.batch_id,
            "remaining_qty": q(o.approved_qty - o.executed_qty),
        }
        for o in store.disposals.values()
        if o.batch_id in involved_set
        and o.status in (DisposalStatus.PENDING, DisposalStatus.PARTIAL)
    ]
    notifs = [
        {"notification_id": n.id, "customer": n.customer, "qty": q(n.qty)}
        for n in store.notifications.values()
        if n.batch_id in involved_set and not n.done and not n.cancelled
    ]
    awaiting = [
        {"proposal_id": p.id, "version": p.version}
        for p in store.proposals.values()
        if p.status == ProposalStatus.SUBMITTED
        and involved_set & set(p.scope)
    ]
    return {
        "disposal_orders": orders,
        "notifications": notifs,
        "awaiting_approval": awaiting,
    }
