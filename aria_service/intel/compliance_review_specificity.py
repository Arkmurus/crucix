# =============================================================================
# ARIA — Compliance Review Specificity
# aria_service/intel/compliance_review_specificity.py
#
# When the user asks ARIA to review a draft email/letter that touches
# export-control / dual-use compliance (ML8, dual-use, EUC, KYC, broker,
# end-user, sanctions, transit), ARIA must demand SPECIFICITY of document
# attributes — not just document types. Past incident 2026-04-26: ARIA
# reviewed a C4 / Serban / Ukraine draft and returned "ready to send, no
# material blind spots" while the draft asked only for a "Standard KYC
# package" and a "preliminary identifying letter from the Ukrainian
# sovereign end user" — both vague enough that the buyer could supply
# paper-thin documents and call the gate cleared. The brain already has
# the EUC-clause requirements (aria_service/intel/euc_library.py covers
# letterhead, signatory, issuing authority/seal, non-retransfer,
# non-re-export, delivery verification) — this module forces those
# attributes through to compliance-review prompts.
#
# Trigger: a message that reads like "review this draft" or "blind spot
# check" AND mentions one of the compliance triggers below. Quiet on
# anything else (returns "" so the prompt stays clean).
# =============================================================================

from __future__ import annotations

import logging
import re

logger = logging.getLogger("ARIA.ComplianceReviewSpecificity")


# ── Trigger detection ────────────────────────────────────────────────────────
# Two conditions must BOTH hold for the addendum to fire:
#   1. The user is asking ARIA to REVIEW a draft (not just write one).
#   2. The draft itself touches export-control / dual-use compliance.
#
# Patterns are deliberately permissive — false positives just inject a
# small prompt addendum (~1.5kB), which costs little. False negatives
# silently let through the very review case this module exists to fix.

_REVIEW_INTENT = re.compile(
    r"\b("
    r"review|review it|review this|review the|"
    r"clean version|clean copy|"
    r"blind\s*spot|blindspot|cover.*blind\s*spot|"
    r"check.*draft|review.*draft|draft.*review|"
    r"any\s+(?:issues|gaps|holes|risks)|"
    r"sanity\s*check"
    r")\b",
    re.IGNORECASE,
)

# Compliance domain — the draft must touch one of these for the addendum
# to be relevant. Catches ML8/dual-use, end-user instruments, KYC, broker
# licensing under the major regimes, sanctions screening, controlled-
# transit framings, and the specific UK/EU/Romanian/US authorities a
# defence broker email is most likely to reference.
_COMPLIANCE_DOMAIN = re.compile(
    r"\b("
    r"ml[\s\-]?8|dual[\s\-]?use|"
    r"end[\s\-]?user|euc|end[\s\-]?user[\s\-]?cert|"
    r"export[\s\-]?(?:control|licen[cs]e|authoris)|"
    r"kyc|ubo|beneficial\s+owner|"
    r"broker(?:age)?|brokering|intermediation|"
    r"sanction|ofac|ofsi|consolidated\s+list|"
    r"ancex|ecju|spire|siel|sitcl|dsp[\s\-]?(?:5|73|83)|"
    r"itar|ear|ccl|"
    r"non[\s\-]?(?:re[\s\-]?)?(?:export|transfer|retransfer)|"
    r"diversion|re[\s\-]?export|re[\s\-]?transfer|"
    r"controlled\s+goods?|strategic\s+goods?|"
    r"defen[cs]e\s+(?:article|export|broker)"
    r")\b",
    re.IGNORECASE,
)


_ADDENDUM = """\
COMPLIANCE-REVIEW SPECIFICITY (ML8 / dual-use / EUC / KYC) —
The user is asking you to review a draft email/letter that touches
export-control or dual-use compliance. Do NOT return "no material
blind spots" if any of the following gates are stated only as
document TYPES (e.g. "standard KYC package", "End User Certificate",
"licence") rather than the SPECIFIC ATTRIBUTES that would let the
counterparty supply a paper-thin document and call the gate cleared.

For every gating item in the draft, check it specifies:

1. EUC / end-user identifying letter:
   - Issued on government / ministry letterhead.
   - Named authorised signatory + title (not just "the buyer").
   - Authentication: state seal, notarisation, or diplomatic channel.
   - Quantity, intended end-use, end-use location.
   - EXPLICIT non-retransfer AND non-re-export clauses.
   - Country of final destination stated.
   See aria_service/intel/euc_library.py PROFILES for the full clause
   set the brain can audit against.

2. KYC package (must enumerate, not say "standard"):
   - Certificate of incorporation; articles.
   - Register of directors; register of members / UBOs (≥10% threshold).
   - Source-of-funds confirmation.
   - PEP + sanctions screening on each director and UBO against UK
     OFSI, US OFAC, EU consolidated, UN lists.
   - The same screening applied at contracting to all proposed parties
     (banks, escrow agent, freight forwarder).

3. Buyer role in the transaction (one of):
   - Takes title / takes physical possession / acts as commercial or
     financial intermediary / onward-resells to a further counterparty.
   The "is the buyer a reseller?" question must be CLOSED, not implied.

4. Routing / transit:
   - Any "no US transit" or "no EU transit" claim must be a BUYER
     WARRANTY (something the buyer confirms), not a SELLER ASSERTION.
   - Confirm whether US-flagged vessels, US carriers, US-domiciled
     financing, or US-origin components / technology are involved —
     EAR re-export rules can still bite on non-US-origin goods that
     contain US-controlled technology.

5. Licensing posture (state both sides explicitly):
   - Buyer-side: jurisdiction + authorisation reference (ANCEX /
     ECJU / SPIRE / SIEL / SITCL / DSP-5 / DSP-73 / DSP-83 / etc.).
   - Seller / broker-side: distinguish brokerage authorisation
     (e.g. ANCEX under Romanian GEO 158/1999) from EXPORT licence
     (separate filing, also requires the EUC).

6. Operational gates (often missed):
   - Deadline for the buyer to provide the documentation (working-day
     window) — open-ended asks invite stall.
   - Quote validity once issued (input-cost volatility on controlled
     goods).
   - Sanctions screening EXPLICIT (not merely implicit in KYC).

If any of the above is vague in the draft, your review must call it
out by name and propose a concrete tightening. "Substantively correct,
ready to send" is only an acceptable verdict when every gate is
attribute-specific. Past failure mode (2026-04-26): you reviewed a
C4 / Ukraine draft asking only for a "Standard KYC package" and a
"preliminary identifying letter" and said "no material blind spots" —
that verdict let through a counterparty-stallable structure on an
ML8 export to an active conflict zone. Do not repeat.
"""


def get_compliance_review_specificity_context(message: str) -> str:
    """Return the compliance-review specificity addendum if the message is
    asking for a review of a draft that touches export-control compliance.

    Returns an empty string for any other message — fires conservatively
    to avoid bloating prompts on unrelated turns.
    """
    if not message or not message.strip():
        return ""

    # Cap at the first ~6kB of the message — patterns are short and we
    # never need the full attached draft body to make the trigger call.
    head = message[:6000]

    has_review_intent = bool(_REVIEW_INTENT.search(head))
    has_compliance_domain = bool(_COMPLIANCE_DOMAIN.search(head))

    if not (has_review_intent and has_compliance_domain):
        return ""

    logger.info(
        "[compliance_review_specificity] addendum injected (review_intent=%s, compliance_domain=%s)",
        has_review_intent,
        has_compliance_domain,
    )
    return _ADDENDUM
