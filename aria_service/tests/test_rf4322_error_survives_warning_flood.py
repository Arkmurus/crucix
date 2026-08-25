"""R-F4322 / C-270 — an ERROR must survive a WARNING flood in the ledger.

MEASURED LIVE 2026-08-25 on aria-intel (build fdec2df9), via
`GET /api/aria/self/recent-errors?hours=168`:

    entries: 200        window covered: 1.4 HOURS
    window_errors_24h: 200      window_errors_7d: 200

Both windows read 200 because 200 is the RING BUFFER CAP, not a count. The
ledger is a single unpartitioned list (`self_improve.MAX_ERRORS = 200`,
trimmed `errors[-MAX_ERRORS:]`) and `error_log_handler` mirrors EVERY
WARNING+ from any `aria.*` logger into it. Live warning rate was ~140/hour
(52 SearXNG relevance rejections and 41 reasoning-timeout retries in the
same 1.4h), so any ERROR is evicted within roughly ninety minutes.

THIS DEFECT IS ALREADY DOCUMENTED, at `error_streak.py:96-99`:
"a warning burst >200 evicts a real ERROR out of the ledger".

R-F2622 fixed the half that certifies Phase A gate #3 — a durable,
TTL-less streak anchor written at record_error() time, so the GATE can no
longer read eviction as cleanliness. It deliberately did not fix the
ledger, and the other half went unaddressed: the ledger is what a human or
an agent READS to diagnose. With a 1.4-hour horizon, "what went wrong
overnight?" is unanswerable, and — the dangerous part — an empty result
reads exactly like a clean night. That is the same absence-reads-as-health
shape CLAUDE.md section 1 records for three Phase A gates and section 17
for the cost probe.

THE FIX IS A RESERVE, NOT A BIGGER BUFFER. Raising MAX_ERRORS just moves
the horizon; the flood still wins, and section 1 forbids the band-aid.
ERROR and CRITICAL entries get a guaranteed floor of slots that WARNINGs
cannot take. Warnings still use everything the errors do not.

WHAT MUST NOT HAPPEN: a second definition of "counts as an ERROR". One
already exists — `error_streak.is_reset_type` — and it is shared precisely
so the write and read paths cannot drift (its own docstring says so). This
fix reuses it rather than matching on a literal type string locally.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import self_improve as si  # noqa: E402
from aria_service.intel import error_streak as es  # noqa: E402


class _FakeStore:
    """Minimal stand-in for redis_store holding keys in memory."""

    def __init__(self) -> None:
        self.data: dict = {}

    async def get_json(self, key, *a, **kw):
        return self.data.get(key)

    async def set_json(self, key, value, *a, **kw):
        self.data[key] = value
        return True

    async def get_json_strict(self, key, *a, **kw):
        return self.data.get(key)

    async def set(self, key, value, *a, **kw):
        self.data[key] = value
        return True

    async def get(self, key, *a, **kw):
        return self.data.get(key)


@pytest.fixture()
def store(monkeypatch):
    s = _FakeStore()
    monkeypatch.setattr(si, "rs", s)
    monkeypatch.setattr(si, "_record_error_cb_until", 0.0, raising=False)
    monkeypatch.setattr(si, "_record_error_failures", 0, raising=False)
    return s


def _ledger(store):
    return store.data.get(si.ERROR_LOG_KEY) or []


# -- THE CAPABILITY TEST ------------------------------------------------

def test_an_error_survives_a_warning_flood(store):
    """THE LIVE SYMPTOM. One real ERROR, then a flood of WARNINGs at the
    observed production rate. The ERROR must still be readable."""
    async def run():
        await si.record_error(
            "log:error",
            "[R-F2006 watchdog] ENGINE DARK: autonomous engine loop NOT TICKING",
            file="aria_service/main.py",
            function="_engine_liveness_watchdog_loop",
        )
        # ~2.5 hours of the measured live warning rate (140/h).
        for i in range(350):
            await si.record_error("log:warning", f"[R-F3844] searxng noise {i}")

    asyncio.run(run())
    entries = _ledger(store)
    errors = [e for e in entries if es.is_reset_type(e.get("type", ""))]
    assert errors, (
        "the ERROR was evicted by a WARNING flood — the ledger cannot answer "
        "'what went wrong' beyond the flood horizon, and its silence is "
        "indistinguishable from a clean period"
    )
    assert "ENGINE DARK" in errors[0].get("message", "")


def test_many_errors_all_survive_a_flood(store):
    """A burst of errors, then a flood. None may be lost while the reserve
    still has room."""
    async def run():
        for i in range(20):
            await si.record_error("log:error", f"genuine failure {i}")
        for i in range(400):
            await si.record_error("log:warning", f"noise {i}")

    asyncio.run(run())
    errors = [e for e in _ledger(store) if es.is_reset_type(e.get("type", ""))]
    assert len(errors) == 20, f"expected all 20 errors kept, got {len(errors)}"


# -- the reserve must not become its own defect -------------------------

def test_the_ledger_still_respects_its_cap(store):
    """A reserve that let the ledger grow without bound would trade an
    eviction bug for an unbounded state_store write."""
    async def run():
        for i in range(600):
            await si.record_error("log:warning", f"noise {i}")

    asyncio.run(run())
    assert len(_ledger(store)) <= si.MAX_ERRORS


def test_an_all_error_ledger_is_still_capped(store):
    """The reserve is a FLOOR for errors, not an exemption from the cap."""
    async def run():
        for i in range(600):
            await si.record_error("log:error", f"failure {i}")

    asyncio.run(run())
    assert len(_ledger(store)) <= si.MAX_ERRORS


def test_warnings_are_not_starved_when_errors_are_rare(store):
    """The healthy case: almost no errors, so warnings should still fill the
    buffer. Reserving slots that nothing uses would shrink the ledger."""
    async def run():
        await si.record_error("log:error", "one real failure")
        for i in range(400):
            await si.record_error("log:warning", f"noise {i}")

    asyncio.run(run())
    entries = _ledger(store)
    warnings = [e for e in entries if not es.is_reset_type(e.get("type", ""))]
    assert len(warnings) >= si.MAX_ERRORS - 10, (
        f"only {len(warnings)} warnings kept of {si.MAX_ERRORS} slots — the "
        "reserve is starving the ordinary case"
    )


def test_the_most_recent_warnings_are_the_ones_kept(store):
    """Recency still governs within the warning half."""
    async def run():
        for i in range(400):
            await si.record_error("log:warning", f"noise {i}")

    asyncio.run(run())
    msgs = [e.get("message", "") for e in _ledger(store)]
    assert "noise 399" in msgs[-1], f"newest warning missing; tail={msgs[-1]!r}"


def test_chronological_order_is_preserved(store):
    """Readers page through this list; reordering it would misreport when
    things happened."""
    async def run():
        for i in range(300):
            kind = "log:error" if i % 50 == 0 else "log:warning"
            await si.record_error(kind, f"event {i}")

    asyncio.run(run())
    ts = [e.get("timestamp", 0) for e in _ledger(store)]
    assert ts == sorted(ts), "ledger is no longer in chronological order"


def test_warnings_survive_an_error_storm(store):
    """The MIRROR of the defect, and it is not symmetric by accident.

    A reserve big enough to claim every slot would fix eviction by inverting
    it — errors would evict the warnings that give them context, and the
    ledger would be just as blind, in the other direction. Both classes must
    survive when both are flooding.

    (Added after mutation testing: raising ERROR_RESERVE to the full 200 left
    every other test green, so nothing pinned this half of the balance.)
    """
    async def run():
        for i in range(300):
            await si.record_error("log:error", f"failure {i}")
            await si.record_error("log:warning", f"noise {i}")

    asyncio.run(run())
    entries = _ledger(store)
    errors = [e for e in entries if es.is_reset_type(e.get("type", ""))]
    warnings = [e for e in entries if not es.is_reset_type(e.get("type", ""))]
    assert errors, "errors starved out by warnings"
    assert warnings, (
        "warnings starved out by the error reserve — an error with no "
        "surrounding context is harder to diagnose, not easier"
    )


# -- the discriminator must stay shared ---------------------------------

def test_the_error_discriminator_is_the_shared_one():
    """`error_streak.is_reset_type` is the ONE definition of 'counts as an
    ERROR'. A local copy here would drift from the gate that reads it."""
    src = (ROOT / "aria_service/intel/self_improve.py").read_text(
        encoding="utf-8", errors="replace")
    i = src.index("def _trim_error_log")
    body = src[i:i + 2000]
    assert "is_reset_type" in body, (
        "the trim must classify via error_streak.is_reset_type, not a local "
        "match on a literal type string"
    )
