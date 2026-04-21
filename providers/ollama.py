"""Ollama chat provider adapter."""

import json
from typing import Any, AsyncGenerator, Dict

import httpx

from config import settings
from protocol import ProtocolViolation, StreamState, format_exception_message, format_http_error


def _format_messages(messages: list) -> list:
    formatted_messages = []
    for msg in messages:
        role = msg.get("role")
        if role == "developer":
            role = "system"
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        formatted_msg = {"role": role, "content": msg.get("content") if msg.get("content") is not None else ""}
        for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
            if key in msg:
                formatted_msg[key] = msg[key]
        formatted_messages.append(formatted_msg)
    return formatted_messages


async def stream_ollama_response(model: str, messages: list) -> AsyncGenerator[str, None]:
    state = StreamState()
    try:
        payload = {"model": model, "messages": _format_messages(messages), "stream": True}
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", f"{settings.OLLAMA_API_BASE}/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="ignore")
                    yield f"data: {json.dumps(state.error_event(format_http_error('Ollama request failed', response.status_code, body)), ensure_ascii=False)}\n\n"
                    return
                saw_done = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    start_event = state.make_start_event()
                    if start_event:
                        yield f"data: {json.dumps(start_event, ensure_ascii=False)}\n\n"
                    message = data.get("message", {})
                    thinking_token = message.get("thinking", "")
                    response_token = message.get("content", "")
                    if thinking_token:
                        state.append_thinking(thinking_token)
                        yield f"data: {json.dumps({'type': 'thinking_token', 'content': thinking_token}, ensure_ascii=False)}\n\n"
                    if response_token:
                        state.append_content(response_token)
                        yield f"data: {json.dumps({'type': 'response_token', 'content': response_token}, ensure_ascii=False)}\n\n"
                    done = data.get("done", False)
                    state.mark_finish_reason("stop" if done else None)
                    if done:
                        saw_done = True
                        break
                if not saw_done:
                    yield f"data: {json.dumps(state.error_event('Ollama stream ended unexpectedly.'), ensure_ascii=False)}\n\n"
                    return
                yield f"data: {json.dumps(state.done_event(answer_key='answer'), ensure_ascii=False)}\n\n"
    except (ProtocolViolation, httpx.RequestError, Exception) as exc:
        message = format_exception_message(exc)
        if isinstance(exc, httpx.RequestError):
            message = f"Failed to connect to Ollama: {message}"
        err = state.error_event(message) if not state.terminated else {"type": "error", "message": message}
        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"


async def get_ollama_models() -> list[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.OLLAMA_API_BASE}/api/tags")
            response.raise_for_status()
            data = response.json()
        return [
            {
                "name": model["name"],
                "id": model["name"],
                "size": _format_size(model.get("size", 0)),
                "modified": model.get("modified_at", ""),
                "provider": "ollama",
            }
            for model in data.get("models", [])
        ]
    except Exception as exc:
        raise Exception(f"Failed to fetch Ollama models: {format_exception_message(exc)}")


async def check_ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.OLLAMA_API_BASE}/api/tags")
            return response.status_code == 200
    except Exception:
        return False


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}PB"


async def stream_ollama_with_tools(model: str, messages: list, tools: list) -> AsyncGenerator[Dict[str, Any], None]:
    state = StreamState()
    try:
        payload = {"model": model, "messages": _format_messages(messages), "tools": tools, "stream": True}
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", f"{settings.OLLAMA_API_BASE}/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="ignore")
                    yield state.error_event(format_http_error("Ollama request failed", response.status_code, body))
                    return
                saw_done = False
                tool_calls = []
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    start_event = state.make_start_event()
                    if start_event:
                        yield start_event
                    message = data.get("message", {})
                    thinking_token = message.get("thinking", "")
                    content_token = message.get("content", "")
                    if thinking_token:
                        state.append_thinking(thinking_token)
                        yield {"type": "thinking_token", "content": thinking_token}
                    if content_token:
                        state.append_content(content_token)
                        yield {"type": "response_token", "content": content_token}
                    if "tool_calls" in message:
                        tool_calls = message["tool_calls"]
                    done = data.get("done", False)
                    state.mark_finish_reason("stop" if done else None)
                    if done:
                        saw_done = True
                        break
                if not saw_done:
                    yield state.error_event("Ollama stream ended unexpectedly.")
                    return
                yield state.done_event(answer_key="content", tool_calls=tool_calls)
    except (ProtocolViolation, Exception) as exc:
        message = format_exception_message(exc)
        yield state.error_event(message) if not state.terminated else {"type": "error", "message": message}
