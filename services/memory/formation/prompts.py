from __future__ import annotations

import json
from typing import Any, Dict, List


def build_extraction_messages(episode: Dict[str, Any], *, max_candidates: int) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a memory formation sidecar for Memoflow. "
        "Your job is to extract lightweight memory candidates from one completed episode. "
        "You may include a brief natural-language note, but your response must contain exactly one JSON object or JSON array fragment that the caller can parse. "
        "Classify the memory layer and storage intent, but do not write storage, generate IDs, create graph edges, TTLs, database rows, retrieval plans, or prompt injection. "
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
                "memory_layer": "raw|event|state|semantic|insight|relation|file|non_memory",
                "storage_intent": "episode_log|state_kv|semantic_kv|vector_projection|relation_graph|dag|file_memory|review_queue|none",
                "evidence_policy": "required|multi_evidence_preferred|review_required|none",
                "lifecycle_hint": "normal|volatile|reinforce|supersedes|archive_after_task|review_before_apply",
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
            "- memory_layer means what kind of memory this is; storage_intent is only a routing hint for later deterministic planners.",
            "- Prefer state/state_kv for active task status, event/episode_log for decisions, semantic/semantic_kv for durable facts or preferences, relation/relation_graph for explicit predicates, and file/file_memory for project rules/procedures that should become file-backed guidance.",
            "Episode evidence:",
            _render_episode(episode),
        ]
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]



def build_observation_messages(episode: Dict[str, Any], *, max_observations: int) -> List[Dict[str, str]]:
    system_prompt = (
        "You are the observation layer for Memoflow. "
        "Your job is not to write long-term memory. Your job is to notice what happened in one completed episode, "
        "using compact structured observations that downstream memory formation can inspect. "
        "You may include a brief natural-language note, but your response must contain exactly one JSON object or JSON array fragment that the caller can parse. "
        "Prefer non_memory_signal or an empty observations list for greetings, acknowledgements, or unsupported inference."
    )
    schema = {
        "observations": [
            {
                "text": "specific thing noticed in the episode",
                "type": "user_preference_signal|project_rule_signal|decision_signal|task_state_signal|open_loop_signal|relation_signal|artifact_signal|procedure_signal|insight_signal|non_memory_signal",
                "scope_hint": "session|user|project|workspace",
                "evidence_message_refs": ["message index or role label"],
                "confidence": "number from 0.0 to 1.0",
                "reason": "one short sentence",
                "negative": "boolean; true only when this should suppress memory formation",
            }
        ]
    }
    user_prompt = "\n\n".join(
        [
            f"Emit at most {max_observations} observations.",
            "Required JSON fragment shape. It may be surrounded by a short note, but the JSON fragment itself must be valid:",
            json.dumps(schema, ensure_ascii=False),
            "Observation rules:",
            "- Observations describe what the memory system should notice, not what should be written to storage.",
            "- Do not create memory IDs, graph edges, write plans, storage rows, or retrieval plans.",
            "- Preserve evidence: each observation must be grounded in this episode.",
            "- Use relation_signal only for explicit dependency, conflict, supersession, cause, derivation, block, or part-of statements.",
            "- Use non_memory_signal/negative=true for one-off style instructions, greetings, acknowledgements, vague chatter, or unsafe inferences.",
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

