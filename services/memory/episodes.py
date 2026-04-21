from __future__ import annotations

import uuid
from typing import Any, Dict, List

from services.memory.harness import MemoryHarness
from services.memory.schemas import Episode, utc_now_iso
from services.memory.stores import SQLiteMemoryStore


class EpisodeRecorder:
    def __init__(self, store: SQLiteMemoryStore) -> None:
        self.store = store

    def record_completed_turn(
        self,
        *,
        session_id: str,
        session_key: str,
        harness: MemoryHarness,
        source: str,
        user_message: str,
        assistant_answer: str,
        react_messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Episode:
        now = utc_now_iso()
        episode = Episode(
            id=str(uuid.uuid4()),
            session_id=session_id,
            session_key=session_key,
            harness=harness.name,
            source=source,
            user_message=user_message,
            assistant_answer=assistant_answer,
            tool_trace=self._extract_tool_trace(react_messages),
            metadata=metadata,
            created_at=now,
            completed_at=now,
        )
        self.store.save_episode(episode)
        return episode

    @staticmethod
    def _extract_tool_trace(react_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        trace: List[Dict[str, Any]] = []
        for msg in react_messages:
            if msg.get("tool_calls"):
                for tool_call in msg.get("tool_calls") or []:
                    function_data = tool_call.get("function") or {}
                    trace.append(
                        {
                            "type": "tool_call",
                            "id": tool_call.get("id"),
                            "name": function_data.get("name"),
                            "arguments": function_data.get("arguments"),
                        }
                    )
            if msg.get("role") == "tool":
                trace.append(
                    {
                        "type": "tool_result",
                        "tool_call_id": msg.get("tool_call_id"),
                        "name": msg.get("name"),
                        "content": msg.get("content"),
                    }
                )
        return trace
