from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from services.memory.formation.schemas import MemoryCandidateLite


EPISODE_LOG = "episode_log"
SEMANTIC_KV = "semantic_kv"
VECTOR_PROJECTION = "vector_projection"
RELATION_GRAPH = "relation_graph"
FILE_MEMORY = "file_memory"


GRAPH_RELATION_MARKERS = (
    "depends_on",
    "depends on",
    "依赖",
    "supersedes",
    "取代",
    "替代",
    "contradicts",
    "冲突",
    "矛盾",
    "derived_from",
    "derived from",
    "来自",
    "caused_by",
    "caused by",
    "导致",
    "part_of",
    "part of",
    "组成",
    "blocks",
    "阻塞",
    "valid_from",
    "valid_until",
)


@dataclass(frozen=True)
class StorageShape:
    canonical_store: Optional[str]
    projections: List[str]
    blocked_reasons: List[str]


DEFAULT_SHAPES: Dict[str, StorageShape] = {
    "preference": StorageShape(SEMANTIC_KV, [EPISODE_LOG, VECTOR_PROJECTION], []),
    "profile_fact": StorageShape(SEMANTIC_KV, [EPISODE_LOG], []),
    "project_rule": StorageShape(FILE_MEMORY, [EPISODE_LOG, VECTOR_PROJECTION], []),
    "procedure": StorageShape(FILE_MEMORY, [EPISODE_LOG, VECTOR_PROJECTION], []),
    "decision": StorageShape(SEMANTIC_KV, [EPISODE_LOG, VECTOR_PROJECTION], []),
    "task_state": StorageShape(SEMANTIC_KV, [EPISODE_LOG], []),
    "entity_relation": StorageShape(RELATION_GRAPH, [EPISODE_LOG], []),
    "episodic_event": StorageShape(EPISODE_LOG, [], []),
    "embedding_hint": StorageShape(VECTOR_PROJECTION, [EPISODE_LOG], []),
    "non_memory": StorageShape(None, [], ["non_memory"]),
}


def has_explicit_graph_relation(candidate: MemoryCandidateLite) -> bool:
    text = f"{candidate.text} {candidate.reason}".lower()
    return any(marker in text for marker in GRAPH_RELATION_MARKERS)


def plan_storage_shape(candidate: MemoryCandidateLite) -> StorageShape:
    base = DEFAULT_SHAPES.get(candidate.type, DEFAULT_SHAPES["non_memory"])
    projections = list(base.projections)
    blocked_reasons = list(base.blocked_reasons)
    canonical_store = base.canonical_store

    if candidate.type == "entity_relation" and not has_explicit_graph_relation(candidate):
        canonical_store = None
        projections = [EPISODE_LOG]
        blocked_reasons.append("graph_relation_missing")

    if candidate.type in {"decision", "task_state"} and has_explicit_graph_relation(candidate):
        if RELATION_GRAPH not in projections:
            projections.append(RELATION_GRAPH)

    if canonical_store == FILE_MEMORY and candidate.scope in {"user", "session"}:
        canonical_store = SEMANTIC_KV
        blocked_reasons.append("file_memory_scope_downgraded")

    return StorageShape(
        canonical_store=canonical_store,
        projections=_dedupe(projections),
        blocked_reasons=blocked_reasons,
    )


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

