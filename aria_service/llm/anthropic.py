"""
Anthropic Claude provider.
Uses the Messages API directly (no SDK dependency).
Handles 429 rate limits with exponential backoff + Retry-After header.
"""
from __future__ import annotations

import asyncio
import httpx
import logging
from .provider import LLMProvider, LLMResult

logger = logging.getLogger("aria.llm.anthropic")

_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0  # seconds


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

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers,
                        json=payload,
                    )

                    # Handle 429 rate limit with backoff
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("retry-after")
                        if retry_after:
                            wait = min(float(retry_after), 30.0)
                        else:
                            wait = _BASE_BACKOFF * (2 ** attempt)
                        logger.warning(
                            "Anthropic 429 rate limited (attempt %d/%d), waiting %.1fs",
                            attempt + 1, _MAX_RETRIES, wait,
                        )
                        await asyncio.sleep(wait)
                        last_error = httpx.HTTPStatusError(
                            f"429 Too Many Requests",
                            request=resp.request,
                            response=resp,
                        )
                        continue

                    # Handle 529 overloaded
                    if resp.status_code == 529:
                        wait = _BASE_BACKOFF * (2 ** attempt)
                        logger.warning(
                            "Anthropic 529 overloaded (attempt %d/%d), waiting %.1fs",
                            attempt + 1, _MAX_RETRIES, wait,
                        )
                        await asyncio.sleep(wait)
                        last_error = httpx.HTTPStatusError(
                            f"529 Overloaded",
                            request=resp.request,
                            response=resp,
                        )
                        continue

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

            except httpx.HTTPStatusError:
                raise  # non-retryable HTTP errors (401, 404, etc.)
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _BASE_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "Anthropic request failed (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, str(e)[:100], wait,
                    )
                    await asyncio.sleep(wait)

        # All retries exhausted
        raise last_error or RuntimeError("Anthropic provider: all retries exhausted")
