"""R-F3015 — a LIVE Companies House hit must not be flagged "registry unavailable".

Live defect (Cohort dd_c1d3fdd5d380): reg 05684823, status active, incorp
2006-01-23, SEVEN named current officers from Companies House — an unambiguous live
registry verification — yet identity read "registry unavailable, NOT registry-verified"
(R-F1636) and scored NO. Root: the R-F1636 enrichment fires off the GB `reg_result`,
which is the GLEIF fallback (GB never hits CH via the adapter path, dd_orchestrator:3702)
and so looks "thin", ignoring the successful CH lookup at :3653. This is a false
NEGATIVE on a real identity — the mirror of a false clean.

Fix: `_ch_verified_live(profile, ch_unavail)` gates the enrichment — a genuine live CH
profile suppresses R-F1636. GLEIF/vault cannot populate a CH profile, so it stays
never-false-clean: a real CH failure still enriches-as-unavailable and scores NO.
"""
from pathlib import Path

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import _dd_decision_readiness

_SRC = (Path(__file__).resolve().parent.parent / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")


# ── the gate helper (unit) ─────────────────────────────────────────────────
def test_rf3015_ch_verified_live_helper():
    assert ddo._ch_verified_live({"company_number": "05684823"}, None) is True
    # unavailable (timeout / rate-limit / officers-truncated) is NOT a clean live hit
    assert ddo._ch_verified_live({"company_number": "05684823"}, "officers_truncated:600_of_800") is False
    assert ddo._ch_verified_live({}, None) is False
    assert ddo._ch_verified_live(None, None) is False
    assert ddo._ch_verified_live({"company_number": ""}, None) is False


# ── the never-false-clean contract the fix depends on ──────────────────────
def _identity(reg="05684823", status="active", gaps=None) -> dict:
    return {
        "identity": {
            "registration_number": reg,
            "registration_status": status,
            "incorporation_date": "2006-01-23",
            "directors": [{"name": "MCGRATH, Raquel"}],
            "data_gaps": gaps or [],
        }
    }


def test_rf3015_live_ch_identity_scores_answered():
    # a live CH profile with NO R-F1636 gap (what the fix produces) → identity ANSWERED
    dr = _dd_decision_readiness(_identity())
    assert dr["questions"]["identity"]["answered"] is True, \
        "a live-verified CH identity (7 officers, active) must score ANSWERED, not 'registry unavailable'"


def test_rf3015_registry_unavailable_gap_still_fails_identity():
    # never-false-clean: a genuine CH failure still enriches-as-unavailable and scores NO
    gap = ("R-F1636: registry unavailable — identity enriched from OSINT/vault, NOT "
           "registry-verified; manual registry check still required before transacting.")
    dr = _dd_decision_readiness(_identity(gaps=[gap]))
    assert dr["questions"]["identity"]["answered"] is False, \
        "when CH genuinely did not verify, R-F1636 must still fail the identity question"


# ── source-contract: the enrichment is actually gated on the live-CH flag ───
def test_rf3015_enrichment_gated_on_live_ch_flag():
    assert "_ch_registry_verified_live = _ch_verified_live(profile, _ch_unavail)" in _SRC, \
        "the live-CH flag must be set from the CH profile + unavailability signal"
    i = _SRC.index('_registry_result_is_thin(locals().get("reg_result"))')
    seg = _SRC[i:i + 120]
    assert 'not locals().get("_ch_registry_verified_live")' in seg, \
        "the R-F1636 enrichment must be gated on the live-CH flag (else the false negative persists)"
