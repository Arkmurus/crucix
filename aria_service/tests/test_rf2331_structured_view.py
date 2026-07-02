"""R-F2331 — DD report structured render contract.

The web report page used to re-parse WhatsApp markdown into cards, dropping each
finding's detail/source/confidence/citations. structured_view() must emit the full
structured evidence straight from the persisted report dict, decision-first."""
import pytest

from aria_service.intel import dd_schema as S


def _sample_report() -> dict:
    r = S.ARKDDReport()
    r.identity.entity_name = "Rosoboronexport"
    r.identity.entity_type = "company"
    r.identity.jurisdiction = "Russia"
    r.identity.registration_number = "1027700001240"
    r.identity.findings.append(S.Finding(
        severity="hard_stop", title="OFAC SDN match",
        detail="Listed on the US OFAC SDN list (Executive Order 13582).",
        source="sanctions.screen_with_aliases", confidence="CONFIRMED",
    ))
    # Compliance: country risk + a financial-health verdict + a finding
    r.compliance.country_risk = {"headline_risk": "HIGH"}
    r.compliance.financial_health = {"health_verdict": "UNKNOWN",
                                     "summary": "not US-listed"}
    r.compliance.sanctions_regimes = ["US OFAC", "EU", "UK OFSI"]
    r.compliance.findings.append(S.Finding(
        severity="amber", title="High-risk jurisdiction",
        detail="Russia — elevated country risk.", source="risk_indices.get_country_risk",
        confidence="CONFIRMED",
    ))
    r.verification.grounded_rate = 0.33
    r.verification.confidence_floor = "ASSESSED"
    r.risk_classification = "RED"
    r.bottom_line = "Do not proceed — sanctioned entity."
    r.recommendation = "Decline."
    return r.as_dict()


def test_structured_view_preserves_finding_evidence():
    sv = S.structured_view(_sample_report())
    assert sv["entity_name"] == "Rosoboronexport"
    assert sv["risk_classification"] == "RED"
    ident = next(s for s in sv["sections"] if s["key"] == "identity")
    f = ident["findings"][0]
    # The rich evidence the markdown render dropped MUST survive here:
    assert f["title"] == "OFAC SDN match"
    assert "OFAC SDN list" in f["detail"]
    assert f["source"] == "sanctions.screen_with_aliases"
    assert f["sources"] == ["sanctions.screen_with_aliases"]
    assert f["confidence"] in ("CONFIRMED", "ASSESSED")  # gate may demote, but present
    assert f["severity"] == "hard_stop"


def test_structured_view_is_decision_first():
    sv = S.structured_view(_sample_report())
    keys = [s["key"] for s in sv["sections"]]
    # Compliance/sanctions (decision drivers) must come before the digital/verification
    assert keys.index("compliance") < keys.index("verification")
    assert keys[0] == "identity"
    # Verification is a trust FOOTER — last core section
    assert keys[-1] == "verification"
    comp = next(s for s in sv["sections"] if s["key"] == "compliance")
    labels = {h["label"]: h["value"] for h in comp["highlights"]}
    assert labels.get("Country risk") == "HIGH"
    assert labels.get("Financial health") == "UNKNOWN"
    assert "US OFAC" in labels.get("Sanctions regimes", "")


def test_structured_view_findings_severity_ordered():
    r = S.ARKDDReport()
    r.identity.entity_name = "X"
    r.identity.findings.extend([
        S.Finding(severity="info", title="info one", source="s1"),
        S.Finding(severity="red", title="red one", source="s2"),
        S.Finding(severity="amber", title="amber one", source="s3"),
    ])
    sv = S.structured_view(r.as_dict())
    ident = next(s for s in sv["sections"] if s["key"] == "identity")
    sevs = [f["severity"] for f in ident["findings"]]
    assert sevs == ["red", "amber", "info"]  # worst-first


def test_structured_view_partial_report_never_raises():
    # Quick-mode / near-blank report must still return a contract, not raise.
    sv = S.structured_view({"run_id": "dd_x", "risk_classification": "GREEN"})
    assert sv["run_id"] == "dd_x"
    assert isinstance(sv["sections"], list)
    # core sections (identity, compliance, verification) always present
    core = {s["key"] for s in sv["sections"]}
    assert {"identity", "compliance", "verification"} <= core


def test_structured_view_digital_evidence_carries_urls():
    r = S.ARKDDReport()
    r.identity.entity_name = "Y"
    r.digital.press_coverage.append(S.Evidence(
        source="Reuters", source_tier="QUALITY_PRESS",
        url="https://reuters.com/x", snippet="revenue fell"))
    r.digital.meta.status = "ok"
    sv = S.structured_view(r.as_dict())
    dig = next((s for s in sv["sections"] if s["key"] == "digital"), None)
    assert dig is not None
    assert dig["evidence"] and dig["evidence"][0]["url"] == "https://reuters.com/x"
