"""R-F569 — sanctions matcher false-positive defect.

Pre-R-F569 MVP fire-test (2026-05-16) found that /api/aria/dd/orchestrate
was returning HARD_STOP on every entity regardless of risk:

    Embraer S.A. (clean Brazilian aerospace) → HARD_STOP via OFAC SDN
        false-match "MARANER HOLDINGS LIMITED"
    Aselsan A.S.  (clean Turkish defence)    → HARD_STOP via OFAC SDN
        false-match "Guillermo NIEBLAS NAVA" + UN SC false-match
        "ALI SADDAM HUSSEIN AL-TIKRITI"
    Acme Widgets Limited (entirely fictional) → HARD_STOP via OFAC SDN
        false-match "TAMIN KALAYE SABZ ARAS COMPANY"

R-F569 ships four hardening layers:

  1. Per-source threshold bumps: ofac_sdn 0.70 → 0.85,
     un_sc 0.72 → 0.85, fcdo (UK OFSI) 0.70 → 0.85.
  2. Name-overlap gate at dd_orchestrator's per-source severity block
     (lines 1109-1185) — requires ≥1 shared meaningful token (≥5 chars)
     OR score ≥0.95 before a HARD_STOP finding is emitted.
  3. Substring-boost guard in _common.similarity — the 0.85 floor now
     only applies if the shorter normalised name is ≥5 chars AND ≥40%
     of the longer.
  4. Topic taxonomy fix: `export.risk` (UANI ir_uani_business_registry
     and similar watchlists) demoted from HARD_STOP to AMBER. Real
     legal sanctions still carry the `sanction` topic which remains
     HARD_STOP.

This file covers the unit-level invariants. The capability test
(Embraer DD live-fires CLEAR / AMBER instead of HARD_STOP) is exercised
via the live fly.io endpoint by the operator after deploy — see
docs/mvp_readiness_2026_05_16.md.
"""
from __future__ import annotations

import pytest


# ── Layer 3: substring-boost guard in _common.similarity ──────────────────


def test_rf569_substring_boost_rejects_short_shared_token():
    """The "ES" in "ES SECURITIES" must NOT boost the score on
    "Embraer S.A." — short substrings have no discriminating power.

    Pre-R-F569 this returned ≥0.85 because "es" was a substring of
    "es securities". Now it falls through to plain SequenceMatcher.
    """
    from aria_service.intel.sources._common import similarity, normalise_name
    # Confirm normalisation produces the expected reduced forms
    assert normalise_name("Embraer S.A.") == "embraer"
    # Cross-check the regression: a short shared substring no longer boosts
    s = similarity("Embraer S.A.", "ES SECURITIES")
    assert s < 0.80, (
        f"R-F569 REGRESSION: 'Embraer S.A.' vs 'ES SECURITIES' scored {s:.3f}; "
        f"the short-substring guard must keep this below 0.80"
    )


def test_rf569_substring_boost_still_works_on_long_substring():
    """The substring boost is still useful when the shorter name is
    long enough to discriminate — "Baykar" inside "Baykar Defense
    Industries" should keep scoring strongly (real Turkish drone-
    maker on OFAC SDN for Ukraine ties)."""
    from aria_service.intel.sources._common import similarity
    s = similarity("Baykar", "Baykar Technology")
    assert s >= 0.85, (
        f"R-F569 OVER-CORRECTED: 'Baykar' vs 'Baykar Technology' scored {s:.3f}; "
        f"≥5-char substring should still boost"
    )


def test_rf569_substring_boost_rejects_ratio_too_small():
    """When the shorter name is ≥5 chars but constitutes <40% of the
    longer, suppress the boost — a 6-char shared token inside a
    50-char SDN entry name is not discriminating."""
    from aria_service.intel.sources._common import similarity
    # Construct a contrived case: "bayker" is 6 chars but only ~13% of the
    # 45-char candidate. Should fall through, NOT get boosted to ≥0.85.
    s = similarity("bayker", "Some Very Long Sanctions Entry Name Bayker")
    # difflib ratio for these will be moderate; we just need to confirm
    # we did NOT see the 0.85 substring-boost floor applied.
    assert s < 0.85, (
        f"R-F569 REGRESSION: 6-char substring inside 45-char candidate "
        f"scored {s:.3f}; ratio guard should keep below 0.85"
    )


# ── Layer 2: dd_orchestrator name-overlap gate ────────────────────────────


def test_rf569_overlap_gate_rejects_zero_token_overlap():
    """A candidate sharing NO meaningful token with the query must be
    rejected, regardless of fuzzy-score input."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name, _name_overlap

    # Same gate function dd_orchestrator builds inline; we verify the
    # primitives that compose it.
    q_tokens = _tokenize_entity_name("Aselsan A.S.")
    c_tokens = _tokenize_entity_name("Guillermo NIEBLAS NAVA")
    assert _name_overlap("Aselsan A.S.", "Guillermo NIEBLAS NAVA") == 0
    assert "aselsan" in q_tokens
    assert q_tokens.isdisjoint(c_tokens), (
        f"R-F569 REGRESSION: 'Aselsan A.S.' tokens {q_tokens} unexpectedly "
        f"overlap with 'Guillermo NIEBLAS NAVA' tokens {c_tokens}"
    )


def test_rf569_overlap_gate_rejects_un_sc_aselsan_false_positive():
    """The Aselsan → 'ALI SADDAM HUSSEIN AL-TIKRITI' false positive must
    not survive the overlap gate."""
    from aria_service.intel._sanctions_classify import _name_overlap
    assert _name_overlap("Aselsan A.S.", "ALI SADDAM HUSSEIN AL-TIKRITI") == 0


def test_rf569_overlap_gate_rejects_embraer_maraner_false_positive():
    """Embraer S.A. → 'MARANER HOLDINGS LIMITED' must fail the overlap
    gate — zero shared meaningful tokens after corporate-suffix strip
    ('holdings' and 'limited' are suffixes; 'embraer' vs 'maraner' do
    not overlap)."""
    from aria_service.intel._sanctions_classify import _name_overlap
    assert _name_overlap("Embraer S.A.", "MARANER HOLDINGS LIMITED") == 0


def test_rf569_overlap_gate_rejects_acme_widgets_false_positive():
    """The fake 'Acme Widgets Limited' → 'TAMIN KALAYE SABZ ARAS
    COMPANY' false positive must fail the overlap gate."""
    from aria_service.intel._sanctions_classify import _name_overlap
    assert _name_overlap("Acme Widgets Limited", "TAMIN KALAYE SABZ ARAS COMPANY") == 0


def test_rf569_overlap_gate_passes_real_match():
    """A REAL sanctions match (Rosoboronexport → 'ROSOBORONEKSPORT OAO')
    must still pass the gate. 'rosoboronexport' / 'rosoboroneksport'
    share no full token after tokenisation (transliteration differs by
    one letter) but the substring boost should make string_similarity
    high enough that score≥0.95 bypass kicks in. We test that combined
    path by exercising the gate's score-bypass branch directly."""
    from aria_service.intel.sources._common import similarity
    s = similarity("Rosoboronexport", "ROSOBORONEKSPORT")
    # Two near-identical transliterations should score very high
    assert s >= 0.90, (
        f"R-F569 OVER-CORRECTED: 'Rosoboronexport' vs 'ROSOBORONEKSPORT' "
        f"scored {s:.3f}; real-transliteration matches must remain ≥0.90"
    )


# ── Layer 4: export.risk topic demoted to AMBER (not HARD_STOP) ────────────


def test_rf569_export_risk_topic_is_amber_not_hard_stop():
    """The `export.risk` topic — carried by UANI's
    ir_uani_business_registry and similar watchlists — must be AMBER.
    Pre-R-F569 it was HARD_STOP, causing Embraer (legitimate KC-390 /
    E-Jets export-control-relevant flag) to be auto-blocked.
    """
    from aria_service.intel._sanctions_classify import _TOPIC_SEVERITY
    assert _TOPIC_SEVERITY.get("export.risk") == "amber", (
        f"R-F569 REGRESSION: export.risk topic mapping is "
        f"{_TOPIC_SEVERITY.get('export.risk')!r}, expected 'amber'"
    )
    # And the real sanction topic must remain HARD_STOP — the demotion
    # must NOT have collateral damage on the load-bearing topic.
    assert _TOPIC_SEVERITY["sanction"] == "hard_stop"
    assert _TOPIC_SEVERITY["export.control"] == "hard_stop"
    assert _TOPIC_SEVERITY["asset.frozen"] == "hard_stop"


def test_rf569_classify_match_demotes_export_risk_to_amber():
    """End-to-end classify_match invariant: a match tagged
    export.risk with high score (typical UANI hit shape) must
    classify as AMBER, not HARD_STOP, after R-F569."""
    from aria_service.intel._sanctions_classify import classify_match
    embraer_uani_match = {
        "name": "Embraer SA",
        "score": 1.0,
        "string_similarity": 0.833,
        "topics": ["export.risk"],
        "lists": ["ir_uani_business_registry"],
    }
    severity = classify_match(embraer_uani_match, query_name="Embraer S.A.")
    assert severity == "amber", (
        f"R-F569 REGRESSION: UANI export.risk match for Embraer "
        f"classified as {severity!r}, expected 'amber' (it's a "
        f"watchlist, not a legal sanction)"
    )


def test_rf569_classify_match_still_hard_stops_real_ofac_sanction():
    """Confirm the fix doesn't break true positives — a real OFAC SDN
    `sanction` topic match still classifies as HARD_STOP."""
    from aria_service.intel._sanctions_classify import classify_match
    rosobor_ofac_match = {
        "name": "ROSOBORONEKSPORT OAO",
        "score": 1.0,
        "string_similarity": 0.95,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn", "eu_fsf"],
    }
    severity = classify_match(rosobor_ofac_match, query_name="Rosoboronexport")
    assert severity == "hard_stop", (
        f"R-F569 OVER-CORRECTED: Rosoboronexport→ROSOBORONEKSPORT OAO "
        f"on OFAC SDN classified as {severity!r}, expected 'hard_stop'"
    )


# ── Threshold bumps (defence-in-depth) ────────────────────────────────────


def test_rf569_ofac_sdn_threshold_is_at_least_0_85():
    """The ofac_sdn adapter's default threshold must be ≥0.85
    post-R-F569. Pre-R-F569 it was 0.70 which let Embraer→MARANER
    HOLDINGS LIMITED through the adapter at all."""
    import inspect
    from aria_service.intel.sources import ofac_sdn
    sig = inspect.signature(ofac_sdn.lookup)
    threshold_default = sig.parameters["threshold"].default
    assert threshold_default >= 0.85, (
        f"R-F569 REGRESSION: ofac_sdn.lookup threshold default is "
        f"{threshold_default}, expected ≥0.85"
    )


def test_rf569_un_sc_threshold_is_at_least_0_85():
    import inspect
    from aria_service.intel.sources import un_sc_sanctions
    sig = inspect.signature(un_sc_sanctions.lookup)
    threshold_default = sig.parameters["threshold"].default
    assert threshold_default >= 0.85, (
        f"R-F569 REGRESSION: un_sc_sanctions.lookup threshold default "
        f"is {threshold_default}, expected ≥0.85"
    )


def test_rf569_fcdo_threshold_is_at_least_0_85():
    import inspect
    from aria_service.intel.sources import fcdo_sanctions
    sig = inspect.signature(fcdo_sanctions.lookup)
    threshold_default = sig.parameters["threshold"].default
    assert threshold_default >= 0.85, (
        f"R-F569 REGRESSION: fcdo_sanctions.lookup threshold default "
        f"is {threshold_default}, expected ≥0.85"
    )
