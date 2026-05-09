from __future__ import annotations

import json
from typing import Any, Dict, List


def build_extraction_messages(episode: Dict[str, Any], *, max_candidates: int) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a memory formation sidecar for Memoflow. "
        "Your job is to extract lightweight memory candidates from one completed episode. "
        "You may include a brief natural-language note, but your response must contain exactly one JSON object or JSON array fragment that the caller can parse. "
        "Do not plan storage, IDs, graph edges, TTL, database tables, retrieval, or prompt injection. "
        "Prefer NOOP or an empty candidates list when unsure. Do not infer hidden preferences from one ambiguous turn. "
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
            "Required JSON fragment shape. It may be surrounded by a short note, but the JSON fragment itself must be valid:",
            json.dumps(schema, ensure_ascii=False),
            "Quality rules:",
            "- Emit at most one candidate for the same durable fact.",
            "- Candidate text must be specific, short, and evidence-backed by this episode.",
            "- Use non_memory/NOOP or [] for greetings, vague chatter, or unsupported inferences.",
            "- Use entity_relation only for explicit predicates such as depends_on, blocks, supersedes, contradicts, or derived_from.",
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

