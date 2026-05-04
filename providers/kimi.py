"""Kimi/Moonshot chat provider adapter."""

import json
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from config import settings
from protocol import (
    ProtocolViolation,
    StreamState,
    format_exception_message,
    format_http_error,
    parse_json_payload,
    validate_tool_call_shape,
)


KIMI_URL = f"{settings.KIMI_API_BASE}/chat/completions"


def _build_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.KIMI_API_KEY}",
        "Content-Type": "application/json",
    }


def _normalize_tool_calls(tool_calls: Any) -> list:
    normalized = []
    if not isinstance(tool_calls, list):
        return normalized
    for index, raw_tool_call in enumerate(tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue
        function_data = raw_tool_call.get("function") if isinstance(raw_tool_call.get("function"), dict) else {}
        tool_name = str(function_data.get("name") or "").strip()
        if not tool_name:
            continue
        arguments = function_data.get("arguments", "")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        elif arguments is None:
            arguments = ""
        else:
            arguments = str(arguments)
        normalized.append(
            {
                "id": str(raw_tool_call.get("id") or f"call_{index}").strip() or f"call_{index}",
                "type": str(raw_tool_call.get("type") or "function"),
                "function": {"name": tool_name, "arguments": arguments},
            }
        )
    return normalized


def _format_messages(messages: list) -> list:
    formatted_messages = []
    pending_tool_calls = []
    for msg in messages:
        role = msg.get("role")
        if role == "developer":
            role = "system"
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        formatted_msg = {"role": role, "content": msg.get("content") or ""}
        if role == "assistant":
            tool_calls = _normalize_tool_calls(msg.get("tool_calls"))
            if tool_calls:
                formatted_msg["tool_calls"] = tool_calls
                formatted_msg["reasoning_content"] = msg.get("reasoning_content") or msg.get("thinking") or ""
                for tool_call in tool_calls:
                    pending_tool_calls.append({"id": tool_call["id"], "name": tool_call["function"]["name"]})
            elif "reasoning_content" in msg:
                formatted_msg["reasoning_content"] = msg.get("reasoning_content") or ""
            if "name" in msg:
                formatted_msg["name"] = msg["name"]
        elif role == "tool":
            tool_call_id = str(msg.get("tool_call_id") or "").strip()
            tool_name = str(msg.get("name") or "").strip()
            if not tool_call_id and pending_tool_calls:
                matched = next((item for item in pending_tool_calls if item["name"] == tool_name), None)
                tool_call_id = (matched or pending_tool_calls[0])["id"]
            if tool_call_id:
                formatted_msg["tool_call_id"] = tool_call_id
                pending_tool_calls = [item for item in pending_tool_calls if item["id"] != tool_call_id]
            if tool_name:
                formatted_msg["name"] = tool_name
        elif "name" in msg:
            formatted_msg["name"] = msg["name"]
        formatted_messages.append(formatted_msg)
    return formatted_messages


def _pick_primary_choice(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    choices = payload.get("choices", [])
    if not choices:
        return None
    for choice in choices:
        if choice.get("index", 0) == 0:
            return choice
    return choices[0]


async def stream_kimi_response(model: str, messages: list) -> AsyncGenerator[str, None]:
    state = StreamState()
    try:
        payload = {"model": model, "messages": _format_messages(messages), "stream": True}
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", KIMI_URL, json=payload, headers=_build_headers()) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="ignore")
                    yield f"data: {json.dumps(state.error_event(format_http_error('Kimi request failed', response.status_code, body)), ensure_ascii=False)}\n\n"
                    return
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line == "data: [DONE]":
                        state.mark_done_sentinel()
                        break
                    if not line.startswith("data: "):
                        continue
                    payload_data = parse_json_payload(line[6:], context="Kimi stream")
                    start_event = state.make_start_event()
                    if start_event:
                        yield f"data: {json.dumps(start_event, ensure_ascii=False)}\n\n"
                    choice = _pick_primary_choice(payload_data)
                    if not choice:
                        continue
                    delta = choice.get("delta", {}) or {}
                    state.mark_finish_reason(choice.get("finish_reason"))
                    thinking_token = delta.get("reasoning_content", "")
                    if thinking_token:
                        state.append_thinking(thinking_token)
                        yield f"data: {json.dumps({'type': 'thinking_token', 'content': thinking_token}, ensure_ascii=False)}\n\n"
                    token = delta.get("content", "")
                    if token:
                        state.append_content(token)
                        yield f"data: {json.dumps({'type': 'response_token', 'content': token}, ensure_ascii=False)}\n\n"
                if not state.saw_done_sentinel and not state.saw_finish_reason:
                    yield f"data: {json.dumps(state.error_event('Kimi stream ended unexpectedly.'), ensure_ascii=False)}\n\n"
                    return
                yield f"data: {json.dumps(state.done_event(answer_key='answer', include_reasoning_content=True), ensure_ascii=False)}\n\n"
    except (ProtocolViolation, httpx.RequestError, Exception) as exc:
        message = format_exception_message(exc)
        if isinstance(exc, httpx.RequestError):
            message = f"Failed to connect to Kimi: {message}"
        err = state.error_event(message) if not state.terminated else {"type": "error", "message": message}
        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"


async def generate_kimi_completion(model: str, messages: list, *, timeout: float = 120.0) -> str:
    payload = {"model": model, "messages": _format_messages(messages), "stream": False}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(KIMI_URL, json=payload, headers=_build_headers())
        if response.status_code >= 400:
            raise RuntimeError(format_http_error("Kimi request failed", response.status_code, response.text))
        data = response.json()
    choice = _pick_primary_choice(data)
    if not choice:
        raise RuntimeError("Kimi completion returned no choices.")
    message = choice.get("message", {}) or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("Kimi completion returned no text content.")
    return content


async def get_kimi_models() -> list[Dict[str, Any]]:
    raw_models = [m.strip() for m in settings.KIMI_MODELS.split(",") if m.strip()] if settings.KIMI_MODELS else ["kimi-k2.5"]
    return [
        {"name": model_name, "id": model_name, "size": "API", "modified": "", "provider": "kimi"}
        for model_name in raw_models
    ]


async def check_kimi_available() -> bool:
    if not settings.KIMI_API_KEY:
        return False
    try:
        model = (await get_kimi_models())[0]["id"]
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                KIMI_URL,
                json={"model": model, "messages": [{"role": "user", "content": "test"}], "max_tokens": 1},
                headers=_build_headers(),
            )
            return response.status_code in [200, 400]
    except Exception:
        return False


async def stream_kimi_with_tools(model: str, messages: list, tools: list) -> AsyncGenerator[Dict[str, Any], None]:
    state = StreamState()
    try:
        payload = {"model": model, "messages": _format_messages(messages), "tools": tools, "stream": True}
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", KIMI_URL, json=payload, headers=_build_headers()) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="ignore")
                    yield state.error_event(format_http_error("Kimi request failed", response.status_code, body))
                    return
                tool_calls_dict: Dict[int, Dict[str, Any]] = {}
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line == "data: [DONE]":
                        state.mark_done_sentinel()
                        break
                    if not line.startswith("data: "):
                        continue
                    payload_data = parse_json_payload(line[6:], context="Kimi stream")
                    start_event = state.make_start_event()
                    if start_event:
                        yield start_event
                    choice = _pick_primary_choice(payload_data)
                    if not choice:
                        continue
                    delta = choice.get("delta", {}) or {}
                    state.mark_finish_reason(choice.get("finish_reason"))
                    thinking_token = delta.get("reasoning_content", "")
                    if thinking_token:
                        state.append_thinking(thinking_token)
                        yield {"type": "thinking_token", "content": thinking_token}
                    content_token = delta.get("content", "")
                    if content_token:
                        state.append_content(content_token)
                        yield {"type": "response_token", "content": content_token}
                    if "tool_calls" in delta:
                        for tc_chunk in delta["tool_calls"]:
                            idx = tc_chunk.get("index", 0)
                            tool_calls_dict.setdefault(
                                idx,
                                {
                                    "id": tc_chunk.get("id", ""),
                                    "type": tc_chunk.get("type", "function"),
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if "id" in tc_chunk:
                                tool_calls_dict[idx]["id"] = tc_chunk["id"]
                            if "type" in tc_chunk:
                                tool_calls_dict[idx]["type"] = tc_chunk["type"]
                            if "function" in tc_chunk:
                                func = tc_chunk["function"]
                                if "name" in func:
                                    tool_calls_dict[idx]["function"]["name"] = func["name"]
                                if "arguments" in func and func["arguments"] is not None:
                                    tool_calls_dict[idx]["function"]["arguments"] += str(func["arguments"])
                if not state.saw_done_sentinel and not state.saw_finish_reason:
                    yield state.error_event("Kimi stream ended unexpectedly.")
                    return
                tool_calls = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]
                for tool_call in tool_calls:
                    validate_tool_call_shape(tool_call)
                yield state.done_event(answer_key="content", tool_calls=tool_calls, include_reasoning_content=True)
    except (ProtocolViolation, Exception) as exc:
        message = format_exception_message(exc)
        yield state.error_event(message) if not state.terminated else {"type": "error", "message": message}
