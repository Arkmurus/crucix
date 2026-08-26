"""
LLM Provider — Base class and factory.
All providers expose the same interface: complete(system, user, opts) → LLMResult.
Streaming: stream(system, user, opts) → AsyncGenerator[str] for token-by-token output.
"""
from __future__ import annotations

import contextvars
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Optional
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

#: R-F4357 (C-303) — True while the chain is dialling a provider it has NO
#: alternative to. Lives here because it is the ONE fact both the chain
#: (fallback.py, which computes it via `_has_reachable_alternative`) and the
#: sovereign's warm-gate (resilience.py, which must honour it) need to agree on,
#: and both already import this module — so it costs no new coupling.
#:
#: WHY IT EXISTS. Two guards on the sovereign — the call clamp and the admission
#: gate — were written to protect a FALLBACK: clamp the deadline so a hang
#: "fast-fails to DeepSeek", refuse an OPEN breaker so the chain "skips to
#: fallback". DeepSeek was later removed from the chain by operator directive,
#: and with `ARIA_LLM_PRIMARY_ALL=1` the sovereign is the whole chain. Both
#: guards kept obeying a premise that no longer held, and their combined effect
#: was to take ARIA's entire reasoning dark for 300s at a time.
#:
#: The doctrine this restores is NOT new. R-F3680 already dials a cooling
#: provider "DESPITE its cooldown — it is the only reachable provider left;
#: going silent is worse", and R-F4330 (C-278) already exempts self-hosted
#: providers from soft cooldown. Both rulings were bypassed because the wrapper
#: refuses BEFORE the chain dials, and nothing told it whether refusing routed
#: anywhere.
#:
#: DEFAULT FALSE, and that is load-bearing: every path that does not explicitly
#: declare a last-resort dial keeps today's fail-closed behaviour. This widens
#: nothing on a chain that has somewhere else to go.
SOLE_PROVIDER_DIAL: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "llm_sole_provider_dial", default=False,
)

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
        # R-F3686 (2026-08-04) — AN AUTHORITATIVE STATUS OUTRANKS A SUBSTRING.
        #
        # The body-sniff below used to run FIRST, before any status was
        # considered. It exists for a real case (see its own comment), but
        # running it first means a 429 or a 503 whose body merely MENTIONS
        # billing — a rate-limit message linking to a billing console, an
        # upstream 503 naming a degraded billing service — is reclassified as
        # `kind="billing", retryable=False`. In this chain that is a 24-hour,
        # restart-surviving, self-sustaining lockout (fallback.py
        # _HARD_BILLING_COOLDOWN_SECONDS) built out of a transient, retryable
        # error.
        #
        # 401/403, 429 and 5xx are authoritative about what they are; a
        # provider does not send 429 to report an empty wallet. Only an
        # ambiguous 4xx (400, and anything unclassified) needs sniffing, which
        # is exactly the case the sniff was written for.
        #
        # Live 2026-08-04: anthropic sat on a `billing` cooldown with ~20h left
        # while the production key returned HTTP 200 on demand. The body that
        # armed it was never recorded, so which path armed it is not knowable
        # after the fact — fallback.py now persists that evidence.
        if status == 401 or status == 403:
            return cls(provider, f"auth failed (HTTP {status})", status=status, kind="auth", retryable=False, cause=cause)
        if status == 429:
            return cls(provider, "rate limited", status=status, kind="rate_limit", retryable=True, cause=cause)
        if 500 <= status < 600:
            return cls(provider, f"upstream server error (HTTP {status})", status=status, kind="server", retryable=True, cause=cause)
        if status == 402:
            return cls(provider, f"billing / payment required (HTTP {status})", status=status, kind="billing", retryable=False, cause=cause)

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
    @fail_wire(module="provider", gap_type="engine_failure")
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        model: str | None = None,   # R-F2768 — per-call model override (Claude-era routing)
    ) -> LLMResult: ...

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done: "Optional[Callable]" = None,
        model: str | None = None,   # R-F2768 — per-call model override (Claude-era routing)
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
            max_tokens=max_tokens, timeout=timeout, model=model,
        )
        # R-F4112 (C-145) — `on_done` USED TO SIT AFTER THE YIELD, so a consumer
        # that stopped reading early never ran it and the usage of a call that
        # had ALREADY happened was silently discarded. `complete()` above has
        # spent the tokens by this point; a consumer walking away does not
        # un-spend them. The `finally` fires on normal exhaustion AND on
        # GeneratorExit, so the meter sees every call exactly once.
        try:
            yield result.text
        finally:
            if on_done:
                try:
                    on_done(result)
                except Exception:      # usage reporting never breaks a stream
                    pass
