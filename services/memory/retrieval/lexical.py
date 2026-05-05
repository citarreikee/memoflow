from __future__ import annotations

import re
from typing import Iterable, Set


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> Set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text or "") if token.strip()}


def lexical_score(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    text_tokens = tokenize(text)
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    if not overlap:
        return 0.0
    precision = len(overlap) / max(1, len(query_tokens))
    coverage = len(overlap) / max(1, len(text_tokens))
    return min(1.0, 0.75 * precision + 0.25 * coverage)


def contains_any(text: str, terms: Iterable[str]) -> bool:
    haystack = (text or "").lower()
    return any(term.lower() in haystack for term in terms if term)

