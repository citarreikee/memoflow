from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.storage.applier import MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore


def make_plan(
    *,
    plan_id: str,
    action: str,
    text: str,
    target_memory_id: Optional[str] = None,
    status: str = "planned",
    canonical_store: Optional[str] = "semantic_kv",
    projections: Optional[List[str]] = None,
    graph_relations: Optional[List[dict[str, str]]] = None,
    needs_review_reasons: Optional[List[str]] = None,
) -> MemoryWritePlan:
    return MemoryWritePlan(
        plan_id=plan_id,
        candidate_id=f"cand_{plan_id}",
        action=action,
        canonical_store=canonical_store,
        projections=projections if projections is not None else ["episode_log", "vector_projection"],
        scope="project",
        evidence_episode_ids=[f"ep_{plan_id}"],
        confidence=0.88,
        status=status,
        type="decision",
        text=text,
        reason="mutation contract",
        integration_action=action,
        write_strategy=action.lower(),
        target_memory_id=target_memory_id,
        related_memory_ids=[target_memory_id] if target_memory_id else [],
        graph_relations=graph_relations or [],
        memory_layers=["event"],
        needs_review_reasons=needs_review_reasons or [],
    )


def active_records(store: MemorySQLiteStore) -> list[dict[str, object]]:
    return store.search_active_records(
        query="Decision",
        namespace="",
        scopes=["project"],
        memory_types=["decision"],
        limit=10,
    )


def test_update_creates_successor_version_and_supersedes_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        seed = make_plan(plan_id="seed_update", action="ADD", text="Decision: use rule-only mutation planning.")
        applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])
        target = active_records(store)[0]

        update = make_plan(
            plan_id="update",
            action="UPDATE",
            text="Decision: use deterministic mutation handlers for apply.",
            target_memory_id=str(target["memory_id"]),
        )
        result = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[update])
        successor = active_records(store)[0]
        old = store.get_record(memory_id=str(target["memory_id"]))

        assert result.canonical_writes == 1
        assert successor["version"] == 2
        assert successor["supersedes_memory_id"] == target["memory_id"]
        assert old is not None
        assert old["status"] == "superseded"


def test_update_replay_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        seed = make_plan(plan_id="seed_update_replay", action="ADD", text="Decision: keep mutation replay safe.")
        applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])
        target = active_records(store)[0]
        update = make_plan(
            plan_id="update_replay",
            action="UPDATE",
            text="Decision: keep mutation replay safe with source plan idempotency.",
            target_memory_id=str(target["memory_id"]),
        )

        first = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[update])
        second = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[update])

        assert first.canonical_writes == 1
        assert first.evidence_links == 1
        assert second.canonical_writes == 0
        assert second.evidence_links == 0
        assert second.vector_projections == 0
        assert store.count_rows("memory_records") == 2
        assert store.count_rows("memory_evidence_links") == 2
        assert store.count_rows("memory_vector_projections") == 2


def test_merge_requires_target_and_adds_successor_when_target_exists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        missing = make_plan(plan_id="merge_missing", action="MERGE", text="Decision: merge needs target.")
        blocked = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[missing])
        assert blocked.blocked_count == 1
        assert "merge_target_missing" in blocked.blocked_reasons
        assert store.count_rows("memory_records") == 0

        seed = make_plan(plan_id="seed_merge", action="ADD", text="Decision: integration routes to write plans.")
        applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])
        target = active_records(store)[0]
        merge = make_plan(
            plan_id="merge",
            action="MERGE",
            text="Decision: integration routes to write plans with mutation handlers.",
            target_memory_id=str(target["memory_id"]),
        )
        result = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[merge])

        assert result.canonical_writes == 1
        assert store.count_rows("memory_records") == 2
        assert store.get_record(memory_id=str(target["memory_id"]))["status"] == "superseded"


def test_supersede_writes_targeted_graph_edge() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        seed = make_plan(plan_id="seed_super", action="ADD", text="Decision: use local sidecar compaction.")
        applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])
        target = active_records(store)[0]
        supersede = make_plan(
            plan_id="super",
            action="SUPERSEDE",
            text="Decision: use async worker sidecar compaction.",
            target_memory_id=str(target["memory_id"]),
            projections=["episode_log", "relation_graph", "vector_projection"],
            graph_relations=[{"relation_type": "supersedes", "target_memory_id": str(target["memory_id"])}],
        )
        result = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[supersede])
        edges = store.search_graph_edges(query="supersedes", scopes=["project"], limit=10)

        assert result.canonical_writes == 1
        assert result.graph_edges >= 1
        assert any(edge["target_memory_id"] == target["memory_id"] for edge in edges)


def test_supersede_replay_does_not_duplicate_edges_or_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        seed = make_plan(plan_id="seed_super_replay", action="ADD", text="Decision: compact through staged worker.")
        applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])
        target = active_records(store)[0]
        supersede = make_plan(
            plan_id="super_replay",
            action="SUPERSEDE",
            text="Decision: compact through staged worker with apply idempotency.",
            target_memory_id=str(target["memory_id"]),
            projections=["episode_log", "relation_graph", "vector_projection"],
            graph_relations=[{"relation_type": "supersedes", "target_memory_id": str(target["memory_id"])}],
        )

        first = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[supersede])
        second = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[supersede])

        assert first.canonical_writes == 1
        assert first.graph_edges >= 1
        assert second.canonical_writes == 0
        assert second.graph_edges == 0
        assert second.evidence_links == 0
        assert store.count_rows("memory_records") == 2
        assert store.count_rows("memory_graph_edges") == 1


def test_link_writes_relation_without_fake_semantic_record() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        seed = make_plan(plan_id="seed_link", action="ADD", text="Decision: routing matrix exists.")
        applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])
        target = active_records(store)[0]
        link = make_plan(
            plan_id="link",
            action="LINK",
            text="Retrieval depends_on routing matrix.",
            target_memory_id=str(target["memory_id"]),
            canonical_store=None,
            projections=["relation_graph"],
            graph_relations=[{"relation_type": "depends_on", "target_memory_id": str(target["memory_id"])}],
        )
        result = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[link])
        edges = store.search_graph_edges(query="depends_on", scopes=["project"], limit=10)

        assert result.canonical_writes == 0
        assert result.graph_edges == 1
        assert store.count_rows("memory_records") == 1
        assert edges[0]["target_memory_id"] == target["memory_id"]


def test_review_and_conflict_are_persisted_without_canonical_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        review = make_plan(
            plan_id="review",
            action="ADD",
            text="Decision: maybe replace the storage backend.",
            status="needs_review",
            canonical_store=None,
            projections=["episode_log"],
            needs_review_reasons=["ambiguous_scope"],
        )
        conflict = make_plan(
            plan_id="conflict",
            action="CONFLICT",
            text="Decision: conflict with existing preference.",
            status="needs_review",
            canonical_store=None,
            projections=["episode_log", "relation_graph"],
            needs_review_reasons=["possible_conflict"],
        )
        result = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[review, conflict])

        assert result.review_items == 2
        assert result.canonical_writes == 0
        assert store.count_rows("memory_records") == 0
        assert store.count_rows("memory_review_items") == 2


def test_review_replay_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        applier = MemoryWriteApplier(store)
        review = make_plan(
            plan_id="review_replay",
            action="CONFLICT",
            text="Decision: possible conflict should wait for review.",
            status="needs_review",
            canonical_store=None,
            projections=["episode_log", "relation_graph"],
            needs_review_reasons=["possible_conflict"],
        )

        first = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[review])
        second = applier.apply_plans(session_id="session-a", workspace_dir=tmp, plans=[review])

        assert first.review_items == 1
        assert second.review_items == 0
        assert store.count_rows("memory_records") == 0
        assert store.count_rows("memory_review_items") == 1


def main() -> None:
    test_update_creates_successor_version_and_supersedes_target()
    test_update_replay_is_idempotent()
    test_merge_requires_target_and_adds_successor_when_target_exists()
    test_supersede_writes_targeted_graph_edge()
    test_supersede_replay_does_not_duplicate_edges_or_records()
    test_link_writes_relation_without_fake_semantic_record()
    test_review_and_conflict_are_persisted_without_canonical_mutation()
    test_review_replay_is_idempotent()
    print("memory mutation contract ok")


if __name__ == "__main__":
    main()
