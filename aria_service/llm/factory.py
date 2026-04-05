"""
LLM Provider Factory — creates the right provider from config.
"""
from __future__ import annotations

import logging
from .provider import LLMProvider
from .openai_compat import OpenAICompatProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .hybrid import HybridProvider

logger = logging.getLogger("aria.llm.factory")


def create_llm_provider(
    provider: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.1:8b",
) -> LLMProvider | None:
    """Factory: returns the appropriate LLM provider or None."""
    p = provider.lower().strip()

    if p == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model or "claude-sonnet-4-6")

    if p == "openai":
        return OpenAICompatProvider(
            name="openai",
            api_key=api_key,
            model=model or "gpt-4",
            base_url=base_url or "https://api.openai.com/v1",
        )

    if p == "deepseek":
        return OpenAICompatProvider(
            name="deepseek",
            api_key=api_key,
            model=model or "deepseek-chat",
            base_url=base_url or "https://api.deepseek.com/v1",
        )

    if p == "gemini":
        return GeminiProvider(api_key=api_key, model=model or "gemini-3.1-pro")

    if p == "mistral":
        return OpenAICompatProvider(
            name="mistral",
            api_key=api_key,
            model=model or "mistral-large-latest",
            base_url="https://api.mistral.ai/v1",
        )

    if p == "openrouter":
        return OpenAICompatProvider(
            name="openrouter",
            api_key=api_key,
            model=model or "openrouter/auto",
            base_url="https://openrouter.ai/api/v1",
            extra_headers={
                "HTTP-Referer": "https://github.com/calesthio/Crucix",
                "X-Title": "Crucix",
            },
        )

    if p == "minimax":
        return OpenAICompatProvider(
            name="minimax",
            api_key=api_key,
            model=model or "MiniMax-M2.5",
            base_url="https://api.minimax.io/v1",
        )

    if p == "ollama":
        return OpenAICompatProvider(
            name="ollama",
            api_key="",
            model=model or ollama_model,
            base_url=f"{ollama_url.rstrip('/')}/v1",
            default_timeout=120.0,
        )

    if p == "hybrid":
        return HybridProvider(
            deepseek_key=api_key,
            deepseek_model=model or "deepseek-chat",
            ollama_model=ollama_model,
            ollama_url=ollama_url,
        )

    logger.warning(f"Unknown LLM provider: {provider}")
    return None
