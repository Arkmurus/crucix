"""LLM Rate Limiter — priority-aware request throttling.

Problem: background loops (research, self-improve, student, MEM0,
self-assessment) consume all Anthropic quota on tier 1 accounts,
starving interactive chat of tokens.

Solution: wrap the LLM provider with a rate limiter that:
  1. Tracks requests per minute
  2. Interactive requests (chat) go through immediately
  3. Background requests are delayed when near the rate limit
  4. Token bucket pattern with configurable burst

Priority levels:
  INTERACTIVE = 0  — user-facing chat, never delayed
  NORMAL      = 1  — on-demand research tasks, moderate delay OK
  BACKGROUND  = 2  — autonomous loops, can wait minutes

Usage:
  from .rate_limiter import RateLimitedProvider, Priority

  # Wrap the existing provider
  llm = RateLimitedProvider(raw_provider, rpm=50)

  # Interactive (default — always immediate)
  await llm.complete(system, user)

  # Background (will yield to interactive)
  with llm.priority(Priority.BACKGROUND):
      await llm.complete(system, user)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextvars import ContextVar
from enum import IntEnum
from typing import Optional

from .provider import LLMProvider, LLMResult

logger = logging.getLogger("aria.llm.rate_limiter")

# ── Priority levels ────────────────────────────────────────────────────────

class Priority(IntEnum):
    INTERACTIVE = 0  # user-facing — never delayed
    NORMAL = 1       # on-demand tasks — short delay OK
    BACKGROUND = 2   # autonomous loops — can wait

# Context variable for current priority
_current_priority: ContextVar[Priority] = ContextVar("llm_priority", default=Priority.INTERACTIVE)


def set_priority(p: Priority) -> object:
    """Set the LLM request priority for the current async context.
    Returns a token to reset with reset_priority()."""
    return _current_priority.set(p)


def reset_priority(token) -> None:
    """Reset priority to the previous value."""
    _current_priority.reset(token)


def get_priority() -> Priority:
    return _current_priority.get()


class _PriorityContext:
    """Context manager for setting LLM priority."""
    def __init__(self, provider: "RateLimitedProvider", priority: Priority):
        self._provider = provider
        self._priority = priority
        self._token = None

    def __enter__(self):
        self._token = _current_priority.set(self._priority)
        return self

    def __exit__(self, *args):
        if self._token is not None:
            _current_priority.reset(self._token)


# ── Rate-limited provider wrapper ──────────────────────────────────────────

class RateLimitedProvider(LLMProvider):
    """Wraps an LLM provider with priority-aware rate limiting.

    - Interactive requests go through immediately (up to burst limit)
    - Background requests wait if we're near the rate limit
    - Tracks request timestamps in a sliding window
    """

    def __init__(
        self,
        inner: LLMProvider,
        rpm: int = 0,
        burst: int = 0,
    ):
        """
        Args:
            inner: The actual LLM provider to wrap
            rpm: Requests per minute limit. 0 = auto-detect from env
                 ARIA_LLM_RPM (default 50 for tier 1 Anthropic)
            burst: Max burst above rpm for interactive requests.
                   0 = auto (rpm * 0.2)
        """
        self._inner = inner
        self._rpm = rpm or int(os.getenv("ARIA_LLM_RPM", "50"))
        self._burst = burst or max(5, int(self._rpm * 0.2))
        self._request_times: list[float] = []
        self._lock = asyncio.Lock()
        self._background_waiting = 0

        logger.info(
            "Rate limiter: rpm=%d burst=%d wrapping %s",
            self._rpm, self._burst, inner.name,
        )

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    def priority(self, p: Priority) -> _PriorityContext:
        """Context manager: `with llm.priority(Priority.BACKGROUND):`"""
        return _PriorityContext(self, p)

    def _requests_in_window(self, window_seconds: float = 60.0) -> int:
        """Count requests in the last N seconds."""
        cutoff = time.monotonic() - window_seconds
        # Prune old entries
        self._request_times = [t for t in self._request_times if t > cutoff]
        return len(self._request_times)

    def _headroom(self) -> int:
        """How many more requests we can make in this minute."""
        used = self._requests_in_window(60.0)
        return max(0, self._rpm - used)

    async def _acquire_slot(self) -> None:
        """Acquire a rate-limit slot based on current priority."""
        priority = _current_priority.get()
        if priority == Priority.INTERACTIVE:
            headroom = self._headroom()
            if headroom <= 0:
                wait = min(5.0, 60.0 / self._rpm)
                logger.debug("Interactive request near limit, brief wait %.1fs", wait)
                await asyncio.sleep(wait)
        else:
            await self._wait_for_slot(priority)
        self._request_times.append(time.monotonic())

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult:
        await self._acquire_slot()
        result = await self._inner.complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout,
        )
        result.routed_via = f"rate_limited:{self._inner.name}"
        return result

    async def stream(  # type: ignore[override]
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
    ):
        """Rate-limited streaming — same slot logic, then yield from inner."""
        await self._acquire_slot()
        async for chunk in self._inner.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
        ):
            yield chunk

    async def _wait_for_slot(self, priority: Priority) -> None:
        """Wait until there's headroom for a background request.

        Background requests reserve 40% of the RPM budget for interactive.
        Normal requests reserve 20%.
        """
        if priority == Priority.BACKGROUND:
            reserve_pct = 0.40  # keep 40% for interactive
            max_wait = 120.0    # wait up to 2 minutes
        else:
            reserve_pct = 0.20  # keep 20% for interactive
            max_wait = 30.0

        effective_limit = int(self._rpm * (1.0 - reserve_pct))
        waited = 0.0
        interval = 2.0

        while waited < max_wait:
            used = self._requests_in_window(60.0)
            if used < effective_limit:
                return  # slot available

            wait = min(interval, max_wait - waited)
            if waited == 0:
                self._background_waiting += 1
                logger.debug(
                    "Background request (priority=%s) waiting — %d/%d RPM used, "
                    "effective limit %d, %d bg queued",
                    priority.name, used, self._rpm, effective_limit,
                    self._background_waiting,
                )
            await asyncio.sleep(wait)
            waited += wait
            interval = min(interval * 1.5, 10.0)  # exponential backoff

        self._background_waiting = max(0, self._background_waiting - 1)
        logger.warning(
            "Background request (priority=%s) waited %.0fs, proceeding anyway",
            priority.name, waited,
        )

    def get_stats(self) -> dict:
        """Return rate limiter stats."""
        inner_stats = {}
        if hasattr(self._inner, "get_stats"):
            inner_stats = self._inner.get_stats()
        return {
            **inner_stats,
            "rate_limiter": {
                "rpm_limit": self._rpm,
                "burst": self._burst,
                "requests_last_60s": self._requests_in_window(60.0),
                "headroom": self._headroom(),
                "background_waiting": self._background_waiting,
            },
        }
