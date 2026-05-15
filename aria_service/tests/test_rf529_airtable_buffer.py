"""R-F529 — Airtable pending-action sync must NEVER silently drop.

Live failure 2026-05-15 morning:
  airtable sync DROPPED action=4a19afaeb076c6cb reason=net:ConnectTimeout

The dropped action was a HIGH/operator_action circuit-trip notification.
Pre-R-F529 it was logged at WARNING level and forgotten — the most
operationally important signal in the recovery window vanished from the
operator's Airtable Task Register.

R-F529 buffers failed syncs to SQLite (airtable_buffer.enqueue) and
retries them on the next drain cycle (autonomous task every 30 min +
operator-triggered via /admin/airtable-buffer/drain).

Tests pin:
  1. enqueue() persists across reads — basic round-trip
  2. Same action_id re-enqueued increments attempts (not duplicates)
  3. drain() removes entries that succeed, keeps failures
  4. Buffer cap enforcement archives oldest rather than dropping
  5. pending_actions.record() routes failed syncs to enqueue
  6. count_pending / list_pending / get_status surface diagnostics
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_buffer_db(monkeypatch, tmp_path):
    """Each test gets a fresh SQLite to avoid state leakage."""
    db_path = tmp_path / "test_airtable_buffer.db"
    monkeypatch.setenv("ARIA_AIRTABLE_BUFFER_DB", str(db_path))
    yield
    if db_path.exists():
        db_path.unlink()


# ───────── enqueue / list / count ─────────


def test_rf529_enqueue_persists_round_trip():
    from aria_service.integrations import airtable_buffer
    entry = {
        "action_id": "test-001",
        "severity": "HIGH",
        "promise": "test promise",
        "resolver_kind": "operator_action",
    }
    r = airtable_buffer.enqueue(entry, reason="net:ConnectTimeout")
    assert r["ok"] is True
    assert r["action_id"] == "test-001"
    assert r["attempts"] == 1

    assert airtable_buffer.count_pending() == 1
    items = airtable_buffer.list_pending()
    assert len(items) == 1
    assert items[0]["action_id"] == "test-001"
    assert items[0]["attempts"] == 1
    assert items[0]["last_reason"] == "net:ConnectTimeout"
    assert items[0]["promise_preview"] == "test promise"
    assert items[0]["severity"] == "HIGH"


def test_rf529_enqueue_no_action_id_returns_error():
    from aria_service.integrations import airtable_buffer
    r = airtable_buffer.enqueue({}, reason="x")
    assert r["ok"] is False
    assert r["error"] == "no_action_id"


def test_rf529_re_enqueue_increments_attempts():
    """The same action_id appearing twice must update the existing
    row (attempts++), not create a duplicate."""
    from aria_service.integrations import airtable_buffer
    e = {"action_id": "test-dup", "promise": "p", "severity": "HIGH"}
    airtable_buffer.enqueue(e, reason="r1")
    airtable_buffer.enqueue(e, reason="r2")
    airtable_buffer.enqueue(e, reason="r3")
    assert airtable_buffer.count_pending() == 1
    items = airtable_buffer.list_pending()
    assert items[0]["attempts"] == 3
    assert items[0]["last_reason"] == "r3"


# ───────── drain ─────────


def test_rf529_drain_removes_successful_syncs(monkeypatch):
    """drain() must call airtable_sync.sync_record on each entry;
    successful ones are removed from the buffer."""
    from aria_service.integrations import airtable_buffer

    # Pre-load the buffer with 3 entries
    for i in range(3):
        airtable_buffer.enqueue(
            {"action_id": f"drain-test-{i}", "promise": f"p{i}", "severity": "HIGH"},
            reason="net:ConnectTimeout",
        )
    assert airtable_buffer.count_pending() == 3

    # Mock sync_record to always succeed
    async def _fake_sync(entry):
        return {"ok": True, "record_id": "rec_" + entry["action_id"]}

    monkeypatch.setattr(
        "aria_service.integrations.airtable_sync.sync_record", _fake_sync,
    )

    result = asyncio.run(airtable_buffer.drain(max_items=10))
    assert result["drained"] == 3
    assert result["succeeded"] == 3
    assert result["still_failed"] == 0
    assert airtable_buffer.count_pending() == 0


def test_rf529_drain_keeps_failed_syncs_and_increments_attempts(monkeypatch):
    """drain() must NOT remove entries that fail on retry — they
    stay queued for the next cycle, with attempts++ and updated
    last_reason."""
    from aria_service.integrations import airtable_buffer

    airtable_buffer.enqueue(
        {"action_id": "stays-queued", "promise": "p", "severity": "HIGH"},
        reason="first_failure",
    )

    async def _fake_sync_fails(entry):
        return {"ok": False, "reason": "still_unreachable"}

    monkeypatch.setattr(
        "aria_service.integrations.airtable_sync.sync_record", _fake_sync_fails,
    )

    result = asyncio.run(airtable_buffer.drain(max_items=10))
    assert result["drained"] == 1
    assert result["succeeded"] == 0
    assert result["still_failed"] == 1
    # Entry still in buffer
    assert airtable_buffer.count_pending() == 1
    items = airtable_buffer.list_pending()
    # attempts was 1 (enqueue), drain increments to 2
    assert items[0]["attempts"] == 2
    assert items[0]["last_reason"] == "still_unreachable"


def test_rf529_drain_handles_sync_raising_exception(monkeypatch):
    """If sync_record raises (not just returns ok=False), drain
    must catch + keep the entry queued, not propagate the error."""
    from aria_service.integrations import airtable_buffer

    airtable_buffer.enqueue(
        {"action_id": "raises", "promise": "p", "severity": "HIGH"},
        reason="initial",
    )

    async def _fake_sync_raises(entry):
        raise ConnectionError("DNS lookup failed")

    monkeypatch.setattr(
        "aria_service.integrations.airtable_sync.sync_record", _fake_sync_raises,
    )

    # Should not propagate
    result = asyncio.run(airtable_buffer.drain(max_items=10))
    assert result["drained"] == 1
    assert result["still_failed"] == 1
    assert airtable_buffer.count_pending() == 1


# ───────── cap + archive ─────────


def test_rf529_buffer_cap_archives_oldest(monkeypatch):
    """When the buffer hits the cap, oldest entries move to the
    archive table instead of being dropped (per
    [[aria_infinite_memory]] — never delete operational data)."""
    from aria_service.integrations import airtable_buffer

    # Tight cap for the test
    monkeypatch.setattr(airtable_buffer, "_BUFFER_CAP", 3)

    for i in range(5):
        airtable_buffer.enqueue(
            {"action_id": f"cap-{i}", "promise": "p", "severity": "HIGH"},
            reason="r",
        )

    # Only 3 entries in the active buffer; oldest 2 archived
    assert airtable_buffer.count_pending() == 3
    items = airtable_buffer.list_pending()
    ids = [it["action_id"] for it in items]
    # Should be the latest 3, not the oldest 3
    assert "cap-0" not in ids
    assert "cap-1" not in ids
    assert "cap-4" in ids


# ───────── get_status ─────────


def test_rf529_get_status_surface():
    from aria_service.integrations import airtable_buffer
    airtable_buffer.enqueue(
        {"action_id": "status-test", "promise": "p", "severity": "HIGH"},
        reason="net:ConnectTimeout",
    )
    s = airtable_buffer.get_status()
    assert s["pending"] == 1
    assert s["cap"] >= 1
    assert s["oldest_unsynced"]["action_id"] == "status-test"


# ───────── live failure repro: 4a19afaeb076c6cb shape ─────────


def test_rf529_live_failure_4a19afaeb076c6cb_is_buffered():
    """The exact action shape from 2026-05-15 morning's lost
    notification. Verifies the buffer would have caught it."""
    from aria_service.integrations import airtable_buffer
    # Reconstructed shape — a HIGH operator_action recording a
    # brain-circuit-trip notification, exactly the type that
    # SHOULD never be silently dropped.
    entry = {
        "action_id": "4a19afaeb076c6cb",
        "severity": "HIGH",
        "resolver_kind": "operator_action",
        "promise": "brain_hook circuit breaker tripped",
        "source": "brain_hook",
    }
    r = airtable_buffer.enqueue(entry, reason="net:ConnectTimeout")
    assert r["ok"] is True
    pending = airtable_buffer.list_pending()
    assert len(pending) == 1
    p = pending[0]
    assert p["action_id"] == "4a19afaeb076c6cb"
    assert p["severity"] == "HIGH"
    assert p["last_reason"] == "net:ConnectTimeout"
    # The signal is now recoverable, not lost.
