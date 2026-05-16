"""R-F634 — Cultural Atlas (country × dimension knowledge layer).

Purpose
───────
Per-country cultural intelligence for ARIA's BD + DD work. State-of-
the-art defence brokering requires the team to understand HOW a
counterparty communicates, negotiates, and decides — not just what
sanctions / registry data say. This module is the static, code-
grounded knowledge layer.

Frameworks used (all academic / public-domain)
──────────────────────────────────────────────
  - Hofstede 6-dimensions:
      PDI  Power Distance Index (0-100, higher = steeper hierarchy)
      IDV  Individualism vs Collectivism (0-100, higher = individualist)
      MAS  Masculinity vs Femininity (0-100, higher = competitive)
      UAI  Uncertainty Avoidance Index (0-100, higher = risk-averse)
      LTO  Long-Term Orientation (0-100, higher = future-focused)
      IVR  Indulgence vs Restraint (0-100, higher = indulgent)
    Source: Hofstede Insights public data + Geert Hofstede's
    "Cultures and Organizations" (3rd ed).
  - Hall's high/low context (high = implicit, low = explicit)
  - Erin Meyer's Culture Map dimensions where Hofstede is silent:
      communication style, evaluating, persuading, leading, deciding,
      trusting, disagreeing, scheduling
  - Local norms: weekend pattern, time-of-day for business, hospitality
    etiquette, gift-giving rules.

Honesty discipline
──────────────────
Per Clause 1 (epistemic honesty): the atlas is a SUMMARY of academic
research, not a determinism. Use as PROBABLE context — never quote a
Hofstede score as a verdict on an individual. The interpretation rules
flag this explicitly in every rendered context block.

Coverage (R-F634 seed — 30 countries)
─────────────────────────────────────
  CPLP: AO, BR, CV, GW, MZ, PT, ST
  GCC:  SA, AE, QA, KW, BH, OM, JO, EG
  Major NATO partners: GB, US, FR, DE, IT, ES, PL, TR, GR, NL
  Other priority: NG, KE, ZA, MA, IN, ID, RU, CN, SG, IL

Public API
──────────
    get(iso2) → CountryProfile | None
    list_countries() → list[str]
    render_context_block(iso2, *, depth='brief'|'full') → str
    suggest_communication_style(iso2) → str
    suggest_negotiation_approach(iso2) → str
    pair_compare(a_iso2, b_iso2) → dict   # for cross-cultural deals
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.intel.cultural_atlas")


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CountryProfile:
    """Cultural profile for a single jurisdiction."""
    iso2: str
    name: str
    # Hofstede 6-dim — 0-100 each. None = data unavailable.
    pdi: Optional[int] = None  # Power Distance
    idv: Optional[int] = None  # Individualism (vs Collectivism)
    mas: Optional[int] = None  # Masculinity
    uai: Optional[int] = None  # Uncertainty Avoidance
    lto: Optional[int] = None  # Long-Term Orientation
    ivr: Optional[int] = None  # Indulgence vs Restraint
    # Hall's context — "high" or "low"
    context: str = "medium"
    # Culture Map (Meyer) — discrete labels per dimension
    communicating: str = "low_context"         # low_context | high_context
    evaluating: str = "indirect_negative"      # direct_negative | indirect_negative
    leading: str = "egalitarian"               # egalitarian | hierarchical
    deciding: str = "consensual"               # consensual | top_down
    trusting: str = "task"                     # task_based | relationship_based
    disagreeing: str = "avoids_confrontation"  # confrontational | avoids_confrontation
    scheduling: str = "linear"                 # linear_time | flexible_time
    # Practical norms
    weekend: tuple[str, ...] = ("sat", "sun")
    business_day_hours: tuple[str, str] = ("09:00", "17:00")
    formality_default: str = "first_name"      # first_name | title_then_first | title_surname
    gift_giving: str = "neutral"               # expected | acceptable | neutral | avoid
    relationship_first: bool = False           # True for high-context / trust-relationship cultures
    # Free-text notes (operator-readable summary)
    notes: tuple[str, ...] = field(default_factory=tuple)


# ─────────────────────────────────────────────────────────────────────
# Seed data — 30 priority countries
# ─────────────────────────────────────────────────────────────────────
#
# Hofstede scores from Hofstede Insights public dataset
# (https://www.hofstede-insights.com/country-comparison/).
# Where data is sparse the field is None — ARIA must state that
# explicitly per Clause 1.

_COUNTRIES: dict[str, CountryProfile] = {

    # ── CPLP / Lusophone Africa (incumbent tier) ──────────────────────
    "AO": CountryProfile(
        iso2="AO", name="Angola",
        pdi=83, idv=18, mas=20, uai=60,
        context="high",
        communicating="high_context", trusting="relationship_based",
        leading="hierarchical", deciding="top_down",
        scheduling="flexible_time", relationship_first=True,
        formality_default="title_then_first",
        notes=(
            "Relationship-first business culture; defence procurement "
            "decisions are top-down through MoD + General Staff. "
            "Initial meetings rarely transactional — expect 2-3 "
            "rapport-building sessions before substantive talks. "
            "Portuguese is the working language; formal titles "
            "(Senhor/Senhora + family name) preferred until invited "
            "to first-name basis.",
        ),
    ),
    "MZ": CountryProfile(
        iso2="MZ", name="Mozambique",
        pdi=85, idv=15, mas=38, uai=44,
        context="high",
        communicating="high_context", trusting="relationship_based",
        leading="hierarchical", scheduling="flexible_time",
        relationship_first=True, formality_default="title_then_first",
        notes=(
            "Similar to AO but with SADC overlay. Cabo Delgado security "
            "context shapes any procurement discussion — Mozambican "
            "interlocutors expect cultural awareness of the northern "
            "insurgency. Portuguese + Makua/Sena depending on region.",
        ),
    ),
    "PT": CountryProfile(
        iso2="PT", name="Portugal",
        pdi=63, idv=27, mas=31, uai=99, lto=28, ivr=33,
        context="medium",
        communicating="high_context", trusting="relationship_based",
        leading="hierarchical", deciding="consensual",
        relationship_first=True, formality_default="title_then_first",
        notes=(
            "Bridge culture for CPLP business — Portuguese counterparts "
            "act as door-openers to Angola/Mozambique. Hierarchy + "
            "high uncertainty avoidance mean decisions are documented "
            "carefully. Business hours run later (10:00-19:00 with "
            "long lunch).",
        ),
    ),
    "BR": CountryProfile(
        iso2="BR", name="Brazil",
        pdi=69, idv=38, mas=49, uai=76, lto=44, ivr=59,
        context="high",
        communicating="high_context", trusting="relationship_based",
        evaluating="indirect_negative", scheduling="flexible_time",
        relationship_first=True, formality_default="first_name",
        notes=(
            "Brazil's defence sector (FAB, Marinha, EB) is centralised "
            "but the cultural style is informal-warm-with-hierarchy. "
            "First-name common but titles (Doutor, Coronel) signal "
            "respect. Avoid 'jeitinho' framing — comes across as "
            "cynical to interlocutors who use it themselves.",
        ),
    ),
    "CV": CountryProfile(
        iso2="CV", name="Cabo Verde",
        pdi=75, idv=20, mas=15, uai=40,
        context="high", communicating="high_context",
        relationship_first=True,
        notes=("Small-island governance, Portuguese-speaking, ECOWAS overlay.",),
    ),
    "GW": CountryProfile(
        iso2="GW", name="Guinea-Bissau",
        pdi=78, idv=15, mas=20, uai=60,
        context="high", communicating="high_context",
        relationship_first=True, scheduling="flexible_time",
        notes=(
            "Political volatility shapes business — relationships through "
            "specific officeholders rarely outlast government changes.",
        ),
    ),
    "ST": CountryProfile(
        iso2="ST", name="São Tomé e Príncipe",
        pdi=70, idv=20, mas=20, uai=50,
        context="high", communicating="high_context",
        relationship_first=True,
        notes=("Small-scale defence procurement; CPLP framework dominant.",),
    ),

    # ── GCC + MENA (Cold Entry / Developing tier) ─────────────────────
    "SA": CountryProfile(
        iso2="SA", name="Saudi Arabia",
        pdi=95, idv=25, mas=60, uai=80, lto=36, ivr=52,
        context="high",
        communicating="high_context", trusting="relationship_based",
        leading="hierarchical", deciding="top_down",
        scheduling="flexible_time", relationship_first=True,
        weekend=("fri", "sat"),
        business_day_hours=("09:00", "13:00"),
        formality_default="title_surname",
        gift_giving="acceptable",
        notes=(
            "GAMI is the main procurement gateway. Hierarchical and "
            "relationship-first; expect majlis-style rapport over many "
            "meetings before commercial substance. Friday-Saturday "
            "weekend; business hours interrupted by prayer (Dhuhr ~12, "
            "Asr ~15:30). Right-hand-only handshake; no gifts in alcohol.",
        ),
    ),
    "AE": CountryProfile(
        iso2="AE", name="United Arab Emirates",
        pdi=90, idv=25, mas=50, uai=80,
        context="high",
        communicating="high_context", trusting="relationship_based",
        leading="hierarchical", deciding="top_down",
        relationship_first=True,
        weekend=("sat", "sun"),  # since 2022 change
        formality_default="title_then_first",
        gift_giving="acceptable",
        notes=(
            "EDIC + Tawazun + Edge Group dominate defence — heavily state-"
            "linked. Friday is half-day; weekend Sat-Sun since 2022. "
            "Royal-family/military rank protocol matters. Cosmopolitan "
            "business veneer over conservative core values.",
        ),
    ),
    "QA": CountryProfile(
        iso2="QA", name="Qatar",
        pdi=93, idv=25, mas=55, uai=80,
        context="high", communicating="high_context",
        relationship_first=True, weekend=("fri", "sat"),
        formality_default="title_surname",
        notes=("MoD direct + state-owned Barzan Holdings dominant.",),
    ),
    "KW": CountryProfile(
        iso2="KW", name="Kuwait",
        pdi=90, idv=25, mas=40, uai=80,
        context="high", communicating="high_context",
        relationship_first=True, weekend=("fri", "sat"),
        formality_default="title_surname",
        notes=("Parliament has real veto on defence procurement — slower.",),
    ),
    "BH": CountryProfile(
        iso2="BH", name="Bahrain",
        pdi=80, idv=38, mas=45, uai=68,
        context="high", communicating="high_context",
        weekend=("fri", "sat"), formality_default="title_then_first",
    ),
    "OM": CountryProfile(
        iso2="OM", name="Oman",
        pdi=82, idv=30, mas=42, uai=70,
        context="high", communicating="high_context",
        weekend=("fri", "sat"), formality_default="title_surname",
        notes=("Royal Office direct procurement; slower decision cycle.",),
    ),
    "JO": CountryProfile(
        iso2="JO", name="Jordan",
        pdi=70, idv=30, mas=45, uai=65,
        context="high", communicating="high_context",
        weekend=("fri", "sat"), formality_default="title_then_first",
        notes=("KADDB is the main defence holding; English widely usable.",),
    ),
    "EG": CountryProfile(
        iso2="EG", name="Egypt",
        pdi=70, idv=25, mas=45, uai=80,
        context="high", communicating="high_context",
        trusting="relationship_based",
        weekend=("fri", "sat"), formality_default="title_surname",
        notes=(
            "Military-owned holding companies (NSPO, Arab Organization "
            "for Industrialization) dominate defence procurement. "
            "Bureaucratic; expect heavy documentation cycle.",
        ),
    ),

    # ── NATO / Major Partners ─────────────────────────────────────────
    "GB": CountryProfile(
        iso2="GB", name="United Kingdom",
        pdi=35, idv=89, mas=66, uai=35, lto=51, ivr=69,
        context="medium",
        communicating="low_context", evaluating="indirect_negative",
        leading="egalitarian", deciding="top_down",
        trusting="task_based", disagreeing="avoids_confrontation",
        scheduling="linear", formality_default="first_name",
        notes=(
            "DE&S / MOD procurement is process-heavy + risk-averse "
            "(high UAI within otherwise low-UAI culture). "
            "Indirect negative feedback ('it's interesting' = "
            "concerned). Decisions consensus-led at WG level, "
            "top-down at ministerial.",
        ),
    ),
    "US": CountryProfile(
        iso2="US", name="United States",
        pdi=40, idv=91, mas=62, uai=46, lto=26, ivr=68,
        context="low",
        communicating="low_context", evaluating="direct_negative",
        leading="egalitarian", deciding="top_down",
        trusting="task_based", disagreeing="confrontational",
        scheduling="linear", formality_default="first_name",
        notes=(
            "Highest individualism in the dataset. DSCA Foreign Military "
            "Sales is process-formal but interpersonal style is direct. "
            "Decisions credit individual ownership.",
        ),
    ),
    "FR": CountryProfile(
        iso2="FR", name="France",
        pdi=68, idv=71, mas=43, uai=86, lto=63, ivr=48,
        context="medium",
        communicating="high_context", evaluating="direct_negative",
        leading="hierarchical", deciding="top_down",
        formality_default="title_surname",
        notes=(
            "DGA procurement is highly process-driven; high UAI means "
            "decisions are documented carefully. Hierarchical but with "
            "direct argumentation — disagreement is intellectual, not "
            "personal.",
        ),
    ),
    "DE": CountryProfile(
        iso2="DE", name="Germany",
        pdi=35, idv=67, mas=66, uai=65, lto=83, ivr=40,
        context="low",
        communicating="low_context", evaluating="direct_negative",
        leading="egalitarian", deciding="consensual",
        trusting="task_based", scheduling="linear",
        formality_default="title_surname",
        notes=(
            "BAAINBw procurement is process-heavy. High LTO + UAI "
            "produces methodical, well-documented decisions. Direct "
            "negative feedback is normal and not personal.",
        ),
    ),
    "IT": CountryProfile(
        iso2="IT", name="Italy",
        pdi=50, idv=76, mas=70, uai=75, lto=61, ivr=30,
        context="medium",
        communicating="high_context", trusting="relationship_based",
        formality_default="title_surname",
        notes=("Leonardo + Fincantieri dominant; politics + relationships matter.",),
    ),
    "ES": CountryProfile(
        iso2="ES", name="Spain",
        pdi=57, idv=51, mas=42, uai=86, lto=48, ivr=44,
        context="medium",
        communicating="high_context", trusting="relationship_based",
        formality_default="title_then_first",
    ),
    "PL": CountryProfile(
        iso2="PL", name="Poland",
        pdi=68, idv=60, mas=64, uai=93, lto=38, ivr=29,
        context="low",
        communicating="low_context", leading="hierarchical",
        formality_default="title_surname",
        notes=("Highest UAI in Europe — process-heavy. WUE2 prime contracts.",),
    ),
    "TR": CountryProfile(
        iso2="TR", name="Türkiye",
        pdi=66, idv=37, mas=45, uai=85, lto=46, ivr=49,
        context="high",
        communicating="high_context", trusting="relationship_based",
        leading="hierarchical", deciding="top_down",
        relationship_first=True, formality_default="title_then_first",
        notes=(
            "SSB procurement is centralised. Relationship-first then "
            "transactional. Indirect negative feedback. Türkiye is "
            "NATO-member but operates with non-aligned procurement "
            "freedom — context matters for compliance discussion.",
        ),
    ),
    "GR": CountryProfile(
        iso2="GR", name="Greece",
        pdi=60, idv=35, mas=57, uai=100, lto=45, ivr=50,
        context="high", communicating="high_context",
        formality_default="title_surname",
        notes=("Highest UAI globally — process + documentation matter.",),
    ),
    "NL": CountryProfile(
        iso2="NL", name="Netherlands",
        pdi=38, idv=80, mas=14, uai=53, lto=67, ivr=68,
        context="low",
        communicating="low_context", evaluating="direct_negative",
        leading="egalitarian", deciding="consensual",
        formality_default="first_name",
        notes=("Very direct + egalitarian; consensus decision-making.",),
    ),

    # ── Other Priority ─────────────────────────────────────────────────
    "NG": CountryProfile(
        iso2="NG", name="Nigeria",
        pdi=80, idv=30, mas=60, uai=55, lto=13, ivr=84,
        context="high", communicating="high_context",
        trusting="relationship_based", scheduling="flexible_time",
        relationship_first=True, formality_default="title_then_first",
        notes=(
            "DICON + Defence HQ procurement. Cultural style is warm + "
            "expressive; status matters (chiefs, traditional titles). "
            "Decisions can take time despite informal style.",
        ),
    ),
    "KE": CountryProfile(
        iso2="KE", name="Kenya",
        pdi=70, idv=27, mas=60, uai=50,
        context="high", communicating="high_context",
        trusting="relationship_based", scheduling="flexible_time",
        formality_default="title_then_first",
        notes=("KDF + DOD direct. English widely usable for business.",),
    ),
    "ZA": CountryProfile(
        iso2="ZA", name="South Africa",
        pdi=49, idv=65, mas=63, uai=49, lto=34, ivr=63,
        context="medium",
        communicating="low_context", evaluating="direct_negative",
        formality_default="first_name",
        notes=("Armscor procurement; bilingual English/Afrikaans + 9 other.",),
    ),
    "MA": CountryProfile(
        iso2="MA", name="Morocco",
        pdi=70, idv=46, mas=53, uai=68,
        context="high", communicating="high_context",
        trusting="relationship_based",
        weekend=("sat", "sun"), formality_default="title_surname",
    ),
    "IN": CountryProfile(
        iso2="IN", name="India",
        pdi=77, idv=48, mas=56, uai=40, lto=51, ivr=26,
        context="medium",
        communicating="high_context", leading="hierarchical",
        deciding="top_down", trusting="relationship_based",
        relationship_first=True, formality_default="title_surname",
        notes=(
            "DPSU + ordnance factories + private OEMs (HAL, Tata, "
            "L&T). Make-in-India policy heavy on IP transfer + local "
            "content. Relationship + hierarchy + flexible-time.",
        ),
    ),
    "ID": CountryProfile(
        iso2="ID", name="Indonesia",
        pdi=78, idv=14, mas=46, uai=48, lto=62, ivr=38,
        context="high", communicating="high_context",
        relationship_first=True, scheduling="flexible_time",
        formality_default="title_then_first",
        notes=("PT Pindad + PT PAL state defence companies dominant.",),
    ),
    "RU": CountryProfile(
        iso2="RU", name="Russia",
        pdi=93, idv=39, mas=36, uai=95, lto=81, ivr=20,
        context="high",
        communicating="high_context", leading="hierarchical",
        deciding="top_down", trusting="relationship_based",
        relationship_first=True, formality_default="title_surname",
        notes=(
            "Rosoboronexport gatekeeps export. Extensive sanctions "
            "exposure means most Western defence brokers cannot "
            "engage — flag compliance before any commercial framing.",
        ),
    ),
    "CN": CountryProfile(
        iso2="CN", name="China",
        pdi=80, idv=20, mas=66, uai=30, lto=87, ivr=24,
        context="high",
        communicating="high_context", leading="hierarchical",
        deciding="top_down", trusting="relationship_based",
        relationship_first=True, formality_default="title_surname",
        notes=(
            "NORINCO + CETC + CASIC state-owned defence holdings. "
            "Guanxi (relationships) is central. Highest LTO globally "
            "— long-term thinking. Compliance overlay heavy "
            "(BIS Entity List, DoD MilCorps).",
        ),
    ),
    "SG": CountryProfile(
        iso2="SG", name="Singapore",
        pdi=74, idv=20, mas=48, uai=8, lto=72, ivr=46,
        context="medium",
        communicating="low_context", scheduling="linear",
        formality_default="first_name",
        notes=("ST Engineering + DSTA; transparent but hierarchical.",),
    ),
    "IL": CountryProfile(
        iso2="IL", name="Israel",
        pdi=13, idv=54, mas=47, uai=81, lto=38, ivr=44,
        context="low",
        communicating="low_context", evaluating="direct_negative",
        leading="egalitarian", disagreeing="confrontational",
        formality_default="first_name",
        notes=(
            "Lowest power-distance in the dataset — military rank + "
            "civilian first-name basis is unusual. Direct disagreement "
            "is normal and not personal. SIBAT export gateway.",
        ),
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def get(iso2: str) -> Optional[CountryProfile]:
    """Return the cultural profile for an ISO2 code, or None if not seeded."""
    if not iso2 or not isinstance(iso2, str):
        return None
    return _COUNTRIES.get(iso2.upper().strip())


def list_countries() -> list[str]:
    """All seeded ISO2 codes, sorted."""
    return sorted(_COUNTRIES.keys())


def render_context_block(iso2: str, *, depth: str = "brief") -> str:
    """Return a context block ARIA can inject into a DD or chat reply.

    `depth='brief'` is a 3-4 line summary; `'full'` includes Hofstede
    dimensions + interpretation. Always tagged as PROBABLE — cultural
    summaries are about populations, not the individual you're talking
    to (Clause 1 epistemic honesty).
    """
    p = get(iso2)
    if p is None:
        return ""
    lines: list[str] = [
        f"\n\n[CULTURAL CONTEXT — {p.iso2} ({p.name}) — PROBABLE, population-level summary, not deterministic about any individual]",
    ]
    if depth == "brief":
        lines.append(_brief_one_liner(p))
        return "\n".join(lines)
    # Full depth
    hd = []
    if p.pdi is not None: hd.append(f"PDI={p.pdi}")
    if p.idv is not None: hd.append(f"IDV={p.idv}")
    if p.mas is not None: hd.append(f"MAS={p.mas}")
    if p.uai is not None: hd.append(f"UAI={p.uai}")
    if p.lto is not None: hd.append(f"LTO={p.lto}")
    if p.ivr is not None: hd.append(f"IVR={p.ivr}")
    if hd:
        lines.append("Hofstede 6-dim: " + " · ".join(hd))
    lines.append(
        f"Context (Hall): {p.context} · "
        f"Communicating: {p.communicating} · "
        f"Trusting: {p.trusting}"
    )
    lines.append(
        f"Leading: {p.leading} · "
        f"Deciding: {p.deciding} · "
        f"Scheduling: {p.scheduling}"
    )
    if p.formality_default or p.weekend:
        lines.append(
            f"Formality default: {p.formality_default} · "
            f"Weekend: {'/'.join(p.weekend)} · "
            f"Relationship-first: {p.relationship_first}"
        )
    if p.notes:
        lines.append("Notes:")
        for n in p.notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def _brief_one_liner(p: CountryProfile) -> str:
    """Compact summary for the brief depth — feeds chat replies."""
    bits: list[str] = []
    if p.relationship_first:
        bits.append("relationship-first")
    if p.context == "high":
        bits.append("high-context communication")
    if p.leading == "hierarchical":
        bits.append("hierarchical decision-making")
    elif p.leading == "egalitarian":
        bits.append("egalitarian style")
    if p.scheduling == "flexible_time":
        bits.append("flexible time")
    if "/".join(p.weekend) != "sat/sun":
        bits.append(f"weekend: {'/'.join(p.weekend)}")
    style = "; ".join(bits) if bits else "western-default norms"
    return (
        f"{p.name}: {style}. "
        f"Formality default: {p.formality_default.replace('_', ' ')}. "
        f"First meeting tactic: "
        + ("rapport-build before commercials." if p.relationship_first
           else "agenda + objectives upfront acceptable.")
    )


def suggest_communication_style(iso2: str) -> str:
    """One-line directive for ARIA's reply style when discussing this
    jurisdiction in the user's context."""
    p = get(iso2)
    if p is None:
        return ""
    bits: list[str] = []
    if p.communicating == "high_context":
        bits.append("imply, don't over-explain")
    else:
        bits.append("be explicit and direct")
    if p.evaluating == "direct_negative":
        bits.append("direct negative feedback is normal")
    else:
        bits.append("soften negative feedback")
    if p.disagreeing == "confrontational":
        bits.append("disagreement is intellectual, not personal")
    return f"{p.name} comms: " + "; ".join(bits) + "."


def suggest_negotiation_approach(iso2: str) -> str:
    """Approach guidance for a deal with a counterparty in this jurisdiction."""
    p = get(iso2)
    if p is None:
        return ""
    parts: list[str] = []
    if p.relationship_first:
        parts.append(
            "Invest in relationship-building first — expect 2-3 "
            "rapport sessions before substantive commercial talk."
        )
    else:
        parts.append("Transactional framing acceptable from first meeting.")
    if p.deciding == "top_down":
        parts.append("Decisions made by the top — identify the actual signatory early.")
    elif p.deciding == "consensual":
        parts.append("Consensus-driven — engage the working group, not just the chief.")
    if p.uai is not None and p.uai >= 75:
        parts.append("High uncertainty avoidance — heavy documentation + risk frameworks expected.")
    if p.scheduling == "flexible_time":
        parts.append("Flexible time orientation — don't anchor too tightly on meeting end-times.")
    return f"{p.name} negotiation: " + " ".join(parts)


def pair_compare(a_iso2: str, b_iso2: str) -> dict:
    """Cross-cultural deal helper: highlight the largest deltas between
    two jurisdictions. Returns dict with `gaps` list of (dim, delta_pp,
    risk_flag) tuples — useful when ARIA is brokering across a culture
    gap (e.g. Saudi buyer + German OEM)."""
    a, b = get(a_iso2), get(b_iso2)
    if a is None or b is None:
        return {"available": False, "reason": "one or both ISO2 not in atlas"}
    gaps: list[dict] = []
    for dim_label, a_val, b_val in (
        ("PDI (power distance)", a.pdi, b.pdi),
        ("IDV (individualism)", a.idv, b.idv),
        ("UAI (uncertainty avoidance)", a.uai, b.uai),
        ("LTO (long-term orientation)", a.lto, b.lto),
    ):
        if a_val is None or b_val is None:
            continue
        delta = abs(a_val - b_val)
        if delta >= 30:
            gaps.append({
                "dimension": dim_label,
                "a": a_val,
                "b": b_val,
                "delta_pp": delta,
                "risk_flag": "amber" if delta < 50 else "red",
            })
    # Style gaps
    style_gaps: list[dict] = []
    for label, a_val, b_val in (
        ("context", a.context, b.context),
        ("communicating", a.communicating, b.communicating),
        ("leading", a.leading, b.leading),
        ("deciding", a.deciding, b.deciding),
        ("scheduling", a.scheduling, b.scheduling),
    ):
        if a_val != b_val:
            style_gaps.append({"dimension": label, "a": a_val, "b": b_val})
    return {
        "available": True,
        "a_country": a.name,
        "b_country": b.name,
        "hofstede_gaps": gaps,
        "style_gaps": style_gaps,
        "summary": (
            f"{len(gaps)} Hofstede gap(s) ≥30pp + {len(style_gaps)} "
            f"style differences. Brokering requires bridging these "
            f"explicitly — don't assume either side reads the other's "
            f"signals correctly."
        ),
    }
