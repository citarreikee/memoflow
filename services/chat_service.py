from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from config import settings


@dataclass
class ChatTurnContext:
    provider: str
    session_id: str
    session_key: str
    user_message: str
    input_source: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    token_budget: int = 0
    estimated_tokens: int = 0


def get_provider_for_model(model_name: str) -> str:
    if model_name.startswith("deepseek"):
        return "deepseek"
    if model_name.startswith("kimi") or model_name.startswith("moonshot"):
        return "kimi"
    return "ollama"


def create_session_payload(session_manager: Any, model: str, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    session = session_manager.create_session(model=model, metadata=metadata)
    return {"session_id": session.session_id, "model": session.model, "created_at": session.created_at}


def list_sessions_payload(session_manager: Any) -> Dict[str, Any]:
    return {"sessions": session_manager.list_sessions()}


def get_session_payload(session_manager: Any, session_id: str) -> Dict[str, Any]:
    session = session_manager.get_session(session_id)
    if not session:
        raise KeyError("Session not found")
    return session.to_dict()


def delete_session_payload(session_manager: Any, session_id: str) -> Dict[str, Any]:
    if not session_manager.delete_session(session_id):
        raise KeyError("Session not found")
    return {"message": "Session deleted"}


def prepare_chat_turn(
    session_manager: Any,
    *,
    model: str,
    user_message: str,
    request_session_id: Optional[str],
    request_session_key: Optional[str] = None,
    input_source: Optional[str] = None,
) -> ChatTurnContext:
    provider = get_provider_for_model(model)
    session_key = (request_session_key or "main").strip() or "main"
    source = (input_source or "web").strip() or "web"

    session = None
    if request_session_id:
        session = session_manager.get_session(request_session_id)
        if not session:
            raise KeyError("Session not found")
    if not session:
        mapped_session_id = session_manager.get_session_id_by_key(session_key)
        if mapped_session_id:
            session = session_manager.get_session(mapped_session_id)
    if not session:
        session = session_manager.create_session(
            model=model,
            metadata={
                "provider": provider,
                "session_key": session_key,
                "input_source": source,
            },
        )

    if session.model != model:
        session.model = model
    session.metadata["provider"] = provider
    session.metadata["session_key"] = session_key
    session.metadata["input_source"] = source
    session_manager.bind_session_key(session_key, session.session_id)
    session_manager.add_message(session_id=session.session_id, role="user", content=user_message)

    history_messages = session_manager.get_history(session_id=session.session_id, limit=None) or []
    messages, token_budget, estimated_tokens = _build_context_messages(history_messages, provider=provider)
    return ChatTurnContext(
        provider=provider,
        session_id=session.session_id,
        session_key=session_key,
        user_message=user_message,
        input_source=source,
        messages=messages,
        token_budget=token_budget,
        estimated_tokens=estimated_tokens,
    )


def _parse_sse_data_event(event: str) -> Optional[Dict[str, Any]]:
    if not event.startswith("data: "):
        return None
    try:
        payload = json.loads(event[6:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _estimate_text_tokens(value: Any) -> int:
    text = "" if value is None else str(value)
    return 0 if not text else max(1, (len(text) + 3) // 4)


def _estimate_message_tokens(message: Dict[str, Any]) -> int:
    token_cost = 4 + _estimate_text_tokens(message.get("role"))
    for key in ("content", "name", "tool_call_id", "reasoning_content"):
        token_cost += _estimate_text_tokens(message.get(key))
    if message.get("tool_calls"):
        token_cost += _estimate_text_tokens(json.dumps(message["tool_calls"], ensure_ascii=False))
    return token_cost


def _estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(_estimate_message_tokens(message) for message in messages)


def _context_window_for_provider(provider: str) -> int:
    if provider == "deepseek":
        return settings.DEEPSEEK_CONTEXT_WINDOW
    if provider == "kimi":
        return settings.KIMI_CONTEXT_WINDOW
    return settings.OLLAMA_CONTEXT_WINDOW


def _resolve_token_budget(provider: str) -> int:
    window = max(2048, _context_window_for_provider(provider))
    if settings.CONTEXT_TOKEN_BUDGET > 0:
        return min(window, settings.CONTEXT_TOKEN_BUDGET)
    ratio = settings.CONTEXT_BUDGET_RATIO if settings.CONTEXT_BUDGET_RATIO > 0 else 0.75
    return max(1024, int(window * ratio))


def _build_pinned_prompt_messages() -> List[Dict[str, Any]]:
    pinned: List[Dict[str, Any]] = []
    if settings.SYSTEM_PROMPT:
        pinned.append({"role": "system", "content": settings.SYSTEM_PROMPT})
    if settings.DEVELOPER_PROMPT:
        pinned.append({"role": "developer", "content": settings.DEVELOPER_PROMPT})
    return pinned


def _history_message_to_dict(message: Any) -> Dict[str, Any]:
    if hasattr(message, "to_provider_message"):
        return message.to_provider_message()
    return {"role": getattr(message, "role", ""), "content": getattr(message, "content", None)}


def _group_messages_by_user_turn(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
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


def _flatten_turns(turns: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []
    for turn in turns:
        flat.extend(turn)
    return flat


def _trim_tool_message_content(messages: List[Dict[str, Any]], max_chars: int) -> List[Dict[str, Any]]:
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


def _build_context_messages(history_messages: List[Any], *, provider: str) -> tuple[List[Dict[str, Any]], int, int]:
    pinned = _build_pinned_prompt_messages()
    history = [_history_message_to_dict(message) for message in history_messages]
    history = _trim_tool_message_content(history, settings.CONTEXT_TOOL_RESULT_MAX_CHARS)
    token_budget = _resolve_token_budget(provider)

    context_history = history
    if _estimate_messages_tokens(pinned + context_history) > token_budget:
        turns = _group_messages_by_user_turn(context_history)
        context_history = _flatten_turns(turns[-max(1, settings.CONTEXT_MAX_USER_TURNS) :])

    while _estimate_messages_tokens(pinned + context_history) > token_budget and context_history:
        turns = _group_messages_by_user_turn(context_history)
        if len(turns) <= 1:
            context_history = context_history[1:]
        else:
            context_history = _flatten_turns(turns[1:])

    estimated_tokens = _estimate_messages_tokens(pinned + context_history)
    return pinned + context_history, token_budget, estimated_tokens


async def stream_chat_with_session(
    session_manager: Any,
    react_stream: Callable[..., AsyncIterator[str]],
    *,
    model: str,
    context: ChatTurnContext,
    force_tool_use: bool,
    enable_tools: bool,
) -> AsyncIterator[str]:
    done_data_pending: Optional[Dict[str, str]] = None
    saved_from_react = False
    seq = 0

    def _to_sse(payload: Dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        payload_with_seq = dict(payload)
        payload_with_seq["seq"] = seq
        return f"data: {json.dumps(payload_with_seq, ensure_ascii=False)}\n\n"

    async for event in react_stream(
        model=model,
        messages=context.messages,
        provider=context.provider,
        enable_tools=enable_tools,
        force_tool_use=force_tool_use,
    ):
        data = _parse_sse_data_event(event)
        if not data:
            yield event
            continue
        yield _to_sse(data)

        event_type = data.get("type")
        if event_type == "done":
            done_data_pending = {
                "answer": data.get("answer") or data.get("content") or "",
                "thinking": data.get("thinking", ""),
            }
            continue
        if event_type != "react_complete":
            continue

        react_messages = data.get("messages", [])
        for msg in react_messages:
            session_manager.add_message(
                session_id=context.session_id,
                role=msg.get("role", "assistant"),
                content=msg.get("content"),
                thinking=msg.get("thinking"),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                name=msg.get("name"),
                reasoning_content=msg.get("reasoning_content"),
                metadata={"provider": context.provider, "session_key": context.session_key},
            )
        if react_messages:
            saved_from_react = True
            done_data_pending = None

    if done_data_pending and not saved_from_react:
        session_manager.add_message(
            session_id=context.session_id,
            role="assistant",
            content=done_data_pending["answer"],
            thinking=done_data_pending["thinking"],
            metadata={"provider": context.provider, "session_key": context.session_key},
        )

    yield _to_sse(
        {
            "type": "session_id",
            "session_id": context.session_id,
            "session_key": context.session_key,
            "token_budget": context.token_budget,
            "estimated_tokens": context.estimated_tokens,
        }
    )
