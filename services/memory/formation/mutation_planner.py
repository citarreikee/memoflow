from __future__ import annotations

import uuid
from typing import Iterable, List

from services.memory.formation.evaluator import evaluate_candidate
from services.memory.formation.schemas import MemoryCandidateLite, MemoryWritePlan
from services.memory.formation.shape_planner import plan_storage_shape


def build_write_plan(
    candidate: MemoryCandidateLite,
    *,
    evidence_episode_ids: List[str],
) -> MemoryWritePlan:
    candidate_id = candidate.candidate_id or f"cand_{uuid.uuid4().hex}"
    evaluation = evaluate_candidate(candidate)
    shape = plan_storage_shape(candidate)
    blocked_reasons = list(evaluation.blocked_reasons)
    blocked_reasons.extend(shape.blocked_reasons)

    status = evaluation.status
    if shape.canonical_store is None and status == "planned":
        status = "blocked"
        blocked_reasons.append("canonical_store_missing")
    if not evidence_episode_ids and status in {"planned", "needs_review"}:
        status = "blocked"
        blocked_reasons.append("evidence_missing")
    if status == "noop":
        canonical_store = None
        projections: List[str] = []
    else:
        canonical_store = shape.canonical_store
        projections = shape.projections

    return MemoryWritePlan(
        plan_id=f"plan_{uuid.uuid4().hex}",
        candidate_id=candidate_id,
        action=candidate.action,
        canonical_store=canonical_store,
        projections=projections,
        scope=candidate.scope,
        evidence_episode_ids=evidence_episode_ids,
        confidence=evaluation.confidence,
        status=status,
        blocked_reasons=_dedupe(blocked_reasons),
        type=candidate.type,
        text=candidate.text,
        reason=candidate.reason,
    )


def build_write_plans(
    candidates: Iterable[MemoryCandidateLite],
    *,
    evidence_episode_ids: List[str],
) -> List[MemoryWritePlan]:
    return [build_write_plan(candidate, evidence_episode_ids=evidence_episode_ids) for candidate in candidates]


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

