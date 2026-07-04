"""R-F2396 — honest NATIVE-grounding credit + validate-and-repair.

Step-0 (2026-07-03) proved DeepSeek grounds well but cites by SOURCE NAME +
[CONFIRMED] tags, not the foreign `[from snippet #N]` token — and refuses
tag/format instructions in the user-message slot (aria_engine.py:627). R-F2396:
  - moves the grounding contract to the trusted tool-block position;
  - scores grounding from the honesty judge's REAL support verdict
    (native_grounding_from_judgment), NOT token presence or lexical overlap;
  - keeps R-F2391's anti-inflation guard: a confidence tag on an UNSUPPORTED or
    fabricated claim scores 0.0.

These are the integrity-proof capability tests the design requires. They drive
native_grounding_from_judgment() + verify_response(..., support_judgment=…) —
the real scoring path — with judge verdicts standing in for the LLM support
check (which runs live).
"""
from __future__ import annotations

from aria_service.intel import source_verifier as sv


def _judgment(claims, supported, status="ok"):
    return {"status": status, "claims": list(claims), "supported_count": supported,
            "verdicts": [], "honesty_score": (supported / len(claims)) if claims else None}


# ── native_grounding_from_judgment ──────────────────────────────────────────

def test_all_claims_supported_is_grounded():
    j = _judgment(["OFAC lists X", "EU lists X"], supported=2)
    v = sv.native_grounding_from_judgment(j)
    assert v["verdict"] == "grounded"
    assert v["grounded_rate"] == 1.0
    assert v["source"] == "native_confidence_support"


def test_no_claims_supported_is_ungrounded_zero():
    """Anti-inflation: confidence tags whose claims the judge could NOT back → 0.0."""
    j = _judgment(["fabricated claim A", "fabricated claim B"], supported=0)
    v = sv.native_grounding_from_judgment(j)
    assert v["grounded_rate"] == 0.0
    assert v["verdict"] == "ungrounded"


def test_partial_support_is_partial():
    j = _judgment(["real claim", "unsupported claim"], supported=1)
    v = sv.native_grounding_from_judgment(j)
    assert v["grounded_rate"] == 0.5
    assert v["verdict"] == "partial"


def test_no_source_status_returns_none():
    """[CONFIRMED] claims with NO source content (judge status 'no_source') are
    NOT credited as grounded here — the deterministic verdict stands."""
    assert sv.native_grounding_from_judgment(
        {"status": "no_source", "claims": ["x"], "supported_count": 0}) is None


def test_no_claims_and_missing_judgment_return_none():
    assert sv.native_grounding_from_judgment({"status": "ok", "claims": [], "supported_count": 0}) is None
    assert sv.native_grounding_from_judgment({"status": "no_claims", "claims": []}) is None
    assert sv.native_grounding_from_judgment(None) is None


def test_supported_count_cannot_exceed_total():
    """A malformed judgment can't inflate above 1.0."""
    j = _judgment(["only one claim"], supported=5)
    v = sv.native_grounding_from_judgment(j)
    assert v["grounded_rate"] == 1.0


# ── verify_response integration with support_judgment ───────────────────────

_CTX = "Snippet #1: OFAC SDN list includes Rosoboronexport.\n\nSnippet #2: EU lists it under 833/2014."
# ARIA's NATIVE citation style: source name + confidence tag, NO foreign token.
_NATIVE_ANSWER = ("Rosoboronexport is on the US OFAC SDN list (OFAC) [CONFIRMED] and is "
                  "listed by the EU under Regulation 833/2014 (EU Consolidated List) [CONFIRMED].")


def test_verify_credits_native_grounding_when_supported():
    j = _judgment(["OFAC SDN list includes Rosoboronexport", "EU lists it under 833/2014"], supported=2)
    v = sv.verify_response(_NATIVE_ANSWER, _CTX, support_judgment=j)
    assert v["verdict"] == "grounded"
    assert v["grounded_rate"] == 1.0
    assert v["source"] == "native_confidence_support"


def test_verify_native_unsupported_scores_zero_not_inflated():
    """A tagged answer the judge finds unsupported → 0.0, never credited for the tag."""
    j = _judgment(["OFAC SDN list includes Rosoboronexport", "EU lists it under 833/2014"], supported=0)
    v = sv.verify_response(_NATIVE_ANSWER, _CTX, support_judgment=j)
    assert v["grounded_rate"] == 0.0
    assert v["verdict"] == "ungrounded"


def test_verify_without_judgment_is_backward_compatible_no_citations():
    """No support judgment + no markers → unchanged no_citations (rate None)."""
    v = sv.verify_response(_NATIVE_ANSWER, _CTX)
    assert v["verdict"] == "no_citations"
    assert v["grounded_rate"] is None


def test_fabricated_snippet_ref_still_zero_even_with_supported_judgment():
    """R-F2391 marker path takes precedence: a fabricated `[from snippet #9]`
    marker scores 0.0 regardless of any support judgment (can't launder a
    fabricated token through the native path)."""
    resp = "Claim with a fake source [from snippet #9]."
    j = _judgment(["Claim with a fake source"], supported=1)
    v = sv.verify_response(resp, "Snippet #1: only one snippet exists.", support_judgment=j)
    assert v["grounded_rate"] == 0.0
    assert v["verdict"] == "ungrounded"


def test_honest_abstention_not_penalized():
    """An on-source answer that abstains (no confidence-tagged claims) → the judge
    yields no_claims → native returns None → no_citations (rate None), which is
    EXCLUDED from the grounded-rate average, i.e. abstention is not scored 0."""
    abstain = "The population figure is not supported by the provided sources."
    v = sv.verify_response(abstain, _CTX, support_judgment={"status": "no_claims", "claims": []})
    assert v["verdict"] == "no_citations"
    assert v["grounded_rate"] is None
