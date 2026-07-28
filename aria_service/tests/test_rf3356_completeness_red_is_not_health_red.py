"""R-F3356 — a completeness alert must never become a health failure.

R-F3351 restored the RED "⚠ Unassigned" card on the ecosystem map. That red is a
COMPLETENESS claim ("these modules match no organ"), and it is painted CLIENT-side
by _h() in public/aria-brain.html.

Health red is a different thing and has a different consequence: /api/aria/health
appends a degraded reason when `ecosystem_health["red"] > 0` (routes/aria.py:25395),
and that count comes from the SERVER's _build_health_map via
coverage["health_sensors"]["by_color"]["red"].

The trap this pins: someone later notices the dashboard shows a red node the
server reports as red=0, calls it an inconsistency, and "fixes" it by colouring
organ:unassigned red server-side. The orphan bucket exists whenever ANY module is
unmapped, so /health would go degraded permanently, for a reason that is not a
fault — cry-wolf on the operator's top-level badge, which is the failure mode the
R-F3048 sample-floor work and the R-F3047 semantic_scholar fix both existed to stop.

The separation holds today by construction rather than by intent, which is exactly
why it needs a test: every sensor path resolves organs through _assign_organ, and
that returns None for an unmatched name — never the string "unassigned" — so
nothing can target organ:unassigned. Verified live: the node exists in the graph
and is absent from the health map.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


# A HIGH gap whose source matches NO organ keyword. This is the input that
# actually exercises the leak: _build_health_map resolves a gap source through
# _organ_for_name -> _assign_organ, so an unmatched name is precisely where a
# "resolve to unassigned instead of None" change would take effect.
#
# Deliberately NOT _gather_signals(): the live signal set contains no
# unmatched-source gap, so a test built on it passes whatever the code does — a
# guard that cannot fire. Tamper-verified below.
_LEAK_SIGNALS = {
    "read_at": 0, "breakers": [], "agents": [], "limbs": {}, "surfaces": {},
    "gaps": [{"severity": "HIGH", "type": "wire_failure",
              "source": "zzz_matches_no_organ", "detail": "synthetic"}],
}


def test_rf3356_orphan_bucket_is_a_node_but_never_carries_server_health():
    full = asyncio.run(em.build_structure())
    node_ids = {n["id"] for n in full["nodes"]}
    assert [n for n in full["nodes"] if n.get("orphan_alert")], \
        "expected an orphan bucket to exist for this assertion to mean anything"
    assert "organ:unassigned" in node_ids

    health = em._build_health_map(_LEAK_SIGNALS, node_ids, em._organ_of_map(full))
    assert "organ:unassigned" not in health, (
        "the orphan bucket acquired a SERVER health colour — /api/aria/health "
        "degrades on by_color.red > 0, so this would flag the brain as degraded "
        "for having unmapped modules, which is a completeness gap and not a fault"
    )


def test_rf3356_this_guard_can_actually_fire():
    """Prove the instrument. Apply the exact change a future agent would make —
    resolve an unmatched name to the orphan bucket rather than None — and confirm
    the assertion above would catch it. Without this, a passing guard is
    indistinguishable from a guard watching the wrong thing."""
    full = asyncio.run(em.build_structure())
    node_ids = {n["id"] for n in full["nodes"]}
    organ_of = em._organ_of_map(full)

    real = em._assign_organ
    try:
        em._assign_organ = lambda mid: real(mid) or "unassigned"
        leaked = em._build_health_map(_LEAK_SIGNALS, node_ids, organ_of)
    finally:
        em._assign_organ = real

    assert leaked.get("organ:unassigned", {}).get("color") == "red", (
        "the tamper did not reproduce the leak, so the guard above proves nothing"
    )
    # ...and the real implementation is intact afterwards.
    assert em._assign_organ("zzz_matches_no_organ") is None


def test_rf3356_the_mechanism_that_keeps_them_separate():
    """_assign_organ returns None for an unmatched name, never "unassigned", so no
    sensor can resolve to the orphan bucket. If this ever returns the string, the
    isolation above becomes accidental."""
    assert em._assign_organ("unassigned") is None
    assert em._assign_organ("__no_organ_matches_this_name__") is None


def test_rf3356_health_red_counts_only_sensor_derived_reds():
    """The number /health gates on must come from real sensors, so a change to the
    orphan bucket cannot move it."""
    cov = asyncio.run(em.get_coverage())
    by_color = cov["health_sensors"]["by_color"]
    assert set(by_color) == {"green", "amber", "red"}
    # Every counted node must be one that actually carries a sensor.
    assert cov["health_sensors"]["with_live_sensor"] == sum(by_color.values()), (
        "by_color must total the sensor-bearing nodes — a colour with no sensor "
        "behind it has leaked into the count /health degrades on"
    )
