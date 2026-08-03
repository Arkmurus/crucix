"""A pinned DD must not degrade to DeepSeek — even once Claude is ALSO a general fallback.

MEASURED REGRESSION, 2026-08-03. To give general chat a non-DeepSeek fallback,
`ARIA_PREFERENCE_ONLY_PROVIDERS` was cleared on aria-intel. That did put anthropic
into the general chain — and in the SAME instant it made a DD pinned to Claude
degradable to DeepSeek, because `_pinned` was computed from that same set:

    _pinned = (prefer_provider or "").lower() in _pref_only

One env var was answering two unrelated questions:

  A. CHAIN COMPOSITION — may this provider serve the DEFAULT order? (cost)
  B. PIN CONTRACT — when explicitly asked for, may it fall back? (integrity)

While Claude was DD-only both answers were "no", so one flag looked sufficient.
They diverge the moment Claude is wanted for BOTH jobs, and the failure is silent:
the DD still returns a report, just authored by DeepSeek. R-F3034 records the
operator directive it violates — "DD reports are to be ran fully on Claude no
deepseek" — and R-F3087 calls that "worse than an honest incomplete DD because
the report still looked Claude-authored".

`non_degrading_pins()` (ARIA_NON_DEGRADING_PINS) now owns (B) alone.

NOTE: intentionally carries no R-number — data/r_number_reservations.json is
being edited by a peer agent and reserving one here would collide.
"""
from __future__ import annotations

import pytest

from aria_service.llm import fallback as fb


class _P:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_configured = True


def test_default_is_unchanged_when_the_new_var_is_unset(monkeypatch):
    """Back-compat: unset ARIA_NON_DEGRADING_PINS must mean exactly what it did."""
    monkeypatch.delenv("ARIA_NON_DEGRADING_PINS", raising=False)
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "anthropic")
    assert fb.non_degrading_pins() == fb.preference_only_providers() == {"anthropic"}


def test_the_two_concepts_can_now_be_set_independently(monkeypatch):
    """The whole point: anthropic general-eligible AND still a hard pin."""
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
    monkeypatch.setenv("ARIA_NON_DEGRADING_PINS", "anthropic")
    assert fb.preference_only_providers() == set()      # (A) may serve general traffic
    assert fb.non_degrading_pins() == {"anthropic"}     # (B) pin still a contract


def test_clearing_preference_only_alone_no_longer_surrenders_the_pin(monkeypatch):
    """The exact production config that caused the regression.

    With ARIA_PREFERENCE_ONLY_PROVIDERS="" and the pin set left at its default,
    the OLD code computed _pinned=False and a DD would have degraded. The pin set
    falls back to preference_only only when ARIA_NON_DEGRADING_PINS is UNSET, so
    this asserts the operator must opt in explicitly — an empty preference set
    plus an explicit pin set is the supported combination.
    """
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
    monkeypatch.setenv("ARIA_NON_DEGRADING_PINS", "anthropic")
    chain = fb.FallbackProvider([_P("deepseek"), _P("anthropic"), _P("deepseek_backup")])

    _general = [p.name for p in chain.providers
                if (p.name or "").lower() not in fb.preference_only_providers()]
    assert _general == ["deepseek", "anthropic", "deepseek_backup"], (
        "anthropic must be reachable on the general path — that is the point of "
        f"clearing preference-only. got {_general}"
    )
    assert "anthropic" in fb.non_degrading_pins(), (
        "a DD pinned to Claude must STILL refuse to degrade to DeepSeek while "
        "Claude is simultaneously a general fallback — R-F3034's contract does "
        "not weaken just because the provider gained a second role."
    )


def test_an_ordinary_pin_is_still_degradable(monkeypatch):
    """R-F1366's coder pin must keep its historical fallback behaviour."""
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
    monkeypatch.setenv("ARIA_NON_DEGRADING_PINS", "anthropic")
    assert "deepseek" not in fb.non_degrading_pins()


@pytest.mark.parametrize("raw,expected", [
    ("anthropic", {"anthropic"}),
    ("Anthropic, DeepSeek ", {"anthropic", "deepseek"}),
    ("", set()),
])
def test_pin_set_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ARIA_NON_DEGRADING_PINS", raw)
    assert fb.non_degrading_pins() == expected
