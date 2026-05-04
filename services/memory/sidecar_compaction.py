from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from providers import deepseek, kimi, ollama


SECTION_TASK_KERNEL = "TASK_KERNEL"
SECTION_CONVERSATION = "CONVERSATION"
SECTION_TASK_STATE = "TASK_STATE"
SECTION_OPEN_LOOPS = "OPEN_LOOPS"
SECTION_DECISIONS = "DECISIONS"
SECTION_IMPORTANT_ARTIFACTS = "IMPORTANT_ARTIFACTS"
SECTION_TOOL_OUTCOMES = "TOOL_OUTCOMES"
SECTION_USER_CONSTRAINTS = "USER_CONSTRAINTS"

ALL_SECTIONS = [
    SECTION_TASK_KERNEL,
    SECTION_CONVERSATION,
    SECTION_TASK_STATE,
    SECTION_OPEN_LOOPS,
    SECTION_DECISIONS,
    SECTION_IMPORTANT_ARTIFACTS,
    SECTION_TOOL_OUTCOMES,
    SECTION_USER_CONSTRAINTS,
]


def _render_turns(turns: List[List[Dict[str, Any]]]) -> str:
    lines: List[str] = []
    for turn_index, turn in enumerate(turns, start=1):
        lines.append(f"## Turn {turn_index}")
        for message in turn:
            role = message.get("role", "")
            name = message.get("name")
            prefix = f"{role}"
            if name:
                prefix += f"({name})"
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                lines.append(f"{prefix}: {content.strip()}")
            tool_calls = message.get("tool_calls")
            if tool_calls:
                lines.append(f"{prefix}.tool_calls: {json.dumps(tool_calls, ensure_ascii=False)}")
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                lines.append(f"{prefix}.reasoning: {reasoning.strip()}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_compaction_messages(
    older_turns: List[List[Dict[str, Any]]],
    previous_checkpoint: Optional[Dict[str, Any]],
    target_tokens: int,
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a working-context compaction sidecar for an autonomous coding agent.\n"
        "Your only job is to compress older conversation turns into a compact runtime checkpoint.\n"
        "Do not output JSON.\n"
        "Do not output analysis before the sections.\n"
        "Output only the required section headers and their content."
    )

    format_spec = (
        "Output format rules:\n"
        "1. Output exactly these sections, in exactly this order.\n"
        f"2. Use the header line '{SECTION_TASK_KERNEL}:' then one short paragraph.\n"
        f"3. Use the header line '{SECTION_CONVERSATION}:' then one short paragraph.\n"
        f"4. Use the header line '{SECTION_TASK_STATE}:' then one short paragraph.\n"
        f"5. Use the header line '{SECTION_OPEN_LOOPS}:' then zero or more bullet lines starting with '- '.\n"
        f"6. Use the header line '{SECTION_DECISIONS}:' then zero or more bullet lines starting with '- '.\n"
        f"7. Use the header line '{SECTION_IMPORTANT_ARTIFACTS}:' then zero or more bullet lines starting with '- '.\n"
        f"8. Use the header line '{SECTION_TOOL_OUTCOMES}:' then zero or more bullet lines starting with '- '.\n"
        f"9. Use the header line '{SECTION_USER_CONSTRAINTS}:' then zero or more bullet lines starting with '- '.\n"
        "10. Do not add any extra headers.\n"
        "11. Do not add code fences.\n"
        "12. Do not add explanations before or after the sections."
    )

    user_parts = [
        f"Target token budget for the checkpoint: <= {target_tokens} tokens.",
        format_spec,
    ]
    if previous_checkpoint and previous_checkpoint.get("summary"):
        user_parts.extend(
            [
                "Existing checkpoint summary to refine or merge with newer covered turns:",
                _render_previous_summary(previous_checkpoint["summary"]),
            ]
        )
    user_parts.extend(
        [
            "Older turns to compact:",
            _render_turns(older_turns),
            (
                "Content rules:\n"
                "1. Preserve current objective, ongoing task state, open loops, decisions, explicit constraints, "
                "important artifacts, and tool outcomes that still matter.\n"
                "2. Drop greetings, repetition, rhetorical filler, and completed historical details.\n"
                "3. If something is uncertain, omit it rather than inventing.\n"
                "4. Keep the result operational and compact."
            ),
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


async def compact_with_sidecar(
    *,
    older_turns: List[List[Dict[str, Any]]],
    previous_checkpoint: Optional[Dict[str, Any]],
    target_tokens: int,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not settings.SIDECAR_COMPACTION_ENABLED:
        return None, {"disabled": True}
    if not older_turns:
        return None, {"disabled": True, "reason": "no_older_turns"}

    messages = _build_compaction_messages(older_turns, previous_checkpoint, target_tokens)
    provider = settings.SIDECAR_COMPACTION_PROVIDER.lower()
    model = settings.SIDECAR_COMPACTION_MODEL
    raw_text: Optional[str] = None

    try:
        attempt_debug: List[Dict[str, Any]] = []
        attempts = _build_attempts(provider=provider, model=model)

        for attempt in attempts:
            raw_text = await _run_attempt(
                provider=attempt["provider"],
                model=attempt["model"],
                messages=messages,
                num_predict=attempt.get("num_predict"),
            )
            attempt_debug.append(
                {
                    "provider": attempt["provider"],
                    "model": attempt["model"],
                    "num_predict": attempt.get("num_predict"),
                    "empty": not bool(raw_text and raw_text.strip()),
                }
            )
            if raw_text and raw_text.strip():
                break

        if not raw_text or not raw_text.strip():
            return None, {"provider": provider, "error": "empty_response", "attempts": attempt_debug}

        parsed = _parse_sectioned_summary(raw_text)
        if not parsed:
            return None, {
                "provider": provider,
                "error": "parse_failed",
                "raw_preview": raw_text[:1200],
                "attempts": attempt_debug,
            }

        return parsed, {
            "provider": provider,
            "model": model,
            "raw_preview": raw_text[:1200],
            "attempts": attempt_debug,
        }
    except Exception as exc:
        return None, {
            "provider": provider,
            "error": str(exc),
        }


async def _run_attempt(
    *,
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    num_predict: Optional[int] = None,
) -> Optional[str]:
    if provider == "ollama":
        return await ollama.generate_ollama_completion(
            model=model,
            messages=messages,
            timeout=settings.SIDECAR_COMPACTION_TIMEOUT_SECONDS,
            num_predict=num_predict,
            think=False,
        )
    if provider == "deepseek":
        return await deepseek.generate_deepseek_completion(
            model=model,
            messages=messages,
            timeout=settings.SIDECAR_COMPACTION_TIMEOUT_SECONDS,
        )
    if provider == "kimi":
        return await kimi.generate_kimi_completion(
            model=model,
            messages=messages,
            timeout=settings.SIDECAR_COMPACTION_TIMEOUT_SECONDS,
        )
    return None


def _parse_sectioned_summary(raw_text: str) -> Optional[Dict[str, Any]]:
    lines = [line.rstrip() for line in raw_text.splitlines()]
    sections: Dict[str, List[str]] = {name: [] for name in ALL_SECTIONS}
    current_section: Optional[str] = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        matched_section = _match_section_header(line)
        if matched_section:
            current_section = matched_section
            inline_value = _extract_after_colon(line)
            if inline_value:
                sections[current_section].append(inline_value)
            continue
        if current_section is None:
            continue
        sections[current_section].append(line)

    if not any(sections.values()):
        return None

    return _normalize_summary_payload(
        {
            "task_kernel": _join_paragraph(sections[SECTION_TASK_KERNEL]),
            "conversation": _join_paragraph(sections[SECTION_CONVERSATION]),
            "task_state": _join_paragraph(sections[SECTION_TASK_STATE]),
            "open_loops": _extract_bullets(sections[SECTION_OPEN_LOOPS]),
            "decisions": _extract_bullets(sections[SECTION_DECISIONS]),
            "important_artifacts": _extract_bullets(sections[SECTION_IMPORTANT_ARTIFACTS]),
            "tool_outcomes": _extract_bullets(sections[SECTION_TOOL_OUTCOMES]),
            "user_constraints": _extract_bullets(sections[SECTION_USER_CONSTRAINTS]),
        }
    )


def _match_section_header(line: str) -> Optional[str]:
    for section in ALL_SECTIONS:
        if line == f"{section}:" or line.startswith(f"{section}:"):
            return section
    return None


def _normalize_summary_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_kernel": _normalize_text(payload.get("task_kernel")),
        "conversation": _normalize_text(payload.get("conversation")),
        "task_state": _normalize_text(payload.get("task_state")),
        "open_loops": _normalize_string_list(payload.get("open_loops")),
        "decisions": _normalize_string_list(payload.get("decisions")),
        "important_artifacts": _normalize_string_list(payload.get("important_artifacts")),
        "tool_outcomes": _normalize_string_list(payload.get("tool_outcomes")),
        "user_constraints": _normalize_string_list(payload.get("user_constraints")),
    }


def _join_paragraph(lines: List[str]) -> str:
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        cleaned.append(stripped)
    return " ".join(cleaned).strip()


def _extract_bullets(lines: List[str]) -> List[str]:
    items: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif not items and stripped:
            items.extend(_split_inline_list(stripped))
    return items


def _render_previous_summary(summary: Dict[str, Any]) -> str:
    return "\n".join(
        [
            f"{SECTION_TASK_KERNEL}: {summary.get('task_kernel', '')}",
            f"{SECTION_CONVERSATION}: {summary.get('conversation', '')}",
            f"{SECTION_TASK_STATE}: {summary.get('task_state', '')}",
            f"{SECTION_OPEN_LOOPS}:",
            *[f"- {item}" for item in summary.get("open_loops", [])],
            f"{SECTION_DECISIONS}:",
            *[f"- {item}" for item in summary.get("decisions", [])],
            f"{SECTION_IMPORTANT_ARTIFACTS}:",
            *[f"- {item}" for item in summary.get("important_artifacts", [])],
            f"{SECTION_TOOL_OUTCOMES}:",
            *[f"- {item}" for item in summary.get("tool_outcomes", [])],
            f"{SECTION_USER_CONSTRAINTS}:",
            *[f"- {item}" for item in summary.get("user_constraints", [])],
        ]
    ).strip()


def _extract_after_colon(line: str) -> str:
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:4000]


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if text.startswith("[") or text.startswith("{"):
            continue
        if text.lower().startswith("list of "):
            continue
        result.append(text[:400])
    return result[:16]


def _split_inline_list(value: str) -> List[str]:
    parts = [part.strip(" -") for part in value.split(",")]
    return [part for part in parts if part]


def _resolve_sidecar_num_predict(*, provider: str, model: str) -> Optional[int]:
    if provider == "ollama" and model == "qwen3:30b-a3b":
        return None
    configured = settings.SIDECAR_COMPACTION_NUM_PREDICT
    return configured if configured > 0 else None


def _build_attempts(*, provider: str, model: str) -> List[Dict[str, Any]]:
    num_predict = _resolve_sidecar_num_predict(provider=provider, model=model)
    retry_count = 3 if provider == "ollama" and model == "qwen3:30b-a3b" else 1
    return [{"provider": provider, "model": model, "num_predict": num_predict} for _ in range(retry_count)]
