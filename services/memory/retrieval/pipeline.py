from __future__ import annotations

from typing import Optional

from config import settings
from services.memory.retrieval.planner import RuleRetrievalIntentPlanner
from services.memory.retrieval.query_reconstructor import RuleQueryReconstructor
from services.memory.retrieval.ranker import build_retrieval_pack
from services.memory.retrieval.repository import MemoryRetrievalRepository
from services.memory.retrieval.schemas import RetrievalIntent, RetrievalPack
from services.memory.storage.sqlite_store import MemorySQLiteStore


class MemoryRetrievalPipeline:
    def run(
        self,
        *,
        user_message: str,
        session_key: str,
        token_budget: int,
        store: Optional[MemorySQLiteStore] = None,
        recent_context_text: str = "",
    ) -> RetrievalPack:
        enabled = settings.MEMORY_RETRIEVAL_ENABLED and settings.MEMORY_STORAGE_ENABLED
        reconstructed_queries = RuleQueryReconstructor().reconstruct(
            user_message=user_message,
            checkpoint=None,
            recent_turns=None,
        )
        plan = RuleRetrievalIntentPlanner().build_plan(
            user_message=user_message,
            reconstructed_queries=reconstructed_queries,
            session_key=session_key,
            token_budget=token_budget,
            enabled=enabled,
            recent_context_text=recent_context_text,
        )
        if plan.intent.kind == "none":
            return RetrievalPack(
                intent=plan.intent,
                items=[],
                omitted_count=0,
                estimated_chars=0,
                trace=[{"decision": "skipped", "reason": "disabled_or_no_intent"}],
            )
        try:
            repository = MemoryRetrievalRepository(store or MemorySQLiteStore.from_settings())
            candidates = repository.search(plan)
            pack = build_retrieval_pack(plan, candidates)
            return RetrievalPack(
                intent=pack.intent,
                items=pack.items,
                omitted_count=pack.omitted_count,
                estimated_chars=pack.estimated_chars,
                trace=[
                    {
                        "decision": "planned",
                        "paths": plan.paths,
                        "intents": plan.intent.intents,
                        "reconstructed_queries": plan.intent.reconstructed_queries,
                        "source_strategies": [strategy.to_dict() for strategy in plan.source_strategies],
                        "budget_allocation": plan.budget_allocation,
                        "candidate_count": len(candidates),
                    },
                    *pack.trace,
                ],
            )
        except Exception as exc:
            return RetrievalPack(
                intent=RetrievalIntent(kind="none", query=user_message),
                items=[],
                omitted_count=0,
                estimated_chars=0,
                trace=[{"decision": "failed", "reason": str(exc)}],
            )
