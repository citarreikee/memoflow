from __future__ import annotations

import uuid
from typing import Iterable, List, Optional

from services.memory.formation.evaluator import evaluate_candidate
from services.memory.formation.integration_schemas import MemoryIntegrationPlan
from services.memory.formation.schemas import MemoryCandidateLite, MemoryWritePlan
from services.memory.formation.shape_planner import plan_storage_shape


AUTO_APPLY_INTEGRATION_ACTIONS = {"ADD", "MERGE", "UPDATE", "SUPERSEDE", "LINK"}
REVIEW_INTEGRATION_ACTIONS = {"NEEDS_REVIEW", "CONFLICT"}


def build_write_plan(
    candidate: MemoryCandidateLite,
    *,
    evidence_episode_ids: List[str],
    integration_plan: Optional[MemoryIntegrationPlan] = None,
) -> MemoryWritePlan:
    candidate_id = candidate.candidate_id or f"cand_{uuid.uuid4().hex}"
    evaluation = evaluate_candidate(candidate)
    shape = plan_storage_shape(
        candidate,
        write_strategy=integration_plan.write_strategy if integration_plan else None,
        memory_layers=integration_plan.memory_layers if integration_plan else None,
    )
    blocked_reasons = list(evaluation.blocked_reasons)
    blocked_reasons.extend(shape.blocked_reasons)
    needs_review_reasons = list(integration_plan.needs_review_reasons) if integration_plan else []
    if integration_plan:
        blocked_reasons.extend(integration_plan.blocked_reasons)

    status = evaluation.status
    action = candidate.action
    plan_text = candidate.text
    plan_reason = candidate.reason
    if integration_plan:
        action = _write_action_from_integration(integration_plan, fallback=candidate.action)
        plan_text = integration_plan.suggested_text or candidate.text
        plan_reason = integration_plan.rationale or candidate.reason
        status = _status_from_integration(integration_plan, fallback=status)
        if integration_plan.action in REVIEW_INTEGRATION_ACTIONS and not needs_review_reasons:
            needs_review_reasons.append(f"integration_{integration_plan.action.lower()}")

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
        action=action,
        canonical_store=canonical_store,
        projections=projections,
        scope=candidate.scope,
        evidence_episode_ids=evidence_episode_ids,
        confidence=evaluation.confidence,
        status=status,
        blocked_reasons=_dedupe(blocked_reasons),
        type=candidate.type,
        text=plan_text,
        reason=plan_reason,
        integration_action=integration_plan.action if integration_plan else None,
        write_strategy=integration_plan.write_strategy if integration_plan else None,
        target_memory_id=integration_plan.target_memory_id if integration_plan else None,
        related_memory_ids=list(integration_plan.related_memory_ids) if integration_plan else [],
        graph_relations=list(integration_plan.graph_relations) if integration_plan else [],
        memory_layers=list(integration_plan.memory_layers) if integration_plan else [],
        needs_review_reasons=_dedupe(needs_review_reasons),
    )


def build_write_plans(
    candidates: Iterable[MemoryCandidateLite],
    *,
    evidence_episode_ids: List[str],
    integration_plans: Optional[Iterable[MemoryIntegrationPlan]] = None,
) -> List[MemoryWritePlan]:
    integrations_by_candidate = {
        plan.candidate_id: plan
        for plan in (integration_plans or [])
        if plan.candidate_id
    }
    return [
        build_write_plan(
            candidate,
            evidence_episode_ids=evidence_episode_ids,
            integration_plan=integrations_by_candidate.get(candidate.candidate_id or ""),
        )
        for candidate in candidates
    ]


def _write_action_from_integration(integration: MemoryIntegrationPlan, *, fallback: str) -> str:
    if integration.action in AUTO_APPLY_INTEGRATION_ACTIONS:
        return integration.action
    if integration.action in {"NOOP", "NEEDS_REVIEW", "CONFLICT"}:
        return "NOOP" if integration.action == "NOOP" else "ADD"
    return fallback


def _status_from_integration(integration: MemoryIntegrationPlan, *, fallback: str) -> str:
    if integration.action == "NOOP":
        return "noop"
    if integration.action in REVIEW_INTEGRATION_ACTIONS:
        return "needs_review"
    if integration.action in AUTO_APPLY_INTEGRATION_ACTIONS:
        return "planned"
    return fallback


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
