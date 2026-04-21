from __future__ import annotations

import hashlib
import re
import uuid
from typing import List

from services.memory.harness import MemoryHarness
from services.memory.reasoner import MemoryReasoner, MemoryReasonerError
from services.memory.schemas import Episode, MemoryAtom, utc_now_iso
from services.memory.stores import SQLiteMemoryStore


class MemoryIngestor:
    """LLM-driven ADD-only ingestor constrained by schema and dedupe."""

    def __init__(self, store: SQLiteMemoryStore, reasoner: MemoryReasoner | None = None) -> None:
        self.store = store
        self.reasoner = reasoner

    def ingest_episode(self, episode: Episode, harness: MemoryHarness) -> List[MemoryAtom]:
        # Ingestion is already model-driven: we ask the sidecar to emit typed
        # candidate atoms, then keep persistence-time rules only for safety.
        state = self.store.get_short_term_state(episode.session_id)
        candidates = self._extract_candidates(episode, harness, state=state)
        written: List[MemoryAtom] = []
        for atom in candidates:
            if self.store.get_memory_atom_by_hash(atom.hash):
                continue
            self.store.save_memory_atom(atom)
            written.append(atom)
        return written

    def _extract_candidates(self, episode: Episode, harness: MemoryHarness, state=None) -> List[MemoryAtom]:
        if not self.reasoner:
            return []
        try:
            result = self.reasoner.reason(
                episode=episode,
                harness=harness,
                existing_conversation_summary=state.conversation_summary if state else "",
                existing_task_state_summary=state.task_state_summary if state else "",
                existing_open_issues_summary=state.open_issues_summary if state else "",
            )
        except MemoryReasonerError:
            return []

        candidates: List[MemoryAtom] = []
        for raw_atom in result.memory_atoms:
            atom_type = str(raw_atom.get("type") or "semantic").strip().lower() or "semantic"
            if atom_type not in {"semantic", "project_state", "preference", "warning", "procedural"}:
                atom_type = "semantic"
            if atom_type not in harness.memory_types and atom_type != "semantic":
                atom_type = "semantic"

            content = str(raw_atom.get("content") or "").strip()
            if not content:
                continue
            if len(content) > 700:
                content = content[:700] + "..."
            if self._looks_dirty(content):
                continue

            memory_hash = self._hash(episode.session_key, atom_type, content)
            now = utc_now_iso()
            candidates.append(
                MemoryAtom(
                    id=str(uuid.uuid4()),
                    type=atom_type,
                    scope_type="project",
                    scope_id=episode.session_key or "main",
                    content=content,
                    normalized_content=content.lower(),
                    evidence_episode_ids=[episode.id],
                    entities=[str(item) for item in (raw_atom.get("entities") or [])][:12],
                    keywords=[str(item).lower() for item in (raw_atom.get("keywords") or [])][:30],
                    confidence=max(0.0, min(1.0, float(raw_atom.get("confidence") or 0.5))),
                    importance=max(0.0, min(1.0, float(raw_atom.get("importance") or 0.5))),
                    status="active",
                    hash=memory_hash,
                    metadata={
                        "harness": harness.name,
                        "source": episode.source,
                        "reasoner_model": self.reasoner.model,
                    },
                    created_at=now,
                    observed_at=episode.completed_at,
                )
            )
        return candidates

    @staticmethod
    def _hash(scope_id: str, atom_type: str, content: str) -> str:
        raw = f"{scope_id}|{atom_type}|{content.lower()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _looks_dirty(content: str) -> bool:
        # Persistence is deliberately stricter than model extraction so bad local
        # outputs do not permanently pollute the memory store.
        normalized = content.strip()
        if not normalized:
            return True
        lowered = normalized.lower()
        if lowered in {"empty", "none", "n/a", "unknown"}:
            return True
        if "\ufffd" in normalized:
            return True

        question_marks = normalized.count("?")
        if question_marks >= 6 and question_marks / max(1, len(normalized)) > 0.08:
            return True

        letters_or_cjk = re.findall(r"[A-Za-z\u4e00-\u9fff0-9]", normalized)
        if not letters_or_cjk:
            return True

        suspicious_tokens = ("???", "锟", "�")
        if any(token in normalized for token in suspicious_tokens):
            return True

        return False
