from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config import settings
from providers import deepseek, kimi, ollama
from services.memory.retrieval.planner import INTENT_SOURCE_STRATEGIES, RuleRetrievalIntentPlanner
from services.memory.retrieval.query_reconstructor import RuleQueryReconstructor
from services.memory.retrieval.schemas import ReconstructedQuery, RetrievalCandidate, RetrievalPlan
from services.memory.retrieval.sufficiency import RuleSufficiencyEvaluator, SufficiencyDecision


ALLOWED_INTENTS = set(INTENT_SOURCE_STRATEGIES.keys())


async def reconstruct_queries_with_llm(
    *,
    user_message: str,
    recent_context_text: str = "",
) -> tuple[List[ReconstructedQuery], Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    messages = _query_reconstruction_messages(user_message=user_message, recent_context_text=recent_context_text)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = _parse_json_payload(raw_text)
        queries = _normalize_reconstructed_queries(payload)
        if not queries:
            raise ValueError("llm_query_reconstruction_empty")
        return queries[:3], _debug(provider=provider, model=model, raw_text=raw_text, mode="llm_query_reconstruction")
    except Exception as exc:
        fallback = RuleQueryReconstructor().reconstruct(user_message=user_message, recent_turns=[{"content": recent_context_text}])
        return fallback, {"mode": "rule_fallback", "provider": provider, "model": model, "error": str(exc)}


async def plan_retrieval_with_llm(
    *,
    user_message: str,
    reconstructed_queries: List[ReconstructedQuery],
    session_key: str,
    token_budget: int,
    enabled: bool,
    recent_context_text: str = "",
) -> tuple[RetrievalPlan, Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    messages = _intent_planning_messages(user_message=user_message, reconstructed_queries=reconstructed_queries)
    rule_planner = RuleRetrievalIntentPlanner()
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = _parse_json_payload(raw_text)
        planned_queries = _queries_with_llm_intents(reconstructed_queries, payload)
        plan = rule_planner.build_plan(
            user_message=user_message,
            reconstructed_queries=planned_queries,
            session_key=session_key,
            token_budget=token_budget,
            enabled=enabled,
            recent_context_text=recent_context_text,
        )
        return plan, _debug(provider=provider, model=model, raw_text=raw_text, mode="llm_intent_planning")
    except Exception as exc:
        plan = rule_planner.build_plan(
            user_message=user_message,
            reconstructed_queries=reconstructed_queries,
            session_key=session_key,
            token_budget=token_budget,
            enabled=enabled,
            recent_context_text=recent_context_text,
        )
        return plan, {"mode": "rule_fallback", "provider": provider, "model": model, "error": str(exc)}


async def evaluate_sufficiency_with_llm(
    *,
    plan: RetrievalPlan,
    candidates: List[RetrievalCandidate],
) -> tuple[SufficiencyDecision, Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    messages = _sufficiency_messages(plan=plan, candidates=candidates)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = _parse_json_payload(raw_text)
        decision = _normalize_sufficiency_decision(payload, fallback=RuleSufficiencyEvaluator().evaluate(plan=plan, candidates=candidates))
        return decision, _debug(provider=provider, model=model, raw_text=raw_text, mode="llm_sufficiency")
    except Exception as exc:
        return RuleSufficiencyEvaluator().evaluate(plan=plan, candidates=candidates), {
            "mode": "rule_fallback",
            "provider": provider,
            "model": model,
            "error": str(exc),
        }


def _query_reconstruction_messages(*, user_message: str, recent_context_text: str) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a memory retrieval query reconstructor. Rewrite vague user language into executable memory search queries. "
                "Return plain text containing one JSON object only. Only rewrite the query; code will handle retrieval routing. "
                "Schema: {\"queries\":[{\"query\":str,\"reason\":str}]} ."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"user_message": user_message, "recent_context_excerpt": recent_context_text[-3000:]},
                ensure_ascii=False,
            ),
        },
    ]


def _intent_planning_messages(*, user_message: str, reconstructed_queries: List[ReconstructedQuery]) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a memory retrieval intent planner. Choose semantic retrieval intents only. "
                "Return plain text containing one JSON object only. Do not output retrieval sources; code maps intents to stores. "
                "Schema: {\"intents\":[str],\"sufficiency_threshold\":\"low|medium|high\",\"reason\":str}. "
                f"Allowed intents: {sorted(ALLOWED_INTENTS)}."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_message": user_message,
                    "reconstructed_queries": _semantic_query_payload(reconstructed_queries),
                },
                ensure_ascii=False,
            ),
        },
    ]


def _sufficiency_messages(*, plan: RetrievalPlan, candidates: List[RetrievalCandidate]) -> List[Dict[str, str]]:
    candidate_payload = [
        {
            "index": index,
            "authority": candidate.authority,
            "memory_type": candidate.memory_type,
            "text": candidate.text[:500],
            "conflict": candidate.conflict,
        }
        for index, candidate in enumerate(candidates[:10])
    ]
    return [
        {
            "role": "system",
            "content": (
                "You are a memory retrieval sufficiency judge. Decide whether the candidate memories are enough for the current turn. "
                "Return plain text containing one JSON object only. Only judge semantic sufficiency; code will choose any fallback sources. "
                "Schema: {\"sufficient\":bool,\"reason\":str}."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "intent": _semantic_intent_payload(plan),
                    "candidates": candidate_payload,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _normalize_reconstructed_queries(payload: Any) -> List[ReconstructedQuery]:
    items = payload.get("queries") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    queries: List[ReconstructedQuery] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        queries.append(ReconstructedQuery(query=query, reason=str(item.get("reason") or "")))
    return queries


def _queries_with_llm_intents(queries: List[ReconstructedQuery], payload: Any) -> List[ReconstructedQuery]:
    intents = _allowed_list(payload.get("intents") if isinstance(payload, dict) else None, ALLOWED_INTENTS)
    if not intents:
        return queries
    updated: List[ReconstructedQuery] = []
    for query in queries:
        merged_intents = _dedupe([*query.target_intents, *intents])
        updated.append(
            ReconstructedQuery(
                query=query.query,
                target_intents=merged_intents,
                target_source_hints=query.target_source_hints,
                reason=query.reason,
            )
        )
    return updated


def _normalize_sufficiency_decision(payload: Any, *, fallback: SufficiencyDecision) -> SufficiencyDecision:
    if not isinstance(payload, dict):
        return fallback
    sufficient = bool(payload.get("sufficient", fallback.sufficient))
    return SufficiencyDecision(
        sufficient=sufficient,
        level=fallback.level,
        reason=str(payload.get("reason") or fallback.reason),
        suggested_paths=[] if sufficient else fallback.suggested_paths,
    )


def _semantic_query_payload(queries: List[ReconstructedQuery]) -> List[Dict[str, str]]:
    return [{"query": query.query, "reason": query.reason} for query in queries]


def _semantic_intent_payload(plan: RetrievalPlan) -> Dict[str, Any]:
    return {
        "query": plan.intent.query,
        "intents": plan.intent.intents,
        "kind": plan.intent.kind,
        "sufficiency_threshold": plan.intent.sufficiency_threshold,
    }


def _allowed_list(value: Any, allowed: set[str]) -> List[str]:
    raw = value if isinstance(value, list) else [value]
    result: List[str] = []
    for item in raw:
        normalized = str(item or "").strip()
        if normalized in allowed and normalized not in result:
            result.append(normalized)
    return result


async def _run_completion(*, provider: str, model: str, messages: List[Dict[str, str]]) -> str:
    if provider == "ollama":
        return await ollama.generate_ollama_completion(
            model=model,
            messages=messages,
            timeout=settings.MEMORY_RETRIEVAL_TIMEOUT_SECONDS,
            num_predict=800,
            think=False,
        )
    if provider == "deepseek":
        return await deepseek.generate_deepseek_completion(
            model=model,
            messages=messages,
            timeout=settings.MEMORY_RETRIEVAL_TIMEOUT_SECONDS,
        )
    if provider == "kimi":
        return await kimi.generate_kimi_completion(
            model=model,
            messages=messages,
            timeout=settings.MEMORY_RETRIEVAL_TIMEOUT_SECONDS,
        )
    raise ValueError(f"unsupported_memory_retrieval_provider:{provider}")


def _resolve_provider() -> str:
    if settings.MEMORY_RETRIEVAL_PROVIDER:
        return settings.MEMORY_RETRIEVAL_PROVIDER
    if settings.MEMORY_FORMATION_PROVIDER:
        return settings.MEMORY_FORMATION_PROVIDER
    if settings.SIDECAR_COMPACTION_PROVIDER:
        return settings.SIDECAR_COMPACTION_PROVIDER.lower()
    return "deepseek"


def _resolve_model(provider: str) -> str:
    if settings.MEMORY_RETRIEVAL_MODEL:
        return settings.MEMORY_RETRIEVAL_MODEL
    if settings.MEMORY_FORMATION_MODEL:
        return settings.MEMORY_FORMATION_MODEL
    if provider == "deepseek":
        return settings.SIDECAR_COMPACTION_MODEL or "deepseek-v4-flash"
    if provider == "kimi":
        return settings.KIMI_MODELS.split(",")[0].strip() if settings.KIMI_MODELS else "kimi-k2.5"
    if provider == "ollama":
        return settings.SIDECAR_COMPACTION_MODEL or "qwen3:30b-a3b"
    return settings.SIDECAR_COMPACTION_MODEL


def _parse_json_payload(raw_text: str) -> Any:
    text = (raw_text or "").strip().lstrip("\ufeff")
    if not text:
        raise ValueError("empty_retrieval_llm_response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        first_valid_payload: Any = None
        last_error: Exception = first_error
        for fragment in _extract_json_fragments(text):
            try:
                payload = json.loads(fragment)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if first_valid_payload is None:
                first_valid_payload = payload
            if isinstance(payload, (dict, list)):
                return payload
        if first_valid_payload is not None:
            return first_valid_payload
        raise last_error


def _extract_json_fragments(text: str) -> List[str]:
    fragments: List[str] = []
    for start, char in enumerate(text):
        if char not in "{[":
            continue
        fragment = _balanced_json_fragment(text, start)
        if fragment is not None:
            fragments.append(fragment)
    return fragments


def _balanced_json_fragment(text: str, start: int) -> Optional[str]:
    opening = text[start]
    if opening not in "{[":
        return None
    stack: List[str] = ["}" if opening == "{" else "]"]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "{[":
            stack.append("}" if char == "{" else "]")
            continue
        if char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return text[start : index + 1]
    return None


def _debug(*, provider: str, model: str, raw_text: str, mode: str) -> Dict[str, Any]:
    return {"mode": mode, "provider": provider, "model": model, "raw_preview": raw_text[:1200]}


def _dedupe(values: List[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
