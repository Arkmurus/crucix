"""R-F640 — Regional sanctions-evasion typology detector.

Why this module exists
──────────────────────
The 2026-05-17 ADSM case had three independent smoking-gun signals:
1. Russian Kornet on the goods list (caught by R-F638)
2. Non-existent SS-20 on the list (caught by R-F639)
3. **Goods location stated as Turkey** + Russian/Chinese-origin items
   on the list + MENA buyer claim

Item #3 is the COMPOSITE typology that turned the verdict from
"some bad items" to "this matches a documented sanctions-evasion
pattern". Conflict Armament Research (CAR), SIPRI, UK NCA + EU FIU
have documented Turkey-as-routing-hub for sanctioned Russian /
Chinese / Iranian goods to MENA and African destinations
extensively since 2022.

Individually, none of:
  - Turkey is a routing location  (legitimate trade hub)
  - Russian-origin items on a list (catches Kornet via R-F638)
  - "Saudi Gov" generic EUC (caught by R-F641)
…are by themselves disqualifying. Composed, they match a documented
typology — and THAT is what produces the highest-confidence verdict.

This module is the composite-pattern detector. It takes:
  - A list of weapon matches (from R-F638)
  - The goods-routing / stockholding location
  - The claimed end-user country
  - Optionally: the named end-user specificity (from R-F641)

…and produces a list of matched typology signatures with citations
to the public reporting that documents the pattern.

Design discipline
─────────────────
Typology rules are CONJUNCTIVE: all conditions in a rule must hold
before the rule fires. Each rule cites:
  - Public-domain source documenting the typology (CAR/SIPRI/OFSI/UN
    Panel of Experts / academic)
  - Severity (the typology's documented exfiltration / IHL impact)
  - Recommended response

Honesty discipline
──────────────────
A typology match is a PATTERN observation, not a conviction. The
output explicitly notes "consistent with documented typology X" —
not "this IS sanctions evasion". The operator + ECJU make the
ultimate determination. confidence on emitted findings = PROBABLE,
not CONFIRMED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# Inputs to the detector
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DealContext:
    """Inputs the detector scores typology patterns against.

    All fields are optional — the detector emits matches based on
    whatever is provided. The MORE that's provided, the stronger the
    composite match.
    """
    weapon_origins_iso2: tuple[str, ...] = ()       # list of ISO2s from R-F638 matches
    weapon_sanctions_statuses: tuple[str, ...] = ()  # from R-F638
    routing_location_iso2: Optional[str] = None      # where goods are physically held
    buyer_country_iso2: Optional[str] = None          # claimed end-user country
    buyer_named_end_user_specific: bool = False       # True if specific entity named (vs "Government")
    has_inf_eliminated_items: bool = False            # from R-F639
    has_categorically_banned_items: bool = False      # from R-F639
    goods_list_mixed_nato_and_non_nato_calibres: bool = False  # from R-F643 (or operator)
    no_timeline_indicated: bool = False
    no_commission_discussed: bool = False
    informal_channel_received: bool = False           # via new BD / trade-fair contact, no MoD reference

    def has_origin(self, iso2: str) -> bool:
        return iso2.upper() in (o.upper() for o in self.weapon_origins_iso2)

    def routing_is(self, iso2: str) -> bool:
        return (self.routing_location_iso2 or "").upper() == iso2.upper()

    def buyer_is(self, iso2: str) -> bool:
        return (self.buyer_country_iso2 or "").upper() == iso2.upper()


# ─────────────────────────────────────────────────────────────────────
# Typology rules — documented evasion patterns
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TypologyMatch:
    typology_id: str
    title: str
    severity: str                          # "amber" | "red" | "hard_stop"
    matched_conditions: tuple[str, ...]
    citations: tuple[str, ...]             # public-domain sources
    recommended_response: str
    detail: str


def detect(ctx: DealContext) -> list[TypologyMatch]:
    """Score the deal context against all typology rules.

    Returns a list of TypologyMatch entries (possibly empty). Multiple
    rules may fire; consumer composes the overall verdict from all
    matches.
    """
    if not isinstance(ctx, DealContext):
        return []
    matches: list[TypologyMatch] = []

    # ── TYPOLOGY 1 — Turkey-routed Russian/Iranian/Chinese-origin
    # goods to MENA buyers. The 2026-05-17 ADSM signature pattern.
    if (
        ctx.routing_is("TR")
        and (ctx.has_origin("RU") or ctx.has_origin("IR") or ctx.has_origin("CN"))
        and ctx.buyer_country_iso2 in (
            "SA", "AE", "EG", "JO", "QA", "OM", "KW", "BH",
            "LY", "SD", "YE", "IQ", "SY", "LB",  # MENA
        )
    ):
        conditions: list[str] = ["Routing location = Turkey"]
        origins_in = [
            iso2 for iso2 in ("RU", "IR", "CN")
            if ctx.has_origin(iso2)
        ]
        conditions.append(
            f"Goods-list contains items with origin "
            f"{'+'.join(origins_in)}"
        )
        conditions.append(
            f"Claimed buyer country = {ctx.buyer_country_iso2} (MENA region)"
        )
        matches.append(TypologyMatch(
            typology_id="TYP-TR-RU-MENA",
            title=(
                "Turkey-routed Russian/Iranian/Chinese-origin goods to "
                "MENA — documented sanctions-evasion typology"
            ),
            severity="red",
            matched_conditions=tuple(conditions),
            citations=(
                "Conflict Armament Research field reports — Libya 2020, "
                "Sudan 2023-2024, Yemen 2022",
                "SIPRI Arms Transfers Database — Turkey re-export trends",
                "UK OFSI Russia sanctions evasion typology paper 2024",
                "UN Panel of Experts on Yemen / Libya / Sudan reports",
                "EU FRONTEX + UK NCA typology bulletins",
            ),
            recommended_response=(
                "HOLD all engagement. Voluntary ECJU disclosure call "
                "recommended (intelligence-relevant pattern). Do NOT "
                "introduce any OEM. Do NOT issue quotation. Do NOT sign "
                "MOU. Document chain-of-custody. Disengage diplomatically "
                "from the originator without enumerating specific findings."
            ),
            detail=(
                "Composite pattern matches the documented Turkey-as-evasion-"
                "hub typology observed since 2022. Turkey-stocked inventory "
                "of Russian/Iranian/Chinese-origin ammunition has been "
                "tracked by CAR + SIPRI moving to MENA conflict zones "
                "through aggregator-broker structures. This pattern does "
                "not by itself prove unlawful intent on any specific "
                "party — but it is sufficient cause to disengage AND "
                "is intelligence-relevant for UK ECJU / NCA."
            ),
        ))

    # ── TYPOLOGY 2 — UAE-routed Iranian-origin (the post-JCPOA pattern)
    if (
        ctx.routing_is("AE")
        and ctx.has_origin("IR")
    ):
        matches.append(TypologyMatch(
            typology_id="TYP-AE-IR",
            title=(
                "UAE-routed Iranian-origin goods — documented "
                "sanctions-evasion typology"
            ),
            severity="red",
            matched_conditions=(
                "Routing location = UAE",
                "Goods-list contains Iranian-origin items",
            ),
            citations=(
                "US OFAC / FinCEN advisories on UAE-Iran trade routing",
                "Project Alpha (King's College London) typology papers",
                "UN Panel of Experts on Yemen reports — Iranian arms "
                "transhipment via UAE-flagged vessels",
            ),
            recommended_response=(
                "HOLD all engagement. UAE-Iran sanctions-evasion is "
                "documented OFAC + UK enforcement priority. Voluntary "
                "compliance disclosure recommended."
            ),
            detail=(
                "Iranian arms / dual-use goods transhipped via UAE has "
                "been a sustained enforcement priority since 2018 — "
                "documented by OFAC, FinCEN, UN Panels."
            ),
        ))

    # ── TYPOLOGY 3 — INF-eliminated items appearing on a procurement
    # list (Indicates list-author inexpertise OR deliberate test/seeded
    # items — either reading disqualifies engagement).
    if ctx.has_inf_eliminated_items:
        matches.append(TypologyMatch(
            typology_id="TYP-INF-ELIM-ON-LIST",
            title=(
                "INF Treaty-eliminated weapon listed as procurable — "
                "list-author inexpertise OR test/seeded item"
            ),
            severity="hard_stop",
            matched_conditions=(
                "Goods list includes an INF-eliminated weapon (e.g. "
                "SS-20, Pershing II, BGM-109G Gryphon) which was "
                "physically destroyed 1988-1991",
            ),
            citations=(
                "INF Treaty (1987) elimination protocols + verified "
                "destruction certificates (1988-1991)",
                "US-Russia Joint Compliance Inspection records (final 1991)",
            ),
            recommended_response=(
                "HARD STOP. A genuine sovereign procurement officer does "
                "not list a weapon physically destroyed 35 years ago. "
                "Disengage."
            ),
            detail=(
                "An INF-eliminated weapon on a procurement list is a "
                "competence/integrity tell. Three readings — all "
                "disqualifying: (1) list-author lacks fundamental "
                "procurement expertise; (2) list deliberately seeded "
                "with non-existent items to test broker discipline; "
                "(3) list pasted from unreliable source material. None "
                "are consistent with a real Saudi/Egyptian/etc. MoD "
                "operational procurement."
            ),
        ))

    # ── TYPOLOGY 4 — CWC/BWC items on a list (criminal trafficking
    # territory, not commerce).
    if ctx.has_categorically_banned_items:
        matches.append(TypologyMatch(
            typology_id="TYP-CWC-BWC-ON-LIST",
            title=(
                "Chemical or biological weapon listed — "
                "criminal trafficking, not commerce"
            ),
            severity="hard_stop",
            matched_conditions=(
                "Goods list includes a CWC- or BWC-banned agent",
            ),
            citations=(
                "Chemical Weapons Convention (CWC) 1993",
                "Biological Weapons Convention (BWC) 1972",
                "Australia Group export controls",
            ),
            recommended_response=(
                "HARD STOP. NOTIFY UK NCA / Counter-Proliferation "
                "(separate from ECJU). This is criminal trafficking "
                "territory. Do not respond on substance."
            ),
            detail="Universal ban — no buyer/state exception.",
        ))

    # ── TYPOLOGY 5 — Aggregator-broker pattern (multi-signal composite)
    aggregator_score = 0
    aggregator_signals: list[str] = []
    if ctx.goods_list_mixed_nato_and_non_nato_calibres:
        aggregator_score += 2
        aggregator_signals.append(
            "Goods list mixes NATO + non-NATO (Soviet/Chinese) calibres "
            "— inconsistent with single operational force"
        )
    if ctx.no_timeline_indicated:
        aggregator_score += 1
        aggregator_signals.append(
            "No timeline indicated — open-ended pricing request, "
            "consistent with shopping multiple intermediaries"
        )
    if ctx.no_commission_discussed:
        aggregator_score += 1
        aggregator_signals.append(
            "No commission rate discussed — deferred until quotes "
            "received, classic aggregator commission-farming pattern"
        )
    if ctx.informal_channel_received:
        aggregator_score += 1
        aggregator_signals.append(
            "Received via informal channel (trade-fair contact / new BD) "
            "rather than via formal MoD tender reference"
        )
    if not ctx.buyer_named_end_user_specific and ctx.buyer_country_iso2:
        aggregator_score += 1
        aggregator_signals.append(
            f"Buyer end-user specified only as '{ctx.buyer_country_iso2} "
            f"government' rather than named MoD directorate"
        )
    if aggregator_score >= 3:
        matches.append(TypologyMatch(
            typology_id="TYP-AGGREGATOR-BROKER",
            title=(
                "Aggregator-broker / commission-farming pattern "
                "(composite score: " + str(aggregator_score) + "/6)"
            ),
            severity="amber" if aggregator_score < 4 else "red",
            matched_conditions=tuple(aggregator_signals),
            citations=(
                "GAR / Conflict Armament Research broker-typology reports",
                "Dentons + Pinsent Masons defence-trade compliance "
                "advisories on intermediary patterns",
            ),
            recommended_response=(
                "Demand: Saudi MoD tender reference, named end-user "
                "directorate (RSLF/SANG/RSADF or equivalent), single "
                "operational-force calibre coherence, fixed deadline, "
                "agreed commission rate. If 3+ are missing, do not "
                "progress."
            ),
            detail=(
                "Composite pattern across multiple weak signals matches "
                "the standard aggregator-broker / commission-farming "
                "structure — a broker accumulates RFQ-style line items "
                "from multiple downstream sub-buyers and shops them to "
                "multiple Western intermediaries, taking commission on "
                "whichever responds. Not necessarily fraud, but "
                "operationally incompatible with the Clause 4.5(e) "
                "compliance-satisfaction discretion."
            ),
        ))

    return matches


def detect_composite_verdict(ctx: DealContext) -> dict:
    """High-level verdict summary across all matched typologies.

    Returns: dict with overall verdict (hard_stop|red|amber|clean),
    matches list, recommended_action, and detail.
    """
    matches = detect(ctx)
    if not matches:
        return {
            "overall_verdict": "clean",
            "matches": [],
            "recommended_action": (
                "No typology patterns matched. Proceed via standard DD "
                "discipline (R-F600-F613 + R-F634-F636)."
            ),
        }

    severity_rank = {"amber": 1, "red": 2, "hard_stop": 3}
    worst_severity = max(matches, key=lambda m: severity_rank.get(m.severity, 0))
    severity = worst_severity.severity

    return {
        "overall_verdict": severity,
        "matches": [
            {
                "typology_id": m.typology_id,
                "title": m.title,
                "severity": m.severity,
                "matched_conditions": list(m.matched_conditions),
                "citations": list(m.citations),
                "recommended_response": m.recommended_response,
                "detail": m.detail,
            }
            for m in matches
        ],
        "recommended_action": worst_severity.recommended_response,
        "summary": (
            f"{len(matches)} typology pattern(s) matched. "
            f"Worst severity: {severity}. "
            f"Worst pattern: {worst_severity.typology_id}."
        ),
    }


def render_findings_for_ctx(ctx: DealContext) -> list[dict]:
    """Return a list of Finding-shaped dicts for the DD orchestrator's
    compliance section."""
    matches = detect(ctx)
    out: list[dict] = []
    for m in matches:
        out.append({
            "severity": m.severity,
            "title": f"Typology match: {m.title}",
            "detail": (
                f"{m.detail}\n\nMatched conditions:\n"
                + "\n".join(f"  - {c}" for c in m.matched_conditions)
                + "\n\nCitations:\n"
                + "\n".join(f"  - {c}" for c in m.citations)
                + f"\n\nRecommended response: {m.recommended_response}"
            ),
            "source": (
                "evasion_typology_detector (R-F640) — "
                f"typology_id={m.typology_id}"
            ),
            "confidence": "PROBABLE",
            "typology_id": m.typology_id,
        })
    return out


def list_known_typologies() -> list[str]:
    """For the operator dashboard / audit log."""
    return [
        "TYP-TR-RU-MENA — Turkey-routed Russian/Iranian/Chinese to MENA",
        "TYP-AE-IR — UAE-routed Iranian-origin",
        "TYP-INF-ELIM-ON-LIST — INF-eliminated item on procurement list",
        "TYP-CWC-BWC-ON-LIST — chemical/biological agent on list",
        "TYP-AGGREGATOR-BROKER — composite aggregator-broker pattern",
    ]
