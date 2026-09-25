"""影响范围评估与处置。

质量人员提报影响范围, 独立复核人批准(复核人必须不同于提报人)。
批准后按实际用量锁定受影响在库份额: 未受影响份额不冻结,
已售份额转为通知责任。替代检验产生的新评估版本获批后取代旧版本,
范围可缩小或扩大, 但不得小于已执行的处置数量。
"""

from __future__ import annotations

from decimal import Decimal

from .errors import ApprovalError, NotFoundError, StateError
from .inventory import InventoryService
from .models import (
    AffectedItem,
    AssessmentStatus,
    Disposition,
    ImpactAssessment,
    NotificationTask,
)
from .store import Store


class ImpactService:
    def __init__(self, store: Store, inventory: InventoryService) -> None:
        self.store = store
        self.inventory = inventory

    def propose(
        self,
        root_batch_id: str,
        items: list[AffectedItem],
        basis: str,
        proposed_by: str,
    ) -> ImpactAssessment:
        """提报影响范围。同一根批次的评估按版本递增。"""
        with self.store.lock:
            if root_batch_id not in self.store.batches:
                raise NotFoundError(f"根批次不存在: {root_batch_id}")
            if not items:
                raise StateError("影响范围不能为空")
            seen: set[str] = set()
            for item in items:
                if item.batch_id not in self.store.batches:
                    raise NotFoundError(f"批次不存在: {item.batch_id}")
                if item.quantity <= 0:
                    raise StateError("受影响数量必须为正")
                if item.batch_id in seen:
                    raise StateError(f"批次重复: {item.batch_id}")
                seen.add(item.batch_id)
            version = 1 + max(
                (a.version for a in self.store.assessments.values()
                 if a.root_batch_id == root_batch_id),
                default=0,
            )
            assessment_id = self.store.next_id("ASM")
            assessment = ImpactAssessment(
                assessment_id=assessment_id,
                root_batch_id=root_batch_id,
                version=version,
                items=list(items),
                basis=basis,
                proposed_by=proposed_by,
            )
            self.store.assessments[assessment_id] = assessment
            return assessment

    def approve(self, assessment_id: str, approved_by: str) -> ImpactAssessment:
        """独立复核人批准: 取代旧获批版本, 重算冻结与通知责任。"""
        with self.store.lock:
            assessment = self._get(assessment_id)
            if assessment.status is not AssessmentStatus.PENDING_APPROVAL:
                raise StateError("仅待批准的评估可以批准")
            if approved_by == assessment.proposed_by:
                raise ApprovalError("复核人必须独立于提报人")

            previous = self._current_approved(assessment.root_batch_id)
            if previous is not None:
                executed = self._executed_by_batch(previous.assessment_id)
                new_qty = {i.batch_id: i.quantity for i in assessment.items}
                for batch_id, qty in executed.items():
                    if qty > 0 and new_qty.get(batch_id, Decimal("0")) < qty:
                        raise StateError(
                            f"批次 {batch_id} 已执行处置 {qty}, 新范围不得小于该数量"
                        )

            if previous is not None:
                previous.status = AssessmentStatus.SUPERSEDED
                self.inventory.release_scope(previous.assessment_id)
                for task in self.store.notifications.values():
                    if task.assessment_id == previous.assessment_id and not task.done:
                        task.cancelled = True

            assessment.status = AssessmentStatus.APPROVED
            assessment.approved_by = approved_by
            self._apply_scope(assessment)
            return assessment

    def reject(self, assessment_id: str, rejected_by: str) -> ImpactAssessment:
        with self.store.lock:
            assessment = self._get(assessment_id)
            if assessment.status is not AssessmentStatus.PENDING_APPROVAL:
                raise StateError("仅待批准的评估可以驳回")
            if rejected_by == assessment.proposed_by:
                raise ApprovalError("复核人必须独立于提报人")
            assessment.status = AssessmentStatus.REJECTED
            return assessment

    def execute(
        self,
        assessment_id: str,
        batch_id: str,
        quantity: Decimal,
        action: str,
        executor: str,
    ) -> Disposition:
        """执行处置: 只允许在已获批范围内, 且消耗对应冻结份额。"""
        with self.store.lock:
            assessment = self._get(assessment_id)
            if assessment.status is not AssessmentStatus.APPROVED:
                raise StateError("仅可执行已获批的影响范围")
            item = next((i for i in assessment.items if i.batch_id == batch_id), None)
            if item is None:
                raise StateError(f"批次 {batch_id} 不在获批范围内")
            if quantity <= 0:
                raise StateError("处置数量必须为正")
            executed = self._executed_by_batch(assessment_id).get(batch_id, Decimal("0"))
            if executed + quantity > item.quantity:
                raise StateError(
                    f"超过获批数量: 已执行 {executed}, 本次 {quantity}, 获批 {item.quantity}"
                )
            self.inventory.consume_locked(batch_id, assessment_id, quantity)
            disposition = Disposition(
                disposition_id=self.store.next_id("DSP"),
                assessment_id=assessment_id,
                batch_id=batch_id,
                quantity=quantity,
                action=action,
                executor=executor,
            )
            self.store.dispositions.append(disposition)
            return disposition

    def complete_notification(self, task_id: str) -> NotificationTask:
        with self.store.lock:
            task = self.store.notifications.get(task_id)
            if task is None:
                raise NotFoundError(f"通知任务不存在: {task_id}")
            if task.cancelled:
                raise StateError("该通知任务已随旧范围版本取消")
            task.done = True
            return task

    def _apply_scope(self, assessment: ImpactAssessment) -> None:
        """按实际用量锁定受影响份额。

        在库部分按比例冻结: 冻结量 = 受影响量 × 在库/初始量;
        其余视为已售受影响份额, 按出库先后生成通知责任, 不冻结。
        """
        for item in assessment.items:
            batch = self.store.batches[item.batch_id]
            if batch.initial_quantity > 0:
                on_hand = item.quantity * batch.quantity / batch.initial_quantity
            else:
                on_hand = Decimal("0")
            on_hand = min(on_hand, item.quantity)
            if on_hand > 0:
                self.inventory.lock(
                    item.batch_id,
                    on_hand,
                    reason="RECALL",
                    assessment_id=assessment.assessment_id,
                )
            sold = item.quantity - on_hand
            if sold > 0:
                for shipment in self.store.shipments.values():
                    if shipment.batch_id != item.batch_id:
                        continue
                    take = min(shipment.quantity, sold)
                    if take <= 0:
                        continue
                    task_id = self.store.next_id("NTF")
                    self.store.notifications[task_id] = NotificationTask(
                        task_id=task_id,
                        shipment_id=shipment.shipment_id,
                        batch_id=item.batch_id,
                        customer=shipment.customer,
                        quantity=take,
                        assessment_id=assessment.assessment_id,
                    )
                    sold -= take
                    if sold == 0:
                        break

    def _get(self, assessment_id: str) -> ImpactAssessment:
        assessment = self.store.assessments.get(assessment_id)
        if assessment is None:
            raise NotFoundError(f"评估不存在: {assessment_id}")
        return assessment

    def _current_approved(self, root_batch_id: str) -> ImpactAssessment | None:
        for a in self.store.assessments.values():
            if a.root_batch_id == root_batch_id and a.status is AssessmentStatus.APPROVED:
                return a
        return None

    def _executed_by_batch(self, assessment_id: str) -> dict[str, Decimal]:
        executed: dict[str, Decimal] = {}
        for d in self.store.dispositions:
            if d.assessment_id == assessment_id:
                executed[d.batch_id] = executed.get(d.batch_id, Decimal("0")) + d.quantity
        return executed
