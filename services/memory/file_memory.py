from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from config import settings


def load_file_memories(workspace_dir: str, user_message: str) -> List[Dict[str, str]]:
    if not settings.FILE_MEMORY_ENABLED:
        return []
    lowered = user_message.lower()
    should_try = any(keyword in lowered for keyword in ("project", "repo", "repository", "architecture", "设计", "文档"))
    if not should_try:
        return []

    base_dir = Path(workspace_dir)
    memories: List[Dict[str, str]] = []
    for candidate in settings.FILE_MEMORY_CANDIDATES[: settings.FILE_MEMORY_MAX_FILES]:
        path = base_dir / candidate
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        clipped = content[: settings.FILE_MEMORY_MAX_CHARS_PER_FILE]
        memories.append(
            {
                "path": path.name,
                "content": clipped,
            }
        )
    return memories

