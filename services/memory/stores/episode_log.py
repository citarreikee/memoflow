from __future__ import annotations

from typing import Iterable

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.stores.base import JsonlMockStore, plan_record


class EpisodeLogProjectionStore(JsonlMockStore):
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "memory_episode_log_projection.jsonl")

    def write_plans(self, *, session_id: str, plans: Iterable[MemoryWritePlan]) -> int:
        return self.append_records(
            plan_record(session_id, plan, store="episode_log", role=_role(plan))
            for plan in plans
            if _accepts(plan)
        )


def _accepts(plan: MemoryWritePlan) -> bool:
    return plan.status in {"planned", "needs_review", "blocked"} and (
        plan.canonical_store == "episode_log" or "episode_log" in plan.projections
    )


def _role(plan: MemoryWritePlan) -> str:
    return "canonical" if plan.canonical_store == "episode_log" else "projection"

