from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.schemas import MemoryCandidateLite
from services.memory.formation.mutation_planner import build_write_plan
from services.memory.formation.storage_routing import plan_storage_route


def candidate(
    *,
    text: str,
    memory_type: str,
    scope: str = "project",
    layer: str = "semantic",
    intent: str = "auto",
) -> MemoryCandidateLite:
    return MemoryCandidateLite(
        text=text,
        type=memory_type,
        scope=scope,
        action="ADD",
        importance=0.8,
        reason="route matrix contract",
        stability="stable",
        memory_layer=layer,
        storage_intent=intent,
        candidate_id=f"cand_{memory_type}_{intent}",
    )


def test_preference_routes_to_semantic_source_with_vector_projection() -> None:
    route = plan_storage_route(
        candidate(text="The user prefers concise Chinese replies.", memory_type="preference", scope="user")
    )

    assert route.canonical_store == "semantic_kv"
    assert route.source_of_truth_store == "semantic_kv"
    assert route.canonical_write == {"store": "semantic_kv", "role": "source_of_truth"}
    assert "vector_projection" in route.projections
    assert {"store": "vector_projection", "role": "projection", "source_of_truth": False} in route.projection_writes
    assert "vector_projection" not in route.unsupported_routes
    assert not route.review_required


def test_project_rule_routes_to_file_source() -> None:
    route = plan_storage_route(
        candidate(
            text="Project rule: keep memory work aligned to the core pipeline roadmap.",
            memory_type="project_rule",
            layer="file",
            intent="file_memory",
        )
    )

    assert route.canonical_store == "file_memory"
    assert route.source_of_truth_store == "file_memory"
    assert route.canonical_write == {"store": "file_memory", "role": "source_of_truth"}
    assert "episode_log" in route.projections
    assert {"store": "episode_log", "role": "evidence", "source_of_truth": False} in route.projection_writes


def test_state_kv_is_explicitly_downgraded_until_store_exists() -> None:
    route = plan_storage_route(
        candidate(
            text="Task state: storage routing matrix is being implemented.",
            memory_type="task_state",
            layer="state",
            intent="state_kv",
        )
    )

    assert route.canonical_store == "semantic_kv"
    assert route.source_of_truth_store == "semantic_kv"
    assert "state_kv" in route.unsupported_routes
    assert "state_kv_downgraded_to_semantic_kv" in route.blocked_reasons


def test_vector_intent_is_projection_not_source_of_truth() -> None:
    route = plan_storage_route(
        candidate(
            text="Search surface hint for runtime compaction.",
            memory_type="embedding_hint",
            intent="vector_projection",
        )
    )

    assert route.canonical_store == "semantic_kv"
    assert route.source_of_truth_store == "semantic_kv"
    assert "vector_projection" in route.projections
    assert "vector_projection" in route.unsupported_routes
    assert "vector_projection_not_source_of_truth" in route.blocked_reasons


def test_review_queue_route_requires_review_without_canonical_write() -> None:
    route = plan_storage_route(
        candidate(
            text="The candidate may conflict with an existing project rule.",
            memory_type="decision",
            layer="event",
            intent="review_queue",
        )
    )

    assert route.canonical_store is None
    assert route.source_of_truth_store is None
    assert route.canonical_write is None
    assert route.review_required
    assert "review_queue" in route.unsupported_routes
    assert "episode_log" in route.projections


def test_dag_route_is_explicit_projection_downgrade() -> None:
    route = plan_storage_route(
        candidate(
            text="Decision A supersedes Decision B.",
            memory_type="decision",
            layer="event",
            intent="dag",
        )
    )

    assert route.canonical_store == "episode_log"
    assert route.source_of_truth_store == "episode_log"
    assert "dag" in route.unsupported_routes
    assert "relation_graph" in route.projections
    assert "dag_projection_only" in route.blocked_reasons


def test_write_plan_carries_storage_route_trace() -> None:
    write_plan = build_write_plan(
        candidate(
            text="Task state: route matrix trace is attached to write plans.",
            memory_type="task_state",
            layer="state",
            intent="state_kv",
        ),
        evidence_episode_ids=["ep_route"],
    )

    assert write_plan.canonical_store == "semantic_kv"
    assert write_plan.storage_route["source_of_truth_store"] == "semantic_kv"
    assert write_plan.storage_route["canonical_write"] == {"store": "semantic_kv", "role": "source_of_truth"}
    assert {"store": "episode_log", "role": "evidence", "source_of_truth": False} in write_plan.storage_route["projection_writes"]
    assert "state_kv" in write_plan.storage_route["unsupported_routes"]
    assert "state_kv_downgraded_to_semantic_kv" in write_plan.storage_route["blocked_reasons"]


def main() -> None:
    test_preference_routes_to_semantic_source_with_vector_projection()
    test_project_rule_routes_to_file_source()
    test_state_kv_is_explicitly_downgraded_until_store_exists()
    test_vector_intent_is_projection_not_source_of_truth()
    test_review_queue_route_requires_review_without_canonical_write()
    test_dag_route_is_explicit_projection_downgrade()
    test_write_plan_carries_storage_route_trace()
    print("memory storage routing contract ok")


if __name__ == "__main__":
    main()
