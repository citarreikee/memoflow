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

IMPLEMENTED_CANONICAL_STORES = {EPISODE_LOG, SEMANTIC_KV, RELATION_GRAPH, FILE_MEMORY, STATE_KV, DAG}


GRAPH_RELATION_MARKERS = (
    "depends_on",
    "depends on",
    "渚濊禆",
    "supersedes",
    "鍙栦唬",
    "鏇夸唬",
    "contradicts",
    "鍐茬獊",
    "鐭涚浘",
    "derived_from",
    "derived from",
    "鏉ヨ嚜",
    "caused_by",
    "caused by",
    "瀵艰嚧",
    "part_of",
    "part of",
    "缁勬垚",
    "blocks",
    "闃诲",
    "valid_from",
    "valid_until",
)


@dataclass(frozen=True)
class StorageRoute:
    canonical_store: Optional[str]
    projections: List[str]
    blocked_reasons: List[str]
    source_of_truth_store: Optional[str] = None
    canonical_write: Optional[Dict[str, object]] = None
    projection_writes: List[Dict[str, object]] = field(default_factory=list)
    review_write: Optional[Dict[str, object]] = None
    unsupported_routes: List[str] = field(default_factory=list)
    review_required: bool = False
    routing_reason: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "canonical_store": self.canonical_store,
            "projections": self.projections,
            "blocked_reasons": self.blocked_reasons,
            "source_of_truth_store": self.source_of_truth_store,
            "canonical_write": self.canonical_write,
            "projection_writes": self.projection_writes,
            "review_write": self.review_write,
            "unsupported_routes": self.unsupported_routes,
            "review_required": self.review_required,
            "routing_reason": self.routing_reason,
        }


@dataclass(frozen=True)
class RoutingMatrixEntry:
    canonical_store: Optional[str]
    projections: List[str]
    blocked_reasons: List[str] = field(default_factory=list)


LAYER_ROUTE_MATRIX: Dict[str, RoutingMatrixEntry] = {
    "raw": RoutingMatrixEntry(EPISODE_LOG, []),
    "event": RoutingMatrixEntry(EPISODE_LOG, [EPISODE_LOG, VECTOR_PROJECTION]),
    "state": RoutingMatrixEntry(STATE_KV, [EPISODE_LOG]),
    "semantic": RoutingMatrixEntry(SEMANTIC_KV, [EPISODE_LOG, VECTOR_PROJECTION]),
    "insight": RoutingMatrixEntry(SEMANTIC_KV, [EPISODE_LOG, VECTOR_PROJECTION]),
    "relation": RoutingMatrixEntry(RELATION_GRAPH, [EPISODE_LOG]),
    "dag": RoutingMatrixEntry(DAG, [EPISODE_LOG, RELATION_GRAPH]),
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


def plan_storage_route(
    candidate: MemoryCandidateLite,
    *,
    write_strategy: Optional[str] = None,
    memory_layers: Optional[Iterable[str]] = None,
) -> StorageRoute:
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

    projections = _dedupe(projections)
    blocked_reasons = _dedupe(blocked_reasons)
    review_write = _review_write(blocked_reasons)
    return StorageRoute(
        canonical_store=canonical_store,
        projections=projections,
        blocked_reasons=blocked_reasons,
        source_of_truth_store=canonical_store,
        canonical_write=_canonical_write(canonical_store),
        projection_writes=_projection_writes(projections, canonical_store=canonical_store),
        review_write=review_write,
        unsupported_routes=_unsupported_routes(blocked_reasons),
        review_required=review_write is not None,
        routing_reason=_routing_reason(candidate=candidate, write_strategy=write_strategy, memory_layers=memory_layers),
    )


def _shape_from_layers(
    candidate: MemoryCandidateLite,
    *,
    memory_layers: Optional[Iterable[str]],
) -> StorageRoute:
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
    return StorageRoute(
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
    if intent == DAG:
        return DAG, [EPISODE_LOG, RELATION_GRAPH], []
    if intent in IMPLEMENTED_CANONICAL_STORES:
        return intent, [], []
    if intent == REVIEW_QUEUE:
        return None, [EPISODE_LOG], ["review_queue_required"]
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
        return None, [EPISODE_LOG], ["review_queue_required"]
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


def _unsupported_routes(blocked_reasons: List[str]) -> List[str]:
    routes: List[str] = []
    reason_to_route = {
        "state_kv_downgraded_to_semantic_kv": STATE_KV,
        "vector_projection_not_source_of_truth": VECTOR_PROJECTION,
    }
    for reason in blocked_reasons:
        route = reason_to_route.get(reason)
        if route and route not in routes:
            routes.append(route)
    return routes


def _canonical_write(canonical_store: Optional[str]) -> Optional[Dict[str, object]]:
    if not canonical_store:
        return None
    return {
        "store": canonical_store,
        "role": "source_of_truth",
    }


def _projection_writes(projections: List[str], *, canonical_store: Optional[str]) -> List[Dict[str, object]]:
    writes: List[Dict[str, object]] = []
    for store in projections:
        role = "evidence" if store == EPISODE_LOG else "projection"
        if store == canonical_store:
            role = "canonical_mirror"
        writes.append(
            {
                "store": store,
                "role": role,
                "source_of_truth": False,
            }
        )
    return writes


def _review_write(blocked_reasons: List[str]) -> Optional[Dict[str, object]]:
    if "review_queue_required" not in blocked_reasons and "conflict_requires_review" not in blocked_reasons:
        return None
    return {
        "store": REVIEW_QUEUE,
        "role": "human_review",
        "source_of_truth": False,
    }


def _routing_reason(
    *,
    candidate: MemoryCandidateLite,
    write_strategy: Optional[str],
    memory_layers: Optional[Iterable[str]],
) -> str:
    layers = ",".join(_normalize_layers([*(list(memory_layers or [])), candidate.memory_layer])) or "default"
    strategy = str(write_strategy or "default").strip() or "default"
    return f"layer={layers};intent={candidate.storage_intent};strategy={strategy}"


