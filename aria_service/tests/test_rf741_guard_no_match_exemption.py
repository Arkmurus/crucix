"""R-F741 (2026-05-20) — tighten rf604 to exempt 'no X match/hit/result'.

LIVE BUG captured in operator dd_orchestrate output 2026-05-20:

  [R-F401 SELF-CLAIM GUARD — possible hallucination detected]
    BLOCK (2):
      · rf604_capability_denial: "no OFAC SDN"
      · rf604_capability_denial: "no OFSI adapter"

The "no OFSI adapter" hit is correct (the LLM was claiming a tool
doesn't exist when it does). But "no OFAC SDN" is a FALSE POSITIVE
— the LLM's full phrase was "no OFAC SDN, EU consolidated, or
OpenSanctions match" — i.e., NO MATCH FOUND, a negative finding,
not a capability denial.

R-F741 adds a negative lookahead to the bare-tool branch of
`_SELF_DENIAL_RE`: bare "no <tool>" without an explicit capability
modifier (access/capability/tooling/integration/adapter/tool) must
NOT be followed by a negative-result word (match/hit/result/record/
entry/finding/listing/return).

These tests:
  1. Confirm the live false-positive "no OFAC SDN match" no longer
     fires (R-F741 fix)
  2. Confirm the legitimate "no OFSI adapter" / "no OFSI access" /
     bare "no OFSI" denials STILL fire (no regression)
  3. Cover the result-word variants (matches, hits, results, etc.)
"""
from __future__ import annotations


def _hits(text):
    from aria_service.intel.self_claim_guard import scan_response
    return [v.pattern_id for v in scan_response(text, self_introspect_ran=False)]


# ── R-F741 false-positive exemptions (must NOT fire) ────────────────


def test_rf741_no_ofac_sdn_match_does_not_fire():
    """LIVE BUG: 'no OFAC SDN, ..., or OpenSanctions match' was flagged."""
    txt = "Sanctions CLEAN — no OFAC SDN, EU consolidated, or OpenSanctions match found."
    assert "rf604_capability_denial" not in _hits(txt), (
        "R-F741: 'no OFAC SDN ... match' is a negative finding, not "
        "a capability denial. Guard must not flag it."
    )


def test_rf741_no_ofsi_hit_does_not_fire():
    txt = "Cross-check: no OFSI hit on the canonical name."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf741_no_ofac_result_does_not_fire():
    txt = "Subject screened — no OFAC result on the SDN list."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf741_no_ofsi_record_does_not_fire():
    txt = "Database query returned no OFSI record for the entity."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf741_no_companies_house_finding_does_not_fire():
    txt = "Registry search complete — no Companies House finding for the trading name."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf741_no_ofac_matches_plural_does_not_fire():
    """Plural result-word variants (matches/hits/results)."""
    txt = "OFAC scan returned no OFAC SDN matches."
    assert "rf604_capability_denial" not in _hits(txt)


# ── R-F741 must preserve real capability denials (must STILL fire) ──


def test_rf741_no_ofsi_adapter_still_fires():
    """LIVE BUG case (correctly-flagged half) — must still fire."""
    txt = "UK list not queried — tool has no OFSI adapter."
    assert "rf604_capability_denial" in _hits(txt), (
        "R-F741 regression: 'no OFSI adapter' is a real capability "
        "denial (the adapter EXISTS at fcdo_sanctions.py) and must "
        "continue to fire."
    )


def test_rf741_no_ofsi_capability_still_fires():
    txt = "no OFSI capability in this build"
    assert "rf604_capability_denial" in _hits(txt)


def test_rf741_no_ofsi_access_still_fires():
    txt = "we have no OFSI access at the moment"
    assert "rf604_capability_denial" in _hits(txt)


def test_rf741_bare_no_ofsi_still_fires():
    """Bare 'no OFSI' without ANY modifier and without a result-word
    after MUST still fire (denial pattern)."""
    txt = "Compliance posture: no OFSI on this case."
    assert "rf604_capability_denial" in _hits(txt)


def test_rf741_i_cannot_query_ofsi_still_fires():
    """The other denial pattern ('I cannot ...') is unaffected by R-F741."""
    txt = "I cannot query OFSI directly from this turn."
    assert "rf604_capability_denial" in _hits(txt)


def test_rf741_companies_house_unavailable_still_fires():
    """'X is unavailable' pattern unaffected by R-F741."""
    txt = "Companies House is not available right now."
    assert "rf604_capability_denial" in _hits(txt)


# ── Mixed case: live BUG output must produce ONE hit, not two ──


def test_rf741_live_bug_output_yields_only_real_denial():
    """The actual live dd_orchestrate output had BOTH a false-positive
    ('no OFAC SDN') and a real denial ('no OFSI adapter'). After
    R-F741 only the latter should fire."""
    txt = (
        "Sanctions: CLEAN — no OFAC SDN, EU consolidated, or "
        "OpenSanctions match. UK OFSI list not queried — tool "
        "has no OFSI adapter."
    )
    hits = _hits(txt)
    # Exactly one rf604_capability_denial occurrence (the adapter one)
    n = hits.count("rf604_capability_denial")
    assert n == 1, (
        f"R-F741: expected exactly 1 rf604 denial hit (the 'no OFSI "
        f"adapter' one); got {n}. All hits: {hits}"
    )
