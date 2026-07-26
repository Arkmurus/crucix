"""ARIA Vetting domain models — Phase 0 hardening.

Adds: Money (no GBP hard-coding, integer minor units, no silent
conversion), CaseManifest (pins pack_id+version+hash per case), date
validation on career entries (no end-before-start). Check-record objects
replacing the remaining booleans are Phase 1 scope.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, model_validator


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
    outcome: str = "PENDING"
    outcome_date: date | None = None
    employment_end: date | None = None
