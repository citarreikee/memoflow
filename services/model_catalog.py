from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from providers import deepseek, kimi, ollama


@dataclass
class ModelCatalogState:
    ollama_models_cache: List[Dict[str, Any]] = field(default_factory=list)
    ollama_cache_updated_at: float = 0.0
    ollama_refresh_task: Optional[asyncio.Task] = None
    ollama_available_cache: Optional[bool] = None


async def _refresh_ollama_models(state: ModelCatalogState) -> None:
    try:
        available = await ollama.check_ollama_available()
        if not available:
            state.ollama_available_cache = False
            return
        state.ollama_models_cache = await ollama.get_ollama_models()
        for model in state.ollama_models_cache:
            model["available"] = True
        state.ollama_cache_updated_at = time.time()
        state.ollama_available_cache = True
    except Exception as exc:
        print(f"Failed to refresh Ollama models: {exc}")
        state.ollama_available_cache = False


async def get_models_response(state: ModelCatalogState) -> Dict[str, Any]:
    all_models: List[Dict[str, Any]] = []
    providers_status: Dict[str, Any] = {}

    deepseek_task = asyncio.create_task(deepseek.get_deepseek_models())
    kimi_task = asyncio.create_task(kimi.get_kimi_models())

    if time.time() - state.ollama_cache_updated_at > 60:
        if not state.ollama_refresh_task or state.ollama_refresh_task.done():
            state.ollama_refresh_task = asyncio.create_task(_refresh_ollama_models(state))

    try:
        all_models.extend(await deepseek_task)
        providers_status["deepseek"] = {"status": "ready"}
    except Exception:
        providers_status["deepseek"] = {"status": "error"}

    try:
        all_models.extend(await kimi_task)
        providers_status["kimi"] = {"status": "ready"}
    except Exception:
        providers_status["kimi"] = {"status": "error"}

    if state.ollama_models_cache:
        all_models.extend(state.ollama_models_cache)

    if state.ollama_available_cache is True:
        providers_status["ollama"] = {"status": "ready", "cached_at": state.ollama_cache_updated_at}
    elif state.ollama_available_cache is False:
        providers_status["ollama"] = {"status": "unavailable"}
    else:
        providers_status["ollama"] = {"status": "loading"}

    if not all_models:
        raise RuntimeError("No models available from any provider.")
    return {"models": all_models, "providers": providers_status}
