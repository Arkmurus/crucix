"""R-F3421 — one component must not condemn an organ, and `mapped` must mean found.

THREE DEFECTS, ALL MEASURED LIVE ON 2026-07-29 against aria-intel.

(1) organ:search read RED from `circuit_breaker[search:duckduckgo] OPEN/rate_limit`,
    and that node was one of the two reds behind the ecosystem DEGRADED banner. At the
    same moment /api/aria/search/health reported EVERY backend up: searxng,
    google_news, bing_news, brave (the primary) and duckduckgo itself. Search is a
    REDUNDANT POOL — one member tripping is what redundancy is for. RED means broken;
    the organ was not broken.

(2) organ:brain read AMBER from this gap:
        detail : "Backend search:duckduckgo rate-limited — set API key ..."
        source : "brain_hook:circuit_breaker"
    `_organ_for_name` substring-matched `brain_hook` and filed it under organ:brain —
    correct by its own rule, wrong in fact. `source` is the module that RECORDED the
    gap, and brain_hook is ARIA's GENERIC SINK, so every subsystem's failure routed
    through it lands on Brain & Memory. Same family as R-F3047 (wrong organ from a
    name), different mechanism: reporter/subject confusion rather than a substring
    coincidence. R-F3047 hardened the breaker path and left this one on keywords.

(3) coverage reported services mapped=[all three] / unmapped=[] while node_modules=0.
    `mapped` was derived from the ORGAN TABLES — an intention — not from modules found.
    Root cause of the zero: the Node scan walks the FILESYSTEM at _NODE_ROOTS
    (server.mjs, lib, public/js, services/wa-listener) and aria_service/Dockerfile
    copies none of them, so inside the container the scan can only return empty. It
    works in dev, where the repo is on disk (127 Node modules found), and fails only in
    production — which is why no local test caught it.

The through-line is this repo's recurring failure: a surface asserting coverage or
severity it has not earned. These tests pin the corrected properties.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import ecosystem_map as em


def _nodes(*ids: str) -> set[str]:
    return set(ids)


def _breaker(name: str, state: str = "OPEN", reason: str = "rate_limit") -> dict:
    return {"name": name, "state": state, "last_failure_reason": reason}


def _health(signals: dict, node_ids: set[str]) -> dict:
    return em._build_health_map(signals, node_ids, {})


# ── (1) a pool member degrades its organ; it does not break it ───────────────

def test_one_open_pool_member_caps_the_organ_at_amber():
    h = _health({"breakers": [_breaker("search:duckduckgo")]}, _nodes("organ:search"))
    assert h["organ:search"]["color"] == "amber", (
        "one tripped backend of a redundant pool painted the organ BROKEN while its "
        "siblings were serving"
    )
    assert "still serving" in h["organ:search"]["value"]


def test_the_whole_pool_open_is_genuinely_red():
    """The cap must not hide a real outage.

    R-F4126 (C-161) changed WHY this is red, not WHETHER. It used to be red
    because breadth reached 100%. It is now red because the web-search pool
    contains `brave`, a paid dependency — and §17 RULE ONE makes Brave the
    DD-EXCLUSIVE engine, so its scraped pool siblings cannot serve DD in its
    place however many of them are up.

    The old assertion on the literal note "whole pool open" is dropped
    deliberately: that string described the breadth reasoning, which no longer
    decides this case. Asserting the colour is what protects the property this
    test exists for.
    """
    pool = em._BACKEND_POOLS["duckduckgo"]
    assert "brave" in pool, "precondition: the paid member is what makes this red"
    h = _health({"breakers": [_breaker(f"search:{m}") for m in pool]},
                _nodes("organ:search"))
    assert h["organ:search"]["color"] == "red", (
        "a paid dependency being open must still reach the top severity")


def test_a_whole_SCRAPED_pool_open_is_amber_not_red():
    """The operator's rule, at the extreme case it is easiest to get wrong.

    The web-archive pool (`wayback`, `archive_is`) has no paid member. Both being
    open is the exact live condition that painted organ:search red on 2026-08-17,
    and §27 calls it the expected steady state rather than an incident — so it is
    amber. Without this test, "whole pool open ⇒ red" could be restored from the
    test above and the cry-wolf would return.
    """
    pool = em._BACKEND_POOLS["archive_is"]
    assert not any("brave" in m for m in pool), "precondition: no paid member"
    h = _health({"breakers": [_breaker(m) for m in pool]},
                _nodes("organ:search"))
    assert h["organ:search"]["color"] == "amber", (
        "an all-scraped pool going dark is expected (§27), not a red incident")


def test_a_backend_outside_any_pool_is_unaffected():
    """Non-redundant backends keep today's behaviour — this change can only soften a
    false red, never suppress a true one."""
    assert "companies_house" not in em._BACKEND_POOLS
    em._SENSOR_ORGAN.setdefault("companies_house", "registries")
    h = _health({"breakers": [_breaker("companies_house")]}, _nodes("organ:registries"))
    assert h["organ:registries"]["color"] == "red"


def test_pool_membership_is_declared_not_inferred():
    for member in ("duckduckgo", "searxng", "brave"):
        assert member in em._BACKEND_POOLS
        assert member in em._BACKEND_POOLS[member]


# ── R-F3423 — the declaration must be COMPLETE and must not contain typos ────

def test_every_pool_member_is_a_real_registry_key():
    """A member that matches no `_SENSOR_ORGAN` key is a SILENT defect: it never
    appears in `_open_now`, so the pool looks permanently healthy and the amber cap
    applies when it should not.

    R-F3421 shipped with two: `brave_search` (the key is `brave`) and `gnews`
    (the key is `gnews_api`), so the real gnews backend was never pooled while a
    phantom one was. Invisible until this assertion existed.
    """
    unknown = sorted(m for m in em._BACKEND_POOLS if m not in em._SENSOR_ORGAN)
    assert not unknown, (
        f"pool members that are not registry keys: {unknown} — these can never trip, "
        f"so the pool is silently over-healthy"
    )


def test_every_search_backend_belongs_to_a_pool():
    """R-F3423's live lesson: organ:search went red again from `openalex`, a backend of
    exactly the same shape as the one R-F3421 fixed. Any search backend outside a pool
    can still condemn the organ single-handed."""
    search_backends = {k for k, v in em._SENSOR_ORGAN.items() if v == "search"}
    unpooled = sorted(search_backends - set(em._BACKEND_POOLS))
    assert not unpooled, (
        f"search backends that can still paint the organ RED alone: {unpooled}"
    )


def test_the_pools_are_distinct_capability_groups():
    """Membership means 'these are interchangeable for a purpose'. Lumping every search
    backend into one pool would mean an academic outage is masked by a web-search
    backend that cannot answer the same question."""
    assert em._BACKEND_POOLS["openalex"] != em._BACKEND_POOLS["duckduckgo"]
    assert em._BACKEND_POOLS["wayback"] != em._BACKEND_POOLS["duckduckgo"]
    assert "semantic_scholar" in em._BACKEND_POOLS["openalex"]


def test_an_academic_backend_alone_no_longer_condemns_search():
    """The exact live reading that reopened this: circuit_breaker[openalex] OPEN."""
    h = _health({"breakers": [_breaker("openalex")]}, _nodes("organ:search"))
    assert h["organ:search"]["color"] == "amber"
    assert "still serving" in h["organ:search"]["value"]


def test_half_open_member_is_still_amber():
    h = _health({"breakers": [_breaker("search:duckduckgo", state="HALF_OPEN")]},
                _nodes("organ:search"))
    assert h["organ:search"]["color"] == "amber"


# ── (2) a gap belongs to its SUBJECT, not to whoever recorded it ─────────────

_LIVE_GAP = {
    "type": "rate_limited",
    "detail": "Backend search:duckduckgo rate-limited — set API key for higher quota "
              "or add caller-side token bucket",
    "source": "brain_hook:circuit_breaker",
    "severity": 0,
}


def test_the_live_gap_no_longer_paints_brain():
    h = _health({"gaps": [_LIVE_GAP]}, _nodes("organ:brain", "organ:search"))
    assert "organ:brain" not in h, (
        "a SEARCH backend rate-limit is still painting Brain & Memory — the gap is "
        "being attributed to brain_hook, which merely recorded it"
    )


def test_the_live_gap_reaches_the_organ_it_is_about():
    h = _health({"gaps": [_LIVE_GAP]}, _nodes("organ:brain", "organ:search"))
    assert h["organ:search"]["color"] == "amber"


def test_subject_resolution_is_token_bounded():
    """R-F3047's lesson: `semantic` IS a token of `semantic_scholar`, so substring
    matching cannot be reintroduced here."""
    assert em._organ_for_gap_subject("the semantic layer misbehaved") is None
    assert em._organ_for_gap_subject("semantic_scholar rate-limited") == "organ:search"


def test_an_unattributable_gap_from_a_generic_sink_paints_nothing():
    """Grey, not a bystander. This module's standing rule is that absence of proof is
    never a claim."""
    g = {"type": "engine_failure", "detail": "something with no declared backend",
         "source": "brain_hook", "severity": 0}
    h = _health({"gaps": [g]}, _nodes("organ:brain", "organ:search"))
    assert h == {}


def test_a_gap_from_a_REAL_module_still_paints_its_organ():
    """The fix must not deafen genuine per-module gaps — a HIGH gap in a real module is
    exactly what this sensor is for."""
    g = {"type": "engine_failure", "detail": "student loop wedged",
         "source": "student", "severity": "HIGH"}
    h = _health({"gaps": [g]}, _nodes("organ:learning"))
    assert h.get("organ:learning", {}).get("color") == "red"


def test_generic_sinks_are_declared():
    assert "brain_hook" in em._GENERIC_GAP_SINKS


def test_subject_beats_reporter_even_when_both_resolve():
    """If the detail names a declared backend, that wins — the reporter is only a
    fallback."""
    g = {"type": "rate_limited", "detail": "Backend searxng rate-limited",
         "source": "student", "severity": 0}
    h = _health({"gaps": [g]}, _nodes("organ:search", "organ:learning"))
    assert "organ:search" in h
    assert "organ:learning" not in h


# ── (3) `mapped` means modules were FOUND ───────────────────────────────────

def test_coverage_separates_intention_from_evidence():
    c = asyncio.run(em.get_coverage())
    s = c["services"]
    for key in ("declared", "mapped", "unmapped", "with_modules_found",
                "declared_but_no_modules_found", "node_scan_roots_present_on_disk"):
        assert key in s, f"coverage lost {key}"


def test_r_f3352_contract_is_not_redefined():
    """`mapped`/`unmapped` answer 'does an organ table claim this service' and have
    their own tests. R-F3421 adds the evidence question ALONGSIDE — redefining another
    R-number's field would silently break its guard."""
    c = asyncio.run(em.get_coverage())
    s = c["services"]
    organ_services = ({x for _o, _l, x, _k in em._ORGANS}
                      | {x for _o, _l, x, _k in em._NODE_ORGANS})
    assert set(s["mapped"]) == organ_services & set(em._SERVICES)
    assert set(s["unmapped"]) == set(em._SERVICES) - organ_services


def test_with_modules_found_is_evidence_not_intention():
    """The defect: aria-web/aria-wa read as mapped with zero modules found."""
    c = asyncio.run(em.get_coverage())
    s = c["services"]
    if s["node_modules"] == 0:
        # container-shaped: the Node tiers must NOT appear as evidenced
        assert "aria-web" not in s["with_modules_found"]
        assert "aria-web" in s["declared_but_no_modules_found"]
    else:
        # dev-shaped: the repo is on disk, so they genuinely are evidenced
        assert "aria-web" in s["with_modules_found"]
        assert "aria-web" not in s["declared_but_no_modules_found"]


def test_intention_and_evidence_can_disagree_and_that_is_the_point():
    """A service can be declared and still contribute nothing — that disagreement is
    exactly the fact the summary used to hide."""
    c = asyncio.run(em.get_coverage())
    s = c["services"]
    assert set(s["declared_but_no_modules_found"]) == (
        set(s["mapped"]) - set(s["with_modules_found"])
    )


def test_scan_roots_presence_is_reported():
    """The honest signal that distinguishes 'nothing there' from 'we cannot see it'."""
    c = asyncio.run(em.get_coverage())
    present = c["services"]["node_scan_roots_present_on_disk"]
    assert set(present) == set(em._NODE_ROOTS)


def test_note_still_carries_the_r_f3352_phrase():
    """R-F3352 asserts the note names services with no module nodes; rewording it
    silently broke that guard once already in this change."""
    c = asyncio.run(em.get_coverage())
    note = c["services"]["note"].lower()
    assert "not in the counts" in note or "no module nodes" in note
