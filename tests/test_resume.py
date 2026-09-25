"""传播作业: 中断后从已确认的传播边界继续, 结果与一次性运行一致。"""

from decimal import Decimal

from conftest import build_chain


def test_resume_from_confirmed_boundary(state):
    build_chain(state)
    propagation = state.propagation

    job = propagation.start("R1")
    propagation.run(job.job_id, max_steps=2)  # 处理到 R1, R1a 后"中断"
    assert not job.done
    boundary = list(job.confirmed)
    assert boundary == ["R1", "R1a"]

    resumed = propagation.run(job.job_id)  # 从边界继续
    assert resumed.done
    assert resumed.confirmed[:2] == boundary  # 已确认部分不重算

    # 与一次性运行结果一致
    oneshot = propagation.start("R1")
    propagation.run(oneshot.job_id)
    assert propagation.affected_quantities(job.job_id) == propagation.affected_quantities(
        oneshot.job_id
    )


def test_affected_quantities_follow_conservation(state):
    build_chain(state)
    job = state.propagation.start("R1")
    state.propagation.run(job.job_id)
    affected = state.propagation.affected_quantities(job.job_id)
    # R1(100) -> R1a 40 / R1b 60; R1a 40 -> M1 38(损耗分摊2); M1 38 -> P1 36(损耗分摊2)
    assert affected == {
        "R1": Decimal("100"),
        "R1a": Decimal("40"),
        "R1b": Decimal("60"),
        "M1": Decimal("38"),
        "P1": Decimal("36"),
    }
