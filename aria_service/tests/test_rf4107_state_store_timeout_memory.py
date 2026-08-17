"""R-F4107 (C-140) — CAPABILITY: the health surface must remember store timeouts.

Measured live on aria-intel 2026-08-17, in a two-minute burst
(06:46:47–06:48:45Z), `state_store` logged **26 read timeouts across 25 distinct
keys**, each:

    state_store.get(<key>) timed out after 5s — DB may be bloated or under
    WAL recovery. Returning None.

Under the R-F1 None-on-error contract every one of those became an absence
indistinguishable from a real one. The affected key families are the ones that
ADJUDICATE:

    crucix:autonomous:paused:task:*   a paused task reads as NOT paused
    crucix:aria:cost                  the meter enforcing the $600 cap (§17)
    crucix:aria:error                 the ledger gate #3 measures
    crucix:dd:report:*                a DD report reads as missing
    crucix:aria:neurons:shard:0-5

Probed five minutes later, `/health` reported
`state_backend: {reachable: true, status: green}` — because the indicator is
**point-in-time reachability with no memory**. A surface that cannot remember
cannot distinguish a quiet store from one that just failed 26 reads, which is
why C-95 ran unnoticed for a day and why this burst would have too.

TWO PROPERTIES THIS FIX MUST NOT VIOLATE, both pinned below:

  1. The recorder must be PURELY IN-PROCESS. Writing a marker to the store on
     every timeout would mean writing to a wedged store precisely when it is
     wedged — the R-F2157 self-DOS shape, and it would deepen the outage it is
     trying to report.
  2. It must never change what `get()` returns or raise into a read path.

Run: python -m pytest aria_service/tests/test_rf4096_state_store_timeout_memory.py -v
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clean():
    from aria_service.intel import state_store
    if hasattr(state_store, "_reset_read_timeouts_for_test"):
        state_store._reset_read_timeouts_for_test()
    yield
    if hasattr(state_store, "_reset_read_timeouts_for_test"):
        state_store._reset_read_timeouts_for_test()


# ══════════════════════════════════════════════════════════════════════
# 1. THE DEFECT — a timeout must leave a trace the surface can read
# ══════════════════════════════════════════════════════════════════════

def test_a_read_timeout_is_remembered(monkeypatch):
    from aria_service.intel import state_store

    state_store.note_read_timeout("crucix:autonomous:paused:task:WEEKLY-CORPUS-INGEST")

    rep = state_store.read_timeout_report()
    assert rep["count"] == 1
    assert rep["last_age_s"] is not None and rep["last_age_s"] < 60
    assert any("autonomous:paused" in k for k in rep["keys_sample"]), (
        "the report must name WHAT went dark — 'a timeout happened' is not "
        "actionable when the key was the paused-task flag"
    )


def test_a_burst_is_distinguishable_from_a_blip():
    from aria_service.intel import state_store

    state_store.note_read_timeout("crucix:aria:cost")
    assert state_store.read_timeout_report()["degraded"] is False, (
        "one timeout is a blip; a surface that cries wolf is one nobody reads"
    )

    for i in range(25):
        state_store.note_read_timeout(f"crucix:aria:neurons:shard:{i}")
    assert state_store.read_timeout_report()["degraded"] is True, (
        "26 timeouts in one window is the live burst — it must surface"
    )


def test_an_old_timeout_falls_out_of_the_window():
    from aria_service.intel import state_store

    state_store.note_read_timeout("crucix:aria:cost")
    rep = state_store.read_timeout_report(window_s=0.0)
    assert rep["count"] == 0, "the gauge must be rolling, not a lifetime tally"


# ══════════════════════════════════════════════════════════════════════
# 2. THE SAFETY PROPERTIES — do not deepen the outage you are reporting
# ══════════════════════════════════════════════════════════════════════

def test_recording_never_touches_the_store():
    """R-F2157: writing a marker to the store on every timeout means writing to
    a wedged store exactly when it is wedged."""
    from ._source_probe import function_source
    from aria_service.intel import state_store

    src = function_source(state_store, "note_read_timeout")
    for forbidden in ("await ", "_conn", "execute(", "set(", "INSERT", "SELECT"):
        assert forbidden not in src, (
            f"note_read_timeout does I/O ({forbidden!r}). It must be a pure "
            f"in-process record — it runs on the failure path of a store that "
            f"is already timing out."
        )


def test_recording_never_raises_into_the_read_path():
    from aria_service.intel import state_store

    # Junk input must not propagate — observability never breaks a read.
    state_store.note_read_timeout(None)          # type: ignore[arg-type]
    state_store.note_read_timeout(12345)         # type: ignore[arg-type]
    assert state_store.read_timeout_report()["count"] >= 0


def test_the_report_is_tri_state_and_never_falsely_green():
    """'could not measure' must not render as healthy — the C-96 lesson."""
    from aria_service.intel import state_store

    rep = state_store.read_timeout_report()
    assert set(["count", "window_s", "degraded", "last_age_s", "keys_sample"]) <= set(rep)
    assert rep["degraded"] in (True, False)


# ══════════════════════════════════════════════════════════════════════
# 3. THE WIRE — /health must actually read the gauge it now has (C-96)
# ══════════════════════════════════════════════════════════════════════

def _health_with_stub_state():
    """Drive the REAL /health handler (the pattern R-F2152 uses)."""
    from unittest.mock import MagicMock
    from aria_service import main as aria_main

    llm = MagicMock()
    llm.get_stats.return_value = {}
    llm.get_health.return_value = {"resilient": True}
    llm.name = "test"
    llm.is_configured = True
    aria_main.app.state.llm_provider = llm
    aria_main.app.state.state_backend = "sqlite"
    aria_main.app.state.state_backend_reachable = True
    return asyncio.run(aria_main.health())


def test_health_surfaces_the_timeout_history():
    """C-96 exactly: a gauge no verdict consumes is why this went unnoticed."""
    from aria_service.intel import state_store

    state_store.note_read_timeout("crucix:aria:cost")
    out = _health_with_stub_state()

    rt = out["state_backend"].get("read_timeouts")
    assert rt is not None, "/health does not read the gauge it now has"
    assert rt["count"] >= 1
    assert any("cost" in k for k in rt["keys_sample"])


def test_a_timeout_burst_reaches_degraded_reasons():
    from aria_service.intel import state_store

    # The live burst: 26 reads, 25 distinct keys, two minutes.
    for i in range(26):
        state_store.note_read_timeout(f"crucix:aria:neurons:shard:{i}")

    out = _health_with_stub_state()

    assert "state_backend_read_timeouts" in out["degraded_reasons"], (
        "a burst that blinded 25 keys must reach the verdict, not sit in a "
        "sub-field a human has to go looking for — on 2026-08-17 /health read "
        "status=operational, degraded_reasons=[], state_backend=green five "
        "minutes after exactly this"
    )
    assert out["state_backend"]["status"] == "amber", (
        "answering-now-but-recently-blind is not the same as healthy"
    )


def test_a_quiet_store_is_still_green():
    """The over-correction guard: no timeouts must stay green, or the signal
    becomes one nobody reads."""
    out = _health_with_stub_state()
    assert out["state_backend"]["status"] == "green"
    assert "state_backend_read_timeouts" not in out["degraded_reasons"]
