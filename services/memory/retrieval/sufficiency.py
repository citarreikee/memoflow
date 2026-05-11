from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from services.memory.retrieval.schemas import RetrievalCandidate, RetrievalPlan


@dataclass(frozen=True)
class SufficiencyDecision:
    sufficient: bool
    level: str
    reason: str
    suggested_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SufficiencyEvaluator:
    def evaluate(self, *, plan: RetrievalPlan, candidates: List[RetrievalCandidate]) -> SufficiencyDecision:
        raise NotImplementedError


class RuleSufficiencyEvaluator(SufficiencyEvaluator):
    def evaluate(self, *, plan: RetrievalPlan, candidates: List[RetrievalCandidate]) -> SufficiencyDecision:
        threshold = plan.intent.sufficiency_threshold or "medium"
        included_sources = {candidate.source for candidate in candidates}
        authoritative_count = sum(1 for candidate in candidates if candidate.authority == "authoritative")
        projection_count = sum(1 for candidate in candidates if candidate.authority == "projection")
        conflict_count = sum(1 for candidate in candidates if candidate.conflict or candidate.authority == "review_only")
        high_score_count = sum(1 for candidate in candidates if candidate.score >= 0.35)

        if threshold == "high":
            sufficient = authoritative_count >= 1 and ("graph_one_hop" in included_sources or high_score_count >= 2)
            suggested = ["graph_one_hop", "review_items", "lexical_projection"]
        elif threshold == "medium":
            sufficient = authoritative_count >= 1 or projection_count >= 1 or high_score_count >= 2
            suggested = ["lexical_projection", "graph_one_hop"]
        else:
            sufficient = bool(candidates)
            suggested = ["lexical_projection"]

        if sufficient:
            return SufficiencyDecision(
                sufficient=True,
                level=threshold,
                reason=f"threshold_met: authoritative={authoritative_count}, projection={projection_count}, high_score={high_score_count}, conflicts={conflict_count}",
            )
        return SufficiencyDecision(
            sufficient=False,
            level=threshold,
            reason=f"threshold_not_met: authoritative={authoritative_count}, projection={projection_count}, high_score={high_score_count}, conflicts={conflict_count}",
            suggested_paths=[path for path in suggested if path not in plan.paths],
        )


class LLMSufficiencyEvaluator(SufficiencyEvaluator):
    """Future sidecar slot: LLM judges whether retrieved memory is enough and how to refine."""

    def evaluate(self, *, plan: RetrievalPlan, candidates: List[RetrievalCandidate]) -> SufficiencyDecision:
        return RuleSufficiencyEvaluator().evaluate(plan=plan, candidates=candidates)
