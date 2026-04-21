from __future__ import annotations

from typing import Any, Dict, Optional

from config import settings
from services.memory.harness import list_harnesses as list_harness_payload
from services.memory.runtime import memory_runtime


def memory_status_payload() -> Dict[str, Any]:
    return {
        "enabled": memory_runtime.enabled,
        "db_path": str(memory_runtime.store.db_path) if memory_runtime.store else None,
        "default_harness": settings.MEMORY_DEFAULT_HARNESS,
    }


def list_episodes_payload(session_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    store = _require_store()
    return {"episodes": store.list_episodes(session_id=session_id, limit=limit)}


def get_episode_payload(episode_id: str) -> Dict[str, Any]:
    store = _require_store()
    episode = store.get_episode(episode_id)
    if not episode:
        raise KeyError("Episode not found")
    return episode


def get_short_term_state_payload(session_id: str) -> Dict[str, Any]:
    store = _require_store()
    state = store.get_short_term_state(session_id)
    if not state:
        raise KeyError("Short-term state not found")
    return state.to_dict()


def list_short_term_states_payload(limit: int = 50) -> Dict[str, Any]:
    store = _require_store()
    return {"states": store.list_short_term_states(limit=limit)}


def list_memory_atoms_payload(
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    store = _require_store()
    return {"atoms": store.list_memory_atoms(scope_type=scope_type, scope_id=scope_id, limit=limit)}


def get_memory_atom_payload(atom_id: str) -> Dict[str, Any]:
    store = _require_store()
    atom = store.get_memory_atom(atom_id)
    if not atom:
        raise KeyError("Memory atom not found")
    return atom


def search_memory_payload(
    query: str,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    store = _require_store()
    query_lower = query.strip().lower()
    atoms = store.list_memory_atoms(scope_type=scope_type, scope_id=scope_id, limit=500)
    if query_lower:
        atoms = [
            atom
            for atom in atoms
            if query_lower in atom.get("content", "").lower()
            or query_lower in atom.get("normalized_content", "").lower()
            or any(query_lower in keyword.lower() for keyword in atom.get("keywords", []))
        ]
    return {"query": query, "atoms": atoms[: max(1, min(limit, 100))]}


def list_harnesses_payload() -> Dict[str, Any]:
    return {"harnesses": list_harness_payload()}


def _require_store():
    if not memory_runtime.enabled or not memory_runtime.store:
        raise RuntimeError("Memory subsystem is disabled")
    return memory_runtime.store
