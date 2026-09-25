"""影响传播作业: 沿谱系向下游分摊受影响数量。

分摊规则: 某事件消耗受影响批次 q, 则产出 k 分摊 q × 产出k / (总产出 + 损耗),
损耗份额随之消失; 出库只消耗不再下传。作业状态(frontier/confirmed/remaining)
持久保存在 Store 中, 中断后再次运行即从已确认的传播边界继续。
"""

from __future__ import annotations

from decimal import Decimal

from .errors import NotFoundError
from .models import EventType, PropagationJob
from .store import Store


class PropagationService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def start(self, root_batch_id: str) -> PropagationJob:
        with self.store.lock:
            batch = self.store.batches.get(root_batch_id)
            if batch is None:
                raise NotFoundError(f"批次不存在: {root_batch_id}")
            job_id = self.store.next_id("JOB")
            job = PropagationJob(
                job_id=job_id,
                root_batch_id=root_batch_id,
                frontier=[root_batch_id],
                remaining={root_batch_id: batch.initial_quantity},
                affected={root_batch_id: batch.initial_quantity},
            )
            self.store.jobs[job_id] = job
            return job

    def run(self, job_id: str, max_steps: int | None = None) -> PropagationJob:
        """推进传播; max_steps 用于限制作业步长, 中断后可再次调用继续。"""
        with self.store.lock:
            job = self._get(job_id)
            steps = 0
            while job.frontier and (max_steps is None or steps < max_steps):
                batch_id = job.frontier.pop(0)
                self._expand(job, batch_id)
                job.confirmed.append(batch_id)
                steps += 1
            job.done = not job.frontier
            return job

    def affected_quantities(self, job_id: str) -> dict[str, Decimal]:
        with self.store.lock:
            return dict(self._get(job_id).affected)

    def _expand(self, job: PropagationJob, batch_id: str) -> None:
        remaining = job.remaining.pop(batch_id, Decimal("0"))
        for event in self.store.events:
            if event.event_type is EventType.STOCK_IN:
                continue  # 状态迁移无物料流动, 受影响量留在原批次
            portion = next(
                (p for p in event.inputs if p.batch_id == batch_id), None
            )
            if portion is None or remaining <= 0:
                continue
            take = min(portion.quantity, remaining)
            remaining -= take
            denominator = sum((o.quantity for o in event.outputs), Decimal("0")) + event.loss
            if denominator <= 0:
                continue
            for output in event.outputs:
                share = take * output.quantity / denominator
                if share <= 0:
                    continue
                job.remaining[output.batch_id] = (
                    job.remaining.get(output.batch_id, Decimal("0")) + share
                )
                job.affected[output.batch_id] = (
                    job.affected.get(output.batch_id, Decimal("0")) + share
                )
                if (
                    output.batch_id not in job.confirmed
                    and output.batch_id not in job.frontier
                ):
                    job.frontier.append(output.batch_id)

    def _get(self, job_id: str) -> PropagationJob:
        job = self.store.jobs.get(job_id)
        if job is None:
            raise NotFoundError(f"传播作业不存在: {job_id}")
        return job
