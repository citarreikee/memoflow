from __future__ import annotations

from typing import Dict, Optional

from services.memory.retrieval.schemas import RetrievalPack


def render_retrieval_message(pack: RetrievalPack) -> Optional[Dict[str, str]]:
    if not pack.items:
        return None
    lines = ["Relevant memory retrieved for this turn:"]
    for item in pack.items:
        lines.append(
            f"- [{item.memory_type}/{item.scope} score={item.score:.2f}] {item.text}"
        )
    return {"role": "system", "content": "\n".join(lines)}

