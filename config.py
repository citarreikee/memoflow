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
        self.API_DESCRIPTION: str = "Minimal multi-provider ReAct conversation backend without memory processing."

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
        self.DEEPSEEK_CONTEXT_WINDOW: int = int(os.getenv("DEEPSEEK_CONTEXT_WINDOW", "128000"))
        self.KIMI_CONTEXT_WINDOW: int = int(os.getenv("KIMI_CONTEXT_WINDOW", "256000"))
        self.OLLAMA_CONTEXT_WINDOW: int = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "32768"))

        self.MEMORY_ENABLED: bool = os.getenv("MEMORY_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.MEMORY_DB_PATH: str = os.getenv("MEMORY_DB_PATH", "./data/memoflow_memory.sqlite3")
        self.MEMORY_DEFAULT_HARNESS: str = os.getenv("MEMORY_DEFAULT_HARNESS", "coding").strip() or "coding"
        self.MEMORY_COMPACTION_ENABLED: bool = os.getenv("MEMORY_COMPACTION_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.MEMORY_RETRIEVAL_TOP_K: int = int(os.getenv("MEMORY_RETRIEVAL_TOP_K", "8"))
        self.MEMORY_CONTEXT_TOKEN_BUDGET: int = int(os.getenv("MEMORY_CONTEXT_TOKEN_BUDGET", "4000"))


settings = Settings()
