"""R-F3930 — "what is my memory doing?" had no answer on demand.

The leak detector's findings were reachable ONLY by waiting for RSS to cross its
threshold and reading a log line or a capability gap. `get_status()` existed and no
route exposed it.

MEASURED CONSEQUENCE, 2026-08-12: after a deploy restart the process sat at 4792MB
against a 6144MB threshold, so nothing fired for 30 minutes of watching and the
diagnosis was unavailable for hours. A session could not distinguish "memory is
healthy" from "not yet measured" — the absence-reads-as-health shape this codebase
is written against (§1, §22), applied to the process itself.

§25 says ARIA must be able to answer what her own limbs are doing rather than only
being told when one is already bad. This makes the process side answerable at any
moment, on the endpoint someone looking for "memory health" will actually open.

Deliberately independent of the running detector instance (which lives on
self_healing): the probes are stateless, so plumbing a singleton through would add
coupling for nothing.
"""
from __future__ import annotations

from aria_service.intel import memory_leak_detector as mld


def test_the_report_answers_without_a_running_detector():
    """THE CAPABILITY: no instance, no threshold crossing, still an answer."""
    r = mld.process_memory_report()
    assert "rss_mb" in r and "threshold_mb" in r
    assert "subsystems" in r and isinstance(r["subsystems"], dict)
    assert r["threshold_mb"] == mld._THRESHOLD_MB


def test_the_first_reading_reports_no_delta_rather_than_zero():
    """'No prior reading' and 'no change' are different facts (§22). Reporting the
    first call as zeros would assert stability that was never observed."""
    mld._LAST_REPORT_CENSUS = {}
    first = mld.process_memory_report()
    assert first["subsystems_delta_since_last_call"] is None

    second = mld.process_memory_report()
    assert isinstance(second["subsystems_delta_since_last_call"], dict)


def test_unreadable_rss_is_unknown_not_healthy():
    """On a platform without /proc, RSS reads 0. `over_threshold` must be None —
    'could not measure' is never 'measured and fine'."""
    r = mld.process_memory_report()
    if r["rss_mb"] is None:
        assert r["over_threshold"] is None
    else:
        assert isinstance(r["over_threshold"], bool)


def test_the_census_never_walks_the_object_graph():
    """The safety property carried over from R-F3920 — this runs on a request now,
    so a heap walk would block a user-facing endpoint, not just the monitor."""
    import ast
    import textwrap

    from aria_service.tests._source_probe import function_source

    tree = ast.parse(textwrap.dedent(function_source(mld, "subsystem_census")))
    names = {getattr(n, "attr", None) or getattr(n, "id", None)
             for n in ast.walk(tree)}
    for banned in ("get_objects", "get_referrers", "tracemalloc"):
        assert banned not in names, f"{banned} would block the request thread"


def test_one_broken_probe_does_not_blind_the_report(monkeypatch):
    import aria_service.intel.knowledge as k
    monkeypatch.delattr(k, "_content_index", raising=False)
    r = mld.process_memory_report()
    assert "facts" in r["subsystems"], "a missing probe must not empty the census"


def test_the_endpoint_reports_process_memory_and_cannot_break_the_store_report():
    """Wired where it will be found (§21a) — and diagnostics must never take down
    the store health the endpoint already served."""
    from aria_service.tests._source_probe import function_source
    from aria_service.routes import aria as routes

    src = function_source(routes, "memory_health_ep")
    assert "process_memory_report" in src, (
        "/memory/health must report PROCESS memory — otherwise the leak diagnosis "
        "remains unavailable until RSS crosses a threshold (R-F3930)")
    assert "except Exception" in src, (
        "a diagnostics failure must not break the store report this endpoint "
        "already provides")


# ── R-F3932: not-loaded is not empty ───────────────────────────────────────────

def test_an_unhydrated_cache_reports_none_not_zero(monkeypatch):
    """THE DEFECT, caught on the first live reading of this very report: `facts: 0`
    at 2552MB on a freshly booted process, because `(_cache or {})` collapsed an
    UNHYDRATED cache into the same 0 as an empty one.

    It matters concretely — "2.5GB with zero facts" invites the conclusion that
    knowledge is not the consumer, when it may simply not have loaded yet. A wrong
    cause pointing at a wrong fix."""
    import aria_service.intel.knowledge as k

    monkeypatch.setattr(k, "_cache", None, raising=False)
    assert mld.subsystem_census()["facts"] is None, (
        "an unhydrated knowledge cache must report None, not 0 (R-F3932)")

    monkeypatch.setattr(k, "_cache", {"facts": []}, raising=False)
    assert mld.subsystem_census()["facts"] == 0, (
        "a genuinely empty cache must report 0 — the distinction is the point")

    monkeypatch.setattr(k, "_cache", {"facts": [1, 2, 3]}, raising=False)
    assert mld.subsystem_census()["facts"] == 3


def test_the_delta_skips_subsystems_that_were_not_loaded(monkeypatch):
    """Subtracting against None would crash or invent a number; both are worse than
    reporting the pair as not comparable."""
    import aria_service.intel.knowledge as k

    mld._LAST_REPORT_CENSUS = {}
    # R-F3941 — the indices must be BUILT for topic_index to be a comparable
    # subsystem. This test used to leave `_index_count` at its unbuilt -1 and still
    # expect `topic_index` in the delta, which only held because an UNBUILT index
    # was reporting a false 0. It was therefore asserting the presence of the very
    # defect R-F3941 removed — so the setup is corrected here rather than the
    # assertion, which states the real intent and still holds.
    monkeypatch.setattr(k, "_index_count", 0, raising=False)
    monkeypatch.setattr(k, "_topic_index", {}, raising=False)
    monkeypatch.setattr(k, "_content_index", {}, raising=False)
    monkeypatch.setattr(k, "_cache", None, raising=False)
    mld.process_memory_report()                     # prime with facts=None

    monkeypatch.setattr(k, "_cache", {"facts": [1]}, raising=False)
    r = mld.process_memory_report()                 # must not raise
    delta = r["subsystems_delta_since_last_call"]
    assert isinstance(delta, dict)
    assert "facts" not in delta, "None -> int is not a comparable delta"
    # ...and the comparable ones survive. (asyncio_tasks is absent here because
    # asyncio.all_tasks() raises outside a running loop, so that probe is correctly
    # omitted rather than reported as a number it could not obtain.)
    assert "topic_index" in delta, "comparable subsystems must still be reported"


def test_an_unbuilt_index_is_not_a_comparable_subsystem(monkeypatch):
    """R-F3941 — the counterpart of the test above, and the reason its setup changed.

    With the indices UNBUILT, topic_index reports None on both readings, so it is
    correctly absent from the delta. Pinned here so a future edit cannot quietly
    restore the false 0 by reverting the `_index_count` setup above.
    """
    import aria_service.intel.knowledge as k

    mld._LAST_REPORT_CENSUS = {}
    monkeypatch.setattr(k, "_index_count", -1, raising=False)   # unbuilt
    monkeypatch.setattr(k, "_topic_index", {}, raising=False)
    monkeypatch.setattr(k, "_content_index", {}, raising=False)
    monkeypatch.setattr(k, "_cache", {"facts": [1]}, raising=False)

    mld.process_memory_report()
    r = mld.process_memory_report()

    assert r["subsystems"]["topic_index"] is None
    assert "topic_index" not in (r["subsystems_delta_since_last_call"] or {}), (
        "an UNBUILT index has nothing to compare — reporting a delta would invent "
        "growth that was really just hydration (R-F3941)")
