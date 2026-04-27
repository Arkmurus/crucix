"""
ARIA Sanctions Intelligence — fuzzy entity screening with OpenSanctions integration.

The previous /api/aria/compliance/sanctions endpoint only checked the local Node
brain's exact-match `entityMatcher` and ARIA's knowledge base. That misses:

  - Transliteration variants (Gazprom / Газпром / Gazprоm with Cyrillic 'о')
  - Common aliases (UAC → United Aircraft Corporation; KAZ → Kazminerals)
  - Ownership-by-proxy ("Bank Rossiya subsidiary" → flag the parent)
  - Phonetic look-alikes ("Bin Salman" / "Ben Salman")
  - Truncated forms ("Wagner" → "PMC Wagner Group")

This module adds:
  1. Levenshtein-distance fuzzy matching (no external library — implemented inline)
  2. Phonetic Metaphone matching for Latin-script names
  3. OpenSanctions API integration (free, no key, https://api.opensanctions.org)
  4. Name variant generation (acronym expansion, common transliteration tables)
  5. Confidence scoring blended from string distance, phonetic, and authority weight

Usage:
    from .sanctions import fuzzy_screen
    result = await fuzzy_screen("Bank Rossiya")
    # → {matches: [...], top_score: 0.95, blocked: True, suggestions: [...]}
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger("aria.sanctions")

OPENSANCTIONS_API = "https://api.opensanctions.org/match/default"
OPENSANCTIONS_SEARCH = "https://api.opensanctions.org/search/default"

# OpenSanctions API key — free tier has heavy rate limits (1 req/sec, 1000/month).
# A paid key (from https://www.opensanctions.org/api/) gives 100 req/sec and unlimited
# monthly volume, plus access to the premium PEP and adverse-media datasets.
OPENSANCTIONS_API_KEY = os.getenv("OPENSANCTIONS_API_KEY", "").strip()


def _opensanctions_headers() -> dict:
    """Build headers for OpenSanctions API calls, including auth if available."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ARIA-Sanctions/1.0 (Arkmurus defence intelligence)",
    }
    if OPENSANCTIONS_API_KEY:
        headers["Authorization"] = f"ApiKey {OPENSANCTIONS_API_KEY}"
    return headers

# ── String distance: Levenshtein (no external dep) ──────────────────────────

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance. O(n*m), pure Python — fine for short names."""
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        curr = [i] + [0] * len(a)
        for j, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]

def _similarity(a: str, b: str) -> float:
    """0..1 similarity score from Levenshtein. 1.0 = identical."""
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b: return 0.0
    if a == b: return 1.0
    max_len = max(len(a), len(b))
    return 1.0 - (_levenshtein(a, b) / max_len)


# ── Phonetic: simplified Metaphone ──────────────────────────────────────────
# Strips vowels (except leading) and normalises consonant clusters so that
# "Smith" / "Smyth" / "Schmidt" all collapse to a similar code.

_VOWELS = set("aeiouy")
_METAPHONE_RULES = [
    (r"[^a-z]", ""),
    (r"^kn", "n"), (r"^gn", "n"), (r"^pn", "n"), (r"^wr", "r"),
    (r"^x", "s"),  (r"^wh", "w"),
    (r"ph", "f"),  (r"th", "0"),
    (r"sch", "sk"),(r"sh", "x"), (r"ch", "x"),
    (r"ck", "k"),  (r"cq", "k"), (r"cc", "k"),
    (r"qu", "kw"), (r"q", "k"),
    (r"x", "ks"),
]

def _metaphone(name: str) -> str:
    s = name.lower()
    for pat, repl in _METAPHONE_RULES:
        s = re.sub(pat, repl, s)
    if not s: return ""
    # Keep first letter (vowel or consonant), strip vowels from rest
    return s[0] + "".join(c for c in s[1:] if c not in _VOWELS)


# ── Transliteration / variant generation ────────────────────────────────────

# Common Cyrillic → Latin substitutions used in sanctions evasion
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X",
})

# Reverse: Latin → Cyrillic look-alike (catches obfuscation attempts)
_LATIN_TO_CYRILLIC = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х",
})

# Defence-industry acronym expansions
_ACRONYMS = {
    "UAC": "United Aircraft Corporation",
    "URSC": "United Shipbuilding Corporation",
    "USC": "United Shipbuilding Corporation",
    "NPK": "Naval Production Combine",
    "RTC": "Russian Technologies Corporation",
    "UEC": "United Engine Corporation",
    "KAMAZ": "KamAZ",
    "PMC": "Private Military Company",
    "VKO": "Aerospace Defence Forces",
    "FSB": "Federal Security Service",
    "GRU": "Main Intelligence Directorate",
}

def _generate_variants(name: str) -> list[str]:
    """Generate plausible name variants for fuzzy matching against sanctions lists."""
    variants = {name.strip()}
    n = name.strip()
    if not n:
        return []

    # Whole-string transformations
    variants.add(n.translate(_CYRILLIC_TO_LATIN))   # Cyrillic→Latin
    variants.add(n.lower())
    variants.add(n.upper())
    variants.add(n.title())

    # Punctuation strip
    variants.add(re.sub(r"[^\w\s]", "", n))

    # Acronym expansion
    upper = n.upper().strip()
    if upper in _ACRONYMS:
        variants.add(_ACRONYMS[upper])

    # If full name contains a known acronym as first word, also try expanded
    parts = n.split()
    if parts and parts[0].upper() in _ACRONYMS:
        expanded = _ACRONYMS[parts[0].upper()] + " " + " ".join(parts[1:])
        variants.add(expanded.strip())

    # Acronym extraction (strip vowels from each word for orgs)
    if len(parts) >= 2 and all(p[:1].isupper() for p in parts):
        acro = "".join(p[0] for p in parts if p)
        if 2 <= len(acro) <= 6:
            variants.add(acro)

    # Drop common corporate suffixes
    cleaned = re.sub(
        r"\b(ltd|limited|inc|incorporated|llc|gmbh|sa|sarl|plc|pte|"
        r"corporation|corp|company|co|holdings|group|jsc|ojsc|pjsc|pjc)\b\.?",
        "", n, flags=re.IGNORECASE,
    ).strip()
    if cleaned and cleaned != n:
        variants.add(cleaned)

    return [v for v in variants if v and len(v) >= 2]


# ── OpenSanctions API ───────────────────────────────────────────────────────

async def _opensanctions_match(name: str, entity_type: str = "Thing") -> list[dict]:
    """Query the OpenSanctions /match endpoint (free, no key required).

    OpenSanctions consolidates OFAC SDN, EU, UK OFSI, UN, Interpol Red Notices,
    PEP databases, and ~200 other lists into a single normalised dataset.
    """
    payload = {
        "queries": {
            "q1": {
                "schema": entity_type,
                "properties": {"name": [name]},
            }
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(OPENSANCTIONS_API, json=payload, headers=_opensanctions_headers())
            if resp.status_code == 401:
                logger.error("OpenSanctions auth failed — check OPENSANCTIONS_API_KEY env var")
                return []
            if resp.status_code == 429:
                logger.warning("OpenSanctions rate-limited (free tier: 1 req/sec). "
                               "Set OPENSANCTIONS_API_KEY for unlimited access.")
                return []
            if resp.status_code != 200:
                logger.debug("OpenSanctions match failed: %s %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
            results = (data.get("responses", {}) or {}).get("q1", {}).get("results", [])
            return results or []
    except httpx.HTTPError as e:
        logger.warning("OpenSanctions request error: %s", e)
        return []


async def _opensanctions_search(query: str, limit: int = 5) -> list[dict]:
    """Free-text search against OpenSanctions when /match returns nothing."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                OPENSANCTIONS_SEARCH,
                params={"q": query, "limit": limit},
                headers=_opensanctions_headers(),
            )
            if resp.status_code == 401:
                logger.error("OpenSanctions auth failed on search — check OPENSANCTIONS_API_KEY")
                return []
            if resp.status_code == 429:
                logger.warning("OpenSanctions rate-limited on search — set OPENSANCTIONS_API_KEY for higher quota")
                return []
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("results", []) or []
    except httpx.HTTPError as e:
        logger.warning("OpenSanctions search error: %s", e)
        return []


# ── Public API ──────────────────────────────────────────────────────────────

def _normalise_match(raw: dict, queried_name: str) -> dict:
    """Convert an OpenSanctions hit into ARIA's standard match shape."""
    props = raw.get("properties") or {}
    candidate_name = (props.get("name") or [raw.get("caption", "")])[0]
    sim = _similarity(queried_name, candidate_name)
    phon_match = _metaphone(queried_name) == _metaphone(candidate_name)
    score = float(raw.get("score", 0)) if raw.get("score") else max(sim, 0.7 if phon_match else 0)

    datasets = raw.get("datasets") or []

    # Extract family/associate relationships from OpenSanctions properties.
    # 2026-04-12: these surface inherited risk — if subject's spouse/sibling
    # is sanctioned, the subject inherits elevated risk.
    relationships = []
    for rel_type in ("familyOf", "associateOf", "relatedTo", "spouseOf",
                     "childOf", "parentOf", "siblingOf"):
        rel_targets = props.get(rel_type) or []
        for target in rel_targets[:5]:
            relationships.append({"kind": rel_type, "target": target})

    return {
        "name": candidate_name,
        "schema": raw.get("schema"),
        "lists": datasets,
        "list": datasets[0] if datasets else "OpenSanctions",
        "score": round(score, 3),
        "string_similarity": round(sim, 3),
        "phonetic_match": phon_match,
        "topics": props.get("topics") or [],
        "countries": props.get("country") or [],
        "aliases": (props.get("alias") or [])[:5],
        "relationships": relationships,
        "first_seen": raw.get("first_seen"),
        "last_change": raw.get("last_change"),
        "url": f"https://www.opensanctions.org/entities/{raw.get('id', '')}/" if raw.get("id") else None,
        "reason": "; ".join(datasets[:3]) if datasets else "OpenSanctions match",
    }


async def fuzzy_screen(name: str, *, threshold: float = 0.78) -> dict:
    """Comprehensive fuzzy sanctions screen for a single entity name.

    Steps:
      1. Generate name variants (transliteration, acronym, suffix-strip)
      2. Query OpenSanctions /match for each variant
      3. Fall back to /search free-text if /match yields nothing
      4. Score each candidate by string similarity + phonetic + dataset count
      5. Return matches above threshold + suggestions for manual review

    Args:
        name: Entity name to screen.
        threshold: Minimum confidence (0..1) for "blocking" matches.
    """
    name = (name or "").strip()
    if not name or len(name) < 2:
        return {"name": name, "error": "name too short", "matches": [], "blocked": False}

    variants = _generate_variants(name)
    seen_ids: set[str] = set()
    all_matches: list[dict] = []

    for variant in variants[:6]:  # cap to avoid API hammering
        try:
            raw_results = await _opensanctions_match(variant)
        except Exception as e:
            logger.warning("OpenSanctions match crashed on '%s': %s", variant, e)
            raw_results = []
        for r in raw_results:
            rid = r.get("id")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            normalised = _normalise_match(r, name)
            normalised["matched_via_variant"] = variant
            all_matches.append(normalised)

    # If no /match hits, try free-text /search as a backup
    if not all_matches:
        try:
            search_results = await _opensanctions_search(name, limit=8)
        except Exception as e:
            logger.warning("OpenSanctions search crashed: %s", e)
            search_results = []
        for r in search_results:
            rid = r.get("id")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            normalised = _normalise_match(r, name)
            normalised["matched_via_variant"] = "free_text_search"
            # Free-text matches are lower-confidence by default
            normalised["score"] = round(normalised["score"] * 0.85, 3)
            all_matches.append(normalised)

    # Rank
    all_matches.sort(key=lambda m: -m["score"])
    top_score = all_matches[0]["score"] if all_matches else 0.0
    blocking_matches = [m for m in all_matches if m["score"] >= threshold]

    result = {
        "name": name,
        "variants_tried": variants[:6],
        "matches": all_matches[:10],
        "blocking_matches": blocking_matches,
        "match_count": len(all_matches),
        "top_score": top_score,
        "blocked": len(blocking_matches) > 0,
        "threshold": threshold,
        "disclaimer": (
            "Pre-screen only. Blocking matches require manual verification against "
            "primary sanctions sources (OFAC SDN, OFSI, EU Consolidated, UN SC). "
            "OpenSanctions is updated daily but may lag designations by 24-48 hours."
        ),
    }

    # ── Brain hook: feed sanctions screening result ──
    if all_matches:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="sanctions",
                summary=f"Sanctions screen '{name}': {len(all_matches)} matches, top_score={top_score:.2f}, blocked={result['blocked']}",
                entity_name=name,
                success=True,
                confidence="PROBABLE",
            )
        except Exception as _bh:
            logger.debug("sanctions brain_hook failed: %s", _bh)

    return result


async def enrich_with_relationships(screen_result: dict, *, max_targets: int = 3,
                                    target_threshold: float = 0.78) -> dict:
    """Extend a fuzzy_screen() result with relationship-risk enrichment.

    For each of the top matches, walks up to `max_targets` family/associate
    relationship targets and checks whether each target is itself on a
    sanctions list. If so, attaches an `inherited_risk` block so ARIA can
    cite it as "subject's spouse/sibling/associate is sanctioned".

    Rate-limit aware: caps at `max_targets` relationships per match so a
    subject with 30 associates doesn't hammer the OpenSanctions free tier
    (1 req/sec). Caller should prefer paying for a key in production.

    Non-destructive: mutates nothing, returns a new dict with
    `relationships_enriched=True` and `inherited_risk_count` summary.
    """
    if not isinstance(screen_result, dict) or not screen_result.get("matches"):
        return screen_result
    enriched_matches: list[dict] = []
    seen_targets: set[str] = set()
    inherited_total = 0
    for m in screen_result.get("matches", []):
        m = dict(m)  # shallow copy
        rels = m.get("relationships") or []
        inherited: list[dict] = []
        for rel in rels[:max_targets]:
            target_name = (rel.get("target") or "").strip()
            if not target_name or len(target_name) < 3:
                continue
            target_key = target_name.lower()
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            try:
                # Free-text search is the cheapest path; /match would need a
                # schema choice we don't have for a raw name.
                hits = await _opensanctions_search(target_name, limit=2)
            except Exception:
                continue
            target_hit = None
            for h in hits or []:
                candidate = (((h.get("properties") or {}).get("name") or [h.get("caption", "")])[0])
                sim = _similarity(target_name, candidate)
                if sim >= target_threshold:
                    target_hit = h
                    target_hit["_sim"] = sim
                    break
            if not target_hit:
                continue
            datasets = target_hit.get("datasets") or []
            props = target_hit.get("properties") or {}
            topics = props.get("topics") or []
            inherited.append({
                "kind": rel.get("kind"),
                "target_name": target_name,
                "target_lists": datasets,
                "target_topics": topics,
                "target_score": round(float(target_hit.get("_sim", 0.0)), 3),
                "target_url": (
                    f"https://www.opensanctions.org/entities/{target_hit.get('id','')}/"
                    if target_hit.get("id") else None
                ),
            })
        if inherited:
            m["inherited_risk"] = inherited
            inherited_total += len(inherited)
        m["relationships_enriched"] = True
        enriched_matches.append(m)
    out = dict(screen_result)
    out["matches"] = enriched_matches
    out["inherited_risk_count"] = inherited_total
    out["relationships_enriched"] = True
    if inherited_total and not out.get("blocked"):
        # Inherited risk never auto-blocks, but it DOES escalate the
        # suggestion tier. The DD layer decides whether to hard-stop.
        out["inherited_risk_note"] = (
            f"{inherited_total} relationship(s) match an OpenSanctions entry — "
            "subject inherits elevated risk. Review relationship targets manually."
        )
    return out


async def screen_with_relationships(name: str, *, threshold: float = 0.78,
                                    max_rel_targets: int = 3) -> dict:
    """Convenience wrapper: fuzzy_screen() + enrich_with_relationships().

    Use when the caller actively cares about family/associate risk
    inheritance (person DD, beneficial-ownership screens). For plain
    tender-counterparty checks, fuzzy_screen() without enrichment is fine.
    """
    screen = await fuzzy_screen(name, threshold=threshold)
    return await enrich_with_relationships(screen, max_targets=max_rel_targets,
                                           target_threshold=threshold)


_ENTITY_STOPWORDS = frozenset({
    "the", "of", "and", "or", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "was", "are", "were", "be", "been", "being",
    "before", "after", "during", "since", "until", "while", "any", "all",
    "current", "former", "new", "old", "this", "that", "these", "those",
    "independently", "directly", "globally", "weekly", "daily", "monthly",
    "summary", "update", "review", "analysis", "report", "intelligence",
    "without", "with", "via", "without", "across", "between", "among",
})

# Domain-name-looking tokens (".gov", ".com", "site.gov", etc.) inside an
# input is a strong signal it's a search query / URL fragment, not a name.
_DOMAIN_TOKEN_RE = re.compile(r"\.(?:gov|com|org|net|edu|co|io|ai|mil)\b", re.IGNORECASE)


def _looks_like_entity_name(s: str) -> bool:
    """Reject inputs that don't look like an entity name before they
    waste OpenSanctions API quota.

    Live failure 2026-04-27 18:24:50: tasks.yaml passes search-query
    strings as `entity:` to deep_research, which forwarded them here.
    Strings like 'sanctions update OFAC SDN EU UN Security Council
    embargo 2026' or 'Arkmurus weekly intelligence summary Angola
    Mozambique...' generated 80+ /match POSTs each, all returning junk.

    Heuristics — entity names are typically:
      - 2-100 chars
      - <= 7 words (covers 'Sheikh Hamad bin Khalifa Al Thani II'
        and 'Krasnoyarsk Aluminum Smelter Open Joint-Stock Company';
        rejects 'GAMI current leadership independently before any
        formal outre')
      - No commas, question marks, exclamations
      - Don't end with a 4-digit year (search query smell)
      - Contain at most 1 common English stop-word (entity names like
        'Bank of America' have one; descriptions have many)
    """
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 2 or len(s) > 100:
        return False
    if "," in s or "?" in s or "!" in s:
        return False
    words = s.split()
    if len(words) > 7:
        return False
    # Year token anywhere is a search-query smell. Real entity names
    # rarely contain bare 4-digit years (model designators like 'Boeing
    # 747' are 3 digits; 'AK-47' has the year embedded but no space).
    if re.search(r"\b(19|20)\d{2}\b", s):
        return False
    if _DOMAIN_TOKEN_RE.search(s):
        return False
    # F39 fix 2026-04-27: previous threshold (>1 stopword) let through
    # 4-word fragments like "Iran nexus before engagement" and 6-word
    # ones like "Iran is openly signalling it will". Word count alone
    # can't distinguish "Bank of America Corp" (valid, 4 words, 1 stop)
    # from "Iran of foreign policy" (fragment, 4 words, 1 stop) — but
    # CASE can: real entity names are proper nouns (uppercase initial),
    # fragments have lowercase common nouns mixed in.
    stopword_count = sum(1 for w in words if w.lower() in _ENTITY_STOPWORDS)
    if stopword_count > 1:
        return False
    # Multi-word inputs: every non-stopword word must start with an
    # uppercase letter or digit ("F-35", "T-90"). One lowercase non-
    # stopword word means it's almost certainly a sentence fragment
    # (Iran NEXUS before ENGAGEMENT — nexus / engagement are lowercase
    # common nouns; Bank OF America CORP — of stopword, others all upper).
    if len(words) > 1:
        for w in words:
            if w.lower() in _ENTITY_STOPWORDS:
                continue
            if not w[0].isupper() and not w[0].isdigit():
                return False
    return True


async def screen_with_aliases(name: str, known_aliases: list[str] | None = None) -> dict:
    """Screen a primary name plus user-provided aliases. Combines results.

    Use this when you already know an entity has aliases (e.g. ship name + IMO,
    company name + former name) — passes each through the full fuzzy pipeline
    and returns the worst-case (highest-scoring) hit.
    """
    # Reject inputs that aren't entity names — caller passed a search
    # query / description by mistake. Returning early avoids hitting
    # OpenSanctions for 80+ wasted calls per cycle (F1+F2 fix 2026-04-27).
    if not _looks_like_entity_name(name):
        logger.info(
            "screen_with_aliases: rejecting non-entity input %r (looks like a search query, not a name)",
            name[:80],
        )
        return {
            "name": name,
            "error": "not_entity_shaped",
            "matches": [],
            "blocked": False,
            "top_score": 0,
        }

    targets = [name] + (known_aliases or [])
    targets = [t for t in targets if t and len(t.strip()) >= 2 and _looks_like_entity_name(t)]
    if not targets:
        return {"error": "no valid names to screen"}

    all_results = []
    for target in targets[:5]:
        try:
            r = await fuzzy_screen(target)
            r["alias_screened"] = target
            all_results.append(r)
        except Exception as e:
            logger.warning("fuzzy_screen failed for alias '%s': %s", target, e)

    if not all_results:
        return {"name": name, "error": "all screens failed", "blocked": False}

    # Aggregate
    worst = max(all_results, key=lambda r: r.get("top_score", 0))
    all_matches = []
    for r in all_results:
        for m in r.get("matches", []):
            all_matches.append(m)
    # Dedup by candidate name + list
    seen = set()
    deduped = []
    for m in sorted(all_matches, key=lambda x: -x["score"]):
        key = (m.get("name", "").lower(), m.get("list", ""))
        if key in seen: continue
        seen.add(key)
        deduped.append(m)

    return {
        "name": name,
        "aliases_checked": targets,
        "matches": deduped[:15],
        "top_score": worst.get("top_score", 0),
        "blocked": worst.get("blocked", False),
        "match_count": len(deduped),
        "per_alias_results": all_results,
        "disclaimer": worst.get("disclaimer"),
    }
