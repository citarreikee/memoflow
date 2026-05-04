from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from config import settings
from services.memory.checkpoints import get_latest_checkpoint
from services.memory.runtime import RuntimeInput, memory_runtime
from services.memory.session_store import session_store
from services.memory.transcript_store import persist_session_message


@dataclass
class ChatTurnContext:
    provider: str
    session_id: str
    session_key: str
    user_message: str
    input_source: str
    workspace_dir: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    token_budget: int = 0
    estimated_tokens: int = 0
    file_memories: List[Dict[str, str]] = field(default_factory=list)
    pending_checkpoint: Optional[Dict[str, Any]] = None
    memory_debug: Dict[str, Any] = field(default_factory=dict)
    finalized: bool = False


def get_provider_for_model(model_name: str) -> str:
    if model_name.startswith("deepseek"):
        return "deepseek"
    if model_name.startswith("kimi") or model_name.startswith("moonshot"):
        return "kimi"
    return "ollama"


def create_session_payload(session_manager: Any, model: str, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    session = session_manager.create_session(model=model, metadata=metadata)
    session.metadata["workspace_dir"] = session.metadata.get("workspace_dir") or os.getcwd()
    session_store.upsert_session(session)
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
    session_store.delete_session(session_id)
    return {"message": "Session deleted"}


async def prepare_chat_turn(
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
                "workspace_dir": os.getcwd(),
            },
        )

    if session.model != model:
        session.model = model
    session.metadata["provider"] = provider
    session.metadata["session_key"] = session_key
    session.metadata["input_source"] = source
    session_manager.bind_session_key(session_key, session.session_id)
    session.metadata["workspace_dir"] = session.metadata.get("workspace_dir") or os.getcwd()
    session_store.upsert_session(session)
    user_message_obj = session_manager.add_message(session_id=session.session_id, role="user", content=user_message)
    if user_message_obj:
        persist_session_message(session_store, session, user_message_obj)

    history_messages = session_manager.get_history(session_id=session.session_id, limit=None) or []
    token_budget = _resolve_token_budget(provider)
    existing_checkpoint = get_latest_checkpoint(session_store, session.session_id)
    context_package = await memory_runtime.prepare_turn(
        RuntimeInput(
            session_id=session.session_id,
            session_key=session_key,
            model=model,
            provider=provider,
            input_source=source,
            user_message=user_message,
            workspace_dir=session.metadata.get("workspace_dir") or os.getcwd(),
            history_messages=history_messages,
            latest_checkpoint=existing_checkpoint,
            token_budget=token_budget,
        )
    )
    messages = context_package.messages
    estimated_tokens = context_package.estimated_tokens
    pending_checkpoint = context_package.pending_checkpoint
    file_memories = context_package.file_memories
    memory_debug = context_package.debug
    return ChatTurnContext(
        provider=provider,
        session_id=session.session_id,
        session_key=session_key,
        user_message=user_message,
        input_source=source,
        workspace_dir=session.metadata.get("workspace_dir") or os.getcwd(),
        messages=messages,
        token_budget=token_budget,
        estimated_tokens=estimated_tokens,
        file_memories=file_memories,
        pending_checkpoint=pending_checkpoint,
        memory_debug=memory_debug,
    )


def _parse_sse_data_event(event: str) -> Optional[Dict[str, Any]]:
    if not event.startswith("data: "):
        return None
    try:
        payload = json.loads(event[6:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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
            persisted = session_manager.add_message(
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
            if persisted:
                session = session_manager.get_session(context.session_id)
                if session:
                    persist_session_message(session_store, session, persisted)
        if react_messages:
            saved_from_react = True
            done_data_pending = None
            await _finalize_turn_memory(
                session_manager,
                context=context,
            )

    if done_data_pending and not saved_from_react:
        persisted = session_manager.add_message(
            session_id=context.session_id,
            role="assistant",
            content=done_data_pending["answer"],
            thinking=done_data_pending["thinking"],
            metadata={"provider": context.provider, "session_key": context.session_key},
        )
        session = session_manager.get_session(context.session_id)
        if session and persisted:
            persist_session_message(session_store, session, persisted)
            await _finalize_turn_memory(
                session_manager,
                context=context,
            )

    yield _to_sse(
        {
            "type": "session_id",
            "session_id": context.session_id,
            "session_key": context.session_key,
            "token_budget": context.token_budget,
            "estimated_tokens": context.estimated_tokens,
            "memory_debug": context.memory_debug,
        }
    )


async def _finalize_turn_memory(session_manager: Any, *, context: ChatTurnContext) -> None:
    if context.finalized:
        return
    context.finalized = True
    context.memory_debug = await memory_runtime.finalize_turn(
        session_manager,
        session_id=context.session_id,
        input_source=context.input_source,
        token_budget=context.token_budget,
        memory_debug=context.memory_debug,
    )
