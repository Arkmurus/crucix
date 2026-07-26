"""R-F3158 — the Art. 10 condition, enforced rather than recorded.

── What this closes ──────────────────────────────────────────────────────
R-F3153 added `criminal_data_condition` to the case model as a free string.
Recording a legal determination in a field nobody validates is the same shape
as the Phase A gates that could not fail: it looks like compliance and asserts
nothing. A blank string satisfied it.

UK GDPR Art. 10 permits processing of criminal-conviction and offence data
only under the control of official authority OR where authorised by domestic
law. In the UK that authorisation is DPA 2018 s.10(5) + Schedule 1. Every
Schedule 1 Part 1 and Part 2 condition additionally requires an APPROPRIATE
POLICY DOCUMENT to be in place at the time of the processing (Sch. 1 Part 4
para 5). Not written afterwards — in place at the time.

So the code now refuses to hold criminal-offence data for a tenant that has
not recorded (a) which condition it relies on and (b) the APD that condition
requires. This mirrors the pack lifecycle: a DRAFT pack cannot be used on a
live case, and an unevidenced Art. 10 condition cannot either.

── The boundary of what code can decide ──────────────────────────────────
Which condition applies is a legal determination about a specific customer's
purpose, sector and contract. Nothing here picks one. What the code CAN do —
and now does — is refuse to proceed until a human has picked one, name the
consequences of each, and make the choice auditable.

`docs/vetting/appropriate_policy_document.md` is the template the APD
reference should point at. It is a DRAFT pending counsel, and this module
does not treat the existence of a reference as proof the document is adequate
— only as proof that someone was required to produce one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Sch1Condition(str, Enum):
    """The DPA 2018 Schedule 1 conditions plausibly relevant to employment
    screening. Deliberately NOT the full schedule: offering conditions that
    do not fit this processing would invite a customer to select one that
    reads well and does not hold."""

    # Part 1 — requires an APD.
    EMPLOYMENT_SOCIAL_SECURITY = "SCH1_P1_1_EMPLOYMENT"
    # Part 2 — substantial public interest; each requires an APD.
    PREVENTING_DETECTING_UNLAWFUL_ACTS = "SCH1_P2_10_UNLAWFUL_ACTS"
    REGULATORY_REQUIREMENTS = "SCH1_P2_12_REGULATORY"
    SAFEGUARDING = "SCH1_P2_18_SAFEGUARDING"
    # Part 3 — legal claims. Does NOT require an APD, and does not support
    # routine screening; it covers processing necessary for legal proceedings.
    LEGAL_CLAIMS = "SCH1_P3_33_LEGAL_CLAIMS"


# Sch. 1 Part 4 para 5: Part 1 and Part 2 conditions require an appropriate
# policy document. Part 3 conditions do not.
_APD_REQUIRED = {
    Sch1Condition.EMPLOYMENT_SOCIAL_SECURITY,
    Sch1Condition.PREVENTING_DETECTING_UNLAWFUL_ACTS,
    Sch1Condition.REGULATORY_REQUIREMENTS,
    Sch1Condition.SAFEGUARDING,
}

# Conditions that cannot carry routine, ongoing screening on their own.
_NOT_FOR_ROUTINE_SCREENING = {Sch1Condition.LEGAL_CLAIMS}

CONDITION_NOTES: dict[Sch1Condition, str] = {
    Sch1Condition.EMPLOYMENT_SOCIAL_SECURITY:
        "Sch. 1 Pt 1 para 1 — processing necessary for obligations or rights "
        "in the field of employment. The usual condition for pre-employment "
        "screening. Requires an appropriate policy document.",
    Sch1Condition.PREVENTING_DETECTING_UNLAWFUL_ACTS:
        "Sch. 1 Pt 2 para 10 — preventing or detecting unlawful acts, in the "
        "substantial public interest. Requires an appropriate policy document.",
    Sch1Condition.REGULATORY_REQUIREMENTS:
        "Sch. 1 Pt 2 para 12 — complying with regulatory requirements "
        "involving unlawful acts and dishonesty. Requires an appropriate "
        "policy document.",
    Sch1Condition.SAFEGUARDING:
        "Sch. 1 Pt 2 para 18 — safeguarding of children and of individuals at "
        "risk. Requires an appropriate policy document.",
    Sch1Condition.LEGAL_CLAIMS:
        "Sch. 1 Pt 3 para 33 — legal claims or judicial acts. No APD required, "
        "but this does NOT authorise routine screening and must not be "
        "selected as a general basis.",
}


class LegalBasisError(ValueError):
    """The Art. 10 position is not evidenced; processing must not proceed."""


@dataclass(frozen=True)
class Art10Position:
    """A tenant's recorded position for criminal-offence data.

    `apd_reference` and `apd_review_date` are the Sch. 1 Pt 4 para 5 artefacts.
    The review date is required because an APD that is never reviewed stops
    being an appropriate policy document — para 5(2)(b) requires it to be kept
    under review.
    """

    tenant_id: str
    condition: Sch1Condition
    apd_reference: str = ""
    apd_review_date: date | None = None
    dpia_reference: str = ""
    determined_by: str = ""

    @property
    def apd_required(self) -> bool:
        return self.condition in _APD_REQUIRED


def requires_apd(condition: Sch1Condition) -> bool:
    return condition in _APD_REQUIRED


def validate_position(position: Art10Position, as_of: date) -> None:
    """Raise LegalBasisError unless this tenant may hold criminal-offence data.

    Every branch below is a statutory requirement, not a house style.
    """
    if position.condition in _NOT_FOR_ROUTINE_SCREENING:
        raise LegalBasisError(
            f"{position.condition.value} does not authorise routine "
            f"pre-employment screening; select a Part 1 or Part 2 condition")

    if not position.determined_by.strip():
        raise LegalBasisError(
            "the Art. 10 condition must be attributed to the person who "
            "determined it — an unattributed legal position cannot be "
            "demonstrated under Art. 5(2)")

    if position.apd_required:
        if not position.apd_reference.strip():
            raise LegalBasisError(
                f"{position.condition.value} requires an appropriate policy "
                f"document (DPA 2018 Sch. 1 Pt 4 para 5) to be in place AT "
                f"THE TIME of processing; none is recorded")
        if position.apd_review_date is None:
            raise LegalBasisError(
                "the appropriate policy document must be kept under review "
                "(Sch. 1 Pt 4 para 5(2)(b)); no review date is recorded")
        if position.apd_review_date < as_of:
            raise LegalBasisError(
                f"the appropriate policy document review date "
                f"({position.apd_review_date.isoformat()}) has passed; review "
                f"it before processing further criminal-offence data")


def holds_criminal_offence_data(case) -> bool:
    """Whether this case actually engages Art. 10.

    Deliberately narrow: a screening file only engages Art. 10 once conviction
    information is actually declared or evidenced. Treating every case as
    Art. 10 from creation would push tenants to record a condition they do not
    yet need, which is its own compliance theatre.
    """
    from .models import DocumentType

    # R-F3158 — read dict-OR-model. `model_copy(update=...)` with a dumped
    # payload leaves `inputs` as a plain dict, and a bare getattr() then
    # returns the default: the gate would report "no criminal data" and let it
    # through. The route re-validates before calling this, so the miss was not
    # reachable — but a detector that depends on statement ordering in ONE
    # caller is the "protection exists, just not on the path that produces the
    # answer" shape. Made shape-independent instead.
    def _field(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    criminality_docs = {
        DocumentType.DISCLOSURE_CERTIFICATE,
        DocumentType.NPCC_POLICE_LETTER,
        DocumentType.SIA_LICENCE,
    }
    criminality_values = {d.value for d in criminality_docs}

    inputs = _field(case, "inputs", {}) or {}
    if _field(inputs, "convictions_declared", False):
        return True
    route = _field(inputs, "criminality_route", None)
    if route in criminality_docs or route in criminality_values:
        return True

    for document in (_field(case, "documents", []) or []):
        doc_type = _field(document, "doc_type", None)
        if doc_type in criminality_docs or doc_type in criminality_values:
            return True
    return False
