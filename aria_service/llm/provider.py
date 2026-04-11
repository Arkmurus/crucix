"""
LLM Provider — Base class and factory.
All providers expose the same interface: complete(system, user, opts) → LLMResult.
Streaming: stream(system, user, opts) → AsyncGenerator[str] for token-by-token output.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.llm")


class ProviderError(Exception):
    """Structured LLM-provider error. Carries provider name, HTTP status
    if applicable, a short kind (billing / rate_limit / auth / server /
    timeout / other), and the original cause. Used so the fallback chain
    and route handlers can reason about failures without inspecting raw
    httpx/anthropic exceptions, and so user-facing code never leaks an
    API vendor URL.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int | None = None,
        kind: str = "other",
        retryable: bool = True,
        cause: Exception | None = None,
    ):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message
        self.status = status
        self.kind = kind
        self.retryable = retryable
        self.__cause__ = cause

    @classmethod
    def from_http_status(cls, provider: str, status: int, body: str = "", cause: Exception | None = None) -> "ProviderError":
        # Body-sniffing: some providers return billing failures as HTTP 400
        # with the reason inside the JSON error.message. Example: Anthropic
        # returns 400 {"type":"error","error":{"type":"invalid_request_error",
        # "message":"Your credit balance is too low..."}}. Without this check
        # the fallback chain puts it on soft cooldown and retries every 60s
        # for a problem that requires a human to top up credit.
        body_lc = (body or "").lower()
        looks_billing = any(k in body_lc for k in (
            "credit balance", "credit_balance", "insufficient balance",
            "payment required", "billing", "insufficient funds",
            "quota exceeded", "out of credits",
        ))
        if looks_billing:
            return cls(
                provider,
                f"billing / credit exhausted (HTTP {status}): {body[:200]}",
                status=status, kind="billing", retryable=False, cause=cause,
            )
        if status == 401 or status == 403:
            return cls(provider, f"auth failed (HTTP {status})", status=status, kind="auth", retryable=False, cause=cause)
        if status == 402:
            return cls(provider, f"billing / payment required (HTTP {status})", status=status, kind="billing", retryable=False, cause=cause)
        if status == 429:
            return cls(provider, "rate limited", status=status, kind="rate_limit", retryable=True, cause=cause)
        if 500 <= status < 600:
            return cls(provider, f"upstream server error (HTTP {status})", status=status, kind="server", retryable=True, cause=cause)
        # Include a body snippet for 4xx so the cause is visible in logs
        # instead of just "HTTP 400".
        body_snippet = body[:200] if body else ""
        msg = f"HTTP {status}" + (f": {body_snippet}" if body_snippet else "")
        return cls(provider, msg, status=status, kind="other", retryable=True, cause=cause)


@dataclass
class LLMResult:
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    routed_via: str = ""  # hybrid only


class LLMProvider(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult: ...

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done: "Optional[Callable]" = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as the LLM generates them.

        Default implementation falls back to complete() and yields the
        full text as a single chunk — every provider works without
        modification. Override for true token-by-token streaming.

        on_done: optional callback(LLMResult) fired after the stream
        completes, carrying final token counts and model info.
        """
        result = await self.complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout,
        )
        yield result.text
        if on_done:
            on_done(result)
