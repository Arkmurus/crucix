"""
Anthropic Claude provider.
Uses the Messages API directly (no SDK dependency).
"""
from __future__ import annotations

import httpx
import logging
from .provider import LLMProvider, LLMResult

logger = logging.getLogger("aria.llm.anthropic")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str = "", model: str = "claude-sonnet-4-6"):
        self._api_key = api_key
        self._model = model

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        usage = data.get("usage", {})
        return LLMResult(
            text=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            model=data.get("model", self._model),
        )
