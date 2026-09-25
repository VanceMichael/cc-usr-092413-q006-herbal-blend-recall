"""检验: 绑定方法、样品范围与校准版本; 替代检验生成新记录; 校准撤销可回溯。"""

import pytest

from app.domain.errors import StateError
from app.domain.models import InspectionResult, InspectionStatus

from conftest import build_chain, receive


def test_inspection_binds_method_scope_and_calibration(state):
    receive(state, "R1", "50")
    inspection = state.inspections.record(
        batch_id="R1",
        method="HPLC-2020",
        sample_scope="R1 三点抽样",
        calibration_version="CAL-1.2",
        result=InspectionResult.PASS,
        inspector="qc01",
    )
    assert inspection.method == "HPLC-2020"
    assert inspection.sample_scope == "R1 三点抽样"
    assert inspection.calibration_version == "CAL-1.2"
    assert inspection.status is InspectionStatus.VALID


def test_substitute_inspection_supersedes_previous(state):
    receive(state, "R1", "50")
    original = state.inspections.record(
        batch_id="R1", method="HPLC-2020", sample_scope="抽样A",
        calibration_version="CAL-1.2", result=InspectionResult.FAIL, inspector="qc01",
    )
    substitute = state.inspections.record(
        batch_id="R1", method="HPLC-2020", sample_scope="抽样A",
        calibration_version="CAL-2.0", result=InspectionResult.PASS, inspector="qc02",
        supersedes=original.inspection_id,
    )
    assert original.status is InspectionStatus.SUPERSEDED
    assert original.superseded_by == substitute.inspection_id
    assert substitute.status is InspectionStatus.VALID
    # 原记录内容仍保留, 不被覆盖
    assert original.result is InspectionResult.FAIL
    assert original.calibration_version == "CAL-1.2"


def test_supersede_requires_same_batch(state):
    receive(state, "R1", "50")
    receive(state, "R2", "50")
    first = state.inspections.record(
        batch_id="R1", method="M", sample_scope="S",
        calibration_version="CAL-1", result=InspectionResult.PASS, inspector="qc01",
    )
    with pytest.raises(StateError):
        state.inspections.record(
            batch_id="R2", method="M", sample_scope="S",
            calibration_version="CAL-1", result=InspectionResult.PASS, inspector="qc01",
            supersedes=first.inspection_id,
        )


def test_revoke_calibration_marks_inspections_suspect(state):
    build_chain(state)
    affected = state.inspections.revoke_calibration("CAL-1.2")
    assert [i.batch_id for i in affected] == ["P1"]
    inspection = next(iter(state.store.inspections.values()))
    assert inspection.status is InspectionStatus.SUSPECT
    # 已替代的检验不再重复受影响
    again = state.inspections.revoke_calibration("CAL-1.2")
    assert again == []
