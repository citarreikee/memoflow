from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional

from config import settings


@dataclass(frozen=True)
class ModelContextPolicy:
    model: str
    provider: str
    context_window: int
    context_window_source: str
    budget_ratio: float
    token_budget: int
    threshold_source: str
    thresholds: Dict[str, float]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# Context windows are model capability facts. Keep policy thresholds separate.
# Only encode defaults when they have a stable public source or are explicit config.
MODEL_CONTEXT_WINDOWS: Dict[str, tuple[int, str]] = {
    "deepseek-v4-pro": (1_000_000, "deepseek_api_docs_v4_1m_context"),
    "deepseek-v4-flash": (1_000_000, "deepseek_api_docs_v4_1m_context"),
    "deepseek-v4": (1_000_000, "deepseek_api_docs_v4_1m_context"),
    "deepseek-chat": (128_000, "deepseek_api_docs_legacy_alias_128k_context"),
    "deepseek-reasoner": (128_000, "deepseek_api_docs_legacy_alias_128k_context"),
    "kimi-k2.5": (256_000, "moonshot_api_docs_kimi_k2_5_256k_context"),
    "kimi-thinking-preview": (256_000, "moonshot_api_docs_kimi_256k_context"),
    "kimi-latest": (256_000, "moonshot_api_docs_kimi_256k_context"),
    "kimi-k2-turbo-preview": (256_000, "moonshot_api_docs_kimi_256k_context"),
}


def resolve_model_context_policy(model: str, provider: str) -> ModelContextPolicy:
    context_window, source = _resolve_context_window(model=model, provider=provider)
    budget_ratio = settings.CONTEXT_BUDGET_RATIO if settings.CONTEXT_BUDGET_RATIO > 0 else 0.75
    configured_budget = settings.CONTEXT_TOKEN_BUDGET
    token_budget = min(context_window, configured_budget) if configured_budget > 0 else int(context_window * budget_ratio)
    token_budget = max(1024, min(context_window, token_budget))
    return ModelContextPolicy(
        model=model,
        provider=provider,
        context_window=context_window,
        context_window_source=source,
        budget_ratio=budget_ratio,
        token_budget=token_budget,
        threshold_source="memoflow_runtime_policy_not_provider_official",
        thresholds={
            "watch_ratio": 0.70,
            "prepare_compact_ratio": settings.CONTEXT_COMPACTION_TRIGGER_RATIO,
            "emergency_ratio": 0.95,
            "hard_fail_ratio": 1.0,
            "min_user_turns": float(settings.CONTEXT_COMPACTION_MIN_USER_TURNS),
            "keep_recent_turns": float(settings.CONTEXT_COMPACTION_KEEP_RECENT_TURNS),
        },
    )


def _resolve_context_window(model: str, provider: str) -> tuple[int, str]:
    configured = _configured_model_context_window(model)
    if configured:
        return configured, "env_model_context_window"
    configured_map = _configured_model_context_windows()
    normalized_model = model.strip().lower()
    if normalized_model in configured_map:
        return configured_map[normalized_model], "env_model_context_windows_map"
    if normalized_model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[normalized_model]
    if provider == "deepseek":
        return settings.DEEPSEEK_CONTEXT_WINDOW, "provider_fallback_deepseek_config"
    if provider == "kimi":
        return settings.KIMI_CONTEXT_WINDOW, "provider_fallback_kimi_config"
    return settings.OLLAMA_CONTEXT_WINDOW, "provider_fallback_ollama_config"


def _configured_model_context_window(model: str) -> Optional[int]:
    env_key = "MODEL_CONTEXT_WINDOW_" + _normalize_env_model_name(model)
    value = getattr(settings, env_key, None)
    if value is None:
        import os

        value = os.getenv(env_key)
    if not value:
        return None
    try:
        parsed = int(str(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _configured_model_context_windows() -> Dict[str, int]:
    result: Dict[str, int] = {}
    raw = settings.MODEL_CONTEXT_WINDOWS.strip()
    if not raw:
        return result
    for item in raw.split(","):
        if not item.strip() or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip().lower()
        if not name:
            continue
        try:
            parsed = int(value.strip())
        except ValueError:
            continue
        if parsed > 0:
            result[name] = parsed
    return result


def _normalize_env_model_name(model: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in model.strip().upper()).strip("_")
