"""In-memory conversation session management.

This module intentionally stores only short-lived chat context for normal
multi-turn conversation. It does not persist, summarize, retrieve, or extract
long-term memory.
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    role: str
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: Optional[str] = None
    thinking: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    reasoning_content: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_provider_message(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        if self.reasoning_content is not None:
            payload["reasoning_content"] = self.reasoning_content
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        return payload


@dataclass
class Session:
    session_id: str
    model: str
    messages: List[Message] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        self.updated_at = datetime.now().isoformat()

    def get_messages(self, limit: Optional[int] = None) -> List[Message]:
        return self.messages if limit is None else self.messages[-limit:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model": self.model,
            "messages": [message.to_dict() for message in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class SessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._session_key_map: Dict[str, str] = {}

    @staticmethod
    def _normalize_session_key(session_key: Optional[str]) -> Optional[str]:
        if session_key is None:
            return None
        normalized = session_key.strip().lower()
        return normalized or None

    def bind_session_key(self, session_key: str, session_id: str) -> None:
        normalized_key = self._normalize_session_key(session_key)
        if normalized_key:
            self._session_key_map[normalized_key] = session_id

    def get_session_id_by_key(self, session_key: Optional[str]) -> Optional[str]:
        normalized_key = self._normalize_session_key(session_key)
        if not normalized_key:
            return None
        return self._session_key_map.get(normalized_key)

    def create_session(self, model: str, metadata: Optional[Dict[str, Any]] = None) -> Session:
        session = Session(session_id=str(uuid.uuid4()), model=model, metadata=metadata or {})
        self._sessions[session.session_id] = session
        return session

    def restore_session(
        self,
        *,
        session_id: str,
        model: str,
        created_at: str,
        updated_at: str,
        metadata: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Message]] = None,
    ) -> Session:
        session = Session(
            session_id=session_id,
            model=model,
            messages=list(messages or []),
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata or {},
        )
        self._sessions[session_id] = session
        session_key = self._normalize_session_key(session.metadata.get("session_key"))
        if session_key:
            self._session_key_map[session_key] = session_id
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: Optional[str],
        thinking: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None,
        reasoning_content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Message]:
        session = self.get_session(session_id)
        if not session:
            return None
        message = Message(
            role=role,
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            name=name,
            reasoning_content=reasoning_content,
            metadata=metadata or {},
        )
        session.add_message(message)
        return message

    def get_history(self, session_id: str, limit: Optional[int] = None) -> Optional[List[Message]]:
        session = self.get_session(session_id)
        if not session:
            return None
        return session.get_messages(limit)

    def delete_session(self, session_id: str) -> bool:
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        stale_keys = [key for key, value in self._session_key_map.items() if value == session_id]
        for key in stale_keys:
            del self._session_key_map[key]
        return True

    def list_sessions(self) -> List[Dict[str, Any]]:
        keys_by_id: Dict[str, List[str]] = {}
        for key, session_id in self._session_key_map.items():
            keys_by_id.setdefault(session_id, []).append(key)
        return [
            {
                "session_id": session.session_id,
                "model": session.model,
                "message_count": len(session.messages),
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "session_keys": sorted(keys_by_id.get(session.session_id, [])),
            }
            for session in self._sessions.values()
        ]


session_manager = SessionManager()
