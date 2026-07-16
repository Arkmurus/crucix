"""R-F2673 — capability tests for the public design-partner application funnel.

The user-visible contract this guards: a PUBLIC self-service application
(partners.html → /api/design-partners/apply → status='applied') must NEVER
move Phase A gate #7. Only an operator qualifying a partner
(contacted/engaged/onboarded) counts. This keeps gate #7 operator-owned
(CLAUDE.md §1) even with an open public funnel.

Before R-F2673, DesignPartnerTracker.gate_pass() counted EVERY entry
(count() >= 4), so any public application would have inflated the gate.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from aria_service.intel.design_partner_tracker import (
    DesignPartnerTracker,
    _QUALIFIED_STATUSES,
)


def _fresh_tracker() -> DesignPartnerTracker:
    d = tempfile.mkdtemp()
    return DesignPartnerTracker(path=os.path.join(d, "design_partners.json"))


def test_public_applications_do_not_move_gate7():
    """The core honesty contract: 5 public applications → gate stays CLOSED."""
    t = _fresh_tracker()
    for i in range(5):
        t.add(name=f"Applicant{i}", contact=f"a{i}@x.com",
              status="applied", source="public_application")
    stats = t.stats()
    assert stats["total"] == 5
    assert stats["qualified"] == 0, "public applications must not count"
    assert stats["gate_pass"] is False
    assert t.gate_pass() is False


def test_operator_qualification_moves_gate7():
    """Only an operator moving records to a qualifying status closes the gate."""
    t = _fresh_tracker()
    for i in range(4):
        t.add(name=f"Applicant{i}", contact=f"a{i}@x.com",
              status="applied", source="public_application")
    assert t.gate_pass() is False          # applications alone: closed
    for i in range(4):
        t.update(i, status="engaged")      # operator qualifies each
    stats = t.stats()
    assert stats["qualified"] == 4
    assert stats["gate_pass"] is True
    assert t.gate_pass() is True


def test_declined_never_counts():
    t = _fresh_tracker()
    for i in range(4):
        t.add(name=f"P{i}", contact=f"p{i}@x.com", status="engaged")
    assert t.gate_pass() is True
    t.update(0, status="declined")         # operator declines one
    assert t.qualified_count() == 3
    assert t.gate_pass() is False


def test_qualified_statuses_are_operator_set_only():
    """'applied' and 'declined' must be OUTSIDE the qualifying set."""
    assert "applied" not in _QUALIFIED_STATUSES
    assert "declined" not in _QUALIFIED_STATUSES
    assert _QUALIFIED_STATUSES == frozenset({"contacted", "engaged", "onboarded"})


def test_persistence_round_trips_source_company_applied():
    """A saved 'applied' row with source+company reloads intact (new fields)."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "dp.json")
    t1 = DesignPartnerTracker(path=path)
    t1.add(name="Acme", contact="jane@acme.com", status="applied",
           source="public_application", company="Acme Risk", notes="wants DD pilot")
    # a second tracker reading the same file sees the persisted record
    t2 = DesignPartnerTracker(path=path)
    rows = t2.list_all()
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "applied"
    assert r["source"] == "public_application"
    assert r["company"] == "Acme Risk"
    assert t2.qualified_count() == 0


def test_stats_shape_for_admin_ui():
    """stats() exposes total + qualified + by_status so the admin UI can show
    applications distinctly while the gate uses qualified only."""
    t = _fresh_tracker()
    t.add(name="A", contact="a@x.com", status="applied", source="public_application")
    t.add(name="B", contact="b@x.com", status="engaged")
    stats = t.stats()
    assert stats["total"] == 2
    assert stats["qualified"] == 1
    assert stats["by_status"] == {"applied": 1, "engaged": 1}
    assert stats["gate_target"] == 4


def test_gate7_renders_qualified_not_total(monkeypatch):
    """Capability: phase_gates gate #7 reports the QUALIFIED count, so a store
    full of applications reads as 0/4 — not total/4. Invokes the real gate."""
    import asyncio
    from aria_service.intel import phase_gates, design_partner_tracker

    t = _fresh_tracker()
    for i in range(6):
        t.add(name=f"App{i}", contact=f"a{i}@x.com",
              status="applied", source="public_application")
    t.update(0, status="engaged")  # 1 qualified, 5 applied
    monkeypatch.setattr(design_partner_tracker, "get_tracker", lambda: t)

    gates = asyncio.get_event_loop().run_until_complete(
        phase_gates.compute_phase_gates()
    )
    g7 = gates["gates"]["gate_7_design_partners"] if "gates" in gates else gates["gate_7_design_partners"]
    # value is the qualified count (1), NOT total (6); gate stays open.
    assert g7["value"] == 1, f"gate #7 should report qualified (1), got {g7['value']}"
    assert g7["pass"] is False


if __name__ == "__main__":
    # Fast, no-async subset — runnable even where the full async suite hangs.
    for fn in (test_public_applications_do_not_move_gate7,
               test_operator_qualification_moves_gate7,
               test_declined_never_counts,
               test_qualified_statuses_are_operator_set_only,
               test_persistence_round_trips_source_company_applied,
               test_stats_shape_for_admin_ui):
        fn()
        print(f"PASS {fn.__name__}")
    print("all tracker-level capability tests passed")
