from __future__ import annotations

from typing import Iterable

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.stores.base import JsonlMockStore, plan_record


class RelationGraphMockStore(JsonlMockStore):
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "memory_relation_graph.jsonl")

    def write_plans(self, *, session_id: str, plans: Iterable[MemoryWritePlan]) -> int:
        return self.append_records(
            plan_record(session_id, plan, store="relation_graph", role=_role(plan))
            for plan in plans
            if plan.status == "planned" and _accepts(plan)
        )


def _accepts(plan: MemoryWritePlan) -> bool:
    return plan.canonical_store == "relation_graph" or "relation_graph" in plan.projections


def _role(plan: MemoryWritePlan) -> str:
    return "canonical" if plan.canonical_store == "relation_graph" else "projection"

