from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config import settings
from services.context_utils import estimate_messages_tokens
from services.memory.sidecar_compaction import compact_with_sidecar


def should_compact(
    *,
    estimated_tokens: int,
    token_budget: int,
    user_turn_count: int,
) -> bool:
    if token_budget <= 0:
        return False
    if user_turn_count < settings.CONTEXT_COMPACTION_MIN_USER_TURNS:
        return False
    return estimated_tokens >= int(token_budget * settings.CONTEXT_COMPACTION_TRIGGER_RATIO)


def split_turns_for_compaction(turns: List[List[Dict[str, Any]]]) -> Tuple[List[List[Dict[str, Any]]], List[List[Dict[str, Any]]]]:
    keep_recent = max(1, settings.CONTEXT_COMPACTION_KEEP_RECENT_TURNS)
    if len(turns) <= keep_recent:
        return [], turns
    return turns[:-keep_recent], turns[-keep_recent:]


async def build_checkpoint_summary(
    older_turns: List[List[Dict[str, Any]]],
    previous_checkpoint: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    sidecar_summary, sidecar_debug = await compact_with_sidecar(
        older_turns=older_turns,
        previous_checkpoint=previous_checkpoint,
        target_tokens=settings.CONTEXT_CHECKPOINT_TARGET_TOKENS,
    )
    if sidecar_summary:
        normalized = _ensure_summary_shape(sidecar_summary)
        folded_summary, folded = maybe_fold_checkpoint_summary(normalized)
        debug = {
            "used_sidecar": True,
            "used_fallback": False,
            "folded": folded,
            "sidecar_debug": sidecar_debug,
        }
        return folded_summary, debug

    fallback_summary = _build_rule_based_checkpoint_summary(older_turns, previous_checkpoint=previous_checkpoint)
    folded_summary, folded = maybe_fold_checkpoint_summary(fallback_summary)
    debug = {
        "used_sidecar": False,
        "used_fallback": True,
        "folded": folded,
        "sidecar_debug": sidecar_debug,
    }
    return folded_summary, debug


def _build_rule_based_checkpoint_summary(
    older_turns: List[List[Dict[str, Any]]],
    previous_checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    conversation_lines: List[str] = []
    decisions: List[str] = []
    open_loops: List[str] = []
    task_fragments: List[str] = []
    important_artifacts: List[str] = []
    tool_outcomes: List[str] = []
    user_constraints: List[str] = []
    prior = previous_checkpoint["summary"] if previous_checkpoint and previous_checkpoint.get("summary") else {}

    for turn in older_turns:
        user_parts = [msg.get("content", "") for msg in turn if msg.get("role") == "user" and msg.get("content")]
        assistant_parts = [
            msg.get("content", "")
            for msg in turn
            if msg.get("role") == "assistant" and msg.get("content") and not msg.get("tool_calls")
        ]
        if user_parts:
            conversation_lines.append(f"User asked: {' '.join(user_parts)[:400]}")
            task_fragments.extend(part[:200] for part in user_parts[:1])
            if any(keyword in user_parts[0].lower() for keyword in ("must", "不要", "不能", "only", "just", "require")):
                user_constraints.append(user_parts[0][:240])
        if assistant_parts:
            conversation_lines.append(f"Assistant responded: {' '.join(assistant_parts)[:400]}")

        for msg in turn:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            lowered = content.lower()
            if any(keyword in lowered for keyword in ("will", "plan", "decide", "decision", "use ", "adopt")):
                decisions.append(content[:240])
            if any(keyword in lowered for keyword in ("todo", "next", "remaining", "follow up", "open issue")):
                open_loops.append(content[:240])
            if any(keyword in lowered for keyword in ("path", ".py", ".md", "api/", "services/", "providers/")):
                important_artifacts.append(content[:240])
            if msg.get("role") == "tool":
                tool_outcomes.append(content[:240])

    summary = {
        "task_kernel": _merge_texts(prior.get("task_kernel", ""), "\n".join(task_fragments[:4])),
        "conversation": "\n".join(conversation_lines[:12]).strip(),
        "task_state": _merge_texts(prior.get("task_state", ""), "\n".join(task_fragments[:6])),
        "open_loops": _dedupe_preserve_order((prior.get("open_loops") or []) + open_loops)[:8],
        "decisions": _dedupe_preserve_order((prior.get("decisions") or []) + decisions)[:8],
        "important_artifacts": _dedupe_preserve_order((prior.get("important_artifacts") or []) + important_artifacts)[:10],
        "tool_outcomes": _dedupe_preserve_order((prior.get("tool_outcomes") or []) + tool_outcomes)[:8],
        "user_constraints": _dedupe_preserve_order((prior.get("user_constraints") or []) + user_constraints)[:8],
    }
    return _ensure_summary_shape(summary)


def maybe_fold_checkpoint_summary(summary: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    normalized = _ensure_summary_shape(summary)
    token_estimate = estimate_checkpoint_tokens(normalized)
    if token_estimate <= settings.CONTEXT_CHECKPOINT_HARD_MAX_TOKENS:
        return normalized, False

    current = normalized
    max_passes = 8
    for _ in range(max_passes):
        if estimate_checkpoint_tokens(current) <= settings.CONTEXT_CHECKPOINT_HARD_MAX_TOKENS:
            return current, True
        current = _fold_summary_once(current)
    return current, True


def build_checkpoint_message(summary: Dict[str, Any]) -> Dict[str, Any]:
    lines: List[str] = ["Working checkpoint summary:"]
    if summary.get("task_kernel"):
        lines.append(f"Task kernel: {summary['task_kernel']}")
    if summary.get("conversation"):
        lines.append(f"Conversation: {summary['conversation']}")
    if summary.get("task_state"):
        lines.append(f"Task state: {summary['task_state']}")
    if summary.get("open_loops"):
        lines.append("Open loops:")
        lines.extend(f"- {item}" for item in summary["open_loops"])
    if summary.get("decisions"):
        lines.append("Decisions:")
        lines.extend(f"- {item}" for item in summary["decisions"])
    if summary.get("important_artifacts"):
        lines.append("Important artifacts:")
        lines.extend(f"- {item}" for item in summary["important_artifacts"])
    if summary.get("tool_outcomes"):
        lines.append("Tool outcomes:")
        lines.extend(f"- {item}" for item in summary["tool_outcomes"])
    if summary.get("user_constraints"):
        lines.append("User constraints:")
        lines.extend(f"- {item}" for item in summary["user_constraints"])
    return {"role": "system", "content": "\n".join(lines).strip()}


def estimate_checkpoint_tokens(summary: Dict[str, Any]) -> int:
    return estimate_messages_tokens([build_checkpoint_message(summary)])


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _ensure_summary_shape(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_kernel": _truncate_text(summary.get("task_kernel", ""), 2000),
        "conversation": _truncate_text(summary.get("conversation", ""), 2500),
        "task_state": _truncate_text(summary.get("task_state", ""), 2200),
        "open_loops": _dedupe_preserve_order([str(item).strip() for item in summary.get("open_loops", []) if str(item).strip()])[:12],
        "decisions": _dedupe_preserve_order([str(item).strip() for item in summary.get("decisions", []) if str(item).strip()])[:12],
        "important_artifacts": _dedupe_preserve_order(
            [str(item).strip() for item in summary.get("important_artifacts", []) if str(item).strip()]
        )[:16],
        "tool_outcomes": _dedupe_preserve_order([str(item).strip() for item in summary.get("tool_outcomes", []) if str(item).strip()])[:12],
        "user_constraints": _dedupe_preserve_order(
            [str(item).strip() for item in summary.get("user_constraints", []) if str(item).strip()]
        )[:12],
    }


def _merge_texts(left: Any, right: Any) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if left_text and right_text:
        return f"{left_text}\n{right_text}".strip()
    return left_text or right_text


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _fold_summary_once(summary: Dict[str, Any]) -> Dict[str, Any]:
    task_kernel = _truncate_text(_merge_texts(summary.get("task_kernel", ""), summary.get("task_state", "")), 600)
    task_state = _truncate_text(summary.get("task_state", ""), 320)
    conversation = _truncate_text(summary.get("conversation", ""), 220)
    return _ensure_summary_shape(
        {
            "task_kernel": task_kernel,
            "conversation": conversation,
            "task_state": task_state,
            "open_loops": summary.get("open_loops", [])[: max(2, len(summary.get("open_loops", [])) - 1)],
            "decisions": summary.get("decisions", [])[: max(2, len(summary.get("decisions", [])) - 1)],
            "important_artifacts": summary.get("important_artifacts", [])[: max(2, len(summary.get("important_artifacts", [])) - 2)],
            "tool_outcomes": summary.get("tool_outcomes", [])[: max(1, len(summary.get("tool_outcomes", [])) - 1)],
            "user_constraints": summary.get("user_constraints", [])[: max(1, len(summary.get("user_constraints", [])) - 1)],
        }
    )
