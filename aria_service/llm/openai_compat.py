"""
OpenAI-compatible provider — covers OpenAI, DeepSeek, Mistral, OpenRouter, MiniMax, Ollama.
All use the same /v1/chat/completions endpoint format.
"""
from __future__ import annotations

import httpx
import logging
from .provider import LLMProvider, LLMResult

logger = logging.getLogger("aria.llm.openai")


class OpenAICompatProvider(LLMProvider):
    """Generic OpenAI-compatible chat completions provider."""

    def __init__(
        self,
        *,
        name: str = "openai",
        api_key: str = "",
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        extra_headers: dict | None = None,
        default_timeout: float = 60.0,
    ):
        self.name = name
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._extra_headers = extra_headers or {}
        self._default_timeout = default_timeout

    @property
    def is_configured(self) -> bool:
        # Ollama doesn't need an API key
        if self.name == "ollama":
            return bool(self._model)
        return bool(self._api_key)

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 0,
    ) -> LLMResult:
        timeout = timeout or self._default_timeout
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})

        return LLMResult(
            text=choice.get("message", {}).get("content", ""),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=data.get("model", self._model),
        )
