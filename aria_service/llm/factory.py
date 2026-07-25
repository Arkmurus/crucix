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
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring
from ..intel.engine_wiring import wire_success, wire_failure  # R-F2489 §21a success+failure

logger = logging.getLogger("aria.llm.factory")


@fail_wire(module="factory", gap_type="engine_failure")
def create_llm_provider(
    provider: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.1:8b",
) -> LLMProvider | None:
    """Factory: returns the appropriate LLM provider or None.

    R-F2489 §21a: both branches reach the brain — a created provider emits
    wire_success; an unknown-provider (soft None return, which ``fail_wire``
    cannot catch because nothing is raised) emits wire_failure.
    """
    prov = _build_provider(
        provider, api_key=api_key, model=model, base_url=base_url,
        ollama_url=ollama_url, ollama_model=ollama_model,
    )
    if prov is None:
        wire_failure(
            module="llm_factory",
            detail=f"unknown LLM provider requested: {provider!r}",
            gap_type="engine_failure", source="llm_factory",
        )
    else:
        wire_success(module="llm_factory", summary=f"created {provider} provider")
    return prov


def _build_provider(
    provider: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.1:8b",
) -> LLMProvider | None:
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
        # R-F3032 — never hardcode a DeepSeek model id here; `deepseek-chat`
        # was retired upstream and this default silently kept it alive.
        from .openai_compat import default_deepseek_model
        return OpenAICompatProvider(
            name="deepseek",
            api_key=api_key,
            model=model or default_deepseek_model(),
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

    if p == "groq":
        # Groq — OpenAI-compatible, very fast inference, generous free tier
        # (~14,400 requests/day on Llama-3.1-70B). Widens the fallback chain
        # so ARIA stays up even if DeepSeek + Anthropic both go down at once.
        # Added 2026-04-17 as part of the "forever on" resilience pass.
        return OpenAICompatProvider(
            name="groq",
            api_key=api_key,
            model=model or "llama-3.3-70b-versatile",
            base_url="https://api.groq.com/openai/v1",
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
        from .openai_compat import default_deepseek_model  # R-F3032
        return HybridProvider(
            deepseek_key=api_key,
            deepseek_model=model or default_deepseek_model(),
            ollama_model=ollama_model,
            ollama_url=ollama_url,
        )

    logger.warning(f"Unknown LLM provider: {provider}")
    return None
