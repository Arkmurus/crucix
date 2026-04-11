"""
Anthropic Claude provider.
Uses the Messages API directly (no SDK dependency).
Handles 429 rate limits with exponential backoff + Retry-After header.
Supports both batch (complete) and streaming (stream) modes.
"""
from __future__ import annotations

import asyncio
import json as _json
import httpx
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Optional

from .provider import LLMProvider, LLMResult, ProviderError

logger = logging.getLogger("aria.llm.anthropic")

_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0  # seconds
_API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str = "", model: str = "claude-sonnet-4-6"):
        self._api_key = api_key
        self._model = model

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

    def _payload(self, system_prompt: str, user_message: str, max_tokens: int) -> dict:
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

    async def _handle_rate_limit(self, resp: httpx.Response, attempt: int) -> Exception:
        """Handle 429/529 responses with backoff. Returns the error for retry tracking."""
        code = resp.status_code
        if code == 429:
            retry_after = resp.headers.get("retry-after")
            wait = min(float(retry_after), 30.0) if retry_after else _BASE_BACKOFF * (2 ** attempt)
            label = "429 rate limited"
        else:
            wait = _BASE_BACKOFF * (2 ** attempt)
            label = "529 overloaded"

        logger.warning(
            "Anthropic %s (attempt %d/%d), waiting %.1fs",
            label, attempt + 1, _MAX_RETRIES, wait,
        )
        await asyncio.sleep(wait)
        return httpx.HTTPStatusError(
            f"{code} {label}", request=resp.request, response=resp,
        )

    # ── Non-streaming (unchanged behaviour) ──────────────────────────

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult:
        payload = self._payload(system_prompt, user_message, max_tokens)
        last_error = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(_API_URL, headers=self._headers(), json=payload)

                    if resp.status_code in (429, 529):
                        last_error = await self._handle_rate_limit(resp, attempt)
                        continue

                    if resp.status_code >= 400:
                        raise ProviderError.from_http_status(self.name, resp.status_code, resp.text[:300])
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

            except ProviderError:
                raise
            except httpx.TimeoutException as e:
                raise ProviderError(self.name, "timeout", kind="timeout", retryable=True, cause=e)
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _BASE_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "Anthropic request failed (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, str(e)[:100], wait,
                    )
                    await asyncio.sleep(wait)

        raise last_error or RuntimeError("Anthropic provider: all retries exhausted")

    # ── Streaming — token-by-token SSE ───────────────────────────────

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done: "Optional[Callable]" = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as Anthropic generates them.

        Uses the Messages API with stream=true. Parses SSE events:
        - content_block_delta → yields delta.text
        - message_delta → captures final usage
        - message_stop → stream complete

        on_done(LLMResult) is called after the stream finishes with
        final token counts and model info for cost tracking.
        """
        payload = {**self._payload(system_prompt, user_message, max_tokens), "stream": True}
        last_error = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(
                    connect=10.0, read=timeout, write=10.0, pool=10.0,
                )) as client:
                    async with client.stream(
                        "POST", _API_URL, headers=self._headers(), json=payload,
                    ) as resp:

                        if resp.status_code in (429, 529):
                            await resp.aread()
                            last_error = await self._handle_rate_limit(resp, attempt)
                            continue

                        if resp.status_code >= 400:
                            await resp.aread()
                            raise ProviderError.from_http_status(self.name, resp.status_code)

                        # Parse the SSE event stream
                        full_text = ""
                        input_tokens = 0
                        output_tokens = 0
                        model = self._model
                        buf = ""

                        async for raw_bytes in resp.aiter_text():
                            buf += raw_bytes
                            while "\n" in buf:
                                line, buf = buf.split("\n", 1)
                                line = line.strip()
                                if not line or line.startswith(":"):
                                    continue
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:]
                                if data_str == "[DONE]":
                                    break

                                try:
                                    event = _json.loads(data_str)
                                except _json.JSONDecodeError:
                                    continue

                                etype = event.get("type", "")

                                if etype == "message_start":
                                    msg = event.get("message", {})
                                    model = msg.get("model", self._model)
                                    usage = msg.get("usage", {})
                                    input_tokens = usage.get("input_tokens", 0)

                                elif etype == "content_block_delta":
                                    delta = event.get("delta", {})
                                    chunk = delta.get("text", "")
                                    if chunk:
                                        full_text += chunk
                                        yield chunk

                                elif etype == "message_delta":
                                    usage = event.get("usage", {})
                                    output_tokens = usage.get("output_tokens", 0)

                        # Stream complete — fire callback with final stats
                        if on_done:
                            on_done(LLMResult(
                                text=full_text,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                model=model,
                            ))
                        return  # success — exit retry loop

            except ProviderError:
                raise
            except httpx.TimeoutException as e:
                raise ProviderError(self.name, "stream timeout", kind="timeout", retryable=True, cause=e)
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _BASE_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "Anthropic stream failed (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, str(e)[:100], wait,
                    )
                    await asyncio.sleep(wait)

        raise last_error or RuntimeError("Anthropic stream: all retries exhausted")
