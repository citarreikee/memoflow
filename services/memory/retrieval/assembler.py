from __future__ import annotations

from typing import Dict, Optional

from services.memory.retrieval.schemas import RetrievalPack


def render_retrieval_message(pack: RetrievalPack) -> Optional[Dict[str, str]]:
    if not pack.items:
        return None
    lines = ["Relevant memory package for this turn:"]
    for label, items in _sections(pack):
        if not items:
            continue
        lines.append(f"{label}:")
        for item in items:
            conflict = " conflict=true" if item.conflict else ""
            intent = f" intent={item.intent}" if item.intent else ""
            evidence = f" evidence={len(item.evidence_episode_ids)}" if item.evidence_episode_ids else ""
            lines.append(
                f"- [{item.memory_type}/{item.scope} source={item.source} authority={item.authority}"
                f" score={item.score:.2f}{intent}{conflict}{evidence}] {item.text}"
            )
    return {"role": "system", "content": "\n".join(lines)}


def _sections(pack: RetrievalPack):
    authoritative = [item for item in pack.items if item.authority == "authoritative" and not item.conflict]
    projections = [item for item in pack.items if item.authority == "projection" and not item.conflict]
    review_or_conflict = [item for item in pack.items if item.authority == "review_only" or item.conflict]
    return [
        ("Authoritative memories", authoritative),
        ("Projection memories", projections),
        ("Review-only or potential conflicts", review_or_conflict),
    ]
