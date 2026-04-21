from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import settings
from services.memory.episodes import EpisodeRecorder
from services.memory.ingestor import MemoryIngestor
from services.memory.short_term import ShortTermContextManager
from services.memory.stores import SQLiteMemoryStore


@dataclass
class MemoryRuntime:
    enabled: bool
    store: Optional[SQLiteMemoryStore] = None
    episodes: Optional[EpisodeRecorder] = None
    ingestor: Optional[MemoryIngestor] = None
    short_term: Optional[ShortTermContextManager] = None


def create_memory_runtime() -> MemoryRuntime:
    if not settings.MEMORY_ENABLED:
        return MemoryRuntime(enabled=False)
    store = SQLiteMemoryStore(settings.MEMORY_DB_PATH)
    return MemoryRuntime(
        enabled=True,
        store=store,
        episodes=EpisodeRecorder(store),
        ingestor=MemoryIngestor(store),
        short_term=ShortTermContextManager(store),
    )


memory_runtime = create_memory_runtime()
