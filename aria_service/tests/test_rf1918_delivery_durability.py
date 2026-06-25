"""R-F1918 (G5 vaccine) — the non-durable-delivery / dark-limb gene must not regress.

H2 (root): the WA listener's in-flight job->chat map (_asyncJobMap) was IN-MEMORY
ONLY, so any restart wiped it and the brain's completion callback 404'd, DROPPING
the finished DD/answer — the recurring "DD never delivers / empty chat" that
R-F1884 only half-closed. Now persisted to the volume + restored at boot.

M5 (§25): the web SSE stream fired NO delivery-outcome on a server-side compose-cut
or ProviderError, so the brain couldn't see the web limb degrade (only WA reported).
Now both branches fire a fire-and-forget outcome.

The listener mjs starts a server on import, so its persistence is source-pinned
(the same approach used for the R-F964 recent-docs cache it mirrors); the web
outcome helper is verified to exist + be wired into both failure branches.
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
WA = (REPO / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8", errors="ignore")
ARIA = (REPO / "aria_service" / "routes" / "aria.py").read_text(encoding="utf-8", errors="ignore")


def test_wa_async_job_map_is_persisted_and_restored():
    # persistence + restore functions exist and mirror the R-F964 pattern
    assert "function _persistAsyncJobs()" in WA and "function _loadAsyncJobs()" in WA
    assert "_ASYNC_JOBS_FILE" in WA
    # restored at boot (next to the recent-docs restore)
    assert "_loadAsyncJobs();" in WA
    # persisted when a job is registered (survives a restart) and when delivered
    assert WA.count("_persistAsyncJobs();") >= 2, \
        "must persist on job-register AND on delivery"


def test_wa_async_job_restore_honours_ttl():
    # the loader must drop stale (>30 min) mappings, not resurrect ancient jobs
    assert "Date.now() - 1800000" in WA and "entry.ts > cutoff" in WA


def test_web_stream_fires_delivery_outcome_on_failure():
    assert "def _fire_web_delivery_outcome(" in ARIA
    # wired into the compose-cut path (degraded) and BOTH error handlers
    assert '_fire_web_delivery_outcome(session_id, "timeout_fallback"' in ARIA
    assert '_fire_web_delivery_outcome(session_id, "error", f"provider:{kind}")' in ARIA
    assert "internal:" in ARIA and ARIA.count("_fire_web_delivery_outcome(session_id,") >= 3
    # surface must be the web channel + fire-and-forget (never block the stream)
    assert 'surface="web"' in ARIA and "asyncio.create_task(_bg())" in ARIA
