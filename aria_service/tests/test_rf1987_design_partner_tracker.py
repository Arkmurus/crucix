"""R-F1987: Design partner tracker — Phase A Gate #7 verifiable tracking.

Tests:
1. Tracker starts empty
2. Adding entries increments count
3. Gate passes at >= 4 entries
4. Update modifies existing entry
5. Stats returns expected shape
6. Persistence across tracker instances
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from aria_service.intel.design_partner_tracker import DesignPartnerTracker


@pytest.fixture
def tracker() -> DesignPartnerTracker:
    """Create a tracker with a temp file for isolation."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    tmp.close()
    t = DesignPartnerTracker(path=tmp.name)
    yield t
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


class TestDesignPartnerTracker:
    """Capability tests for the design partner tracker."""

    def test_starts_empty(self, tracker: DesignPartnerTracker) -> None:
        """A fresh tracker has zero entries."""
        assert tracker.count() == 0
        assert tracker.list_all() == []
        assert not tracker.gate_pass()

    def test_add_increments_count(self, tracker: DesignPartnerTracker) -> None:
        """Adding entries increments the count."""
        tracker.add(name="Acme Corp", contact="john@acme.com")
        assert tracker.count() == 1
        tracker.add(name="Beta Ltd", contact="jane@beta.com")
        assert tracker.count() == 2

    def test_gate_passes_at_four(self, tracker: DesignPartnerTracker) -> None:
        """Gate #7 passes when >= 4 entries exist."""
        for i in range(3):
            tracker.add(name=f"Company {i}", contact=f"contact{i}@test.com")
        assert not tracker.gate_pass(), "Gate should not pass at 3 entries"
        tracker.add(name="Company 4", contact="contact4@test.com")
        assert tracker.gate_pass(), "Gate should pass at 4 entries"

    def test_update_modifies_entry(self, tracker: DesignPartnerTracker) -> None:
        """Update changes notes and status."""
        tracker.add(name="Acme Corp", contact="john@acme.com", notes="Initial call")
        entry = tracker.update(0, notes="Follow-up done", status="engaged")
        assert entry is not None
        assert entry.notes == "Follow-up done"
        assert entry.status == "engaged"

    def test_update_invalid_index(self, tracker: DesignPartnerTracker) -> None:
        """Update on invalid index returns None."""
        result = tracker.update(99, notes="test")
        assert result is None

    def test_stats_shape(self, tracker: DesignPartnerTracker) -> None:
        """Stats returns expected keys."""
        tracker.add(name="Acme Corp", contact="john@acme.com", status="contacted")
        tracker.add(name="Beta Ltd", contact="jane@beta.com", status="engaged")
        stats = tracker.stats()
        assert stats["total"] == 2
        assert stats["by_status"]["contacted"] == 1
        assert stats["by_status"]["engaged"] == 1
        assert not stats["gate_pass"]
        assert stats["gate_target"] == 4

    def test_persistence(self, tracker: DesignPartnerTracker) -> None:
        """Data persists across tracker instances."""
        tracker.add(name="Acme Corp", contact="john@acme.com")
        path = tracker._path

        # New tracker reading same file
        t2 = DesignPartnerTracker(path=path)
        assert t2.count() == 1
        entries = t2.list_all()
        assert entries[0]["name"] == "Acme Corp"
        assert entries[0]["contact"] == "john@acme.com"

    def test_add_returns_entry(self, tracker: DesignPartnerTracker) -> None:
        """add() returns the created entry."""
        entry = tracker.add(name="Test Co", contact="test@test.com", notes="test note")
        assert entry.name == "Test Co"
        assert entry.contact == "test@test.com"
        assert entry.notes == "test note"
        assert entry.status == "contacted"


def test_get_tracker_returns_working_singleton(tmp_path, monkeypatch):
    """R-F1990 — get_tracker() returns a usable singleton whose add() persists
    and whose stats() drive the gate (capability test for the module entrypoint)."""
    import aria_service.intel.design_partner_tracker as dpt
    monkeypatch.setattr(dpt, "_tracker", None)
    monkeypatch.setattr(dpt, "_TRACKER_PATH", str(tmp_path / "dp.json"))
    t1 = dpt.get_tracker()
    t2 = dpt.get_tracker()
    assert t1 is t2, "get_tracker must return a singleton"
    t1._path = str(tmp_path / "dp.json")
    t1.add(name="Acme", contact="a@acme.com")
    assert t1.count() == 1
    assert t1.stats()["gate_pass"] is False        # 1 < 4
    assert t1.list_all()[0]["name"] == "Acme"
