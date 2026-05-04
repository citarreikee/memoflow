"""DeepSeek chat provider adapter."""

import json
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from config import settings
from protocol import ProtocolViolation, StreamState, format_exception_message, format_http_error, parse_json_payload


DEEPSEEK_URL = f"{settings.DEEPSEEK_API_BASE}/chat/completions"


def _create_async_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def _build_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


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


def _pick_primary_choice(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    choices = payload.get("choices", [])
    if not choices:
        return None
    for choice in choices:
        if choice.get("index", 0) == 0:
            return choice
    return choices[0]


async def stream_deepseek_response(model: str, messages: list) -> AsyncGenerator[str, None]:
    state = StreamState()
    try:
        payload = {"model": model, "messages": _format_messages(messages), "stream": True}
        async with _create_async_client(timeout=120.0) as client:
            async with client.stream("POST", DEEPSEEK_URL, json=payload, headers=_build_headers()) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="ignore")
                    yield f"data: {json.dumps(state.error_event(format_http_error('DeepSeek request failed', response.status_code, body)), ensure_ascii=False)}\n\n"
                    return

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line == "data: [DONE]":
                        state.mark_done_sentinel()
                        break
                    if not line.startswith("data: "):
                        continue
                    payload_data = parse_json_payload(line[6:], context="DeepSeek stream")
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
                    response_token = delta.get("content", "")
                    if response_token:
                        state.append_content(response_token)
                        yield f"data: {json.dumps({'type': 'response_token', 'content': response_token}, ensure_ascii=False)}\n\n"

                if not state.saw_done_sentinel and not state.saw_finish_reason:
                    yield f"data: {json.dumps(state.error_event('DeepSeek stream ended unexpectedly.'), ensure_ascii=False)}\n\n"
                    return
                yield f"data: {json.dumps(state.done_event(answer_key='answer'), ensure_ascii=False)}\n\n"
    except (ProtocolViolation, httpx.RequestError, Exception) as exc:
        message = format_exception_message(exc)
        if isinstance(exc, httpx.RequestError):
            message = f"Failed to connect to DeepSeek: {message}"
        err = state.error_event(message) if not state.terminated else {"type": "error", "message": message}
        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"


async def generate_deepseek_completion(model: str, messages: list, *, timeout: float = 120.0) -> str:
    payload = {"model": model, "messages": _format_messages(messages), "stream": False}
    async with _create_async_client(timeout=timeout) as client:
        response = await client.post(DEEPSEEK_URL, json=payload, headers=_build_headers())
        if response.status_code >= 400:
            raise RuntimeError(format_http_error("DeepSeek request failed", response.status_code, response.text))
        data = response.json()
    choice = _pick_primary_choice(data)
    if not choice:
        raise RuntimeError("DeepSeek completion returned no choices.")
    message = choice.get("message", {}) or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("DeepSeek completion returned no text content.")
    return content


async def get_deepseek_models() -> list[Dict[str, Any]]:
    models = [model.strip() for model in settings.DEEPSEEK_MODELS.split(",") if model.strip()]
    return [
        {"name": model, "id": model, "size": "API", "modified": "", "provider": "deepseek"}
        for model in models
    ]


async def check_deepseek_available() -> bool:
    if not settings.DEEPSEEK_API_KEY:
        return False
    try:
        async with _create_async_client(timeout=5.0) as client:
            response = await client.post(
                DEEPSEEK_URL,
                json={"model": (await get_deepseek_models())[0]["id"], "messages": [{"role": "user", "content": "test"}], "max_tokens": 1},
                headers=_build_headers(),
            )
            return response.status_code in [200, 400]
    except Exception:
        return False


async def stream_deepseek_with_tools(model: str, messages: list, tools: list) -> AsyncGenerator[Dict[str, Any], None]:
    state = StreamState()
    try:
        payload = {"model": model, "messages": _format_messages(messages), "tools": tools, "stream": True}
        async with _create_async_client(timeout=120.0) as client:
            async with client.stream("POST", DEEPSEEK_URL, json=payload, headers=_build_headers()) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="ignore")
                    yield state.error_event(format_http_error("DeepSeek request failed", response.status_code, body))
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
                    payload_data = parse_json_payload(line[6:], context="DeepSeek stream")
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
                    yield state.error_event("DeepSeek stream ended unexpectedly.")
                    return
                tool_calls = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]
                yield state.done_event(answer_key="content", tool_calls=tool_calls, include_reasoning_content=True)
    except (ProtocolViolation, Exception) as exc:
        message = format_exception_message(exc)
        yield state.error_event(message) if not state.terminated else {"type": "error", "message": message}
