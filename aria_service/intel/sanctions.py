"""ARIA Sanctions Intelligence 

— fuzzy entity screening with OpenSanctions integration.

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
    # → {matches: [...], top_score: 0.95, blocked: True, suggestions: [...]}"""
from __future__ import annotations
from .engine_wiring import wire_success

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
    # R-F62 (2026-05-09): dropped the n.lower() variant. OpenSanctions
    # match is case-insensitive at the API level, so the lowercase form
    # adds zero discriminating value — but for multi-word inputs like
    # "DD on EDGE Group" → "dd on edge group" it RELIABLY trips the
    # entity-name validator (lowercase non-stopwords), which then logs
    # "rejecting non-entity input" every cycle. Live evidence 2026-05-09
    # 11:18:34 (fly logs). Kept .upper() and .title() because some
    # legitimate-looking corporate variants still differ usefully.
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
    # F73 fix extension 2026-05-01: live evidence 07:30:05.969 — a chat
    # leaked a prompt fragment ('HIGH-STAKES and you are NOT 100%
    # confident, state what you would need to confirm') into the entity
    # extractor. /search/default already rejects via _looks_like_entity_name
    # but /match/default did NOT — so the bad input burned a free-tier
    # request (1 req/sec quota) before the /search guard caught the
    # follow-on call. Apply the same guard here so neither endpoint
    # wastes quota on prompt-text leaks.
    # R-F311 (2026-05-11): brandify hostname inputs first.
    if name and _DOMAIN_TOKEN_RE.search(name):
        try:
            from .web_explorer import brandify_query as _brand
            brandified = _brand(name)
            if brandified and brandified != name:
                name = brandified
        except Exception:
            pass
    if not _looks_like_entity_name(name):
        logger.info(
            "_opensanctions_match: rejecting non-entity input %r "
            "(looks like a prompt fragment / search query, not a name)",
            name[:80],
        )
        return []
    # R-F469 (2026-05-14): OpenSanctions circuit breaker. Free-tier is
    # 1 req/sec; under a 429 storm pre-R-F469 every match() call still
    # hit the upstream → quota drained, latency spiked, every screen
    # fired BUT returned empty results silently. The breaker now opens
    # after 3 consecutive failures and cools for 5 min. When OPEN we
    # short-circuit return [] without an HTTP call — caller already
    # treats empty results as "no match", so behaviour from caller's
    # POV is unchanged except faster + no quota burn.
    from .circuit_breaker import get_breaker as _r469_get_breaker
    _r469_breaker = _r469_get_breaker(
        "opensanctions.org",
        failure_threshold=3,
        cooldown_seconds=300,
    )
    if _r469_breaker.is_open():
        logger.info("R-F469: OpenSanctions breaker OPEN — skipping match() for %r", name[:80])
        return []
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
                _r469_breaker.record_failure(reason="auth")
                return []
            if resp.status_code == 429:
                logger.warning("OpenSanctions rate-limited (free tier: 1 req/sec). "
                               "Set OPENSANCTIONS_API_KEY for unlimited access.")
                _r469_breaker.record_failure(reason="rate_limit")
                return []
            if resp.status_code != 200:
                logger.debug("OpenSanctions match failed: %s %s", resp.status_code, resp.text[:200])
                _r469_breaker.record_failure(reason="server" if resp.status_code >= 500 else "timeout")
                return []
            data = resp.json()
            results = (data.get("responses", {}) or {}).get("q1", {}).get("results", [])
            _r469_breaker.record_success()
            return results or []
    except httpx.HTTPError as e:
        logger.warning("OpenSanctions request error: %s", e)
        _r469_breaker.record_failure(reason="timeout")
        return []


async def _opensanctions_search(query: str, limit: int = 5) -> list[dict]:
    """Free-text search against OpenSanctions when /match returns nothing."""
    # F73 fix 2026-04-28: production trace 10:13:40 hit /search/default
    # with `q=HIGH-STAKES and you are NOT 100% confident, state what you
    # would need to confirm — do NOT fabricate verifiable facts...` —
    # a fragment of ARIA's own system prompt extracted by some upstream
    # entity-extraction regex. OpenSanctions returned 400 (query too
    # long / not name-shaped). Same guard as screen_with_aliases now
    # gates the search endpoint too, so the only way bad input reaches
    # OpenSanctions is via direct internal call sites we control.
    # R-F311 (2026-05-11): brandify hostname inputs first.
    if query and _DOMAIN_TOKEN_RE.search(query):
        try:
            from .web_explorer import brandify_query as _brand
            brandified = _brand(query)
            if brandified and brandified != query:
                query = brandified
        except Exception:
            pass
    if not _looks_like_entity_name(query):
        logger.info(
            "_opensanctions_search: rejecting non-entity input %r "
            "(looks like a prompt fragment / search query, not a name)",
            query[:80],
        )
        return []
    # R-F469: same breaker as match() — single host, single quota pool.
    from .circuit_breaker import get_breaker as _r469_get_breaker
    _r469_breaker = _r469_get_breaker(
        "opensanctions.org",
        failure_threshold=3,
        cooldown_seconds=300,
    )
    if _r469_breaker.is_open():
        logger.info("R-F469: OpenSanctions breaker OPEN — skipping search() for %r", query[:80])
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                OPENSANCTIONS_SEARCH,
                params={"q": query, "limit": limit},
                headers=_opensanctions_headers(),
            )
            if resp.status_code == 401:
                logger.error("OpenSanctions auth failed on search — check OPENSANCTIONS_API_KEY")
                _r469_breaker.record_failure(reason="auth")
                return []
            if resp.status_code == 429:
                logger.warning("OpenSanctions rate-limited on search — set OPENSANCTIONS_API_KEY for higher quota")
                _r469_breaker.record_failure(reason="rate_limit")
                return []
            if resp.status_code != 200:
                _r469_breaker.record_failure(reason="server" if resp.status_code >= 500 else "timeout")
                return []
            data = resp.json()
            _r469_breaker.record_success()
            return data.get("results", []) or []
    except httpx.HTTPError as e:
        logger.warning("OpenSanctions search error: %s", e)
        _r469_breaker.record_failure(reason="timeout")
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

    # R-F335 (2026-05-11): match-path transparency. Operator on the
    # Swisscraft Aviation DD 22:29 saw a HARD_STOP based on a Michele
    # Zagaria SDN match but had no way to verify HOW the query reached
    # Zagaria. Now we capture:
    #   - sdn_entry_id: raw OpenSanctions ID for direct lookup
    #   - match_field: which field on the SDN entry matched
    #     (primary_name / alias / linked_entity / weak_match)
    #   - matched_token: the actual SDN field value that matched
    #   - all_names: every name/alias on the SDN entry for inspection
    sdn_entry_id = raw.get("id") or ""
    all_aliases = (props.get("alias") or [])
    all_names = list(props.get("name") or []) + all_aliases

    # Determine which field on the SDN record best explains the match.
    # The OpenSanctions /match API doesn't tell us directly — infer
    # from string similarity to each candidate string.
    qlower = (queried_name or "").lower().strip()
    match_field = "weak_match"
    matched_token = candidate_name
    if qlower:
        best_sim = 0.0
        for nm in (props.get("name") or []):
            _s = _similarity(queried_name, nm)
            if _s > best_sim:
                best_sim = _s
                matched_token = nm
                match_field = "primary_name"
        for al in all_aliases:
            _s = _similarity(queried_name, al)
            if _s > best_sim:
                best_sim = _s
                matched_token = al
                match_field = "alias"

    # Human-readable match path for chat / dashboard.
    match_path = (
        f"query='{queried_name}' → score={round(score, 3)} → "
        f"matched_field={match_field}='{matched_token}' → "
        f"sdn_id={sdn_entry_id or 'unknown'} "
        f"({datasets[0] if datasets else 'OpenSanctions'})"
    )

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
        # R-F335 match-path transparency fields
        "sdn_entry_id": sdn_entry_id,
        "match_field": match_field,
        "matched_token": matched_token,
        "match_path": match_path,
        "all_names_on_sdn": all_names[:10],
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


# R-F49 (2026-05-09): denylist of export-control regimes, compliance
# acronyms, and generic concepts that are NOT entity names but pass the
# all-caps shape heuristic. Past leak (memory session_2026_05_06.md):
# 'ITAR' got pushed to OpenSanctions because its shape is identical to
# 'BAE' — short, all-caps. The defensive guard logged "rejecting" but
# only after the wasted quota call. This denylist short-circuits before
# any other checks, and is the cheapest mitigation for R-F37.
_NON_ENTITY_DENYLIST = frozenset({
    # Export-control regimes
    "itar", "ear", "eccn", "euc", "spire", "sitcl", "ofac", "ofsi",
    "ncnt", "wassenaar", "mtcr", "nsg", "ag", "cwc", "att", "ccl",
    # Sanctions list short-names
    "sdn", "consolidated", "sema", "dfat", "seco", "unsc", "scsanc",
    # Compliance / DD generic concepts
    "kyc", "aml", "ubo", "pep", "edd", "cdd", "cft", "mlro",
    # ARIA-internal acronyms surfaced through prompt fragments
    "dd", "rfq", "rfp", "rfi", "moq", "mou", "lol", "loi", "nda",
    # Geopolitical generic shorthand (not a real entity)
    "nato", "eu", "un", "asean", "ecowas", "sadc", "gcc", "mercosur",
    "cplp", "au", "oas", "osce",
})


def _looks_like_entity_name(s: str) -> bool:
    """Reject inputs that don't look like an entity name before they
    waste OpenSanctions API quota.

    Live failure 2026-04-27 18:24:50: tasks.yaml passes search-query
    strings as `entity:` to deep_research, which forwarded them here.
    Strings like 'sanctions update OFAC SDN EU UN Security Council
    embargo 2026' or 'Arkmurus weekly intelligence summary Angola
    Mozambique...' generated 80+ /match POSTs each, all returning junk.

    R-F49 (2026-05-09): added _NON_ENTITY_DENYLIST short-circuit for
    export-control regime / compliance-concept acronyms (ITAR, OFAC,
    EUC, etc.) that pass the all-caps shape heuristic but are never
    sanctions-screenable entities.

    Heuristics — entity names are typically:
      - NOT in the regime/concept denylist
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
    # R-F49: regime / concept acronym denylist BEFORE other heuristics.
    # Single-token inputs like 'ITAR' or 'OFAC' are checked here; multi-
    # word combinations that contain a denylist token as their entirety
    # ('Cite OFAC' would pass; 'OFAC' alone fails) are rejected.
    if s.lower() in _NON_ENTITY_DENYLIST:
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
    # R-F311 (2026-05-11): when the operator-supplied name is a hostname
    # ("modirumgespi.com"), brandify it BEFORE the entity-shape gate.
    # The 21:11 live DD on modirumgespi.com had the screen rejected
    # twice because `.com` triggered _DOMAIN_TOKEN_RE — the "CLEAN ✅"
    # verdict in chat output was hollow. Now we brandify the hostname
    # and proceed; the original hostname is added as an alias so the
    # screen also probes the literal string.
    original_name = name
    # R-F434 (2026-05-13): track whether the input was a hostname and whether
    # any legal-name corroboration was supplied via known_aliases. Brandified
    # hostnames produce false-positive HARD STOPs when the stripped stem
    # collides by string similarity with an unrelated SDN entry (live
    # examples this conversation: ngast.com → "Oscar Noe MEDINA GONZALEZ",
    # armesavn.com → "SHAZAND PETROCHEMICAL"). Until the orchestrator
    # re-screens with a verified legal name from the crawl/registry, any
    # match derived from the brandified stem alone must be capped at
    # AMBER by the classifier — operator preserves the signal without
    # the false HARD STOP. The original hostname is NOT corroboration
    # because we appended it ourselves below.
    _from_brandified_hostname = False
    _brandified_stem: str = ""
    # known_aliases AT ENTRY = caller-supplied legal-name corroboration.
    # Captured before we mutate the list with the original hostname.
    _has_caller_supplied_aliases = bool(known_aliases)
    if name and _DOMAIN_TOKEN_RE.search(name):
        try:
            from .web_explorer import brandify_query as _brand
            brandified = _brand(name)
            if brandified and brandified != name:
                logger.info(
                    "R-F311: screen_with_aliases brandified hostname %r → %r",
                    name[:60], brandified[:60],
                )
                # Add original hostname as alias so we also probe the
                # literal — sometimes the registrant or operating-name
                # field on a sanctions list is the domain.
                known_aliases = list(known_aliases or [])
                if original_name not in known_aliases:
                    known_aliases.append(original_name)
                name = brandified
                _from_brandified_hostname = True
                _brandified_stem = brandified
        except Exception as _be:
            logger.debug("R-F311 brandify failed for %r: %s", name[:60], _be)

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
            "original_name": original_name,
        }

    targets = [name] + (known_aliases or [])
    targets = [t for t in targets if t and len(t.strip()) >= 2 and _looks_like_entity_name(t)]
    if not targets:
        return {"error": "no valid names to screen"}

    # R-F434 (2026-05-13): the set of targets that originate from the
    # brandified hostname (brandified stem + original hostname). Matches
    # surfaced via these targets ARE NOT corroborated by a verified legal
    # name, so the classifier must cap them at AMBER. Caller-supplied
    # known_aliases (legal-name corroboration) are NOT in this set.
    _hostname_origin_targets: set[str] = set()
    if _from_brandified_hostname:
        _hostname_origin_targets.add(name)  # brandified stem (already mutated)
        _hostname_origin_targets.add(original_name)  # raw hostname

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
        _alias = r.get("alias_screened")
        _is_hostname_origin = _alias in _hostname_origin_targets
        for m in r.get("matches", []):
            # R-F434: tag origin so the classifier can cap severity
            # at AMBER for brandified-hostname-derived false positives.
            if _is_hostname_origin:
                m["_from_brandified_hostname"] = True
                m["_brandified_stem"] = _brandified_stem
            # R-F449 (2026-05-13) — tag corroboration on EVERY surfaced
            # match, not only hostname-origin ones. Pre-R-F449 R-F436
            # and R-F437 comments claimed "passing the entity name as
            # known_aliases sets _has_caller_supplied_aliases=True per
            # match" but in fact the flag was ONLY written for
            # hostname-origin matches because the assignment sat inside
            # the `if _is_hostname_origin:` guard. Behaviourally moot
            # today (the R-F434 cap only fires when the brandified-
            # hostname tag is also set) but a future non-hostname cap
            # reading the same flag would silently no-op. Move out so
            # the tag reflects reality.
            m["_has_caller_supplied_aliases"] = _has_caller_supplied_aliases
            # R-F444 — deprecated alias retained for one release for
            # any consumer still reading the old key.
            m["_has_legal_name_corroboration"] = _has_caller_supplied_aliases
            all_matches.append(m)
    # Dedup by candidate name + list
    seen = set()
    deduped = []
    for m in sorted(all_matches, key=lambda x: -x["score"]):
        key = (m.get("name", "").lower(), m.get("list", ""))
        if key in seen: continue
        seen.add(key)
        deduped.append(m)


    # R-F996 — wire to brain
    wire_success(
        module="sanctions",
        summary="Sanctions screening",
        source_id="sanctions:R-F996",
    )
    return {
        "name": name,
        "aliases_checked": targets,
        "matches": deduped[:15],
        "top_score": worst.get("top_score", 0),
        "blocked": worst.get("blocked", False),
        "match_count": len(deduped),
        "per_alias_results": all_results,
        "disclaimer": worst.get("disclaimer"),
        # R-F434: visibility hooks for renderers and chat output.
        "from_brandified_hostname": _from_brandified_hostname,
        "brandified_stem": _brandified_stem,
        # R-F444 (2026-05-13) — flag renamed to honest name. The old
        # `has_legal_name_corroboration` overstated the guarantee:
        # the flag was True whenever ANY known_aliases were passed,
        # including the R-F312 brandified-re-fire path which passes
        # the raw hostname as an alias (not a verified legal name).
        # New canonical key: `has_caller_supplied_aliases`. Old key
        # preserved as a deprecated alias for one release.
        "has_caller_supplied_aliases": _has_caller_supplied_aliases,
        "has_legal_name_corroboration": _has_caller_supplied_aliases,  # deprecated
    }
