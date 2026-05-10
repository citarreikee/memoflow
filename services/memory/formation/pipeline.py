from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.formation.extractor import (
    extract_candidates_rule_based,
    extract_candidates_with_llm,
    should_trigger_extraction,
)
from services.memory.formation.observations import (
    extract_observations_rule_based,
    extract_observations_with_llm,
    form_candidates_from_observations,
    form_candidates_from_observations_with_llm,
)
from services.memory.formation.mutation_planner import build_write_plans
from services.memory.formation.schemas import MemoryCandidateLite, MemoryObservation, MemoryWritePlan


@dataclass
class MemoryFormationResult:
    triggered: bool
    episode_ids: List[str]
    observations: List[MemoryObservation]
    candidates: List[MemoryCandidateLite]
    plans: List[MemoryWritePlan]
    skipped_reason: str | None = None
    extractor_debug: Dict[str, Any] | None = None

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "episode_ids": self.episode_ids,
            "observation_count": len(self.observations),
            "candidate_count": len(self.candidates),
            "planned_count": len([plan for plan in self.plans if plan.status == "planned"]),
            "blocked_count": len([plan for plan in self.plans if plan.status == "blocked"]),
            "needs_review_count": len([plan for plan in self.plans if plan.status == "needs_review"]),
            "noop_count": len([plan for plan in self.plans if plan.status == "noop"]),
            "skipped_reason": self.skipped_reason,
            "extractor": self.extractor_debug or {},
            "observations": [observation.to_dict() for observation in self.observations],
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
            observations=[],
            candidates=[],
            plans=[],
            skipped_reason="trigger_policy_noop",
        )
    observations, observation_debug = await _extract_observations(episode)
    candidates, formation_debug = await _form_candidates(episode, observations)
    plans = build_write_plans(candidates, evidence_episode_ids=episode_ids)
    extractor_debug = {
        "observation": observation_debug,
        "formation": formation_debug,
    }
    return MemoryFormationResult(
        triggered=True,
        episode_ids=episode_ids,
        observations=observations,
        candidates=candidates,
        plans=plans,
        extractor_debug=extractor_debug,
    )


async def _extract_observations(episode: Dict[str, Any]) -> tuple[List[MemoryObservation], Dict[str, Any]]:
    mode = settings.MEMORY_FORMATION_EXTRACTOR
    if mode == "llm":
        observations, debug = await extract_observations_with_llm(episode)
        if observations or not debug.get("error"):
            debug["mode"] = "llm_observation"
            return observations, debug
        fallback = extract_observations_rule_based(episode)
        debug["mode"] = "llm_observation_with_rule_fallback"
        debug["fallback_observation_count"] = len(fallback)
        return fallback, debug
    observations = extract_observations_rule_based(episode)
    return observations, {"mode": "rule_observation", "observation_count": len(observations)}


async def _form_candidates(
    episode: Dict[str, Any],
    observations: List[MemoryObservation],
) -> tuple[List[MemoryCandidateLite], Dict[str, Any]]:
    if observations:
        if settings.MEMORY_FORMATION_EXTRACTOR == "llm":
            candidates, debug = await form_candidates_from_observations_with_llm(
                episode,
                observations,
                max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES,
            )
            if candidates or not debug.get("error"):
                return candidates, debug
            fallback, fallback_debug = form_candidates_from_observations(
                observations,
                max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES,
            )
            fallback_debug["mode"] = "llm_observation_candidate_with_mapping_fallback"
            fallback_debug["llm_error"] = debug.get("error")
            fallback_debug["llm_provider"] = debug.get("provider")
            fallback_debug["llm_model"] = debug.get("model")
            return fallback, fallback_debug
        return form_candidates_from_observations(
            observations,
            max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES,
        )
    # Degraded compatibility path: if observation extraction produces nothing,
    # fall back to the old direct candidate extractor instead of dropping a
    # potentially important explicit memory turn.
    mode = settings.MEMORY_FORMATION_EXTRACTOR
    if mode == "llm":
        candidates, debug = await extract_candidates_with_llm(episode)
        if candidates or not debug.get("error"):
            debug["mode"] = "legacy_llm_candidate_fallback"
            return candidates, debug
        fallback = extract_candidates_rule_based(episode)
        debug["mode"] = "legacy_llm_candidate_with_rule_fallback"
        debug["fallback_candidate_count"] = len(fallback)
        return fallback, debug
    candidates = extract_candidates_rule_based(episode)
    return candidates, {"mode": "legacy_rule_candidate_fallback", "candidate_count": len(candidates)}
