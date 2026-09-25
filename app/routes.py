"""HTTP API 层。请求模型 + 路由, 业务逻辑全部委托给领域服务。"""

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from . import genealogy, propagation, quality, tracing
from .errors import ConflictError
from .models import EventType, JobStatus, Portion
from .serialize import (
    batch_dict,
    disposal_dict,
    event_dict,
    inspection_dict,
    isolated_dict,
    job_dict,
    notification_dict,
    proposal_dict,
    revocation_dict,
    shipment_dict,
)
from .store import Store, require_batch

router = APIRouter()


def get_store(request: Request) -> Store:
    return request.app.state.store


# ---------- 请求模型 ----------

class PortionIn(BaseModel):
    batch_id: str
    qty: Decimal


class NamedQty(BaseModel):
    material: str | None = None
    qty: Decimal


class ReceiveIn(BaseModel):
    flow_no: str
    material: str
    qty: Decimal
    unit: str
    op_version: str
    operator: str


class SplitIn(BaseModel):
    flow_no: str
    input: PortionIn
    outputs: list[Decimal]  # 拆分产出继承投入物料
    loss_qty: Decimal = Decimal("0")
    unit: str
    op_version: str
    operator: str
    expected_versions: dict[str, int] = Field(default_factory=dict)


class MergeIn(BaseModel):
    flow_no: str
    inputs: list[PortionIn]
    output_material: str
    output_qty: Decimal
    loss_qty: Decimal = Decimal("0")
    unit: str
    op_version: str
    operator: str
    expected_versions: dict[str, int] = Field(default_factory=dict)


class ProcessIn(BaseModel):
    flow_no: str
    inputs: list[PortionIn]
    outputs: list[NamedQty]
    loss_qty: Decimal = Decimal("0")
    unit: str
    op_version: str
    operator: str
    expected_versions: dict[str, int] = Field(default_factory=dict)


class WarehouseIn(BaseModel):
    flow_no: str
    input: PortionIn
    output_qty: Decimal
    output_material: str | None = None
    loss_qty: Decimal = Decimal("0")
    unit: str
    op_version: str
    operator: str
    expected_versions: dict[str, int] = Field(default_factory=dict)


class OutboundIn(BaseModel):
    flow_no: str
    batch_id: str
    qty: Decimal
    customer: str
    op_version: str = "OUT-1"
    operator: str
    expected_version: int | None = None


class InspectionIn(BaseModel):
    batch_id: str
    method: str
    sample_scope: str
    calibration_version: str
    result: Literal["PASS", "FAIL"]
    inspector: str
    supersedes: str | None = None


class RevokeIn(BaseModel):
    calibration_version: str
    revoked_by: str
    reason: str


class ProposalIn(BaseModel):
    cause: dict
    scope: dict[str, Decimal]
    basis: str
    proposed_by: str
    supersedes: str | None = None


class ProposalFromJobIn(BaseModel):
    job_id: str
    basis: str
    proposed_by: str
    cause: dict | None = None
    supersedes: str | None = None


class ApproveIn(BaseModel):
    approver: str


class RejectIn(BaseModel):
    reviewer: str
    reason: str = ""


class ExecuteIn(BaseModel):
    qty: Decimal
    action: Literal["DESTROY", "RETURN", "RELEASE"]
    executor: str
    expected_version: int | None = None


class NotifyDoneIn(BaseModel):
    notified_by: str


class JobIn(BaseModel):
    root_batch_ids: list[str]
    max_steps: int = 1000


class ResumeIn(BaseModel):
    max_steps: int = 1000


# ---------- 批次谱系事件 ----------

def _event_response(res) -> dict:
    return {
        "event": event_dict(res.event),
        "batches": [batch_dict(b) for b in res.batches],
        "shipment": shipment_dict(res.shipment) if res.shipment else None,
        "reused": res.reused,
    }


@router.post("/batches/receive")
def receive(body: ReceiveIn, store: Store = Depends(get_store)):
    res = genealogy.record_event(
        store, flow_no=body.flow_no, event_type=EventType.RECEIVE, inputs=[],
        outputs=[genealogy.OutputSpec(body.material, body.qty)],
        unit=body.unit, op_version=body.op_version, operator=body.operator,
    )
    return _event_response(res)


@router.post("/batches/split")
def split(body: SplitIn, store: Store = Depends(get_store)):
    res = genealogy.record_event(
        store, flow_no=body.flow_no, event_type=EventType.SPLIT,
        inputs=[Portion(body.input.batch_id, body.input.qty)],
        outputs=[genealogy.OutputSpec(None, qty) for qty in body.outputs],
        loss_qty=body.loss_qty, unit=body.unit,
        op_version=body.op_version, operator=body.operator,
        expected_versions=body.expected_versions,
    )
    return _event_response(res)


@router.post("/batches/merge")
def merge(body: MergeIn, store: Store = Depends(get_store)):
    res = genealogy.record_event(
        store, flow_no=body.flow_no, event_type=EventType.MERGE,
        inputs=[Portion(p.batch_id, p.qty) for p in body.inputs],
        outputs=[genealogy.OutputSpec(body.output_material, body.output_qty)],
        loss_qty=body.loss_qty, unit=body.unit,
        op_version=body.op_version, operator=body.operator,
        expected_versions=body.expected_versions,
    )
    return _event_response(res)


@router.post("/batches/process")
def process(body: ProcessIn, store: Store = Depends(get_store)):
    res = genealogy.record_event(
        store, flow_no=body.flow_no, event_type=EventType.PROCESS,
        inputs=[Portion(p.batch_id, p.qty) for p in body.inputs],
        outputs=[genealogy.OutputSpec(o.material, o.qty) for o in body.outputs],
        loss_qty=body.loss_qty, unit=body.unit,
        op_version=body.op_version, operator=body.operator,
        expected_versions=body.expected_versions,
    )
    return _event_response(res)


@router.post("/batches/warehouse")
def warehouse(body: WarehouseIn, store: Store = Depends(get_store)):
    res = genealogy.record_event(
        store, flow_no=body.flow_no, event_type=EventType.WAREHOUSE,
        inputs=[Portion(body.input.batch_id, body.input.qty)],
        outputs=[genealogy.OutputSpec(body.output_material, body.output_qty)],
        loss_qty=body.loss_qty, unit=body.unit,
        op_version=body.op_version, operator=body.operator,
        expected_versions=body.expected_versions,
    )
    return _event_response(res)


@router.post("/batches/outbound")
def outbound(body: OutboundIn, store: Store = Depends(get_store)):
    expected = {body.batch_id: body.expected_version} if body.expected_version is not None else None
    res = genealogy.record_event(
        store, flow_no=body.flow_no, event_type=EventType.OUTBOUND,
        inputs=[Portion(body.batch_id, body.qty)], outputs=[],
        unit=_batch_unit(store, body.batch_id), op_version=body.op_version,
        operator=body.operator, customer=body.customer,
        expected_versions=expected,
    )
    return _event_response(res)


def _batch_unit(store: Store, batch_id: str) -> str:
    return require_batch(store, batch_id).unit


# ---------- 批次与追溯查询 ----------

@router.get("/batches")
def list_batches(store: Store = Depends(get_store)):
    return [batch_dict(b) for b in store.batches.values()]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, store: Store = Depends(get_store)):
    return batch_dict(require_batch(store, batch_id))


@router.get("/batches/{batch_id}/trace/backward")
def trace_backward(batch_id: str, store: Store = Depends(get_store)):
    return tracing.backward_trace(store, batch_id)


@router.get("/batches/{batch_id}/trace/forward")
def trace_forward(batch_id: str, store: Store = Depends(get_store)):
    return tracing.forward_trace(store, batch_id)


@router.get("/batches/{batch_id}/responsibilities")
def batch_responsibilities(batch_id: str, store: Store = Depends(get_store)):
    return tracing.responsibilities(store, batch_id)


# ---------- 事件台账与隔离区 ----------

@router.get("/events")
def list_events(store: Store = Depends(get_store)):
    return [event_dict(e) for e in store.events]


@router.get("/events/verify")
def verify_events(store: Store = Depends(get_store)):
    return {"valid": genealogy.verify_chain(store), "events": len(store.events)}


@router.get("/isolated-reports")
def list_isolated(store: Store = Depends(get_store)):
    return [isolated_dict(r) for r in store.isolated.values()]


# ---------- 检验与校准 ----------

@router.post("/inspections")
def create_inspection(body: InspectionIn, store: Store = Depends(get_store)):
    ins = quality.record_inspection(
        store, batch_id=body.batch_id, method=body.method,
        sample_scope=body.sample_scope, calibration_version=body.calibration_version,
        result=body.result, inspector=body.inspector, supersedes=body.supersedes,
    )
    return inspection_dict(ins, ins.calibration_version in store.revocations)


@router.get("/inspections")
def list_inspections(batch_id: str | None = None, store: Store = Depends(get_store)):
    records = store.inspections.values()
    if batch_id is not None:
        records = [i for i in records if i.batch_id == batch_id]
    return [inspection_dict(i, i.calibration_version in store.revocations) for i in records]


@router.post("/calibrations/revoke")
def revoke_calibration(body: RevokeIn, store: Store = Depends(get_store)):
    rev, affected, reused = quality.revoke_calibration(
        store, calibration_version=body.calibration_version,
        revoked_by=body.revoked_by, reason=body.reason,
    )
    return {
        "revocation": revocation_dict(rev),
        "reused": reused,
        "affected_inspections": [inspection_dict(i, True) for i in affected],
        "affected_batches": sorted({i.batch_id for i in affected}),
    }


# ---------- 影响范围方案 ----------

@router.post("/impact-proposals")
def create_proposal(body: ProposalIn, store: Store = Depends(get_store)):
    p = quality.create_proposal(
        store, cause=body.cause, scope=body.scope, basis=body.basis,
        proposed_by=body.proposed_by, supersedes=body.supersedes,
    )
    return proposal_dict(p)


@router.post("/impact-proposals/from-job")
def create_proposal_from_job(body: ProposalFromJobIn, store: Store = Depends(get_store)):
    job = propagation.get_job(store, body.job_id)
    if job.status != JobStatus.COMPLETED:
        raise ConflictError("传播作业尚未完成, 请先续跑至完成", status=job.status.value)
    scope = {bid: qty for bid, qty in job.affected.items() if qty > 0}
    cause = body.cause or {"type": "PROPAGATION", "job_id": job.id}
    p = quality.create_proposal(
        store, cause=cause, scope=scope, basis=body.basis,
        proposed_by=body.proposed_by, supersedes=body.supersedes,
    )
    return proposal_dict(p)


@router.get("/impact-proposals")
def list_proposals(store: Store = Depends(get_store)):
    return [proposal_dict(p) for p in store.proposals.values()]


@router.get("/impact-proposals/{proposal_id}")
def get_proposal(proposal_id: str, store: Store = Depends(get_store)):
    p = quality._require_proposal(store, proposal_id)
    return {
        "proposal": proposal_dict(p),
        "disposal_orders": [disposal_dict(o) for o in store.disposals.values()
                            if o.proposal_id == p.id],
        "notifications": [notification_dict(n) for n in store.notifications.values()
                          if n.proposal_id == p.id],
    }


@router.post("/impact-proposals/{proposal_id}/submit")
def submit_proposal(proposal_id: str, store: Store = Depends(get_store)):
    return proposal_dict(quality.submit_proposal(store, proposal_id))


@router.post("/impact-proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, body: ApproveIn, store: Store = Depends(get_store)):
    p, orders, notifs = quality.approve_proposal(store, proposal_id, approver=body.approver)
    return {
        "proposal": proposal_dict(p),
        "disposal_orders": [disposal_dict(o) for o in orders],
        "notifications": [notification_dict(n) for n in notifs],
    }


@router.post("/impact-proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, body: RejectIn, store: Store = Depends(get_store)):
    return proposal_dict(quality.reject_proposal(
        store, proposal_id, reviewer=body.reviewer, reason=body.reason,
    ))


# ---------- 处置与通知 ----------

@router.post("/disposals/{order_id}/execute")
def execute_disposal(order_id: str, body: ExecuteIn, store: Store = Depends(get_store)):
    order = quality.execute_disposal(
        store, order_id, qty=body.qty, action=body.action,
        executor=body.executor, expected_version=body.expected_version,
    )
    return disposal_dict(order)


@router.get("/disposals")
def list_disposals(batch_id: str | None = None, store: Store = Depends(get_store)):
    orders = store.disposals.values()
    if batch_id is not None:
        orders = [o for o in orders if o.batch_id == batch_id]
    return [disposal_dict(o) for o in orders]


@router.post("/notifications/{notification_id}/complete")
def complete_notification(notification_id: str, body: NotifyDoneIn,
                          store: Store = Depends(get_store)):
    return notification_dict(quality.complete_notification(
        store, notification_id, notified_by=body.notified_by,
    ))


@router.get("/notifications")
def list_notifications(batch_id: str | None = None, store: Store = Depends(get_store)):
    tasks = store.notifications.values()
    if batch_id is not None:
        tasks = [n for n in tasks if n.batch_id == batch_id]
    return [notification_dict(n) for n in tasks]


# ---------- 影响传播作业 ----------

@router.post("/propagation-jobs")
def create_and_run_job(body: JobIn, store: Store = Depends(get_store)):
    job = propagation.create_job(store, root_batch_ids=body.root_batch_ids)
    return job_dict(propagation.run_job(store, job.id, max_steps=body.max_steps))


@router.post("/propagation-jobs/{job_id}/resume")
def resume_job(job_id: str, body: ResumeIn, store: Store = Depends(get_store)):
    return job_dict(propagation.run_job(store, job_id, max_steps=body.max_steps))


@router.get("/propagation-jobs/{job_id}")
def get_job(job_id: str, store: Store = Depends(get_store)):
    return job_dict(propagation.get_job(store, job_id))
