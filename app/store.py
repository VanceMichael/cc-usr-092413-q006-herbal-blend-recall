"""进程内领域存储。

所有变更在同一把可重入锁下完成, 配合批次 version 乐观锁,
保证上游召回、校准撤销、仓库出库并发时按实际用量锁定同一库存版本。
持久化(数据库)后续可在本接口后替换, 领域服务不感知。
"""

import threading
from collections import defaultdict

from .errors import NotFoundError
from .models import (
    Batch,
    BatchEvent,
    CalibrationRevocation,
    DisposalOrder,
    Edge,
    ImpactProposal,
    Inspection,
    IsolatedReport,
    NotificationTask,
    OutboundShipment,
    PropagationJob,
)

GENESIS_HASH = "0" * 64


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.batches: dict[str, Batch] = {}
        self.events: list[BatchEvent] = []
        self.events_by_flow: dict[str, BatchEvent] = {}
        self.edges_from: dict[str, list[Edge]] = defaultdict(list)
        self.edges_to: dict[str, list[Edge]] = defaultdict(list)
        self.inspections: dict[str, Inspection] = {}
        self.revocations: dict[str, CalibrationRevocation] = {}
        self.proposals: dict[str, ImpactProposal] = {}
        self.disposals: dict[str, DisposalOrder] = {}
        self.notifications: dict[str, NotificationTask] = {}
        self.shipments: dict[str, OutboundShipment] = {}
        self.jobs: dict[str, PropagationJob] = {}
        self.isolated: dict[str, IsolatedReport] = {}
        self.last_hash = GENESIS_HASH


def require_batch(store: Store, batch_id: str) -> Batch:
    batch = store.batches.get(batch_id)
    if batch is None:
        raise NotFoundError("批次不存在", batch_id=batch_id)
    return batch
