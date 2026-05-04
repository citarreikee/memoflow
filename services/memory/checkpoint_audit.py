from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from services.memory.session_store import SessionStore


TEXT_FIELDS = ("task_kernel", "conversation", "task_state")
LIST_FIELDS = ("open_loops", "decisions", "important_artifacts", "tool_outcomes", "user_constraints")
SUMMARY_FIELDS = TEXT_FIELDS + LIST_FIELDS

POLLUTION_MARKERS = (
    "output format rules",
    "output exactly these sections",
    "target token budget",
    "content rules",
    "do not output json",
    "do not add code fences",
    "section headers",
    "schema placeholder",
    "list of ",
    "from turn",
    "we need to",
    "this should",
)


@dataclass
class CheckpointQualityReport:
    ok: bool
    score: float
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    field_lengths: Dict[str, int] = field(default_factory=dict)
    polluted_items_removed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def sanitize_checkpoint_summary(summary: Dict[str, Any]) -> tuple[Dict[str, Any], CheckpointQualityReport]:
    cleaned: Dict[str, Any] = {}
    removed = 0
    for field in TEXT_FIELDS:
        text = _clean_text(summary.get(field, ""))
        if _looks_polluted(text):
            text = ""
            removed += 1
        cleaned[field] = text
    for field in LIST_FIELDS:
        items: List[str] = []
        for item in summary.get(field, []) if isinstance(summary.get(field), list) else []:
            text = _clean_text(item)
            if not text:
                continue
            if _looks_polluted(text):
                removed += 1
                continue
            items.append(text[:400])
        cleaned[field] = _dedupe(items)[:16]

    report = evaluate_checkpoint_quality(cleaned)
    report.polluted_items_removed = removed
    if removed:
        report.warnings.append("polluted_items_removed")
    return cleaned, report


def evaluate_checkpoint_quality(summary: Dict[str, Any]) -> CheckpointQualityReport:
    field_lengths = _field_lengths(summary)
    reasons: List[str] = []
    warnings: List[str] = []

    task_kernel = str(summary.get("task_kernel") or "").strip()
    conversation = str(summary.get("conversation") or "").strip()
    task_state = str(summary.get("task_state") or "").strip()
    list_signal_count = sum(len(summary.get(field) or []) for field in LIST_FIELDS)
    non_empty_fields = sum(1 for field in SUMMARY_FIELDS if field_lengths.get(field, 0) > 0)

    if not task_kernel:
        reasons.append("missing_task_kernel")
    if not conversation and not task_state:
        reasons.append("missing_conversation_or_task_state")
    if list_signal_count == 0:
        reasons.append("missing_operational_lists")
    if _summary_has_pollution(summary):
        reasons.append("pollution_detected")
    if _has_repeated_list_items(summary):
        warnings.append("duplicate_or_repeated_items")
    if non_empty_fields < 3:
        warnings.append("low_field_coverage")

    score = 1.0
    score -= 0.25 * len(reasons)
    score -= 0.08 * len(warnings)
    score = max(0.0, round(score, 2))
    return CheckpointQualityReport(
        ok=not reasons and score >= 0.65,
        score=score,
        reasons=reasons,
        warnings=warnings,
        field_lengths=field_lengths,
    )


def build_checkpoint_trace(
    store: SessionStore,
    *,
    session_id: str,
    checkpoint: Optional[Dict[str, Any]],
    max_episode_previews: int = 6,
) -> Dict[str, Any]:
    if not checkpoint:
        return {
            "checkpoint_id": None,
            "covered_count": 0,
            "covered_episode_ids": [],
            "covered_turn_range": None,
            "coverage_complete": True,
            "episode_previews": [],
        }

    covered_ids = _checkpoint_covered_ids(checkpoint)
    episodes_by_id = {episode["episode_id"]: episode for episode in store.list_episodes(session_id)}
    covered_episodes = [episodes_by_id[episode_id] for episode_id in covered_ids if episode_id in episodes_by_id]
    turn_indices = [episode.get("turn_index") for episode in covered_episodes if episode.get("turn_index") is not None]
    previews = [_episode_preview(episode) for episode in covered_episodes[:max_episode_previews]]
    return {
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "covered_count": len(covered_ids),
        "covered_episode_ids": covered_ids,
        "covered_turn_range": [min(turn_indices), max(turn_indices)] if turn_indices else None,
        "coverage_complete": len(covered_episodes) == len(covered_ids),
        "missing_episode_ids": [episode_id for episode_id in covered_ids if episode_id not in episodes_by_id],
        "episode_previews": previews,
    }


def build_checkpoint_recovery_payload(
    store: SessionStore,
    *,
    session_id: str,
    checkpoint_id: Optional[str] = None,
) -> Dict[str, Any]:
    checkpoint = store.get_checkpoint(checkpoint_id) if checkpoint_id else store.get_latest_checkpoint(session_id)
    trace = build_checkpoint_trace(store, session_id=session_id, checkpoint=checkpoint, max_episode_previews=100)
    return {
        "session_id": session_id,
        "checkpoint": checkpoint,
        "trace": trace,
        "covered_episodes": _covered_episode_payloads(store, session_id=session_id, checkpoint=checkpoint),
    }


def _covered_episode_payloads(
    store: SessionStore,
    *,
    session_id: str,
    checkpoint: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not checkpoint:
        return []
    episodes_by_id = {episode["episode_id"]: episode for episode in store.list_episodes(session_id)}
    return [episodes_by_id[episode_id] for episode_id in _checkpoint_covered_ids(checkpoint) if episode_id in episodes_by_id]


def _checkpoint_covered_ids(checkpoint: Dict[str, Any]) -> List[str]:
    return list(checkpoint.get("covers_episode_ids") or checkpoint.get("covered_episode_ids") or [])


def _episode_preview(episode: Dict[str, Any]) -> Dict[str, Any]:
    messages = episode.get("messages") or []
    user_text = ""
    assistant_text = ""
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role == "user" and not user_text:
            user_text = content[:160]
        if role == "assistant" and not assistant_text:
            assistant_text = content[:160]
    return {
        "episode_id": episode.get("episode_id"),
        "turn_index": episode.get("turn_index"),
        "token_estimate": episode.get("token_estimate"),
        "user": user_text,
        "assistant": assistant_text,
    }


def _field_lengths(summary: Dict[str, Any]) -> Dict[str, int]:
    lengths: Dict[str, int] = {}
    for field in TEXT_FIELDS:
        lengths[field] = len(str(summary.get(field) or "").strip())
    for field in LIST_FIELDS:
        value = summary.get(field)
        lengths[field] = len(value) if isinstance(value, list) else 0
    return lengths


def _summary_has_pollution(summary: Dict[str, Any]) -> bool:
    for field in TEXT_FIELDS:
        if _looks_polluted(str(summary.get(field) or "")):
            return True
    for field in LIST_FIELDS:
        for item in summary.get(field) or []:
            if _looks_polluted(str(item)):
                return True
    return False


def _looks_polluted(text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return False
    return any(marker in lowered for marker in POLLUTION_MARKERS)


def _has_repeated_list_items(summary: Dict[str, Any]) -> bool:
    for field in LIST_FIELDS:
        items = [str(item).strip().lower() for item in summary.get(field) or [] if str(item).strip()]
        if len(items) != len(set(items)):
            return True
    return False


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
