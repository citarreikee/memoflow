from __future__ import annotations

from services.memory.formation.storage_routing import (
    DAG,
    EPISODE_LOG,
    FILE_MEMORY,
    GRAPH_RELATION_MARKERS,
    IMPLEMENTED_CANONICAL_STORES,
    LAYER_ROUTE_MATRIX,
    RELATION_GRAPH,
    REVIEW_QUEUE,
    SEMANTIC_KV,
    STATE_KV,
    TYPE_DEFAULT_LAYERS,
    VECTOR_PROJECTION,
    RoutingMatrixEntry,
    StorageRoute as StorageShape,
    has_explicit_graph_relation,
    plan_storage_route,
)


def plan_storage_shape(*args, **kwargs) -> StorageShape:
    return plan_storage_route(*args, **kwargs)
