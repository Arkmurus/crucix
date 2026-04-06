"""
ARIA Service — Configuration
All settings driven by environment variables.
"""
import os
from typing import List

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ── LLM ──────────────────────────────────────────────────────────────────
    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    llm_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="llama3.1:8b", alias="OLLAMA_MODEL")

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")

    # ── Server ───────────────────────────────────────────────────────────────
    # Reads ARIA_PORT first, then falls back to PORT (Render sets PORT dynamically)
    port: int = Field(default=8000, alias="ARIA_PORT")
    host: str = Field(default="0.0.0.0", alias="ARIA_HOST")

    @property
    def effective_port(self) -> int:
        import os
        return int(os.getenv("PORT", str(self.port)))

    # ── Auth ─────────────────────────────────────────────────────────────────
    api_key: str = Field(default="", alias="ARIA_API_KEY")
    admin_key: str = Field(default="", alias="ARIA_ADMIN_KEY")

    # ── Priority Markets ─────────────────────────────────────────────────────
    priority_markets: List[str] = [
        "Angola", "Mozambique", "Guinea-Bissau", "Cape Verde",
        "São Tomé and Príncipe", "Nigeria", "Kenya", "Ghana",
        "Senegal", "Ivory Coast", "Morocco", "Tanzania",
    ]

    # ── Fallback LLM keys (optional — enables auto-failover) ──────────────
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")

    model_config = {"env_file": ".env", "extra": "ignore", "populate_by_name": True}


settings = Settings()
# v2.1.0
