from __future__ import annotations

import hashlib
import re
import uuid
from typing import List

from services.memory.harness import MemoryHarness
from services.memory.schemas import Episode, MemoryAtom, utc_now_iso
from services.memory.stores import SQLiteMemoryStore


class MemoryIngestor:
    """Conservative ADD-only ingestor for v0.1.

    This is intentionally rule-gated. It writes only evidence-backed atoms when
    the turn contains explicit memory-worthy signals.
    """

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self.store = store

    def ingest_episode(self, episode: Episode, harness: MemoryHarness) -> List[MemoryAtom]:
        candidates = self._extract_candidates(episode, harness)
        written: List[MemoryAtom] = []
        for atom in candidates:
            if self.store.get_memory_atom_by_hash(atom.hash):
                continue
            self.store.save_memory_atom(atom)
            written.append(atom)
        return written

    def _extract_candidates(self, episode: Episode, harness: MemoryHarness) -> List[MemoryAtom]:
        text = f"{episode.user_message}\n{episode.assistant_answer}".strip()
        if not self._is_memory_worthy(text):
            return []

        atom_type = self._classify(text)
        if atom_type not in harness.memory_types and atom_type != "semantic":
            atom_type = "semantic"

        content = self._normalize_content(text)
        if not content:
            return []

        memory_hash = self._hash(episode.session_key, atom_type, content)
        now = utc_now_iso()
        return [
            MemoryAtom(
                id=str(uuid.uuid4()),
                type=atom_type,
                scope_type="project",
                scope_id=episode.session_key or "main",
                content=content,
                normalized_content=content.lower(),
                evidence_episode_ids=[episode.id],
                entities=self._extract_entities(text),
                keywords=self._extract_keywords(text),
                confidence=0.65,
                importance=0.6 if atom_type != "warning" else 0.8,
                status="active",
                hash=memory_hash,
                metadata={"harness": harness.name, "source": episode.source},
                created_at=now,
                observed_at=episode.completed_at,
            )
        ]

    @staticmethod
    def _is_memory_worthy(text: str) -> bool:
        lowered = text.lower()
        markers = (
            "remember",
            "记住",
            "决定",
            "约定",
            "规则",
            "不要",
            "必须",
            "路径",
            "workdir",
            "working directory",
            "error",
            "failed",
            "失败",
            "错误",
            "blocked",
            "已完成",
            "implemented",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _classify(text: str) -> str:
        lowered = text.lower()
        if any(marker in lowered for marker in ("error", "failed", "失败", "错误", "blocked", "不要")):
            return "warning"
        if any(marker in lowered for marker in ("how to", "步骤", "流程", "procedure", "workflow")):
            return "procedural"
        if any(marker in lowered for marker in ("path", "路径", "workdir", "已完成", "implemented", "决定")):
            return "project_state"
        if any(marker in lowered for marker in ("prefer", "偏好", "习惯")):
            return "preference"
        return "semantic"

    @staticmethod
    def _normalize_content(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        content = " ".join(lines)
        content = re.sub(r"\s+", " ", content).strip()
        if len(content) > 700:
            content = content[:700] + "..."
        return content

    @staticmethod
    def _extract_entities(text: str) -> List[str]:
        entities = set(re.findall(r"[A-Za-z]:\\[^\s`]+|`([^`]+)`", text))
        flattened = set()
        for item in entities:
            if isinstance(item, tuple):
                flattened.update(part for part in item if part)
            elif item:
                flattened.add(item)
        return sorted(flattened)[:12]

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        keywords = set()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text):
            if len(token) <= 40:
                keywords.add(token.lower())
        return sorted(keywords)[:30]

    @staticmethod
    def _hash(scope_id: str, atom_type: str, content: str) -> str:
        raw = f"{scope_id}|{atom_type}|{content.lower()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
