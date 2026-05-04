from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.model_context import resolve_model_context_policy


def main() -> None:
    pro = resolve_model_context_policy("deepseek-v4-pro", "deepseek")
    flash = resolve_model_context_policy("deepseek-v4-flash", "deepseek")
    legacy = resolve_model_context_policy("deepseek-chat", "deepseek")
    kimi = resolve_model_context_policy("kimi-k2.5", "kimi")
    ollama = resolve_model_context_policy("qwen3:30b-a3b", "ollama")
    custom = resolve_model_context_policy("deepseek-future-model", "deepseek")
    assert pro.context_window == 1_000_000
    assert flash.context_window == 1_000_000
    assert legacy.context_window == 128_000
    assert kimi.context_window == 256_000
    assert ollama.context_window_source == "provider_fallback_ollama_config"
    assert pro.context_window_source == "deepseek_api_docs_v4_1m_context"
    assert pro.threshold_source == "memoflow_runtime_policy_not_provider_official"
    assert custom.context_window_source == "provider_fallback_deepseek_config"
    print(
        "model context smoke ok",
        {
            "pro": pro.to_dict(),
            "flash": flash.to_dict(),
            "legacy": legacy.to_dict(),
            "kimi": kimi.to_dict(),
            "ollama": ollama.to_dict(),
            "custom": custom.to_dict(),
        },
    )


if __name__ == "__main__":
    main()
