"""进程内存储: 全部聚合与事件日志。

一把可重入锁串行化所有写操作; 乐观锁版本检查在锁内完成,
因此"读取版本 -> 校验 -> 修改"是原子的。后续接入真实持久化时,
以本模块的聚合边界为 Repository 边界。
"""

from __future__ import annotations

import threading

from .models import (
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


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._seq = 0
        self.batches: dict[str, Batch] = {}
        self.events: list[TransformationEvent] = []         # 只增不改的谱系日志
        self.idempotency: dict[tuple[str, str], str] = {}   # (来源, 流水号) -> 事件id
        self.quarantine: dict[str, QuarantinedReport] = {}  # 内容冲突的隔离上报
        self.shipments: dict[str, Shipment] = {}
        self.inspections: dict[str, Inspection] = {}
        self.assessments: dict[str, ImpactAssessment] = {}
        self.locks: dict[str, QuantityLock] = {}
        self.dispositions: list[Disposition] = []           # 只增不改
        self.notifications: dict[str, NotificationTask] = {}
        self.jobs: dict[str, PropagationJob] = {}

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def next_id(self, prefix: str) -> str:
        return f"{prefix}-{self.next_seq():06d}"
