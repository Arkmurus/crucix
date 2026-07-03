"""R-F2361 + R-F2362 — DD honesty on data-starved entities + sanctions acronym-noise.

Source: a live DD on Brazilian company "Modirum Gespi" (3 Jul 2026).
  - R-F2362: "Modirum Gespi" generated the 2-letter acronym alias "MG", which
    fuzzy-matched unrelated sanctions names (MG Corp / MG Global / mg-tr.com /
    MGフロート) and rendered them as real "transparency/state-ownership matches".
    The token-overlap noise gate only ran on amber+ severity, so these INFO-tier
    zero-overlap hits skipped it. Fix: don't emit <=3-char acronyms, and flag
    ANY zero meaningful-token-overlap match as noise regardless of tier.
  - R-F2361: the entity was AMBER-LIGHT purely via Brazil's country risk, with
    no directors / no registry substance, yet the BLUF said "can proceed with
    enhanced due diligence" — the honest reframing only fired when
    confidence_gate_triggered, which is set only on the GREEN gate. Fix: reframe
    data-starved AMBER-LIGHT runs too.

Both tests drive the REAL broken paths (classify_matches / _generate_variants /
_assemble_bluf) and assert the user-visible outcome, per CLAUDE.md §3c.
"""
from __future__ import annotations

import pytest

from aria_service.intel._sanctions_classify import classify_matches
from aria_service.intel.sanctions import _generate_variants
from aria_service.intel.dd_schema import (
    ARKDDReport, Evidence, LayerStatus, RiskClassification,
)
from aria_service.intel.dd_orchestrator import _assemble_bluf

_AMBER = RiskClassification.AMBER_LIGHT.value

# The five acronym-collision hits from the live Modirum Gespi report.
_MG_NOISE = [
    {"name": "MG Corp.",     "score": 0.91, "topics": [],            "datasets": ["kp_rusi_reports"]},
    {"name": "mg-tr.com",    "score": 0.83, "topics": ["crime.fin"], "datasets": ["fr_illegal_financial_services"]},
    {"name": "MG Global Co", "score": 0.80, "topics": [],            "datasets": ["gem_energy_ownership"]},
]


# ─────────────────────────── R-F2362 sanctions noise ───────────────────────────

def test_rf2362_two_char_acronym_alias_not_generated():
    """The 2-letter initialism 'MG' must NOT be an alias (no discriminating power)."""
    variants = _generate_variants("Modirum Gespi")
    assert "MG" not in variants, f"2-char acronym still generated: {variants}"
    # a >=3-char acronym is still meaningful and kept
    assert "MGH" in _generate_variants("Modirum Gespi Holdings")


def test_rf2362_acronym_collision_noise_is_suppressed():
    """All MG* zero-overlap hits are flagged noise; summary doesn't list them."""
    out = classify_matches(_MG_NOISE, query_name="Modirum Gespi")
    assert all(m["noise_filtered"] for m in out["per_match"]), (
        f"acronym-collision hits not flagged as noise: {out['per_match']}"
    )
    assert out["noise_filtered"] == 3
    # the summary must NOT present the noise as real matches
    assert "MG Corp" not in out["summary"], f"noise leaked into summary: {out['summary']}"
    assert ("noise" in out["summary"].lower() or "no real hit" in out["summary"].lower()), (
        f"summary should state the hits were filtered as noise: {out['summary']}"
    )


def test_rf2362_real_name_overlap_match_survives_and_escalates():
    """never-false-clean: a candidate SHARING a meaningful token with the entity
    is NOT filtered and still escalates on a sanction topic."""
    real = [{"name": "Modirum Defence Ltd", "score": 0.88, "topics": ["sanction"],
             "datasets": ["ofac_sdn"]}]
    out = classify_matches(real, query_name="Modirum Gespi")
    assert not out["per_match"][0]["noise_filtered"], "real token-overlap match wrongly filtered"
    assert out["worst_severity"] == "hard_stop", (
        f"a real sanction hit must escalate (never-false-clean): {out['worst_severity']}"
    )


def test_rf2362_near_exact_transliteration_bypass_preserved():
    """R-F569 bypass: a near-exact match (score>=0.95 & sim>=0.50) survives even
    with zero token overlap — real transliterations must not be filtered."""
    m = [{"name": "ROSOBORONEKSPORT", "score": 0.97, "string_similarity": 0.90,
          "topics": ["sanction"], "datasets": ["ofac_sdn"]}]
    out = classify_matches(m, query_name="Rosoboronexport")
    assert not out["per_match"][0]["noise_filtered"], "near-exact transliteration wrongly filtered"


# ─────────────────────────── R-F2361 DD honest headline ─────────────────────────

def _report(entity="subject", press=True):
    r = ARKDDReport(target={"name": entity, "type": "company"})
    r.identity.entity_name = entity  # name 'subject' skips the R-F398 fallback web search
    if press:
        r.digital.meta.status = LayerStatus.OK.value if hasattr(LayerStatus, "OK") else "complete"
        r.digital.press_coverage = [
            Evidence(source="Defence Industry Europe", source_tier="T2", url="https://di.eu/x"),
            Evidence(source="Modirum", source_tier="T2", url="https://modirum.com/y"),
        ]
    return r


@pytest.mark.asyncio
async def test_rf2361_data_starved_amber_reframed_not_can_proceed():
    """AMBER-LIGHT via an UNRELATED reason (country risk), no registry substance,
    NOT confidence_gate_triggered — must be reframed, never 'can proceed'."""
    r = _report()
    r.risk_classification = _AMBER
    r.confidence_gate_triggered = False  # the exact gap: country-risk AMBER, gate never ran
    # identity has no directors / registration_status / incorporation_date
    await _assemble_bluf(r)
    bl = r.bottom_line.lower()
    assert "can proceed with enhanced" not in bl, (
        f"over-reassured a data-starved DD: {r.bottom_line[:200]}"
    )
    assert "LIMITED REGISTRY DATA" in r.bottom_line, (
        f"OSINT present but not reframed as a briefing: {r.bottom_line[:200]}"
    )
    assert "registry" in bl  # still honest about the gap


@pytest.mark.asyncio
async def test_rf2361_amber_with_registry_substance_still_can_proceed():
    """Regression: an AMBER-LIGHT entity WITH substance (directors) was genuinely
    investigated → keep the 'can proceed with enhanced DD' headline."""
    r = _report()
    r.risk_classification = _AMBER
    r.confidence_gate_triggered = False
    r.identity.directors = ["Jane Doe"]  # substance present → not data-starved
    await _assemble_bluf(r)
    assert "can proceed with enhanced" in r.bottom_line.lower(), (
        f"wrongly reframed a substantive AMBER DD: {r.bottom_line[:200]}"
    )
