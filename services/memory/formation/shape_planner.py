from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from services.memory.formation.schemas import MemoryCandidateLite


EPISODE_LOG = "episode_log"
SEMANTIC_KV = "semantic_kv"
VECTOR_PROJECTION = "vector_projection"
RELATION_GRAPH = "relation_graph"
FILE_MEMORY = "file_memory"
STATE_KV = "state_kv"
DAG = "dag"
REVIEW_QUEUE = "review_queue"

IMPLEMENTED_CANONICAL_STORES = {EPISODE_LOG, SEMANTIC_KV, RELATION_GRAPH, FILE_MEMORY}


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

    def to_dict(self) -> Dict[str, object]:
        return {
            "canonical_store": self.canonical_store,
            "projections": self.projections,
            "blocked_reasons": self.blocked_reasons,
        }


@dataclass(frozen=True)
class RoutingMatrixEntry:
    canonical_store: Optional[str]
    projections: List[str]
    blocked_reasons: List[str] = field(default_factory=list)


LAYER_ROUTE_MATRIX: Dict[str, RoutingMatrixEntry] = {
    "raw": RoutingMatrixEntry(EPISODE_LOG, []),
    "event": RoutingMatrixEntry(EPISODE_LOG, [EPISODE_LOG, VECTOR_PROJECTION]),
    "state": RoutingMatrixEntry(SEMANTIC_KV, [EPISODE_LOG], ["state_kv_downgraded_to_semantic_kv"]),
    "semantic": RoutingMatrixEntry(SEMANTIC_KV, [EPISODE_LOG, VECTOR_PROJECTION]),
    "insight": RoutingMatrixEntry(SEMANTIC_KV, [EPISODE_LOG, VECTOR_PROJECTION]),
    "relation": RoutingMatrixEntry(RELATION_GRAPH, [EPISODE_LOG]),
    "file": RoutingMatrixEntry(FILE_MEMORY, [EPISODE_LOG, VECTOR_PROJECTION]),
    "non_memory": RoutingMatrixEntry(None, [], ["non_memory"]),
}


# Type defaults remain as a compatibility shim. The router first asks what layer
# the candidate belongs to, then lets storage_intent and write_strategy refine
# the canonical/projection shape. This keeps LLM output semantic while code owns
# deterministic storage decisions.
TYPE_DEFAULT_LAYERS: Dict[str, str] = {
    "preference": "semantic",
    "profile_fact": "semantic",
    "project_rule": "file",
    "procedure": "file",
    "decision": "event",
    "task_state": "state",
    "entity_relation": "relation",
    "episodic_event": "event",
    "embedding_hint": "semantic",
    "non_memory": "non_memory",
}


def has_explicit_graph_relation(candidate: MemoryCandidateLite) -> bool:
    text = f"{candidate.text} {candidate.reason}".lower()
    return any(marker in text for marker in GRAPH_RELATION_MARKERS)


def plan_storage_shape(
    candidate: MemoryCandidateLite,
    *,
    write_strategy: Optional[str] = None,
    memory_layers: Optional[Iterable[str]] = None,
) -> StorageShape:
    base = _shape_from_layers(candidate, memory_layers=memory_layers)
    projections = list(base.projections)
    blocked_reasons = list(base.blocked_reasons)
    canonical_store, intent_projections, intent_reasons = _shape_from_intent(
        candidate,
        fallback=base.canonical_store,
    )
    projections.extend(intent_projections)
    blocked_reasons.extend(intent_reasons)

    canonical_store, strategy_projections, strategy_reasons = _shape_from_write_strategy(
        write_strategy,
        fallback=canonical_store,
    )
    projections.extend(strategy_projections)
    blocked_reasons.extend(strategy_reasons)

    if candidate.type == "entity_relation" and not has_explicit_graph_relation(candidate):
        canonical_store = None
        projections = [EPISODE_LOG]
        blocked_reasons.append("graph_relation_missing")

    if candidate.type in {"decision", "task_state"} and has_explicit_graph_relation(candidate):
        if RELATION_GRAPH not in projections:
            projections.append(RELATION_GRAPH)

    if candidate.type in {"project_rule", "procedure"} and candidate.scope in {"user", "session"}:
        if canonical_store == FILE_MEMORY:
            canonical_store = SEMANTIC_KV
        if "file_memory_scope_downgraded" not in blocked_reasons:
            blocked_reasons.append("file_memory_scope_downgraded")

    return StorageShape(
        canonical_store=canonical_store,
        projections=_dedupe(projections),
        blocked_reasons=_dedupe(blocked_reasons),
    )


def _shape_from_layers(
    candidate: MemoryCandidateLite,
    *,
    memory_layers: Optional[Iterable[str]],
) -> StorageShape:
    default_layer = TYPE_DEFAULT_LAYERS.get(candidate.type, "non_memory")
    provided_layers = list(memory_layers or [])
    candidate_layer = candidate.memory_layer
    if not provided_layers and candidate_layer == "semantic" and default_layer != "semantic" and candidate.storage_intent == "auto":
        candidate_layer = default_layer
    layers = _normalize_layers([*(provided_layers or []), candidate_layer])
    if not layers:
        layers = [default_layer]
    primary = layers[0]
    entry = LAYER_ROUTE_MATRIX.get(primary, LAYER_ROUTE_MATRIX["non_memory"])
    projections = list(entry.projections)
    blocked_reasons = list(entry.blocked_reasons)
    for layer in layers[1:]:
        secondary = LAYER_ROUTE_MATRIX.get(layer)
        if not secondary:
            continue
        projections.extend(secondary.projections)
        if layer in {"relation", "state"} and secondary.canonical_store:
            projections.append(secondary.canonical_store)
        blocked_reasons.extend(secondary.blocked_reasons)
    return StorageShape(
        canonical_store=entry.canonical_store,
        projections=_dedupe(projections),
        blocked_reasons=_dedupe(blocked_reasons),
    )


def _shape_from_intent(candidate: MemoryCandidateLite, *, fallback: Optional[str]) -> tuple[Optional[str], List[str], List[str]]:
    intent = candidate.storage_intent
    if intent == "auto":
        return fallback, [], []
    if intent == "none":
        return None, [], []
    if intent == VECTOR_PROJECTION:
        return fallback, [VECTOR_PROJECTION], ["vector_projection_not_source_of_truth"]
    if intent in IMPLEMENTED_CANONICAL_STORES:
        return intent, [], []
    if intent == STATE_KV:
        return SEMANTIC_KV, [EPISODE_LOG], ["state_kv_downgraded_to_semantic_kv"]
    if intent == DAG:
        return fallback, [RELATION_GRAPH], ["dag_projection_only"]
    if intent == REVIEW_QUEUE:
        return None, [EPISODE_LOG], ["review_queue_not_implemented"]
    return fallback, [], []


def _shape_from_write_strategy(
    write_strategy: Optional[str],
    *,
    fallback: Optional[str],
) -> tuple[Optional[str], List[str], List[str]]:
    strategy = str(write_strategy or "").strip()
    if not strategy:
        return fallback, [], []
    if strategy == "do_not_write":
        return None, [], ["write_strategy_do_not_write"]
    if strategy == "needs_review":
        return None, [EPISODE_LOG], ["review_queue_not_implemented"]
    if strategy == "link_as_relation":
        return RELATION_GRAPH, [EPISODE_LOG], []
    if strategy == "mark_conflict":
        return None, [EPISODE_LOG, RELATION_GRAPH], ["conflict_requires_review"]
    if strategy == "supersede_existing":
        return fallback, [RELATION_GRAPH], []
    return fallback, [], []


def _normalize_layers(values: Iterable[str]) -> List[str]:
    valid = set(LAYER_ROUTE_MATRIX)
    result: List[str] = []
    for value in values:
        layer = str(value or "").strip()
        if layer not in valid or layer in result:
            continue
        result.append(layer)
    return result


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

