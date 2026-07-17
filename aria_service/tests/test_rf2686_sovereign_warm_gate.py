"""R-F2686 — the sovereign warm-gate is actually WIRED (P0.5 residual, to-do list #2 #4).

The bug this pins (found 2026-07-17 by a read-only audit of R-F2648):

  1. `LLMHealthChecker.wrap()` was NEVER called in production. `main.py:1758` starts
     the checker but builds the chain without it, so `create_fallback_chain`'s
     SHADOW / PRIMARY_ALL slots took the RAW aria_llm provider — gated only on
     `is_configured`. R-F2648's own comment justifies its safety with
     "is_available() still fails closed via the R-F1957 warm-gate", a gate that was
     not in the call path. The comment was true of the design and false of the
     deployment.

  2. `_health_checker_instance` was declared `= None` at module scope and assigned
     NOWHERE. So the moment anyone DID wire wrap(), the first sovereign call would
     raise `AttributeError: 'NoneType' object has no attribute 'is_available'` — a
     landmine under the fix.

HONEST SCOPE: under the live R-F2410 two-track default (SHADOW=0, PRIMARY_ALL unset,
verified on fly 2026-07-17) the sovereign is not in this chain, so the wrap is INERT
in prod today — it arms the slots for the flag flip. The default sovereign path
(model_router → aria_llm_provider) still has no warm-gate: separate open item.

Capability assertions (§3c — drive the real path, assert the user-visible outcome):
  - `_build_chain` in SHADOW mode yields a chain whose sovereign entry is GATED.
  - A cold/unproven sovereign is SKIPPED and DeepSeek serves the user (no
    AttributeError, no 404 against the dead pod).
  - `start()` binds the singleton the gate reads.
"""
import asyncio
import os
from unittest.mock import patch

import pytest

from aria_service.llm import resilience as _res
from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError


class _FakeSovereign(LLMProvider):
    """Stands in for the RunPod endpoint. Records whether it was ever called."""
    name = "aria_llm"

    def __init__(self):
        self.calls = 0

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, system_prompt, user_message, *, max_tokens=4096, timeout=60.0):
        self.calls += 1
        return LLMResult(text="from sovereign", model="aria-llm-v0.1")


@pytest.fixture(autouse=True)
def _restore_singleton():
    """The gate reads a module global — never leak it across tests."""
    _saved = _res._health_checker_instance
    yield
    _res._health_checker_instance = _saved


def _gated(inner):
    """wrap() no-ops unless ARIA_LLM_URL is set at wrap time."""
    with patch.object(_res, "_ARIA_LLM_URL", "https://pod.example/v1"):
        return _res.LLMHealthChecker.wrap(inner)


# ── 1. The None-deref landmine ────────────────────────────────────────────────

def test_unstarted_checker_skips_to_fallback_not_attributeerror():
    """PRE-FIX: AttributeError on None. POST-FIX: a clean skip-to-fallback."""
    _res._health_checker_instance = None
    inner = _FakeSovereign()
    gated = _gated(inner)

    with pytest.raises(ProviderError) as exc:
        asyncio.run(gated.complete("sys", "hello"))

    assert exc.value.kind == "server"          # chain treats it as skippable
    assert exc.value.retryable is True
    assert "unproven" in str(exc.value).lower()
    assert inner.calls == 0                     # the dead pod was never called


# ── 2. start() binds the singleton the gate reads ─────────────────────────────

def test_start_binds_the_module_singleton():
    _res._health_checker_instance = None
    checker = _res.LLMHealthChecker(endpoint="")   # disabled → no probe task spawned
    asyncio.run(checker.start())
    assert _res._health_checker_instance is checker, (
        "start() must bind the singleton wrap() consults, else the warm-gate is unreachable"
    )


# ── 3. Cold vs warm admission ─────────────────────────────────────────────────

def test_cold_sovereign_is_skipped():
    """last_success_at == 0 → never proven warm → skip (R-F1957)."""
    checker = _res.LLMHealthChecker(endpoint="https://pod.example/v1")
    _res._health_checker_instance = checker
    assert checker.is_available() is False

    inner = _FakeSovereign()
    gated = _gated(inner)
    with pytest.raises(ProviderError):
        asyncio.run(gated.complete("sys", "hello"))
    assert inner.calls == 0


def test_warm_sovereign_is_admitted():
    """A probe-confirmed warm endpoint still serves — the gate must not be a wall."""
    import time
    checker = _res.LLMHealthChecker(endpoint="https://pod.example/v1")
    checker.last_success_at = time.time()
    checker.probe_status = "healthy"
    _res._health_checker_instance = checker
    assert checker.is_available() is True

    inner = _FakeSovereign()
    gated = _gated(inner)
    out = asyncio.run(gated.complete("sys", "hello"))
    assert out.text == "from sovereign"
    assert inner.calls == 1


def test_dormant_pod_is_skipped():
    """R-F2648 — pod deliberately off (§24) → dormant → skip, not a 404."""
    checker = _res.LLMHealthChecker(endpoint="https://pod.example/v1")
    checker.probe_status = "dormant"
    _res._health_checker_instance = checker
    assert checker.is_available() is False


# ── 4. THE capability test — the real chain, SHADOW mode, user gets an answer ──

class _FakePrimary(LLMProvider):
    """DeepSeek stand-in. Always answers — it is the coverage floor."""
    name = "deepseek"

    def __init__(self):
        self.calls = 0

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, system_prompt, user_message, *, max_tokens=4096, timeout=60.0):
        self.calls += 1
        return LLMResult(text="from deepseek", model="deepseek-chat")


def test_shadow_chain_skips_cold_sovereign_and_deepseek_serves():
    """Drive the REAL chain builder the operator's traffic goes through.

    SHADOW=1 slots the sovereign in as a fallback below the primary. With the
    sovereign COLD, its chain entry must fail CLOSED (skip) rather than dial the
    dead pod — and DeepSeek must still serve the user.
    """
    from aria_service.llm import fallback as _fb

    checker = _res.LLMHealthChecker(endpoint="https://pod.example/v1")
    _res._health_checker_instance = checker          # cold: last_success_at == 0
    sovereign = _FakeSovereign()
    primary = _FakePrimary()

    def _fake_create(provider, key, model=None, base_url=None, **kw):
        if base_url and "pod.example" in str(base_url):
            return sovereign
        if provider == "deepseek":
            return primary
        return None

    env = {"ARIA_LLM_URL": "https://pod.example/v1", "ARIA_LLM_SHADOW": "1"}
    with patch.dict(os.environ, env, clear=False), \
         patch.object(_res, "_ARIA_LLM_URL", "https://pod.example/v1"), \
         patch.object(_fb, "create_llm_provider", side_effect=_fake_create):
        chain = _fb.create_fallback_chain(
            primary_provider="deepseek", primary_key="k",
            primary_model="deepseek-chat", primary_base_url="",
        )

    entries = [p for p in chain.providers if getattr(p, "name", "") == "aria_llm"]
    assert entries, "sovereign should be in the SHADOW chain"
    entry = entries[0]
    assert entry is not sovereign, (
        "R-F2686: the chain took the RAW sovereign — the warm-gate is not wired, "
        "so a stopped pod still eats a live 404 + timeout on every fallback turn"
    )

    # The gated entry fails closed...
    with pytest.raises(ProviderError):
        asyncio.run(entry.complete("sys", "hello"))
    assert sovereign.calls == 0, "cold pod must never be dialled"

    # ...and the user still gets a real answer from the chain.
    out = asyncio.run(chain.complete("sys", "hello"))
    assert out.text == "from deepseek"
    assert primary.calls == 1
