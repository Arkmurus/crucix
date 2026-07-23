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
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.anthropic")

_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0  # seconds
_API_URL = "https://api.anthropic.com/v1/messages"


def _billable_input_tokens(usage: dict) -> int:
    """R-F2915 — the REAL billable input size, including cached tokens.

    Anthropic's `usage.input_tokens` is the UNCACHED REMAINDER only. The full
    prompt is:

        input_tokens + cache_creation_input_tokens + cache_read_input_tokens

    R-F2760 deliberately caches ARIA's large, stable persona/constitution
    prefix — which is exactly the portion that then disappears from
    `input_tokens`. So the more effective the cache, the MORE spend became
    invisible: cost_tracker saw only the uncached tail, and both the $300
    monthly cap and the R-F2888 daily cap were computed from that. Live on
    2026-07-23 the ledger reported $8.56 of Anthropic spend while the provider
    console showed roughly twice that.

    Cached tokens are not free, they are DISCOUNTED, so they are weighted at
    Anthropic's published multipliers rather than counted flat:
      * cache WRITE  ~1.25x the base input rate
      * cache READ   ~0.10x the base input rate
    Returning a weighted token count lets the existing per-model pricing table
    stay unchanged while the resulting USD tracks the real bill.
    """
    def _n(key: str) -> int:
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            # A malformed count must never crash a completed LLM call, and must
            # never silently read as 0 spend either — log it and move on.
            logger.warning("[anthropic] non-numeric usage.%s: %r", key, usage.get(key))
            return 0

    return (
        _n("input_tokens")
        + int(round(_n("cache_creation_input_tokens") * 1.25))
        + int(round(_n("cache_read_input_tokens") * 0.10))
    )


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

    def _payload(self, system_prompt: str, user_message: str, max_tokens: int,
                 model: str | None = None) -> dict:
        # R-F2760 — prompt caching. Send the system prompt as a cacheable text block
        # (Anthropic Messages API accepts `system` as a string OR a list of blocks) so
        # ARIA's large, stable persona/constitution prefix is cached. Cache reads bill
        # at ~0.1x input; on the DeepSeek->Claude switch this cuts the dominant input
        # cost ~10x whenever the same prefix recurs within the 5-min TTL (high hit-rate
        # under real chat load). Below the model's minimum cacheable prefix it silently
        # no-ops — additive and safe. Prompt caching is GA (no beta header). The writers
        # path (_resilient_llm.py) already uses this pattern.
        # R-F2768 — per-call model override for Claude-era model routing: use the
        # routed Claude model when the caller supplied one, else this provider's
        # configured model. Guarding on the "claude" prefix means a routed id
        # meant for a different provider can never mis-target Anthropic.
        _eff_model = model if (model and str(model).startswith("claude")) else self._model
        return {
            "model": _eff_model,
            "max_tokens": max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
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

    @fail_wire(module="anthropic", gap_type="engine_failure", control_flow_exempt=("ProviderError",))
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        model: str | None = None,   # R-F2768 — per-call Claude model override
    ) -> LLMResult:
        payload = self._payload(system_prompt, user_message, max_tokens, model=model)
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
                        input_tokens=_billable_input_tokens(usage),
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

        # R-F1059 — wire Anthropic failure to brain
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="llm_anthropic",
                detail=f"Anthropic all retries exhausted: {last_error}",
                gap_type="llm_provider_failure",
                source="llm_anthropic",
            )
        except Exception:
            pass
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
        model: str | None = None,   # R-F2768 — per-call Claude model override
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as Anthropic generates them.

        Uses the Messages API with stream=true. Parses SSE events:
        - content_block_delta → yields delta.text
        - message_delta → captures final usage
        - message_stop → stream complete

        on_done(LLMResult) is called after the stream finishes with
        final token counts and model info for cost tracking.
        """
        payload = {**self._payload(system_prompt, user_message, max_tokens, model=model), "stream": True}
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
                            try:
                                _body = resp.text
                            except Exception:
                                _body = ""
                            raise ProviderError.from_http_status(self.name, resp.status_code, _body[:300])

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
                                    # R-F2915 — same cached-token blindness as the
                                    # non-streaming path; message_start carries the
                                    # cache_creation/cache_read counts too.
                                    input_tokens = _billable_input_tokens(usage)

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
