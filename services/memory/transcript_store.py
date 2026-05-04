from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from chat_history import Message

from services.memory.session_store import SessionStore


def persist_session_message(session_store: SessionStore, session: Any, message: Message) -> None:
    session_store.append_message(session, message)


def build_episode_payload(
    *,
    session_id: str,
    turn_index: int,
    source: str,
    messages: List[Message],
    token_estimate: int,
) -> Dict[str, Any]:
    return {
        "episode_id": f"ep_{uuid.uuid4().hex}",
        "session_id": session_id,
        "turn_index": turn_index,
        "source": source,
        "messages": [message.to_provider_message() for message in messages],
        "message_ids": [message.message_id for message in messages],
        "token_estimate": token_estimate,
        "created_at": datetime.utcnow().isoformat(),
    }


def persist_episode(session_store: SessionStore, episode: Dict[str, Any]) -> None:
    message_ids = episode.get("message_ids") or []
    if not message_ids:
        return
    session_store.insert_episode(
        episode_id=episode["episode_id"],
        session_id=episode["session_id"],
        start_message_id=message_ids[0],
        end_message_id=message_ids[-1],
        turn_index=episode["turn_index"],
        token_estimate=episode["token_estimate"],
        source=episode["source"],
        created_at=episode["created_at"],
        payload=episode,
    )


def next_turn_index(session_store: SessionStore, session_id: str) -> int:
    return session_store.get_episode_count(session_id) + 1

