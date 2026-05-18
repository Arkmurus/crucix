"""R-F681 (2026-05-18) — billing-cooldown log ERROR→WARNING when fallback healthy.

CLAUDE.md §14 (Fallback transparency): "When a provider cools down and a
fallback serves, ARIA reports 'operational', never 'degraded'. Cooling
≠ broken."

Live evidence 2026-05-18 (other-agent DD report): Gate #3 (0 fly
ERRORs/7d) was failing — last_error 18 min ago, 200 errors/7d, all
from Anthropic billing cooldown. With operator declining the Anthropic
top-up (2026-05-18), the once-per-24h ERROR re-emission (post-R-F678
cooldown extension) STILL counts in the error ledger even though
DeepSeek is serving every chat call.

R-F681 demotes the billing-cooldown log from ERROR→WARNING when the
fallback chain has at least one healthy provider. Auth failures stay
ERROR because they often need urgent operator action (key rotation).

This is the gate-#3 unblocker. Without it, R-F678 alone reduces the
ERROR rate but never lets the 7-day streak counter clear.
"""
from __future__ import annotations

import logging
import time


def _build_chain_with_peers(names=("anthropic", "deepseek")):
    """Build a FallbackProvider with multiple providers wired so the
    healthy-peer check has something to look at."""
    from aria_service.llm.fallback import FallbackProvider
    chain = FallbackProvider.__new__(FallbackProvider)
    chain._stats = {
        n: {"calls": 0, "failures": 0, "last_failure": 0,
            "cooldown_until": 0, "last_kind": ""}
        for n in names
    }
    # Minimal fake providers — name is the only attribute used by the
    # log-demotion helper.
    class _P:
        def __init__(self, name): self.name = name
    chain.providers = [_P(n) for n in names]
    return chain


class _FakeProvider:
    name = "anthropic"


def _make_billing_error():
    from aria_service.llm.provider import ProviderError
    return ProviderError(
        "anthropic", "credit balance too low (HTTP 400)",
        kind="billing", retryable=False,
    )


def _make_auth_error():
    from aria_service.llm.provider import ProviderError
    return ProviderError(
        "anthropic", "invalid API key (HTTP 401)",
        kind="auth", retryable=False,
    )


def test_rf681_billing_cooldown_logs_warning_when_peer_healthy(caplog):
    """Anthropic billing fails, DeepSeek is healthy → WARNING (not ERROR).
    This is the gate-#3 unblocker — no ERROR added to the streak ledger."""
    chain = _build_chain_with_peers(("anthropic", "deepseek"))
    # DeepSeek has no cooldown set; it's healthy.
    err = _make_billing_error()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    error_lines = [r for r in caplog.records
                   if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    warning_lines = [r for r in caplog.records
                     if "HARD cooldown" in r.message and r.levelname == "WARNING"]

    assert len(error_lines) == 0, (
        f"R-F681: billing cooldown with healthy peer must NOT log ERROR; got "
        f"{[r.message for r in error_lines]}"
    )
    assert len(warning_lines) == 1, (
        f"R-F681: expected one WARNING line, got {len(warning_lines)}"
    )
    assert "fallback chain still has healthy provider" in warning_lines[0].message
    assert "system operational" in warning_lines[0].message


def test_rf681_billing_cooldown_logs_error_when_all_peers_cooling(caplog):
    """If every peer is ALSO cooling, the chain is truly degraded → ERROR.
    System operators need to see this and respond."""
    chain = _build_chain_with_peers(("anthropic", "deepseek"))
    # Mark DeepSeek as also cooling.
    chain._stats["deepseek"]["cooldown_until"] = time.time() + 600
    err = _make_billing_error()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    error_lines = [r for r in caplog.records
                   if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    warning_lines = [r for r in caplog.records
                     if "HARD cooldown" in r.message and r.levelname == "WARNING"]

    assert len(error_lines) == 1, (
        f"R-F681: billing cooldown with NO healthy peers must log ERROR; got "
        f"{len(error_lines)} ERROR / {len(warning_lines)} WARNING"
    )
    assert len(warning_lines) == 0


def test_rf681_auth_cooldown_still_logs_error_even_when_peer_healthy(caplog):
    """Auth failures are NOT demoted — they often signal an operator
    action is needed (rotate key) and shouldn't be hidden in WARNING."""
    chain = _build_chain_with_peers(("anthropic", "deepseek"))
    err = _make_auth_error()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    error_lines = [r for r in caplog.records
                   if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    assert len(error_lines) == 1, (
        f"R-F681: auth cooldown must STILL log ERROR even when peer healthy "
        f"— operator needs to see it. Got {len(error_lines)}"
    )


def test_rf681_healthy_peer_helper_correctness():
    """Direct unit test on the helper to lock its contract:
       - peer with cooldown_until=0 → healthy
       - peer with cooldown_until=now+600 → cooling
       - peer with cooldown_until=now-10 → expired → healthy"""
    chain = _build_chain_with_peers(("anthropic", "deepseek", "groq"))

    # All peers free.
    assert chain._fallback_chain_has_healthy_peer("anthropic") is True

    # DeepSeek cooling, Groq free.
    chain._stats["deepseek"]["cooldown_until"] = time.time() + 600
    assert chain._fallback_chain_has_healthy_peer("anthropic") is True

    # Both peers cooling.
    chain._stats["groq"]["cooldown_until"] = time.time() + 600
    assert chain._fallback_chain_has_healthy_peer("anthropic") is False

    # DeepSeek cooldown expired.
    chain._stats["deepseek"]["cooldown_until"] = time.time() - 10
    assert chain._fallback_chain_has_healthy_peer("anthropic") is True


def test_rf681_burst_debounce_still_works(caplog):
    """The F29 burst-debounce must still work — 5 concurrent billing
    failures with healthy peer = 1 WARNING + 4 DEBUG, not 5 WARNINGs."""
    chain = _build_chain_with_peers(("anthropic", "deepseek"))
    err = _make_billing_error()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        for _ in range(5):
            chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    warning_lines = [r for r in caplog.records
                     if "HARD cooldown" in r.message and r.levelname == "WARNING"]
    debug_lines = [r for r in caplog.records if "burst peer" in r.message]
    assert len(warning_lines) == 1, (
        f"R-F681 + F29: burst-debounce broken; got {len(warning_lines)} WARNINGs"
    )
    assert len(debug_lines) == 4
