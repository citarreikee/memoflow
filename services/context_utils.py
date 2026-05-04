from __future__ import annotations

import json
from typing import Any, Dict, List

from config import settings


def estimate_text_tokens(value: Any) -> int:
    text = "" if value is None else str(value)
    return 0 if not text else max(1, (len(text) + 3) // 4)


def estimate_message_tokens(message: Dict[str, Any]) -> int:
    token_cost = 4 + estimate_text_tokens(message.get("role"))
    for key in ("content", "name", "tool_call_id", "reasoning_content"):
        token_cost += estimate_text_tokens(message.get(key))
    if message.get("tool_calls"):
        token_cost += estimate_text_tokens(json.dumps(message["tool_calls"], ensure_ascii=False))
    return token_cost


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def build_pinned_prompt_messages() -> List[Dict[str, Any]]:
    pinned: List[Dict[str, Any]] = []
    if settings.SYSTEM_PROMPT:
        pinned.append({"role": "system", "content": settings.SYSTEM_PROMPT})
    if settings.DEVELOPER_PROMPT:
        pinned.append({"role": "developer", "content": settings.DEVELOPER_PROMPT})
    return pinned


def history_message_to_dict(message: Any) -> Dict[str, Any]:
    if hasattr(message, "to_provider_message"):
        return message.to_provider_message()
    return {"role": getattr(message, "role", ""), "content": getattr(message, "content", None)}


def group_messages_by_user_turn(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    turns: List[List[Dict[str, Any]]] = []
    current_turn: List[Dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "user":
            if current_turn:
                turns.append(current_turn)
            current_turn = [message]
        else:
            current_turn.append(message)
    if current_turn:
        turns.append(current_turn)
    return turns


def flatten_turns(turns: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []
    for turn in turns:
        flat.extend(turn)
    return flat


def trim_tool_message_content(messages: List[Dict[str, Any]], max_chars: int) -> List[Dict[str, Any]]:
    if max_chars <= 0:
        return messages
    trimmed = []
    for message in messages:
        cloned = dict(message)
        if cloned.get("role") == "tool":
            content = cloned.get("content")
            if isinstance(content, str) and len(content) > max_chars:
                cloned["content"] = content[:max_chars] + "...[truncated]"
        trimmed.append(cloned)
    return trimmed
