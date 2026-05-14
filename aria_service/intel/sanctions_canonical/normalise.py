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
_STOPWORDS = frozenset({
    # English connectives
    "of", "and", "the", "for", "in", "at", "to", "by", "on", "or",
    "as", "an", "is", "be", "are", "with", "from",
    # Corporate suffixes (formed-as words — not entity identity)
    "ltd", "limited", "llc", "inc", "incorporated", "corp",
    "corporation", "co", "company", "plc", "ag", "gmbh", "sa",
    "sarl", "srl", "spa", "oao", "ooo", "zao", "pao",
    "jsc", "ojsc", "pjsc", "joint", "stock",
    # Honorifics / titles
    "mr", "mrs", "ms", "mister", "miss", "dr", "sir", "lord",
    # Sanctions-list filler that survives the regex
    "company", "enterprise", "group", "holding", "holdings",
    "international", "industries", "industry", "trading",
    "investments", "investment", "capital", "partners", "fund",
    # Latin words used in legal entity names
    "et", "fils", "freres",
})

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
