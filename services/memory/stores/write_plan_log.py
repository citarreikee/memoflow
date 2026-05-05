from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from services.memory.formation.schemas import MemoryWritePlan


class WritePlanLog:
    """JSONL dry-run sink for v0.2 memory write plans."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "memory_write_plans.jsonl"

    def append(self, *, session_id: str, plans: Iterable[MemoryWritePlan]) -> int:
        plan_list = list(plans)
        if not plan_list:
            return 0
        with self.path.open("a", encoding="utf-8") as handle:
            for plan in plan_list:
                record = {"session_id": session_id, "plan": plan.to_dict()}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(plan_list)

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records

