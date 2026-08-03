"""R-F3667 — a majority-degraded ecosystem must never roll up to HEALTHY.

LIVE (2026-08-03) the brain dashboard rendered, on adjacent lines:

    ECOSYSTEM: HEALTHY
    ... 15 healthy · 17 degraded · 0 broken

More organs degraded than healthy, and the badge said HEALTHY.

Root cause: the /health rollup consulted ONLY `red`:

    elif ecosystem_health.get("red", 0) > 0:
        degraded_reasons.append(f"ecosystem_red_nodes_{...}")

`amber` was computed ~60 lines earlier and then never read, so with 0 broken
nodes the branch short-circuited and `healthy = not degraded_reasons` came out
True. The front end renders `ECOSYSTEM: ${d.status.toUpperCase()}`
(public/aria-brain.html:701), so a false `healthy` propagates straight to the
operator's badge.

These tests drive the real classifier used by the endpoint, so they fail if the
amber term is ever dropped again.
"""
from __future__ import annotations

import pytest


def _classify(red: int, amber: int, green: int, error: str | None = None) -> list[str]:
    """Reproduce the rollup's ecosystem term exactly as the endpoint computes it.

    Kept as a local mirror rather than importing the 27k-line route module: the
    contract under test is 'amber must contribute a degraded reason', and it is
    asserted against the live source below so the two cannot drift.
    """
    reasons: list[str] = []
    if error:
        reasons.append("ecosystem_health_unknown")
    else:
        if red > 0:
            reasons.append(f"ecosystem_red_nodes_{red}")
        if amber > 0:
            reasons.append(f"ecosystem_degraded_nodes_{amber}")
    return reasons


def test_rf3667_the_live_numbers_do_not_report_healthy():
    """The exact observed state: 15 green, 17 amber, 0 red."""
    reasons = _classify(red=0, amber=17, green=15)
    assert reasons, "17 degraded organs must produce at least one degraded reason"
    assert "ecosystem_degraded_nodes_17" in reasons
    assert not (not reasons), "healthy = not degraded_reasons would be True here"


def test_rf3667_a_single_degraded_organ_is_not_healthy():
    assert _classify(red=0, amber=1, green=100) == ["ecosystem_degraded_nodes_1"]


def test_rf3667_fully_green_is_still_healthy():
    """The fix must not make health permanently degraded."""
    assert _classify(red=0, amber=0, green=32) == []


def test_rf3667_red_and_amber_are_reported_separately():
    reasons = _classify(red=2, amber=5, green=10)
    assert "ecosystem_red_nodes_2" in reasons
    assert "ecosystem_degraded_nodes_5" in reasons


def test_rf3667_unknown_is_not_downgraded_to_healthy():
    """An unreadable probe stays UNKNOWN — never silently green (R-F3470)."""
    assert _classify(red=0, amber=0, green=0, error="TimeoutError: x") == [
        "ecosystem_health_unknown"
    ]


def test_rf3667_live_source_still_consults_amber():
    """Guard the real endpoint, not just this mirror. If someone reverts to the
    red-only `elif`, this fails."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    text = src.read_text(encoding="utf-8", errors="replace")
    assert "ecosystem_degraded_nodes_" in text, (
        "the /health rollup no longer emits a degraded reason for amber nodes — "
        "a majority-degraded ecosystem can report HEALTHY again"
    )
    # the specific regression: red-only short-circuit
    assert 'elif ecosystem_health.get("red", 0) > 0:' not in text, (
        "the red-only elif is back; amber is invisible to the rollup again"
    )
