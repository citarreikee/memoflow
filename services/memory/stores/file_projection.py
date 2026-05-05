from __future__ import annotations

from typing import Iterable

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.stores.base import JsonlMockStore, plan_record


class FileProjectionMockStore(JsonlMockStore):
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "memory_file_suggestions.jsonl")

    def write_plans(self, *, session_id: str, plans: Iterable[MemoryWritePlan]) -> int:
        return self.append_records(
            _record(session_id, plan)
            for plan in plans
            if plan.status in {"planned", "needs_review"} and plan.canonical_store == "file_memory"
        )


def _record(session_id: str, plan: MemoryWritePlan) -> dict:
    record = plan_record(session_id, plan, store="file_memory", role="suggestion")
    record["auto_apply"] = False
    record["suggested_patch"] = _suggested_patch(plan)
    return record


def _suggested_patch(plan: MemoryWritePlan) -> str:
    return f"- {plan.text}"

