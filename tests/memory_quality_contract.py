from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.mutation_planner import build_write_plan
from services.memory.formation.schemas import MemoryCandidateLite, normalize_candidate


FIXTURE_PATH = ROOT_DIR / "tests" / "fixtures" / "memory_golden_cases.json"


def load_cases() -> List[Dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))


def test_golden_case(case: Dict[str, Any]) -> None:
    candidate = normalize_candidate(case["candidate"], fallback_id=case["id"])
    assert isinstance(candidate, MemoryCandidateLite)
    plan = build_write_plan(candidate, evidence_episode_ids=list(case.get("evidence_episode_ids") or []))
    expected = case["expected"]

    assert plan.status == expected["status"], _message(case, "status", expected["status"], plan.status)
    assert plan.canonical_store == expected["canonical_store"], _message(
        case,
        "canonical_store",
        expected["canonical_store"],
        plan.canonical_store,
    )
    for projection in expected.get("projections_include", []):
        assert projection in plan.projections, _message(case, "projection", projection, plan.projections)
    for reason in expected.get("blocked_reasons_include", []):
        assert reason in plan.blocked_reasons, _message(case, "blocked_reason", reason, plan.blocked_reasons)
    if plan.status in {"planned", "needs_review"}:
        assert plan.evidence_episode_ids, _message(case, "evidence", "non-empty", plan.evidence_episode_ids)
        assert plan.confidence > 0, _message(case, "confidence", ">0", plan.confidence)
    if plan.status == "noop":
        assert plan.canonical_store is None
        assert plan.projections == []


def test_normalization_clamps_unsafe_values() -> None:
    candidate = normalize_candidate(
        {
            "text": "Bad enum values should normalize safely.",
            "type": "unsupported_type",
            "scope": "global",
            "action": "WRITE",
            "importance": 9,
            "reason": "test",
            "stability": "forever",
            "memory_layer": "everything",
            "storage_intent": "black_hole",
            "evidence_policy": "trust_me",
            "lifecycle_hint": "forever",
        },
        fallback_id="cand_normalized",
    )

    assert candidate.type == "non_memory"
    assert candidate.scope == "session"
    assert candidate.action == "NOOP"
    assert candidate.importance == 1.0
    assert candidate.stability == "unknown"
    assert candidate.memory_layer == "non_memory"
    assert candidate.storage_intent == "none"
    assert candidate.evidence_policy == "required"
    assert candidate.lifecycle_hint == "normal"


def _message(case: Dict[str, Any], field: str, expected: Any, actual: Any) -> str:
    return f"{case['id']} expected {field}={expected!r}, got {actual!r}"


def main() -> None:
    cases = load_cases()
    assert cases, "golden cases must not be empty"
    for case in cases:
        test_golden_case(case)
    test_normalization_clamps_unsafe_values()
    print(f"memory quality contract ok ({len(cases)} golden cases)")


if __name__ == "__main__":
    main()

