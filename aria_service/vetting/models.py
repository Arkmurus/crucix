"""ARIA Vetting domain models — Phase 0 hardening.

Adds: Money (no GBP hard-coding, integer minor units, no silent
conversion), CaseManifest (pins pack_id+version+hash per case), date
validation on career entries (no end-before-start). Check-record objects
replacing the remaining booleans are Phase 1 scope.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# R-F3152 — the accepted retention outcomes, as a validated type rather than a
# free string. Kept here (not imported from retention.py) so models.py stays
# dependency-free; retention.CaseOutcome mirrors it and a test pins them equal,
# so the two cannot drift.
CaseOutcomeLiteral = Literal["PENDING", "UNSUCCESSFUL", "EMPLOYED", "WITHDRAWN"]

# R-F3153 — the lawful bases available for employment screening.
#
# CONSENT is ABSENT ON PURPOSE and must stay absent. An applicant cannot freely
# refuse their prospective employer's screening, so consent is not "freely
# given" within Art. 4(11) and collapses under Art. 7(4). Offering it as an
# option would let a customer select the one basis a regulator reliably rejects
# — and they would, because it is the one that feels most reassuring.
LawfulBasisLiteral = Literal[
    "LEGITIMATE_INTERESTS",   # Art. 6(1)(f) — the usual basis, needs an LIA
    "CONTRACT",               # Art. 6(1)(b) — steps prior to entering a contract
    "LEGAL_OBLIGATION",       # Art. 6(1)(c) — e.g. statutory sector vetting
    "PUBLIC_TASK",            # Art. 6(1)(e)
]


class Money(BaseModel):
    model_config = {"frozen": True}
    amount_minor: int              # pence, cents, centavos...
    currency: str                  # ISO 4217

    def __str__(self) -> str:
        return f"{self.amount_minor / 100:,.2f} {self.currency}"


class CareerEntryType(str, Enum):
    EMPLOYMENT = "EMPLOYMENT"
    SELF_EMPLOYMENT = "SELF_EMPLOYMENT"
    UNEMPLOYMENT = "UNEMPLOYMENT"
    EDUCATION = "EDUCATION"
    CAREER_BREAK = "CAREER_BREAK"
    RESIDENCE_ABROAD = "RESIDENCE_ABROAD"
    TRAVEL_ABROAD = "TRAVEL_ABROAD"


class VerificationState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    EVIDENCE_RECEIVED = "EVIDENCE_RECEIVED"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    COVERED_BY_STAT_DEC = "COVERED_BY_STAT_DEC"


class DocumentType(str, Enum):
    PASSPORT = "PASSPORT"
    DRIVING_LICENCE = "DRIVING_LICENCE"
    BIRTH_CERTIFICATE = "BIRTH_CERTIFICATE"
    RESIDENCE_PERMIT = "RESIDENCE_PERMIT"
    PROOF_OF_ADDRESS = "PROOF_OF_ADDRESS"
    PAYSLIP = "PAYSLIP"
    P45 = "P45"
    P60 = "P60"
    EMPLOYMENT_CONTRACT = "EMPLOYMENT_CONTRACT"
    REDUNDANCY_LETTER = "REDUNDANCY_LETTER"
    BANK_STATEMENT = "BANK_STATEMENT"
    HMRC_DOCUMENT = "HMRC_DOCUMENT"
    DWP_CONFIRMATION = "DWP_CONFIRMATION"
    EMPLOYER_REFERENCE = "EMPLOYER_REFERENCE"
    EDUCATION_REFERENCE = "EDUCATION_REFERENCE"
    ACCOUNTANT_REFERENCE = "ACCOUNTANT_REFERENCE"
    SIA_LICENCE = "SIA_LICENCE"
    DISCLOSURE_CERTIFICATE = "DISCLOSURE_CERTIFICATE"
    NPCC_POLICE_LETTER = "NPCC_POLICE_LETTER"
    STATUTORY_DECLARATION = "STATUTORY_DECLARATION"
    TRAVEL_EVIDENCE = "TRAVEL_EVIDENCE"
    APPLICATION_FORM = "APPLICATION_FORM"
    SIGNED_AUTHORISATION = "SIGNED_AUTHORISATION"
    OTHER = "OTHER"


class UploadedDocument(BaseModel):
    document_id: str
    doc_type: DocumentType
    evidence_id: str | None = None
    covers_from: date | None = None
    covers_to: date | None = None
    issuer: str | None = None
    extraction_confidence: float = 0.0
    authenticity_flags: list[str] = Field(default_factory=list)
    # R-F3155 — integrity digest of the PLAINTEXT examined. The evidence store
    # hashes what it actually holds (the ciphertext), so this is what proves
    # which document a human looked at. `encrypted` records whether this
    # artifact is crypto-shreddable: a document stored before encryption was
    # enabled cannot be erased by destroying the key, and an erasure report
    # must not pretend otherwise.
    plaintext_sha256: str = ""
    encrypted: bool = False


class CareerEntry(BaseModel):
    entry_id: str
    entry_type: CareerEntryType
    start: date
    end: date | None = None
    organisation: str | None = None
    state: VerificationState = VerificationState.UNVERIFIED
    supporting_documents: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _dates_sane(self):
        if self.end is not None and self.end < self.start:
            raise ValueError(
                f"entry {self.entry_id}: end {self.end} before start {self.start}"
            )
        return self


class FinancialFlags(BaseModel):
    ccj_total: Money | None = None
    is_bankrupt: bool = False
    is_or_was_director: bool = False


class ScreeningInputs(BaseModel):
    # NOTE (Phase 1): these booleans will be superseded by typed
    # CheckExecution records (source, performer, timestamps, expiry, result).
    full_name: bool = False
    previous_names_declared: bool = False
    address_history_5y: bool = False
    date_of_birth: bool = False
    ni_number: bool = False
    right_to_work_evidenced: bool = False
    sia_licence_number: str | None = None
    convictions_declared: bool = False
    financial_history_declared: bool = False
    misrepresentation_ack_signed: bool = False
    verification_authorisation_signed: bool = False
    screening_consent_signed: bool = False
    identity_verified: bool = False
    address_confirmed: bool = False
    watchlist_check_done: bool = False
    public_record_search_done: bool = False
    interview_done: bool = False
    criminality_route: DocumentType | None = None


class CaseManifest(BaseModel):
    """Pins the exact rules a case is governed by. Immutable once set;
    the assessment service resolves packs ONLY through this."""
    model_config = {"frozen": True}
    pack_id: str
    pack_version: str
    pack_hash: str


class VettingCase(BaseModel):
    # R-F3137 — tenant_id is the isolation boundary and is REQUIRED, not
    # optional-with-a-default. A vetting case carries criminal-conviction and
    # financial data about a named individual (UK GDPR Art. 10 territory);
    # this is the most sensitive data set in the platform. An optional tenant
    # field defaults to "" on the one code path that forgets to set it, and
    # "" then matches every other forgetful writer — which is precisely how
    # the five cross-tenant DD leaks happened. Required-by-construction means
    # a case that does not know who owns it cannot be built at all.
    tenant_id: str = Field(min_length=1)
    case_id: str
    applicant_name: str
    date_of_birth: date
    employment_start: date
    screening_years: int = 5
    manifest: CaseManifest | None = None
    conditional_employment_start: date | None = None
    extension_approved: bool = False
    inputs: ScreeningInputs = Field(default_factory=ScreeningInputs)
    career: list[CareerEntry] = Field(default_factory=list)
    documents: list[UploadedDocument] = Field(default_factory=list)
    financial: FinancialFlags = Field(default_factory=FinancialFlags)
    stat_dec_days_used: int = 0

    # R-F3148 — retention anchors. A retention period runs from an OUTCOME, so
    # these are what start the clock; without them `retention_due_date` returns
    # None with a stated reason rather than inventing a date. `employment_end`
    # is separate from `outcome_date` on purpose: the post-employment clock
    # starts when employment ENDS, and anchoring it to anything else would
    # schedule a live personnel file for deletion years early.
    # R-F3152 — a plain `str` here was a live 500: retention.py does
    # `CaseOutcome(case.outcome)`, so any value outside the enum raised
    # ValueError inside the route. An unvalidated field that a downstream enum
    # constructor trusts is a crash waiting for its first typo.
    outcome: CaseOutcomeLiteral = "PENDING"
    outcome_date: date | None = None
    employment_end: date | None = None

    # R-F3153 — Art. 22 / Art. 5(2). Decisions are RECORDED, never derived.
    # Art. 22(3) also gives the applicant the right to contest, so disputes
    # live on the same file as the decision they contest.
    decisions: list[dict] = Field(default_factory=list)
    disputes: list[dict] = Field(default_factory=list)

    # R-F3153 — Art. 6 / Art. 10 lawful basis, recorded per case.
    #
    # Deliberately defaults to LEGITIMATE_INTERESTS, and CONSENT is not an
    # accepted value at all (see LawfulBasisLiteral). In an employment context
    # consent is not freely given — the imbalance of power between employer and
    # applicant makes it invalid (EDPB Opinion 2/2017; ICO employment guidance).
    # BS 7858 requires a signed screening authorisation, and that signature is
    # evidence the screening was AUTHORISED; it is not the GDPR lawful basis.
    # Conflating the two is the most common compliance error in this market, so
    # the type system refuses it here.
    lawful_basis: LawfulBasisLiteral = "LEGITIMATE_INTERESTS"
    criminal_data_condition: str = ""

    # R-F3168 — last assessment result, cached so a case LIST can show status
    # without re-running an assessment per row. It is a CACHE, never a source of
    # truth: `last_assessed_at` is stored with it so the UI can say "as of" and
    # an unassessed case reads UNKNOWN rather than clean. The authoritative
    # answer always comes from POST /assess, which recomputes from scratch.
    last_status: str = ""
    last_assessed_at: str = ""
    last_blockers: int = 0

    @model_validator(mode="after")
    def _retention_anchors_coherent(self):
        # A dated outcome that precedes the employment it concluded is not a
        # date we may quietly accept: it would compute a disposal deadline in
        # the past and mark a live file overdue for deletion.
        if self.outcome_date is not None and self.outcome_date < self.employment_start:
            # Only meaningful once employment actually started; a screening
            # abandoned before the start date legitimately predates it.
            if self.outcome == "EMPLOYED":
                raise ValueError(
                    "outcome_date precedes employment_start for an EMPLOYED case")
        if self.employment_end is not None and self.employment_end < self.employment_start:
            raise ValueError("employment_end precedes employment_start")
        return self
