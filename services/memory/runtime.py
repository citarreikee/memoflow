from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import settings
from services.memory.assembler import ContextAssembler
from services.memory.episodes import EpisodeRecorder
from services.memory.ingestor import MemoryIngestor
from services.memory.reasoner import MemoryReasoner
from services.memory.retriever import MemoryRetriever
from services.memory.short_term import ShortTermContextManager
from services.memory.stores import SQLiteMemoryStore


@dataclass
class MemoryRuntime:
    enabled: bool
    store: Optional[SQLiteMemoryStore] = None
    episodes: Optional[EpisodeRecorder] = None
    reasoner: Optional[MemoryReasoner] = None
    ingestor: Optional[MemoryIngestor] = None
    short_term: Optional[ShortTermContextManager] = None
    retriever: Optional[MemoryRetriever] = None
    assembler: Optional[ContextAssembler] = None


def create_memory_runtime() -> MemoryRuntime:
    if not settings.MEMORY_ENABLED:
        return MemoryRuntime(enabled=False)

    # Runtime wiring for the current usable memory path:
    # episode recorder -> sidecar reasoner -> short-term manager / ingestor ->
    # retriever -> assembler.
    store = SQLiteMemoryStore(settings.MEMORY_DB_PATH)
    reasoner = MemoryReasoner()
    return MemoryRuntime(
        enabled=True,
        store=store,
        episodes=EpisodeRecorder(store),
        reasoner=reasoner,
        ingestor=MemoryIngestor(store, reasoner),
        short_term=ShortTermContextManager(store, reasoner),
        retriever=MemoryRetriever(store),
        assembler=ContextAssembler(),
    )


memory_runtime = create_memory_runtime()
