"""R-F4126 (C-161) — a blocked SCRAPED source painted an organ RED, leaving no
severity left to signal a paid dependency actually failing.

Measured live 2026-08-17 on aria-intel::

    /health degraded_reasons: ['ecosystem_red_nodes_1', 'ecosystem_degraded_nodes_22']
    organ:search  health=red  sensor=circuit_breaker[archive_is]
                  value="OPEN/timeout (whole pool open)"

Every OPEN breaker at that moment was a FREE, SCRAPED source — `search:duckduckgo`,
`semantic_scholar`, `openalex`, `archive_is`, `wayback`. Not one paid dependency was
down, and search itself was serving normally through Brave.

§27 is explicit that this is the expected steady state, not an incident:

    "you cannot code your way out of an IP block … the engine list rots
     continuously … a better scraper gets blocked slightly later."

So the top severity in the product was permanently spent on a condition that is
known, expected, and unfixable in code — which means a REAL outage (Brave 429, the
Anthropic key exhausted, OpenSanctions quota spent) had no louder colour available to
distinguish itself. §17 records what that class of failure costs: DD went down when
Anthropic credit ran out.

Operator decision, 2026-08-17, verbatim: **"amber, and reserve red for paid sources we
depend on"**.

`_cap_for_pool` already understood blast radius — it caps a pool member at amber while
a sibling still serves, escalating only when the whole pool is open. That was right
about *breadth* and silent about *kind*. This adds the missing axis: a scraped source
never reaches red, however many of its siblings are down.

The MODULE node keeps its true red — the backend really is open, and nothing is
hidden. Only the organ's blast radius is corrected, which is the same principle
R-F3421 applied to pools.
"""
from __future__ import annotations

import pytest

from aria_service.intel import ecosystem_map as em


def test_paid_backends_are_declared():
    assert hasattr(em, "_PAID_BACKENDS"), (
        "there is no declaration of which sources we PAY for, so severity cannot "
        "distinguish a scraped block from a paid outage")
    paid = em._PAID_BACKENDS
    # The four §17/§18 name as paid, live dependencies.
    for name in ("brave", "anthropic", "deepseek", "opensanctions"):
        assert name in paid, f"{name} is a paid dependency (§17/§18) but is not declared"


def test_a_scraped_source_never_reaches_red():
    """The live case: archive_is + wayback both open = 'whole pool open'."""
    color, note = em._cap_for_pool_with_kind("archive_is", "red", open_now={"archive_is", "wayback"})
    assert color == "amber", (
        f"a blocked scraped source must not paint an organ red (got {color}). "
        "§27: an IP block is the expected steady state, not an incident.")
    assert "scraped" in note.lower(), note


def test_a_paid_source_still_reaches_red():
    """The whole point of reserving red — it must still be reachable."""
    color, _ = em._cap_for_pool_with_kind("brave", "red", open_now={"brave"})
    assert color == "red", (
        "Brave is the paid, DD-exclusive search engine (§17 RULE ONE). If it is "
        "open, that IS the loudest thing on the board.")


def test_kind_beats_blast_radius_for_a_paid_source():
    """The subtle half, and the one a future edit will undo.

    Brave is IN the web-search pool, so a blast-radius-first rule downgrades it to
    amber whenever any scraped sibling is up — which is what the first draft of
    this fix did. That is wrong: §17 RULE ONE makes Brave the DD-EXCLUSIVE engine
    and the pool siblings tier-2 only, so they cannot serve DD in its place. "A
    sibling is serving" only justifies relaxing when the siblings are genuine
    substitutes; for a paid dependency they are not, which is why we pay for it.
    """
    assert "brave" in em._BACKEND_POOLS, (
        "precondition: brave must be pooled, or this test proves nothing")
    color, _ = em._cap_for_pool_with_kind(
        "brave", "red", open_now={"brave"})     # six scraped siblings alive
    assert color == "red", (
        "a surviving scraped sibling must NOT downgrade a paid dependency — it "
        "cannot serve DD in Brave's place")


def test_a_surviving_sibling_still_caps_at_amber():
    """R-F3421's blast-radius rule must survive this change."""
    color, note = em._cap_for_pool_with_kind("archive_is", "red", open_now={"archive_is"})
    assert color == "amber"
    assert "still serving" in note, note


def test_green_and_amber_are_passed_through_untouched():
    for c in ("green", "amber", "grey"):
        color, note = em._cap_for_pool_with_kind("archive_is", c, open_now=set())
        assert color == c and note == "", (c, color, note)


def test_an_unpooled_backend_fails_LOUD():
    """The default is red-capable, and this expectation was corrected by a test.

    The first draft asserted an unknown backend should be treated as scraped
    (amber). Running the suite showed that demotes `ofac` — free, but an OFFICIAL
    registry, not a consumer engine we scrape — and C-39 records what an unmeasured
    sanctions source costs. §27's "a block is expected" is a claim about SCRAPED
    aggregators, which are exactly the pooled set. Everything else fails loud, which
    also preserves R-F3421's contract that an unpooled backend is unaffected."""
    for name in ("ofac", "some_unknown_dependency"):
        color, _ = em._cap_for_pool_with_kind(name, "red", open_now={name})
        assert color == "red", f"{name} is not a scraped pool member; it must fail loud"


def test_every_metered_service_is_declared_paid():
    """The anti-rot guard, and the reason this is not just a hand-list.

    A service we are BILLED per call for is by definition a paid dependency. If one
    appears in the external cost ledger and is absent from `_PAID_BACKENDS`, the
    declaration has silently fallen behind the money — the failure mode CLAUDE.md
    §27d records for every hand-maintained list in this repo.
    """
    from aria_service.intel import cost_tracker as ct

    metered = {s.lower() for s in getattr(ct, "_KNOWN_PAID_SERVICES", set())}
    if not metered:
        pytest.skip("cost_tracker declares no paid services to cross-check")
    missing = metered - {p.lower() for p in em._PAID_BACKENDS}
    assert not missing, (
        f"these services are METERED as paid but are not in _PAID_BACKENDS, so an "
        f"outage of theirs would be capped at amber: {sorted(missing)}")


def test_the_module_node_keeps_the_true_red():
    """Nothing is hidden — only the organ's blast radius is corrected."""
    import inspect
    src = inspect.getsource(em)
    i = src.index("for nid in node_ids:")
    window = src[i:i + 400]
    assert "_apply(nid, color," in window, (
        "the module node must still receive the RAW breaker colour; capping it too "
        "would hide a genuinely open backend")
