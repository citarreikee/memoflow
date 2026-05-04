from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.context_utils import (
    build_pinned_prompt_messages,
    estimate_messages_tokens,
    flatten_turns,
    group_messages_by_user_turn,
    history_message_to_dict,
    trim_tool_message_content,
)
from services.memory.compaction import build_checkpoint_message
from services.memory.file_memory import load_file_memories


@dataclass
class WorkingSet:
    messages: List[Dict[str, Any]]
    estimated_tokens: int
    recent_turn_count: int
    file_memories: List[Dict[str, str]] = field(default_factory=list)


def assemble_working_set(
    *,
    history_messages: List[Any],
    token_budget: int,
    trim_tool_result_chars: int,
    existing_checkpoint: Optional[Dict[str, Any]],
    workspace_dir: str,
    user_message: str,
    load_file_memory: bool,
) -> WorkingSet:
    """Assemble prompt messages from pinned prompts, checkpoint, files, and recent raw turns.

    This function is intentionally deterministic. It does not decide whether to
    compact, retrieve, or persist memory; `runtime.py` owns those policy choices.
    """

    pinned = build_pinned_prompt_messages()
    history = [history_message_to_dict(message) for message in history_messages]
    history = trim_tool_message_content(history, trim_tool_result_chars)
    turns = group_messages_by_user_turn(history)

    checkpoint_message = None
    if existing_checkpoint and existing_checkpoint.get("summary"):
        checkpoint_message = build_checkpoint_message(existing_checkpoint["summary"])
        covered_turns = min(len(turns), len(existing_checkpoint.get("covers_episode_ids") or []))
        if covered_turns > 0:
            turns = turns[covered_turns:]
            history = flatten_turns(turns)

    file_memories = load_file_memories(workspace_dir, user_message) if load_file_memory else []
    file_memory_messages = [
        {
            "role": "system",
            "content": f"Project memory from {item['path']}:\n{item['content']}",
        }
        for item in file_memories
    ]

    messages = _compose_messages(pinned, checkpoint_message, file_memory_messages, history)
    while estimate_messages_tokens(messages) > token_budget and len(turns) > 1:
        turns = turns[1:]
        history = flatten_turns(turns)
        messages = _compose_messages(pinned, checkpoint_message, file_memory_messages, history)

    if estimate_messages_tokens(messages) > token_budget and file_memory_messages:
        file_memory_messages = []
        messages = _compose_messages(pinned, checkpoint_message, file_memory_messages, history)

    return WorkingSet(
        messages=messages,
        estimated_tokens=estimate_messages_tokens(messages),
        recent_turn_count=len(turns),
        file_memories=file_memories if file_memory_messages else [],
    )


def _compose_messages(
    pinned: List[Dict[str, Any]],
    checkpoint_message: Optional[Dict[str, Any]],
    file_memory_messages: List[Dict[str, Any]],
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    messages = list(pinned)
    if checkpoint_message:
        messages.append(checkpoint_message)
    messages.extend(file_memory_messages)
    messages.extend(history)
    return messages

