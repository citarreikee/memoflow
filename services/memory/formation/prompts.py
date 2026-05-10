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



def build_candidate_formation_messages(
    episode: Dict[str, Any],
    observations: List[Dict[str, Any]],
    *,
    max_candidates: int,
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are the memory formation layer for Memoflow. "
        "You receive structured observations from one completed episode and propose memory candidates for later integration. "
        "You do not write database rows, create final IDs, mutate existing memory, build graph edges, or decide final retrieval behavior. "
        "You may include a brief natural-language note, but your response must contain exactly one JSON object or JSON array fragment that the caller can parse. "
        "Prefer an empty candidates list when the observations do not justify durable memory."
    )
    schema = {
        "candidates": [
            {
                "text": "concise memory candidate grounded in observations",
                "source_observation_ids": ["observation id"],
                "type": "preference|profile_fact|project_rule|procedure|decision|task_state|entity_relation|episodic_event|embedding_hint|non_memory",
                "scope": "session|user|project|workspace",
                "action": "ADD|UPDATE|DELETE|NOOP",
                "importance": "number from 0.0 to 1.0",
                "reason": "one short sentence explaining future utility",
                "stability": "temporary|evolving|stable|unknown",
                "memory_layer": "raw|event|state|semantic|insight|relation|file|non_memory",
                "storage_intent": "episode_log|state_kv|semantic_kv|vector_projection|relation_graph|dag|file_memory|review_queue|none",
                "evidence_policy": "required|multi_evidence_preferred|review_required|none",
                "lifecycle_hint": "normal|volatile|reinforce|supersedes|archive_after_task|review_before_apply",
                "risk": "low|medium|high",
            }
        ]
    }
    user_prompt = "\n\n".join(
        [
            f"Emit at most {max_candidates} candidates.",
            "Required JSON fragment shape. It may be surrounded by a short note, but the JSON fragment itself must be valid:",
            json.dumps(schema, ensure_ascii=False),
            "Formation rules:",
            "- Form candidates from observations, not from unsupported inference.",
            "- Do not preserve raw chatter unless it has future task value.",
            "- Temporary one-off style instructions should usually be omitted or NOOP.",
            "- Use task_state/state_kv for active project state and open loops.",
            "- Use decision/event/episode_log for project decisions and roadmap turns.",
            "- Use semantic/semantic_kv for durable user preferences and stable facts.",
            "- Use file/file_memory for project rules or procedures that should become human-readable guidance.",
            "- Use relation/relation_graph only for explicit predicates such as depends_on, blocks, supersedes, contradicts, caused_by, derived_from, or part_of.",
            "- Use review_queue or risk=high when valuable but ambiguous, conflicting, or potentially sensitive.",
            "Episode evidence:",
            _render_episode(episode),
            "Structured observations:",
            json.dumps({"observations": observations}, ensure_ascii=False, indent=2),
        ]
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]



def build_integration_messages(
    candidate: Dict[str, Any],
    related_existing_memories: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are the semantic memory integration judge for Memoflow. "
        "You decide how one candidate memory should enter long-term memory compared with already related memories. "
        "Do not write databases, create IDs, reference hidden identifiers, assign confidence scores, or design storage tables. "
        "Only choose a memory layer set, a write strategy, related existing memory indices, and a short reason. "
        "Your response may include a brief note, but it must contain exactly one JSON object fragment that the caller can parse."
    )
    schema = {
        "memory_content": "final concise memory content, or empty string for do_not_write",
        "memory_layers": ["raw|event|state|semantic|insight|relation|file"],
        "write_strategy": "add_new|do_not_write|merge_with_existing|update_existing|supersede_existing|link_as_relation|mark_conflict|needs_review",
        "related_existing_indices": [0],
        "reason": "one short sentence",
        "relation_type": "depends_on|blocks|supersedes|contradicts|derived_from|part_of|caused_by|null",
    }
    user_prompt = "\n\n".join(
        [
            "Decide the clean semantic write strategy for this candidate memory.",
            "Required JSON fragment shape:",
            json.dumps(schema, ensure_ascii=False),
            "Rules:",
            "- Use only array indices from related_existing_memories; never invent database IDs.",
            "- Do not output confidence scores. Choose the strategy directly.",
            "- Choose multiple memory_layers only when the memory genuinely spans layers, such as a decision that is both event and semantic guidance.",
            "- Use merge_with_existing when the candidate and an existing memory express the same durable fact with compatible wording.",
            "- Use update_existing when the candidate revises a current state or mutable fact without invalidating history.",
            "- Use supersede_existing when the candidate clearly replaces an older preference, rule, or decision.",
            "- Use mark_conflict when candidate and existing memory disagree but the winner is not safely clear.",
            "- Use link_as_relation only for explicit relations such as depends_on, blocks, supersedes, contradicts, derived_from, part_of, or caused_by.",
            "- Use needs_review for valuable but ambiguous, risky, under-evidenced, or over-broad memory.",
            "- Use do_not_write when the candidate is chatter, temporary one-off instruction, or already fully covered.",
            "Candidate memory:",
            json.dumps(candidate, ensure_ascii=False, indent=2),
            "Related existing memories:",
            json.dumps({"related_existing_memories": related_existing_memories}, ensure_ascii=False, indent=2),
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

