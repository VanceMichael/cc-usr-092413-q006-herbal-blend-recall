"""质量域: 检验、校准撤销、影响范围方案(提案/独立复核)、处置执行、销售通知。"""

from decimal import Decimal

from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from .models import (
    BatchStatus,
    CalibrationRevocation,
    DisposalOrder,
    DisposalStatus,
    ImpactProposal,
    Inspection,
    NotificationTask,
    ProposalStatus,
    new_id,
    utcnow,
)
from .store import Store, require_batch

VALID_RESULTS = ("PASS", "FAIL")
VALID_ACTIONS = ("DESTROY", "RETURN", "RELEASE")


# ---------- 检验与校准 ----------

def record_inspection(
    store: Store, *,
    batch_id: str, method: str, sample_scope: str,
    calibration_version: str, result: str, inspector: str,
    supersedes: str | None = None,
) -> Inspection:
    """登记检验。结果绑定方法、样品范围与校准版本; 同(批次,方法)按版本递增替代。"""
    with store.lock:
        require_batch(store, batch_id)
        if result not in VALID_RESULTS:
            raise ValidationError("检验结果须为 PASS 或 FAIL", result=result)
        if calibration_version in store.revocations:
            raise ValidationError(
                "校准版本已被撤销, 检验须使用有效校准版本",
                calibration_version=calibration_version,
            )
        history = [i for i in store.inspections.values()
                   if i.batch_id == batch_id and i.method == method]
        latest = max(history, key=lambda i: i.version, default=None)
        if supersedes is not None and (latest is None or supersedes != latest.id):
            raise ConflictError(
                "替代检验须基于当前最新检验版本",
                latest_inspection=latest.id if latest else None,
            )
        ins = Inspection(
            id=new_id("ins"), batch_id=batch_id, method=method,
            sample_scope=sample_scope, calibration_version=calibration_version,
            result=result, version=latest.version + 1 if latest else 1,
            supersedes=latest.id if latest else None, inspector=inspector,
        )
        store.inspections[ins.id] = ins
        return ins


def revoke_calibration(
    store: Store, *, calibration_version: str, revoked_by: str, reason: str,
) -> tuple[CalibrationRevocation, list[Inspection], bool]:
    """撤销校准版本。幂等; 返回受影响检验(其批次即为候选影响范围)。"""
    with store.lock:
        existing = store.revocations.get(calibration_version)
        if existing is not None:
            return existing, [], True
        rev = CalibrationRevocation(
            calibration_version=calibration_version,
            reason=reason, revoked_by=revoked_by,
        )
        store.revocations[calibration_version] = rev
        affected = [i for i in store.inspections.values()
                    if i.calibration_version == calibration_version]
        return rev, affected, False


# ---------- 影响范围方案 ----------

def create_proposal(
    store: Store, *,
    cause: dict, scope: dict[str, Decimal], basis: str,
    proposed_by: str, supersedes: str | None = None,
) -> ImpactProposal:
    """质量人员提出影响范围。supersedes 指向上一个已批准版本, 形成版本链。"""
    with store.lock:
        if not scope:
            raise ValidationError("影响范围不能为空")
        for bid, qty in scope.items():
            require_batch(store, bid)
            if qty <= 0:
                raise ValidationError("影响数量必须为正", batch_id=bid)
        version = 1
        if supersedes is not None:
            old = _require_proposal(store, supersedes)
            if old.status != ProposalStatus.APPROVED:
                raise ValidationError("仅可替代已批准的方案", supersedes=supersedes)
            version = old.version + 1
        p = ImpactProposal(
            id=new_id("prop"), version=version, cause=dict(cause),
            scope=dict(scope), basis=basis,
            proposed_by=proposed_by, supersedes=supersedes,
        )
        store.proposals[p.id] = p
        return p


def submit_proposal(store: Store, proposal_id: str) -> ImpactProposal:
    with store.lock:
        p = _require_proposal(store, proposal_id)
        if p.status != ProposalStatus.DRAFT:
            raise ConflictError("仅草案可提交复核", status=p.status.value)
        p.status = ProposalStatus.SUBMITTED
        return p


def approve_proposal(store: Store, proposal_id: str, *, approver: str):
    """独立复核人批准。批准即冻结受影响份额、签发处置单与销售通知。"""
    with store.lock:
        p = _require_proposal(store, proposal_id)
        if p.status != ProposalStatus.SUBMITTED:
            raise ConflictError("仅待复核状态的方案可批准", status=p.status.value)
        if approver == p.proposed_by:
            raise ForbiddenError("提出人与复核人必须为不同人员(独立复核)")
        if p.supersedes:
            old = _require_proposal(store, p.supersedes)
            if old.status == ProposalStatus.APPROVED:
                _release_scope(store, old)
                old.status = ProposalStatus.SUPERSEDED
        orders, notifs = _apply_scope(store, p)
        p.status = ProposalStatus.APPROVED
        p.approved_by = approver
        p.approved_at = utcnow()
        return p, orders, notifs


def reject_proposal(store: Store, proposal_id: str, *, reviewer: str, reason: str = "") -> ImpactProposal:
    with store.lock:
        p = _require_proposal(store, proposal_id)
        if p.status != ProposalStatus.SUBMITTED:
            raise ConflictError("仅待复核状态的方案可驳回", status=p.status.value)
        if reviewer == p.proposed_by:
            raise ForbiddenError("提出人与复核人必须为不同人员(独立复核)")
        p.status = ProposalStatus.REJECTED
        return p


def _apply_scope(store: Store, p: ImpactProposal):
    """按获批范围冻结在库份额; 已售部分生成客户通知任务。"""
    orders, notifs = [], []
    for bid, qty in p.scope.items():
        batch = store.batches[bid]
        freeze = min(qty, batch.available)
        if freeze > 0:
            batch.qty_frozen += freeze
            batch.version += 1
            order = DisposalOrder(
                id=new_id("disp"), proposal_id=p.id,
                batch_id=bid, approved_qty=freeze,
            )
            store.disposals[order.id] = order
            orders.append(order)
        sold = qty - freeze
        if sold > 0:
            notifs.extend(_allocate_notifications(store, p, bid, sold))
    return orders, notifs


def _allocate_notifications(store: Store, p: ImpactProposal, batch_id: str, qty: Decimal):
    """已售受影响数量按出库先后 FIFO 分摊到各客户发货单。"""
    tasks = []
    shipments = sorted(
        (s for s in store.shipments.values() if s.batch_id == batch_id),
        key=lambda s: (s.created_at, s.id),
    )
    remaining = qty
    for s in shipments:
        if remaining <= 0:
            break
        active = sum(
            (n.qty for n in store.notifications.values()
             if n.shipment_id == s.id and not n.cancelled),
            Decimal("0"),
        )
        free = s.qty - active
        if free <= 0:
            continue
        take = min(free, remaining)
        t = NotificationTask(
            id=new_id("notif"), shipment_id=s.id, batch_id=batch_id,
            customer=s.customer, qty=take, proposal_id=p.id,
        )
        store.notifications[t.id] = t
        tasks.append(t)
        remaining -= take
    return tasks


def _release_scope(store: Store, old: ImpactProposal) -> None:
    """释放旧方案未执行的冻结与未完成的通知(已执行的处置不可回退)。"""
    for o in store.disposals.values():
        if o.proposal_id != old.id or o.status not in (DisposalStatus.PENDING, DisposalStatus.PARTIAL):
            continue
        releasable = o.approved_qty - o.executed_qty
        batch = store.batches[o.batch_id]
        actual = min(releasable, batch.qty_frozen)
        batch.qty_frozen -= actual
        batch.version += 1
        o.status = DisposalStatus.SUPERSEDED
    for n in store.notifications.values():
        if n.proposal_id == old.id and not n.done and not n.cancelled:
            n.cancelled = True


# ---------- 处置与通知执行 ----------

def execute_disposal(
    store: Store, order_id: str, *,
    qty: Decimal, action: str, executor: str,
    expected_version: int | None = None,
) -> DisposalOrder:
    """处置人员执行获批数量内的处置。DESTROY/RETURN 核销实物, RELEASE 放行解冻。"""
    with store.lock:
        order = _require_order(store, order_id)
        if order.status not in (DisposalStatus.PENDING, DisposalStatus.PARTIAL):
            raise ConflictError("处置单当前状态不可执行", status=order.status.value)
        proposal = store.proposals[order.proposal_id]
        if proposal.status != ProposalStatus.APPROVED:
            raise ConflictError("处置单所属方案已被替代或失效")
        if action not in VALID_ACTIONS:
            raise ValidationError("未知处置方式", action=action)
        if qty <= 0:
            raise ValidationError("处置数量必须为正")
        remaining = order.approved_qty - order.executed_qty
        if qty > remaining:
            raise ValidationError(
                "处置数量超过获批剩余数量",
                remaining=str(remaining), requested=str(qty),
            )
        batch = store.batches[order.batch_id]
        if expected_version is not None and batch.version != expected_version:
            raise ConflictError(
                "库存版本已变化, 请刷新后按最新版本重试",
                batch_id=batch.id, expected=expected_version, actual=batch.version,
            )
        if qty > batch.qty_frozen:
            raise ValidationError("批次冻结份额不足", frozen=str(batch.qty_frozen))
        if action in ("DESTROY", "RETURN"):
            batch.qty_total -= qty
            batch.qty_frozen -= qty
        else:  # RELEASE: 放行, 份额回到可用
            batch.qty_frozen -= qty
        batch.version += 1
        if batch.qty_total == 0:
            batch.status = BatchStatus.CLOSED
        order.executed_qty += qty
        order.status = (
            DisposalStatus.DONE if order.executed_qty == order.approved_qty
            else DisposalStatus.PARTIAL
        )
        order.executions.append({
            "qty": str(qty), "action": action, "executor": executor,
            "at": utcnow().isoformat(),
        })
        return order


def complete_notification(store: Store, notification_id: str, *, notified_by: str) -> NotificationTask:
    with store.lock:
        n = _require_notification(store, notification_id)
        if n.cancelled:
            raise ConflictError("通知任务已随范围缩小取消")
        if n.done:
            return n  # 幂等
        n.done = True
        n.notified_by = notified_by
        n.notified_at = utcnow()
        return n


# ---------- 查询辅助 ----------

def _require_proposal(store: Store, proposal_id: str) -> ImpactProposal:
    p = store.proposals.get(proposal_id)
    if p is None:
        raise NotFoundError("影响范围方案不存在", proposal_id=proposal_id)
    return p


def _require_order(store: Store, order_id: str) -> DisposalOrder:
    o = store.disposals.get(order_id)
    if o is None:
        raise NotFoundError("处置单不存在", order_id=order_id)
    return o


def _require_notification(store: Store, notification_id: str) -> NotificationTask:
    n = store.notifications.get(notification_id)
    if n is None:
        raise NotFoundError("通知任务不存在", notification_id=notification_id)
    return n
