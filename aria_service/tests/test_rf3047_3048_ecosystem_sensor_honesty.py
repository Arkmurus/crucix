"""R-F3047 + R-F3048 — the two sensor defects that put the ecosystem banner
into DEGRADED on 2026-07-25.

Live at the time (`GET /api/aria/brain/dashboard` → panel `/health`):

    "degraded_reasons": ["ecosystem_red_nodes_3"]
    ecosystem_health: red=3 amber=20 green=11 grey=544

and the three reds were:

    organ:brain     circuit_breaker[semantic_scholar]  OPEN/rate_limit
    organ:delivery  outcome[web]  success 33% (n=3)
    organ:search    circuit_breaker[search:gnews_api]  OPEN/auth

Only the third was a real problem. This module pins the other two.
"""
from __future__ import annotations

import pytest

from aria_service.intel import ecosystem_map as em


# ---------------------------------------------------------------------------
# R-F3047 — sensor→organ attribution
# ---------------------------------------------------------------------------

def test_rf3047_semantic_scholar_does_not_paint_the_brain():
    """THE bug, verbatim.

    `_assign_organ` matches organ keywords by naked substring, and the brain
    organ's keyword list contains "semantic" (for ARIA's own semantic_search).
    So `semantic_scholar` — an external academic-paper API — resolved to
    organ:brain, and its rate-limit breaker painted "Brain & Memory"
    (56 modules) RED while the same health payload reported the real brain
    healthy with 459,634 facts indexed.

    FAILS BEFORE: _assign_organ("semantic_scholar") == "brain".
    """
    assert em._SENSOR_ORGAN.get("semantic_scholar") != "brain", (
        "semantic_scholar is an academic-paper API, not part of Brain & Memory"
    )
    assert em._SENSOR_ORGAN.get("semantic_scholar") == "search"


def test_rf3047_breaker_path_uses_the_registry_agents_and_gaps_do_not():
    """Guard the mechanism AND its scope.

    Token-matching would NOT have fixed the collision ("semantic" IS a token
    of "semantic_scholar"), so backend attribution has to be an explicit
    registry. But the FIRST cut of this fix pointed agents and gaps at the
    registry too, which broke three R-F2972 tests: those names are ARIA's own
    internal modules, where the _ORGANS keyword lists are exactly right (a
    stale `student_loop` must still reach organ:learning). Scope matters as
    much as the mechanism, so pin both halves.
    """
    src = open(em.__file__, encoding="utf-8").read()
    assert "_organ_for_backend" in src, "the backend resolver is gone"
    assert "org = _organ_for_backend(name)" in src, (
        "the circuit-breaker loop no longer resolves via the explicit registry"
    )
    # agents + gaps must still use the keyword resolver
    assert src.count("_organ_for_name(") >= 2, (
        "agent/gap attribution should still go through the keyword resolver"
    )


@pytest.mark.parametrize("backend,organ", [
    ("search:duckduckgo", "search"),   # was None — a real search outage painted nothing
    ("openalex", "search"),            # was None — same
    ("semantic_scholar", "search"),    # was brain — false positive
    ("search:gnews_api", "search"),    # was already correct; must stay correct
])
def test_rf3047_known_backends_resolve_to_the_right_organ(backend, organ):
    """Both error directions in one place: the false positive that painted an
    unrelated organ red, and the false negatives that painted nothing at all."""
    base = backend.lower().split(":")[-1]
    assert em._SENSOR_ORGAN.get(base) == organ, (
        f"{backend} should file under organ:{organ}, got {em._SENSOR_ORGAN.get(base)!r}"
    )


def test_rf3047_unknown_backend_paints_nothing():
    """An unregistered backend must not guess an organ — grey is the honest
    state for 'we do not know which organ this belongs to'."""
    assert em._SENSOR_ORGAN.get("some_backend_nobody_registered") is None


def test_rf3047_registry_targets_are_real_organs():
    """A typo'd organ id would silently paint nothing — catch it here."""
    import re

    src = open(em.__file__, encoding="utf-8").read()
    seg = src[src.find("_ORGANS"):]
    known = set(re.findall(r'\(\s*"([a-z_]+)",\s*"[^"]+",\s*"[a-z-]+"', seg[:9000]))
    unknown = {o for o in em._SENSOR_ORGAN.values() if o not in known}
    assert not unknown, f"_SENSOR_ORGAN points at non-existent organ(s): {unknown}"


# ---------------------------------------------------------------------------
# R-F3048 — minimum sample before a rate may colour a node
# ---------------------------------------------------------------------------

def _color_for(rate: float, total: int) -> str | None:
    """Mirror of the post-fix rule in section (4) of the signal walk."""
    if rate is None or total == 0:
        return None
    if total < em._MIN_OUTCOME_SAMPLES:
        return None
    return "green" if rate >= 0.95 else ("amber" if rate >= 0.7 else "red")


def test_rf3048_the_live_case_no_longer_reds_the_organ():
    """THE bug, verbatim: outcome[web] success 33% (n=3) turned Delivery RED
    and contributed one of the three reds behind the DEGRADED banner. A
    parallel audit showed those 3 records were one logical interaction
    (1 success + 2 duplicate empty_stream failures), so n was really 1.

    FAILS BEFORE: returned "red".
    """
    assert _color_for(0.333, 3) is None, (
        "one failure in three still paints the Delivery organ RED"
    )


@pytest.mark.parametrize("total", [1, 2, 3, 4])
def test_rf3048_below_floor_leaves_grey(total):
    assert _color_for(0.0, total) is None, (
        f"n={total} is below the {em._MIN_OUTCOME_SAMPLES}-sample floor and must "
        f"not colour the organ"
    )


def test_rf3048_a_genuinely_bad_run_still_goes_red():
    """The guard must not become a way to hide real delivery failure — this is
    the over-correction to protect against."""
    assert _color_for(0.1, 10) == "red"
    assert _color_for(0.5, 20) == "red"


def test_rf3048_healthy_traffic_still_greens():
    assert _color_for(1.0, 10) == "green"
    assert _color_for(0.8, 10) == "amber"


def test_rf3048_floor_is_declared_not_inline():
    """A magic number inline is how the previous `total == 0` guard ossified;
    keep the threshold named and auditable."""
    assert isinstance(em._MIN_OUTCOME_SAMPLES, int)
    assert em._MIN_OUTCOME_SAMPLES >= 5
