"""R-F611 — Promote cert_transparency hits to compliance Findings rows.

cert_transparency (R-F585) inspects Certificate Transparency logs for
the counterparty's domain and scores a shell-broker fingerprint.
Output sits in report.extensions["by_module"]["cert_transparency"]
but never becomes a Finding row.

R-F611 adds _emit_cert_transparency_findings(), a pure helper that
turns the CT block into auditable headline + per-signal Finding rows.
Same surgical pattern as R-F600 / R-F601 / R-F602 / R-F610.
"""
from __future__ import annotations


def _block(severity, *, query="example.com", cert_count=0, apex_count=0,
           issuer_count=0, shell_score=0, signals=None):
    return {
        "module": "cert_transparency",
        "r_number": "R-F585",
        "query": query,
        "cert_count": cert_count,
        "shell_score": shell_score,
        "signals": signals or [],
        "issuer_count": issuer_count,
        "apex_count": apex_count,
        "severity": severity,
        "summary": "(test fixture)",
    }


def test_rf611_none_severity_emits_no_findings():
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    assert _emit_cert_transparency_findings(_block("NONE")) == []


def test_rf611_low_severity_emits_info_findings():
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "LOW",
        query="modirumgespi.com",
        cert_count=8,
        apex_count=2,
        issuer_count=1,
        shell_score=35,
        signals=["short_lived_certs", "one_issuer"],
    )
    findings = _emit_cert_transparency_findings(block)
    assert len(findings) == 3  # 1 headline + 2 signal rows
    for f in findings:
        assert f.severity == "info"


def test_rf611_elevated_severity_amber_headline():
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "ELEVATED",
        cert_count=50,
        apex_count=20,
        issuer_count=1,
        shell_score=85,
        signals=["many_apex_one_issuer", "shell_cluster"],
    )
    findings = _emit_cert_transparency_findings(block)
    assert findings[0].severity == "amber"


def test_rf611_critical_keyword_escalates_to_amber():
    """Signals matching shell/typo/squat/cluster/many_apex/one_issuer/
    short_lived/anomal/suspicious get amber severity when bundle is
    ELEVATED."""
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "ELEVATED",
        shell_score=70,
        signals=["shell_pattern", "typo_squat"],
    )
    findings = _emit_cert_transparency_findings(block)
    # Headline + 2 signal rows, all amber
    assert all(f.severity == "amber" for f in findings)


def test_rf611_benign_signal_stays_info_even_elevated():
    """ELEVATED bundle but generic signal that doesn't match critical
    keywords → info row."""
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "ELEVATED",
        shell_score=65,
        signals=["routine_renewal"],  # no critical keyword
    )
    findings = _emit_cert_transparency_findings(block)
    assert findings[0].severity == "amber"
    assert findings[1].severity == "info"


def test_rf611_caps_at_five_signal_rows():
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "LOW",
        shell_score=30,
        signals=[f"sig_{i}" for i in range(20)],
    )
    findings = _emit_cert_transparency_findings(block)
    assert len(findings) == 6  # 1 headline + 5 signal rows


def test_rf611_query_appears_in_headline():
    """Headline must name the queried domain — operator needs to know
    WHICH domain triggered the score."""
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "LOW",
        query="lngtradinginternationalpanamasa.com",
        shell_score=30,
        signals=["x"],
    )
    findings = _emit_cert_transparency_findings(block)
    assert "lngtradinginternationalpanamasa.com" in findings[0].title


def test_rf611_headline_warns_about_san_verification():
    """Headline must instruct operator to verify SAN list before
    flagging — protects against false-firing on legitimate domains."""
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "ELEVATED",
        shell_score=80,
        signals=["shell_cluster"],
    )
    findings = _emit_cert_transparency_findings(block)
    detail_lc = findings[0].detail.lower()
    assert "verify" in detail_lc
    assert "san" in detail_lc


def test_rf611_confidence_is_probable():
    """Heuristic fingerprint → PROBABLE, no gate demotion."""
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    block = _block(
        "ELEVATED",
        shell_score=80,
        signals=["shell_pattern"],
    )
    findings = _emit_cert_transparency_findings(block)
    for f in findings:
        assert f.confidence == "PROBABLE"
        assert f.gate_demoted is False


def test_rf611_handles_bad_inputs():
    from aria_service.intel.dd_orchestrator import _emit_cert_transparency_findings
    assert _emit_cert_transparency_findings(None) == []
    assert _emit_cert_transparency_findings({}) == []
    assert _emit_cert_transparency_findings("not a dict") == []
    bad = _block("LOW", shell_score=30, signals="not a list")
    findings = _emit_cert_transparency_findings(bad)
    # Headline only, no signal rows
    assert len(findings) == 1


def test_rf611_helper_invoked_from_run_all_extensions_block_in_source():
    """Source-level pin: orchestrator must call _emit_cert_transparency_findings
    after run_all_extensions completes, AFTER R-F600 + R-F610."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    rae_idx = src.find("run_all_extensions(target, report")
    region_end = src.find("\n        except asyncio.TimeoutError", rae_idx)
    region = (
        src[rae_idx:region_end]
        if region_end > 0
        else src[rae_idx:rae_idx + 8000]
    )
    assert "_emit_cert_transparency_findings" in region, (
        "R-F611: cert_transparency Finding emission missing from extension block"
    )
    # Must have at least 3 append calls now (R-F600 + R-F610 + R-F611)
    cnt = region.count("report.compliance.findings.append")
    assert cnt >= 3, (
        f"R-F611: expected ≥3 compliance.findings.append in extension block; got {cnt}"
    )
