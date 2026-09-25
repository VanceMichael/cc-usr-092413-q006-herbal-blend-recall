"""领域实体。所有数量使用 Decimal, 保证守恒校验精确。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BatchKind(str, Enum):
    RAW = "RAW"                # 原料
    INTERMEDIATE = "INTERMEDIATE"  # 中间品(拆分/合并/炮制产出)
    FINISHED = "FINISHED"      # 成品


class BatchStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"  # 已全部投入下游
    CLOSED = "CLOSED"      # 已全部处置完毕


class EventType(str, Enum):
    RECEIVE = "RECEIVE"        # 收货
    SPLIT = "SPLIT"            # 拆分
    MERGE = "MERGE"            # 合并(混批)
    PROCESS = "PROCESS"        # 炮制
    WAREHOUSE = "WAREHOUSE"    # 成品入库
    OUTBOUND = "OUTBOUND"      # 出库(销售去向)


@dataclass
class Batch:
    id: str
    material: str
    kind: BatchKind
    unit: str
    qty_total: Decimal
    qty_frozen: Decimal = Decimal("0")  # 影响范围获批后冻结的份额
    status: BatchStatus = BatchStatus.ACTIVE
    version: int = 0  # 乐观锁版本, 任何数量/冻结变化都 +1
    created_by_event: str = ""
    created_at: datetime = field(default_factory=utcnow)

    @property
    def available(self) -> Decimal:
        """可动用份额 = 在库总量 - 冻结份额。冻结份额不可投入下游或出库。"""
        return self.qty_total - self.qty_frozen


@dataclass(frozen=True)
class Portion:
    batch_id: str
    qty: Decimal


@dataclass
class BatchEvent:
    """批次变换事件。台账只追加不覆盖, 通过 prev_hash/hash 形成防篡改链。"""
    id: str
    flow_no: str           # 设备流水号(幂等键)
    type: EventType
    inputs: list[Portion]
    outputs: list[Portion]
    loss_qty: Decimal
    unit: str
    op_version: str        # 操作版本(工艺/规程版本)
    operator: str
    payload_hash: str
    prev_hash: str
    hash: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Edge:
    """谱系边: from_batch 的 qty 数量经 event 流入 to_batch。"""
    from_batch: str
    to_batch: str
    qty: Decimal
    event_id: str


@dataclass
class Inspection:
    """检验记录。同一(批次, 方法)的检验按 version 递增, 旧版本保留不覆盖。"""
    id: str
    batch_id: str
    method: str              # 检验方法
    sample_scope: str        # 样品范围
    calibration_version: str  # 校准版本
    result: str              # PASS / FAIL
    version: int
    supersedes: str | None   # 被替代的上一版检验
    inspector: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class CalibrationRevocation:
    calibration_version: str
    reason: str
    revoked_by: str
    created_at: datetime = field(default_factory=utcnow)


class ProposalStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"  # 被新版本方案替代


@dataclass
class ImpactProposal:
    """影响范围方案。scope: batch_id -> 受影响数量(在库+已售)。"""
    id: str
    version: int
    cause: dict
    scope: dict[str, Decimal]
    basis: str               # 判定依据(检验记录/校准撤销说明)
    proposed_by: str
    status: ProposalStatus = ProposalStatus.DRAFT
    supersedes: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)


class DisposalStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    DONE = "DONE"
    SUPERSEDED = "SUPERSEDED"


@dataclass
class DisposalOrder:
    """处置单。处置人员累计执行数量不得超过 approved_qty。"""
    id: str
    proposal_id: str
    batch_id: str
    approved_qty: Decimal
    executed_qty: Decimal = Decimal("0")
    status: DisposalStatus = DisposalStatus.PENDING
    executions: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class NotificationTask:
    """已销售去向的通知责任。"""
    id: str
    shipment_id: str
    batch_id: str
    customer: str
    qty: Decimal
    proposal_id: str
    done: bool = False
    cancelled: bool = False  # 范围缩小后被取消
    notified_by: str | None = None
    notified_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class OutboundShipment:
    id: str
    batch_id: str
    qty: Decimal
    customer: str
    event_id: str
    created_at: datetime = field(default_factory=utcnow)


class JobStatus(str, Enum):
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"  # 中断, 可从已确认边界继续
    COMPLETED = "COMPLETED"


@dataclass
class PropagationJob:
    """影响传播作业。confirmed 为已确认边界, 中断后续跑不重复处理。"""
    id: str
    root_batch_ids: list[str]
    status: JobStatus = JobStatus.RUNNING
    confirmed: list[str] = field(default_factory=list)
    frontier: list[str] = field(default_factory=list)
    affected: dict[str, Decimal] = field(default_factory=dict)
    steps: int = 0
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class IsolatedReport:
    """流水号相同但内容不一致的上报, 隔离待人工核对, 不进入谱系。"""
    id: str
    flow_no: str
    reason: str
    payload: dict
    payload_hash: str
    existing_event_id: str
    created_at: datetime = field(default_factory=utcnow)
