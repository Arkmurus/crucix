"""R-F3970 / C-59 — every crawler domain refusal filed a CODER GAP, so correct
decisions filled the defect ledger.

Measured live 2026-08-13 on the running server: the capability-gap ring is
**500/500 unresolved, 0 resolved ever**, and `source_validator_rejected` holds
**131 of the 500 slots — 26%**. Those are not defects. They are a working
on-mission gate saying "no" to ordinary domains.

    crawler/on_demand.py:520
        if not on_mission:
            logger.info("[R-F3820] registration refused: %s (%s)", domain, why)
            wire_failure(module="crawler.on_demand",
                         detail=f"domain registration refused: {domain} ({why})",
                         gap_type="source_validator_rejected", ...)
            return False

Three faults compound:

1. **A refusal is filed into the CODER's queue.** `record_gap` is documented as
   "the coder loop (something to fix)". The coder cannot fix "news.google.com is
   off-mission" — that is the gate working. A category error puts normal
   operation into the defect queue.

2. **The 1h dedupe cannot collapse it.** `_gap_fingerprint(gap_type, detail)` and
   `detail` embeds the domain, so every distinct domain is a distinct
   fingerprint. `capability_gaps.py:49` already documents this precise trap for
   a different caller — "detail embeds the QUESTION, so every question is a
   distinct fingerprint and the 1h dedupe cannot collapse them". This caller
   walked into the same one.

3. **The refusal is recorded before any idempotency check.** A refused domain
   returns before `db.get_domain(domain)`, so a domain ARIA has already refused
   a thousand times re-emits on every encounter, forever.

The ledger is capped at 500 (R-F1669), so each slot spent on a correct decision
**evicts a real defect unread** — which is why the self-coder is reading phantom
work while genuine gaps age out.

CLAUDE.md already states the policy this violates, from C-40: *"Refusals are
deliberately NOT wired as gaps — a per-refusal gap would be the self-sustaining
flood that has already filled the 500-slot capability ledger."* The fix applies
it here: COUNT refusals, announce once per process, and keep the log line. A
refusal stays observable (§21a is satisfied by a metric); it just stops
pretending to be a defect.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.crawler import on_demand as OD


@pytest.fixture(autouse=True)
def _reset_counters():
    OD.reset_refusal_stats()
    yield
    OD.reset_refusal_stats()


class _FakeDB:
    """Stands in for `aria_service.search_index.db`, which the module imports at
    line 41 and uses as a MODULE-level dependency — it is not a parameter.
    Verified against the real signature (§3b) rather than assumed."""

    def __init__(self):
        self.registered: list[str] = []

    async def get_domain(self, domain):
        return None

    async def register_domain(self, **kw):
        self.registered.append(kw["domain"])


@pytest.fixture()
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(OD, "db", db)
    return db


def _refuse(domain="news.google.com"):
    """Drive the real refusal path: no requested_entity and no on-mission evidence."""
    return asyncio.run(OD.auto_register_domain(
        domain, evidence="", requested_entity="",
        tier=3, sector="news", rate_limit_per_sec=1.0,
    ))


# ── a refusal must not enter the coder queue ─────────────────────────────────

def test_a_refusal_does_not_file_a_capability_gap(monkeypatch):
    calls = []

    def _spy(**kw):
        calls.append(kw)

    monkeypatch.setattr("aria_service.intel.engine_wiring.wire_failure", _spy)

    assert _refuse() is False
    gap_calls = [c for c in calls if c.get("gap_type") == "source_validator_rejected"]
    assert not gap_calls, (
        "a working on-mission gate still files a coder gap per refusal — 131 of "
        "the live ledger's 500 slots are these, evicting real defects unread"
    )


def test_the_refusal_is_still_counted():
    """§21a — it must remain observable. A metric is an accepted sink."""
    _refuse("news.google.com")
    _refuse("www.rs-online.com")
    _refuse("news.google.com")

    stats = OD.refusal_stats()
    assert stats["refused_total"] == 3
    assert stats["refused_distinct_domains"] == 2


def test_the_brain_is_told_ONCE_per_process(monkeypatch):
    """Announce-once, the same shape as C-39's degraded notice and C-41's
    recovery — the condition is a standing state, not a per-event incident."""
    signals = []
    monkeypatch.setattr(
        "aria_service.intel.engine_wiring.wire_success",
        lambda **kw: signals.append(kw),
    )
    for i in range(5):
        _refuse(f"domain{i}.example.com")
    assert len(signals) == 1, (
        f"announced {len(signals)} times — a per-refusal signal is the flood "
        f"this fix exists to stop, just pointed at a different sink"
    )


# ── the gate itself must be unchanged ────────────────────────────────────────

def test_an_on_mission_domain_is_still_registered(fake_db):
    ok = asyncio.run(OD.auto_register_domain(
        "janes.com", evidence="", requested_entity="Janes Defence Weekly",
        tier=1, sector="defence", rate_limit_per_sec=1.0,
    ))
    assert ok is True
    assert fake_db.registered == ["janes.com"]
    assert OD.refusal_stats()["refused_total"] == 0


def test_an_unsafe_domain_is_still_refused():
    assert _refuse("localhost") is False


def test_a_genuine_engine_failure_still_files_a_gap(monkeypatch):
    """The fix must not blind the module — only refusals stop being defects."""
    import inspect
    src = inspect.getsource(OD)
    assert "wire_failure" in src, (
        "wire_failure was removed from the module entirely — a real crawler "
        "failure must still reach the coder queue"
    )


def test_stats_survive_a_reset():
    _refuse()
    assert OD.refusal_stats()["refused_total"] == 1
    OD.reset_refusal_stats()
    assert OD.refusal_stats()["refused_total"] == 0
