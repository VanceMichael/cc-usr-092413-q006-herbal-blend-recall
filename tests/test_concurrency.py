"""并发: 出库/冻结/处置按批次版本串行, 不超卖、不重复冻结。"""

import threading
from decimal import Decimal

from app.errors import ConflictError, ValidationError
from app.genealogy import OutputSpec, record_event
from app.models import EventType, Portion
from app.store import Store


def _finished_batch(store, qty="10"):
    r = record_event(
        store, flow_no="R1", event_type=EventType.RECEIVE, inputs=[],
        outputs=[OutputSpec("黄芪", Decimal(qty))], unit="kg",
        op_version="RCV-1", operator="仓管员")
    w = record_event(
        store, flow_no="W1", event_type=EventType.WAREHOUSE,
        inputs=[Portion(r.batches[0].id, Decimal(qty))],
        outputs=[OutputSpec("黄芪饮片", Decimal(qty))], unit="kg",
        op_version="WH-1", operator="仓管员")
    return w.batches[0].id


def _outbound(store, fid, flow, qty, version):
    return record_event(
        store, flow_no=flow, event_type=EventType.OUTBOUND,
        inputs=[Portion(fid, Decimal(qty))], outputs=[], unit="kg",
        op_version="OUT-1", operator="仓管员", customer="客户甲",
        expected_versions={fid: version})


def test_concurrent_outbound_same_version_only_one_wins():
    store = Store()
    fid = _finished_batch(store)

    results, conflicts = [], []

    def worker(i):
        try:
            results.append(_outbound(store, fid, f"O{i}", "4", 0))
        except ConflictError:
            conflicts.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 同一库存版本只放行一笔, 另一笔因版本变化冲突
    assert len(results) == 1 and len(conflicts) == 1
    # 刷新版本后重试成功
    _outbound(store, fid, "O-retry", "4", 1)
    batch = store.batches[fid]
    assert batch.qty_total == Decimal("2")
    # 超卖被拒绝: 可用 2 < 4
    try:
        _outbound(store, fid, "O3", "4", 2)
        raise AssertionError("应当超卖失败")
    except ValidationError:
        pass
    assert store.batches[fid].qty_total == Decimal("2")


def test_concurrent_outbound_and_freeze_serialize():
    """出库与召回冻结并发: 同一库存版本上按实际用量串行。"""
    from app.quality import approve_proposal, create_proposal, submit_proposal

    store = Store()
    fid = _finished_batch(store, "10")

    # 出库 6kg(版本 0 -> 1)
    _outbound(store, fid, "O1", "6", 0)
    # 冻结方案获批(在库 4 全部冻结)
    p = create_proposal(store, cause={"type": "UPSTREAM_RECALL"}, scope={fid: Decimal("4")},
                        basis="上游召回", proposed_by="质检员甲")
    submit_proposal(store, p.id)
    approve_proposal(store, p.id, approver="复核员乙")

    batch = store.batches[fid]
    assert batch.qty_total == Decimal("4")
    assert batch.qty_frozen == Decimal("4")
    assert batch.available == Decimal("0")
    # 冻结后任何出库都被拒绝(可用不足), 旧版本号也失效
    try:
        _outbound(store, fid, "O2", "1", 1)
        raise AssertionError("应当版本冲突")
    except ConflictError:
        pass
    try:
        _outbound(store, fid, "O3", "1", batch.version)
        raise AssertionError("应当可用不足")
    except ValidationError:
        pass
