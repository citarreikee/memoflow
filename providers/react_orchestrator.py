"""ReAct orchestration for model tool-use loops."""

import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from protocol import ProtocolViolation, parse_tool_arguments, validate_tool_call_shape
from tools import registry


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_with_react(
    model: str,
    messages: List[Dict[str, Any]],
    provider: str,
    enable_tools: bool = True,
    max_iterations: Optional[int] = None,
    force_tool_use: bool = False,
    force_tool_retries: int = 2,
) -> AsyncGenerator[str, None]:
    if not enable_tools:
        if provider == "deepseek":
            from providers import deepseek

            async for event in deepseek.stream_deepseek_response(model, messages):
                yield event
        elif provider == "kimi":
            from providers import kimi

            async for event in kimi.stream_kimi_response(model, messages):
                yield event
        else:
            from providers import ollama

            async for event in ollama.stream_ollama_response(model, messages):
                yield event
        return

    if provider == "deepseek":
        from providers import deepseek

        stream_func = deepseek.stream_deepseek_with_tools
    elif provider == "kimi":
        from providers import kimi

        stream_func = kimi.stream_kimi_with_tools
    else:
        from providers import ollama

        stream_func = ollama.stream_ollama_with_tools

    iteration = 0
    turn_messages: List[Dict[str, Any]] = []
    working_messages = messages.copy()
    force_tool_satisfied = not force_tool_use
    force_tool_retry_count = 0

    while True:
        if max_iterations is not None and iteration >= max_iterations:
            yield _sse({"type": "error", "message": "Max iterations reached."})
            return
        iteration += 1

        full_thinking = ""
        full_content = ""
        reasoning_content = ""
        tool_calls: List[Dict[str, Any]] = []
        done_received = False
        should_buffer_events = force_tool_use and not force_tool_satisfied
        buffered_events: List[Dict[str, Any]] = []

        async for response in stream_func(model, working_messages, registry.get_openai_format()):
            event_type = response.get("type")
            if should_buffer_events:
                buffered_events.append(response)

            if event_type == "start":
                if not should_buffer_events:
                    yield _sse(response)
            elif event_type == "thinking_token":
                token = response.get("content", "")
                full_thinking += token
                if not should_buffer_events:
                    yield _sse(response)
            elif event_type == "response_token":
                token = response.get("content", "")
                full_content += token
                if not should_buffer_events:
                    yield _sse(response)
            elif event_type == "done":
                done_received = True
                full_thinking = response.get("thinking", full_thinking)
                full_content = response.get("content", response.get("answer", full_content))
                reasoning_content = response.get("reasoning_content", "")
                tool_calls = response.get("tool_calls", []) or []
            elif event_type == "error":
                yield _sse(response)
                return

        if not done_received:
            yield _sse({"type": "error", "message": "Provider stream ended without a done event."})
            return

        if not tool_calls:
            if force_tool_use and not force_tool_satisfied:
                if force_tool_retry_count < force_tool_retries:
                    force_tool_retry_count += 1
                    retry_assistant_msg: Dict[str, Any] = {"role": "assistant", "content": full_content}
                    if reasoning_content:
                        retry_assistant_msg["reasoning_content"] = reasoning_content
                    working_messages.append(retry_assistant_msg)
                    working_messages.append(
                        {
                            "role": "user",
                            "content": "For this test turn, you MUST call at least one tool before giving your final answer.",
                        }
                    )
                    continue
                yield _sse({"type": "error", "message": "Forced tool-use mode: model did not emit tool_calls."})
                return

            final_message: Dict[str, Any] = {
                "role": "assistant",
                "content": full_content,
                "thinking": full_thinking,
            }
            if reasoning_content:
                final_message["reasoning_content"] = reasoning_content
            turn_messages.append(final_message)
            break

        if should_buffer_events:
            for buffered_event in buffered_events:
                if buffered_event.get("type") in {"start", "thinking_token", "response_token"}:
                    yield _sse(buffered_event)

        force_tool_satisfied = True
        assistant_tool_call_message: Dict[str, Any] = {
            "role": "assistant",
            "content": full_content or None,
            "thinking": full_thinking,
            "tool_calls": tool_calls,
        }
        if reasoning_content:
            assistant_tool_call_message["reasoning_content"] = reasoning_content

        provider_assistant_message: Dict[str, Any] = {
            "role": "assistant",
            "content": full_content or "",
            "tool_calls": tool_calls,
        }
        if reasoning_content:
            provider_assistant_message["reasoning_content"] = reasoning_content
        elif provider == "kimi":
            provider_assistant_message["reasoning_content"] = ""

        working_messages.append(provider_assistant_message)
        turn_messages.append(assistant_tool_call_message)

        try:
            for tool_call in tool_calls:
                validate_tool_call_shape(tool_call)
                function_data = tool_call.get("function", {})
                tool_name = function_data.get("name", "")
                tool_args = parse_tool_arguments(function_data.get("arguments", "{}"))
                tool_call_id = tool_call.get("id", "")

                yield _sse({"type": "tool_use", "name": tool_name, "arguments": tool_args})
                tool_result = registry.execute(tool_name, tool_args)
                result_text = tool_result if isinstance(tool_result, str) else json.dumps(tool_result, ensure_ascii=False)
                yield _sse({"type": "tool_result", "name": tool_name, "result": result_text})

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": result_text,
                }
                working_messages.append(tool_message)
                turn_messages.append(tool_message)
        except ProtocolViolation as exc:
            yield _sse({"type": "error", "message": f"Protocol violation: {exc}"})
            return
        except Exception as exc:
            yield _sse({"type": "error", "message": f"Tool execution failed: {exc}"})
            return

    final_answer = ""
    for msg in reversed(turn_messages):
        if msg.get("role") == "assistant" and not msg.get("tool_calls") and msg.get("content"):
            final_answer = msg["content"]
            break
    if not final_answer:
        final_answer = "Task completed."

    all_thinking = "\n\n".join(
        msg.get("thinking", "")
        for msg in turn_messages
        if msg.get("role") == "assistant" and msg.get("thinking")
    )
    yield _sse({"type": "done", "thinking": all_thinking, "answer": final_answer, "time": str(iteration * 2)})
    yield _sse({"type": "react_complete", "messages": turn_messages, "iterations": iteration})


async def get_available_tools() -> List[Dict[str, Any]]:
    return registry.get_openai_format()
