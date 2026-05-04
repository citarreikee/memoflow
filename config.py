"""Configuration for the Memoflow conversation backend."""

import os
from typing import List

from dotenv import load_dotenv


load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("PORT", "3001"))
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

        self.CORS_ORIGINS: List[str] = []
        self.CORS_ALLOW_ORIGIN_REGEX: str = ""
        if self.ENVIRONMENT == "production":
            origins = os.getenv("CORS_ORIGINS", "")
            self.CORS_ORIGINS = [origin.strip() for origin in origins.split(",") if origin.strip()]
        else:
            self.CORS_ALLOW_ORIGIN_REGEX = (
                r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3})(:\d+)?$"
            )

        self.API_TITLE: str = "Memoflow Conversation API"
        self.API_VERSION: str = "0.1.0"
        self.API_DESCRIPTION: str = "Multi-provider ReAct conversation backend."

        self.SYSTEM_PROMPT: str = os.getenv("SYSTEM_PROMPT", "").strip()
        self.DEVELOPER_PROMPT: str = os.getenv("DEVELOPER_PROMPT", "").strip()

        self.OLLAMA_API_BASE: str = os.getenv("OLLAMA_API_BASE", "http://localhost:11434").rstrip("/")
        self.DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
        self.DEEPSEEK_API_BASE: str = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1").rstrip("/")
        self.KIMI_API_KEY: str = os.getenv("KIMI_API_KEY", "")
        self.KIMI_API_BASE: str = os.getenv("KIMI_API_BASE", "https://api.moonshot.cn/v1").rstrip("/")
        self.KIMI_MODELS: str = os.getenv("KIMI_MODELS", "")

        self.CONTEXT_MAX_USER_TURNS: int = int(os.getenv("CONTEXT_MAX_USER_TURNS", "8"))
        self.CONTEXT_BUDGET_RATIO: float = float(os.getenv("CONTEXT_BUDGET_RATIO", "0.75"))
        self.CONTEXT_TOKEN_BUDGET: int = int(os.getenv("CONTEXT_TOKEN_BUDGET", "0"))
        self.CONTEXT_TOOL_RESULT_MAX_CHARS: int = int(os.getenv("CONTEXT_TOOL_RESULT_MAX_CHARS", "2000"))
        self.CONTEXT_COMPACTION_TRIGGER_RATIO: float = float(os.getenv("CONTEXT_COMPACTION_TRIGGER_RATIO", "0.82"))
        self.CONTEXT_COMPACTION_MIN_USER_TURNS: int = int(os.getenv("CONTEXT_COMPACTION_MIN_USER_TURNS", "8"))
        self.CONTEXT_COMPACTION_KEEP_RECENT_TURNS: int = int(os.getenv("CONTEXT_COMPACTION_KEEP_RECENT_TURNS", "4"))
        self.CONTEXT_CHECKPOINT_TARGET_TOKENS: int = int(os.getenv("CONTEXT_CHECKPOINT_TARGET_TOKENS", "900"))
        self.CONTEXT_CHECKPOINT_HARD_MAX_TOKENS: int = int(os.getenv("CONTEXT_CHECKPOINT_HARD_MAX_TOKENS", "1400"))
        self.MEMORY_DATA_DIR: str = os.getenv("MEMORY_DATA_DIR", "data").strip() or "data"
        self.FILE_MEMORY_ENABLED: bool = os.getenv("FILE_MEMORY_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.FILE_MEMORY_CANDIDATES: List[str] = [
            item.strip()
            for item in os.getenv("FILE_MEMORY_CANDIDATES", "CLAUDE.md,MEMORY.md,README.md").split(",")
            if item.strip()
        ]
        self.FILE_MEMORY_MAX_FILES: int = int(os.getenv("FILE_MEMORY_MAX_FILES", "2"))
        self.FILE_MEMORY_MAX_CHARS_PER_FILE: int = int(os.getenv("FILE_MEMORY_MAX_CHARS_PER_FILE", "4000"))
        self.SIDECAR_COMPACTION_ENABLED: bool = os.getenv("SIDECAR_COMPACTION_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.SIDECAR_COMPACTION_PROVIDER: str = os.getenv("SIDECAR_COMPACTION_PROVIDER", "ollama").strip() or "ollama"
        self.SIDECAR_COMPACTION_MODEL: str = os.getenv("SIDECAR_COMPACTION_MODEL", "qwen3:30b-a3b").strip()
        self.SIDECAR_COMPACTION_TIMEOUT_SECONDS: float = float(
            os.getenv("SIDECAR_COMPACTION_TIMEOUT_SECONDS", "120")
        )
        self.SIDECAR_COMPACTION_NUM_PREDICT: int = int(os.getenv("SIDECAR_COMPACTION_NUM_PREDICT", "800"))
        self.DEEPSEEK_CONTEXT_WINDOW: int = int(os.getenv("DEEPSEEK_CONTEXT_WINDOW", "128000"))
        self.KIMI_CONTEXT_WINDOW: int = int(os.getenv("KIMI_CONTEXT_WINDOW", "256000"))
        self.OLLAMA_CONTEXT_WINDOW: int = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "32768"))


settings = Settings()
