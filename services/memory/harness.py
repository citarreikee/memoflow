from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class MemoryHarness:
    name: str
    recent_turn_limit: int
    compaction_token_threshold: int
    memory_types: List[str]
    retrieval_priority: List[str]
    tool_trace_policy: str


_HARNESSES: Dict[str, MemoryHarness] = {
    "conversation": MemoryHarness(
        name="conversation",
        recent_turn_limit=6,
        compaction_token_threshold=12000,
        memory_types=["semantic", "preference", "project_state"],
        retrieval_priority=["profile", "semantic", "project_state", "warning"],
        tool_trace_policy="compact_aggressively",
    ),
    "coding": MemoryHarness(
        name="coding",
        recent_turn_limit=8,
        compaction_token_threshold=18000,
        memory_types=["semantic", "project_state", "procedural", "warning"],
        retrieval_priority=["warning", "project_state", "procedural", "semantic"],
        tool_trace_policy="preserve_changed_files_and_errors",
    ),
}


def get_harness(name: str) -> MemoryHarness:
    return _HARNESSES.get((name or "").strip().lower(), _HARNESSES["coding"])


def list_harnesses() -> List[Dict[str, object]]:
    return [
        {
            "name": harness.name,
            "recent_turn_limit": harness.recent_turn_limit,
            "compaction_token_threshold": harness.compaction_token_threshold,
            "memory_types": harness.memory_types,
            "retrieval_priority": harness.retrieval_priority,
            "tool_trace_policy": harness.tool_trace_policy,
        }
        for harness in _HARNESSES.values()
    ]
