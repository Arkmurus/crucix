"""R-F3402 — the DD Standard: a versioned question catalogue and evidence-state model.

WHY THIS EXISTS
═══════════════
ARIA reaches plenty of sources. What was missing is anything between collection and
rendering that forces the data to MATTER. Coverage was an EMERGENT property — whatever
the layers happened to produce — so "did you check X?" could only be answered by reading
the output and inferring. Three measured consequences:

  * `discipline_coverage` (dd_orchestrator) certified disciplines from which LAYERS RAN.
    A populated director list asserted `sanctions_screening` covered, including on runs
    where the screen returned `screened: False`. It also cannot pass: `defence_broker`
    requires 21 disciplines and the layer→discipline map can emit at most 15, so
    `gate_passes` is identically False. And no renderer reads it.

  * `_dd_decision_readiness` (dd_schema) measures five COMPOSITE questions honestly, but
    "adverse media, corruption and litigation" is one boolean over three different
    evidence bases with three different remedies. R-F3244 had to bolt
    `sanctions_evidenced`/`export_control_evidenced` onto one of them for exactly this
    reason — the decomposition was already happening, one question at a time.

  * Live run dd_b53ea3332471 (Silverbrook, 2026-07-29): SEVEN identity findings, of which
    THREE were about ARIA's own process ("Sanctions screen NOT performed", "Subject name
    resolved", "GREEN overridden to AMBER") rather than about the counterparty. Honest,
    and still not a decision surface.

This module is the closed set of assertions a report must answer. The orchestrator's job
becomes mechanical: for each question in the applicable tier, dispatch the resolvers that
can answer it. Coverage stops being emergent and becomes a CHECKLIST DIFF.

WHAT THIS MODULE IS NOT
═══════════════════════
Not a second aggregator. It does not re-measure what `_dd_decision_readiness` measures —
it DECOMPOSES it. The five readiness questions are recovered exactly as cluster rollups
(see `CLUSTER_TO_READINESS_KEY`), so the two surfaces cannot disagree: one is the other
at a different granularity. Retiring `discipline_coverage`'s layer→discipline proxy is a
separate, explicitly-sequenced change — two live aggregators is the failure class
CLAUDE.md §1 spent three R-numbers killing on the Phase A gates.

PURITY
══════
`assess()` is a pure function of a persisted report dict. No clock, no I/O, no network —
same contract as `vetting.rules.assess()`. That is what makes it replayable: the same
report always yields the same checklist, so a grade cannot drift without the evidence
drifting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .wire import fail_wire  # R-F1789 §21 brain-wiring

#: Versioned like code. A report records the version it was assessed under, so re-running
#: an old case against a new standard is a STATED event rather than a silent re-grade
#: (R-F591 carries version_number/version_diff; R-F2808 recorded why a silent flip reads
#: as a retraction). Bump MINOR to add a question, MAJOR to change a pass condition.
STANDARD_VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════
# EVIDENCE STATE
# ═══════════════════════════════════════════════════════════════════════════

class EvidenceState(str, Enum):
    """How a question terminated. Exactly one per question, never inferred.

    The first four are the evidence-strength ladder. The last three are terminal states
    that are NOT weak evidence — they are different sentences to the reader, and
    collapsing them into NOT_RUN is a lie in one direction or the other:

      * NOT_APPLICABLE — the question is not asked of this subject. R-F3063 exists
        because asking an individual for "financial capacity" capped every person DD at
        3/5 and read as a DEFICIENCY IN THE SUBJECT rather than a question nobody asked.
      * AWAITING_COUNTERPARTY — a `SUPPLIED`/`HYBRID` question whose evidence can only
        come from the counterparty (identity documents, authority to act, source of
        funds). Reporting these as NOT_RUN understates the product: they are a stated
        boundary, not a failure to look.
      * WAIVED — a named person decided not to pursue it, and said why. Mirrors
        `vetting.requirements.RequirementState.WAIVED`, which renders WAIVED and NEVER
        renders satisfied: a file that looks complete because someone quietly stopped
        asking is the exact failure that module was written to prevent.
    """

    CORROBORATED = "CORROBORATED"                    # >=2 INDEPENDENT origins
    SINGLE_SOURCE = "SINGLE_SOURCE"                  # answered, one origin
    ATTEMPTED_INCONCLUSIVE = "ATTEMPTED_INCONCLUSIVE"  # we looked, no answer
    NOT_RUN = "NOT_RUN"                              # we did not look
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AWAITING_COUNTERPARTY = "AWAITING_COUNTERPARTY"
    WAIVED = "WAIVED"


#: Worst-first. This is the reader's queue ("what still needs me?") and the order the
#: summary counts in — the same convention as vetting.requirements.STATE_ORDER.
STATE_ORDER: dict[str, int] = {
    EvidenceState.NOT_RUN.value: 0,
    EvidenceState.ATTEMPTED_INCONCLUSIVE.value: 1,
    EvidenceState.AWAITING_COUNTERPARTY.value: 2,
    EvidenceState.SINGLE_SOURCE.value: 3,
    EvidenceState.WAIVED.value: 4,
    EvidenceState.NOT_APPLICABLE.value: 5,
    EvidenceState.CORROBORATED.value: 6,
}

#: States that count as ANSWERED for coverage. `SINGLE_SOURCE` answers the question but
#: does not corroborate it — the distinction drives the grade, not the coverage count.
_ANSWERED_STATES = frozenset({
    EvidenceState.CORROBORATED.value,
    EvidenceState.SINGLE_SOURCE.value,
})

#: States that are neither answered nor a failure to look — excluded from the denominator
#: so a person DD is not permanently capped by corporate questions (R-F3063), and a
#: SUPPLIED question is not counted as a gap ARIA failed to close.
_EXCLUDED_FROM_DENOMINATOR = frozenset({
    EvidenceState.NOT_APPLICABLE.value,
})


class Tier(str, Enum):
    SIMPLIFIED = "SIMPLIFIED"
    STANDARD = "STANDARD"
    ENHANCED = "ENHANCED"


#: Tier containment: a STANDARD run answers SIMPLIFIED + STANDARD questions.
_TIER_INCLUDES: dict[str, tuple[str, ...]] = {
    Tier.SIMPLIFIED.value: (Tier.SIMPLIFIED.value,),
    Tier.STANDARD.value: (Tier.SIMPLIFIED.value, Tier.STANDARD.value),
    Tier.ENHANCED.value: (Tier.SIMPLIFIED.value, Tier.STANDARD.value, Tier.ENHANCED.value),
}


class Cluster(str, Enum):
    """The five clusters of the Twenty Fundamentals."""
    EXISTENCE_IDENTITY = "EXISTENCE_IDENTITY"
    OWNERSHIP_CONTROL = "OWNERSHIP_CONTROL"
    FINANCIAL_STANDING = "FINANCIAL_STANDING"
    INTEGRITY_SCREENING = "INTEGRITY_SCREENING"
    LEGITIMACY_REGULATION = "LEGITIMACY_REGULATION"


#: The bridge that stops this being a second aggregator: each cluster rolls up to exactly
#: one existing `_dd_decision_readiness` question key. INTEGRITY_SCREENING carries two
#: readiness keys because readiness splits it that way; the mapping is explicit so the
#: relationship is auditable rather than assumed.
CLUSTER_TO_READINESS_KEY: dict[str, tuple[str, ...]] = {
    Cluster.EXISTENCE_IDENTITY.value: ("identity",),
    Cluster.OWNERSHIP_CONTROL.value: ("ownership_control",),
    Cluster.FINANCIAL_STANDING.value: ("financial_capacity",),
    Cluster.INTEGRITY_SCREENING.value: ("sanctions_export_control", "adverse_media"),
    Cluster.LEGITIMACY_REGULATION.value: (),   # no readiness question exists — the gap
}


class EstablishedBy(str, Enum):
    """The product boundary, on the schema rather than inferred.

    A DATA question ARIA is expected to answer. A SUPPLIED question she is expected to
    ASK FOR — reporting it as a gap misrepresents the product, and reporting it as
    answered misrepresents the evidence.
    """
    DATA = "DATA"
    SUPPLIED = "SUPPLIED"
    HYBRID = "HYBRID"


class AppliesTo(str, Enum):
    ENTITY = "ENTITY"
    INDIVIDUAL = "INDIVIDUAL"
    BOTH = "BOTH"


# ═══════════════════════════════════════════════════════════════════════════
# QUESTION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Resolution:
    """One question's terminal answer. `state` is never inferred from absence."""
    question_id: str
    state: str
    reason: str = ""
    #: What a reader should DO about a non-answered state. R-F3017 proved this pattern:
    #: "Companies House stores these as scanned documents" is actionable where a bare
    #: "unknown" is indistinguishable from "nothing was filed" and from "we never looked".
    remedy: str = ""
    #: Origins that answered. Independence is NOT counted here — that is
    #: dd_independent_verifier's job, and duplicating the rule would fork it.
    origins: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "state": self.state,
            "reason": self.reason,
            "remedy": self.remedy,
            "origins": list(self.origins),
        }


#: A reader inspects the persisted report dict and returns a Resolution. Returning
#: NOT_RUN is always legitimate; returning an answered state without having read a real
#: field is the defect this whole module exists to make impossible.
Reader = Callable[[dict, "Question"], Resolution]


@dataclass(frozen=True)
class Question:
    id: str
    #: 1-20 where this question refines one of the Twenty Fundamentals; the source
    #: document's own numbering, kept stable so it stays quotable to a customer.
    fundamental: int
    cluster: str
    tier: str
    applies_to: str
    established_by: str
    #: What must be established, in the words a reader would use.
    text: str
    #: The falsifiable condition. Prose, deliberately: it is what a customer reads when
    #: they ask "what would it take to answer this?", and it is what a reviewer checks
    #: the reader function against.
    pass_condition: str
    #: source_ids that MAY answer this question. Empty tuple = no resolver is built yet,
    #: which resolves to NOT_RUN with a named reason rather than silently passing.
    resolvers: tuple[str, ...] = ()
    #: Does the jurisdiction/country-risk overlay weigh on this row? The source document
    #: is explicit that jurisdiction "weighs several fundamentals rather than standing
    #: alone", so it is a property of the question, never a question of its own.
    jurisdiction_weighted: bool = False
    reader: Optional[Reader] = None

    def applicable_to(self, entity_type: str) -> bool:
        et = (entity_type or "").strip().lower()
        if self.applies_to == AppliesTo.BOTH.value:
            return True
        if et == "person":
            return self.applies_to == AppliesTo.INDIVIDUAL.value
        return self.applies_to == AppliesTo.ENTITY.value


# ═══════════════════════════════════════════════════════════════════════════
# READERS — each reads a REAL field. None certifies from an absence.
# ═══════════════════════════════════════════════════════════════════════════

def _m(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _not_run(q: "Question", reason: str, remedy: str = "") -> Resolution:
    return Resolution(q.id, EvidenceState.NOT_RUN.value, reason=reason, remedy=remedy)


def _unbuilt(q: "Question") -> Resolution:
    """No resolver is bound yet. This is the honest default and it must never be
    mistaken for 'checked and clear' — it is 'not checked, and here is why'."""
    return Resolution(
        q.id,
        EvidenceState.NOT_RUN.value,
        reason="no resolver is bound to this question in this build",
        remedy=f"bind a source to {q.id} (candidates: "
               f"{', '.join(q.resolvers) if q.resolvers else 'none identified'})",
    )


def _awaiting(q: "Question") -> Resolution:
    return Resolution(
        q.id,
        EvidenceState.AWAITING_COUNTERPARTY.value,
        reason="this question is answered by counterparty-supplied evidence, "
               "not by open data",
        remedy="request the document from the counterparty and attach it to the case",
    )


def _read_legal_existence(r: dict, q: "Question") -> Resolution:
    ident = _m(r.get("identity"))
    status = str(ident.get("registration_status") or "").strip()
    number = str(ident.get("registration_number") or "").strip()
    if not number and not status:
        return _not_run(
            q,
            "no registry record was established for this subject",
            "confirm the jurisdiction and registration number, then re-run",
        )
    # Registry-unavailable must NOT read as an answer (mirrors R-F2995: fields enriched
    # from OSINT/vault/GLEIF are not a registry verification).
    gaps = " ".join(str(g).lower() for g in (ident.get("data_gaps") or []))
    if "registry unavailable" in gaps or "r-f1636" in gaps or "not registry-verified" in gaps:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="identity was enriched from OSINT/vault because the registry was "
                   "unavailable on this run — the register was not consulted",
            remedy="re-run when the registry responds; do not rely on this row meanwhile",
        )
    if not status:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="a registration number was established but no company status",
            remedy="confirm active status directly with the register",
        )
    origins = ("registry",)
    if _m(ident.get("lei_registration")).get("lei"):
        origins = ("registry", "gleif")
    state = (EvidenceState.CORROBORATED.value if len(origins) >= 2
             else EvidenceState.SINGLE_SOURCE.value)
    return Resolution(q.id, state, reason=f"registry status: {status}", origins=origins)


def _read_officers(r: dict, q: "Question") -> Resolution:
    ident = _m(r.get("identity"))
    officers = [o for o in (ident.get("directors") or []) if o]
    if not officers:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="the registry returned no current officers",
            remedy="confirm on the register; an active company with no officers is itself a flag",
        )
    return Resolution(
        q.id, EvidenceState.SINGLE_SOURCE.value,
        reason=f"{len(officers)} current officer(s) from the register",
        origins=("registry",),
    )


def _read_officer_screen(r: dict, q: "Question") -> Resolution:
    """R-F3397 is what makes this question answerable at all: before it, officers
    discovered by the registry were never screened and nothing said so."""
    ident = _m(r.get("identity"))
    officers = [o for o in (ident.get("directors") or []) if o]
    if not officers:
        return _not_run(q, "no officers were established, so none could be screened",
                        "resolve the officer list first")
    findings = [f for f in (ident.get("findings") or []) if isinstance(f, dict)]
    screened = [f for f in findings if f.get("source") == "sanctions.director_screen"]
    gaps = [g for g in (ident.get("data_gaps") or [])
            if "officer sanctions screen" in str(g).lower()]
    if gaps and not screened:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=f"the officer screen ran but could not reach a list "
                   f"({len(gaps)} officer(s) unresolved)",
            remedy="re-screen when the sanctions source is available — this is not a clearance",
        )
    if not screened:
        return _not_run(
            q, "officers were established but no officer screen result is on the report",
            "re-run; if this persists the officer screen is not firing",
        )
    return Resolution(
        q.id, EvidenceState.SINGLE_SOURCE.value,
        reason=f"{len(screened)} officer screen result(s)",
        origins=("sanctions",),
    )


def _read_subject_sanctions(r: dict, q: "Question") -> Resolution:
    screen = _m(_m(r.get("identity")).get("sanctions_screen"))
    if not screen:
        return _not_run(q, "no sanctions screen is recorded on this report",
                        "re-run the screen")
    if screen.get("source_unavailable") or screen.get("error"):
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=str(screen.get("error") or "sanctions source unavailable"),
            remedy="re-screen when the source is available — an unperformed screen is "
                   "not a clearance",
        )
    verified = [s for s in (screen.get("verified_sources") or []) if s]
    if not verified:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="the screen returned no per-source verification, so list coverage "
                   "is not evidenced",
            remedy="re-screen and confirm which lists answered",
        )
    state = (EvidenceState.CORROBORATED.value if len(verified) >= 2
             else EvidenceState.SINGLE_SOURCE.value)
    return Resolution(
        q.id, state,
        reason=f"{len(verified)} list(s) answered"
               + (f" at {screen['screened_at']}" if screen.get("screened_at") else ""),
        origins=tuple(str(v) for v in verified[:8]),
    )


def _read_beneficial_ownership(r: dict, q: "Question") -> Resolution:
    ident = _m(r.get("identity"))
    network = _m(r.get("network"))
    holders = [h for h in (ident.get("shareholders") or []) if h]
    untraversed = [c for c in (network.get("controlled_by_unanchored") or [])
                   if isinstance(c, dict) and c.get("controller_name")]
    if untraversed:
        names = ", ".join(str(c.get("controller_name")) for c in untraversed[:2])
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=f"corporate controller {names} holds significant control but has no "
                   f"registration number to traverse",
            remedy="identify the controller's register and walk the chain to a natural person",
        )
    if not holders:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="no persons with significant control were returned",
            remedy="check the PSC exemption register — a lawful exemption and an empty "
                   "register are different answers",
        )
    return Resolution(
        q.id, EvidenceState.SINGLE_SOURCE.value,
        reason=f"{len(holders)} controller(s) from the register", origins=("registry",),
    )


def _read_financial_standing(r: dict, q: "Question") -> Resolution:
    fin = _m(_m(r.get("compliance")).get("financial_health"))
    verdict = str(fin.get("health_verdict") or "").upper()
    if fin.get("data_available") and verdict not in {"", "UNKNOWN", "UNAVAILABLE", "NOT_AVAILABLE"}:
        return Resolution(q.id, EvidenceState.SINGLE_SOURCE.value,
                          reason=f"health verdict: {verdict}", origins=("filed_accounts",))
    unavail = _m(fin.get("financial_figures_unavailable"))
    why = str(unavail.get("explanation") or "").strip()
    return Resolution(
        q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
        reason=why or "no machine-readable financial figures were obtained",
        remedy="request filed accounts from the counterparty, or a credit reference",
    )


def _read_regulatory_status(r: dict, q: "Question") -> Resolution:
    for section in ("identity", "compliance"):
        for f in (_m(r.get(section)).get("findings") or []):
            if isinstance(f, dict) and "fca_register" in str(f.get("source") or ""):
                return Resolution(
                    q.id, EvidenceState.SINGLE_SOURCE.value,
                    reason=str(f.get("title") or "FCA Register result"),
                    origins=("fca_register",),
                )
    return _not_run(
        q, "no regulator register was consulted for this subject",
        "consult the relevant sector regulator — absence of a check is not evidence of "
        "no authorisation requirement",
    )


# ═══════════════════════════════════════════════════════════════════════════
# THE CATALOGUE
# ═══════════════════════════════════════════════════════════════════════════

def _q(**kw) -> Question:
    return Question(**kw)


QUESTIONS: tuple[Question, ...] = (
    # ── Existence & identity ────────────────────────────────────────────────
    _q(id="EI-1", fundamental=1, cluster=Cluster.EXISTENCE_IDENTITY.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="The entity is registered and currently active",
       pass_condition="A company register returns a record whose status is live and "
                      "which was consulted on this run",
       resolvers=("companies_house", "registry_adapters", "gleif"),
       reader=_read_legal_existence),
    _q(id="EI-2", fundamental=2, cluster=Cluster.EXISTENCE_IDENTITY.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Exact legal name, number, legal form, date and jurisdiction of incorporation",
       pass_condition="Registry record carries name, number and incorporation date; a "
                      "second authority (LEI) corroborates the legal name",
       resolvers=("companies_house", "registry_adapters", "gleif"),
       reader=_read_legal_existence),
    _q(id="EI-3", fundamental=3, cluster=Cluster.EXISTENCE_IDENTITY.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.INDIVIDUAL.value,
       established_by=EstablishedBy.SUPPLIED.value,
       text="The individual is who they claim — verified to document and liveness",
       pass_condition="An identity document is verified and bound to a live capture",
       resolvers=(), reader=None),
    _q(id="EI-4", fundamental=4, cluster=Cluster.EXISTENCE_IDENTITY.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.HYBRID.value,
       text="Verified registered/trading address (entity) and residential address (individual)",
       pass_condition="Entity address from the register; individual address from a "
                      "document or bureau match",
       resolvers=("companies_house",), reader=None),

    # ── Ownership & control ─────────────────────────────────────────────────
    _q(id="OC-5", fundamental=5, cluster=Cluster.OWNERSHIP_CONTROL.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Natural persons ultimately owning or controlling, traced through the chain",
       pass_condition="Every controlling interest resolves to a NATURAL PERSON; a chain "
                      "that stops at a company does not pass",
       resolvers=("companies_house", "network_walker"),
       reader=_read_beneficial_ownership),
    _q(id="OC-6", fundamental=6, cluster=Cluster.OWNERSHIP_CONTROL.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Who legally runs and represents the entity",
       pass_condition="Current officers returned by the register",
       resolvers=("companies_house", "registry_adapters"), reader=_read_officers),
    _q(id="OC-7", fundamental=7, cluster=Cluster.OWNERSHIP_CONTROL.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Parent, subsidiaries, affiliates and ultimate parent",
       pass_condition="A relationship authority returns the direct and ultimate parent, "
                      "or states that none is recorded",
       resolvers=(),   # GLEIF Level 2 is not wired — /lei-records only
       reader=None),
    _q(id="OC-8", fundamental=8, cluster=Cluster.OWNERSHIP_CONTROL.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.HYBRID.value,
       text="The individual is mandated to bind the entity",
       pass_condition="The signatory appears as an officer, or a mandate/POA is on file",
       resolvers=("companies_house",), reader=None),

    # ── Financial standing ──────────────────────────────────────────────────
    _q(id="FS-9", fundamental=9, cluster=Cluster.FINANCIAL_STANDING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Statutory accounts and filings are current, not overdue or in default",
       pass_condition="Filing history shows accounts and confirmation statement within "
                      "their due dates",
       resolvers=("companies_house",), reader=None),
    _q(id="FS-10", fundamental=10, cluster=Cluster.FINANCIAL_STANDING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Creditworthiness, going concern and ability to perform",
       pass_condition="Structured figures yield a solvency signal, or the obstacle to "
                      "obtaining them is NAMED",
       resolvers=("sec_edgar", "companies_house"), reader=_read_financial_standing),
    _q(id="FS-11", fundamental=11, cluster=Cluster.FINANCIAL_STANDING.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Past or current insolvency of the entity or its principals",
       pass_condition="The insolvency register and the official gazette are both "
                      "consulted and either return notices or confirm none",
       resolvers=(),   # R-F3403 binds the Gazette; R-F3404 binds CH /insolvency
       jurisdiction_weighted=True, reader=None),
    _q(id="FS-12", fundamental=12, cluster=Cluster.FINANCIAL_STANDING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Existing security, liens or prior claims over the assets",
       pass_condition="The charges register is consulted and returns the outstanding "
                      "charge count with detail",
       resolvers=(),   # R-F3404 binds CH /charges
       reader=None),

    # ── Integrity screening ─────────────────────────────────────────────────
    _q(id="IS-13", fundamental=13, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="The subject is screened against sanctions and denied-party lists",
       pass_condition="At least one list ANSWERED and is named; an unreachable source "
                      "never passes",
       resolvers=("sanctions",), jurisdiction_weighted=True,
       reader=_read_subject_sanctions),
    _q(id="IS-13b", fundamental=13, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Every officer and controller is screened in their own name",
       pass_condition="Each current officer has a screen result or a named reason it "
                      "could not run",
       resolvers=("sanctions",), reader=_read_officer_screen),
    _q(id="IS-14", fundamental=14, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.ENHANCED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Politically exposed persons among the controllers or their close associates",
       pass_condition="A PEP dataset answers for the subject and for each controller",
       resolvers=("sanctions", "rca_screening"), jurisdiction_weighted=True, reader=None),
    _q(id="IS-15", fundamental=15, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.ENHANCED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Negative news, allegations and reputational red flags",
       pass_condition="A dedicated media sweep ran and a backend answered",
       resolvers=("web_search",), reader=None),
    _q(id="IS-16", fundamental=16, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.ENHANCED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.HYBRID.value,
       text="Fraud, bribery or financial-crime convictions, and regulatory penalties",
       pass_condition="Enforcement registers are consulted; a formal criminal-record "
                      "check is counterparty-supplied",
       resolvers=(), reader=None),
    _q(id="IS-16b", fundamental=16, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="No officer is a disqualified director",
       pass_condition="The disqualified-directors register is searched for each officer",
       resolvers=(),   # R-F3404 binds CH /search/disqualified-officers
       reader=None),
    _q(id="IS-17", fundamental=17, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Material claims and judgments, as claimant or defendant",
       pass_condition="A court-record source is searched by PARTY NAME and either "
                      "returns matters or confirms none",
       resolvers=("court_records",), reader=None),

    # ── Legitimacy & regulation ─────────────────────────────────────────────
    _q(id="LR-18", fundamental=18, cluster=Cluster.LEGITIMACY_REGULATION.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Authorised or licensed where required; any disciplinary action or bar",
       pass_condition="The relevant regulator register is consulted and its status quoted",
       resolvers=("fca_register",), reader=_read_regulatory_status),
    _q(id="LR-19", fundamental=19, cluster=Cluster.LEGITIMACY_REGULATION.value,
       tier=Tier.ENHANCED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.SUPPLIED.value,
       text="Origin of the money in the dealing and of the wealth behind it",
       pass_condition="Counterparty evidence is on file and corroborated",
       resolvers=(), reader=None),
    _q(id="LR-20", fundamental=20, cluster=Cluster.LEGITIMACY_REGULATION.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.HYBRID.value,
       text="What they do, and whether the proposed relationship is economically rational",
       pass_condition="Declared activity from the register is reconciled against the "
                      "web footprint and the proposed dealing",
       resolvers=("companies_house", "domain_ownership_verifier"), reader=None),
)

QUESTIONS_BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}


def questions_for(tier: str, entity_type: str = "company") -> list[Question]:
    """The catalogue slice a run of `tier` on `entity_type` must answer."""
    tiers = _TIER_INCLUDES.get((tier or "").strip().upper(), _TIER_INCLUDES[Tier.STANDARD.value])
    return [q for q in QUESTIONS if q.tier in tiers and q.applicable_to(entity_type)]


# ═══════════════════════════════════════════════════════════════════════════
# ASSESS — the checklist diff
# ═══════════════════════════════════════════════════════════════════════════

@fail_wire(module="dd_standard", gap_type="engine_failure")
def assess(report: dict, *, tier: str = "STANDARD") -> dict:
    """Resolve every applicable question against a persisted report dict.

    Pure. Never raises on a partial report: a reader that throws yields NOT_RUN with the
    exception named, because a reader crash is a failure to establish, never a pass.
    """
    report = report if isinstance(report, dict) else {}
    entity_type = str(_m(report.get("identity")).get("entity_type") or "company")
    applicable = questions_for(tier, entity_type)

    resolutions: list[Resolution] = []
    for q in applicable:
        # A question nobody asks of this subject type never reaches a reader.
        if not q.applicable_to(entity_type):
            resolutions.append(Resolution(
                q.id, EvidenceState.NOT_APPLICABLE.value,
                reason=f"not asked of a subject of type {entity_type!r}"))
            continue
        if q.established_by == EstablishedBy.SUPPLIED.value:
            resolutions.append(_awaiting(q))
            continue
        if q.reader is None:
            resolutions.append(_unbuilt(q))
            continue
        try:
            res = q.reader(report, q)
            resolutions.append(res if isinstance(res, Resolution) else _unbuilt(q))
        except Exception as exc:      # a crashed reader is not a pass
            resolutions.append(Resolution(
                q.id, EvidenceState.NOT_RUN.value,
                reason=f"reader raised {type(exc).__name__}: {str(exc)[:120]}",
                remedy="fix the reader; this row is unestablished, not clear"))

    by_id = {r.question_id: r for r in resolutions}
    denominator = [r for r in resolutions if r.state not in _EXCLUDED_FROM_DENOMINATOR]
    answered = [r for r in denominator if r.state in _ANSWERED_STATES]
    corroborated = [r for r in denominator if r.state == EvidenceState.CORROBORATED.value]

    clusters: dict[str, dict] = {}
    for q in applicable:
        c = clusters.setdefault(q.cluster, {"total": 0, "answered": 0, "open": []})
        r = by_id[q.id]
        if r.state in _EXCLUDED_FROM_DENOMINATOR:
            continue
        c["total"] += 1
        if r.state in _ANSWERED_STATES:
            c["answered"] += 1
        else:
            c["open"].append({"question_id": q.id, "state": r.state,
                              "reason": r.reason, "remedy": r.remedy})
    for c in clusters.values():
        c["complete"] = c["total"] > 0 and c["answered"] == c["total"]

    # The checklist diff: what the standard asks minus what the report answered.
    missing = sorted(
        (
            {"question_id": r.question_id,
             "fundamental": QUESTIONS_BY_ID[r.question_id].fundamental,
             "text": QUESTIONS_BY_ID[r.question_id].text,
             "state": r.state, "reason": r.reason, "remedy": r.remedy}
            for r in denominator if r.state not in _ANSWERED_STATES
        ),
        key=lambda d: (STATE_ORDER.get(d["state"], 0), d["question_id"]),
    )

    return {
        "standard_version": STANDARD_VERSION,
        "tier": (tier or "STANDARD").strip().upper(),
        "entity_type": entity_type,
        "required": len(denominator),
        "answered": len(answered),
        "corroborated": len(corroborated),
        "coverage_pct": round(100 * len(answered) / len(denominator), 1) if denominator else 0.0,
        "clusters": clusters,
        "resolutions": [r.as_dict() for r in resolutions],
        "missing": missing,
        "awaiting_counterparty": [
            r.question_id for r in resolutions
            if r.state == EvidenceState.AWAITING_COUNTERPARTY.value
        ],
        "scope_note": (
            "Coverage is a checklist diff against DD Standard "
            f"{STANDARD_VERSION}, not a risk verdict. An unanswered question is never "
            "a clean result."
        ),
    }
