from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import settings
from services.memory.harness import MemoryHarness
from services.memory.schemas import RetrievalEvent, ShortTermState, utc_now_iso
from services.memory.stores import SQLiteMemoryStore


@dataclass
class RetrievedMemoryAtom:
    atom: Dict[str, object]
    score: float
    reasons: List[str]


@dataclass
class RetrievalBundle:
    session_id: str
    session_key: str
    harness: str
    query: str
    short_term_state: Optional[ShortTermState]
    warnings: List[RetrievedMemoryAtom]
    project_state: List[RetrievedMemoryAtom]
    procedures: List[RetrievedMemoryAtom]
    semantic: List[RetrievedMemoryAtom]

    @property
    def all_items(self) -> List[RetrievedMemoryAtom]:
        return self.warnings + self.project_state + self.procedures + self.semantic


class MemoryRetriever:
    def __init__(self, store: SQLiteMemoryStore) -> None:
        self.store = store

    def retrieve(
        self,
        *,
        session_id: str,
        session_key: str,
        query: str,
        harness: MemoryHarness,
        limit: Optional[int] = None,
    ) -> RetrievalBundle:
        # v0.1 retrieval is usable but intentionally simple: it loads the current
        # short-term state, then ranks stored atoms by lexical overlap, harness
        # type priority, and confidence/importance.
        top_k = max(1, limit or settings.MEMORY_RETRIEVAL_TOP_K)
        short_term_state = self.store.get_short_term_state(session_id)
        candidate_atoms = self.store.list_memory_atoms(scope_type="project", scope_id=session_key, limit=500)
        ranked = self._rank_atoms(candidate_atoms, query=query, harness=harness, top_k=top_k)
        categorized = self._categorize(ranked, harness)
        return RetrievalBundle(
            session_id=session_id,
            session_key=session_key,
            harness=harness.name,
            query=query,
            short_term_state=short_term_state,
            warnings=categorized["warning"],
            project_state=categorized["project_state"],
            procedures=categorized["procedural"],
            semantic=categorized["semantic"],
        )

    def save_event(self, bundle: RetrievalBundle, assembled_context_preview: str) -> RetrievalEvent:
        # Retrieval events make prompt injection observable: we persist which
        # atoms were loaded, their scores, and the assembled preview that was
        # attached to the next chat turn.
        event = RetrievalEvent(
            id=str(uuid.uuid4()),
            session_id=bundle.session_id,
            query=bundle.query,
            harness=bundle.harness,
            retrieved_atom_ids=[str(item.atom.get("id")) for item in bundle.all_items],
            scores={str(item.atom.get("id")): round(item.score, 4) for item in bundle.all_items},
            assembled_context_preview=assembled_context_preview,
            created_at=utc_now_iso(),
        )
        self.store.save_retrieval_event(event)
        return event

    @staticmethod
    def _rank_atoms(
        atoms: List[Dict[str, object]],
        *,
        query: str,
        harness: MemoryHarness,
        top_k: int,
    ) -> List[RetrievedMemoryAtom]:
        query_terms = [term for term in query.lower().split() if term]
        scored: List[RetrievedMemoryAtom] = []

        for atom in atoms:
            atom_type = str(atom.get("type") or "semantic")
            reasons: List[str] = []
            score = 0.0

            if atom_type in harness.retrieval_priority:
                priority_index = harness.retrieval_priority.index(atom_type)
                score += max(0.0, 1.25 - (priority_index * 0.15))
                reasons.append(f"priority:{atom_type}")

            content = str(atom.get("content") or "").lower()
            normalized = str(atom.get("normalized_content") or "").lower()
            keywords = [str(keyword).lower() for keyword in atom.get("keywords") or []]
            entities = [str(entity).lower() for entity in atom.get("entities") or []]

            matched_terms = 0
            for term in query_terms:
                if term in content or term in normalized:
                    matched_terms += 1
                elif any(term in keyword for keyword in keywords):
                    matched_terms += 1
                elif any(term in entity for entity in entities):
                    matched_terms += 1
            if matched_terms:
                lexical_score = min(1.5, matched_terms / max(1.0, math.sqrt(len(query_terms))))
                score += lexical_score
                reasons.append(f"lexical:{matched_terms}")

            confidence = float(atom.get("confidence") or 0.5)
            importance = float(atom.get("importance") or 0.5)
            score += confidence * 0.6
            score += importance * 0.8
            reasons.append(f"quality:{round(confidence, 2)}/{round(importance, 2)}")

            if score <= 0:
                continue
            scored.append(RetrievedMemoryAtom(atom=atom, score=score, reasons=reasons))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    @staticmethod
    def _categorize(
        ranked: List[RetrievedMemoryAtom],
        harness: MemoryHarness,
    ) -> Dict[str, List[RetrievedMemoryAtom]]:
        buckets: Dict[str, List[RetrievedMemoryAtom]] = {
            "warning": [],
            "project_state": [],
            "procedural": [],
            "semantic": [],
        }
        for item in ranked:
            atom_type = str(item.atom.get("type") or "semantic")
            if atom_type not in buckets:
                atom_type = "semantic"
            if atom_type not in harness.memory_types and atom_type != "semantic":
                atom_type = "semantic"
            buckets[atom_type].append(item)
        return buckets
