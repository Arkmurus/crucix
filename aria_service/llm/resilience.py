"""
LLM Resilience Layer — health checker, request queue, and response cache.

Three independent components that wrap the existing FallbackProvider chain
to add production-grade resilience for ARIA-LLM (sovereign 14B on RunPod):

  1. LLMHealthChecker  — background task that probes ARIA-LLM periodically
     and updates the circuit_breaker registry so the fallback chain routes
     around a dead sovereign model without waiting for a user request to
     discover the outage.

  2. LLMRequestQueue   — semaphore-based concurrency limiter that prevents
     N concurrent LLM calls from overwhelming a single provider. Same
     wrapper pattern as MeteredProvider / RateLimitedProvider.

  3. LLMResponseCache  — LRU cache keyed by (prompt_hash, temperature).
     Repeated questions (common in chat) skip the LLM entirely. TTL-based
     eviction; never grows unbounded.

All three integrate with the existing brain-wiring (wire_success/wire_failure)
so the autonomous coder can see and act on provider health signals.

Usage:
    from .resilience import LLMHealthChecker, LLMRequestQueue, LLMResponseCache

    # In lifespan:
    health_checker = LLMHealthChecker()
    await health_checker.start()

    # Wrap the SOVEREIGN provider only (R-F2686) — never the whole chain:
    # wrap() fails CLOSED on a cold sovereign, so wrapping the chain would
    # take DeepSeek down with it. fallback.py:create_fallback_chain does this
    # at the one point the aria_llm provider is constructed.
    aria_llm = LLMHealthChecker.wrap(aria_llm)
    llm = LLMResponseCache(LLMRequestQueue(llm))
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from typing import Any, Optional

from . import aria_llm_url as _aria_llm_url  # R-F2645: the one URL join
from .provider import LLMProvider, LLMResult
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.resilience")

# ── Configuration (env-var overridable) ──────────────────────────────────────

_ARIA_LLM_URL = (os.getenv("ARIA_LLM_URL") or "").strip()
_ARIA_LLM_KEY = os.getenv("ARIA_LLM_KEY", "sovereign")
_ARIA_LLM_MODEL = os.getenv("ARIA_LLM_MODEL", "aria-llm-v0.1")

_HEALTH_CHECK_INTERVAL = int(os.getenv("ARIA_LLM_HEALTH_CHECK_INTERVAL", "10"))  # R-F1957: 30→10, trip breaker faster
_HEALTH_CHECK_TIMEOUT = int(os.getenv("ARIA_LLM_HEALTH_CHECK_TIMEOUT", "10"))
_HEALTH_CHECK_FAILURE_THRESHOLD = int(os.getenv("ARIA_LLM_HEALTH_FAILURE_THRESHOLD", "3"))
_HEALTH_CHECK_COOLDOWN = int(os.getenv("ARIA_LLM_HEALTH_COOLDOWN", "300"))

# R-F1957 — hangs-but-healthy / cold-start protection (ALL inert while ARIA_LLM_URL unset).
# The 2026-06-26 outage: a reached-but-hung endpoint passed `is_available()` and each user
# call waited the full 60s; a scale-to-zero cold-start (>60s) looked the same. Three guards:
#   _ARIA_LLM_CALL_TIMEOUT  — clamp the per-call deadline so a hang fast-fails to DeepSeek
#   _ARIA_LLM_STREAM_TIMEOUT — same for streaming (more generous; long answers stream > clamp)
#   _ARIA_LLM_WARM_TTL      — warm-gate: only admit aria_llm if a probe SUCCEEDED this recently
_ARIA_LLM_CALL_TIMEOUT = float(os.getenv("ARIA_LLM_CALL_TIMEOUT_S", "12"))
_ARIA_LLM_STREAM_TIMEOUT = float(os.getenv("ARIA_LLM_STREAM_TIMEOUT_S", "45"))
_ARIA_LLM_WARM_TTL = float(os.getenv("ARIA_LLM_WARM_TTL_S", "120"))

# R-F2088 — concurrency raised 5→24 + queue 100→200 for 50-100 concurrent users.
# LLM calls are I/O-bound (outbound HTTPS to DeepSeek) — they do NOT block the
# event loop, so a higher PARALLELISM ceiling just lets a burst of users be served
# at once instead of serialising 5-at-a-time. This is the safe scaling lever: it
# raises throughput/latency WITHOUT raising the cost RATE — the per-minute spend is
# still bounded by the rate limiter (ARIA_LLM_RPM, default 50) and the $300/mo +
# $20/user + $5/user/day cost caps, which are unchanged. To raise sustained
# throughput further (and accept faster cost burn), raise ARIA_LLM_RPM — that is the
# operator's spend decision, deliberately left at its current default here.
_QUEUE_MAX_CONCURRENT = int(os.getenv("ARIA_LLM_MAX_CONCURRENT", "24"))
_QUEUE_MAX_SIZE = int(os.getenv("ARIA_LLM_QUEUE_MAX_SIZE", "200"))
_QUEUE_TIMEOUT = int(os.getenv("ARIA_LLM_QUEUE_TIMEOUT", "30"))

_CACHE_MAX_SIZE = int(os.getenv("ARIA_LLM_CACHE_MAX_SIZE", "100"))
_CACHE_TTL = int(os.getenv("ARIA_LLM_CACHE_TTL", "3600"))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LLMHealthChecker — background health probe for ARIA-LLM
# ═══════════════════════════════════════════════════════════════════════════════


def _chat_completions_url(endpoint: str) -> str:
    """Build the probe URL — delegates to the one shared join (R-F2645).

    R-F2641: this probe used to append ``/v1/chat/completions`` to a base that
    already ended in ``/v1``, requesting ``/v1/v1/chat/completions`` → 404
    against a HEALTHY endpoint, which tripped the ``aria_llm`` breaker and
    reported the sovereign DOWN while it was UP (the promotion-gate corruption
    R-F2566 warns about at :195). R-F2645 moved the join into aria_llm_url so
    the probe and aria_llm_provider cannot drift apart again — the probe is now
    UP iff the provider is UP, by construction.
    """
    return _aria_llm_url.chat_completions_url(endpoint)


class LLMHealthChecker:
    """Background health probe for the sovereign ARIA-LLM endpoint.

    Runs a lightweight probe every ``check_interval`` seconds. On failure,
    records the failure to the circuit_breaker registry so the fallback
    chain skips ARIA-LLM without waiting for a user request to time out.
    On recovery, resets the breaker so traffic flows back.

    Only activates when ``ARIA_LLM_URL`` is set (sovereign model configured).
    Otherwise it's a no-op — no probe, no background task.

    Wire discipline (CLAUDE.md §21a):
      - Success: wire_success(module="llm_health_checker", ...)
      - Failure: wire_failure(module="llm_health_checker", ...) + circuit_breaker.record_failure
    """

    def __init__(
        self,
        *,
        endpoint: str = _ARIA_LLM_URL,
        api_key: str = _ARIA_LLM_KEY,
        check_interval: int = _HEALTH_CHECK_INTERVAL,
        probe_timeout: int = _HEALTH_CHECK_TIMEOUT,
        failure_threshold: int = _HEALTH_CHECK_FAILURE_THRESHOLD,
        cooldown_seconds: int = _HEALTH_CHECK_COOLDOWN,
    ):
        self._endpoint = endpoint
        self._api_key = api_key
        self._check_interval = check_interval
        self._probe_timeout = probe_timeout
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds

        self._enabled = bool(endpoint)
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # Circuit breaker — lazily created on first probe
        self._breaker = None

        # Probe stats (for /health)
        self.last_probe_at: float = 0.0
        self.last_success_at: float = 0.0
        self.last_latency_ms: float = 0.0
        self.consecutive_failures: int = 0
        self.probe_status: str = "unknown"  # healthy / degraded / unhealthy / unknown

    @fail_wire(module="resilience", gap_type="engine_failure")
    async def start(self) -> None:
        """Start the background health probe loop.

        Holds a strong reference to the task (anti-hallucination law #12:
        R-F1363 pt1 — GC'd tasks never execute). The task is stored as
        self._task so it survives until stop() is called.
        """
        # R-F2686 — bind the module singleton the wrap()'d provider consults.
        # Before this, NOTHING ever assigned _health_checker_instance, so the
        # warm-gate that R-F1957/R-F2648 cite as the reason traffic stays on
        # DeepSeek was unreachable: wrap()'s `_health_checker_instance.is_available()`
        # would AttributeError on None the moment it was ever wired. Bound here
        # (not __init__) so the singleton is the checker that is actually RUNNING.
        global _health_checker_instance
        _health_checker_instance = self
        if not self._enabled:
            logger.info("[LLMHealthChecker] ARIA_LLM_URL not set — health checker disabled")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "[LLMHealthChecker] started (interval=%ds, endpoint=%s)",
            self._check_interval, self._endpoint,
        )

    @fail_wire(module="resilience", gap_type="engine_failure")
    async def stop(self) -> None:
        """Stop the health probe loop."""
        if self._task:
            self._stop_event.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Probe loop — runs until stop() is called."""
        while not self._stop_event.is_set():
            await self._probe()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._check_interval,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                continue  # normal — time to probe again

    def _pod_expected_up(self) -> bool:
        """R-F2648 — is the sovereign pod supposed to be serving right now?

        Delegates to the ONE shared signal (runpod_scheduler.expected_serving:
        active work-claim OR shadow-autostart-in-window), so the probe and the
        chat router (model_router) agree on when the pod is deliberately off.
        Lazy import (llm→intel; runpod_scheduler is stdlib-only, no cycle).
        Fails SAFE toward probing so an import/logic error never blinds health.
        """
        try:
            from ..intel import runpod_scheduler as _sched
            return _sched.expected_serving()
        except Exception:
            return True

    async def _probe(self) -> None:
        """Single health probe against the ARIA-LLM endpoint.

        Uses a minimal prompt ("ok") with max_tokens=5 and temperature=0
        so the probe is fast and deterministic. Records the result to the
        circuit breaker and brain wiring.
        """
        if not self._enabled:
            return

        # R-F2648 — schedule-aware gate. When the sovereign pod is DELIBERATELY
        # off (CLAUDE.md §24 stop-only, no work-claim, outside any serving
        # window) probing it and tripping the aria_llm breaker measures POLICY,
        # not health — a false "sovereign DOWN". Skip the probe, mark dormant,
        # and stand the breaker down so /health is honest. is_available() still
        # fails closed via the R-F1957 warm-gate (last_success_at goes stale),
        # so traffic keeps routing to DeepSeek. Checked here (not at __init__
        # :139) so a pod a cycle starts mid-session re-activates without restart.
        if not self._pod_expected_up():
            self.last_probe_at = time.time()
            self.probe_status = "dormant"
            if self._breaker is not None and self._breaker.is_open():
                # Was open from prior probing of the now-stopped pod — stand it
                # down; "deliberately off" is not a failure to alarm on.
                from ..intel.circuit_breaker import reset_breaker
                reset_breaker("aria_llm")
            return

        start = time.time()
        self.last_probe_at = start

        try:
            import httpx
            # R-F2566: only send the Authorization header when the key is non-empty.
            # ARIA_LLM_KEY can be empty (the served RunPod endpoint needs no token) — an
            # unconditional f"Bearer {key}" then builds "Bearer " (whitespace-only value),
            # which httpx rejects as an "Illegal header value" BEFORE the request is sent.
            # The probe would then fail at header construction and report the sovereign as
            # DOWN even when it is UP (breaking the promotion-gate health signal), and spam
            # the gap ledger every cycle. Guard it, matching aria_llm_provider/openai_compat.
            _headers = {}
            _key = (self._api_key or "").strip()
            if _key:
                _headers["Authorization"] = f"Bearer {_key}"
            async with httpx.AsyncClient(timeout=self._probe_timeout) as client:
                resp = await client.post(
                    _chat_completions_url(self._endpoint),
                    headers=_headers,
                    json={
                        "model": _ARIA_LLM_MODEL,
                        "messages": [{"role": "user", "content": "ok"}],
                        "max_tokens": 5,
                        "temperature": 0,
                    },
                )
                latency = (time.time() - start) * 1000
                self.last_latency_ms = latency

                if resp.status_code == 200:
                    self._record_healthy()
                    self._wire_success(latency)
                else:
                    reason = self._classify_status(resp.status_code)
                    self._record_failure(reason)
                    self._wire_failure(f"HTTP {resp.status_code}: {resp.text[:200]}", reason)

        except asyncio.TimeoutError:
            self.last_latency_ms = (time.time() - start) * 1000
            self._record_failure("timeout")
            self._wire_failure("probe timed out", "timeout")
        except Exception as e:
            self.last_latency_ms = (time.time() - start) * 1000
            self._record_failure("server")
            self._wire_failure(str(e)[:200], "server")

    def _record_healthy(self) -> None:
        """Record a successful probe."""
        self.consecutive_failures = 0
        self.last_success_at = time.time()
        self.probe_status = "healthy"
        if self._breaker:
            self._breaker.record_success()

    def _record_failure(self, reason: str) -> None:
        """Record a failed probe."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self._failure_threshold:
            self.probe_status = "unhealthy"
        else:
            self.probe_status = "degraded"
        # Update circuit breaker
        breaker = self._get_breaker()
        breaker.record_failure(reason=reason)

    @fail_wire(module="resilience", gap_type="engine_failure")
    def record_user_failure(self, reason: str = "server") -> None:
        """R-F1957: a USER-facing call (not the background probe) failed/timed-out.

        Feed it into the SAME breaker so a hung/dead endpoint trips fast instead of
        waiting for the next 10s probe cycle (user traffic is far more frequent than
        the probe). Best-effort — never raises into the caller's error path."""
        try:
            self._record_failure(reason)
        except Exception:
            pass

    def _get_breaker(self):
        """Lazy-init the circuit breaker for ARIA-LLM."""
        if self._breaker is None:
            from ..intel.circuit_breaker import get_breaker
            self._breaker = get_breaker(
                "aria_llm",
                failure_threshold=self._failure_threshold,
                cooldown_seconds=self._cooldown_seconds,
            )
        return self._breaker

    @staticmethod
    def _classify_status(status: int) -> str:
        """Map HTTP status to a circuit-breaker reason tag."""
        if status in (401, 403):
            return "auth"
        if status == 402:
            return "billing"
        if status == 429:
            return "rate_limit"
        if 500 <= status < 600:
            return "server"
        return "server"

    def _wire_success(self, latency_ms: float) -> None:
        """Fire-and-forget brain signal on successful probe."""
        try:
            from ..intel.engine_wiring import wire_success
            wire_success(
                module="llm_health_checker",
                summary=f"ARIA-LLM health probe OK ({latency_ms:.0f}ms)",
                source_id="llm_health_checker:probe",
            )
        except Exception:
            pass

    def _wire_failure(self, detail: str, reason: str) -> None:
        """Fire-and-forget brain signal on failed probe."""
        try:
            from ..intel.engine_wiring import wire_failure
            wire_failure(
                module="llm_health_checker",
                detail=f"ARIA-LLM health probe failed: {detail}",
                gap_type="llm_provider_failure",
                source="llm_health_checker",
            )
        except Exception:
            pass

    @fail_wire(module="resilience", gap_type="engine_failure")
    def is_available(self) -> bool:
        """Is ARIA-LLM currently considered available?

        R-F1957 warm-gate: a cold/unproven endpoint must be SKIPPED (fast-fail to
        DeepSeek), never timed-out-on by user traffic. The old "breaker is None →
        assume available" path is exactly what let a cold/hung endpoint stall users
        on 2026-06-26. Admission now REQUIRES a successful completion probe within
        _ARIA_LLM_WARM_TTL seconds — so a scale-to-zero endpoint that is still
        cold-starting, or has never answered, is treated as unavailable until the
        probe actually confirms it warm."""
        if not self._enabled:
            return False
        # R-F2648 — deliberately off (§24 stop-only) → unavailable, route to
        # DeepSeek. Redundant with the warm-gate below (a dormant checker never
        # refreshes last_success_at) but explicit so the intent is unmissable.
        if self.probe_status == "dormant":
            return False
        # never succeeded yet → cold/unproven → skip
        if self.last_success_at <= 0:
            return False
        # last good probe too old → treat as cold until the probe re-confirms
        if (time.time() - self.last_success_at) > _ARIA_LLM_WARM_TTL:
            return False
        if self._breaker is not None and self._breaker.is_open():
            return False
        return True

    @fail_wire(module="resilience", gap_type="engine_failure")
    def get_status(self) -> dict:
        """Return health-checker status for /health endpoints."""
        return {
            "enabled": self._enabled,
            "endpoint": self._endpoint if self._enabled else "",
            "status": self.probe_status,
            "consecutive_failures": self.consecutive_failures,
            "last_latency_ms": self.last_latency_ms,
            "last_probe_seconds_ago": time.time() - self.last_probe_at if self.last_probe_at > 0 else None,
            "last_success_seconds_ago": time.time() - self.last_success_at if self.last_success_at > 0 else None,
            "breaker": self._breaker.to_dict() if self._breaker else None,
        }

    @staticmethod
    def wrap(inner: LLMProvider) -> LLMProvider:
        """Wrap the SOVEREIGN LLMProvider so the health checker's warm-gate gates calls.

        ⚠️ R-F2686 — wrap ONLY the aria_llm provider, NEVER the fallback chain:
        this gate fails CLOSED (a cold/unproven sovereign is skipped), so wrapping
        the chain would fast-fail DeepSeek too and leave ARIA with no LLM at all.

        Returns a thin wrapper that checks ``is_available()`` before
        delegating to the inner provider. When the breaker is OPEN, the
        wrapper raises ProviderError with kind="server" so the fallback
        chain skips ARIA-LLM cleanly.

        Only wraps when ARIA_LLM_URL is set. Otherwise returns the inner
        provider unchanged.
        """
        # R-F2686 — re-read the env as a fallback: the module global is bound at
        # IMPORT time (:51) but create_fallback_chain reads ARIA_LLM_URL at CALL
        # time, so a URL set after this module imported would leave the sovereign
        # SILENTLY ungated (gate vanishes, no log — §21a dark-path). Consult both.
        if not (_ARIA_LLM_URL or (os.getenv("ARIA_LLM_URL") or "").strip()):
            return inner

        def _admission() -> tuple[bool, str]:
            """R-F2686 — may the sovereign take this call? Fails CLOSED.

            Resolved at CALL time (not wrap time): the chain is built at
            main.py:1735 BEFORE the checker starts at :1758, so an eager read
            would always see None. A missing checker means nothing has PROVEN
            the endpoint warm — R-F1957's rule is that unproven = skip, so the
            honest answer is "no" (route to DeepSeek), never an AttributeError.
            """
            hc = _health_checker_instance
            if hc is None:
                logger.warning(
                    "[R-F2686] aria_llm call gated: health checker not started — "
                    "cannot prove the endpoint warm, skipping to fallback"
                )
                return False, "health checker not started (unproven)"
            if not hc.is_available():
                return False, "cold/unproven or breaker OPEN"
            return True, ""

        class _HealthCheckedProvider(LLMProvider):
            name = getattr(inner, "name", "aria_llm")

            @property
            def is_configured(self) -> bool:
                return inner.is_configured

            async def complete(
                self,
                system_prompt: str,
                user_message: str,
                *,
                max_tokens: int = 4096,
                timeout: float = 60.0,
            ) -> LLMResult:
                # The breaker check happens here — if OPEN, fast-fail
                # so the fallback chain moves to DeepSeek immediately
                # instead of waiting for a real timeout.
                _ok, _why = _admission()
                if not _ok:
                    from .provider import ProviderError
                    raise ProviderError(
                        "aria_llm",
                        f"ARIA-LLM unavailable ({_why}) — skipping to fallback",
                        kind="server", retryable=True,
                    )
                # R-F1957: clamp the deadline so a reached-but-hung endpoint
                # fast-fails to DeepSeek instead of stalling user traffic ~60s.
                eff_timeout = min(timeout, _ARIA_LLM_CALL_TIMEOUT)
                try:
                    return await inner.complete(
                        system_prompt, user_message,
                        max_tokens=max_tokens, timeout=eff_timeout,
                    )
                except Exception:
                    # R-F1957: feed user-call failures into the breaker so it
                    # trips fast (don't wait for the next probe cycle).
                    # R-F2686: None-safe — never mask the real provider error
                    # with an AttributeError from an unstarted checker.
                    if _health_checker_instance is not None:
                        _health_checker_instance.record_user_failure()
                    raise

            async def stream(
                self,
                system_prompt: str,
                user_message: str,
                *,
                max_tokens: int = 4096,
                timeout: float = 120.0,
                on_done=None,
            ):
                _ok, _why = _admission()
                if not _ok:
                    from .provider import ProviderError
                    raise ProviderError(
                        "aria_llm",
                        f"ARIA-LLM unavailable ({_why}) — skipping to fallback",
                        kind="server", retryable=True,
                    )
                # R-F1957: clamp streaming deadline (more generous than complete —
                # long answers stream past the call-clamp) + feed failures to the breaker.
                eff_timeout = min(timeout, _ARIA_LLM_STREAM_TIMEOUT)
                try:
                    async for chunk in inner.stream(
                        system_prompt, user_message,
                        max_tokens=max_tokens, timeout=eff_timeout, on_done=on_done,
                    ):
                        yield chunk
                except Exception:
                    if _health_checker_instance is not None:  # R-F2686 None-safe
                        _health_checker_instance.record_user_failure()
                    raise

        return _HealthCheckedProvider()


# Module-level singleton so the wrapper can reference the running checker
_health_checker_instance: LLMHealthChecker = None  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LLMRequestQueue — semaphore-based concurrency limiter
# ═══════════════════════════════════════════════════════════════════════════════

class LLMRequestQueue(LLMProvider):
    """Semaphore-based concurrency limiter for LLM calls.

    Wraps an inner LLMProvider and limits the number of concurrent
    ``complete()`` and ``stream()`` calls. When the queue is full,
    new requests raise ``QueueFullError`` immediately (fail-fast)
    rather than piling up and timing out.

    Same wrapper pattern as MeteredProvider / RateLimitedProvider —
    transparent passthrough for name, is_configured, and attributes.

    Wire discipline:
      - Queue-full events: wire_failure with gap_type="rate_limited"
      - Successful dispatch: wire_success
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        max_concurrent: int = _QUEUE_MAX_CONCURRENT,
        max_queue_size: int = _QUEUE_MAX_SIZE,
        queue_timeout: float = _QUEUE_TIMEOUT,
    ):
        self._inner = inner
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_queue_size = max_queue_size
        self._queue_timeout = queue_timeout

        # Stats
        self._active_count = 0
        self._queued_count = 0
        self._dropped_count = 0
        self._total_dispatched = 0

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._inner, "name", "queued")

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    def __getattr__(self, item):
        return getattr(self._inner, item)

    @fail_wire(module="resilience", gap_type="engine_failure", control_flow_exempt=("ProviderError",))
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        prefer_provider: str = "",
        model: str = "",   # R-F2769 — per-call Claude model override
    ) -> LLMResult:
        extra = {}
        if prefer_provider:
            extra["prefer_provider"] = prefer_provider
        if model:
            extra["model"] = model   # R-F2769 — forward the routed model

        # Fast-fail if queue is saturated
        if self._queued_count >= self._max_queue_size:
            self._dropped_count += 1
            self._wire_dropped("queue_full")
            from .provider import ProviderError
            raise ProviderError(
                self.name,
                f"LLM request queue full ({self._max_queue_size}) — try again later",
                kind="rate_limit", retryable=True,
            )

        self._queued_count += 1
        try:
            async with self._semaphore:
                self._active_count += 1
                self._queued_count -= 1
                self._total_dispatched += 1
                self._wire_dispatched()
                return await self._inner.complete(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=timeout, **extra,
                )
        finally:
            self._active_count -= 1

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
        model: str = "",   # R-F2769 — per-call Claude model override
    ):
        if self._queued_count >= self._max_queue_size:
            self._dropped_count += 1
            self._wire_dropped("queue_full")
            from .provider import ProviderError
            raise ProviderError(
                self.name,
                f"LLM request queue full ({self._max_queue_size}) — try again later",
                kind="rate_limit", retryable=True,
            )

        self._queued_count += 1
        try:
            async with self._semaphore:
                self._active_count += 1
                self._queued_count -= 1
                self._total_dispatched += 1
                self._wire_dispatched()
                async for chunk in self._inner.stream(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=timeout, on_done=on_done,
                    **({"model": model} if model else {}),   # R-F2769
                ):
                    yield chunk
        finally:
            self._active_count -= 1

    @fail_wire(module="resilience", gap_type="engine_failure")
    def get_stats(self) -> dict:
        """Return queue stats for /health endpoints."""
        return {
            "active": self._active_count,
            "queued": self._queued_count,
            "dropped": self._dropped_count,
            "total_dispatched": self._total_dispatched,
            "max_concurrent": self._max_concurrent,
            "max_queue_size": self._max_queue_size,
        }

    def _wire_dispatched(self) -> None:
        """Fire-and-forget brain signal on dispatch."""
        try:
            from ..intel.engine_wiring import wire_success
            wire_success(
                module="llm_request_queue",
                summary=f"LLM request dispatched (active={self._active_count})",
                source_id="llm_request_queue:dispatch",
            )
        except Exception:
            pass

    def _wire_dropped(self, reason: str) -> None:
        """Fire-and-forget brain signal on dropped request."""
        try:
            from ..intel.engine_wiring import wire_failure
            wire_failure(
                module="llm_request_queue",
                detail=f"LLM request dropped: {reason} (queued={self._queued_count})",
                gap_type="rate_limited",
                source="llm_request_queue",
            )
        except Exception:
            pass


class QueueFullError(Exception):
    """Raised when the LLM request queue is saturated."""


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LLMResponseCache — LRU cache for repeated LLM queries
# ═══════════════════════════════════════════════════════════════════════════════

class LLMResponseCache(LLMProvider):
    """LRU cache for LLM responses.

    Wraps an inner LLMProvider. Repeated questions return the cached
    response within TTL instead of re-hitting the LLM. Cache is in-process
    only — resets on restart, which is fine (cold cache after deploy is
    acceptable).

    Cache key = sha256(system_prompt | user_message | effective provider pin |
    model). See `_cache_key` for why the pin is part of it — R-F3954: this
    class is the OUTERMOST wrapper, so a key made of prompt bytes alone
    collides across the Claude/DeepSeek authorship boundary.

    Wire discipline:
      - Cache hit: wire_success with from_cache=True
      - Cache miss + LLM call: wire_success with from_cache=False
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        max_size: int = _CACHE_MAX_SIZE,
        ttl: int = _CACHE_TTL,
    ):
        self._inner = inner
        self._max_size = max_size
        self._ttl = ttl
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        # R-F3477 — upstream failures counted separately. Folding them into
        # _misses made a total LLM outage look like a cache that never writes.
        self._errors = 0

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._inner, "name", "cached")

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    def __getattr__(self, item):
        return getattr(self._inner, item)

    @staticmethod
    def _effective_pin(prefer_provider: str = "") -> Optional[str]:
        """Who will actually author the answer — resolved exactly as the chain does.

        `FallbackProvider` takes the explicit argument when given, else the
        `provider_scope` contextvar (fallback.py:1087). This wrapper sits
        ABOVE that resolution, so it has to repeat it to know what its key
        is really identifying.

        Returns None for "could not determine", which is not the same as
        "unpinned" and must never be collapsed into one: an unresolvable pin
        means the caller cannot tell whether serving a cached entry would
        cross the authorship boundary, so `complete` bypasses the cache
        entirely. A cache miss costs a call; a wrong badge costs the verdict.
        """
        explicit = (prefer_provider or "").strip().lower()
        if explicit:
            return explicit
        try:
            from .fallback import get_preferred_provider
            return (get_preferred_provider() or "").strip().lower()
        except Exception:      # pragma: no cover — import-time failure only
            return None

    @staticmethod
    def _cache_key(
        system_prompt: str,
        user_message: str,
        prefer_provider: str = "",
        model: str = "",
    ) -> Optional[str]:
        """Deterministic cache key over everything that decides the answer.

        R-F3954 (C-45) — this used to be `sha256(system_prompt|user_message)`,
        prompt bytes and nothing else. That is wrong *here specifically*
        because `LLMResponseCache` is the OUTERMOST wrapper (main.py:2084 →
        `app.state.llm_provider`) while DD pins Claude through a contextvar
        resolved one layer DOWN. A DD call and a general chat call with
        byte-identical prompts produced the same key, so within the 1-hour
        TTL a DeepSeek-authored answer was served verbatim to a
        Claude-pinned DD run, tagged `model="cache"` — precisely the
        "DeepSeek verdict wearing a Claude badge" R-F3034 exists to prevent.
        The non-degrading pin in fallback.py was sound; this key undid it.

        `web_search._search_cache_key` already keys on the serving backend
        (`|brave`) for the same reason — a cache entry belongs to whoever
        produced it.

        `model` is in the key for the same reason (R-F2769 routes it
        per-call): claude-opus and claude-sonnet are different authors.

        Temperature is still excluded — callers do not pass it through the
        LLMProvider.complete() interface at all, so it cannot vary between
        two calls that reach this key. If a future caller varies it, plumb
        it through here.

        Returns None when the pin cannot be resolved; see `_effective_pin`.
        """
        pin = LLMResponseCache._effective_pin(prefer_provider)
        if pin is None:
            return None
        raw = f"{system_prompt}|{user_message}|pin={pin}|model={(model or '').strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _get_cached(self, key: str) -> Optional[str]:
        """Return cached response if valid, else None."""
        if key not in self._cache:
            return None
        cached_at, response = self._cache[key]
        if time.time() - cached_at > self._ttl:
            del self._cache[key]
            return None
        # Move to end (LRU: most recently used)
        self._cache.move_to_end(key)
        return response

    def _set_cached(self, key: str, response: str) -> None:
        """Store response in cache with LRU eviction."""
        self._cache[key] = (time.time(), response)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    @fail_wire(module="resilience", gap_type="engine_failure")
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        prefer_provider: str = "",
        model: str = "",   # R-F2769 — per-call Claude model override
    ) -> LLMResult:
        # R-F3954 — key on the EFFECTIVE pin, not on prompt bytes alone.
        # `key is None` means the pin could not be resolved: read nothing and
        # write nothing, so an unknowable scope can never be served another
        # provider's answer.
        key = self._cache_key(system_prompt, user_message, prefer_provider, model)

        cached = self._get_cached(key) if key is not None else None
        if cached is not None:
            self._hits += 1
            self._wire_cache_hit()
            return LLMResult(
                text=cached,
                model="cache",
                routed_via="cache",
                input_tokens=0,
                output_tokens=0,
            )

        extra = {}
        if prefer_provider:
            extra["prefer_provider"] = prefer_provider
        if model:
            extra["model"] = model   # R-F2769 — forward the routed model
        # R-F3477 — count the miss only once the call is SERVED. This used to
        # increment before the call, so a failed call was recorded as a cache
        # miss. Live 2026-07-30 that produced "misses":491 with "size":0 during a
        # total LLM outage, which reads as a cache that is never written — the
        # 15-cycle DD recorded it as a standing cost leak on that basis. The
        # cache was fine (llm/resilience.py:773-790 is a correct LRU+TTL); it had
        # nothing to store because every complete() raised. An error is not a
        # cache event, so it now has its own counter.
        try:
            result = await self._inner.complete(
                system_prompt, user_message,
                max_tokens=max_tokens, timeout=timeout, **extra,
            )
        except Exception:
            self._errors = getattr(self, "_errors", 0) + 1
            raise
        self._misses += 1

        # Cache successful responses (non-empty, non-error). R-F3954 — never
        # store under an unresolvable pin; an entry nobody can attribute is
        # the entry that gets served to the wrong caller.
        if key is not None and result.text and len(result.text) > 10:
            self._set_cached(key, result.text)

        self._wire_cache_miss()
        return result

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
        model: str = "",   # R-F2769 — per-call Claude model override
    ):
        # Streaming is not cached (can't know the full response upfront)
        async for chunk in self._inner.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
            **({"model": model} if model else {}),   # R-F2769
        ):
            yield chunk

    @fail_wire(module="resilience", gap_type="engine_failure")
    def get_stats(self) -> dict:
        """Return cache stats for /health endpoints, MERGED with the inner chain.

        R-F3704 — `app.state.llm_provider` is this cache (the OUTERMOST wrapper),
        so `/health`'s `hasattr(llm, "get_stats")` resolved here. This method
        returned only cache counters, which meant the field literally named
        `llm_fallback_stats` in the /health payload contained NO fallback data:
        FallbackProvider's per-provider calls / failures / reliability /
        last_kind never reached the operator.

        Merging inner-first is the pattern `RateLimitedProvider.get_stats`
        (rate_limiter.py:322-330) already uses one layer down — this wrapper was
        the only one in the stack that terminated the chain instead of
        forwarding it.

        Cache counters keep their existing top-level names so nothing that reads
        `hits` / `misses` / `errors` today breaks, and are ALSO grouped under
        `response_cache` so a reader can tell the two layers apart.
        """
        inner_stats: dict = {}
        try:
            if hasattr(self._inner, "get_stats"):
                inner_stats = self._inner.get_stats() or {}
        except Exception as e:  # never let observability break a health probe
            logger.debug("[R-F3704] inner get_stats failed: %s", e)
            inner_stats = {}
        cache_stats = {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            # R-F3477 — upstream failures, counted separately so a provider outage
            # can no longer masquerade as a cache that is never written.
            "errors": getattr(self, "_errors", 0),
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1), 3),
            "ttl_seconds": self._ttl,
        }
        return {**inner_stats, **cache_stats, "response_cache": cache_stats}

    @fail_wire(module="resilience", gap_type="engine_failure")
    def clear(self) -> int:
        """Clear the cache. Returns number of entries evicted."""
        n = len(self._cache)
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        # R-F3704 — `_errors` was NOT reset here, so after a clear the error
        # count described a different (longer) window than hits/misses, and the
        # three could not be read against each other. Same value/window mismatch
        # class as R-F3696's sample-size bug.
        self._errors = 0
        return n

    def _wire_cache_hit(self) -> None:
        """Fire-and-forget brain signal on cache hit."""
        try:
            from ..intel.engine_wiring import wire_success
            wire_success(
                module="llm_response_cache",
                summary=f"LLM cache hit (size={len(self._cache)})",
                source_id="llm_response_cache:hit",
            )
        except Exception:
            pass

    def _wire_cache_miss(self) -> None:
        """Fire-and-forget brain signal on cache miss."""
        try:
            from ..intel.engine_wiring import wire_success
            wire_success(
                module="llm_response_cache",
                summary=f"LLM cache miss (size={len(self._cache)})",
                source_id="llm_response_cache:miss",
            )
        except Exception:
            pass
