from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.integration_planner import plan_memory_integration
from services.memory.formation.integration_schemas import ExistingMemorySnapshot
from services.memory.formation.schemas import MemoryCandidateLite


def candidate(
    text: str,
    *,
    memory_type: str = "decision",
    scope: str = "project",
    importance: float = 0.82,
    stability: str = "stable",
    candidate_id: str = "cand",
    action: str = "ADD",
) -> MemoryCandidateLite:
    return MemoryCandidateLite(
        text=text,
        type=memory_type,
        scope=scope,
        action=action,
        importance=importance,
        reason="contract test",
        stability=stability,
        candidate_id=candidate_id,
    )


def memory(
    memory_id: str,
    text: str,
    *,
    memory_type: str = "decision",
    scope: str = "project",
) -> ExistingMemorySnapshot:
    return ExistingMemorySnapshot(
        memory_id=memory_id,
        type=memory_type,
        scope=scope,
        text=text,
        confidence=0.8,
        evidence_episode_ids=["ep_existing"],
    )


def test_add_when_no_existing_memory() -> None:
    plan = plan_memory_integration(
        candidate("Decision: build integration planner as dry-run first."),
        existing_memories=[],
    )

    assert plan.action == "ADD"
    assert plan.target_memory_id is None
    assert plan.suggested_text


def test_noop_when_duplicate_existing_memory() -> None:
    existing = memory("mem_1", "Decision: build integration planner as dry-run first.")
    plan = plan_memory_integration(
        candidate("Decision: build integration planner as dry-run first."),
        existing_memories=[existing],
    )

    assert plan.action == "NOOP"
    assert plan.target_memory_id == "mem_1"
    assert "duplicate_existing_memory" in plan.blocked_reasons


def test_merge_when_candidate_overlaps_existing_memory() -> None:
    existing = memory("mem_1", "Decision: build integration planner as dry-run first.")
    plan = plan_memory_integration(
        candidate("Decision: build integration planner dry-run first, then add LLM planner later."),
        existing_memories=[existing],
    )

    assert plan.action == "MERGE"
    assert plan.target_memory_id == "mem_1"
    assert "Additional evidence" in (plan.suggested_text or "")



def test_update_when_candidate_requests_update_existing_memory() -> None:
    existing = memory("mem_state", "Task state: formation scenario eval is planned but not implemented.", memory_type="task_state")
    plan = plan_memory_integration(
        candidate(
            "Task state: formation scenario eval is implemented and connected to the harness.",
            memory_type="task_state",
            importance=0.86,
            stability="evolving",
            action="UPDATE",
        ),
        existing_memories=[existing],
    )

    assert plan.action == "UPDATE"
    assert plan.target_memory_id == "mem_state"
    assert plan.suggested_text == "Task state: formation scenario eval is implemented and connected to the harness."


def test_supersede_when_candidate_explicitly_replaces_existing_memory() -> None:
    existing = memory("mem_sidecar", "Decision: use local qwen sidecar for memory compaction.")
    plan = plan_memory_integration(
        candidate("Decision: deepseek sidecar replaces local qwen sidecar for memory compaction."),
        existing_memories=[existing],
    )

    assert plan.action == "SUPERSEDE"
    assert plan.target_memory_id == "mem_sidecar"
    assert plan.related_memory_ids == ["mem_sidecar"]
    assert plan.graph_relations[0]["relation_type"] == "supersedes"
    assert "supersedes" in plan.rationale.lower()

def test_link_when_relation_targets_existing_memory() -> None:
    existing = memory("mem_runtime", "Runtime compaction keeps recent raw turns and checkpoints older turns.")
    plan = plan_memory_integration(
        candidate(
            "Retrieval depends_on runtime compaction checkpoints for bounded context.",
            memory_type="entity_relation",
            stability="evolving",
        ),
        existing_memories=[existing],
    )

    assert plan.action == "LINK"
    assert plan.target_memory_id == "mem_runtime"
    assert plan.graph_relations[0]["relation_type"] == "depends_on"


def test_relation_without_target_needs_review() -> None:
    plan = plan_memory_integration(
        candidate(
            "Retrieval depends_on a future embedding worker.",
            memory_type="entity_relation",
            stability="evolving",
        ),
        existing_memories=[],
    )

    assert plan.action == "NEEDS_REVIEW"
    assert "relation_target_missing" in plan.needs_review_reasons


def test_low_confidence_candidate_needs_review() -> None:
    plan = plan_memory_integration(
        candidate(
            "Decision: maybe we should adjust one small debug wording.",
            importance=0.5,
            stability="unknown",
        ),
        existing_memories=[],
    )

    assert plan.action == "NEEDS_REVIEW"
    assert "low_confidence_or_unknown_stability" in plan.needs_review_reasons


def test_possible_conflict_needs_review() -> None:
    existing = memory("mem_pref", "The user prefers concise status summaries.", memory_type="preference", scope="user")
    plan = plan_memory_integration(
        candidate(
            "The user no longer wants concise status summaries.",
            memory_type="preference",
            scope="user",
            importance=0.82,
            stability="stable",
        ),
        existing_memories=[existing],
    )

    assert plan.action == "NEEDS_REVIEW"
    assert plan.target_memory_id == "mem_pref"
    assert "possible_conflict" in plan.needs_review_reasons


def main() -> None:
    test_add_when_no_existing_memory()
    test_noop_when_duplicate_existing_memory()
    test_merge_when_candidate_overlaps_existing_memory()
    test_update_when_candidate_requests_update_existing_memory()
    test_supersede_when_candidate_explicitly_replaces_existing_memory()
    test_link_when_relation_targets_existing_memory()
    test_relation_without_target_needs_review()
    test_low_confidence_candidate_needs_review()
    test_possible_conflict_needs_review()
    print("memory integration planner contract ok")


if __name__ == "__main__":
    main()


