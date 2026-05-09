from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from services.memory.formation.integration_schemas import ExistingMemorySnapshot, normalize_existing_memory
from services.memory.formation.schemas import MemoryCandidateLite, MemoryWritePlan
from services.memory.storage.keys import derive_memory_key, derive_namespace
from services.memory.storage.sqlite_store import MemorySQLiteStore


MAX_NEIGHBORHOOD_ITEMS = 16
MAX_SNAPSHOT_TEXT_CHARS = 800
MAX_TOTAL_CHARS = 12000

COMPATIBLE_TYPES = {
    "decision": ["decision", "project_rule", "procedure", "task_state", "entity_relation"],
    "project_rule": ["project_rule", "procedure", "decision", "entity_relation"],
    "procedure": ["procedure", "project_rule", "decision"],
    "task_state": ["task_state", "decision", "entity_relation"],
    "preference": ["preference", "profile_fact"],
    "profile_fact": ["profile_fact", "preference"],
    "entity_relation": ["entity_relation", "decision", "project_rule", "procedure", "task_state"],
    "episodic_event": ["episodic_event", "task_state", "decision"],
    "embedding_hint": ["embedding_hint", "decision", "project_rule", "preference"],
}


@dataclass(frozen=True)
class NeighborhoodFetchPolicy:
    max_items: int = MAX_NEIGHBORHOOD_ITEMS
    max_snapshot_text_chars: int = MAX_SNAPSHOT_TEXT_CHARS
    max_total_chars: int = MAX_TOTAL_CHARS
    exact_limit: int = 3
    lexical_same_type_limit: int = 8
    lexical_compatible_limit: int = 8


class MemoryNeighborhoodRepository:
    def __init__(self, store: MemorySQLiteStore) -> None:
        self.store = store

    def fetch_for_candidate(
        self,
        candidate: MemoryCandidateLite,
        *,
        session_id: str,
        workspace_dir: Optional[str] = None,
        user_id: Optional[str] = None,
        policy: NeighborhoodFetchPolicy | None = None,
    ) -> List[ExistingMemorySnapshot]:
        policy = policy or NeighborhoodFetchPolicy()
        namespace = derive_namespace(
            scope=candidate.scope,
            session_id=session_id,
            workspace_dir=workspace_dir,
            user_id=user_id,
        )
        rows: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
        candidate_key = derive_memory_key(_candidate_plan_adapter(candidate))

        exact = self.store.find_active_record(
            scope=candidate.scope,
            namespace=namespace,
            memory_type=candidate.type,
            key=candidate_key,
        )
        if exact:
            rows.append((exact, {"path": "exact_key", "score": 1.0, "reason": "same derived key"}))

        same_type_rows = self.store.search_active_records(
            query=candidate.text,
            namespace=namespace,
            scopes=[candidate.scope],
            memory_types=[candidate.type],
            limit=policy.lexical_same_type_limit,
        )
        for row in same_type_rows:
            rows.append((row, _match(candidate, row, path="lexical_same_type", reason="same type and scope lexical overlap")))

        compatible = [item for item in COMPATIBLE_TYPES.get(candidate.type, [candidate.type]) if item != candidate.type]
        if compatible:
            compatible_rows = self.store.search_active_records(
                query=candidate.text,
                namespace=namespace,
                scopes=[candidate.scope],
                memory_types=compatible,
                limit=policy.lexical_compatible_limit,
            )
            for row in compatible_rows:
                rows.append((row, _match(candidate, row, path="lexical_compatible_type", reason="compatible type and scope lexical overlap")))

        return _rows_to_snapshots(rows, policy=policy)


def _rows_to_snapshots(
    rows: Iterable[tuple[Dict[str, Any], Dict[str, Any]]],
    *,
    policy: NeighborhoodFetchPolicy,
) -> List[ExistingMemorySnapshot]:
    by_id: Dict[str, ExistingMemorySnapshot] = {}
    total_chars = 0
    for row, match in sorted(rows, key=lambda item: float(item[1].get("score", 0.0)), reverse=True):
        memory_id = str(row.get("memory_id") or "")
        if not memory_id or memory_id in by_id:
            continue
        text = str(row.get("value") or "")
        clipped = _clip(text, policy.max_snapshot_text_chars)
        if total_chars + len(clipped) > policy.max_total_chars:
            break
        payload = _decode_json(row.get("payload_json"))
        evidence = payload.get("evidence_episode_ids") if isinstance(payload, dict) else []
        snapshot = normalize_existing_memory(
            {
                "memory_id": memory_id,
                "type": row.get("type"),
                "scope": row.get("scope"),
                "namespace": row.get("namespace"),
                "key": row.get("key"),
                "text": clipped,
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "version": row.get("version"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "source_plan_id": row.get("source_plan_id"),
                "supersedes_memory_id": row.get("supersedes_memory_id"),
                "superseded_by_memory_id": row.get("superseded_by_memory_id"),
                "evidence_episode_ids": evidence if isinstance(evidence, list) else [],
                "payload": payload if isinstance(payload, dict) else {},
                "match": match,
            }
        )
        by_id[memory_id] = snapshot
        total_chars += len(clipped)
        if len(by_id) >= policy.max_items:
            break
    return list(by_id.values())


def _match(candidate: MemoryCandidateLite, row: Dict[str, Any], *, path: str, reason: str) -> Dict[str, Any]:
    text = str(row.get("value") or row.get("key") or "")
    return {"path": path, "score": round(_lexical_score(candidate.text, text), 4), "reason": reason}


def _lexical_score(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9_\-]+", (text or "").lower()) if len(token) >= 2}


def _clip(text: str, max_chars: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def _decode_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = json.loads(value)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _candidate_plan_adapter(candidate: MemoryCandidateLite) -> MemoryWritePlan:
    return MemoryWritePlan(
        plan_id="candidate_neighborhood_probe",
        candidate_id=candidate.candidate_id or "",
        action=candidate.action,
        canonical_store=None,
        projections=[],
        scope=candidate.scope,
        evidence_episode_ids=[],
        confidence=candidate.importance,
        status="planned",
        type=candidate.type,
        text=candidate.text,
        reason=candidate.reason,
    )
