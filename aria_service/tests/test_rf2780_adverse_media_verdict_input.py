"""R-F2780 — adverse media is a first-class VERDICT input.

Before R-F2780 the deep adverse-media search merged its findings into
report.adverse_media but was explicitly forbidden from touching the verdict, AND
it only fired on already-AMBER/RED/low-coverage runs — so a GREEN entity's
corruption history could never change its GREEN "proceed" verdict.

R-F2780 lets CREDIBLE (tier-1a/1b), subject-named adverse findings ESCALATE the
stored verdict (GREEN/AMBER-LIGHT -> AMBER-LIGHT). It ONLY escalates — never
downgrades — because a false AMBER (flag for human review) is a safe error while
a false clean is the never-false-clean breach this exists to prevent.

These tests drive the REAL pure helper _apply_adverse_media_to_verdict on stored-
report dicts (no I/O), covering: escalate on credible, do-not-escalate on weak,
do-not-escalate on empty, and never-downgrade on an already-worse verdict.
"""
from __future__ import annotations


def _green_body():
    return {
        "risk_classification": "GREEN",
        "bottom_line": "🟢 GREEN — Acme Defence Ltd passes baseline due diligence.",
        "identity": {"entity_name": "Acme Defence Ltd"},
        "synthesis": {"risk_classification": "GREEN", "key_findings": []},
    }


def _am(findings):
    return {"ok": True, "findings": findings, "findings_count": len(findings)}


def _f(tier, url=None, title=None):
    """A finding at the given credibility tier. Production carries the web_search
    INT scale (1=official … 5=general); we pass ints to mirror reality.

    R-F3022 — these fixtures used ONE shared URL and the placeholder title "Adverse
    story", which the materiality gate now (correctly) rejects: one URL is one item
    however many templates surfaced it, and a title carrying no adverse content is
    not adverse media. That leniency is exactly what let 37 non-adverse hits — 14
    registry pages, 14 of ARIA's own memory records and 9 academic papers — escalate
    a live verdict to AMBER-LIGHT. The fixtures now carry DISTINCT URLs and real
    adverse wording, so these tests still assert what they were written to assert."""
    n = f"{tier}"
    return {"credibility_tier": tier,
            "title": title or f"Regulator fines Acme Ltd over bribery findings ({n})",
            "source_url": url or f"https://www.ft.com/content/adverse-{n}",
            "snippet": "enforcement action and penalty reported"}


def test_rf2780_escalates_green_on_official_source():
    """A single OFFICIAL-tier (tier 1: regulator/court/gov) subject-named adverse
    finding must raise GREEN -> AMBER-LIGHT."""
    from aria_service.intel import dd_orchestrator as dd

    body = _green_body()
    res = dd._apply_adverse_media_to_verdict(body, _am([_f(1)]))
    assert res["escalated"] is True
    assert body["risk_classification"] == "AMBER-LIGHT"
    assert body["synthesis"]["risk_classification"] == "AMBER-LIGHT"
    assert body.get("adverse_media_escalated") is True
    assert "AMBER-LIGHT" in body["bottom_line"] and "NOT a clearance" in body["bottom_line"]
    assert any("Adverse-media escalation" in f.get("title", "")
               for f in body["synthesis"]["key_findings"])


def test_rf2780_escalates_on_two_credible_sources():
    """Two credible findings (institution tier 2 + quality-press tier 4) clear the
    default credible threshold (2) even with no official-tier source."""
    from aria_service.intel import dd_orchestrator as dd

    body = _green_body()
    res = dd._apply_adverse_media_to_verdict(body, _am([_f(2), _f(4)]))
    assert res["escalated"] is True and body["risk_classification"] == "AMBER-LIGHT"


def test_rf2780_handles_legacy_string_tiers_defensively():
    """A tier-convention drift (legacy 'tier_1a' string) must still be recognised
    as official — never silently un-material (which would reintroduce a false
    clean)."""
    from aria_service.intel import dd_orchestrator as dd

    # R-F3276 — the title must NAME THE SUBJECT. This fixture said only
    # "gov sanction notice", and it passed until R-F3089 added the materiality/
    # attribution gate — the fix for the Mitie "green report, adverse comments"
    # class, where the adverse filter passed on domain match alone. The gate now
    # (correctly) answers `no-material-adverse` for a finding that never names the
    # entity, so this test was asserting behaviour that was deliberately removed.
    #
    # The LEGACY-TIER property it exists to prove is intact and still proven here:
    # _adverse_finding_tier({"credibility_tier": "tier_1a"}) == 1, and with the
    # subject named the same legacy string escalates GREEN -> AMBER-LIGHT.
    # Loosening the attribution gate to make this green would reintroduce exactly
    # the false-positive class R-F3089 fixed.
    body = _green_body()
    _subject = (body.get("identity") or {}).get("entity_name") or "Acme Defence Ltd"
    res = dd._apply_adverse_media_to_verdict(body, _am([{"credibility_tier": "tier_1a",
                                                         "title": f"{_subject} sanction notice"}]))
    assert res["escalated"] is True and body["risk_classification"] == "AMBER-LIGHT"
    # and the legacy string really was read as official-tier, not defaulted to 5
    assert dd._adverse_finding_tier({"credibility_tier": "tier_1a"}) == 1


def test_rf2780_does_not_escalate_on_single_midtier_or_weak_sources():
    """One credible non-official source (below threshold) or only weak sources
    (industry tier 3 / general tier 5) must NOT flip the verdict — the guard
    against noisy false positives."""
    from aria_service.intel import dd_orchestrator as dd

    body = _green_body()
    res = dd._apply_adverse_media_to_verdict(body, _am([_f(2)]))  # single institution
    assert res["escalated"] is False and body["risk_classification"] == "GREEN"

    body2 = _green_body()
    res2 = dd._apply_adverse_media_to_verdict(body2, _am([_f(3), _f(5), _f(3)]))  # weak only
    assert res2["escalated"] is False and body2["risk_classification"] == "GREEN"


def test_rf2780_no_escalation_on_empty_or_failed_search():
    from aria_service.intel import dd_orchestrator as dd

    body = _green_body()
    assert dd._apply_adverse_media_to_verdict(body, _am([]))["escalated"] is False
    assert body["risk_classification"] == "GREEN"

    body2 = _green_body()
    assert dd._apply_adverse_media_to_verdict(body2, {"ok": False, "error": "x"})["escalated"] is False
    assert body2["risk_classification"] == "GREEN"


def test_rf2780_never_downgrades_an_already_worse_verdict():
    """Credible adverse findings on an already-RED report must NOT lower it to
    AMBER-LIGHT — escalate-only."""
    from aria_service.intel import dd_orchestrator as dd

    body = _green_body()
    body["risk_classification"] = "RED"
    body["synthesis"]["risk_classification"] = "RED"
    res = dd._apply_adverse_media_to_verdict(body, _am([_f(1), _f(1)]))
    assert res["escalated"] is False
    assert body["risk_classification"] == "RED", "must never downgrade RED -> AMBER"
    # but it still records the corroborating adverse finding
    assert any("Adverse-media" in f.get("title", "") for f in body["synthesis"]["key_findings"])
