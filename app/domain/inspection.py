"""检验服务: 检验结果绑定方法、样品范围与校准版本。

检验记录内容不可改; 修正通过替代检验生成新记录并取代旧记录;
校准版本被撤销时, 依赖它的有效检验全部转为存疑, 供影响评估引用。
"""

from __future__ import annotations

from .errors import NotFoundError, StateError
from .models import Inspection, InspectionResult, InspectionStatus
from .store import Store


class InspectionService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def record(
        self,
        batch_id: str,
        method: str,
        sample_scope: str,
        calibration_version: str,
        result: InspectionResult,
        inspector: str,
        supersedes: str | None = None,
    ) -> Inspection:
        with self.store.lock:
            if batch_id not in self.store.batches:
                raise NotFoundError(f"批次不存在: {batch_id}")
            if not method or not sample_scope or not calibration_version:
                raise StateError("检验必须绑定方法、样品范围与校准版本")
            previous = None
            if supersedes is not None:
                previous = self.store.inspections.get(supersedes)
                if previous is None:
                    raise NotFoundError(f"被替代的检验不存在: {supersedes}")
                if previous.batch_id != batch_id:
                    raise StateError("替代检验必须针对同一批次")
                if previous.status is InspectionStatus.SUPERSEDED:
                    raise StateError("该检验已被替代")
            inspection_id = self.store.next_id("INS")
            inspection = Inspection(
                inspection_id=inspection_id,
                batch_id=batch_id,
                method=method,
                sample_scope=sample_scope,
                calibration_version=calibration_version,
                result=result,
                inspector=inspector,
            )
            self.store.inspections[inspection_id] = inspection
            if previous is not None:
                previous.status = InspectionStatus.SUPERSEDED
                previous.superseded_by = inspection_id
            return inspection

    def revoke_calibration(self, calibration_version: str) -> list[Inspection]:
        """撤销校准版本: 依赖它的有效检验全部转为存疑, 返回受影响检验。"""
        with self.store.lock:
            affected = [
                i
                for i in self.store.inspections.values()
                if i.calibration_version == calibration_version
                and i.status is InspectionStatus.VALID
            ]
            for inspection in affected:
                inspection.status = InspectionStatus.SUSPECT
            return affected
