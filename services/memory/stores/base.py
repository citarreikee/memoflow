from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from services.memory.formation.schemas import MemoryWritePlan


class JsonlMockStore:
    """Append-only JSONL mock store used by v0.2 dry-run substrates."""

    def __init__(self, base_dir: str, filename: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / filename

    def append_records(self, records: Iterable[Dict[str, Any]]) -> int:
        record_list = list(records)
        if not record_list:
            return 0
        with self.path.open("a", encoding="utf-8") as handle:
            for record in record_list:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(record_list)

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


def plan_record(session_id: str, plan: MemoryWritePlan, *, store: str, role: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "store": store,
        "role": role,
        "plan_id": plan.plan_id,
        "candidate_id": plan.candidate_id,
        "type": plan.type,
        "action": plan.action,
        "scope": plan.scope,
        "text": plan.text,
        "reason": plan.reason,
        "confidence": plan.confidence,
        "evidence_episode_ids": plan.evidence_episode_ids,
        "status": plan.status,
        "blocked_reasons": plan.blocked_reasons,
    }

