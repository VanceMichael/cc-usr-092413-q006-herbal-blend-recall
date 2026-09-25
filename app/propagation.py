"""影响传播作业: 从问题批次沿谱系向下游扩散, 支持中断后从已确认边界续跑。"""

from decimal import Decimal

from .errors import NotFoundError, ValidationError
from .models import JobStatus, PropagationJob, new_id
from .store import Store, require_batch


def create_job(store: Store, *, root_batch_ids: list[str]) -> PropagationJob:
    with store.lock:
        if not root_batch_ids:
            raise ValidationError("须指定至少一个根批次")
        for bid in root_batch_ids:
            require_batch(store, bid)
        job = PropagationJob(
            id=new_id("job"),
            root_batch_ids=list(root_batch_ids),
            frontier=list(root_batch_ids),
        )
        store.jobs[job.id] = job
        return job


def run_job(store: Store, job_id: str, *, max_steps: int) -> PropagationJob:
    """推进作业至多 max_steps 步。

    每确认一个批次即落账(计入 confirmed), 未跑完时状态为 INTERRUPTED,
    再次调用从已确认边界继续, 不重复处理。
    """
    with store.lock:
        job = _require_job(store, job_id)
        if job.status == JobStatus.COMPLETED:
            return job
        if max_steps < 1:
            raise ValidationError("max_steps 须 ≥ 1")
        job.status = JobStatus.RUNNING
        steps = 0
        while job.frontier and steps < max_steps:
            bid = job.frontier.pop(0)
            if bid in job.confirmed:
                continue
            _confirm(store, job, bid)
            steps += 1
            job.steps += 1
        job.status = JobStatus.COMPLETED if not job.frontier else JobStatus.INTERRUPTED
        return job


def get_job(store: Store, job_id: str) -> PropagationJob:
    return _require_job(store, job_id)


def _confirm(store: Store, job: PropagationJob, batch_id: str) -> None:
    batch = store.batches[batch_id]
    sold = sum(
        (s.qty for s in store.shipments.values() if s.batch_id == batch_id),
        Decimal("0"),
    )
    total = batch.qty_total + sold
    # 多路径到达同一批次时取最大值, 避免重复计数
    if total > job.affected.get(batch_id, Decimal("-1")):
        job.affected[batch_id] = total
    job.confirmed.append(batch_id)
    for edge in store.edges_from.get(batch_id, []):
        nxt = edge.to_batch
        if nxt not in job.confirmed and nxt not in job.frontier:
            job.frontier.append(nxt)


def _require_job(store: Store, job_id: str) -> PropagationJob:
    job = store.jobs.get(job_id)
    if job is None:
        raise NotFoundError("传播作业不存在", job_id=job_id)
    return job
