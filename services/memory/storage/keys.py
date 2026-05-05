from __future__ import annotations

import hashlib
import re

from services.memory.formation.schemas import MemoryWritePlan


def derive_namespace(*, scope: str, session_id: str, workspace_dir: str | None = None, user_id: str | None = None) -> str:
    if scope == "user" and user_id:
        return f"user:{user_id}"
    if scope == "project" and workspace_dir:
        return f"project:{_short_hash(workspace_dir)}"
    if scope == "workspace" and workspace_dir:
        return f"workspace:{_short_hash(workspace_dir)}"
    return f"session:{session_id}"


def derive_memory_key(plan: MemoryWritePlan) -> str:
    normalized = _normalize(plan.text)
    if plan.type == "task_state":
        return f"task:{_short_hash(normalized)}"
    if plan.type == "entity_relation":
        return f"relation:{_short_hash(normalized)}"
    if plan.type in {"project_rule", "procedure", "decision"}:
        prefix = plan.type.replace("_", "-")
        return f"{prefix}:{_short_hash(normalized)}"
    if plan.type in {"preference", "profile_fact"}:
        words = normalized.split(" ")[:8]
        if words:
            return f"{plan.type}:{'-'.join(words)}"
    return f"{plan.type}:{_short_hash(normalized)}"


def _normalize(text: str) -> str:
    lowered = (text or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]

