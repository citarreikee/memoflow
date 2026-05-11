from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


def build_retrieval_usage_audit(
    *,
    retrieval_pack: Optional[Dict[str, Any]],
    assistant_response: str,
) -> Dict[str, Any]:
    items = retrieval_pack.get("items") if isinstance(retrieval_pack, dict) else []
    if not isinstance(items, list) or not items:
        return {
            "items_retrieved": 0,
            "items_cited_by_model": 0,
            "items_ignored": 0,
            "usage_rate": 0.0,
            "items": [],
        }

    response_terms = set(_terms(assistant_response))
    audited_items: List[Dict[str, Any]] = []
    cited_count = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        item_terms = _terms(text)
        matched_terms = sorted(set(item_terms) & response_terms)
        cited = _is_cited(text=text, item_terms=item_terms, matched_terms=matched_terms, response=assistant_response)
        if cited:
            cited_count += 1
        audited_items.append(
            {
                "index": index,
                "memory_id": item.get("memory_id"),
                "source": item.get("source"),
                "authority": item.get("authority"),
                "intent": item.get("intent"),
                "conflict": bool(item.get("conflict")),
                "cited": cited,
                "matched_terms": matched_terms[:8],
            }
        )

    total = len(audited_items)
    ignored = max(0, total - cited_count)
    return {
        "items_retrieved": total,
        "items_cited_by_model": cited_count,
        "items_ignored": ignored,
        "usage_rate": round(cited_count / total, 4) if total else 0.0,
        "items": audited_items,
        "method": "deterministic_keyword_overlap_v1",
    }


def _is_cited(*, text: str, item_terms: List[str], matched_terms: List[str], response: str) -> bool:
    normalized_text = _normalize(text)
    normalized_response = _normalize(response)
    if normalized_text and len(normalized_text) >= 24 and normalized_text in normalized_response:
        return True
    if not item_terms:
        return False
    required = 1 if len(item_terms) <= 3 else 2
    coverage = len(matched_terms) / max(1, len(set(item_terms)))
    return len(matched_terms) >= required and coverage >= 0.25


def _terms(text: str) -> List[str]:
    raw_terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text or "")
    stop = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "about",
        "from",
        "into",
        "user",
        "decision",
        "memory",
        "这个",
        "那个",
        "我们",
        "已经",
    }
    return _dedupe(term.lower() if term.isascii() else term for term in raw_terms if len(term) >= 2 and term.lower() not in stop)


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())
