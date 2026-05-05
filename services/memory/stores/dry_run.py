from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.stores.episode_log import EpisodeLogProjectionStore
from services.memory.stores.file_projection import FileProjectionMockStore
from services.memory.stores.relation_graph import RelationGraphMockStore
from services.memory.stores.semantic_kv import SemanticKvMockStore
from services.memory.stores.vector_projection import VectorProjectionMockStore
from services.memory.stores.write_plan_log import WritePlanLog


@dataclass(frozen=True)
class DryRunWriteResult:
    total_written: int
    by_store: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return {"total_written": self.total_written, "by_store": self.by_store}


def write_dry_run_outputs(*, base_dir: str, session_id: str, plans: List[MemoryWritePlan]) -> DryRunWriteResult:
    by_store = {
        "write_plan_log": WritePlanLog(base_dir).append(session_id=session_id, plans=plans),
        "episode_log": EpisodeLogProjectionStore(base_dir).write_plans(session_id=session_id, plans=plans),
        "semantic_kv": SemanticKvMockStore(base_dir).write_plans(session_id=session_id, plans=plans),
        "vector_projection": VectorProjectionMockStore(base_dir).write_plans(session_id=session_id, plans=plans),
        "relation_graph": RelationGraphMockStore(base_dir).write_plans(session_id=session_id, plans=plans),
        "file_memory": FileProjectionMockStore(base_dir).write_plans(session_id=session_id, plans=plans),
    }
    return DryRunWriteResult(total_written=sum(by_store.values()), by_store=by_store)

