"""并发: 上游召回冻结与仓库出库按同一库存版本互斥, 不超卖、不误冻。"""

import threading
from decimal import Decimal

from app.domain.errors import InsufficientQuantity, VersionConflict
from app.domain.models import EventType

from conftest import cmd, receive


def _ship_with_retry(state, batch_id, qty, key, customer):
    while True:
        version = state.store.batches[batch_id].version
        try:
            state.genealogy.record(
                cmd(
                    EventType.SHIP,
                    key,
                    inputs=[(batch_id, qty)],
                    customer=customer,
                    expected_versions={batch_id: version},
                )
            )
            return "ok"
        except VersionConflict:
            continue
        except InsufficientQuantity:
            return "insufficient"


def _lock_with_retry(state, batch_id, qty, reason):
    while True:
        version = state.store.batches[batch_id].version
        try:
            state.inventory.lock(
                batch_id, Decimal(qty), reason=reason, expected_version=version
            )
            return "ok"
        except VersionConflict:
            continue
        except InsufficientQuantity:
            return "insufficient"


def _run_pairs(*fns):
    barrier = threading.Barrier(len(fns))

    def wrapped(fn):
        barrier.wait()
        return fn()

    threads = [threading.Thread(target=wrapped, args=(fn,)) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_ship_and_recall_lock_same_version(state):
    receive(state, "B1", "100")
    results: dict[str, str] = {}

    def ship():
        results["ship"] = _ship_with_retry(state, "B1", "30", "SHP-C1", "华东药房")

    def lock():
        results["lock"] = _lock_with_retry(state, "B1", "50", "RECALL")

    _run_pairs(ship, lock)
    assert results == {"ship": "ok", "lock": "ok"}
    batch = state.store.batches["B1"]
    assert batch.quantity == Decimal("70")
    assert batch.frozen == Decimal("50")
    assert batch.available == Decimal("20")  # 未受影响份额不冻结


def test_concurrent_ships_never_oversell(state):
    receive(state, "B2", "100")
    outcomes: list[str] = []
    guard = threading.Lock()

    def make_ship(i):
        def ship():
            result = _ship_with_retry(state, "B2", "30", f"SHP-O{i}", f"客户{i}")
            with guard:
                outcomes.append(result)

        return ship

    _run_pairs(*[make_ship(i) for i in range(5)])
    assert outcomes.count("ok") == 3
    assert outcomes.count("insufficient") == 2
    batch = state.store.batches["B2"]
    assert batch.quantity == Decimal("10")
    assert batch.quantity >= 0


def test_concurrent_locks_never_overfreeze(state):
    receive(state, "B3", "80")
    outcomes: list[str] = []
    guard = threading.Lock()

    def make_lock(i):
        def lock():
            result = _lock_with_retry(state, "B3", "50", f"RECALL-{i}")
            with guard:
                outcomes.append(result)

        return lock

    _run_pairs(*[make_lock(i) for i in range(3)])
    assert outcomes.count("ok") == 1
    assert outcomes.count("insufficient") == 2
    batch = state.store.batches["B3"]
    assert batch.frozen == Decimal("50")
    assert batch.available == Decimal("30")
