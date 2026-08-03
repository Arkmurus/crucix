"""R-F600 — Promote court-record hits to compliance Findings rows.

Past observation 2026-05-16: court_records.py (R-F579) + dd_layer_
extensions.run_court_records_check (R-F584) successfully query
CourtListener + BAILII and stash the result in report.extensions[
"by_module"]["court_records"]. But the individual case hits never
surface as Findings rows in report.compliance — invisible to a
client reading the DD report.

R-F600 adds _emit_court_record_findings(), a pure helper that turns
the extension result into auditable Finding rows (headline +
per-case). Same pattern as R-F601 FATF and R-F602 shell-co
indicator rows.
"""
from __future__ import annotations

from ._source_probe import repo_path


# ══════════════════════════════════════════════════════════════════
# PART A — helper logic (pure function)
# ══════════════════════════════════════════════════════════════════

def _build_block(severity, hits, us_count=0, uk_count=0, summary=""):
    """Helper to build a court_records extension-result block."""
    return {
        "module": "court_records",
        "r_number": "R-F584",
        "entity": "Test Entity",
        "hits": hits,
        "us_count": us_count,
        "uk_count": uk_count,
        "severity": severity,
        "summary": summary,
    }


def _hit(title, court, date, citation_url, snippet="", jurisdiction="US",
        source="courtlistener"):
    return {
        "title": title,
        "court": court,
        "date": date,
        "citation_url": citation_url,
        "snippet": snippet,
        "jurisdiction": jurisdiction,
        "source": source,
        "tier": "1a",
    }


def test_rf600_none_severity_emits_no_findings():
    """Clean entities (no court hits) must NOT clutter the report."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(severity="NONE", hits=[])
    assert _emit_court_record_findings(block) == []


def test_rf600_empty_hits_emits_no_findings_even_with_severity():
    """Defensive: severity says LOW but no hits → no findings."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(severity="LOW", hits=[])
    assert _emit_court_record_findings(block) == []


def test_rf600_low_severity_emits_info_findings():
    """Non-federal hits → severity info."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="LOW",
        us_count=0,
        uk_count=1,
        hits=[_hit(
            title="Re ABC Plc",
            court="Royal Courts of Justice",
            date="2024-06-12",
            citation_url="https://www.bailii.org/case/123",
            snippet="Breach of fiduciary duty alleged",
            jurisdiction="UK",
            source="bailii",
        )],
    )
    findings = _emit_court_record_findings(block)
    assert len(findings) == 2  # 1 headline + 1 case row
    # All info severity
    for f in findings:
        assert f.severity == "info"


def test_rf600_elevated_us_federal_emits_amber():
    """US federal court hit → severity amber on both headline and case row."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="ELEVATED",
        us_count=1,
        uk_count=0,
        hits=[_hit(
            title="United States v. Smith",
            court="S.D.N.Y.",
            date="2024-03-15",
            citation_url="https://www.courtlistener.com/opinion/9999",
            snippet="Wire fraud indictment",
        )],
    )
    findings = _emit_court_record_findings(block)
    assert len(findings) == 2
    # Headline + federal case → both amber
    assert all(f.severity == "amber" for f in findings)


def test_rf600_elevated_state_court_headline_amber_case_info():
    """Elevated bundle but the specific case is state-level → case row
    drops to info while headline stays amber."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="ELEVATED",
        us_count=1,
        uk_count=0,
        hits=[_hit(
            title="People v. Doe",
            court="New York Supreme Court (state-level)",
            date="2024-01-10",
            citation_url="https://www.courtlistener.com/opinion/state",
            snippet="State fraud claim",
        )],
    )
    findings = _emit_court_record_findings(block)
    # Headline amber, state case info
    assert findings[0].severity == "amber"
    assert findings[1].severity == "info"


def test_rf600_case_row_carries_url_inline_per_clause_15():
    """Clause 15: tool-derived facts cite an inline URL."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="LOW",
        us_count=0,
        uk_count=1,
        hits=[_hit(
            title="Re XYZ Ltd",
            court="High Court of Justice",
            date="2024-06-12",
            citation_url="https://www.bailii.org/abc/123",
            jurisdiction="UK",
            source="bailii",
        )],
    )
    findings = _emit_court_record_findings(block)
    case_row = findings[1]
    assert "https://www.bailii.org/abc/123" in case_row.source
    assert "https://www.bailii.org/abc/123" in case_row.detail


def test_rf600_caps_at_five_case_rows():
    """Defensive: never emit more than 5 per-case rows even if upstream
    sends more."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="LOW",
        us_count=10,
        uk_count=0,
        hits=[_hit(
            title=f"Case {i}",
            court="State Court",
            date="2024-01-01",
            citation_url=f"https://x/{i}",
        ) for i in range(10)],
    )
    findings = _emit_court_record_findings(block)
    # 1 headline + 5 case rows max
    assert len(findings) == 6


def test_rf600_confidence_is_probable_to_avoid_gate_demotion():
    """Confidence is PROBABLE (not CONFIRMED) because the name match
    needs human disambiguation. Avoids R-5005 gate demotion path."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="ELEVATED",
        us_count=1,
        uk_count=0,
        hits=[_hit(
            title="U.S. v. Smith",
            court="S.D.N.Y.",
            date="2024-03-15",
            citation_url="https://x/y",
        )],
    )
    findings = _emit_court_record_findings(block)
    for f in findings:
        assert f.confidence == "PROBABLE"
        # Gate must NOT have demoted (since we never set CONFIRMED)
        assert f.gate_demoted is False


def test_rf600_headline_warns_about_name_match_verification():
    """Headline must instruct the operator to verify name match — this
    is the human-disambiguation guardrail."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    block = _build_block(
        severity="LOW",
        us_count=0,
        uk_count=1,
        hits=[_hit(
            title="Case",
            court="High Court",
            date="2024-01-01",
            citation_url="https://x/y",
            jurisdiction="UK",
            source="bailii",
        )],
    )
    findings = _emit_court_record_findings(block)
    assert "VERIFY" in findings[0].detail or "verify" in findings[0].detail.lower()
    assert "fuzzy" in findings[0].detail.lower() or "disambiguation" in findings[0].detail.lower()


def test_rf600_handles_bad_inputs():
    """Defensive: None, empty dict, missing fields → no crash."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    assert _emit_court_record_findings(None) == []
    assert _emit_court_record_findings({}) == []
    assert _emit_court_record_findings("not a dict") == []
    # Hits is wrong type
    bad = _build_block(severity="LOW", hits="not a list")
    assert _emit_court_record_findings(bad) == []


# ══════════════════════════════════════════════════════════════════
# PART B — orchestrator wiring
# ══════════════════════════════════════════════════════════════════

def test_rf600_helper_invoked_from_run_all_extensions_block_in_source():
    """Source-level pin: the orchestrator must call
    _emit_court_record_findings after run_all_extensions completes,
    inside the same try block."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/intel/dd_orchestrator.py")
    ).read_text(encoding="utf-8", errors="ignore")
    # Locate the run_all_extensions call
    rae_idx = src.find("run_all_extensions(target, report")
    assert rae_idx > 0, "run_all_extensions call site not found"
    # The helper invocation must come AFTER the call but BEFORE the
    # outer except clause.
    region_end = src.find("\n        except asyncio.TimeoutError", rae_idx)
    region = (
        src[rae_idx:region_end]
        if region_end > 0
        else src[rae_idx:rae_idx + 4000]
    )
    assert "_emit_court_record_findings" in region, (
        "R-F600: helper not invoked after run_all_extensions"
    )
    assert "report.compliance.findings.append" in region, (
        "R-F600: helper invoked but findings not appended to compliance"
    )
