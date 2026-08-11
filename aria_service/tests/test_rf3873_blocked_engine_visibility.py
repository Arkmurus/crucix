"""R-F3873 — the anti-rot mechanism was blind to the exact rot it exists to catch.

THE DEFECT, MEASURED LIVE 2026-08-11 on aria-intel/aria-searxng in the same second:

    SearXNG  → unresponsive_engines: [["google cse", "Suspended: too many requests"],
                                      ["yep",        "Suspended: access denied"]]
               engines_seen: ["bing"]        (and bing returned Milan tourism for
                                              the query "Modirum Gespi Ltd")

    /api/aria/search/health → engine_relevance:
               yep: {total: 81, ratio: 0.025, quarantined: false, judged: true}

So `yep` was reported as the HEALTHIEST engine on the board — 2.5% query-independent
across 81 observations — while it was access-denied and serving nothing at all.

WHY R-F3865 COULD NOT SEE IT. `record_observation` is driven by `_per_engine_verdicts`,
which iterates the engines that appear in the RESULT ROWS, and it is called inside
`if normalised:`. An engine that is 403'd, CAPTCHA'd or timed out contributes no rows,
so it accrues no observations: `total` freezes at its last good value, the ratio stays
excellent, `judged` stays True, and nothing is ever quarantined. **A source that stops
answering entirely is indistinguishable from one that was never asked.**

That inverts the module's stated purpose. §27d makes `engine_relevance` binding — "if a
search source looks dead, do not edit the engine list from intuition, read
engine_relevance" — and the surface a future session is instructed to trust is
structurally incapable of showing a dead engine. Same class as the three Phase A gates
certified by an absence (§1), the `spent_usd: 0.0` scare (§17), and C-23's infobox zero:
an absence that reads exactly like health.

SearXNG publishes `unresponsive_engines` on EVERY response, with a reason string. ARIA
discarded it — a repo-wide grep for the key found no consumer at all.

FOUR PROPERTIES THIS PINS:

  1. Blocked is a SEPARATE axis from lying, because they need OPPOSITE responses. A
     lying engine must be filtered (R-F3853); a blocked engine must be escalated — no
     code change fixes an IP block (§27), so quarantining it would be theatre that
     also converts a transient block into a self-inflicted outage on recovery.
  2. THE GUARD MUST BE ABLE TO FAIL (R-F3858). A healthy engine must not be reported
     blocked, or the surface becomes noise and gets muted.
  3. IT MUST NEVER EMPTY A RESULT SET (R-F3857). This is report-only. An emptied set
     reads as "nothing found", which an adverse-media sweep reads as CLEAN.
  4. IT FAILS OPEN and never claims to have measured what it could not (§22).
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import search_engine_health as seh
from aria_service.intel import search_searxng as sx


class _FakeStore:
    """Minimal in-memory stand-in for redis_store, so these tests never touch a
    real store and never depend on one being up."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, **kw):
        self.data[key] = str(value)

    async def get_json(self, key):
        import json
        raw = self.data.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key, obj, ex=None, **kw):
        import json
        self.data[key] = json.dumps(obj)


@pytest.fixture()
def store(monkeypatch):
    fake = _FakeStore()
    for name in ("get", "set", "get_json", "set_json"):
        monkeypatch.setattr(seh.rs, name, getattr(fake, name))
    # The roster cache is process-global and would otherwise carry registrations
    # from a previous test into this one's empty store, so an engine would look
    # registered while the store had never seen it. Cheap isolation beats the
    # order-dependent flake it prevents (cf. the §16 known-flaky set).
    seh._registered.clear()
    yield fake
    seh._registered.clear()


# ── the payload SearXNG actually sends ──────────────────────────────────────────

def test_the_real_unresponsive_payload_is_parsed():
    """The exact shape measured live on aria-searxng — a list of [name, reason]
    pairs. Asserted against the REAL payload, not a paraphrase: R-F3868's classify_429
    defect was caught only because a test used the real body text."""
    data = {
        "query": "Modirum Gespi Ltd",
        "results": [],
        "unresponsive_engines": [["google cse", "Suspended: too many requests"],
                                 ["yep", "Suspended: access denied"]],
    }
    got = dict(sx._extract_unresponsive(data))
    assert got == {"google cse": "Suspended: too many requests",
                   "yep": "Suspended: access denied"}


def test_a_response_with_every_engine_healthy_reports_nothing_blocked():
    """Property 2 — a guard that cannot come back clean is not a guard, it is an
    alarm that will be muted."""
    assert sx._extract_unresponsive({"results": [{"engine": "bing"}]}) == []
    assert sx._extract_unresponsive({"unresponsive_engines": []}) == []


def test_malformed_unresponsive_entries_do_not_raise():
    """Untrusted input on an observability path must never reach the search."""
    data = {"unresponsive_engines": [None, [], ["solo"], {"engine": "x"}, ["a", "b", "c"]]}
    out = sx._extract_unresponsive(data)          # must not raise
    assert all(isinstance(p, tuple) and len(p) == 2 for p in out)


# ── the capability: a blocked engine becomes VISIBLE ────────────────────────────

@pytest.mark.asyncio
async def test_a_blocked_engine_is_reported_as_not_serving(store):
    """THE SYMPTOM. Before this fix `yep` read as the healthiest engine on the board
    while access-denied, because a blocked engine accrues no observations."""
    # yep has an excellent relevance history — exactly the live reading.
    for _ in range(20):
        await seh.record_observation("yep", query_independent=False)

    rep = await seh.health_report(["yep"])
    assert rep["engines"]["yep"]["serving"] is True
    assert "yep" not in rep["blocked"]

    # ...and now SearXNG says it is access-denied.
    await seh.record_unresponsive("yep", "Suspended: access denied")

    rep = await seh.health_report(["yep"])
    entry = rep["engines"]["yep"]
    assert entry["serving"] is False, (
        "an access-denied engine that reads as serving is the R-F3873 defect")
    assert "yep" in rep["blocked"]
    assert entry["last_unresponsive_reason"] == "Suspended: access denied"
    # The relevance history is UNCHANGED — blocked and lying are separate axes.
    assert entry["ratio"] == 0.0
    assert entry["quarantined"] is False


@pytest.mark.asyncio
async def test_a_recovered_engine_stops_being_reported_blocked(store):
    """Property 2 again, in the direction that matters for rot: every block is a
    hypothesis with an expiry, never a death sentence (R-F3865 property 2)."""
    await seh.record_unresponsive("yep", "Suspended: access denied")
    assert "yep" in (await seh.health_report(["yep"]))["blocked"]

    await seh.record_observation("yep", query_independent=False)   # it answered again

    rep = await seh.health_report(["yep"])
    assert rep["engines"]["yep"]["serving"] is True
    assert "yep" not in rep["blocked"]


@pytest.mark.asyncio
async def test_blocked_is_not_quarantined(store):
    """Property 1 — quarantining a blocked engine is theatre (it already returns
    nothing) AND harmful (it would keep it out for an hour after the block lifts)."""
    for _ in range(30):
        await seh.record_unresponsive("mojeek", "Suspended: access denied")
    assert await seh.is_quarantined("mojeek") is False


@pytest.mark.asyncio
async def test_an_engine_never_seen_is_unknown_not_healthy(store):
    """§22 — 'could not measure' is never 'measured and passed'."""
    rep = await seh.health_report(["never-heard-of-it"])
    assert "never-heard-of-it" not in rep["engines"]
    assert rep["blocked"] == []


@pytest.mark.asyncio
async def test_the_engine_list_populates_itself(store):
    """The module exists because a hand-maintained engine list rots. Its own report
    must not depend on one — an engine ARIA has never been told about must still
    appear once it is observed."""
    await seh.record_unresponsive("some-new-engine", "Suspended: too many requests")
    rep = await seh.health_report()                 # no explicit names
    assert "some-new-engine" in rep["engines"]
    assert "some-new-engine" in rep["blocked"]


# ── it must never make search worse ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recording_survives_a_dead_store(monkeypatch):
    """Property 4 — observability must never raise into the search path."""
    async def _boom(*a, **k):
        raise RuntimeError("state_store: no connection")

    for name in ("get", "set", "get_json", "set_json"):
        monkeypatch.setattr(seh.rs, name, _boom)
    await seh.record_unresponsive("yep", "Suspended: access denied")   # must not raise
    rep = await seh.health_report(["yep"])                             # must not raise
    assert rep["blocked"] == []          # fails open — never invents a block


def test_the_unresponsive_wire_cannot_filter_results():
    """Property 3 — R-F3857 shipped a gate that emptied result sets, turning a
    detected backend failure into ok=True, count=0, which an adverse-media sweep
    reads as CLEAN. This wire is report-only and must stay that way."""
    from aria_service.tests._source_probe import function_source

    src = function_source(sx, "_extract_unresponsive")
    for banned in ("normalised", "results.remove", "del ", "filter("):
        assert banned not in src, (
            f"_extract_unresponsive touched {banned!r} — it must only OBSERVE")


def test_unresponsive_is_recorded_even_when_there_are_zero_results():
    """THE ROOT CAUSE, pinned. R-F3865 recorded health inside `if normalised:`, so
    the total-blackout case — every engine down, no rows at all — recorded nothing.
    That is precisely the case the operator needs to see."""
    from aria_service.tests._source_probe import function_source

    src = function_source(sx, "search")
    # Match STATEMENTS, not prose. A substring scan finds the guard quoted inside a
    # comment and reports a false position — the same literal-matching fragility
    # R-F3858 was shipped to fix.
    lines = src.splitlines()
    call_at = [i for i, ln in enumerate(lines)
               if "_extract_unresponsive(data)" in ln and not ln.strip().startswith("#")]
    guard_at = [i for i, ln in enumerate(lines) if ln.strip() == "if normalised:"]

    assert call_at, "search() must consume unresponsive_engines"
    assert not guard_at or call_at[0] < guard_at[0], (
        "unresponsive recording must not sit behind `if normalised:` — a blocked "
        "engine produces no rows, which is exactly when it must be recorded")
