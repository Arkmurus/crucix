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
from functools import lru_cache          # R-F3435 — _binding_present caches its probe
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
class Waiver:
    """R-F3406 — a named person decided not to pursue a question, and said why.

    THE DESIGN PROBLEM. The operator needs to scope a run — not every counterparty
    warrants a sanctions screen, and the OpenSanctions monthly quota is finite (it was
    EXHAUSTED on 2026-07-29: HTTP 429 on every screen). The obvious implementation is a
    tick box, and on a compliance product a tick box is dangerous: an unticked check
    produces a report with no sanctions section, which reads exactly like a report whose
    sanctions section found nothing. That is a false clean created by the UI.

    So opting out is modelled as a WAIVER, never a toggle, and the distinction is
    load-bearing in four places:

      1. It carries WHO and WHY. An auditor asking "why was this not screened?" gets a
         name and a reason, not silence. Same contract as
         `vetting.models.RequirementWaiver`, whose docstring states the rule this
         inherits: "the one thing that must not be possible here is a file that looks
         complete because someone quietly stopped asking."
      2. WAIVED stays IN the denominator. NOT_APPLICABLE leaves it (the question was
         never asked of this subject); WAIVED does not (the question applies and someone
         declined it). So coverage FALLS when you waive — which is the honest arithmetic.
      3. WAIVED is never `answered`, so it can never render as clean.
      4. Waiving a SIMPLIFIED-tier question sets `baseline_waived`, because declining a
         baseline check is a different statement from declining an enhanced one and
         belongs in the headline rather than a footnote.

    NEVER conflate a waiver with an outage. "We chose not to screen" and "the source was
    unreachable" have different remedies and different liability; the second is
    ATTEMPTED_INCONCLUSIVE and is set by the reader, never by this.
    """
    question_id: str
    waived_by: str
    reason: str
    waived_at: str = ""

    def is_valid(self) -> bool:
        """A waiver with no name or no reason is an anonymous opt-out — the thing this
        class exists to prevent. Invalid waivers are DISCARDED, so the question falls
        back to being genuinely assessed rather than silently dropped."""
        return bool(
            str(self.question_id or "").strip()
            and str(self.waived_by or "").strip()
            and str(self.reason or "").strip()
        )


@dataclass(frozen=True)
class Election:
    """R-F3408 — the operator explicitly ORDERED this check. The inverse of a Waiver.

    WHY IT IS A SEPARATE CONCEPT. The New DD form offers optional sections, some of them
    metered (a CCJ search is £6-£10 a time). Selecting one is a purchase, and the
    operator's requirement is exact: "once those selections are made the DD MUST search
    those, we cannot have issues."

    An election that silently does not run is WORSE than never offering the section: the
    buyer believes they have coverage they do not have, and the report contains no row
    saying otherwise. That is a false clean the customer PAID for.

    So an election creates an obligation the assessment reports on explicitly:

      * it PULLS THE QUESTION INTO SCOPE even when the tier would not include it —
        otherwise ticking "include CCJ search" on a Simplified run would do nothing at
        all, which is precisely the failure mode being guarded against;
      * it BEATS a waiver for the same question (you cannot both order and decline a
        check), and the contradiction is RECORDED rather than silently resolved;
      * if it does not end in an answered state the run is NOT honoured, and
        `elections_honoured` goes False so a caller can refuse to present the report as
        complete, retry, or refund — rather than shipping a gap the buyer cannot see.

    `unfulfilled` is further split by WHY, because the two failures have different
    owners: `no_adapter` is ours (we offered something we cannot deliver), while
    `source_failed` is the register's (tried, did not answer) and is retryable.
    """
    question_id: str
    elected_by: str = ""
    note: str = ""

    def is_valid(self) -> bool:
        return bool(str(self.question_id or "").strip())


def _coerce_elections(elections: Any) -> dict[str, Election]:
    """Accept Election objects, dicts, or bare question-id strings (the form path)."""
    out: dict[str, Election] = {}
    for e in (elections or []):
        if isinstance(e, Election):
            cand = e
        elif isinstance(e, str):
            cand = Election(question_id=e.strip())
        elif isinstance(e, dict):
            cand = Election(
                question_id=str(e.get("question_id") or "").strip(),
                elected_by=str(e.get("elected_by") or "").strip(),
                note=str(e.get("note") or "").strip(),
            )
        else:
            continue
        # An election naming a question that does not exist is a form/API bug, and
        # honouring it silently would let a typo look like a purchased check.
        if cand.is_valid() and cand.question_id in QUESTIONS_BY_ID:
            out[cand.question_id] = cand
    return out


def _coerce_waivers(waivers: Any) -> dict[str, Waiver]:
    """Accept Waiver objects or plain dicts (the API/JSON path). Silently ignoring a
    malformed waiver is correct: the question then gets assessed normally, which is the
    fail-safe direction."""
    out: dict[str, Waiver] = {}
    for w in (waivers or []):
        if isinstance(w, Waiver):
            cand = w
        elif isinstance(w, dict):
            cand = Waiver(
                question_id=str(w.get("question_id") or "").strip(),
                waived_by=str(w.get("waived_by") or "").strip(),
                reason=str(w.get("reason") or "").strip(),
                waived_at=str(w.get("waived_at") or "").strip(),
            )
        else:
            continue
        if cand.is_valid():
            out[cand.question_id] = cand
    return out


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
# RESOLVER REGISTRY — R-F3406
#
# A question names the sources that MAY answer it; this registry says what each source
# actually is. The split matters because the catalogue is deliberately written ahead of
# the adapters: a question can be wired now and bound to an API later, and until then it
# must report "not established, here is what would establish it" rather than either
# passing or looking like a capability we have.
#
# `access` is the commercial fact, and it is the operator's decision to take, not ours
# (§6 puts the burden of proof on any new third party; §17 caps spend). Every value below
# was PROBED on 2026-07-29, not read off documentation.
# ═══════════════════════════════════════════════════════════════════════════

class Access(str, Enum):
    FREE = "FREE"                          # no key, no cost
    KEYED_FREE = "KEYED_FREE"              # key required, no per-call cost
    PAID_PER_SEARCH = "PAID_PER_SEARCH"    # metered — needs explicit spend approval
    QUOTA_LIMITED = "QUOTA_LIMITED"        # keyed, finite monthly allowance
    LICENCE_REQUIRED = "LICENCE_REQUIRED"  # technically open, legally gated
    SUPPLIED = "SUPPLIED"                  # comes from the counterparty, not an API


@dataclass(frozen=True)
class ResolverSpec:
    id: str
    name: str
    #: DECLARED build state. Kept as the fallback for resolvers that have nothing to
    #: derive from (no adapter exists at all). `is_built()` is the authoritative answer
    #: — see R-F3435 below for why a hand-maintained flag cannot be trusted here.
    built: bool
    access: str
    endpoint: str = ""
    #: What it costs and what it constrains. Shown to the operator verbatim.
    note: str = ""
    #: R-F3435 — (module, attribute) that MUST exist for this resolver to be real.
    #: Presence of the binding is what `is_built()` measures. None = nothing to derive
    #: from, so the declaration stands (genuinely unbuilt sources like registry_trust).
    binding: Optional[tuple[str, str]] = None
    #: Environment variables that must be set for this resolver to be USABLE. Built and
    #: available are different questions: an adapter can exist and still have no key.
    env_vars: tuple[str, ...] = ()
    #: Where the operator goes to unblock this source (signup, licence application).
    decision_url: str = ""
    #: R-F3442 — (module, callable) that answers "is this usable?" itself. Needed when
    #: configuration is not a flat AND over env vars: Registry Trust is usable with EITHER
    #: a licensed dataset path OR a contracted API pair, and `env_vars` cannot express an
    #: OR. When set, this WINS over env_vars.
    configured_by: Optional[tuple[str, str]] = None

    def is_built(self) -> bool:
        """Is the adapter actually present in this build? DERIVED, never declared.

        R-F3435 — the declared flag drifted inside a single session: gazette, ch_charges,
        ch_insolvency, ch_disqualified and employment_tribunal were all still `built=False`
        here AFTER R-F3403/R-F3404/R-F3422/R-F3424 shipped and wired them into every DD.
        A pre-run selection screen reading the declaration would have told the operator
        that live, running sources did not exist — and the operator's requirement is that
        this screen be ACCURATE, so the flag has to measure something rather than assert it.
        """
        if not self.binding:
            return self.built
        module, attr = self.binding
        return _binding_present(module, attr)

    def availability(self) -> tuple[bool, str]:
        """(usable_now, reason). BUILT and AVAILABLE are deliberately separate.

        An adapter can be present and still unusable (no credential, no licence, no
        subscription). Reporting either one alone would mislead: "built" invites the
        operator to expect data, "unavailable" invites them to think it needs coding.
        """
        import os as _os

        if not self.is_built():
            if self.access == Access.PAID_PER_SEARCH.value:
                return (False, "no adapter and metered spend not approved")
            if self.access == Access.LICENCE_REQUIRED.value:
                return (False, "no adapter and the licence question is unanswered")
            return (False, "no adapter is bound in this build")
        if self.configured_by:
            mod, fn = self.configured_by
            try:
                import importlib
                probe = getattr(importlib.import_module(mod), fn, None)
                if probe is not None and not probe():
                    hint = getattr(importlib.import_module(mod), "configuration_hint", None)
                    return (False, hint() if callable(hint) else "not configured")
                if probe is not None:
                    return (True, "")
            except Exception as e:
                # A probe that cannot run must not certify the source as usable.
                return (False, f"availability probe failed: {type(e).__name__}")
        missing = [v for v in self.env_vars if not (_os.getenv(v) or "").strip()]
        if missing:
            return (False, f"credential not set: {', '.join(missing)}")
        if self.access == Access.SUPPLIED.value:
            return (False, "counterparty-supplied, not an API")
        return (True, "")


@lru_cache(maxsize=None)
def _binding_present(module: str, attr: str) -> bool:
    """Does `module.attr` exist? Import errors mean NOT built — never an exception.

    Uses find_spec first so a missing module costs no import. The attribute check needs
    a real import, which is why the result is cached: this runs per resolver per request
    on the scope-options endpoint.
    """
    import importlib
    import importlib.util

    try:
        if importlib.util.find_spec(module) is None:
            return False
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
    if not attr:
        return True
    try:
        return hasattr(importlib.import_module(module), attr)
    except Exception:
        return False


RESOLVERS: dict[str, ResolverSpec] = {
    "companies_house": ResolverSpec(
        "companies_house", "UK Companies House", built=True, access=Access.KEYED_FREE.value,
        endpoint="https://api.company-information.service.gov.uk",
        note="Free with a registered key, which is already deployed. Profile, officers, "
             "PSC, filing history and accounts are bound; /charges, /insolvency and "
             "/search/disqualified-officers are PROBED-WORKING but not yet bound.",
        binding=("aria_service.intel.companies_house", "is_enabled"),
        env_vars=("COMPANIES_HOUSE_API_KEY", )),
    "registry_adapters": ResolverSpec(
        "registry_adapters", "Non-UK company registers", built=True,
        access=Access.FREE.value, note="~30 jurisdiction adapters."),
    "gleif": ResolverSpec(
        "gleif", "GLEIF LEI", built=True, access=Access.FREE.value,
        endpoint="https://api.gleif.org/api/v1/lei-records",
        note="Level 1 only. Level 2 (direct/ultimate parent) is NOT bound, which is why "
             "OC-7 has no built resolver.",
        binding=("aria_service.intel.gleif", "search_lei")),
    "sanctions": ResolverSpec(
        "sanctions", "OpenSanctions consolidated screening", built=True,
        access=Access.QUOTA_LIMITED.value,
        note="Monthly allowance. VERIFIED EXHAUSTED 2026-07-29 (HTTP 429: 'This API key "
             "has exceeded its rate limit for the month'), which is the operational "
             "reason scope selection exists. See Waiver.",
        binding=("aria_service.intel.sanctions", "screen_with_aliases"),
        decision_url="https://www.opensanctions.org/api/"),
    "gazette": ResolverSpec(
        "gazette", "The Gazette (official public record)", built=True,
        access=Access.FREE.value,
        endpoint="https://www.thegazette.co.uk/{service}/notice/data.json",
        note="No key. service=insolvency constrains to Corporate Insolvency (24) and "
             "Personal Insolvency (25); free-text parameter is `text`. Probed live: "
             "returns winding-up resolutions and liquidator appointments by name.",
        binding=("aria_service.intel.sources.gazette", "search_all")),
    "ch_charges": ResolverSpec(
        "ch_charges", "Companies House charges register", built=True,
        access=Access.KEYED_FREE.value,
        endpoint="/company/{number}/charges",
        note="Probed live: HTTP 200 with total_count/unfiltered_count. Replaces the "
             "has_charges boolean, which cannot say what is secured over what.",
        binding=("aria_service.intel.companies_house", "get_charges"),
        env_vars=("COMPANIES_HOUSE_API_KEY", )),
    "ch_insolvency": ResolverSpec(
        "ch_insolvency", "Companies House insolvency register", built=True,
        access=Access.KEYED_FREE.value,
        endpoint="/company/{number}/insolvency",
        note="Probed live: returns 404 for a SOLVENT company. 404 means 'no cases', NOT "
             "an error. An adapter that treats it as a failure creates a false gap.",
        binding=("aria_service.intel.companies_house", "get_insolvency"),
        env_vars=("COMPANIES_HOUSE_API_KEY", )),
    "ch_disqualified": ResolverSpec(
        "ch_disqualified", "Companies House disqualified officers", built=True,
        access=Access.KEYED_FREE.value,
        endpoint="/search/disqualified-officers?q={name}",
        note="Probed live: 67 results for q=Smith. Free on the key we already hold, and "
             "never yet consulted by any DD.",
        binding=("aria_service.intel.companies_house", "search_disqualified_officers"),
        env_vars=("COMPANIES_HOUSE_API_KEY", )),
    "find_case_law": ResolverSpec(
        "find_case_law", "Find Case Law (The National Archives)", built=True,
        access=Access.LICENCE_REQUIRED.value,
        binding=("aria_service.intel.sources.find_case_law", "search_by_party"),
        configured_by=("aria_service.intel.sources.find_case_law", "is_configured"),
        endpoint="https://caselaw.nationalarchives.gov.uk/atom.xml?party={name}",
        note="Free, no key, 1000 requests / 5 minutes per IP, and `party` is a full-match "
             "party search. BUT the Open Justice Licence forbids computational analysis "
             "without a separate application. This is an operator legal decision before binding.",
        env_vars=("FIND_CASE_LAW_LICENCE_GRANTED", ),
        decision_url="https://caselaw.nationalarchives.gov.uk/open-justice-licence"),
    "court_records": ResolverSpec(
        "court_records", "CourtListener + BAILII", built=True, access=Access.FREE.value,
        note="US federal via CourtListener; UK via a BAILII RSS proxy.",
        binding=("aria_service.intel.sources.court_records", "search_all")),
    "registry_trust": ResolverSpec(
        "registry_trust", "Registry Trust (Register of Judgments, Orders and Fines)",
        built=True,
        access=Access.PAID_PER_SEARCH.value,
        binding=("aria_service.intel.sources.registry_trust", "search_judgments"),
        configured_by=("aria_service.intel.sources.registry_trust", "is_configured"),
        note="The ONLY authoritative register of County Court Judgments, for BOTH "
             "companies and individuals. £6 for one part of the register, up to £10 for "
             "all England & Wales plus the other registers; no free tier and no public "
             "API. A search leaves no footprint and needs no subject consent. Metered "
             "spend, and requires explicit operator approval before binding (§6/§17).",
        env_vars=("REGISTRY_TRUST_API_KEY", ),
        decision_url="https://www.trustonline.org.uk/"),
    "employment_tribunal": ResolverSpec(
        "employment_tribunal", "UK Employment Tribunal decisions", built=True,
        access=Access.FREE.value,
        endpoint="https://www.gov.uk/api/search.json?filter_format="
                 "employment_tribunal_decision&q={name}",
        note="No key. Probed live: 503 decisions for q=Mitie, each with the case number "
             "and both parties in the title. Directly answers employment litigation "
             "exposure for UK employers.",
        binding=("aria_service.intel.sources.employment_tribunal", "search_decisions")),
    "network_walker": ResolverSpec(
        "network_walker", "UBO / officer chain traversal", built=True,
        access=Access.KEYED_FREE.value,
        note="walk_network() traverses registry-anchored control edges. Rides whatever "
             "register answers, so its reach is the register's reach. An unanchored "
             "corporate controller cannot be walked (R-F3027).",
        binding=("aria_service.intel.network_walker", "walk_network"),
        env_vars=("COMPANIES_HOUSE_API_KEY", )),
    "web_search": ResolverSpec(
        "web_search", "Multi-backend media search", built=True, access=Access.FREE.value,
        binding=("aria_service.intel.web_search", "search")),
    "rca_screening": ResolverSpec(
        "rca_screening", "PEP relatives and close associates", built=True,
        access=Access.QUOTA_LIMITED.value, note="Rides the same OpenSanctions allowance.",
        binding=("aria_service.intel.rca_screening", "screen_with_relatives")),
    "sec_edgar": ResolverSpec(
        "sec_edgar", "SEC EDGAR XBRL", built=True, access=Access.FREE.value,
        binding=("aria_service.intel.sources.sec_edgar", "lookup")),
    "fca_register": ResolverSpec(
        "fca_register", "FCA Register + Directory", built=True, access=Access.KEYED_FREE.value,
        binding=("aria_service.intel.fca_register", "lookup_firm")),
    "domain_ownership_verifier": ResolverSpec(
        "domain_ownership_verifier", "RDAP domain ownership", built=True,
        access=Access.FREE.value,
        binding=("aria_service.intel.domain_ownership_verifier", "verify_domain")),
    "idv": ResolverSpec(
        "idv", "Identity verification / document collection", built=False,
        access=Access.SUPPLIED.value,
        note="The product boundary: identity, authority to act, criminal-record checks "
             "and source of funds are counterparty-supplied, not open data."),
}


def resolver_status(question: "Question") -> dict:
    """What would it take to answer this question? Separates 'no adapter yet' from
    'no source exists', because they are different asks of the operator."""
    specs = [RESOLVERS[r] for r in question.resolvers if r in RESOLVERS]
    # R-F3435 — is_built() is DERIVED from the adapter's presence. Reading the declared
    # `.built` here is what let five live sources report as unbuilt for a whole session.
    return {
        "declared": [s.id for s in specs],
        "built": [s.id for s in specs if s.is_built()],
        "unbuilt": [s.id for s in specs if not s.is_built()],
        # R-F3442 — keyed on AVAILABILITY, not on build state. This previously read
        # `not is_built()`, which meant that building the Registry Trust adapter made the
        # CCJ question stop reporting as blocked on a commercial decision — even though
        # nothing about the spend decision had changed. "No adapter" and "no contract" are
        # different blocks with different owners, and only the second one is what this
        # field is for: a built-but-unpaid source is still blocked, and the form and the
        # report both need to say so.
        "blocked_on": sorted({
            s.access for s in specs
            if not s.availability()[0] and s.access in (Access.PAID_PER_SEARCH.value,
                                                        Access.LICENCE_REQUIRED.value)
        }),
    }


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
                   "unavailable on this run. The register was not consulted",
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
            remedy="re-screen when the sanctions source is available. This is not a clearance",
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
            remedy="re-screen when the source is available. An unperformed screen is "
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
            remedy="check the PSC exemption register. A lawful exemption and an empty "
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


# ── R-F3426 — read the evidence the DD now actually gathers ─────────────────
#
# R-F3422/R-F3403/R-F3424 wired four registers into the run: Companies House charges,
# CH insolvency, CH disqualified officers, The Gazette (corporate AND personal
# insolvency) and the employment tribunals. The findings land in the report — and the
# checklist still said NOT_RUN for every one of them, because these questions were
# declared with `reader=None`.
#
# That is a CONTRADICTION BETWEEN TWO SURFACES of the same report: an insolvency finding
# in the body beside "insolvency: not run" in the scorecard. It is the same shape as the
# defects this whole session has been closing (the officer screen, the discipline proxy,
# the layer-health proxy), and it is worse here because the checklist is the thing a
# customer is told to rely on.
#
# Each reader below resolves from a REAL field — a finding with a known `source`, or a
# data gap naming the register — and never from the mere fact that a layer ran.

def _findings_from(r: dict, *sources: str) -> list[dict]:
    """Every finding in the report emitted by any of `sources`."""
    out: list[dict] = []
    for section in ("identity", "network", "compliance", "digital"):
        for f in (_mapping_of(r, section).get("findings") or []):
            if isinstance(f, dict) and str(f.get("source") or "") in sources:
                out.append(f)
    return out


def _mapping_of(r: dict, key: str) -> dict:
    v = (r or {}).get(key)
    return v if isinstance(v, dict) else {}


def _gap_mentions(r: dict, *needles: str) -> bool:
    """True when a data gap names one of `needles` — i.e. the register was reached for
    and could not answer. That is ATTEMPTED_INCONCLUSIVE, never NOT_RUN, and never a
    pass."""
    for section in ("identity", "network", "compliance", "digital"):
        for g in (_mapping_of(r, section).get("data_gaps") or []):
            low = str(g).lower()
            if any(n.lower() in low for n in needles):
                return True
    return False


def _register_reader(*, sources: tuple[str, ...], gap_needles: tuple[str, ...],
                     remedy: str, unavailable_needles: tuple[str, ...] = ()) -> Reader:
    """Build a reader for a question answered by one or more registers.

    ANSWERED means a register produced a finding — including a finding that says
    'nothing on file', because an empty register is a real answer. A data gap naming the
    register means it was tried and did not answer. Neither present means nothing looked.

    R-F3447 — `unavailable_needles` separates NEVER ATTEMPTED from ATTEMPTED AND FAILED.
    Found by a live DD: an elected CCJ search came back `source_failed` with the detail
    "the source was searched and did not answer" when there is no Registry Trust contract,
    so the register was never contacted at all. The two are not interchangeable —
    `source_failed` is the register's fault and tells the reader to RETRY, while an
    unconfigured source is ours and retrying changes nothing until a contract exists.
    Telling a buyer a judgment register "did not answer" when nobody asked it is the same
    class of dishonesty as a clean line on an unsearched register.

    Gaps matching these needles resolve to NOT_RUN, which for an ELECTED question is what
    R-F3408 renders as "ORDERED BUT NOT SEARCHED — must not be presented as covered or
    charged for". Checked BEFORE `gap_needles`, since an unavailability gap usually names
    the register too and would otherwise be swallowed by the generic branch.
    """
    def _read(r: dict, q: "Question") -> Resolution:
        hits = _findings_from(r, *sources)
        if hits:
            origins = tuple(sorted({str(h.get("source") or "") for h in hits}))
            state = (EvidenceState.CORROBORATED.value if len(origins) >= 2
                     else EvidenceState.SINGLE_SOURCE.value)
            return Resolution(
                q.id, state,
                reason="; ".join(str(h.get("title") or "")[:70] for h in hits[:2]),
                origins=origins,
            )
        if unavailable_needles and _gap_mentions(r, *unavailable_needles):
            return Resolution(
                q.id, EvidenceState.NOT_RUN.value,
                reason="the register was NOT searched: no configured backend for it",
                remedy=remedy,
            )
        if _gap_mentions(r, *gap_needles):
            return Resolution(
                q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
                reason="the register was searched and did not answer",
                remedy=remedy,
            )
        return Resolution(
            q.id, EvidenceState.NOT_RUN.value,
            reason="no register result is on this report",
            remedy=remedy,
        )
    return _read



def _sweep_count(value: object) -> int:
    """A count from the sweep record, or 0 when it is not a usable number.

    Defensive on purpose: every branch that consumes these decides whether a
    CLEAN LINE is allowed, so a malformed record must degrade to "nothing was
    established", never raise past the caller or coerce into a pass.
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _read_adverse_media(r: dict, q: "Question") -> Resolution:
    """R-F4277 (C-235) — IS-15 is answered by the sweep the DD already runs.

    Until this reader existed IS-15 took the `_unbuilt` branch and rendered
    NOT_RUN "no resolver is bound to this question in this build", while the
    adverse-media sweep ran on the same report, cost real search spend, and wrote
    its result to `report.adverse_media`. `coverage_pct` is computed over these
    resolutions, so the customer's report understated what had been established —
    on the discipline most likely to carry the finding they are paying for.

    THE ONLY DANGEROUS DIRECTION IS A FABRICATED CLEAN LINE, so the fields this
    reads are not a matter of taste. **R-F2791 established that `templates_run`
    alone certified sweeps in which every backend call failed** and named
    `templates_searched` + `search_backends_answered` as the pair a consumer must
    read. IS-15's own `pass_condition` — "A dedicated media sweep ran AND a
    backend answered" — is written in exactly those terms, so this reader is
    implementing a contract the standard already stated rather than inventing one.

    The states, worst first, and each is a different sentence to the reader:

      * no `adverse_media` block            -> NOT_RUN, nobody swept
      * `error` / `ok: False`               -> ATTEMPTED_INCONCLUSIVE, the sweep failed
      * `status: in_progress`               -> ATTEMPTED_INCONCLUSIVE (R-F2657 defers
        the sweep to a follow-up; a restart can leave it unfinished forever, and
        an unfinished sweep is not a clean one)
      * searched or answered < 1            -> NOT_RUN, templates were entered but
        nothing actually answered (the R-F2791 case)
      * findings present                    -> answered; CORROBORATED on >=2 distinct
        origin domains, else SINGLE_SOURCE
      * `partial`/`timed_out` and NOTHING found -> ATTEMPTED_INCONCLUSIVE. Findings
        from a truncated sweep are real and are reported; its SILENCE is not.
        "We ran out of time before finding anything" is not "there is nothing".
      * swept, backends answered, nothing found -> SINGLE_SOURCE

    A clean sweep is SINGLE_SOURCE however many backends answered. Two backends
    returning nothing is ONE observation of absence, not two independent origins;
    calling it CORROBORATED would overstate negative evidence, and CORROBORATED
    is reserved for ">=2 INDEPENDENT origins" that actually said something.
    """
    am = (r or {}).get("adverse_media")
    if not isinstance(am, dict) or not am:
        return Resolution(
            q.id, EvidenceState.NOT_RUN.value,
            reason="no adverse-media sweep is on this report",
            remedy="run the adverse-media discipline for this subject")

    error = str(am.get("error") or "").strip()
    if error or am.get("ok") is False:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=f"the adverse-media sweep failed: {error[:110]}"
                   if error else "the adverse-media sweep did not complete",
            remedy="re-run the adverse-media sweep; this is not a clear result")

    if str(am.get("status") or "").strip().lower() == "in_progress":
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="the adverse-media sweep was deferred to a follow-up and has "
                   "not completed",
            remedy="wait for the follow-up to merge, or re-run it; an unfinished "
                   "sweep is not a clear result")

    searched = _sweep_count(am.get("templates_searched"))
    answered = _sweep_count(am.get("search_backends_answered"))
    findings = [f for f in (am.get("findings") or []) if isinstance(f, dict)]

    if searched < 1 or answered < 1:
        entered = _sweep_count(am.get("templates_run"))
        return Resolution(
            q.id, EvidenceState.NOT_RUN.value,
            reason=(f"the sweep entered {entered} template(s) but no search "
                    f"backend answered — nothing was actually screened"),
            remedy="re-run the adverse-media sweep once a search backend is "
                   "available; entered templates are not a screen (R-F2791)")

    if findings:
        origins = tuple(sorted({_origin_of(f) for f in findings if _origin_of(f)}))
        state = (EvidenceState.CORROBORATED.value if len(origins) >= 2
                 else EvidenceState.SINGLE_SOURCE.value)
        return Resolution(
            q.id, state,
            reason="; ".join(str(f.get("title") or "")[:70] for f in findings[:2]),
            origins=origins)

    if am.get("partial") or am.get("timed_out"):
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=(f"the sweep was stopped by its deadline after "
                    f"{searched} template(s) and found nothing — a truncated "
                    f"sweep's silence is not evidence of absence"),
            remedy="re-run the adverse-media sweep to completion before relying "
                   "on a clean result")

    return Resolution(
        q.id, EvidenceState.SINGLE_SOURCE.value,
        reason=(f"a dedicated media sweep ran across {searched} template(s), "
                f"{answered} search backend(s) answered, and no credible adverse "
                f"coverage was returned"),
        origins=("adverse_media_sweep",))


def _origin_of(finding: dict) -> str:
    """The outlet a finding came from — the unit of independence for IS-15."""
    from urllib.parse import urlsplit
    url = str(finding.get("source_url") or finding.get("url") or "").strip()
    if not url:
        return str(finding.get("source_class") or "").strip()
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host



def _pep_origins(hits: list, inherited: list) -> tuple:
    """The datasets that actually flagged someone. Independence, not volume."""
    out = set()
    for hit in hits:
        for match in (hit.get("matches") or []):
            if isinstance(match, dict):
                name = str(match.get("dataset") or match.get("list")
                           or match.get("source") or "").strip()
                if name:
                    out.add(name)
    for risk in inherited:
        for name in (risk.get("relative_lists") or []):
            if str(name).strip():
                out.add(str(name).strip())
    return tuple(sorted(out))


def _read_pep_exposure(r: dict, q: "Question") -> Resolution:
    """R-F4279 (C-238) — IS-14 is answered by the PEP and RCA screens already run.

    Same shape as C-235 one question along: IS-14 rendered NOT_RUN "no resolver is
    bound to this question in this build" while TWO screens ran on the same report
    and spent real budget —

      * the network layer screens every ENUMERATED OFFICER and promotes a
        `role.pep` / `role.pol` topic hit into `network.pep_connections`
        (network_walker:313 via `_sanctions_classify.classify_matches`);
      * `rca_screening.screen_with_relatives` runs in `deterministic_primitives`
        and writes `report.rca_relatives` (dd_orchestrator:16626).

    IS-14 NAMES TWO POPULATIONS — "politically exposed persons among the
    controllers OR THEIR CLOSE ASSOCIATES" — so a clean line needs BOTH screens to
    have run. One of them is honest partial evidence, never a clearance of the
    whole question.

    THE POPULATION TRAP, and it is the reason this reader is not simply
    "pep_connections is empty". The officer screen only screens officers that were
    ENUMERATED. A DD whose identity resolution failed has no officer list, so
    `pep_connections` is empty for a reason that has nothing to do with PEP
    status — and reading that as clean would clear a population nobody assembled.
    That is C-39 applied to people. `identity.directors` must be non-empty AND the
    network layer must have reached `ok` before an absence of hits means anything.

    A FINDING always answers, even when the other half never ran: a PEP that WAS
    found is evidence regardless of how much of the sweep completed.
    """
    network = _m(r.get("network"))
    ident = _m(r.get("identity"))
    rca = r.get("rca_relatives")
    rca = rca if isinstance(rca, dict) else {}

    hits = [h for h in (network.get("pep_connections") or []) if isinstance(h, dict)]
    inherited = [x for x in (rca.get("inherited_risks") or []) if isinstance(x, dict)]

    if hits or inherited:
        origins = _pep_origins(hits, inherited)
        parts = []
        if hits:
            named = ", ".join(str(h.get("name") or "?") for h in hits[:2])
            parts.append(f"{len(hits)} controller(s) flagged: {named}")
        if inherited:
            via = ", ".join(str(x.get("relationship") or "relative")
                            for x in inherited[:2])
            parts.append(f"{len(inherited)} inherited risk(s) via relative ({via})")
        return Resolution(
            q.id,
            EvidenceState.CORROBORATED.value if len(origins) >= 2
            else EvidenceState.SINGLE_SOURCE.value,
            reason="; ".join(parts), origins=origins or ("pep_screen",))

    controllers = [d for d in (ident.get("directors") or []) if d]
    status = str(_m(network.get("meta")).get("status") or "").strip().lower()
    controllers_screened = bool(controllers) and status == "ok"

    if not controllers_screened:
        why = ("no controller was enumerated, so nobody was screened"
               if not controllers else
               f"the network layer did not complete (status: {status or 'unknown'}), "
               f"so the controllers were not screened")
        return Resolution(
            q.id, EvidenceState.NOT_RUN.value,
            reason=f"PEP exposure of the controllers is unestablished: {why}",
            remedy="resolve the entity's officers and re-run the network screen; "
                   "an empty flag list over an empty population is not a clear result")

    if rca.get("source_unavailable") is True or rca.get("ok") is False:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="the controllers were screened, but the close-associate (RCA) "
                   "screen could not reach its source, so inherited exposure is "
                   "UNVERIFIED, not absent",
            remedy="re-run the relatives screen once the sanctions source answers")

    relatives_screened = _sweep_count(rca.get("relatives_screened"))
    if relatives_screened < 1:
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason="the controllers were screened and none is politically exposed, "
                   "but no close associate (relative) was screened, so half of the "
                   "question is unanswered",
            remedy="run rca_screening.screen_with_relatives for the controllers")

    return Resolution(
        q.id, EvidenceState.SINGLE_SOURCE.value,
        reason=(f"{len(controllers)} controller(s) screened and "
                f"{relatives_screened} close associate(s) screened; no politically "
                f"exposed person returned"),
        origins=("pep_screen",))



def _read_filing_currency(r: dict, q: "Question") -> Resolution:
    """R-F4290 (C-244) — FS-9 is answered by the filing evidence already gathered.

    Third instance of the C-235 shape. FS-9 rendered NOT_RUN "no resolver is
    bound to this question in this build" while `financial_health.
    _uk_registry_accounts` fetched exactly this evidence on every GB run and
    parked it at `compliance.financial_health.registry_accounts`.

    WHY THE EVIDENCE WAS SITTING UNUSED, and why using it here is not the thing
    that function forbids. Its docstring is emphatic: "THIS IS EVIDENCE, NOT A
    VERDICT ... answering financial capacity from filing dates would be a false
    clean." That is the FS-10 boundary and it is correct — filing metadata
    carries no revenue or solvency figures. **FS-9 is the question filing dates
    DO answer**: "statutory accounts and filings are current, not overdue or in
    default." FS-10 is untouched and still refuses this evidence.

    FS-9 NAMES TWO FILINGS — accounts AND the confirmation statement — so the
    IS-14 rule applies: a FINDING always answers, but a clean line needs both
    halves. An overdue accounts filing is a definitive adverse answer on its own;
    current accounts with an UNKNOWN confirmation statement is half a question,
    and `_confirmation_block.known` exists so "not overdue" is never mistaken for
    "we have no idea".
    """
    reg = _m(_m(_m(r.get("compliance")).get("financial_health")).get("registry_accounts"))
    accounts = _m(reg.get("accounts"))
    if not reg or not accounts:
        return Resolution(
            q.id, EvidenceState.NOT_RUN.value,
            reason="no registry filing evidence is on this report",
            remedy="run the Companies House profile lookup for this subject")

    confirmation = _m(reg.get("confirmation_statement"))
    flags = [f for f in (accounts.get("distress_flags") or []) if isinstance(f, str)]
    cite = ("companies_house",)

    adverse: list[str] = []
    if accounts.get("overdue") or "accounts_overdue" in flags:
        adverse.append("statutory accounts are OVERDUE")
    if "no_accounts_filed" in flags or not accounts.get("filed"):
        adverse.append("no accounts have ever been filed")
    if confirmation.get("overdue"):
        adverse.append("the confirmation statement is OVERDUE")

    if adverse:
        return Resolution(
            q.id, EvidenceState.SINGLE_SOURCE.value,
            reason="; ".join(adverse) + " (a standard early-distress signal)",
            origins=cite)

    # Nothing adverse. A clean line needs the SECOND filing to be known too.
    if not confirmation.get("known"):
        made_up = accounts.get("last_made_up_to") or "an unstated date"
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=(f"accounts are filed and not overdue (made up to {made_up}), "
                    f"but the confirmation statement's status was not returned, so "
                    f"half of this question is unanswered"),
            remedy="re-read the Companies House profile; its confirmation "
                   "statement block carries the due date and overdue flag")

    made_up = accounts.get("last_made_up_to") or "an unstated date"
    return Resolution(
        q.id, EvidenceState.SINGLE_SOURCE.value,
        reason=(f"accounts made up to {made_up} and the confirmation statement "
                f"are both filed and within their due dates"),
        origins=cite)



#: The primary-list label the DD's fan-out uses for World Bank debarment
#: (dd_orchestrator._src_labels). Kept as a constant so a rename shows up here as
#: a NOT_RUN rather than as a silent clean.
_DEBARMENT_LABEL = "wb_debarred"
_DEBARMENT_SOURCE = "sources.worldbank_debarred"


def _read_enforcement_actions(r: dict, q: "Question") -> Resolution:
    """R-F4291 (C-245) — IS-16 from the debarment register already consulted.

    Fourth instance of the C-235 shape and the first HYBRID one. IS-16 rendered
    NOT_RUN "no resolver is bound to this question in this build" while the DD's
    primary-source fan-out called `sources.worldbank_debarred` on every run and
    R-F2843 recorded whether that list answered, at
    `identity.sanctions_screen.primary_snapshots`.

    World Bank debarment is squarely in scope: an enforcement action for fraud,
    corruption or collusion, cross-recognised by AfDB/AsDB/EBRD/IDB under MCEA
    2010.

    THE HYBRID HALF IS THE POINT, and it is why a clean register is NOT a pass.
    IS-16's pass condition is "Enforcement registers are consulted; **a formal
    criminal-record check is counterparty-supplied**". Clearing the open-source
    half and calling the whole question answered would be a false clean on the
    most consequential integrity question in the set. A clean register therefore
    resolves AWAITING_COUNTERPARTY — a stated boundary, not a failure to look,
    which is exactly what that state exists for. An ADVERSE finding answers on
    its own: a debarment is a refusal ground whatever the counterparty supplies.

    R-F2843's rule is what makes reading the snapshot safe: "an unstamped source
    asserts nothing, because defaulting to 'ok' would claim a check we never
    made." A snapshot dict that never mentions the register is NOT_RUN.
    """
    hits = _findings_from(r, _DEBARMENT_SOURCE)
    if hits:
        titles = "; ".join(str(h.get("title") or "")[:70] for h in hits[:2])
        return Resolution(
            q.id, EvidenceState.SINGLE_SOURCE.value,
            reason=titles or "a debarment record was returned",
            origins=(_DEBARMENT_SOURCE,))

    screen = _m(_m(r.get("identity")).get("sanctions_screen"))
    snapshots = _m(screen.get("primary_snapshots"))
    stamp = snapshots.get(_DEBARMENT_LABEL)
    if not isinstance(stamp, str) or not stamp:
        return Resolution(
            q.id, EvidenceState.NOT_RUN.value,
            reason="the debarment register is not stamped on this report, so no "
                   "enforcement register is evidenced as consulted",
            remedy="re-run the primary-source screen; an unstamped source asserts "
                   "nothing (R-F2843)")
    if stamp != "ok":
        return Resolution(
            q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
            reason=f"the World Bank debarment register was reached for but did "
                   f"not answer ({stamp})",
            remedy="re-screen when the register is available. An unperformed "
                   "check is not a clearance")

    return Resolution(
        q.id, EvidenceState.AWAITING_COUNTERPARTY.value,
        reason=("the World Bank debarment register was consulted and returned no "
                "enforcement action; a formal criminal-record check is "
                "counterparty-supplied and is not on file"),
        remedy="obtain a criminal-record check from the counterparty; the "
               "open-source half of this question is clear",
        origins=(_DEBARMENT_SOURCE,))


_read_insolvency = _register_reader(
    sources=("companies_house.insolvency", "gazette.corporate_insolvency",
             "gazette.personal_insolvency"),
    gap_needles=("insolvency register", "gazette corporate", "personal-insolvency"),
    remedy="re-run when the insolvency registers respond. An unsearched register is "
           "not a clean one",
)

_read_charges = _register_reader(
    sources=("companies_house.charges",),
    gap_needles=("charges register",),
    remedy="re-run when the charges register responds; without it there is no view of "
           "security over the assets",
)

_read_disqualification = _register_reader(
    sources=("companies_house.disqualified_officers",),
    gap_needles=("disqualification check",),
    # A silent pass is the hazard here: the register answering "nothing for this name"
    # produces NO finding by design (R-F3422 stays quiet on a clean check), so absence
    # of a finding cannot mean absence of a check.
    remedy="re-run the disqualified-directors search for each serving officer",
)

_read_tribunal = _register_reader(
    sources=("employment_tribunal.decisions",),
    gap_needles=("employment tribunal",),
    remedy="re-run when gov.uk responds. There is no view of claims against this employer",
)

_read_ccj = _register_reader(
    sources=("registry_trust.ccj",),
    gap_needles=("ccj search", "ccj register", "county court judgment"),
    # R-F3447 — the phrases _run_ccj_search and _preflight_elections emit when the register
    # was never contacted. Without these, a LIVE DD reported the CCJ question as
    # "searched and did not answer" while no Registry Trust contract exists.
    unavailable_needles=("could not run", "cannot be searched", "not configured"),
    # R-F3442 — this question is ORDERED-ONLY, so NOT_RUN is the correct and common
    # answer, not a defect. What must never happen is NOT_RUN reading as clean: IS-17b's
    # own pass condition says a CCJ's ABSENCE is a material finding, so an unsearched
    # register cannot certify anything.
    remedy="select the CCJ check on the DD form to order it; it needs a licensed "
           "Registry Trust backend (REGISTRY_TRUST_DATA_PATH, or the contracted API pair)",
)


def _read_court_judgments(r: dict, q: "Question") -> Resolution:
    """IS-17a — reported judgments. Distinct from the tribunal reader because the
    sources are different bodies with different coverage, and conflating them would let
    a tribunal result answer a question about the courts."""
    # R-F3442 — find_case_law added. IS-17a declares it as a resolver, so a reader that
    # cannot see its findings would leave the question NOT_RUN even after the search
    # succeeded: the producer/consumer break this codebase has hit repeatedly.
    # R-F3442 — find_case_law added. NOTE `_findings_from` matches the source string
    # EXACTLY, so the emitted label "find_case_law.judgments" must be listed in full; the
    # bare module name silently matches nothing, which would leave IS-17a NOT_RUN after a
    # successful search — the producer/consumer break, one layer down.
    hits = _findings_from(r, "court_records", "courtlistener", "bailii",
                          "find_case_law.judgments")
    if hits:
        return Resolution(q.id, EvidenceState.SINGLE_SOURCE.value,
                          reason=str(hits[0].get("title") or "")[:80],
                          origins=("court_records",))
    if _gap_mentions(r, "court record", "courtlistener", "bailii"):
        return Resolution(q.id, EvidenceState.ATTEMPTED_INCONCLUSIVE.value,
                          reason="the court-record search did not answer",
                          remedy="re-run the judgment search")
    return Resolution(q.id, EvidenceState.NOT_RUN.value,
                      reason="no judgment search result is on this report",
                      remedy="search a judgment database by PARTY NAME")


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
       text="The individual is who they claim, verified to document and liveness",
       pass_condition="An identity document is verified and bound to a live capture",
       resolvers=("idv",), reader=None),
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
       resolvers=("gleif",),
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
       resolvers=("companies_house",), reader=_read_filing_currency),
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
       resolvers=("gazette", "ch_insolvency"),
       jurisdiction_weighted=True, reader=_read_insolvency),
    _q(id="FS-12", fundamental=12, cluster=Cluster.FINANCIAL_STANDING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Existing security, liens or prior claims over the assets",
       pass_condition="The charges register is consulted and returns the outstanding "
                      "charge count with detail",
       resolvers=("ch_charges",),
       reader=_read_charges),

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
       resolvers=("sanctions", "rca_screening"), jurisdiction_weighted=True,
       reader=_read_pep_exposure),
    _q(id="IS-15", fundamental=15, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.ENHANCED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Negative news, allegations and reputational red flags",
       pass_condition="A dedicated media sweep ran and a backend answered",
       resolvers=("web_search",), reader=_read_adverse_media),
    _q(id="IS-16", fundamental=16, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.ENHANCED.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.HYBRID.value,
       text="Fraud, bribery or financial-crime convictions, and regulatory penalties",
       pass_condition="Enforcement registers are consulted; a formal criminal-record "
                      "check is counterparty-supplied",
       resolvers=("idv", "web_search"), reader=_read_enforcement_actions),
    _q(id="IS-16b", fundamental=16, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.SIMPLIFIED.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="No officer is a disqualified director",
       pass_condition="The disqualified-directors register is searched for each officer",
       resolvers=("ch_disqualified",),
       reader=_read_disqualification),
    # R-F3406 — litigation DECOMPOSED. "Adverse media, corruption and litigation" was one
    # question over three different evidence bases with three different remedies, which is
    # precisely why a report could look covered while two of the three were never run.
    # Each row below has its own source, its own access model and its own remedy.
    _q(id="IS-17a", fundamental=17, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="Reported court judgments naming the subject as a party",
       pass_condition="A judgment database is searched by PARTY NAME and either returns "
                      "matters or confirms none were found",
       resolvers=("court_records", "find_case_law"), reader=_read_court_judgments),
    _q(id="IS-17b", fundamental=17, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.BOTH.value,
       established_by=EstablishedBy.DATA.value,
       text="County Court Judgments (CCJs) against the company or the individual",
       pass_condition="The Register of Judgments, Orders and Fines is searched for the "
                      "subject's name and address, and returns the judgments on record "
                      "or confirms none. A CCJ is a money judgment: its ABSENCE is a "
                      "material finding, so an unsearched register is not a clean one",
       resolvers=("registry_trust",), jurisdiction_weighted=True, reader=_read_ccj),
    _q(id="IS-17c", fundamental=17, cluster=Cluster.INTEGRITY_SCREENING.value,
       tier=Tier.STANDARD.value, applies_to=AppliesTo.ENTITY.value,
       established_by=EstablishedBy.DATA.value,
       text="Employment tribunal claims decided against the employer",
       pass_condition="The published tribunal decisions are searched by respondent name "
                      "and either return decisions or confirm none",
       resolvers=("employment_tribunal",), reader=_read_tribunal),

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
       resolvers=("idv",), reader=None),
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


#: Sources the operator must DECIDE about before a run: they cost money, burn a finite
#: allowance, or are legally gated. FREE/KEYED_FREE sources simply run and need no choice.
GATED_ACCESS = (
    Access.PAID_PER_SEARCH.value,
    Access.QUOTA_LIMITED.value,
    Access.LICENCE_REQUIRED.value,
)


@fail_wire(module="dd_standard", gap_type="engine_failure")
def gated_source_options(entity_type: str = "company", tier: str = "STANDARD") -> dict:
    """R-F3436 — the PRE-RUN selection screen: which metered/gated sources does THIS
    subject need, which are usable right now, and what does each one buy?

    The operator's requirement, in their words: a paid section must be selectable before
    the DD runs, the run must actually search what was selected, and the screen must be
    accurate. Two distinctions carry that:

    * REQUIRED vs OPTIONAL is DERIVED from the catalogue, never hardcoded. A gated source
      is REQUIRED for a question when it is the only thing that could answer it — IS-17b
      (CCJs) declares `resolvers=("registry_trust",)` and nothing else, so without Registry
      Trust that question cannot be answered by any means. It is OPTIONAL when the question
      has another resolver that is usable now — IS-17a can fall back to court_records.
      This is why the answer changes per subject: a person and a company do not have the
      same questions in scope, so they do not need the same sources.

    * BUILT vs AVAILABLE stay separate (see ResolverSpec.availability). "No adapter" is a
      coding task; "no credential" is an operator task; "quota exhausted" is a spend
      decision. Collapsing them into one 'unavailable' would tell the operator nothing
      about what to actually do.

    Selecting a source here produces an ELECTION on the questions it unlocks, which
    `assess` then holds the run to: an elected question that did not run comes back
    `fulfilled: False` and `billable: False` (R-F3408). Declining produces a WAIVER, which
    stays in the denominator and can never improve a score (R-F3406).
    """
    et = (entity_type or "company").strip().lower()
    tier_norm = (tier or "STANDARD").strip().upper()
    in_scope = questions_for(tier_norm, et)

    rows: dict[str, dict] = {}
    for q in in_scope:
        specs = [RESOLVERS[r] for r in q.resolvers if r in RESOLVERS]
        if not specs:
            continue
        usable_now = [s for s in specs if s.availability()[0]]
        for s in specs:
            if s.access not in GATED_ACCESS:
                continue
            avail, why = s.availability()
            row = rows.setdefault(s.id, {
                "source_id": s.id,
                "name": s.name,
                "access": s.access,
                "available": avail,
                "unavailable_reason": why,
                "built": s.is_built(),
                "cost_note": s.note,
                "decision_url": s.decision_url,
                "required_for": [],
                "enhances": [],
            })
            # The question can be answered some other way right now -> this source only
            # ENHANCES it. Nothing else can answer it -> this source is REQUIRED.
            other_available = [u for u in usable_now if u.id != s.id]
            entry = {"question_id": q.id, "fundamental": q.fundamental, "text": q.text}
            (row["enhances"] if other_available else row["required_for"]).append(entry)

    options = sorted(rows.values(), key=lambda r: (not r["required_for"], r["source_id"]))
    for r in options:
        r["required"] = bool(r["required_for"])
        # What the operator is actually being asked. Stated per row so the UI never has
        # to infer intent from the access enum.
        # R-F3543 — these are read by a person deciding where to spend, so they
        # state the consequence of each choice rather than a status enum. The old
        # lines were terse to the point of cryptic ("REQUIRED: usable now; select
        # to search, decline to waive") and leaned on semicolons and dashes.
        if r["required"] and not r["available"]:
            r["decision"] = ("Blocking. Nothing else can answer these questions, "
                             "and this source cannot run yet.")
        elif r["required"] and r["available"]:
            r["decision"] = ("Required. Nothing else can answer these questions. "
                             "Tick to search it, or leave it to record a waiver.")
        elif r["available"]:
            r["decision"] = ("Optional. Another source already answers these. "
                             "Tick to add depth.")
        else:
            r["decision"] = ("Optional. Another source already answers these, and "
                             "this one cannot run yet.")

    return {
        "entity_type": et,
        "tier": tier_norm,
        "standard_version": STANDARD_VERSION,
        "questions_in_scope": len(in_scope),
        "options": options,
        # A run that selects nothing is legitimate; it is not a run that searched nothing.
        "note": ("Selecting a source ELECTS the questions it unlocks and the run is held "
                 "to it. Declining is a WAIVER: it is recorded with who and why, stays in "
                 "the denominator, and can never improve a score."),
    }


# ═══════════════════════════════════════════════════════════════════════════
# ASSESS — the checklist diff
# ═══════════════════════════════════════════════════════════════════════════

@fail_wire(module="dd_standard", gap_type="engine_failure")
def assess(report: dict, *, tier: str = "STANDARD", waivers: Any = None,
           elections: Any = None) -> dict:
    """Resolve every applicable question against a persisted report dict.

    Pure. Never raises on a partial report: a reader that throws yields NOT_RUN with the
    exception named, because a reader crash is a failure to establish, never a pass.

    `waivers` is the scope selection (R-F3406): questions the operator has deliberately
    declined, each carrying a name and a reason. A waiver lowers coverage, never raises
    it, and can never make a question read as clean.
    """
    report = report if isinstance(report, dict) else {}
    entity_type = str(_m(report.get("identity")).get("entity_type") or "company")
    applicable = questions_for(tier, entity_type)
    waiver_map = _coerce_waivers(waivers)
    election_map = _coerce_elections(elections)

    # R-F3408 — an elected question is IN SCOPE even if the tier excludes it. Without
    # this, ticking a paid section on a Simplified run would change nothing, which is the
    # exact "selection that does not search" failure this model exists to prevent.
    _in_scope = {q.id for q in applicable}
    for qid in election_map:
        if qid in _in_scope:
            continue
        q = QUESTIONS_BY_ID[qid]
        # Still never ask a corporate question of a person: an election cannot make an
        # inapplicable question applicable, it can only widen the tier.
        if q.applicable_to(entity_type):
            applicable = applicable + [q]
            _in_scope.add(qid)

    # An order and a decline for the same check cannot both stand. The order wins —
    # money changed hands — but the contradiction is recorded, never silently dropped.
    election_waiver_conflicts = sorted(set(election_map) & set(waiver_map))
    for qid in election_waiver_conflicts:
        waiver_map.pop(qid, None)

    # R-F3410 — A WAIVER THAT DOES NOT APPLY MUST STILL BE REPORTED.
    #
    # Found by the R-F3410 wiring test: waiving IS-15 (an ENHANCED question) on a
    # STANDARD run silently did nothing — the question was never in scope, so the waiver
    # had nothing to attach to and simply evaporated. The operator sees a form that
    # accepted their instruction and a report that contains no trace of it.
    #
    # Unlike an election, an unapplied waiver is NOT a broken promise: nothing was
    # ordered and nothing is owed. But it IS a silent discrepancy between what was asked
    # for and what happened, and this module's whole purpose is that such gaps are
    # stated. Reported, never inflated into a failure.
    _scope_ids = {q.id for q in applicable}
    waivers_ignored = [
        {"question_id": qid,
         "reason": ("not applicable to a subject of this type"
                    if qid in QUESTIONS_BY_ID
                    and not QUESTIONS_BY_ID[qid].applicable_to(entity_type)
                    else f"not in scope at tier {(tier or 'STANDARD').strip().upper()}"),
         "waived_by": w.waived_by}
        for qid, w in sorted(waiver_map.items()) if qid not in _scope_ids
    ]

    resolutions: list[Resolution] = []
    for q in applicable:
        # A question nobody asks of this subject type never reaches a reader.
        if not q.applicable_to(entity_type):
            resolutions.append(Resolution(
                q.id, EvidenceState.NOT_APPLICABLE.value,
                reason=f"not asked of a subject of type {entity_type!r}"))
            continue
        # A waiver is checked BEFORE any reader runs — the point of declining a check is
        # that it is not performed, so spending the quota anyway would defeat it.
        wv = waiver_map.get(q.id)
        if wv is not None:
            resolutions.append(Resolution(
                q.id, EvidenceState.WAIVED.value,
                reason=f"waived by {wv.waived_by}"
                       + (f" on {wv.waived_at}" if wv.waived_at else "")
                       + f": {wv.reason}",
                remedy="remove the waiver and re-run to establish this question. "
                       "a waived check is not a clear one"))
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

    # ── R-F3408 — did every ORDERED check actually run? ─────────────────────
    #
    # This is the ledger the operator's requirement turns on. A selected section that
    # produced nothing must be impossible to miss, so each election is classified and an
    # unfulfilled one flips `elections_honoured` False for the whole run.
    elections_out: list[dict] = []
    for qid, el in sorted(election_map.items()):
        r = by_id.get(qid)
        q = QUESTIONS_BY_ID[qid]
        if r is None:
            # Elected but not applicable to this subject type — an honest refusal, not a
            # silent drop, and NOT a broken promise (nothing could have been searched).
            elections_out.append({
                "question_id": qid, "fulfilled": False, "failure_kind": "not_applicable",
                "detail": f"{qid} does not apply to a subject of type {entity_type!r}; "
                          f"it was not searched and must not be charged for",
                "elected_by": el.elected_by, "billable": False,
            })
            continue
        fulfilled = r.state in _ANSWERED_STATES
        if fulfilled:
            kind, detail = "", f"searched and answered ({r.state})"
        elif r.state == EvidenceState.ATTEMPTED_INCONCLUSIVE.value:
            # Ours to retry, theirs to answer.
            kind, detail = "source_failed", (
                f"the source was searched and did not answer: {r.reason}")
        else:
            # NOT_RUN on an ELECTED question is the worst case in this whole model: a
            # section was ordered and nothing looked for it.
            kind, detail = "no_adapter", (
                f"ORDERED BUT NOT SEARCHED — {r.reason or 'no resolver ran'}. "
                f"This section must not be presented as covered or charged for.")
        elections_out.append({
            "question_id": qid,
            "fundamental": q.fundamental,
            "text": q.text,
            "fulfilled": fulfilled,
            "state": r.state,
            "failure_kind": kind,
            "detail": detail,
            "elected_by": el.elected_by,
            # Only a check that actually ran is chargeable. A metered search that never
            # reached the register must never appear on an invoice.
            "billable": fulfilled,
        })
    unfulfilled = [e for e in elections_out if not e["fulfilled"]]

    return {
        "standard_version": STANDARD_VERSION,
        "tier": (tier or "STANDARD").strip().upper(),
        "entity_type": entity_type,
        "elections": elections_out,
        "elections_unfulfilled": unfulfilled,
        #: False when ANY ordered section did not run. A caller must not present the
        #: report as complete while this is False.
        "elections_honoured": not unfulfilled,
        "election_waiver_conflicts": election_waiver_conflicts,
        #: Waivers the operator supplied that had nothing to attach to. Not a failure —
        #: nothing was ordered — but never silently dropped either.
        "waivers_ignored": waivers_ignored,
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
