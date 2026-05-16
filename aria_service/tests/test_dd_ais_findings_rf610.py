"""R-F610 — Promote AIS dark-voyage signals to compliance Findings rows.

ais_gap_detector (R-F587) + dd_layer_extensions.run_ais_gap_check
already produce a structured block when vessel positions are supplied
to a DD target. The block sits in report.extensions["by_module"]
["ais_gap_detector"] and never becomes a Finding row — invisible to
a client reading the DD report.

R-F610 adds _emit_ais_findings(), a pure helper that turns the AIS
block into auditable headline + per-signal Finding rows. Same pattern
as R-F600 (litigation) / R-F601 (FATF) / R-F602 (shell-co indicators).

Important: this layer only fires when vessel positions are present in
the target — typical entity DD has no vessel data so the layer is
skipped, no Findings emitted, no clutter.
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════
# PART A — helper logic
# ══════════════════════════════════════════════════════════════════

def _block(severity, *, vessel_id="IMO_9999999", total_gaps=0,
           longest_gap_h=0, score=0, signals=None):
    """Build an ais_gap_detector extension result block."""
    return {
        "module":      "ais_gap_detector",
        "r_number":    "R-F587",
        "vessel_id":   vessel_id,
        "total_gaps":  total_gaps,
        "longest_gap_h": longest_gap_h,
        "score":       score,
        "signals":     signals or [],
        "severity":    severity,
        "summary":     "(test fixture)",
    }


def test_rf610_none_severity_emits_no_findings():
    """Clean vessel data (or no vessel data) → no clutter."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    assert _emit_ais_findings(_block("NONE")) == []


def test_rf610_low_severity_emits_info_findings():
    """LOW (score 15..49) → info headline + info per-signal."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "LOW",
        vessel_id="IMO_9876543",
        total_gaps=2,
        longest_gap_h=8,
        score=25,
        signals=["AIS_gap_short_8h", "Port_call_undocumented"],
    )
    findings = _emit_ais_findings(block)
    assert len(findings) == 3  # 1 headline + 2 signal rows
    for f in findings:
        assert f.severity == "info"


def test_rf610_elevated_severity_amber_headline():
    """ELEVATED (score ≥50 or critical gap) → amber headline."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "ELEVATED",
        vessel_id="IMO_1234567",
        total_gaps=5,
        longest_gap_h=72,
        score=85,
        signals=["AIS_gap_critical_72h", "Dark_voyage_pattern"],
    )
    findings = _emit_ais_findings(block)
    # Headline should be amber
    assert findings[0].severity == "amber"


def test_rf610_critical_signal_escalates_to_amber():
    """ELEVATED + critical/dark/anomalous signal → amber signal row."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "ELEVATED",
        score=80,
        signals=["AIS_gap_critical_60h"],
    )
    findings = _emit_ais_findings(block)
    # 1 headline (amber) + 1 critical signal (amber)
    assert all(f.severity == "amber" for f in findings)


def test_rf610_non_critical_signal_stays_info_even_when_elevated():
    """ELEVATED bundle but specific signal is non-critical → info row."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "ELEVATED",
        score=60,
        signals=["Routine_maintenance_window"],
    )
    findings = _emit_ais_findings(block)
    # Headline amber, signal info (no "critical"/"dark"/"anomal" keyword)
    assert findings[0].severity == "amber"
    assert findings[1].severity == "info"


def test_rf610_caps_at_five_signal_rows():
    """Defensive: never emit more than 5 signal rows."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "LOW",
        score=20,
        signals=[f"Signal_{i}" for i in range(20)],
    )
    findings = _emit_ais_findings(block)
    # 1 headline + max 5 signal rows
    assert len(findings) == 6


def test_rf610_vessel_id_appears_in_headline():
    """Headline must name the vessel — credibility requires the
    operator to know WHICH vessel is being flagged."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "LOW",
        vessel_id="IMO_9123456",
        score=30,
        signals=["x"],
    )
    findings = _emit_ais_findings(block)
    assert "IMO_9123456" in findings[0].title


def test_rf610_headline_warns_about_legitimate_explanation():
    """Headline must instruct operator to verify against legitimate
    explanations (port stay, antenna failure) — protects against
    over-firing accusation."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "ELEVATED",
        score=80,
        signals=["Dark_voyage"],
    )
    findings = _emit_ais_findings(block)
    detail = findings[0].detail.lower()
    assert "legitimate" in detail
    assert "verify" in detail or "cross-referenc" in detail


def test_rf610_confidence_is_probable():
    """AIS gaps are factual but interpretation needs human assessment.
    PROBABLE avoids R-5005 CONFIRMED→ASSESSED demotion."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    block = _block(
        "ELEVATED",
        score=80,
        signals=["Dark_voyage_anomaly"],
    )
    findings = _emit_ais_findings(block)
    for f in findings:
        assert f.confidence == "PROBABLE"
        assert f.gate_demoted is False


def test_rf610_handles_bad_inputs():
    """Defensive: None, empty dict, non-list signals → []."""
    from aria_service.intel.dd_orchestrator import _emit_ais_findings
    assert _emit_ais_findings(None) == []
    assert _emit_ais_findings({}) == []
    assert _emit_ais_findings("not a dict") == []
    # signals wrong type — accept the headline but not signal rows
    bad = _block("LOW", score=30, signals="not a list")
    findings = _emit_ais_findings(bad)
    # Should emit just the headline, no signal rows
    assert len(findings) == 1
    assert "AIS" in findings[0].title


# ══════════════════════════════════════════════════════════════════
# PART B — orchestrator wiring
# ══════════════════════════════════════════════════════════════════

def test_rf610_helper_invoked_from_run_all_extensions_block_in_source():
    """Source-level pin: the orchestrator must call _emit_ais_findings
    after run_all_extensions completes, inside the same try block,
    AFTER the R-F600 court-record block (alphabetical-ish ordering)."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    rae_idx = src.find("run_all_extensions(target, report")
    assert rae_idx > 0
    region_end = src.find("\n        except asyncio.TimeoutError", rae_idx)
    region = (
        src[rae_idx:region_end]
        if region_end > 0
        else src[rae_idx:rae_idx + 6000]
    )
    assert "_emit_ais_findings" in region, (
        "R-F610: AIS Finding emission missing from run_all_extensions block"
    )
    # Must append to compliance.findings (same section as R-F600/F601)
    cnt = region.count("report.compliance.findings.append")
    assert cnt >= 2, (
        f"R-F610: expected ≥2 compliance.findings.append calls in the "
        f"extension block (R-F600 + R-F610); got {cnt}"
    )
