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
