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

# R-F3189 — the progress sheet's COPY / ORG / N-A columns.
#
# NOT_RECORDED is the DEFAULT and is deliberately distinct from COPY_ONLY:
# "nobody has said" and "we hold only a copy" are different states, and
# defaulting an unanswered question to the weaker answer would quietly assert
# something no one checked. Same discipline as the assessment verdict — absence
# is never a finding.
SightingLiteral = Literal[
    "ORIGINAL_SEEN",    # original inspected in person, copy retained (7.4 c))
    "COPY_ONLY",        # a copy or scan only — original never sighted
    "NOT_APPLICABLE",   # e.g. an electronic right-to-work share code
    "NOT_RECORDED",     # nobody has answered yet
]

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
    # R-F3207 — the intake set an officer actually chases. These existed
    # nowhere in the vocabulary, so a CV or an interview write-up could only be
    # filed as OTHER, and OTHER satisfies no requirement and evidences no
    # clause. A document the file needs but cannot name is a document the
    # engine cannot count.
    CV = "CV"
    INTERVIEW_RECORD = "INTERVIEW_RECORD"
    RIGHT_TO_WORK_CHECK = "RIGHT_TO_WORK_CHECK"
    OTHER = "OTHER"


# R-F3207 — how a requirement got onto the file. Displayed, not decorative: an
# auditor asking "why was this asked for?" gets a different answer for a clause
# of the standard than for something the screening team added by hand, and the
# officer waiving one needs to know which they are waiving.
RequirementBasisLiteral = Literal[
    "STANDARD",         # required by the screening standard — carries a clause
    "STATUTORY",        # required by law independently of the standard, e.g.
                        # the right-to-work check under the Immigration, Asylum
                        # and Nationality Act 2006. Distinguished because the
                        # consequence of skipping it is a civil penalty, not a
                        # non-conformity — and because it survives a customer
                        # who chooses not to screen to BS 7858 at all.
    "HOUSE_PRACTICE",   # our own intake discipline, not the standard's demand
    "CLIENT_CONTRACT",  # asked for by the customer this screening is run for
]


class DocumentRequirement(BaseModel):
    """One thing the file must hold, and what satisfies it.

    Lives here rather than in requirements.py so packs can carry these without
    a circular import, and so the manual per-case additions are the SAME shape
    as the pack's — an officer's bespoke requirement is a first-class item, not
    a note in a free-text box that nothing counts.

    `accepted` is a LIST because the question is almost never "do you have a
    passport" but "do you have any acceptable proof of identity". Encoding the
    alternatives means a driving licence satisfies the requirement without an
    officer having to know that it does.
    """

    model_config = {"frozen": True}

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    accepted: list[DocumentType] = Field(min_length=1)
    # 2 proofs of address is a min_count of 2, not two separate requirements:
    # a file holding one is PARTIAL, which is a different thing to say than
    # "the second proof of address is outstanding".
    min_count: int = 1
    reference: str = ""
    basis: RequirementBasisLiteral = "STANDARD"
    # Which part of the screening this belongs to; drives the officer's view.
    stage: str = "INTAKE"
    mandatory: bool = True
    note: str = ""
    origin: Literal["PACK", "MANUAL"] = "PACK"


class RequirementWaiver(BaseModel):
    """A requirement deliberately not pursued — recorded, never implied.

    Waiving is a judgement a named person makes and must answer for, so it
    carries who and why, exactly like an employment decision. A waived
    requirement renders as WAIVED with that name attached; it never renders as
    satisfied. The one thing that must not be possible here is a file that
    looks complete because someone quietly stopped asking.
    """

    model_config = {"frozen": True}
    key: str = Field(min_length=1)
    waived_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    waived_at: date


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
    # R-F3189 — BS 7858 7.4 c)/d): "visual inspection of ORIGINAL documents and
    # retention of a copy", with a record of who examined and copied them. The
    # manual progress sheet tracks this as three columns — COPY / ORG / N-A —
    # and the module recorded none of it. Holding a scan is NOT the same as
    # having sighted the original, and for identity documents that difference
    # is the whole control: a forged PDF passes a copy check and fails an
    # in-person one.
    sighting: SightingLiteral = "NOT_RECORDED"
    examined_by: str = ""
    examined_at: date | None = None


class CareerEntry(BaseModel):
    entry_id: str
    entry_type: CareerEntryType
    start: date
    end: date | None = None
    organisation: str | None = None
    state: VerificationState = VerificationState.UNVERIFIED
    supporting_documents: list[str] = Field(default_factory=list)
    notes: str = ""

    # ── R-F3206 — the referee the APPLICANT nominated for this period ────────
    #
    # The share dialog made the vetting officer TYPE the referee's name and
    # address, when the applicant had already nominated them on the application
    # form. Re-keying data the file already holds is how transcription errors and
    # mis-sent referee links happen — a referee link exposes one engagement, so
    # sending it to a mistyped address is a disclosure, not a typo.
    #
    # Nominated details belong on the PERIOD, not on the share request: a period
    # is what a referee confirms, one referee can cover several periods, and the
    # nomination has to survive the dialog being cancelled and reopened.
    #
    # All optional. A gap period (unemployment, travel) legitimately has no
    # nominated referee — the officer names one by hand, and
    # `periods_without_nominated_referee` surfaces those as their own action
    # instead of leaving them to be noticed.
    referee_name: str | None = None
    referee_email: str | None = None
    referee_phone: str | None = None
    referee_title: str | None = None

    def has_nominated_referee(self) -> bool:
        """True when the applicant named someone AND left a way to reach them.

        A name with no address cannot be sent a link, so it is not a usable
        nomination — reporting it as one would recreate the manual re-keying this
        exists to remove.
        """
        if not (self.referee_name or "").strip():
            return False
        return bool((self.referee_email or "").strip() or (self.referee_phone or "").strip())

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

    # ── R-F3174: verified against BS 7858:2019 clause by clause ───────────
    # Added after checking the pack against the licensed standard rather than
    # against memory. Each field maps to a specific requirement that the old
    # checklist either omitted or collapsed into a single tick.
    #
    # 7.3.2 a)8) — SIA licence NUMBER *and expiry date*. The number alone was
    # captured; an expired licence is a live compliance failure in the security
    # industry, which is the first sector this module serves.
    sia_licence_expiry: date | None = None
    # 7.4 c)1) — a licence holder's licence must be verified against the SIA
    # public register, and a copy of the register search result retained.
    # Seeing the card is not the check; the register is.
    sia_register_verified: bool = False

    # 7.4 f) — the public-record search is SEVEN required elements. One boolean
    # let an officer tick "done" with only some of them performed, which is the
    # collapse-to-a-single-tick pattern this module exists to avoid.
    electoral_roll_confirmed: bool = False
    linked_addresses_5y_searched: bool = False
    ccj_iva_searched: bool = False
    bankruptcy_orders_searched: bool = False
    aliases_searched: bool = False

    # 7.4 c) and d) — the standard requires a record of WHO examined and copied
    # the original identity and address documents, not merely that it happened.
    identity_examined_by: str = ""
    address_examined_by: str = ""

    # ── R-F3212 — 7.3.4, the interview ──────────────────────────────────────
    #
    # `interview_done` alone is a tick with nobody and no date behind it. The
    # clause requires the interview to happen BEFORE an offer, and that is a
    # claim about a date — unanswerable from a boolean. Recording who conducted
    # it matters for the same reason the examiner of an identity document does:
    # an auditor asks "who, and when", and "yes" is not an answer to either.
    interview_date: date | None = None
    interviewed_by: str = ""


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

    # ── R-F3211 — requirements the officer added by hand ────────────────────
    #
    # The pack carries what the framework demands; this carries what THIS
    # screening additionally needs — a sponsor licence, a professional
    # registration, an airside pass, whatever the contract asks for. Same
    # shape as the pack's, so it is counted, chased and reported identically
    # rather than living as a note nothing reads.
    #
    # On the CASE, not the pack, deliberately: adding one must never mutate a
    # released pack (its hash is pinned in every manifest), and one applicant's
    # extra requirement is not every applicant's.
    extra_requirements: list[DocumentRequirement] = Field(default_factory=list)
    requirement_waivers: list[RequirementWaiver] = Field(default_factory=list)

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
    # R-F3172 — set whenever the file CHANGES after its last assessment. The
    # cached verdict then describes a file that no longer exists, so the UI must
    # render "re-assessment required", never the cached state. Proven defect: a
    # case assessed IN_PROGRESS/0 blockers, then given a future-dated entry,
    # still showed IN_PROGRESS/0 blockers with a current-looking date while the
    # truth was NOT_READY/1 blocker.
    assessment_stale: bool = False

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
