"""Unit tests for compliance_review_specificity trigger logic.

Past failure mode 2026-04-26: ARIA reviewed a C4 / Ukraine ML8 draft and
returned "no material blind spots" while the draft asked only for a
"Standard KYC package" and a "preliminary identifying letter". This
module forces ARIA to demand attribute-specific gates (letterhead,
signatory, seal, non-retransfer, KYC enumeration) on review prompts.

The trigger must:
  - FIRE on review-intent + compliance-domain combinations.
  - STAY QUIET on review prompts about non-compliance topics.
  - STAY QUIET on compliance discussions that aren't asking for a review.
"""
from aria_service.intel.compliance_review_specificity import (
    get_compliance_review_specificity_context,
)


def test_fires_on_serban_c4_ml8_review_request():
    """The exact pattern from the 2026-04-26 incident must trigger."""
    msg = (
        "Aria, this is the version that Ari shared for review. Can you "
        "review it or give a clean version to ensure we covered all "
        "blind spots?\n\n"
        "Hi Alon, ... ANCEX authorisation under GEO 158/1999 ... ML8 "
        "controlled goods ... End User Certificate ... Ukrainian "
        "sovereign end user ..."
    )
    out = get_compliance_review_specificity_context(msg)
    assert out, "compliance review specificity addendum must fire on this prompt"
    assert "letterhead" in out.lower()
    assert "non-retransfer" in out.lower() or "non-re-export" in out.lower()


def test_fires_on_blind_spot_check_phrasing():
    msg = "blind spot check on this draft EUC for the Ukrainian end user"
    assert get_compliance_review_specificity_context(msg)


def test_fires_on_kyc_review():
    msg = "review this kyc package and let me know if it's tight"
    assert get_compliance_review_specificity_context(msg)


def test_quiet_on_review_of_non_compliance_topic():
    """Review intent without the compliance domain must NOT inject the addendum."""
    msg = "review my marketing copy and give me a clean version"
    assert get_compliance_review_specificity_context(msg) == ""


def test_quiet_on_compliance_discussion_without_review_intent():
    """Compliance topic alone (e.g. an explainer question) must NOT inject."""
    msg = "what is the difference between an EUC and a DSP-83 certificate"
    assert get_compliance_review_specificity_context(msg) == ""


def test_quiet_on_empty_input():
    assert get_compliance_review_specificity_context("") == ""
    assert get_compliance_review_specificity_context("   ") == ""


def test_handles_long_drafts_via_head_cap():
    """Trigger detection should not require scanning the whole draft body."""
    long_tail = "padding " * 5000  # ~40k chars
    msg = "Please review this draft and check for blind spots — ML8 export to Ukraine.\n" + long_tail
    out = get_compliance_review_specificity_context(msg)
    assert out, "should still fire when the trigger phrases are within the first 6kB"
