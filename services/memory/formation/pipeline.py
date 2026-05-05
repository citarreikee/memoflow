from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.formation.extractor import (
    extract_candidates_rule_based,
    extract_candidates_with_llm,
    should_trigger_extraction,
)
from services.memory.formation.mutation_planner import build_write_plans
from services.memory.formation.schemas import MemoryCandidateLite, MemoryWritePlan


@dataclass
class MemoryFormationResult:
    triggered: bool
    episode_ids: List[str]
    candidates: List[MemoryCandidateLite]
    plans: List[MemoryWritePlan]
    skipped_reason: str | None = None
    extractor_debug: Dict[str, Any] | None = None

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "episode_ids": self.episode_ids,
            "candidate_count": len(self.candidates),
            "planned_count": len([plan for plan in self.plans if plan.status == "planned"]),
            "blocked_count": len([plan for plan in self.plans if plan.status == "blocked"]),
            "needs_review_count": len([plan for plan in self.plans if plan.status == "needs_review"]),
            "noop_count": len([plan for plan in self.plans if plan.status == "noop"]),
            "skipped_reason": self.skipped_reason,
            "extractor": self.extractor_debug or {},
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "plans": [plan.to_dict() for plan in self.plans],
        }


async def run_memory_formation_dry_run(episode: Dict[str, Any]) -> MemoryFormationResult:
    episode_id = str(episode.get("episode_id") or "")
    episode_ids = [episode_id] if episode_id else []
    if not should_trigger_extraction(episode):
        return MemoryFormationResult(
            triggered=False,
            episode_ids=episode_ids,
            candidates=[],
            plans=[],
            skipped_reason="trigger_policy_noop",
        )
    candidates, extractor_debug = await _extract_candidates(episode)
    plans = build_write_plans(candidates, evidence_episode_ids=episode_ids)
    return MemoryFormationResult(
        triggered=True,
        episode_ids=episode_ids,
        candidates=candidates,
        plans=plans,
        extractor_debug=extractor_debug,
    )


async def _extract_candidates(episode: Dict[str, Any]) -> tuple[List[MemoryCandidateLite], Dict[str, Any]]:
    mode = settings.MEMORY_FORMATION_EXTRACTOR
    if mode == "llm":
        candidates, debug = await extract_candidates_with_llm(episode)
        if candidates or not debug.get("error"):
            debug["mode"] = "llm"
            return candidates, debug
        fallback = extract_candidates_rule_based(episode)
        debug["mode"] = "llm_with_rule_fallback"
        debug["fallback_candidate_count"] = len(fallback)
        return fallback, debug
    candidates = extract_candidates_rule_based(episode)
    return candidates, {"mode": "rule", "candidate_count": len(candidates)}
