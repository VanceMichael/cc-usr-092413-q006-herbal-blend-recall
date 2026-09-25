"""领域错误类型。

API 层依据错误类型映射 HTTP 状态码; 领域层只抛出这些错误, 不感知传输层。
"""


class DomainError(Exception):
    """领域错误基类。"""


class NotFoundError(DomainError):
    """引用的聚合(批次/评估/任务等)不存在。"""


class ConservationViolation(DomainError):
    """投入、产出与损耗不守恒。"""


class IdempotencyConflict(DomainError):
    """同一设备流水号携带了不同内容, 已隔离。"""

    def __init__(self, quarantine_id: str):
        super().__init__(f"流水号内容冲突, 已隔离: {quarantine_id}")
        self.quarantine_id = quarantine_id


class InsufficientQuantity(DomainError):
    """可用数量不足(在库减去已冻结之后)。"""


class VersionConflict(DomainError):
    """库存版本与预期不一致, 存在并发修改, 调用方应重读后重试。"""


class StateError(DomainError):
    """状态机不允许的操作(如未获批即处置、超获批数量执行)。"""


class ApprovalError(DomainError):
    """复核约束被违反(如复核人与提报人相同)。"""
