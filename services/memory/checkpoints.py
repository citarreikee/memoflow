from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.memory.session_store import SessionStore


def save_checkpoint(
    session_store: SessionStore,
    *,
    session_id: str,
    covered_episode_ids: List[str],
    summary: Dict[str, Any],
    token_estimate: int,
) -> Dict[str, Any]:
    checkpoint = {
        "checkpoint_id": f"ckpt_{uuid.uuid4().hex}",
        "session_id": session_id,
        "covers_episode_ids": covered_episode_ids,
        "summary": summary,
        "token_estimate": token_estimate,
        "created_at": datetime.utcnow().isoformat(),
    }
    session_store.save_checkpoint(
        checkpoint_id=checkpoint["checkpoint_id"],
        session_id=session_id,
        created_at=checkpoint["created_at"],
        token_estimate=token_estimate,
        covered_episode_ids=covered_episode_ids,
        summary=summary,
    )
    return checkpoint


def get_latest_checkpoint(session_store: SessionStore, session_id: str) -> Optional[Dict[str, Any]]:
    checkpoint = session_store.get_latest_checkpoint(session_id)
    if not checkpoint:
        return None
    checkpoint["covers_episode_ids"] = checkpoint.pop("covered_episode_ids", [])
    return checkpoint


def get_checkpoint(session_store: SessionStore, checkpoint_id: str) -> Optional[Dict[str, Any]]:
    checkpoint = session_store.get_checkpoint(checkpoint_id)
    if not checkpoint:
        return None
    checkpoint["covers_episode_ids"] = checkpoint.pop("covered_episode_ids", [])
    return checkpoint
