"""
Google Gemini provider.
Uses the REST generateContent API.
"""
from __future__ import annotations

import httpx
import logging
from .provider import LLMProvider, LLMResult

logger = logging.getLogger("aria.llm.gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str = "", model: str = "gemini-3.1-pro"):
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
        url = f"{BASE}/{self._model}:generateContent?key={self._api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = parts[0].get("text", "") if parts else ""

        meta = data.get("usageMetadata", {})
        return LLMResult(
            text=text,
            input_tokens=meta.get("promptTokenCount", 0),
            output_tokens=meta.get("candidatesTokenCount", 0),
            model=self._model,
        )
