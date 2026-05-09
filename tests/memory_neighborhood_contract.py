from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.integration_planner import plan_memory_integration
from services.memory.formation.neighborhood import MemoryNeighborhoodRepository, NeighborhoodFetchPolicy
from services.memory.formation.schemas import MemoryCandidateLite, MemoryWritePlan
from services.memory.storage.applier import MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore


def candidate(
    text: str,
    *,
    memory_type: str = "preference",
    scope: str = "user",
    importance: float = 0.82,
    stability: str = "stable",
    candidate_id: str = "cand",
) -> MemoryCandidateLite:
    return MemoryCandidateLite(
        text=text,
        type=memory_type,
        scope=scope,
        action="ADD",
        importance=importance,
        reason="neighborhood test",
        stability=stability,
        candidate_id=candidate_id,
    )


def plan(
    text: str,
    *,
    plan_id: str,
    memory_type: str = "preference",
    scope: str = "user",
    canonical_store: str = "semantic_kv",
    evidence: str = "ep_1",
) -> MemoryWritePlan:
    return MemoryWritePlan(
        plan_id=plan_id,
        candidate_id=f"cand_{plan_id}",
        action="ADD",
        canonical_store=canonical_store,
        projections=["episode_log", "vector_projection"],
        scope=scope,
        evidence_episode_ids=[evidence],
        confidence=0.88,
        status="planned",
        type=memory_type,
        text=text,
        reason="seed memory",
    )


def test_exact_key_snapshot_feeds_noop_integration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        seed = plan("The user prefers concise status summaries.", plan_id="plan_exact")
        MemoryWriteApplier(store).apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])

        cand = candidate("The user prefers concise status summaries.")
        snapshots = MemoryNeighborhoodRepository(store).fetch_for_candidate(
            cand,
            session_id="session-a",
            workspace_dir=tmp,
        )
        integration = plan_memory_integration(cand, existing_memories=snapshots)

        assert snapshots
        assert snapshots[0].match["path"] == "exact_key"
        assert snapshots[0].namespace == "session:session-a"
        assert snapshots[0].evidence_episode_ids == ["ep_1"]
        assert integration.action == "NOOP"


def test_lexical_neighborhood_finds_near_duplicate_for_merge() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        seed = plan("The user prefers concise status summaries after validation runs.", plan_id="plan_near")
        MemoryWriteApplier(store).apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])

        cand = candidate("The user prefers concise progress summaries after validation.")
        snapshots = MemoryNeighborhoodRepository(store).fetch_for_candidate(
            cand,
            session_id="session-a",
            workspace_dir=tmp,
        )
        integration = plan_memory_integration(cand, existing_memories=snapshots)

        assert snapshots
        assert any(snapshot.match["path"] in {"lexical_same_type", "exact_key"} for snapshot in snapshots)
        assert integration.action in {"MERGE", "NOOP"}
        assert integration.target_memory_id == snapshots[0].memory_id


def test_compatible_type_snapshot_finds_related_project_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        seed = plan(
            "Project rule: memory writes must remain default-off until quality gates pass.",
            plan_id="plan_rule",
            memory_type="project_rule",
            scope="project",
            canonical_store="semantic_kv",
        )
        MemoryWriteApplier(store).apply_plans(session_id="session-a", workspace_dir=tmp, plans=[seed])

        cand = candidate(
            "Decision: memory writes stay default-off while quality gates are incomplete.",
            memory_type="decision",
            scope="project",
            importance=0.83,
            stability="evolving",
        )
        snapshots = MemoryNeighborhoodRepository(store).fetch_for_candidate(
            cand,
            session_id="session-a",
            workspace_dir=tmp,
        )

        assert snapshots
        assert snapshots[0].type == "project_rule"
        assert snapshots[0].match["path"] == "lexical_compatible_type"


def test_neighborhood_policy_caps_and_clips_snapshots() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        plans: List[MemoryWritePlan] = []
        for index in range(4):
            plans.append(
                plan(
                    "The user prefers concise validation summaries " + ("detail " * 80) + str(index),
                    plan_id=f"plan_cap_{index}",
                )
            )
        MemoryWriteApplier(store).apply_plans(session_id="session-a", workspace_dir=tmp, plans=plans)

        cand = candidate("The user prefers concise validation summaries.")
        snapshots = MemoryNeighborhoodRepository(store).fetch_for_candidate(
            cand,
            session_id="session-a",
            workspace_dir=tmp,
            policy=NeighborhoodFetchPolicy(max_items=2, max_snapshot_text_chars=80, max_total_chars=200),
        )

        assert len(snapshots) <= 2
        assert all(len(snapshot.text) <= 80 for snapshot in snapshots)
        assert all("score" in snapshot.match for snapshot in snapshots)


def main() -> None:
    test_exact_key_snapshot_feeds_noop_integration()
    test_lexical_neighborhood_finds_near_duplicate_for_merge()
    test_compatible_type_snapshot_finds_related_project_memory()
    test_neighborhood_policy_caps_and_clips_snapshots()
    print("memory neighborhood contract ok")


if __name__ == "__main__":
    main()
