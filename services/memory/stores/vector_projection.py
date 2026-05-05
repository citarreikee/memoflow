from __future__ import annotations

from typing import Iterable

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.stores.base import JsonlMockStore, plan_record


class VectorProjectionMockStore(JsonlMockStore):
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "memory_vector_projection.jsonl")

    def write_plans(self, *, session_id: str, plans: Iterable[MemoryWritePlan]) -> int:
        return self.append_records(
            _record(session_id, plan)
            for plan in plans
            if plan.status == "planned" and _accepts(plan)
        )


def _accepts(plan: MemoryWritePlan) -> bool:
    return plan.canonical_store == "vector_projection" or "vector_projection" in plan.projections


def _record(session_id: str, plan: MemoryWritePlan) -> dict:
    record = plan_record(
        session_id,
        plan,
        store="vector_projection",
        role="canonical" if plan.canonical_store == "vector_projection" else "projection",
    )
    record["source_of_truth"] = False
    record["canonical_store"] = plan.canonical_store
    return record

