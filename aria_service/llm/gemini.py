"""
Google Gemini provider.
Uses the REST generateContent API.
"""
from __future__ import annotations

import httpx
import logging
from .provider import LLMProvider, LLMResult, ProviderError
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

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

    @fail_wire(module="gemini", gap_type="engine_failure", control_flow_exempt=("ProviderError",))
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

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    raise ProviderError.from_http_status(self.name, resp.status_code, resp.text[:300])
                data = resp.json()
        except ProviderError:
            raise
        except httpx.TimeoutException as e:
            raise ProviderError(self.name, "timeout", kind="timeout", retryable=True, cause=e)
        except httpx.HTTPError as e:
            raise ProviderError(self.name, f"network error: {e}", kind="other", retryable=True, cause=e)

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
