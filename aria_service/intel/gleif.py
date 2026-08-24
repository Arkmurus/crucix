"""GLEIF (Global Legal Entity Identifier) — free, key-less authoritative lookup.

R-F1876 (direct-source-first DD R2): the GLEIF public API (https://api.gleif.org)
returns AUTHORITATIVE registration data — legal name, LEI, jurisdiction (ISO2),
the home-registry "registered as" id, entity + registration status, legal
address — for any entity holding an LEI. Free, no API key, global.

Independence path (CLAUDE.md §6): authoritative entity facts with NO web search
and NO paid vendor. Critically, it supplies the REAL registration data ARIA must
never invent — cf. the 2026-04-09 incident where ARIA fabricated a Modirum Gespi
company number / NACE codes / address. Matching is deliberately CONSERVATIVE
(strict normalized-name agreement) so a near-miss never injects another entity's
data — a wrong "authoritative" fact is as dangerous as a fabricated one.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("aria.gleif")

_API = "https://api.gleif.org/api/v1/lei-records"
_TIMEOUT = 8.0  # MUST stay under the DD layer budgets

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(ltd|limited|plc|llc|llp|inc|incorporated|corp|corporation|co|company|gmbh|ag|"
    r"sa|s\.a|sarl|srl|spa|s\.p\.a|bv|nv|oy|ab|as|lda|unipessoal|ltda|pte|pty|kft|"
    r"sl|s\.l|sas|sasu|ug|kg|ohg|aps|sro|s\.r\.o)\b",
    re.IGNORECASE,
)


def _norm(name: str) -> str:
    """Normalise a company name for comparison: lowercase, drop legal-form
    suffixes + punctuation, collapse whitespace."""
    s = (name or "").lower()
    s = re.sub(r"[.,\-/&]", " ", s)
    s = _LEGAL_SUFFIX_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> set[str]:
    return {t for t in _norm(name).split() if len(t) > 1}


def _wire_lei_fail(detail: str) -> None:
    """R-F2105 (ARIA brain DD) §21a — surface a GENUINE GLEIF/LEI lookup failure to
    the brain so an API outage that returns [] isn't read as 'no entity found'.
    Caller still gets [] for back-compat. Best-effort."""
    try:
        from .engine_wiring import wire_failure
        wire_failure(module="gleif", detail=str(detail)[:200],
                     gap_type="engine_failure", source="gleif.search_lei")
    except Exception:
        pass


async def search_lei(name: str, *, country: str | None = None, limit: int = 5,
                     timeout: float = _TIMEOUT) -> list[dict]:
    """Look up legal entities by name on GLEIF. Best-effort: returns [] on any
    failure. Each hit: {lei, legal_name, jurisdiction, status,
    registration_status, registered_as, legal_form, category, legal_address,
    source_url}."""
    q = (name or "").strip()
    if not q:
        return []
    try:
        import httpx
    except Exception:
        return []
    params = {"filter[entity.legalName]": q, "page[size]": str(max(1, min(limit, 10)))}
    if country:
        cc = (country or "").upper().strip()[:2]
        if len(cc) == 2:
            params["filter[entity.jurisdiction]"] = cc
    try:
        async with httpx.AsyncClient(  # no-breaker: best-effort authoritative lookup (GLEIF), timeout-bounded, returns []+wire_failure on error
            timeout=timeout,
            headers={"Accept": "application/vnd.api+json",
                     "User-Agent": "AriaIntelligence/1.0 (defence-DD; aria@arkmurus.com)"},
        ) as client:
            r = await client.get(_API, params=params)
            if r.status_code != 200:
                logger.debug("GLEIF HTTP %s for %r", r.status_code, q[:60])
                _wire_lei_fail(f"GLEIF LEI lookup HTTP {r.status_code} for {str(q)[:60]}")
                return []
            data = r.json().get("data") or []
    except Exception as e:
        logger.debug("GLEIF lookup failed for %r: %s", q[:60], e)
        _wire_lei_fail(f"GLEIF LEI lookup FAILED ({type(e).__name__}) for {str(q)[:60]}")
        return []

    out: list[dict] = []
    for rec in data[:limit]:
        attr = (rec or {}).get("attributes") or {}
        ent = attr.get("entity") or {}
        reg = attr.get("registration") or {}
        legal_name = ((ent.get("legalName") or {}).get("name") or "").strip()
        if not legal_name:
            continue
        la = ent.get("legalAddress") or {}
        addr = ", ".join(p for p in [
            " ".join(la.get("addressLines") or []),
            la.get("city"), la.get("region"), la.get("postalCode"), la.get("country"),
        ] if p)
        lei = attr.get("lei") or rec.get("id") or ""
        out.append({
            "lei": lei,
            "legal_name": legal_name,
            "jurisdiction": (ent.get("jurisdiction") or "").upper() or None,
            "status": (ent.get("status") or "").upper(),               # ACTIVE / INACTIVE
            "registration_status": (reg.get("status") or "").upper(),  # ISSUED / LAPSED / RETIRED
            "registered_as": ent.get("registeredAs") or "",
            "legal_form": (ent.get("legalForm") or {}).get("id") or "",
            "category": ent.get("category") or "",
            "legal_address": addr,
            "source_url": f"https://search.gleif.org/#/record/{lei}" if lei else "https://search.gleif.org",
        })
    # R-F2105 §21a — wire the successful LEI lookup so the brain knows GLEIF was
    # reachable (distinguishes a real 'no match' from an outage []).
    try:
        from .engine_wiring import wire_success
        wire_success(module="gleif",
                     summary=f"GLEIF LEI lookup OK for {str(name)[:60]} ({len(out)} result(s))",
                     entity_name=str(name)[:120])
    except Exception:
        pass
    return out


async def fetch_parents(lei: str, *, timeout: float = _TIMEOUT) -> dict:
    """R-F4294 — the direct and ultimate parent GLEIF reports for `lei`.

    OC-7 asks for "parent, subsidiaries, affiliates and ultimate parent" and its
    pass condition is "a relationship authority returns the direct and ultimate
    parent, OR STATES THAT NONE IS". GLEIF answers both halves, free and
    key-less, and C-248 recorded that nothing here fetched them — so OC-7 was
    unanswerable for want of a capability, not for want of a reader.

    THE THREE-WAY DISTINCTION IS THE POINT and it must never be collapsed:

      * `/direct-parent` 200            -> a parent exists and is named
      * `/direct-parent-reporting-exception` 200
                                        -> the authority STATES none is reported,
                                           with a reason (NO_KNOWN_PERSON,
                                           NON_CONSOLIDATING, NO_LEI, ...)
      * 404 on BOTH                     -> the authority said NOTHING

    Reading the third as the second would report "no parent" for an entity GLEIF
    has no statement about. `checked` is therefore True only when GLEIF actually
    answered something; the caller must treat False as unknown, never as clear.

    Best-effort like the rest of this module: never raises, returns the same
    shape on every path so a consumer cannot be surprised by a missing key.
    """
    out: dict = {
        "checked": False, "lei": (lei or "").strip(),
        "direct": None, "ultimate": None,
        "direct_exception": None, "ultimate_exception": None,
        "source_url": "", "error": "",
    }
    code = out["lei"]
    if not code:
        out["error"] = "no LEI"
        return out
    out["source_url"] = f"{_API}/{code}"
    try:
        import httpx
    except Exception:
        out["error"] = "httpx unavailable"
        return out

    async def _one(client, path: str):
        try:
            r = await client.get(f"{_API}/{code}/{path}")
            if r.status_code == 200:
                return r.json().get("data")
            if r.status_code == 404:
                return None            # a real "nothing here", not a failure
            return {"_http": r.status_code}
        except Exception as e:  # noqa: BLE001 — best-effort, never raises
            return {"_err": f"{type(e).__name__}"}

    try:
        import asyncio
        async with httpx.AsyncClient(  # no-breaker: free key-less authority (GLEIF), timeout-bounded, degrades to checked=False
            timeout=timeout,
            headers={"Accept": "application/vnd.api+json",
                     "User-Agent": "AriaIntelligence/1.0 (defence-DD; aria@arkmurus.com)"},
        ) as client:
            dp, up, dx, ux = await asyncio.gather(
                _one(client, "direct-parent"),
                _one(client, "ultimate-parent"),
                _one(client, "direct-parent-reporting-exception"),
                _one(client, "ultimate-parent-reporting-exception"),
            )
    except Exception as e:
        logger.debug("GLEIF parent fetch failed for %s: %s", code, e)
        _wire_lei_fail(f"GLEIF parent lookup FAILED ({type(e).__name__}) for {code}")
        out["error"] = type(e).__name__
        return out

    def _entity(node):
        if not isinstance(node, dict) or "_http" in node or "_err" in node:
            return None
        attrs = node.get("attributes") or {}
        name = ((attrs.get("entity") or {}).get("legalName") or {}).get("name") or ""
        got = str(attrs.get("lei") or "").strip()
        return {"lei": got, "name": str(name).strip()} if got else None

    def _exception(node):
        if not isinstance(node, dict) or "_http" in node or "_err" in node:
            return None
        attrs = node.get("attributes") or {}
        category = str(attrs.get("category") or "").strip()
        reasons = attrs.get("reason")
        reason = ", ".join(str(x) for x in reasons) if isinstance(reasons, list)             else str(reasons or "").strip()
        return {"category": category, "reason": reason} if (category or reason) else None

    out["direct"], out["ultimate"] = _entity(dp), _entity(up)
    out["direct_exception"], out["ultimate_exception"] = _exception(dx), _exception(ux)

    # `checked` means GLEIF ANSWERED, not that a parent exists. A transport error
    # on every endpoint leaves it False, which the reader treats as unknown.
    transport_failed = all(
        isinstance(x, dict) and ("_http" in x or "_err" in x) for x in (dp, up, dx, ux))
    out["checked"] = not transport_failed
    if transport_failed:
        out["error"] = "GLEIF relationship endpoints did not answer"
    return out


def best_match(name: str, hits: list[dict]) -> dict | None:
    """Return the single GLEIF hit whose legal name CONFIDENTLY matches `name`,
    or None. Conservative by design: requires either a normalised exact match or
    full token-subset agreement (every distinctive query token present in the
    candidate). This prevents injecting a different entity's authoritative data.
    """
    if not hits:
        return None
    q_norm = _norm(name)
    q_tokens = _tokens(name)
    if not q_tokens:
        return None
    best = None
    best_score = 0.0
    for h in hits:
        cand = h.get("legal_name") or ""
        c_norm = _norm(cand)
        c_tokens = _tokens(cand)
        if not c_tokens:
            continue
        if c_norm == q_norm:
            return h  # exact normalised match — unambiguous
        # R-F1881: accept ONLY when the candidate contains EVERY query token
        # (q ⊆ c) — i.e. the candidate is the query entity, possibly with extra
        # descriptors ("Modirum Gespi" ⊆ "Modirum Gespi International"). The
        # REVERSE (c ⊆ q) is REJECTED: a shorter/generic candidate must never
        # match a more specific query. Real-API check 2026-06-24: "Modirum Gespi"
        # wrongly matched "MODIRUM OÜ" (a different Estonian entity) under the old
        # bidirectional-subset rule — exactly the wrong-entity data injection R2
        # must prevent.
        inter = q_tokens & c_tokens
        jacc = len(inter) / len(q_tokens | c_tokens)
        # R-F1906 (verification V-08): the q⊆c subset bonus applies ONLY to
        # multi-token queries. A SINGLE-token query ("Apple" → {apple}) is a
        # subset of any padded candidate ("Apple International Ltd"), so with the
        # bonus it scored 0.5+0.6=1.1 ≥ 1.0 and matched a DIFFERENT entity — the
        # wrong-entity injection R-F1881 set out to stop, surviving for 1-token
        # names. Single-token queries now match only via the exact-normalised
        # path above (c_norm == q_norm) or genuinely-high jaccard.
        q_in_c = q_tokens.issubset(c_tokens) and len(q_tokens) >= 2
        score = jacc + (0.6 if q_in_c else 0.0)
        if score > best_score:
            best_score, best = score, h
    # Accept only a strong match: every query token present in the candidate
    # AND high token overlap (guards against a candidate padded with extra
    # tokens that drowns out a one-token query).
    if best is not None and best_score >= 1.0:
        return best
    return None
