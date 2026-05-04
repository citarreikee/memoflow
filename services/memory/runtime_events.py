from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class RuntimeEvent:
    type: str
    timestamp: str
    data: Dict[str, Any]


class RuntimeEventLog:
    """Small append-only event log used to explain runtime memory decisions."""

    def __init__(self) -> None:
        self._events: List[RuntimeEvent] = []

    def add(self, event_type: str, **data: Any) -> None:
        self._events.append(
            RuntimeEvent(
                type=event_type,
                timestamp=datetime.utcnow().isoformat(),
                data=data,
            )
        )

    def names(self) -> List[str]:
        return [event.type for event in self._events]

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [asdict(event) for event in self._events]

