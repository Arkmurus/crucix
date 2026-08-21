"""R-F4214 / C-193: a parked metabolism must be visible, not just logged.

R-F4213 fixed the barrier so it always opens. This closes the other half: when
it opens LATE — hydration never signalled and seven boot workloads were released
degraded — the only evidence was one WARNING in a boot log nobody reads.

`/health/ready` already publishes knowledge_ready / neural_ready, and neither
can answer the question. Both are False during a normal ~10-minute warmup and
permanently False on a lean web worker (R-F2201), so False is indistinguishable
from "hydration died and the metabolism started contended". C-96 is the
precedent: /health reported `operational` next to a starved event loop for a
day, because no field in the payload could express what was wrong.

NOTE ON HOW THIS DRIVES THE HANDLER. Deliberately NOT `with TestClient(app)`
(R-F3365/R-F3347): that enters the REAL main.lifespan in-process and starts
ARIA's background subsystems on a loop that is then closed, wedging a later
test inside a native wait that `--timeout` cannot interrupt. Observed again
while writing this file — a 180s cap did not fire on a >600s hang. The handler
is a plain sync function over `request.app.state`, so calling it directly
drives the real code with none of that.
"""

import importlib
from types import SimpleNamespace

import pytest

from aria_service.routes import aria as aria_routes


def _request(**state):
    """Minimal stand-in for the only thing the handler touches."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def _body(**state):
    result = aria_routes.health_ready_ep(_request(**state))
    # not-ready returns a JSONResponse (503) carrying the same body
    if hasattr(result, "body"):
        import json
        return json.loads(result.body)
    return result


def test_ready_publishes_whether_the_barrier_opened():
    import asyncio
    opened = asyncio.Event()
    opened.set()
    body = _body(knowledge_ready=True, neural_ready=True, heavy_graph_ready=opened)
    assert "heavy_graph_ready" in body, (
        "nothing on any health surface says whether the barrier gating the "
        "autonomous engine, coder, seeds and web-integrity agent ever opened"
    )
    assert body["heavy_graph_ready"] is True


def test_an_unopened_barrier_reads_false_not_missing():
    """Absence must not render as health — the C-39/C-41/§1 shape."""
    import asyncio
    body = _body(heavy_graph_ready=asyncio.Event())  # created, never set
    assert body["heavy_graph_ready"] is False
    body_missing = _body()  # no attribute at all
    assert body_missing["heavy_graph_ready"] is False


def test_ready_publishes_the_forced_release_count():
    body = _body()
    assert "heavy_barrier_timeouts" in body, (
        "a forced release is the fact that matters — hydration never signalled "
        "and the metabolism started degraded. It must not live only in a log."
    )
    assert isinstance(body["heavy_barrier_timeouts"], int)
    assert body["heavy_barrier_timeouts"] >= 0


def test_the_count_is_read_live_not_bound_at_import(monkeypatch):
    """A module-level int bound at import freezes at 0 and can only say 'healthy'.

    `from ..main import _HEAVY_BARRIER_TIMEOUTS` would copy the value once — a
    gauge that cannot report the condition it exists to report.
    """
    main = importlib.import_module("aria_service.main")
    monkeypatch.setattr(main, "_HEAVY_BARRIER_TIMEOUTS", 4, raising=True)
    assert _body()["heavy_barrier_timeouts"] == 4, (
        "the count is bound at import, not read live — it can never leave 0"
    )
