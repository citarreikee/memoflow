from __future__ import annotations

import json
from typing import Any, Dict, List


def build_extraction_messages(episode: Dict[str, Any], *, max_candidates: int) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a memory formation sidecar for Memoflow. "
        "Return only strict JSON. Do not use markdown. "
        "Extract lightweight memory candidates only; do not plan storage, IDs, graph edges, TTL, or database tables. "
        "Prefer NOOP when unsure. Do not infer hidden preferences from one ambiguous turn. "
        "Avoid sensitive personal data unless explicitly necessary for task continuity."
    )
    schema = {
        "candidates": [
            {
                "text": "concise memory statement",
                "type": "preference|profile_fact|project_rule|procedure|decision|task_state|entity_relation|episodic_event|embedding_hint|non_memory",
                "scope": "session|user|project|workspace",
                "action": "ADD|UPDATE|DELETE|NOOP",
                "importance": "number from 0.0 to 1.0",
                "reason": "one short sentence",
                "stability": "temporary|evolving|stable|unknown",
            }
        ]
    }
    user_prompt = "\n\n".join(
        [
            f"Emit at most {max_candidates} candidates.",
            "Required JSON shape:",
            json.dumps(schema, ensure_ascii=False),
            "Episode evidence:",
            _render_episode(episode),
        ]
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def _render_episode(episode: Dict[str, Any]) -> str:
    lines = [
        f"episode_id: {episode.get('episode_id', '')}",
        f"turn_index: {episode.get('turn_index', '')}",
        f"source: {episode.get('source', '')}",
    ]
    for message in episode.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "unknown")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content.strip()}")
    return "\n".join(lines)

