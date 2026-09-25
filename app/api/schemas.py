"""API 请求模型。数量以字符串/数值传入, 统一解析为 Decimal。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.genealogy import TransformationCommand
from app.domain.models import BatchKind, EventType, InspectionResult, OutputSpec, Portion


class PortionIn(BaseModel):
    batch_id: str
    quantity: Decimal


class OutputIn(BaseModel):
    batch_id: str
    material_code: str
    kind: BatchKind
    quantity: Decimal


class TransformationIn(BaseModel):
    event_type: EventType
    idempotency_key: str = Field(min_length=1)
    source: str = Field(min_length=1)
    operation_version: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    inputs: list[PortionIn] = []
    outputs: list[OutputIn] = []
    loss: Decimal = Decimal("0")
    customer: str | None = None
    expected_versions: dict[str, int] | None = None
    note: str = ""

    def to_command(self) -> TransformationCommand:
        return TransformationCommand(
            event_type=self.event_type,
            idempotency_key=self.idempotency_key,
            source=self.source,
            operation_version=self.operation_version,
            unit=self.unit,
            actor=self.actor,
            inputs=tuple(Portion(p.batch_id, p.quantity) for p in self.inputs),
            outputs=tuple(
                OutputSpec(o.batch_id, o.material_code, o.kind, o.quantity)
                for o in self.outputs
            ),
            loss=self.loss,
            customer=self.customer,
            expected_versions=self.expected_versions,
            note=self.note,
        )


class InspectionIn(BaseModel):
    batch_id: str
    method: str = Field(min_length=1)
    sample_scope: str = Field(min_length=1)
    calibration_version: str = Field(min_length=1)
    result: InspectionResult
    inspector: str = Field(min_length=1)
    supersedes: str | None = None


class CalibrationRevokeIn(BaseModel):
    calibration_version: str = Field(min_length=1)


class AssessmentIn(BaseModel):
    root_batch_id: str
    items: list[PortionIn]
    basis: str = Field(min_length=1)
    proposed_by: str = Field(min_length=1)


class ReviewIn(BaseModel):
    reviewer: str = Field(min_length=1)


class DispositionIn(BaseModel):
    batch_id: str
    quantity: Decimal
    action: str = Field(min_length=1)
    executor: str = Field(min_length=1)


class PropagationStartIn(BaseModel):
    root_batch_id: str


class PropagationRunIn(BaseModel):
    max_steps: int | None = None


class LockIn(BaseModel):
    batch_id: str
    quantity: Decimal
    reason: str = Field(min_length=1)
    expected_version: int | None = None
