"""R-F602 — DD Layer Shell-co indicator-row Findings formalisation.

Past observation 2026-05-16 self-assessment: ARIA flagged the
10-indicator ghost checklist as "proven effective" but the orchestrator
was only surfacing the TOTAL score as a Finding row. The individual
triggered indicators ("mail-drop address", "no LinkedIn presence",
"nominee director") were buried in the identity.ghost_score dict and
invisible to a client reading the DD report.

For credibility each triggered indicator must surface as its own
auditable Finding row so a client can challenge "why is this scored
2/2 on mail-drop?" by reading the dedicated row.
"""
from __future__ import annotations

from types import SimpleNamespace


def _make_ghost(classification: str, indicators: list[dict]):
    """Build a duck-typed GhostScoreResult for pure-helper testing."""
    inds = []
    for d in indicators:
        inds.append(SimpleNamespace(
            id=d["id"],
            label=d["label"],
            max_points=d["max_points"],
            points=d["points"],
            reason=d.get("reason", ""),
        ))
    triggered = [i for i in inds if i.points > 0]
    return SimpleNamespace(
        classification=classification,
        indicators=inds,
        triggered=lambda: triggered,
    )


# ══════════════════════════════════════════════════════════════════
# PART A — helper logic
# ══════════════════════════════════════════════════════════════════

def test_rf602_green_classification_emits_no_indicator_findings():
    """Clean reports must NOT be cluttered with indicator rows."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    ghost = _make_ghost("GREEN", [
        {"id": 1, "label": "Age", "max_points": 2, "points": 0},
        {"id": 3, "label": "Address", "max_points": 2, "points": 0},
    ])
    assert _emit_ghost_indicator_findings(ghost) == []


def test_rf602_amber_emits_one_finding_per_triggered_indicator():
    """AMBER report → one Finding per indicator with points > 0."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    ghost = _make_ghost("AMBER-DARK", [
        {"id": 1, "label": "Age", "max_points": 2, "points": 2,
         "reason": "Entity formed 3 months ago"},
        {"id": 2, "label": "Digital presence", "max_points": 3, "points": 0},
        {"id": 3, "label": "Registered address", "max_points": 2, "points": 2,
         "reason": "Mail-drop address"},
        {"id": 6, "label": "Director quality", "max_points": 3, "points": 1,
         "reason": "Director has 14 other appointments"},
    ])
    findings = _emit_ghost_indicator_findings(ghost)
    assert len(findings) == 3, (
        f"R-F602: expected 3 triggered findings, got {len(findings)}"
    )
    titles = [f.title for f in findings]
    assert any("Age" in t and "(2/2)" in t for t in titles)
    assert any("Registered address" in t for t in titles)
    assert any("Director quality" in t for t in titles)


def test_rf602_red_emits_one_finding_per_triggered_indicator():
    """RED report → indicator-row Findings (same shape as AMBER)."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    ghost = _make_ghost("RED", [
        {"id": 1, "label": "Age", "max_points": 2, "points": 2, "reason": "new"},
        {"id": 7, "label": "Address shared with flagged", "max_points": 2, "points": 2, "reason": "shared"},
    ])
    findings = _emit_ghost_indicator_findings(ghost)
    assert len(findings) == 2


def test_rf602_severity_proportional_to_weight_ratio():
    """Full-weight indicator → amber; partial trigger → info."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    ghost = _make_ghost("AMBER-LIGHT", [
        # Full weight (2/2 = 1.0 ratio) → amber
        {"id": 1, "label": "Age", "max_points": 2, "points": 2, "reason": "new"},
        # Half weight (1/2 = 0.5 ratio) → amber (threshold inclusive)
        {"id": 3, "label": "Address", "max_points": 2, "points": 1, "reason": "virt"},
        # Partial (1/3 = 0.33 ratio) → info
        {"id": 2, "label": "Digital", "max_points": 3, "points": 1, "reason": "no LI"},
    ])
    findings = _emit_ghost_indicator_findings(ghost)
    sev_by_id = {int(f.title.split("#")[1].split(":")[0]): f.severity for f in findings}
    assert sev_by_id[1] == "amber"
    assert sev_by_id[3] == "amber"
    assert sev_by_id[2] == "info"


def test_rf602_finding_carries_indicator_reason():
    """The Finding detail must come from the indicator's reason string —
    that's what gives the report its 'why' provenance."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    ghost = _make_ghost("AMBER-DARK", [
        {"id": 3, "label": "Address", "max_points": 2, "points": 2,
         "reason": "Mail-drop at 30 Old Compton St — 47 cohabitants"},
    ])
    findings = _emit_ghost_indicator_findings(ghost)
    assert len(findings) == 1
    assert "47 cohabitants" in findings[0].detail


def test_rf602_finding_confidence_is_probable():
    """Heuristic indicators are PROBABLE, not CONFIRMED."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    ghost = _make_ghost("AMBER-LIGHT", [
        {"id": 1, "label": "Age", "max_points": 2, "points": 1, "reason": "x"},
    ])
    findings = _emit_ghost_indicator_findings(ghost)
    assert findings[0].confidence == "PROBABLE"


def test_rf602_caps_at_twelve_indicators():
    """Defensive: never emit more than 12 indicator rows."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    # Synthetic 20-indicator result
    inds = [
        {"id": i, "label": f"Ind{i}", "max_points": 2, "points": 1, "reason": "x"}
        for i in range(1, 21)
    ]
    ghost = _make_ghost("RED", inds)
    findings = _emit_ghost_indicator_findings(ghost)
    assert len(findings) <= 12


def test_rf602_empty_or_none_ghost_returns_empty():
    """Defensive: bad inputs must not crash."""
    from aria_service.intel.dd_orchestrator import _emit_ghost_indicator_findings
    assert _emit_ghost_indicator_findings(None) == []
    # Object without .classification attr
    bad = SimpleNamespace(foo="bar")
    assert _emit_ghost_indicator_findings(bad) == []


# ══════════════════════════════════════════════════════════════════
# PART B — orchestrator wiring
# ══════════════════════════════════════════════════════════════════

def test_rf602_helper_invoked_from_run_identity_person_in_source():
    """Source-level pin: _run_identity_person must call
    _emit_ghost_indicator_findings AFTER the headline score Finding.
    Otherwise the indicator rows would render before the headline,
    confusing report consumers."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    # Anchor on the call site (inside the try block where ghost is built).
    score_call = src.find("_dd.score_ghost_indicators(profile)")
    assert score_call > 0, "score_ghost_indicators call site not found"
    # The emission call must come AFTER score_call but BEFORE the
    # except clause that follows.
    region_end = src.find("except Exception as e:", score_call)
    region = src[score_call:region_end] if region_end > 0 else src[score_call:score_call + 4000]
    assert "_emit_ghost_indicator_findings" in region, (
        "R-F602: indicator-Finding emission missing from the "
        "_run_identity_person ghost-scoring block."
    )
