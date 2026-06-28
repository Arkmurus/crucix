"""ARIA Search Doctrine — Clause 19 discipline layer over researcher.web_search.

Problem this solves
───────────────────
`researcher.web_search` and `deep_research` have the primitives: tier
classification at rank time, multi-language parallel querying, 5 fixed
angles, budget-gated execution. What was missing is the DISCIPLINE
LAYER — the rules that say *when* to fire each primitive, *how* to
react to failure, and *how* to mark results so the LLM renders them
honestly.

Five pillars from the Search Doctrine:
  1. Query construction   — strip wrapper, broad-first, year markers,
                            3-attempt reformulation with vocabulary swap
  2. Source evaluation    — tier before read, single-source flag,
                            uniformity/seeding detection, primary-chain
  3. Search sequencing    — adaptive result count, decomposition,
                            "insufficient_public_intel" on 3× zero
  4. Synthesis            — [MEMORY] vs [WEB] vs [CONFLICT] tags
  5. Language             — already IMPLEMENTED in web_search.py; this
                            module just passes through

Public API
──────────
  await search(question, intent="factual"|"entity"|"bd"|"dd") -> dict
      Returns:
        {
          "status": "ok" | "insufficient_public_intel",
          "cleaned_query": str,
          "attempts": [query1, query2, ...],
          "components": [q1, q2, ...],   # if decomposed
          "result_count": int,
          "results": [  # tagged
            {"url": str, "snippet": str, "tier": "1a|1b|2|3|4|5",
             "tags": ["UNVERIFIED_SINGLE_SOURCE" | "SUSPECTED_SEEDING"
                      | "PRIMARY_FOLLOWED" | ...],
             "angle": str},
          ],
          "flags": ["SUSPECTED_SEEDING" | "INSUFFICIENT_PUBLIC_INTEL" | ...],
        }
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("aria.intel.search_doctrine")


# ══════════════════════════════════════════════════════════════════════════
# 1. QUERY CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════

# Conversational wrappers — strip before querying. The WhatsApp listener +
# chat router already strip Aria's trigger token, but operators still type
# "Aria, can you find..." style phrasing. These patterns are case-insensitive.
_WRAPPER_PATTERNS = [
    r"^\s*aria[,\s]+",
    r"^\s*(?:hi|hey|hello)[,\s]+aria[,\s]+",
    r"^\s*(?:can|could|would|will)\s+you\s+",
    r"^\s*please\s+",
    r"^\s*(?:find|look\s+up|tell\s+me|give\s+me|show\s+me|search\s+for|search|lookup|look\s+at|check|investigate|research|look\s+into)\s+",
    r"^\s*(?:i\s+(?:want|need)(?:\s+to\s+know)?)\s+",
    r"^\s*(?:for\s+me)[,\s]+",
]
_WRAPPER_RE = re.compile(
    "|".join(f"({p})" for p in _WRAPPER_PATTERNS),
    re.IGNORECASE,
)

# Trailing polite phrases
_TAIL_PATTERNS = [
    r"\s*please\s*\??\s*$",
    r"\s*thanks?\s*\??\s*$",
    r"\s*thank\s+you\s*\??\s*$",
    r"\s*\?\s*$",
]
_TAIL_RE = re.compile(
    "|".join(f"({p})" for p in _TAIL_PATTERNS),
    re.IGNORECASE,
)

# Question decomposition — split multi-part questions into component
# searches. Matches "AND", "plus", "; and", sentence-internal conjunctions
# that connect two independent clauses.
_DECOMPOSE_RE = re.compile(
    r"\s+(?:and\s+(?:also\s+|who\s+|what\s+|which\s+|how\s+)|"
    r"plus\s+|"
    r";\s*(?:and\s+)?|"
    r",\s+and\s+who\s+|"
    r",\s+and\s+what\s+)",
    re.IGNORECASE,
)

# Vocabulary swaps for reformulation on failure. These are targeted —
# each swap changes the *kind* of source we're likely to match, not just
# adds/removes specifiers.
_VOCAB_SWAPS = [
    # Appointment / office-holder language
    ("appointed",     "named"),
    ("appointment",   "nomination"),
    ("chief of",      "head of"),
    ("minister of",   "ministerial"),
    # Corporate / procurement language
    ("contract",      "tender"),
    ("awarded",       "selected"),
    ("signed",        "concluded"),
    ("procurement",   "acquisition"),
    ("purchase",      "order"),
    # Legal / compliance language
    ("sanctioned",    "designated"),
    ("sanctions",     "restrictive measures"),
    ("subsidiary",    "affiliate"),
    # Military / defence language
    ("weapons",       "armament"),
    ("military",      "armed forces"),
    ("defence",       "defense"),   # UK/US
    ("defense",       "defence"),
]


def _strip_conversational_wrapper(text: str) -> str:
    """Strip 'Aria, can you ...', tailing 'please', etc. Returns the
    content words only. Idempotent."""
    out = (text or "").strip()
    # Repeat-strip up to 3 times so nested wrappers ("aria can you please
    # find me...") collapse fully.
    for _ in range(3):
        new = _WRAPPER_RE.sub("", out, count=1).strip()
        if new == out:
            break
        out = new
    out = _TAIL_RE.sub("", out).strip()
    return out


def _decompose_question(text: str) -> list[str]:
    """Split a compound question into component searches. Returns [text]
    unchanged if no decomposition boundaries are found."""
    if not text:
        return []
    parts = [p.strip() for p in _DECOMPOSE_RE.split(text) if p and p.strip()]
    # Filter tiny fragments (pronouns alone, conjunctions, etc.)
    parts = [p for p in parts if len(p.split()) >= 2]
    if not parts:
        return [text]
    if len(parts) == 1:
        return parts
    # Only decompose if the component looks like an independent question
    # (has at least 3 content words after stop-word filtering). Otherwise
    # the split is probably mid-clause and we should keep the whole thing.
    stopwords = {"the", "a", "an", "of", "in", "on", "to", "for", "with",
                 "by", "at", "is", "are", "was", "were", "do", "does"}
    viable = []
    for p in parts:
        content = [w for w in p.lower().split()
                   if w.strip(".,;:?!") not in stopwords]
        if len(content) >= 3:
            viable.append(p)
    return viable if len(viable) >= 2 else [text]


def _reformulate(original: str, attempt: int) -> Optional[str]:
    """Produce a reformulation with DIFFERENT vocabulary, not just added
    words. Returns None if no further reformulation is possible.

    Attempt 1: apply the first matching vocab swap.
    Attempt 2: drop the most specific token (longest word); this widens.
    Attempt 3: keep only the two most content-bearing words.
    """
    if not original:
        return None
    text = original.strip()
    if attempt == 1:
        for a, b in _VOCAB_SWAPS:
            if re.search(rf"\b{re.escape(a)}\b", text, re.IGNORECASE):
                return re.sub(rf"\b{re.escape(a)}\b", b, text,
                              count=1, flags=re.IGNORECASE)
        # No matching swap — fall through to attempt 2 behaviour
        attempt = 2
    if attempt == 2:
        tokens = text.split()
        if len(tokens) <= 2:
            return None
        # Drop the longest non-quoted token (most specific)
        candidates = [(i, t) for i, t in enumerate(tokens)
                      if not (t.startswith('"') or t.endswith('"'))]
        if not candidates:
            return None
        idx, _ = max(candidates, key=lambda x: len(x[1]))
        return " ".join(t for i, t in enumerate(tokens) if i != idx)
    if attempt == 3:
        # Extract the two most content-bearing tokens — prefer quoted
        # entities, then capitalised words, then longest words.
        tokens = text.split()
        scored = []
        for i, t in enumerate(tokens):
            score = len(t)
            if t.startswith('"') or t.endswith('"'):
                score += 20
            elif t and t[0].isupper():
                score += 10
            scored.append((score, i, t))
        scored.sort(reverse=True)
        picks = sorted(scored[:2], key=lambda x: x[1])
        return " ".join(p[2] for p in picks) if picks else None
    return None


# ══════════════════════════════════════════════════════════════════════════
# 1b. NAMED-ENTITY EXTRACTION (zero-dependency, regex/heuristic)
# ══════════════════════════════════════════════════════════════════════════

# Country → (ISO2, demonym(s))
_COUNTRY_MAP: dict[str, tuple[str, list[str]]] = {
    "Angola":           ("AO", ["Angolan"]),
    "Mozambique":       ("MZ", ["Mozambican"]),
    "Nigeria":          ("NG", ["Nigerian"]),
    "Kenya":            ("KE", ["Kenyan"]),
    "Turkey":           ("TR", ["Turkish"]),
    "Brazil":           ("BR", ["Brazilian"]),
    "Ghana":            ("GH", ["Ghanaian"]),
    "Poland":           ("PL", ["Polish"]),
    "Romania":          ("RO", ["Romanian"]),
    "UAE":              ("AE", ["Emirati"]),
    "Saudi Arabia":     ("SA", ["Saudi", "Saudi Arabian"]),
    "India":            ("IN", ["Indian"]),
    "Germany":          ("DE", ["German"]),
    "France":           ("FR", ["French"]),
    "UK":               ("GB", ["British"]),
    "USA":              ("US", ["American"]),
    "China":            ("CN", ["Chinese"]),
    "Russia":           ("RU", ["Russian"]),
    "Israel":           ("IL", ["Israeli"]),
    "South Africa":     ("ZA", ["South African"]),
    "Egypt":            ("EG", ["Egyptian"]),
    "Morocco":          ("MA", ["Moroccan"]),
    "Algeria":          ("DZ", ["Algerian"]),
    "Ethiopia":         ("ET", ["Ethiopian"]),
    "Tanzania":         ("TZ", ["Tanzanian"]),
    "Uganda":           ("UG", ["Ugandan"]),
    "Senegal":          ("SN", ["Senegalese"]),
    "Côte d'Ivoire":    ("CI", ["Ivorian"]),
    "Mali":             ("ML", ["Malian"]),
    "Netherlands":      ("NL", ["Dutch"]),
    "Portugal":         ("PT", ["Portuguese"]),
    "Spain":            ("ES", ["Spanish"]),
    "Italy":            ("IT", ["Italian"]),
    "Japan":            ("JP", ["Japanese"]),
    "South Korea":      ("KR", ["Korean"]),
    "United Kingdom":   ("GB", ["British"]),
    "United States":    ("US", ["American"]),
    "United Arab Emirates": ("AE", ["Emirati"]),
}

# Build fast lookup: demonym/name/ISO2 → canonical country name
_JURISDICTION_LOOKUP: dict[str, str] = {}
for _country, (_iso2, _demonyms) in _COUNTRY_MAP.items():
    _JURISDICTION_LOOKUP[_country.lower()] = _country
    _JURISDICTION_LOOKUP[_iso2.lower()] = _country
    for _dem in _demonyms:
        _JURISDICTION_LOOKUP[_dem.lower()] = _country

# Also map "Africa" as a region keyword (not a country) — useful for
# search faceting
_REGION_KEYWORDS = {
    "africa", "african", "europe", "european", "asia", "asian",
    "middle east", "latin america", "south america", "nato",
    "ecowas", "sadc", "asean", "gcc", "mercosur",
}

# Role keywords — matched case-insensitively as whole words
_ROLE_KEYWORDS = [
    "minister", "director", "general", "colonel", "admiral",
    "commander", "chief", "officer", "secretary", "attaché", "attache",
    "procurement", "logistics", "defence", "defense", "military",
    "naval", "air force", "army", "brigadier", "major", "captain",
    "lieutenant", "marshal", "commodore", "cds",
]
_ROLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(r) for r in _ROLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
# Multi-word role phrases (matched greedily before single keywords)
_ROLE_PHRASE_RE = re.compile(
    r"\b(procurement\s+officer|air\s+force\s+chief|chief\s+of\s+(?:staff|defence|defense)"
    r"|defence\s+attach[ée]|defense\s+attach[ée]|military\s+attach[ée]"
    r"|naval\s+officer|logistics\s+officer|commanding\s+officer)\b",
    re.IGNORECASE,
)

# Product/platform pattern: 2-4 uppercase letters, optional dash, 1-4 digits
# e.g. TB2, MiG-29, F-16, C-130, VBTP-MR, AW109
_PRODUCT_RE = re.compile(
    r"\b([A-Z][A-Za-z]{0,3}(?:-[A-Z0-9]{1,4})?-?\d{1,4}[A-Z]?\b"
    r"|[A-Z]{2,5}-[A-Z]{1,4})\b"
)

# URL pattern — extract separately so they don't pollute search terms
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)

# Entity names — capitalised multi-word sequences that look like orgs/companies
# Excludes common English words that happen to start with caps at sentence start
_ENTITY_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "can", "may", "might", "should", "shall", "if", "then", "than",
    "this", "that", "these", "those", "it", "its", "i", "we", "you",
    "they", "he", "she", "who", "what", "where", "when", "why", "how",
    "find", "search", "look", "tell", "give", "show", "check", "get",
    "need", "want", "info", "information", "about", "research",
    "aria", "please", "hi", "hey", "hello", "thanks",
}


def extract_search_entities(message: str) -> dict:
    """Extract named entities from a user message using regex/heuristics.

    Returns a dict with keys:
      entities      - probable company/org names (capitalised sequences)
      jurisdictions - country names resolved from demonyms, ISO2, or names
      roles         - role/title phrases
      products      - defence product/platform designators
      urls          - extracted URLs
      clean_query   - the message stripped of wrappers, URLs removed,
                      with extracted facets joined for search
    """
    text = (message or "").strip()
    if not text:
        return {
            "entities": [], "jurisdictions": [], "roles": [],
            "products": [], "urls": [], "clean_query": "",
        }

    # ── URLs ─────────────────────────────────────────────────────────
    urls = _URL_RE.findall(text)
    # Remove URLs from working text so they don't interfere
    working = _URL_RE.sub("", text).strip()

    # ── Strip conversational wrapper ─────────────────────────────────
    stripped = _strip_conversational_wrapper(working)

    # ── Jurisdictions ────────────────────────────────────────────────
    jurisdictions: list[str] = []
    seen_j: set[str] = set()
    # Check multi-word countries first (South Africa, South Korea, etc.)
    for country_name in _COUNTRY_MAP:
        if " " not in country_name:
            continue
        pat = re.compile(r"\b" + re.escape(country_name) + r"\b", re.IGNORECASE)
        if pat.search(stripped):
            canon = _JURISDICTION_LOOKUP.get(country_name.lower(), country_name)
            if canon not in seen_j:
                jurisdictions.append(canon)
                seen_j.add(canon)
    # Check single words: country names, demonyms, ISO2 codes
    # For 2-letter tokens, require ALL UPPERCASE to avoid false positives
    # (e.g. "in" matching India's ISO2 "IN", "it" matching Italy's "IT")
    for word in re.findall(r"\b[\w']+\b", stripped):
        wl = word.lower()
        # Skip 2-letter tokens unless they are all-uppercase (real ISO2)
        if len(word) == 2 and not word.isupper():
            continue
        canon = _JURISDICTION_LOOKUP.get(wl)
        if canon and canon not in seen_j:
            jurisdictions.append(canon)
            seen_j.add(canon)
    # Check region keywords (returned as-is, titlecased)
    for region in _REGION_KEYWORDS:
        if re.search(r"\b" + re.escape(region) + r"\b", stripped, re.IGNORECASE):
            titled = region.title()
            if titled not in seen_j:
                jurisdictions.append(titled)
                seen_j.add(titled)

    # ── Roles ────────────────────────────────────────────────────────
    roles: list[str] = []
    seen_r: set[str] = set()
    # Multi-word phrases first
    for m in _ROLE_PHRASE_RE.finditer(stripped):
        role = m.group(0).lower().strip()
        if role not in seen_r:
            roles.append(role)
            seen_r.add(role)
    # Single keywords — only if not already captured in a phrase
    for m in _ROLE_RE.finditer(stripped):
        kw = m.group(0).lower().strip()
        # Skip if this keyword is part of an already-captured phrase
        if any(kw in r for r in seen_r):
            continue
        if kw not in seen_r:
            roles.append(kw)
            seen_r.add(kw)

    # ── Products/platforms ───────────────────────────────────────────
    products: list[str] = []
    seen_p: set[str] = set()
    for m in _PRODUCT_RE.finditer(stripped):
        prod = m.group(0)
        if prod not in seen_p:
            products.append(prod)
            seen_p.add(prod)

    # ── Entity names ─────────────────────────────────────────────────
    # Capitalised sequences of 1-4 words (likely org/company/person names)
    # Also catch all-uppercase tokens ≥2 chars (acronyms like MINFAR)
    entities: list[str] = []
    seen_e: set[str] = set()

    # All-uppercase tokens (acronyms: MINFAR, NATO, etc.)
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,})\b", stripped):
        tok = m.group(1)
        # Skip ISO2 codes already captured as jurisdictions
        if tok.lower() in _JURISDICTION_LOOKUP:
            continue
        # Skip product designators already captured
        if tok in seen_p:
            continue
        if tok not in seen_e and len(tok) >= 2:
            entities.append(tok)
            seen_e.add(tok)

    # Capitalised multi-word sequences (e.g. "DUMA Engineering", "Baykar")
    for m in re.finditer(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\b", stripped):
        phrase = m.group(0)
        words = phrase.split()
        # Filter out stopwords and jurisdiction/role words
        filtered = [w for w in words
                    if w.lower() not in _ENTITY_STOPWORDS
                    and w.lower() not in _JURISDICTION_LOOKUP
                    and not _ROLE_RE.fullmatch(w)]
        if not filtered:
            continue
        name = " ".join(filtered)
        if name not in seen_e and len(name) >= 2:
            entities.append(name)
            seen_e.add(name)

    # ── Dedup: remove entities that duplicate jurisdictions or are
    # substrings of other entities (e.g. "DUMA" when "DUMA Engineering"
    # already captured, or "Africa" in both entities and jurisdictions).
    # Filter entities that match a jurisdiction name or are a word within
    # a multi-word jurisdiction (e.g. "Arabia" inside "Saudi Arabia")
    _juris_lower = {j.lower() for j in jurisdictions}
    _juris_words = {w.lower() for j in jurisdictions for w in j.split()}
    entities = [e for e in entities
                if e.lower() not in _juris_lower
                and e.lower() not in _juris_words]
    # Remove entities that are substrings of other entities or products
    # (e.g. "DUMA" when "DUMA Engineering" exists, "MiG" when "MiG-29" exists)
    _deduped_entities: list[str] = []
    for e in entities:
        if any(e != other and e in other for other in entities):
            continue
        if any(e in p for p in products):
            continue
        _deduped_entities.append(e)
    entities = _deduped_entities

    # ── Build clean_query ────────────────────────────────────────────
    # Combine extracted facets into a focused search query.
    # Priority: entities + jurisdictions + roles + products
    facets: list[str] = []
    facets.extend(entities)
    facets.extend(jurisdictions)
    facets.extend(roles)
    facets.extend(products)
    if facets:
        clean_query = " ".join(facets)
    else:
        # No structured extractions — fall back to the stripped message
        clean_query = stripped

    return {
        "entities": entities,
        "jurisdictions": jurisdictions,
        "roles": roles,
        "products": products,
        "urls": urls,
        "clean_query": clean_query,
    }


_CURRENT_YEAR = datetime.now(timezone.utc).year


def _inject_year_marker(query: str, fact_ttl_days: Optional[int] = None) -> str:
    """When the fact is time-sensitive (TTL < 365 days), append the
    current year to the query unless a year is already present."""
    if fact_ttl_days is not None and fact_ttl_days >= 365:
        return query
    if re.search(r"\b(19|20)\d{2}\b", query):
        return query
    return f"{query} {_CURRENT_YEAR}"


# ══════════════════════════════════════════════════════════════════════════
# 3. SEARCH SEQUENCING — adaptive result count
# ══════════════════════════════════════════════════════════════════════════

_INTENT_RESULT_COUNTS = {
    "factual":  2,     # simple lookup: "who is X", "what is Y"
    "entity":   6,     # identity / role / employer research
    "bd":       10,    # business development assessment
    "dd":       12,    # due diligence — go wide
    "default":  6,
}


def _adaptive_result_count(intent: str) -> int:
    return _INTENT_RESULT_COUNTS.get(intent, _INTENT_RESULT_COUNTS["default"])


# ══════════════════════════════════════════════════════════════════════════
# 2. SOURCE EVALUATION
# ══════════════════════════════════════════════════════════════════════════

def _snippet_similarity(a: str, b: str) -> float:
    """Sequence-matcher ratio on lowercased snippets, short-circuited
    for obvious mismatches. Above ~0.85 is suspiciously uniform — the
    kind of copy-paste signature seeded content leaves behind."""
    if not a or not b:
        return 0.0
    a_l, b_l = a.lower()[:400], b.lower()[:400]
    if abs(len(a_l) - len(b_l)) / max(len(a_l), len(b_l)) > 0.5:
        return 0.0
    return SequenceMatcher(None, a_l, b_l).ratio()


def _flag_uniformity(results: list[dict]) -> list[dict]:
    """Flag groups of ≥3 near-identical snippets as SUSPECTED_SEEDING.
    Mutates each result's `tags` list in place and returns the list."""
    n = len(results)
    if n < 3:
        return results
    # Pair-wise similarity; seeded clusters of size ≥3 get flagged
    cluster_map: dict[int, set[int]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            sim = _snippet_similarity(
                results[i].get("snippet", ""),
                results[j].get("snippet", ""),
            )
            if sim >= 0.85:
                cluster_map.setdefault(i, {i}).add(j)
                cluster_map.setdefault(j, {j}).add(i)
    for idx, cluster in cluster_map.items():
        if len(cluster) >= 3:
            tags = results[idx].setdefault("tags", [])
            if "SUSPECTED_SEEDING" not in tags:
                tags.append("SUSPECTED_SEEDING")
    return results


def _flag_single_source(results: list[dict]) -> list[dict]:
    """Flag results whose URL/family appears in only ONE angle as
    UNVERIFIED_SINGLE_SOURCE. A claim backed by only one source is not
    verified regardless of tier — the policy mirrors Clause 17."""
    from urllib.parse import urlparse
    family_counts: dict[str, int] = {}
    for r in results:
        try:
            fam = urlparse(r.get("url", "")).netloc.replace("www.", "").lower()
        except Exception:
            fam = ""
        r["_family"] = fam
        if fam:
            family_counts[fam] = family_counts.get(fam, 0) + 1
    # A family is "multi-source" if it appears in ≥2 different angles,
    # or if ≥2 different families cover the same snippet neighbourhood.
    # Simplest heuristic: if only one result exists from this family AND
    # the corpus has <3 families total, tag it.
    distinct_families = len(family_counts)
    for r in results:
        fam = r.pop("_family", "")
        # Tag as single-source if (a) the whole result set draws from only
        # one family, or (b) this specific family appears exactly once AND
        # the overall corpus has ≤2 distinct families (thin coverage).
        if fam and (
            distinct_families == 1
            or (family_counts.get(fam, 0) == 1 and distinct_families <= 2)
        ):
            tags = r.setdefault("tags", [])
            if "UNVERIFIED_SINGLE_SOURCE" not in tags:
                tags.append("UNVERIFIED_SINGLE_SOURCE")
    return results


async def _primary_chain_follow(results: list[dict]) -> list[dict]:
    """When a Tier 2/3 result's snippet or extract cites a Tier 1a/1b URL
    in-body, enrich the result with a PRIMARY_FOLLOWED tag and append the
    primary URL. We do not re-extract here; the extractor downstream picks
    it up."""
    try:
        from . import verified_intel as _vi
    except Exception:
        return results
    classifier = _vi.SourceTierClassifier()
    url_pat = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)
    for r in results:
        tier_str = (r.get("tier") or "").lower()
        if tier_str not in ("2", "3"):
            continue
        snippet = r.get("snippet", "") or r.get("description", "")
        extract = r.get("extract", "")
        for chunk in (snippet, extract):
            if not chunk:
                continue
            for url in url_pat.findall(chunk):
                try:
                    t = classifier.classify(url)
                except Exception:
                    continue
                if t in (_vi.SourceTier.TIER_1A, _vi.SourceTier.TIER_1B):
                    r.setdefault("tags", []).append("PRIMARY_FOLLOWED")
                    r.setdefault("primary_urls", []).append(url)
                    break
    return results


def _classify_tiers_pre_read(results: list[dict]) -> list[dict]:
    """Attach tier to every result BEFORE the LLM/extractor reads it.
    This is what makes 'tier before content' an enforceable rule."""
    try:
        from . import verified_intel as _vi
    except Exception:
        return results
    classifier = _vi.SourceTierClassifier()
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        try:
            t = classifier.classify(url)
            r["tier"] = t.value
            r["tier_score"] = _vi.TIER_SCORES[t]
        except Exception:
            continue
    return results


# ══════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════

_MAX_REFORMULATIONS = 3


async def search(
    question: str,
    *,
    intent: str = "default",
    fact_ttl_days: Optional[int] = None,
) -> dict:
    """Run a disciplined web search per Clause 19.

    Args:
      question:        The raw user/LLM question (may contain wrappers)
      intent:          "factual" | "entity" | "bd" | "dd" | "default"
      fact_ttl_days:   Time-sensitivity hint; <365 injects current year

    Returns:
      dict as documented at module top.
    """
    cleaned = _strip_conversational_wrapper(question)
    if not cleaned:
        return {
            "status": "insufficient_public_intel",
            "cleaned_query": "",
            "attempts": [],
            "components": [],
            "result_count": 0,
            "results": [],
            "flags": ["EMPTY_QUERY_AFTER_STRIP"],
        }

    # ── NER pre-processing — extract structured facets ────────────────
    ner = extract_search_entities(question)
    # If NER produced a meaningful clean_query (has extracted facets),
    # use it instead of the raw stripped text for better search precision.
    if ner["entities"] or ner["jurisdictions"] or ner["roles"] or ner["products"]:
        cleaned = ner["clean_query"]
        logger.debug("NER facets: %s → query %r", {k: v for k, v in ner.items() if k != "clean_query"}, cleaned)

    # Decompose compound questions. Each component runs its own search;
    # results are merged at the end.
    components = _decompose_question(cleaned)

    max_results = _adaptive_result_count(intent)

    # Primary query is the first component (or the whole cleaned string
    # if no decomposition). Year-marker injection only for time-sensitive.
    primary = _inject_year_marker(components[0], fact_ttl_days=fact_ttl_days)

    attempts: list[str] = []
    aggregated_results: list[dict] = []
    try:
        from . import researcher as _r
    except Exception as e:
        return {
            "status": "insufficient_public_intel",
            "cleaned_query": cleaned, "attempts": [],
            "components": components, "result_count": 0,
            "results": [], "flags": ["RESEARCHER_MODULE_UNAVAILABLE"],
        }

    async def _one_query(q: str) -> list[dict]:
        try:
            raw = await _r.web_search(q, max_results=max_results)
        except Exception as e:
            logger.debug("web_search(%r) failed: %s", q, e)
            return []
        # researcher.web_search returns a dict with 'results' list
        if isinstance(raw, dict):
            items = raw.get("results") or raw.get("snippets") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        return items

    # ── Primary query with 3-attempt reformulation ────────────────────
    current = primary
    for attempt in range(1, _MAX_REFORMULATIONS + 1):
        attempts.append(current)
        results = await _one_query(current)
        if results:
            for r in results:
                r.setdefault("angle", f"primary_a{attempt}")
            aggregated_results.extend(results)
            break
        # Empty — reformulate and retry
        next_q = _reformulate(current, attempt)
        if not next_q or next_q == current:
            break
        current = next_q

    # ── Decomposed components (if any) run in parallel ────────────────
    if len(components) > 1:
        tasks = [_one_query(_inject_year_marker(c, fact_ttl_days))
                 for c in components[1:]]
        component_results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, cres in enumerate(component_results):
            if isinstance(cres, Exception):
                continue
            for r in cres:
                r.setdefault("angle", f"component_{i+1}")
            aggregated_results.extend(cres)

    # ── De-dup by URL ────────────────────────────────────────────────
    seen_urls: set[str] = set()
    dedup: list[dict] = []
    for r in aggregated_results:
        url = r.get("url", "")
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        dedup.append(r)

    if not dedup:
        # Brain + mistake-ledger signal: search exhaustion is a learning
        # signal — the predictor should warn on similar future queries.
        try:
            from . import brain_hook as _bh, mistake_ledger as _ml
            await _bh.absorb(
                module="search_doctrine",
                summary=f"Search exhausted: {cleaned[:80]}",
                detail=f"Attempts: {attempts}",
                success=False,
                gap_type="insufficient_public_intel",
                gap_detail=f"Exhausted {len(attempts)} reformulations: {attempts}",
            )
            await _ml.record(
                category="insufficient_public_intel",
                task_type="web_search",
                domain=(components[0] if components else cleaned)[:40].lower(),
                what=f"Zero results after {len(attempts)} attempts: {cleaned[:100]}",
                why="3 vocabulary-swap reformulations returned empty result sets",
                fix="Surface INSUFFICIENT_PUBLIC_INTEL; do not fabricate",
                what_class="search_exhaustion",
                severity="MEDIUM",
            )
        except Exception:
            pass
        return {
            "status": "insufficient_public_intel",
            "cleaned_query": cleaned,
            "attempts": attempts,
            "components": components,
            "result_count": 0,
            "results": [],
            "flags": ["INSUFFICIENT_PUBLIC_INTEL",
                      f"exhausted_{len(attempts)}_attempts"],
            "ner": ner,
        }

    # ── Apply the 4 discipline gates ───────────────────────────────────
    dedup = _classify_tiers_pre_read(dedup)
    dedup = _flag_single_source(dedup)
    dedup = _flag_uniformity(dedup)
    dedup = await _primary_chain_follow(dedup)

    flags: list[str] = []
    if any("SUSPECTED_SEEDING" in r.get("tags", []) for r in dedup):
        flags.append("SUSPECTED_SEEDING")
    if all("UNVERIFIED_SINGLE_SOURCE" in r.get("tags", []) for r in dedup):
        flags.append("ALL_RESULTS_SINGLE_SOURCE")

    # Brain signal: every successful search feeds mastery; seeding /
    # single-source flags feed the mistake ledger so the predictor
    # can warn on future queries in the same domain.
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="search_doctrine",
            summary=f"Search: {cleaned[:80]} ({len(dedup)} results)",
            detail=f"Flags: {flags or 'clean'}, attempts {len(attempts)}",
            success=True,
            gap_type=("source_seeding_suspected"
                      if "SUSPECTED_SEEDING" in flags else None),
            gap_detail=(f"{sum(1 for r in dedup if 'SUSPECTED_SEEDING' in r.get('tags', []))} "
                        f"results flagged as seeded"
                        if "SUSPECTED_SEEDING" in flags else None),
        )
        if "SUSPECTED_SEEDING" in flags:
            from . import mistake_ledger as _ml
            await _ml.record(
                category="source_seeding_suspected",
                task_type="web_search",
                domain=(components[0] if components else cleaned)[:40].lower(),
                what=f"Seeded results on query: {cleaned[:100]}",
                why="≥3 snippets with similarity ≥0.85 — copy-paste signature",
                fix="Rely on non-seeded sources; do not cite seeded cluster as CONFIRMED",
                what_class="source_uniformity",
                severity="MEDIUM",
            )
    except Exception:
        pass

    return {
        "status": "ok",
        "cleaned_query": cleaned,
        "attempts": attempts,
        "components": components,
        "result_count": len(dedup),
        "results": dedup,
        "flags": flags,
        "ner": ner,
    }


# ══════════════════════════════════════════════════════════════════════════
# 4. SYNTHESIS — post-generation checks
# ══════════════════════════════════════════════════════════════════════════

# A snippet reproduced ≥200 chars verbatim is a paraphrase-discipline
# violation. The check is best-effort — the snippet store must be passed
# in by the caller; we can't sniff it from the prompt alone.

_VERBATIM_THRESHOLD_CHARS = 200


def check_paraphrase_discipline(
    response_text: str,
    source_snippets: list[str],
) -> dict:
    """Flag verbatim reproduction of source snippets ≥200 chars.
    Returns {"ok": bool, "verbatim_hits": [{"snippet_start": str, "chars": int}]}.
    Caller (source_verifier or the LLM gate) decides what to do on a hit."""
    hits: list[dict] = []
    if not response_text or not source_snippets:
        return {"ok": True, "verbatim_hits": []}
    for snip in source_snippets:
        if not snip or len(snip) < _VERBATIM_THRESHOLD_CHARS:
            continue
        # Slide a window of 120 chars through the snippet; if any slice
        # appears verbatim in the response, flag the full match length.
        window = 120
        for i in range(0, len(snip) - window, 40):
            needle = snip[i:i + window]
            if needle in response_text:
                # Find max match length at this point
                j = i
                while j < len(snip) and snip[j:j + 20] in response_text:
                    j += 20
                match_len = max(j - i, window)
                if match_len >= _VERBATIM_THRESHOLD_CHARS:
                    hits.append({
                        "snippet_start": snip[i:i + 60],
                        "chars": match_len,
                    })
                    break  # one hit per source snippet is enough
    return {"ok": not hits, "verbatim_hits": hits}


def detect_conflicts(results: list[dict]) -> list[dict]:
    """Crude conflict detector — scans snippets for opposing
    numeric/date/status claims about the same entity. This is a
    heuristic gate; the LLM is still expected to reason about conflicts
    it finds, not blindly suppress them.

    Returns a list of conflict descriptors for inline [CONFLICT: ...]
    rendering by the synthesis layer.
    """
    conflicts: list[dict] = []
    # Numeric conflicts — same entity cited with different headline numbers
    number_re = re.compile(r"\$?\b(\d{1,3}(?:[,\.]\d{3})*(?:\.\d+)?)\s*(million|billion|m|bn)?\b", re.IGNORECASE)
    by_entity: dict[str, list[tuple[str, str]]] = {}
    for r in results:
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        if not snippet:
            continue
        for m in number_re.finditer(snippet):
            val = m.group(1)
            entity_bucket = r.get("entity") or "unspecified"
            by_entity.setdefault(entity_bucket, []).append((val, url))
    for ent, items in by_entity.items():
        uniq_vals = {v for v, _ in items}
        if len(uniq_vals) >= 2 and len(items) >= 2:
            # Multiple different numeric claims about the same entity
            conflicts.append({
                "kind": "numeric_mismatch",
                "entity": ent,
                "values": sorted(uniq_vals),
                "sources": [u for _, u in items][:4],
            })
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="search_doctrine",
        summary="Detect Conflicts",
        source_id="search_doctrine:R-F996",
    )

    return conflicts
