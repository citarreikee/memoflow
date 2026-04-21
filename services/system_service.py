from __future__ import annotations

from typing import Any, Dict, List

from config import settings
from providers import deepseek, kimi, ollama, react_orchestrator


async def get_health_payload() -> Dict[str, Any]:
    ollama_available = await ollama.check_ollama_available()
    deepseek_available = await deepseek.check_deepseek_available()
    kimi_available = await kimi.check_kimi_available()
    return {
        "status": "healthy",
        "providers": {
            "ollama": "healthy" if ollama_available else "unavailable",
            "deepseek": "healthy" if deepseek_available else "unavailable",
            "kimi": "healthy" if kimi_available else "unavailable",
        },
        "version": settings.API_VERSION,
    }


async def get_tools_payload() -> Dict[str, Any]:
    return {"tools": await react_orchestrator.get_available_tools()}


async def get_api_info_payload() -> Dict[str, Any]:
    all_models: List[Dict[str, Any]] = []
    for fetch_models in (ollama.get_ollama_models, deepseek.get_deepseek_models, kimi.get_kimi_models):
        try:
            all_models.extend(await fetch_models())
        except Exception:
            pass

    return {
        "api_version": settings.API_VERSION,
        "description": settings.API_DESCRIPTION,
        "providers": {
            "ollama": {"available": await ollama.check_ollama_available()},
            "deepseek": {"available": await deepseek.check_deepseek_available()},
            "kimi": {"available": await kimi.check_kimi_available()},
        },
        "total_models": len(all_models),
        "models": [model["name"] for model in all_models],
        "memory_processing": "enabled" if settings.MEMORY_ENABLED else "disabled",
    }
