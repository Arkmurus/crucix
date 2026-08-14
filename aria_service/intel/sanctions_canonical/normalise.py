"""Name normalisation shared across all sanctions sources.

Goal: produce a stable, lowercased, stopword-stripped, sorted token
key that lets us:
  (a) compare across sources (OFAC + EU + UK + UN) on equal footing
  (b) extract entity-name tokens for R-F518's entity-overlap gate

R-F518 inheritance: `entity_tokens(name)` strips a domain-jargon
allowlist (sanctions / corporate / honorific tokens) so the
remaining tokens are dominated by the actual entity identity.
"""
from __future__ import annotations

import re
import unicodedata

# Stopwords that survive _normalise_question in reasoning_library
# would still pollute sanctions matching. Strip them aggressively.
# ── R-F3984 (C-71) — TWO CATEGORIES, and conflating them blocks the wrong
# company.
#
# GENERIC BUSINESS nouns are weak identity, but they are exactly what
# DISTINGUISHES "Aviation Group" from "Aviation Industry Corporation". Strip
# them and both collapse to the single token `aviation`, at which point
# `_evaluate_gate` rule (a) — "exact normalised-name equality" — grants an
# immediate pass and an innocent defence company is HARD_STOPped against an
# unrelated designation. Reproduced live: three distinct companies, all
# HARD_STOP, all `method=exact score=1.0`, `gate_blocked=0`.
#
# They stay in `_STOPWORDS` (so `normalise_name` is byte-for-byte unchanged and
# matching recall is untouched), but they are named here so
# `normalise_name_conservative` can keep them and give the gate a second
# opinion on whether an "exact" match is real or an artifact of stripping.
_GENERIC_BUSINESS_STOPWORDS = frozenset({
    "company", "enterprise", "group", "holding", "holdings",
    "international", "industries", "industry", "trading",
    "investments", "investment", "capital", "partners", "fund",
})

# LEGAL-FORM tokens genuinely carry no identity — "JSC ROSOBORONEXPORT" and
# "Rosoboronexport Ltd" are the same entity. These are stripped by BOTH
# normalisations, which is what keeps true alias matching working.
_LEGAL_FORM_STOPWORDS = frozenset({
    # English connectives
    "of", "and", "the", "for", "in", "at", "to", "by", "on", "or",
    "as", "an", "is", "be", "are", "with", "from",
    # Corporate suffixes (formed-as words — not entity identity)
    "ltd", "limited", "llc", "inc", "incorporated", "corp",
    "corporation", "co", "plc", "ag", "gmbh", "sa",
    "sarl", "srl", "spa", "oao", "ooo", "zao", "pao",
    "jsc", "ojsc", "pjsc", "joint", "stock",
    # Honorifics / titles
    "mr", "mrs", "ms", "mister", "miss", "dr", "sir", "lord",
    # Latin words used in legal entity names
    "et", "fils", "freres",
})

_STOPWORDS = _LEGAL_FORM_STOPWORDS | _GENERIC_BUSINESS_STOPWORDS

# Domain jargon stripped to produce entity-only tokens for R-F518 gate.
# Same family as reasoning_library._DOMAIN_JARGON.
_DOMAIN_JARGON = frozenset({
    # Sanctions / programs
    "sanction", "sanctioned", "sanctions", "sdn", "ofac", "ofsi",
    "fcdo", "eu", "uk", "us", "usa", "un", "unsc", "seco", "dfat",
    "mofa", "bis", "ddtc",
    # Entity types
    "person", "individual", "entity", "company", "vessel", "ship",
    "aircraft",
    # Geographic noise
    "country", "region", "jurisdiction",
    # Sanctions program codes — common prefixes
    "globalmagnit", "syria", "cyber", "russia", "iran", "venezuela",
    "ukraine", "belarus",
})


def normalise_name(name: str) -> str:
    """Stable normalised form: lowercased, accent-stripped,
    non-word→space, stopword-stripped, tokens sorted unique.

    "Bank of Russia" → "bank russia"
    "ZAGARIA, Michele" → "michele zagaria"
    "Société Générale" → "generale societe"
    """
    if not name:
        return ""
    # Accent-strip via NFKD then ASCII
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t and len(t) > 1 and t not in _STOPWORDS]
    return " ".join(sorted(set(tokens)))


def normalise_name_conservative(name: str) -> str:
    """Like `normalise_name`, but KEEPS the generic business nouns.

    R-F3984 (C-71) — the second opinion on whether an "exact" match is real.
    `normalise_name` strips sector nouns, so "Aviation Group" and "Aviation
    Industry Corporation" both become `aviation`; rule (a) of the R-F518 gate
    then grants an immediate pass on that equality and HARD_STOPs an innocent
    company. Under this form they are `aviation group` vs `aviation industry`
    and are correctly distinguishable.

    Legal-form tokens are still stripped, which is what preserves the
    legitimate case: "JSC ROSOBORONEXPORT", "Rosoboronexport Ltd" and
    "Rosoboronexport" all reduce to `rosoboronexport` here too, so a true alias
    match keeps its exact-name pass.

    Deliberately NOT used for scoring or candidate selection — only to qualify
    the exact-match SHORTCUT. Using it to match would narrow recall, which is
    the opposite of what a never-false-clean screen wants.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split()
              if t and len(t) > 1 and t not in _LEGAL_FORM_STOPWORDS]
    return " ".join(sorted(set(tokens)))


def entity_tokens(normalised: str) -> set[str]:
    """Strip domain-jargon from a normalised name. What remains is
    dominated by the actual entity identity — what R-F518's gate
    intersects against the query's entity tokens."""
    if not normalised:
        return set()
    return set(normalised.split()) - _DOMAIN_JARGON


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return len(inter) / len(a | b)
