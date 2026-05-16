"""R-F612 — Promote ECCN keyword-indicator hits to compliance Findings rows.

eccn_lookup (R-F581) + dd_layer_extensions.run_eccn_indicator_check
(R-F586) already return potential ECCN matches against the equipment
text in a DD target. The block sits inside report.extensions[
"by_module"]["eccn_lookup"] and never becomes Finding rows — invisible
to a client reading the DD report.

R-F612 adds _emit_eccn_findings(), the last of the R-F584-F587 bundle
to get the Findings-row promotion. Per Clause 3 + Clause 14, every
hit is tagged INDICATOR — operator must consult licensed export
counsel before applying for an export licence.
"""
from __future__ import annotations


def _block(severity, *, hits=None, summary=""):
    return {
        "module":   "eccn_lookup",
        "r_number": "R-F586",
        "hits":     hits or [],
        "severity": severity,
        "summary":  summary or "(test fixture)",
    }


def _hit(eccn, title, regime="EAR", match_count=1, match_keywords=None,
         reference_url="https://www.bis.doc.gov/index.php/regulations/commerce-control-list-ccl"):
    return {
        "eccn": eccn,
        "title": title,
        "regime": regime,
        "match_count": match_count,
        "match_keywords": match_keywords or [],
        "reference_url": reference_url,
        "tier": "1a",
    }


def test_rf612_none_severity_emits_no_findings():
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    assert _emit_eccn_findings(_block("NONE")) == []


def test_rf612_empty_hits_emits_no_findings():
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    assert _emit_eccn_findings(_block("LOW", hits=[])) == []


def test_rf612_low_severity_emits_info_findings():
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "LOW",
        hits=[_hit("9A012", "UAV components", match_count=1)],
    )
    findings = _emit_eccn_findings(block)
    assert len(findings) == 2  # 1 headline + 1 hit
    for f in findings:
        assert f.severity == "info"


def test_rf612_elevated_amber_headline_and_high_match_amber_hit():
    """ELEVATED bundle + hit with match_count ≥ 2 → amber on both."""
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "ELEVATED",
        hits=[_hit("ML1", "Smooth-bore weapons", match_count=3)],
    )
    findings = _emit_eccn_findings(block)
    assert all(f.severity == "amber" for f in findings)


def test_rf612_low_match_count_stays_info_even_elevated():
    """ELEVATED bundle but specific hit has match_count = 1 → info row."""
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "ELEVATED",
        hits=[
            _hit("ML1", "Smooth-bore weapons", match_count=3),
            _hit("3A001", "Rad-hard electronics", match_count=1),
        ],
    )
    findings = _emit_eccn_findings(block)
    # Headline amber, ML1 amber, 3A001 info
    assert findings[0].severity == "amber"  # headline
    assert findings[1].severity == "amber"  # ML1 (match_count=3)
    assert findings[2].severity == "info"   # 3A001 (match_count=1)


def test_rf612_caps_at_five_hits():
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "LOW",
        hits=[_hit(f"ML{i}", f"Item {i}", match_count=1) for i in range(1, 11)],
    )
    findings = _emit_eccn_findings(block)
    assert len(findings) == 6  # 1 headline + 5 hits


def test_rf612_headline_carries_indicator_clause_3_warning():
    """Headline must warn this is INDICATOR not legal classification.
    Mandatory per Clause 3 + Clause 14."""
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "LOW",
        hits=[_hit("ML1", "Weapons", match_count=1)],
    )
    findings = _emit_eccn_findings(block)
    detail_lc = findings[0].detail.lower()
    assert "indicator" in detail_lc
    assert ("licensed export counsel" in detail_lc
            or "consult" in detail_lc)


def test_rf612_per_hit_row_carries_indicator_warning():
    """Each per-hit Finding must also include the indicator warning —
    so a single Finding shown in isolation still carries the disclaimer."""
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "LOW",
        hits=[_hit("ML1", "Weapons", match_count=1)],
    )
    findings = _emit_eccn_findings(block)
    # findings[1] is the per-hit row
    assert "INDICATOR" in findings[1].detail or "indicator" in findings[1].detail.lower()


def test_rf612_per_hit_row_carries_reference_url_per_clause_15():
    """Per-hit row must carry the BIS / Wassenaar reference URL inline
    per Clause 15."""
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "LOW",
        hits=[_hit(
            "ML1",
            "Weapons",
            match_count=1,
            reference_url="https://www.bis.doc.gov/eccn/ml1",
        )],
    )
    findings = _emit_eccn_findings(block)
    row = findings[1]
    assert "https://www.bis.doc.gov/eccn/ml1" in row.source
    assert "https://www.bis.doc.gov/eccn/ml1" in row.detail


def test_rf612_per_hit_row_includes_matched_keywords():
    """Detail row must surface which keywords triggered the match —
    auditability requirement."""
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "LOW",
        hits=[_hit(
            "9A012",
            "UAV components",
            match_count=2,
            match_keywords=["unmanned aerial vehicle", "rotor"],
        )],
    )
    findings = _emit_eccn_findings(block)
    assert "unmanned aerial vehicle" in findings[1].detail


def test_rf612_confidence_is_probable():
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    block = _block(
        "ELEVATED",
        hits=[_hit("ML1", "Weapons", match_count=3)],
    )
    findings = _emit_eccn_findings(block)
    for f in findings:
        assert f.confidence == "PROBABLE"
        assert f.gate_demoted is False


def test_rf612_handles_bad_inputs():
    from aria_service.intel.dd_orchestrator import _emit_eccn_findings
    assert _emit_eccn_findings(None) == []
    assert _emit_eccn_findings({}) == []
    assert _emit_eccn_findings("not a dict") == []
    assert _emit_eccn_findings(_block("LOW", hits="not a list")) == []


def test_rf612_helper_invoked_from_run_all_extensions_block_in_source():
    """Source pin: orchestrator must call _emit_eccn_findings inside
    the run_all_extensions try, AFTER R-F600/F610/F611."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    rae_idx = src.find("run_all_extensions(target, report")
    region_end = src.find("\n        except asyncio.TimeoutError", rae_idx)
    region = (
        src[rae_idx:region_end]
        if region_end > 0
        else src[rae_idx:rae_idx + 10000]
    )
    assert "_emit_eccn_findings" in region, (
        "R-F612: ECCN Finding emission missing from extension block"
    )
    cnt = region.count("report.compliance.findings.append")
    assert cnt >= 4, (
        f"R-F612: expected ≥4 compliance.findings.append (R-F600 + "
        f"R-F610 + R-F611 + R-F612); got {cnt}"
    )
