"""领域服务组装: 单进程内共享一个 Store。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.genealogy import GenealogyService
from app.domain.impact import ImpactService
from app.domain.inspection import InspectionService
from app.domain.inventory import InventoryService
from app.domain.propagation import PropagationService
from app.domain.store import Store
from app.domain.trace import TraceService


@dataclass
class DomainState:
    store: Store
    genealogy: GenealogyService
    inventory: InventoryService
    inspections: InspectionService
    impact: ImpactService
    propagation: PropagationService
    trace: TraceService


def build_state() -> DomainState:
    store = Store()
    inventory = InventoryService(store)
    return DomainState(
        store=store,
        genealogy=GenealogyService(store),
        inventory=inventory,
        inspections=InspectionService(store),
        impact=ImpactService(store, inventory),
        propagation=PropagationService(store),
        trace=TraceService(store),
    )
