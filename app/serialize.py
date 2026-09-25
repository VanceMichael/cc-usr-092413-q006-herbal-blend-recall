"""实体 -> API 响应字典。数量统一序列化为字符串, 避免浮点误差。"""

from decimal import Decimal

from .models import (
    Batch,
    BatchEvent,
    CalibrationRevocation,
    DisposalOrder,
    ImpactProposal,
    Inspection,
    IsolatedReport,
    NotificationTask,
    OutboundShipment,
    PropagationJob,
)


def q(v: Decimal) -> str:
    return format(v.normalize(), "f")


def batch_dict(b: Batch) -> dict:
    return {
        "id": b.id,
        "material": b.material,
        "kind": b.kind.value,
        "unit": b.unit,
        "qty_total": q(b.qty_total),
        "qty_frozen": q(b.qty_frozen),
        "qty_available": q(b.available),
        "status": b.status.value,
        "version": b.version,
        "created_by_event": b.created_by_event,
        "created_at": b.created_at.isoformat(),
    }


def _portion_dict(p) -> dict:
    return {"batch_id": p.batch_id, "qty": q(p.qty)}


def event_dict(e: BatchEvent) -> dict:
    return {
        "id": e.id,
        "flow_no": e.flow_no,
        "type": e.type.value,
        "inputs": [_portion_dict(p) for p in e.inputs],
        "outputs": [_portion_dict(p) for p in e.outputs],
        "loss_qty": q(e.loss_qty),
        "unit": e.unit,
        "op_version": e.op_version,
        "operator": e.operator,
        "payload_hash": e.payload_hash,
        "prev_hash": e.prev_hash,
        "hash": e.hash,
        "created_at": e.created_at.isoformat(),
    }


def inspection_dict(i: Inspection, calibration_revoked: bool) -> dict:
    return {
        "id": i.id,
        "batch_id": i.batch_id,
        "method": i.method,
        "sample_scope": i.sample_scope,
        "calibration_version": i.calibration_version,
        "calibration_revoked": calibration_revoked,
        "result": i.result,
        "version": i.version,
        "supersedes": i.supersedes,
        "inspector": i.inspector,
        "created_at": i.created_at.isoformat(),
    }


def revocation_dict(r: CalibrationRevocation) -> dict:
    return {
        "calibration_version": r.calibration_version,
        "reason": r.reason,
        "revoked_by": r.revoked_by,
        "created_at": r.created_at.isoformat(),
    }


def proposal_dict(p: ImpactProposal) -> dict:
    return {
        "id": p.id,
        "version": p.version,
        "cause": p.cause,
        "scope": {bid: q(v) for bid, v in p.scope.items()},
        "basis": p.basis,
        "proposed_by": p.proposed_by,
        "status": p.status.value,
        "supersedes": p.supersedes,
        "approved_by": p.approved_by,
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "created_at": p.created_at.isoformat(),
    }


def disposal_dict(o: DisposalOrder) -> dict:
    return {
        "id": o.id,
        "proposal_id": o.proposal_id,
        "batch_id": o.batch_id,
        "approved_qty": q(o.approved_qty),
        "executed_qty": q(o.executed_qty),
        "remaining_qty": q(o.approved_qty - o.executed_qty),
        "status": o.status.value,
        "executions": o.executions,
        "created_at": o.created_at.isoformat(),
    }


def notification_dict(n: NotificationTask) -> dict:
    return {
        "id": n.id,
        "shipment_id": n.shipment_id,
        "batch_id": n.batch_id,
        "customer": n.customer,
        "qty": q(n.qty),
        "proposal_id": n.proposal_id,
        "done": n.done,
        "cancelled": n.cancelled,
        "notified_by": n.notified_by,
        "notified_at": n.notified_at.isoformat() if n.notified_at else None,
        "created_at": n.created_at.isoformat(),
    }


def shipment_dict(s: OutboundShipment) -> dict:
    return {
        "id": s.id,
        "batch_id": s.batch_id,
        "qty": q(s.qty),
        "customer": s.customer,
        "event_id": s.event_id,
        "created_at": s.created_at.isoformat(),
    }


def job_dict(j: PropagationJob) -> dict:
    return {
        "id": j.id,
        "root_batch_ids": j.root_batch_ids,
        "status": j.status.value,
        "confirmed": j.confirmed,
        "frontier": j.frontier,
        "affected": {bid: q(v) for bid, v in j.affected.items()},
        "steps": j.steps,
        "created_at": j.created_at.isoformat(),
    }


def isolated_dict(r: IsolatedReport) -> dict:
    return {
        "id": r.id,
        "flow_no": r.flow_no,
        "reason": r.reason,
        "payload": r.payload,
        "existing_event_id": r.existing_event_id,
        "created_at": r.created_at.isoformat(),
    }
