"""R-F3921 — a DISABLED engine read as `blocked` forever.

`serving` reads a last-writer-wins key, so an engine that stops being ASKED freezes
at whatever it last did. Observed live: after R-F3883 disabled `google cse` in
searxng, `engine_relevance` kept reporting it as blocked — a disabled engine can
never write "served" again, so the verdict was permanent.

§27d makes that surface BINDING ("if a search source looks dead... read
engine_relevance"), so a future session would read "Google is blocking us" when in
fact WE turned it off. A wrong cause pointing at a wrong fix — the §18 OpenSanctions
defect in miniature, and the stale-forever class R-F3865 was written to kill
("a permanent ban would make this module the next stale hand-maintained list").

"Blocked" must mean REFUSING US RECENTLY. Past the window the verdict returns to
None — the honest reading for a source nobody is querying.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import search_engine_health as seh


class _Store:
    """READ-ONLY stand-in. `health_report` only reads, so no `set` is defined —
    which also keeps the pre-commit builtin-shadowing gate happy, correctly: a
    method literally named `set` is exactly the shape that guard exists to catch."""

    def __init__(self, data): self.data = data

    async def get(self, k): return self.data.get(k)

    async def get_json(self, k):
        import json
        v = self.data.get(k)
        return json.loads(v) if isinstance(v, str) else v


def _with_state(monkeypatch, engine, last_event, age_s):
    store = _Store({
        seh._skey(engine): {"engine": engine, "last_event": last_event,
                            "at": time.time() - age_s, "unresponsive_count": 3,
                            "last_unresponsive_reason": "Suspended: too many requests"},
        seh._KEY_PREFIX + engine: {"engine": engine, "total": 20,
                                   "independent": 0, "ratio": 0.0},
    })
    for n in ("get", "get_json"):
        monkeypatch.setattr(seh.rs, n, getattr(store, n))


@pytest.mark.asyncio
async def test_a_recent_refusal_still_reads_as_blocked(monkeypatch):
    """The guard must not go blind — a genuinely blocked engine stays visible."""
    _with_state(monkeypatch, "yep", "unresponsive", age_s=60)
    rep = await seh.health_report(["yep"])
    assert rep["engines"]["yep"]["serving"] is False
    assert "yep" in rep["blocked"]


@pytest.mark.asyncio
async def test_a_stale_refusal_is_no_longer_a_verdict(monkeypatch):
    """THE DEFECT: google cse, disabled upstream, reported blocked indefinitely."""
    _with_state(monkeypatch, "google cse", "unresponsive", age_s=seh._STATE_STALE_S + 60)
    rep = await seh.health_report(["google cse"])
    entry = rep["engines"]["google cse"]
    assert entry["serving"] is None, "a stale event must not remain a verdict"
    assert entry["stale"] is True
    assert "google cse" not in rep["blocked"], (
        "an engine nobody queries must not read as blocked forever (R-F3921)")


@pytest.mark.asyncio
async def test_a_stale_SERVING_state_also_expires(monkeypatch):
    """Symmetric, and it matters: an engine that served once and was never asked
    again must not read as healthy either — that is the R-F3873 defect returning
    from the other side."""
    _with_state(monkeypatch, "bing", "served", age_s=seh._STATE_STALE_S + 60)
    rep = await seh.health_report(["bing"])
    assert rep["engines"]["bing"]["serving"] is None


@pytest.mark.asyncio
async def test_the_age_is_reported_so_the_reading_is_auditable(monkeypatch):
    _with_state(monkeypatch, "yep", "unresponsive", age_s=120)
    entry = (await seh.health_report(["yep"]))["engines"]["yep"]
    assert entry["last_event_age_s"] >= 119


def test_the_window_exceeds_the_block_alert_ttl():
    """An engine under sustained refusal re-stamps its state on every query, so the
    window must be comfortably longer than the alert TTL or a genuinely blocked
    engine could age out between alerts."""
    assert seh._STATE_STALE_S > seh._BLOCK_ALERT_TTL_S
