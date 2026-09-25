"""HTTP 路由: 只做参数解析与序列化, 业务规则全部在领域服务中。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter

from app.api.schemas import (
    AssessmentIn,
    CalibrationRevokeIn,
    DispositionIn,
    InspectionIn,
    LockIn,
    PropagationRunIn,
    PropagationStartIn,
    ReviewIn,
    TransformationIn,
)
from app.domain.models import (
    AffectedItem,
    Batch,
    Disposition,
    ImpactAssessment,
    Inspection,
    NotificationTask,
    PropagationJob,
    QuantityLock,
    QuarantinedReport,
    Shipment,
    TransformationEvent,
)
from app.domain.trace import BackwardTrace, ForwardTrace
from app.services import DomainState


def _d(value: Decimal) -> str:
    return str(value)


def batch_dict(b: Batch) -> dict:
    return {
        "batch_id": b.batch_id,
        "material_code": b.material_code,
        "kind": b.kind.value,
        "unit": b.unit,
        "quantity": _d(b.quantity),
        "initial_quantity": _d(b.initial_quantity),
        "frozen": _d(b.frozen),
        "available": _d(b.available),
        "version": b.version,
        "status": b.status.value,
        "created_by": b.created_by,
    }


def event_dict(e: TransformationEvent) -> dict:
    return {
        "event_id": e.event_id,
        "seq": e.seq,
        "event_type": e.event_type.value,
        "idempotency_key": e.idempotency_key,
        "source": e.source,
        "operation_version": e.operation_version,
        "unit": e.unit,
        "inputs": [{"batch_id": p.batch_id, "quantity": _d(p.quantity)} for p in e.inputs],
        "outputs": [{"batch_id": p.batch_id, "quantity": _d(p.quantity)} for p in e.outputs],
        "loss": _d(e.loss),
        "actor": e.actor,
        "note": e.note,
    }


def inspection_dict(i: Inspection) -> dict:
    return {
        "inspection_id": i.inspection_id,
        "batch_id": i.batch_id,
        "method": i.method,
        "sample_scope": i.sample_scope,
        "calibration_version": i.calibration_version,
        "result": i.result.value,
        "inspector": i.inspector,
        "status": i.status.value,
        "superseded_by": i.superseded_by,
    }


def assessment_dict(a: ImpactAssessment) -> dict:
    return {
        "assessment_id": a.assessment_id,
        "root_batch_id": a.root_batch_id,
        "version": a.version,
        "items": [{"batch_id": i.batch_id, "quantity": _d(i.quantity)} for i in a.items],
        "basis": a.basis,
        "proposed_by": a.proposed_by,
        "approved_by": a.approved_by,
        "status": a.status.value,
    }


def disposition_dict(d: Disposition) -> dict:
    return {
        "disposition_id": d.disposition_id,
        "assessment_id": d.assessment_id,
        "batch_id": d.batch_id,
        "quantity": _d(d.quantity),
        "action": d.action,
        "executor": d.executor,
    }


def lock_dict(l: QuantityLock) -> dict:
    return {
        "lock_id": l.lock_id,
        "batch_id": l.batch_id,
        "quantity": _d(l.quantity),
        "reason": l.reason,
        "assessment_id": l.assessment_id,
        "active": l.active,
    }


def shipment_dict(s: Shipment) -> dict:
    return {
        "shipment_id": s.shipment_id,
        "event_id": s.event_id,
        "batch_id": s.batch_id,
        "quantity": _d(s.quantity),
        "customer": s.customer,
    }


def notification_dict(t: NotificationTask) -> dict:
    return {
        "task_id": t.task_id,
        "shipment_id": t.shipment_id,
        "batch_id": t.batch_id,
        "customer": t.customer,
        "quantity": _d(t.quantity),
        "assessment_id": t.assessment_id,
        "done": t.done,
        "cancelled": t.cancelled,
    }


def quarantine_dict(q: QuarantinedReport) -> dict:
    return {
        "quarantine_id": q.quarantine_id,
        "source": q.source,
        "idempotency_key": q.idempotency_key,
        "conflicting_event_id": q.conflicting_event_id,
        "payload": q.payload,
    }


def job_dict(j: PropagationJob) -> dict:
    return {
        "job_id": j.job_id,
        "root_batch_id": j.root_batch_id,
        "frontier": list(j.frontier),
        "confirmed": list(j.confirmed),
        "remaining": {k: _d(v) for k, v in j.remaining.items()},
        "affected": {k: _d(v) for k, v in j.affected.items()},
        "done": j.done,
    }


def backward_dict(t: BackwardTrace) -> dict:
    return {
        "root": t.root,
        "batches": {k: batch_dict(v) for k, v in t.batches.items()},
        "events": [event_dict(e) for e in t.events],
        "inspections": {
            k: [inspection_dict(i) for i in v] for k, v in t.inspections.items()
        },
    }


def forward_dict(t: ForwardTrace) -> dict:
    return {
        "root": t.root,
        "batches": {k: batch_dict(v) for k, v in t.batches.items()},
        "events": [event_dict(e) for e in t.events],
        "shipments": [shipment_dict(s) for s in t.shipments],
        "dispositions": [disposition_dict(d) for d in t.dispositions],
        "notifications": [notification_dict(n) for n in t.notifications],
        "active_locks": [lock_dict(l) for l in t.active_locks],
        "outstanding": list(t.outstanding),
    }


def build_router(state: DomainState) -> APIRouter:
    r = APIRouter()

    @r.post("/transformations")
    def record_transformation(body: TransformationIn) -> dict:
        result = state.genealogy.record(body.to_command())
        return {"status": result.status.value, "event": event_dict(result.event)}

    @r.get("/events")
    def list_events() -> list[dict]:
        return [event_dict(e) for e in state.store.events]

    @r.get("/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict:
        batch = state.store.batches.get(batch_id)
        if batch is None:
            from app.domain.errors import NotFoundError

            raise NotFoundError(f"批次不存在: {batch_id}")
        return batch_dict(batch)

    @r.get("/quarantine")
    def list_quarantine() -> list[dict]:
        return [quarantine_dict(q) for q in state.store.quarantine.values()]

    @r.post("/inspections")
    def record_inspection(body: InspectionIn) -> dict:
        inspection = state.inspections.record(
            batch_id=body.batch_id,
            method=body.method,
            sample_scope=body.sample_scope,
            calibration_version=body.calibration_version,
            result=body.result,
            inspector=body.inspector,
            supersedes=body.supersedes,
        )
        return inspection_dict(inspection)

    @r.post("/calibrations/revoke")
    def revoke_calibration(body: CalibrationRevokeIn) -> list[dict]:
        return [
            inspection_dict(i)
            for i in state.inspections.revoke_calibration(body.calibration_version)
        ]

    @r.post("/locks")
    def lock_quantity(body: LockIn) -> dict:
        qlock = state.inventory.lock(
            body.batch_id, body.quantity, body.reason,
            expected_version=body.expected_version,
        )
        return lock_dict(qlock)

    @r.post("/locks/{lock_id}/release")
    def release_lock(lock_id: str) -> dict:
        state.inventory.release(lock_id)
        return {"released": lock_id}

    @r.post("/assessments")
    def propose_assessment(body: AssessmentIn) -> dict:
        assessment = state.impact.propose(
            root_batch_id=body.root_batch_id,
            items=[AffectedItem(i.batch_id, i.quantity) for i in body.items],
            basis=body.basis,
            proposed_by=body.proposed_by,
        )
        return assessment_dict(assessment)

    @r.post("/assessments/{assessment_id}/approve")
    def approve_assessment(assessment_id: str, body: ReviewIn) -> dict:
        return assessment_dict(state.impact.approve(assessment_id, body.reviewer))

    @r.post("/assessments/{assessment_id}/reject")
    def reject_assessment(assessment_id: str, body: ReviewIn) -> dict:
        return assessment_dict(state.impact.reject(assessment_id, body.reviewer))

    @r.post("/assessments/{assessment_id}/dispositions")
    def execute_disposition(assessment_id: str, body: DispositionIn) -> dict:
        disposition = state.impact.execute(
            assessment_id=assessment_id,
            batch_id=body.batch_id,
            quantity=body.quantity,
            action=body.action,
            executor=body.executor,
        )
        return disposition_dict(disposition)

    @r.post("/notifications/{task_id}/complete")
    def complete_notification(task_id: str) -> dict:
        return notification_dict(state.impact.complete_notification(task_id))

    @r.post("/propagation/jobs")
    def start_propagation(body: PropagationStartIn) -> dict:
        return job_dict(state.propagation.start(body.root_batch_id))

    @r.post("/propagation/jobs/{job_id}/run")
    def run_propagation(job_id: str, body: PropagationRunIn) -> dict:
        return job_dict(state.propagation.run(job_id, body.max_steps))

    @r.get("/trace/backward/{batch_id}")
    def trace_backward(batch_id: str) -> dict:
        return backward_dict(state.trace.backward(batch_id))

    @r.get("/trace/forward/{batch_id}")
    def trace_forward(batch_id: str) -> dict:
        return forward_dict(state.trace.forward(batch_id))

    return r
