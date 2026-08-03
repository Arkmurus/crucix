"""The pre-outage redundancy page must count only providers DISPATCH CAN REACH.

MEASURED INCIDENT — 2026-08-03, production.

    18:01  ⚠️ STALLED: LLM chain has no fallback left.
           STILL SERVING: anthropic (answers are NOT degraded right now)
           UNAVAILABLE: deepseek, deepseek_backup

    18:32  🚨 BLOCKED: LLM chain — every provider failed.
           STUCK: no provider served the last request (path=complete, attempts=2).
           CALLED: deepseek, deepseek_backup

Both pages are correct about the facts and contradict each other about the
consequence. The reconciliation is `preference_only_providers()`: anthropic is
DD-reserved (R-F2922 / R-F3034) and is removed from the DEFAULT order, so
`complete()` and `stream()` never walk it — which is why 18:32 reports
attempts=2 and never names it.

`_check_redundancy_lost` was reading the RAW `self.providers`. With both deepseek
entries cooling, the reachable set was already EMPTY, but the raw set still had
exactly one "active" member — anthropic — which is the precise trigger for
"redundancy lost". So a TOTAL general-path outage was paged as a warning whose
own text asserted "answers are NOT degraded right now". The honest page did not
arrive for another 31 minutes.

This is the R-F3634 defect in a second location: that fix applied the filter to
`get_health()` and left this alert unfiltered.

NOTE: intentionally carries no R-number — data/r_number_reservations.json is
being edited by a peer agent and reserving one here would collide.
"""
from __future__ import annotations

import time

import pytest

from aria_service.llm import fallback as fb


class _P:
    """Minimal provider stub: the chain only needs `name` and `is_configured`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.is_configured = True


def _chain(monkeypatch, preference_only: str = "anthropic"):
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", preference_only)
    chain = fb.FallbackProvider([_P("deepseek"), _P("anthropic"), _P("deepseek_backup")])
    # Reproduce 18:01 exactly: both general providers cooling, anthropic clean.
    for name in ("deepseek", "deepseek_backup"):
        chain._stats[name]["cooldown_until"] = time.time() + 600
        chain._stats[name]["last_kind"] = "rate_limit"
    return chain


@pytest.fixture(autouse=True)
def _reset_alert_window():
    """The alert is globally rate-limited; clear it so each test can fire."""
    fb._last_redundancy_alert_at = 0.0
    yield
    fb._last_redundancy_alert_at = 0.0


def test_preference_only_provider_does_not_count_as_remaining_fallback(monkeypatch):
    """The 18:01 page must NOT fire: general chat had zero reachable providers."""
    chain = _chain(monkeypatch)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, source: sent.append((text, source)))

    chain._check_redundancy_lost()

    assert not sent, (
        "Paged 'STILL SERVING: anthropic (answers are NOT degraded right now)' "
        "while the general path had NO reachable provider. anthropic is "
        "preference-only, so complete()/stream() cannot use it. This is a total "
        "outage and belongs to the exhaustion page, not a redundancy warning.\n"
        f"sent={sent}"
    )


def test_the_guard_still_fires_when_the_survivor_is_genuinely_reachable(monkeypatch):
    """Sensitivity check — proves the test above is not vacuously green.

    With the preference-only mechanism disabled (empty string, per the
    documented escape hatch), anthropic IS reachable, exactly one provider
    remains, and losing redundancy is then a true and useful statement.
    """
    chain = _chain(monkeypatch, preference_only="")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, source: sent.append((text, source)))

    chain._check_redundancy_lost()

    assert len(sent) == 1, f"expected the redundancy page to fire, got {sent}"
    text, source = sent[0]
    assert source == "llm_chain_redundancy_lost"
    assert "anthropic" in text
    assert "deepseek" in text


def test_a_chain_whose_only_spare_is_preference_only_has_no_redundancy(monkeypatch):
    """Two configured providers, but only ONE is reachable by the general path.

    The `len(providers) < 2` guard has to count reachable providers too —
    otherwise a [deepseek, anthropic] chain looks redundant while being a
    standing single point of failure for every non-DD call.
    """
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "anthropic")
    chain = fb.FallbackProvider([_P("deepseek"), _P("anthropic")])
    chain._stats["deepseek"]["cooldown_until"] = time.time() + 600
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, source: sent.append((text, source)))

    chain._check_redundancy_lost()

    assert not sent, (
        "Only one provider is reachable on the general path, so there was never "
        "redundancy to lose — the docstring's own single-provider rule applies. "
        f"sent={sent}"
    )
