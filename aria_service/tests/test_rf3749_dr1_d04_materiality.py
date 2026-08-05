"""R-F3749 — DR-1 **D-04 adjudicated**: the materiality filter and the FRC class.

D-04 was UNADJUDICATED: "Materiality filter (the FRC class)", P1, suspected
`dd_disciplines.py` / `dd_orchestrator.py`. Fifth DR-1 entry adjudicated from this
repo (after D-02, D-03, D-05, D-06).

WHAT "THE FRC CLASS" MEANS, and it decides the whole entry: the Financial
Reporting Council is a REGULATOR. A regulator/court/government finding is tier 1
("official") in `_adverse_finding_tier` (`dd_orchestrator.py:12842`). So the FRC
class is the case where a SINGLE regulatory finding must be material on its own —
because demanding corroboration for a regulator is how a real enforcement action
gets filtered out as noise.

THE ADJUDICATION: satisfied. `_adverse_media_materiality` (`:13585`) sets

    material = (len(official) >= 1) or (len(credible) >= _min_credible)

so one official-tier finding is material with no second source, while weak tiers
(3 industry, 5 general) cannot move a verdict alone — the guard against
single-source false positives. `dd_disciplines.py` only *instructs* materiality
assessment (prompt text at `:293`, schema at `:312`); the enforcement is here.

Two earlier fixes already hardened the surrounding behaviour, and these tests pin
both because either regressing would recreate a false clean:

  R-F3022 — a non-material result must NOT go silent. Many name matches with no
            adverse content is a DIFFERENT and more useful statement than
            silence, and silence lets a later reader assume the search never ran.
            Every exclusion is counted and returned, never silently dropped.
  R-F3084 — raw diagnostics stay separate from the filtered review set. The PDF
            once saw 26 RAW hits and called them 26 subject-named items that
            "survived filtering".

Run: python -m pytest aria_service/tests/test_rf3749_dr1_d04_materiality.py -v
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as dd


SUBJECT = "TestCo Ltd"


def _f(url: str, tier: int, text: str = "fined for misconduct and sanctioned") -> dict:
    """A deep-search adverse finding.

    Two premises this fixture got WRONG on its first run, both worth recording
    because the next fixture will hit them:

      * `credibility_tier` is the INT web_search scale (1=official … 5=general),
        per _adverse_finding_tier:12845.
      * the SUBJECT NAME must appear in the finding's TEXT. A `subject_named`
        flag is not consulted — `_adverse_names_subject` tokenises the actual
        content (:13630-13633), so a finding that never names the subject is
        dropped as `subject_unnamed_dropped`. That is correct behaviour: an
        article that does not name the entity is not evidence about it.
    """
    body = f"{SUBJECT} {text}"
    return {"url": url, "title": f"Regulatory action — {SUBJECT}",
            "snippet": body, "summary": body, "credibility_tier": tier}


def test_a_single_regulator_finding_is_material_on_its_own():
    """THE FRC CLASS: one regulator action needs no corroboration."""
    mat = dd._adverse_media_materiality(
        {"ok": True, "findings": [_f("https://frc.org.uk/enforcement/case-1", 1)]},
        SUBJECT)
    assert mat["official"] == 1, f"tier-1 not counted as official: {mat}"
    assert mat["material"] is True, (
        "a single FRC/regulator-tier finding was NOT material. Demanding a second "
        "source for a REGULATOR is how a real enforcement action gets filtered "
        "out as noise — the D-04 false clean."
    )


def test_one_weak_source_alone_is_not_material():
    """Negative control: the filter must still suppress single-source noise.

    Without this, a filter that called EVERYTHING material would satisfy the
    test above while destroying the verdict's meaning.
    """
    mat = dd._adverse_media_materiality(
        {"ok": True, "findings": [_f("https://blog.example.com/rumour", 5)]}, SUBJECT)
    assert mat["official"] == 0
    assert mat["material"] is False, (
        f"a single general-tier item moved materiality: {mat}. Tiers 3 and 5 are "
        f"weak by design — that is the single-source false-positive guard."
    )


def test_raw_count_stays_separate_from_the_reviewed_set():
    """R-F3084 — the PDF once reported RAW hits as items that survived filtering."""
    findings = [_f("https://blog.example.com/a", 5),
                _f("https://blog.example.com/b", 5),
                _f("https://frc.org.uk/case", 1)]
    mat = dd._adverse_media_materiality({"ok": True, "findings": findings}, SUBJECT)
    assert mat["raw_count"] == 3, f"raw_count must report the sweep size: {mat}"
    assert mat["credible_count"] < mat["raw_count"], (
        f"credible_count ({mat['credible_count']}) is not distinguishable from "
        f"raw_count ({mat['raw_count']}) — conflating them is exactly how 26 raw "
        f"hits were once described as having survived filtering"
    )


def test_a_duplicate_url_counts_once():
    """R-F3022 (a) — the same URL from several query templates is ONE item."""
    dup = "https://frc.org.uk/enforcement/case-1"
    mat = dd._adverse_media_materiality(
        {"ok": True, "findings": [_f(dup, 1), _f(dup, 1), _f(dup, 1)]}, SUBJECT)
    assert mat["official"] == 1, (
        f"the same URL counted {mat['official']} times — duplicate query hits "
        f"would inflate an official count and manufacture materiality"
    )


def test_a_non_material_sweep_does_not_go_silent():
    """R-F3022 — 'matched names, no adverse content' must be SAID, not omitted."""
    body: dict = {"risk_classification": "GREEN"}
    am = {"ok": True, "findings": [_f("https://blog.example.com/rumour", 5)]}
    res = dd._apply_adverse_media_to_verdict(body, am)
    assert res.get("escalated") is False
    assert res.get("reason") == "no-material-adverse"
    assert res.get("raw_count", 0) >= 1, (
        "the raw sweep size was not reported, so a reader cannot tell "
        "'searched and found nothing adverse' from 'never searched'"
    )


def test_a_material_finding_escalates_green():
    body: dict = {"risk_classification": "GREEN"}
    am = {"ok": True, "findings": [_f("https://frc.org.uk/enforcement/case-1", 1)]}
    res = dd._apply_adverse_media_to_verdict(body, am)
    assert res.get("escalated") is True, f"material adverse did not escalate: {res}"
    assert body["risk_classification"] == "AMBER-LIGHT"


def test_a_material_finding_never_downgrades_a_worse_verdict():
    """Adverse media may RAISE a verdict; it must never soften one."""
    body: dict = {"risk_classification": "RED"}
    am = {"ok": True, "findings": [_f("https://frc.org.uk/enforcement/case-1", 1)]}
    dd._apply_adverse_media_to_verdict(body, am)
    assert body["risk_classification"] == "RED", (
        "adverse media downgraded a RED verdict to AMBER-LIGHT — a finding must "
        "never soften a worse conclusion reached elsewhere"
    )


def test_tier_convention_drift_cannot_silently_demote_a_regulator():
    """_adverse_finding_tier:12849 defends the legacy STRING scale for this reason.

    If the deep search ever emits 'tier_1a' again instead of 1, a regulator must
    still read as official — otherwise a convention change silently produces a
    false clean.
    """
    f = _f("https://frc.org.uk/enforcement/case-1", 1)
    f["credibility_tier"] = "tier_1a"
    assert dd._adverse_finding_tier(f) == 1
    mat = dd._adverse_media_materiality({"ok": True, "findings": [f]}, SUBJECT)
    assert mat["material"] is True, (
        f"a regulator finding on the legacy string tier scale was not material: {mat}"
    )
