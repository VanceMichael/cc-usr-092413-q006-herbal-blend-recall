"""库存冻结服务: 按实际用量锁定同一库存版本。

冻结只覆盖受影响份额, 未受影响份额保持可用; 出库与冻结通过
批次乐观锁版本互斥, 并发冲突由调用方重读后重试。
"""

from __future__ import annotations

from decimal import Decimal

from .errors import InsufficientQuantity, NotFoundError, StateError, VersionConflict
from .models import QuantityLock, refresh_status
from .store import Store


class InventoryService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def lock(
        self,
        batch_id: str,
        quantity: Decimal,
        reason: str,
        assessment_id: str = "",
        expected_version: int | None = None,
    ) -> QuantityLock:
        with self.store.lock:
            batch = self.store.batches.get(batch_id)
            if batch is None:
                raise NotFoundError(f"批次不存在: {batch_id}")
            if quantity <= 0:
                raise StateError("冻结数量必须为正")
            if expected_version is not None and batch.version != expected_version:
                raise VersionConflict(f"批次 {batch_id} 库存版本已变化, 请重读后重试")
            if quantity > batch.available:
                raise InsufficientQuantity(
                    f"批次 {batch_id} 可用 {batch.available} 不足 {quantity}"
                )
            lock_id = self.store.next_id("LCK")
            qlock = QuantityLock(
                lock_id=lock_id,
                batch_id=batch_id,
                quantity=quantity,
                reason=reason,
                assessment_id=assessment_id,
            )
            self.store.locks[lock_id] = qlock
            batch.frozen += quantity
            batch.version += 1
            refresh_status(batch)
            return qlock

    def release(self, lock_id: str) -> None:
        with self.store.lock:
            qlock = self.store.locks.get(lock_id)
            if qlock is None:
                raise NotFoundError(f"冻结不存在: {lock_id}")
            if not qlock.active:
                return
            batch = self.store.batches[qlock.batch_id]
            batch.frozen -= qlock.quantity
            batch.version += 1
            qlock.active = False
            refresh_status(batch)

    def release_scope(self, assessment_id: str) -> None:
        """释放某影响范围版本的全部冻结(范围被新版本取代时调用)。"""
        with self.store.lock:
            for qlock in list(self.store.locks.values()):
                if qlock.active and qlock.assessment_id == assessment_id:
                    self.release(qlock.lock_id)

    def consume_locked(self, batch_id: str, assessment_id: str, quantity: Decimal) -> None:
        """处置执行: 同时扣减冻结量与在库量, 冻结记录按序冲销。"""
        with self.store.lock:
            batch = self.store.batches.get(batch_id)
            if batch is None:
                raise NotFoundError(f"批次不存在: {batch_id}")
            locks = [
                l
                for l in self.store.locks.values()
                if l.active and l.batch_id == batch_id and l.assessment_id == assessment_id
            ]
            if sum((l.quantity for l in locks), Decimal("0")) < quantity:
                raise StateError(f"批次 {batch_id} 在该范围内的冻结数量不足 {quantity}")
            remaining = quantity
            for qlock in locks:
                take = min(qlock.quantity, remaining)
                qlock.quantity -= take
                remaining -= take
                if qlock.quantity == 0:
                    qlock.active = False
                if remaining == 0:
                    break
            batch.frozen -= quantity
            batch.quantity -= quantity
            batch.version += 1
            refresh_status(batch)
