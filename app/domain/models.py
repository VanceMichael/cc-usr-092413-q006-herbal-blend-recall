"""领域模型。

谱系(TransformationEvent)只增不改: 任何纠正都通过新的变换事件表达,
历史事件永不更新或删除。批次(Batch)保存由事件推导出的当前库存视图,
并携带乐观锁版本号, 供并发出库/冻结按同一库存版本互斥。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal


class BatchKind(str, enum.Enum):
    RAW = "RAW"                # 原料
    IN_PROCESS = "IN_PROCESS"  # 在制品
    FINISHED = "FINISHED"      # 成品


class BatchStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PARTIALLY_FROZEN = "PARTIALLY_FROZEN"  # 部分冻结, 未受影响份额仍可用
    FROZEN = "FROZEN"
    CONSUMED = "CONSUMED"      # 全部投入下游
    SHIPPED = "SHIPPED"        # 全部出库


class EventType(str, enum.Enum):
    RECEIVE = "RECEIVE"    # 收货
    SPLIT = "SPLIT"        # 拆分
    MERGE = "MERGE"        # 合并(混批)
    PROCESS = "PROCESS"    # 炮制
    STOCK_IN = "STOCK_IN"  # 成品入库(批次状态迁移, 无物料流动)
    SHIP = "SHIP"          # 出库/销售


@dataclass(frozen=True)
class Portion:
    """事件中对某批次的一次数量引用。"""

    batch_id: str
    quantity: Decimal


@dataclass(frozen=True)
class OutputSpec:
    """变换命令中一个产出批次的规格(产出批次必须是新批次号)。"""

    batch_id: str
    material_code: str
    kind: BatchKind
    quantity: Decimal


@dataclass
class Batch:
    batch_id: str
    material_code: str
    kind: BatchKind
    unit: str
    quantity: Decimal           # 当前在库数量(含已冻结)
    initial_quantity: Decimal   # 批次形成时的数量, 影响传播按此比例分摊
    version: int                # 乐观锁版本, 任何数量/冻结变化都递增
    created_by: str             # 形成本批次的事件 id
    status: BatchStatus = BatchStatus.ACTIVE
    frozen: Decimal = Decimal("0")

    @property
    def available(self) -> Decimal:
        return self.quantity - self.frozen


def refresh_status(batch: Batch) -> None:
    """根据数量与冻结量重算批次状态。"""
    if batch.quantity <= 0:
        batch.status = (
            BatchStatus.SHIPPED if batch.kind is BatchKind.FINISHED else BatchStatus.CONSUMED
        )
    elif batch.frozen <= 0:
        batch.status = BatchStatus.ACTIVE
    elif batch.frozen >= batch.quantity:
        batch.status = BatchStatus.FROZEN
    else:
        batch.status = BatchStatus.PARTIALLY_FROZEN


@dataclass
class TransformationEvent:
    """不可覆盖的谱系事件。每次变换记录数量、单位、损耗与操作版本。"""

    event_id: str
    seq: int                    # 全局单调序号, 传播与追溯按此排序
    event_type: EventType
    idempotency_key: str        # 设备/上游流水号
    source: str                 # 上报设备或系统
    operation_version: str      # 操作版本(如 SOP 版本)
    unit: str
    inputs: list[Portion]
    outputs: list[Portion]
    loss: Decimal
    actor: str
    payload_hash: str           # 命令内容的规范哈希, 用于幂等判定
    note: str = ""


@dataclass
class Shipment:
    """已销售去向。召回时不冻结, 转为通知责任。"""

    shipment_id: str
    event_id: str
    batch_id: str
    quantity: Decimal
    customer: str


class InspectionResult(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class InspectionStatus(str, enum.Enum):
    VALID = "VALID"
    SUSPECT = "SUSPECT"        # 校准版本被撤销, 结果存疑
    SUPERSEDED = "SUPERSEDED"  # 被替代检验取代


@dataclass
class Inspection:
    """检验记录: 绑定方法、样品范围与校准版本。内容不可改,
    修正只能通过替代检验生成新记录。"""

    inspection_id: str
    batch_id: str
    method: str
    sample_scope: str
    calibration_version: str
    result: InspectionResult
    inspector: str
    status: InspectionStatus = InspectionStatus.VALID
    superseded_by: str | None = None


class AssessmentStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"  # 被同根批次的新获批版本取代


@dataclass(frozen=True)
class AffectedItem:
    """影响范围中的一个批次及其受影响数量。"""

    batch_id: str
    quantity: Decimal


@dataclass
class ImpactAssessment:
    """影响范围评估, 按根批次版本化。质量人员提报, 独立复核人批准。"""

    assessment_id: str
    root_batch_id: str
    version: int
    items: list[AffectedItem]
    basis: str                  # 评估依据(检验记录/校准撤销/上游召回等)
    proposed_by: str
    status: AssessmentStatus = AssessmentStatus.PENDING_APPROVAL
    approved_by: str | None = None


@dataclass
class QuantityLock:
    """按实际用量对某库存版本加的冻结。只锁定受影响份额。"""

    lock_id: str
    batch_id: str
    quantity: Decimal
    reason: str
    assessment_id: str
    active: bool = True


@dataclass
class Disposition:
    """一次处置执行, 只增不改。数量不得超过获批范围。"""

    disposition_id: str
    assessment_id: str
    batch_id: str
    quantity: Decimal
    action: str                 # 销毁/返工/让步放行等
    executor: str


@dataclass
class NotificationTask:
    """已售受影响份额的通知责任。"""

    task_id: str
    shipment_id: str
    batch_id: str
    customer: str
    quantity: Decimal
    assessment_id: str
    done: bool = False
    cancelled: bool = False     # 范围版本被取代时取消未完成的旧任务


@dataclass
class QuarantinedReport:
    """流水号相同但内容不同的上报, 隔离待人工处理, 不进入谱系。"""

    quarantine_id: str
    source: str
    idempotency_key: str
    payload_hash: str
    conflicting_event_id: str
    payload: dict


@dataclass
class PropagationJob:
    """影响传播作业。frontier/confirmed 构成已确认的传播边界,
    中断后用剩余 frontier 即可从边界继续。"""

    job_id: str
    root_batch_id: str
    frontier: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    remaining: dict[str, Decimal] = field(default_factory=dict)  # 尚未向下游分摊的受影响数量
    affected: dict[str, Decimal] = field(default_factory=dict)   # 每个批次累计受影响数量
    done: bool = False
