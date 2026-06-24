# =============================================================================
# ARIA — ARK-DD Orchestrator
# aria_service/intel/dd_orchestrator.py
#
# The 7-layer due-diligence orchestrator. Takes a trigger (entity name
# + optional hints) and walks:
#
#   1. IDENTITY         sanctions + companies_house + ghost-score
#   2. NETWORK          one-hop director graph + PEP + sanctions network
#   3. VERIFICATION     cross-source triangulation + conflict detection
#   4. COMPLIANCE       country risk + export control + regional blocs
#   5. DIGITAL          web search (multilingual) + RAG + neural + press
#   6. SYNTHESIS        ACH + ghost score aggregation + SAR trigger
#   7. ARK-DD REPORT    assembled structured output
#
# COMPOSITIONAL — every existing module is CALLED via its public
# interface. No existing function signature is modified. No existing
# route is removed or changed. This module is purely additive.
#
# SHORT-CIRCUIT RULES (budget protection):
#   - If IDENTITY returns a sanctions hit → skip NETWORK, VERIFICATION,
#     DIGITAL, synthesise immediately as HARD_STOP
#   - If the per-run cost cap is exceeded mid-run → skip remaining
#     layers, mark them SKIPPED, synthesise with what's been collected
#   - If the per-layer timeout fires → mark layer ERROR, continue
#
# PERSISTENCE:
#   - Full report stored in Redis under crucix:dd:report:{run_id} (7 day TTL)
#   - Summary signal appended to intel_ledger
#   - Markdown render appended to mem0 notebook
#   - Trace linked via trace_stream so /trace shows the full lifecycle
#
# CALLABLE FROM:
#   - routes/aria.py POST /api/aria/dd/orchestrate (interactive)
#   - autonomous/tasks.py WEEKLY-DD-WATCHLIST (scheduled)
#   - fly ssh for manual one-shot runs
#
# FEATURE FLAGS (env):
#   ARIA_DD_ORCHESTRATOR_ENABLED (default 1)
#   ARIA_DD_COST_CAP_USD          (default 0.50 per run)
#   ARIA_DD_DEEP_RESEARCH          (default 1 — disable to skip layer 5 LLM)
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .dd_schema import (
    ARKDDReport,
    IdentitySection,
    NetworkSection,
    VerificationSection,
    ComplianceSection,
    DigitalSection,
    SynthesisSection,
    SectionMeta,
    Finding,
    Evidence,
    LayerStatus,
    RiskClassification,
    EntityType,
    weakest_confidence,
)

logger = logging.getLogger("aria.dd_orchestrator")

from .engine_wiring import wired  # R-F1121 — @wired decorator for brain sinks
from .wire import fail_wire  # R-F1789 §21 brain-wiring


def _note_dd_screen_gap(report, who: str, exc: Exception) -> None:
    """R-F1348: a sanctions/entity screen that threw must surface as a visible
    data_gap in the DD report (never silently dropped — a false negative the
    operator can't see is the worst failure mode for a compliance product),
    and be recorded as a brain gap (§21). Best-effort, never raises."""
    try:
        report.identity.data_gaps.append(
            f"Sanctions screen FAILED for {str(who)[:80]} — NOT screened; "
            f"MANUAL screening required ({str(exc)[:100]})"
        )
    except Exception:
        pass
    try:
        import asyncio as _aio
        from . import capability_gaps as _cg
        _aio.get_running_loop().create_task(_cg.record_gap(
            gap_type="module_bug",
            detail=f"dd sanctions screen threw for {str(who)[:80]}: {str(exc)[:120]}",
            source="dd_orchestrator.screen_failure",
        ))
    except Exception:
        pass

# R-F896 — flag-level DD goods-screening severities worth recording to the brain
# as compliance catches. Engine vocab is info/amber/red/hard_stop; routine
# "info"/clean findings are skipped to avoid noise — only real flags are a
# learning signal.
_DD_FLAG_SEVERITIES = {"amber", "red", "hard_stop", "critical", "high", "elevated"}


def _absorb_dd_compliance_catch(report, target, severity, summary, detail=""):
    """R-F896 — record a flag-level DD goods-screening catch (sanctioned weapon
    origin, aggregation pattern, evasion typology, weak end-user, MOU gate) to
    the brain so ARIA learns from her own compliance flags. The success side of
    these engines was dark; R-F892 wired eliminated_weapons and R-F891 wired
    their failure side. Fire-and-forget; never blocks DD; never raises."""
    if (severity or "").lower() not in _DD_FLAG_SEVERITIES:
        return
    try:
        from . import brain_hook as _bh
        _entity = (getattr(getattr(report, "identity", None), "legal_name", "")
                   or str((target or {}).get("name", "")))
        coro = _bh.absorb_silent(
            module="dd_orchestrator",
            summary=str(summary)[:200],
            detail=str(detail or "")[:600],
            entity_name=_entity[:120],
            success=True,
            confidence="PROBABLE",
            source_id="dd_goods_screening:R-F896",
        )
    except Exception:
        return
    try:
        _t = asyncio.create_task(coro)  # orchestrate_dd is async → loop present
        _t.add_done_callback(
            lambda t: t.result() if not t.cancelled() and not t.exception() else None
        )
    except Exception:
        # No running loop (e.g. unit test) — close the coro so it doesn't warn.
        try:
            coro.close()
        except Exception:
            pass


# =============================================================================
# CONFIG
# =============================================================================

# ── Jurisdiction inference from phone, address, registration number ──────────

_PHONE_PREFIX_TO_ISO2 = {
    "+421": "SK", "+420": "CZ", "+48": "PL", "+40": "RO", "+36": "HU",
    "+43": "AT", "+49": "DE", "+44": "GB", "+33": "FR", "+34": "ES",
    "+39": "IT", "+90": "TR", "+55": "BR", "+234": "NG", "+971": "AE",
    "+91": "IN", "+350": "GI", "+351": "PT", "+966": "SA", "+962": "JO",
    "+20": "EG", "+254": "KE", "+27": "ZA", "+244": "AO", "+258": "MZ",
    "+233": "GH",
    # +1 is US/CA/Caribbean. Default to US for our deal flow; address
    # keywords will override if the entity is clearly Canadian.
    "+1": "US",
}

_ADDRESS_KEYWORDS_TO_ISO2 = {
    "slovak republic": "SK", "slovensko": "SK", "slovakia": "SK",
    "czech republic": "CZ", "česko": "CZ", "czechia": "CZ",
    "poland": "PL", "polska": "PL", "romania": "RO", "românia": "RO",
    "hungary": "HU", "türkiye": "TR", "turkey": "TR", "brazil": "BR",
    "brasil": "BR", "nigeria": "NG", "united arab emirates": "AE",
    "gibraltar": "GI", "united kingdom": "GB", "england": "GB",
    "india": "IN", "angola": "AO", "mozambique": "MZ",
    "south africa": "ZA", "kenya": "KE", "ghana": "GH",
    "saudi arabia": "SA", "jordan": "JO", "egypt": "EG",
    "france": "FR", "germany": "DE", "deutschland": "DE",
    "spain": "ES", "españa": "ES", "italy": "IT", "italia": "IT",
    "portugal": "PT", "austria": "AT", "österreich": "AT",
    "united states": "US", "usa": "US", "u.s.a.": "US",
    # Common US state markers — any of these in the address strongly
    # implies jurisdiction = US. Fine-grained state detection is the
    # responsibility of registry_adapters._detect_us_state.
    "florida": "US", "delaware": "US", "california": "US",
    "new york": "US", "texas": "US", "nevada": "US", "wyoming": "US",
}

_ISO2_TO_COUNTRY = {v: k.title() for k, v in _ADDRESS_KEYWORDS_TO_ISO2.items()}
_ISO2_TO_COUNTRY.update({
    "SK": "Slovak Republic", "CZ": "Czech Republic", "GB": "United Kingdom",
    "AE": "United Arab Emirates", "ZA": "South Africa", "SA": "Saudi Arabia",
})


# =============================================================================
# URL-TO-ENTITY EXTRACTION (R-F1177)
# =============================================================================

_URL_FETCH_TIMEOUT_S = 15  # max seconds to wait for a single website fetch
_URL_FETCH_MAX_BYTES = 200_000  # cap response body to avoid OOM on huge pages


def _ssrf_safe_url(url: str) -> tuple[bool, str]:
    """R-F1811 — SSRF guard for user-supplied DD URLs. Returns (ok, reason).

    Rejects non-http(s) schemes and any host that resolves to a private,
    loopback, link-local, reserved, multicast or unspecified IP — or a fly
    ``.internal`` / ``localhost`` name — so an attacker-controlled DD website
    cannot make the brain fetch internal services, localhost-trusted endpoints,
    or cloud-metadata. Confirmed exploitable by the R-F1808 IAST run
    (_extract_entity_from_url firing on target.url with no guard).
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse as _up
    try:
        p = _up(url)
    except Exception:
        return (False, "unparseable url")
    if p.scheme not in ("http", "https"):
        return (False, f"scheme {p.scheme!r} not allowed")
    host = (p.hostname or "").strip().lower().rstrip(".")
    if not host:
        return (False, "no host")
    if host == "localhost" or host.endswith(".internal") or host.endswith(".local"):
        return (False, f"internal host {host!r}")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except Exception as e:
        return (False, f"dns resolution failed: {str(e)[:80]}")
    for _info in infos:
        ip = _info[4][0]
        try:
            ipobj = ipaddress.ip_address(ip)
        except ValueError:
            return (False, f"unparseable resolved ip {ip}")
        if (ipobj.is_private or ipobj.is_loopback or ipobj.is_link_local
                or ipobj.is_reserved or ipobj.is_multicast or ipobj.is_unspecified):
            return (False, f"host resolves to non-public ip {ip}")
    return (True, "ok")


async def _extract_entity_from_url(url: str) -> dict:
    """Fetch a URL and extract the most likely company/entity name from its
    HTML <title> tag and visible text. Returns a dict with keys:
      - name: str | None  — the extracted entity name
      - title: str | None — the raw <title> tag content
      - domain: str       — the apex domain (e.g. "myskyegroove.com")
      - snippet: str      — first ~500 chars of visible text (for jurisdiction hints)

    Designed to be called from the orchestrator's identity layer when the
    target has no explicit name but has a website/URL field. Timeout-safe:
    if the fetch hangs, returns a best-effort domain-based name within
    _URL_FETCH_TIMEOUT_S seconds.

    Example:
      Input:  "https://myskyegroove.com"
      Output: {"name": "Skyegroove", "title": "Skyegroove || Nigeria's Leading Aviation Service Provider",
               "domain": "myskyegroove.com", "snippet": "Plot 242 Muhammadu Buhari Way..."}
    """
    from urllib.parse import urlparse as _urlparse

    # 1. Parse the domain as a fallback name
    parsed = _urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    domain = domain.removeprefix("www.").lower()
    # Derive a fallback name from the domain: "myskyegroove.com" → "Skyegroove"
    _fallback_name = domain
    _dot = domain.rfind(".")
    if _dot > 0:
        _base = domain[:_dot]
        # Capitalise: "my-sky-groove" → "My Sky Groove", "myskyegroove" → "Myskyegroove"
        _base = _base.replace("-", " ").replace("_", " ")
        _fallback_name = _base.strip().title()

    result: dict = {
        "name": None,
        "title": None,
        "domain": domain,
        "snippet": "",
        "_fallback_name": _fallback_name,
    }

    # R-F1811 — SSRF guard: the DD `url` is attacker-controlled (target.url /
    # target.website). Never fetch a URL pointing at an internal/loopback/
    # private/link-local host. Blocked → return the domain-derived fallback.
    _ok, _why = _ssrf_safe_url(url)
    if not _ok:
        logger.warning("_extract_entity_from_url: blocked SSRF-unsafe url %r (%s)", url[:120], _why)
        result["name"] = _fallback_name
        return result

    # 2. Fetch the page with a tight timeout. follow_redirects is OFF so we can
    # re-validate every redirect hop (an open redirect to an internal host would
    # otherwise bypass the guard above).
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(
            timeout=_httpx.Timeout(_URL_FETCH_TIMEOUT_S),
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)"},
        ) as _client:
            _cur = url
            _resp = None
            for _hop in range(4):  # initial request + up to 3 validated redirects
                _resp = await _client.get(_cur)
                if _resp.status_code in (301, 302, 303, 307, 308) and _resp.headers.get("location"):
                    _next = str(_httpx.URL(_resp.url).join(_resp.headers["location"]))
                    _rok, _rwhy = _ssrf_safe_url(_next)
                    if not _rok:
                        logger.warning("_extract_entity_from_url: blocked SSRF-unsafe redirect %r (%s)", _next[:120], _rwhy)
                        result["name"] = _fallback_name
                        return result
                    _cur = _next
                    continue
                break
            _resp.raise_for_status()
            _body = _resp.text[:_URL_FETCH_MAX_BYTES]
    except Exception as _fetch_err:
        logger.debug("_extract_entity_from_url fetch failed for %s: %s", url, _fetch_err)
        result["name"] = _fallback_name
        return result

    # 3. Extract <title> tag
    _title_match = re.search(
        r"<title[^>]*>(.*?)</title>", _body, re.IGNORECASE | re.DOTALL
    )
    if _title_match:
        _raw_title = _title_match.group(1).strip()
        result["title"] = _raw_title
        # Clean the title: strip "||", "|", "—", "-" separators and trailing
        # boilerplate like " | Company Name", " || Nigeria's Leading..."
        _clean = re.split(r"\s*\|\||\s*\|\s*|\s*[—–-]\s*", _raw_title)[0].strip()
        # Remove common suffixes like "Home", "Welcome", "Official Website"
        _clean = re.sub(
            r"\s+(?:Home|Welcome|Official\s*Website|Homepage)\s*$",
            "", _clean, flags=re.IGNORECASE,
        ).strip()
        if _clean and len(_clean) > 2:
            result["name"] = _clean

    # 4. Extract a snippet of visible text for jurisdiction hints
    _text_only = re.sub(r"<[^>]+>", " ", _body)
    _text_only = re.sub(r"\s+", " ", _text_only).strip()
    result["snippet"] = _text_only[:500]

    # 5. Fall back to domain-derived name if title didn't yield anything useful
    if not result["name"]:
        result["name"] = _fallback_name

    return result


# R-F1878 — strong refs for fire-and-forget brain signals. The DD critical path
# must NEVER await a brain_hook.absorb: under load the synchronous embed tier is
# GIL-bound and absorb p95 climbed to ~29s, which (a) tripped the brain_hook
# circuit breaker and (b) blocked DD completion, feeding the budget overrun. A
# completion signal is telemetry — fire it in a tracked background task (strong
# ref so it isn't GC'd mid-flight) and let brain_hook's own background tiers +
# circuit breaker absorb it off the DD path.
_DD_SIGNAL_TASKS: set = set()


def _fire_signal(**kwargs) -> None:
    """Fire brain_hook.absorb as a tracked background task — never awaited."""
    try:
        from . import brain_hook as _bh_fs
    except Exception:
        return
    _coro = _bh_fs.absorb_silent(**kwargs)
    try:
        _t = asyncio.create_task(_coro)
        _DD_SIGNAL_TASKS.add(_t)
        _t.add_done_callback(_DD_SIGNAL_TASKS.discard)
    except RuntimeError:
        _coro.close()  # no running event loop (sync/unit context) — close cleanly
    except Exception:
        try:
            _coro.close()
        except Exception:
            pass


def _dd_strip_html(html: str) -> str:
    """R-F1874 — minimal, dependency-free HTML→text (drop script/style, tags,
    entities; collapse whitespace). Mirrors link_investigator's regex approach."""
    h = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"<[^>]+>", " ", h)
    h = re.sub(r"&nbsp;|&amp;|&#\d+;|&[a-z]+;", " ", h)
    return re.sub(r"\s+", " ", h).strip()


# R-F1874 — anchors whose href/text hint at the key DD pages on an entity site.
_DD_KEY_PAGE_RE = re.compile(
    r"about|team|leadership|management|people|board|director|contact|"
    r"service|solution|company|who-?we-?are|our-?(?:team|company|people|story)",
    re.IGNORECASE,
)


async def _mine_entity_website(url: str, *, max_pages: int = 5, budget_s: float = 25.0) -> list[dict]:
    """R-F1874 (direct-source-first) — mine the entity's OWN website as PRIMARY
    DD evidence: the homepage plus key sub-pages (about / team / leadership /
    contact / services). Returns ``[{url, label, title, text}]`` (text capped).

    This is the independence path (CLAUDE.md §6): a company's own site rarely
    blocks the Fly datacenter IP the way Google/Bing/DDG do, so the DD still has
    real content when general web search is 403'd. SSRF-safe (every hop
    re-validated), same-domain only, bounded by ``max_pages`` and a wall-clock
    ``budget_s``. Never raises — returns ``[]`` on any failure (best-effort)."""
    import time as _t
    _t0 = _t.monotonic()
    out: list[dict] = []
    if not url:
        return out
    _ok, _why = _ssrf_safe_url(url)
    if not _ok:
        logger.warning("_mine_entity_website: blocked SSRF-unsafe url %r (%s)", url[:120], _why)
        return out
    from urllib.parse import urlparse as _up, urljoin as _uj
    try:
        import httpx as _httpx
    except Exception:
        return out
    base_host = (_up(url).netloc or "").removeprefix("www.").lower()

    async def _fetch(_client, _u: str):
        if not _ssrf_safe_url(_u)[0]:
            return None
        cur = _u
        for _ in range(4):  # initial + up to 3 SSRF-revalidated redirects
            r = await _client.get(cur)
            loc = r.headers.get("location")
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                nxt = str(_httpx.URL(r.url).join(loc))
                if not _ssrf_safe_url(nxt)[0]:
                    return None
                cur = nxt
                continue
            r.raise_for_status()
            return r.text[:_URL_FETCH_MAX_BYTES]
        return None

    def _title(html: str) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        return (m.group(1).strip() if m else "")[:200]

    try:
        async with _httpx.AsyncClient(
            timeout=_httpx.Timeout(_URL_FETCH_TIMEOUT_S),
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)"},
        ) as client:
            home = await _fetch(client, url)
            if not home:
                return out
            out.append({"url": url, "label": "homepage", "title": _title(home),
                        "text": _dd_strip_html(home)[:4000]})
            # Discover same-domain key sub-pages from homepage anchors.
            seen = {url.split("#")[0].rstrip("/")}
            cand: list[tuple[str, str]] = []
            for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                                 home, re.IGNORECASE | re.DOTALL):
                href, anchor = m.group(1), _dd_strip_html(m.group(2))
                if not (_DD_KEY_PAGE_RE.search(href) or _DD_KEY_PAGE_RE.search(anchor)):
                    continue
                full = _uj(url, href)
                if (_up(full).netloc or "").removeprefix("www.").lower() != base_host:
                    continue
                key = full.split("#")[0].rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                cand.append((full, (anchor or _DD_KEY_PAGE_RE.search(href).group(0)).strip()[:40]))
                if len(cand) >= max_pages - 1:
                    break
            for full, label in cand:
                if _t.monotonic() - _t0 > budget_s:
                    break
                try:
                    body = await _fetch(client, full)
                except Exception:
                    body = None
                if not body:
                    continue
                txt = _dd_strip_html(body)
                if len(txt) < 80:
                    continue
                out.append({"url": full, "label": label, "title": _title(body),
                            "text": txt[:4000]})
    except Exception as e:
        logger.debug("_mine_entity_website failed for %s: %s", url[:120], e)
    return out


# R-F1831 — detect a "name" that is really a URL or bare domain (so it should
# be resolved to an org, not used as the entity name). Matches "https://x.com",
# "x.com", "sub.x.co.uk/path"; rejects real org names ("Modirum Gespi",
# "Acme Corp Ltd") which contain spaces and no domain shape.
_URL_OR_DOMAIN_NAME_RE = re.compile(
    r"^\s*(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:/\S*)?\s*$",
    re.IGNORECASE,
)


def _looks_like_url_or_domain(text: str) -> bool:
    """True if `text` is a URL or bare domain rather than an org name."""
    if not text or not isinstance(text, str):
        return False
    s = text.strip()
    if " " in s and not s.lower().startswith(("http://", "https://")):
        return False  # multi-word → an org name, not a domain
    return bool(_URL_OR_DOMAIN_NAME_RE.match(s))


async def _enrich_target_from_url(target: dict) -> dict:
    """If the target dict has a website/URL but no name, fetch the URL and
    populate name, jurisdiction hints, and address from the page content.

    Called at the top of orchestrate_dd() before any layer runs, so the
    identity layer has a name to work with even when the user only provided
    a URL like 'https://myskyegroove.com'.

    Returns the (possibly enriched) target dict. Never raises — on any
    error, returns the original target unchanged.

    R-F1831: a "name" that is itself a bare URL / domain does NOT count as a
    real org name. The chat intent detector passes a URL DD target through as
    the entity ("modirumgespi.com"), which used to satisfy the early-return
    below — so enrichment was skipped and EVERY registry / people / juris-
    diction layer ran against a dead domain string (live 2026-06-23:
    "modirumgespi.com → directors_found=0, registration=MISSING,
    jurisdiction=MISSING" despite the org being trivially resolvable). Now a
    URL-shaped name is moved into `website` and cleared so the resolver below
    fetches the page <title> and recovers the real organisation name first.
    """
    _existing = (target.get("name") or target.get("entity") or "").strip()
    if _existing and not _looks_like_url_or_domain(_existing):
        return target  # real org name — no enrichment needed
    if _existing and _looks_like_url_or_domain(_existing):
        # Re-route the URL-shaped name into the website slot, clear the name
        # so _extract_entity_from_url resolves the real org from the page.
        target.setdefault("website", _existing)
        target["name"] = ""
        target["entity"] = ""
        logger.info("R-F1831: URL-shaped DD name %r → website (resolving org)", _existing)

    _url = (
        target.get("website")
        or target.get("url")
        or target.get("domain")
        or target.get("query", "")
    )
    if not _url or "." not in _url:
        return target

    # Normalise: prepend https:// if missing
    if not _url.startswith(("http://", "https://")):
        _url = "https://" + _url

    try:
        _info = await _extract_entity_from_url(_url)
        if _info.get("name"):
            target["name"] = _info["name"]
            target["_name_derivation"] = "url_extraction"
        if _info.get("domain"):
            target.setdefault("domain", _info["domain"])
        # If the snippet contains address-like text, pass it as address hint
        _snip = _info.get("snippet", "")
        if _snip and not target.get("address"):
            # Look for common address patterns: "Plot 123", "123 Street", etc.
            _addr_match = re.search(
                r"(?:Plot\s+\d+|No\.?\s*\d+)\s+[A-Za-z\s,]+(?:Abuja|Lagos|London|New\s+York|Dubai|Luanda|Maputo|Brasília)",
                _snip, re.IGNORECASE,
            )
            if _addr_match:
                target["address"] = _addr_match.group(0).strip()
        logger.info(
            "R-F1177 URL enrichment: %s → name=%r domain=%s",
            _url, _info.get("name"), _info.get("domain"),
        )
    except Exception as _enrich_err:
        logger.warning("R-F1177 URL enrichment failed for %s: %s", _url, _enrich_err)

    return target


def _infer_jurisdiction(target: dict, name: str, reg_number: str | None) -> str | None:
    """Infer jurisdiction ISO2 from phone, address, email domain, or reg number format."""
    # 1. Phone prefix (most reliable)
    phone = target.get("phone") or target.get("tel") or ""
    for prefix, iso2 in sorted(_PHONE_PREFIX_TO_ISO2.items(), key=lambda x: -len(x[0])):
        if phone.startswith(prefix):
            return iso2

    # 2. Address keywords
    address = " ".join(filter(None, [
        target.get("address", ""),
        target.get("jurisdiction", ""),
        name,
    ])).lower()
    for keyword, iso2 in _ADDRESS_KEYWORDS_TO_ISO2.items():
        if keyword in address:
            return iso2

    # 3. Email domain → country TLD
    email = target.get("email") or ""
    tld_match = re.search(r'\.([a-z]{2})$', email.lower())
    if tld_match:
        tld = tld_match.group(1)
        _tld_map = {"sk": "SK", "cz": "CZ", "pl": "PL", "ro": "RO", "hu": "HU",
                     "tr": "TR", "br": "BR", "uk": "GB", "de": "DE", "fr": "FR",
                     "es": "ES", "it": "IT", "pt": "PT", "at": "AT", "ae": "AE",
                     "ng": "NG", "in": "IN", "za": "ZA", "ke": "KE"}
        if tld in _tld_map:
            return _tld_map[tld]

    # 4. Registration number format
    if reg_number:
        clean = reg_number.replace(" ", "")
        # German Handelsregister: HRB / HRA / HRG / GnR / PR / VR + digits
        if re.match(r"^HR[ABG]\d", clean, re.IGNORECASE) or re.match(r"^(GnR|PR|VR)\d", clean, re.IGNORECASE):
            return "DE"
        # Slovak IČO: 6-8 pure digits
        if clean.isdigit() and 6 <= len(clean) <= 8:
            # Could be SK or CZ — default SK if address hints
            if any(kw in address for kw in ("sk", "slovak", "bratislava", "košice", "čachtice")):
                return "SK"
            if any(kw in address for kw in ("cz", "czech", "praha", "brno")):
                return "CZ"
            # Default to SK for ambiguous 6-8 digit IDs (most common in our deal flow)
            return "SK"

    # 5. R-F1815: legal-form suffix in entity name → jurisdiction hint.
    return _infer_jurisdiction_from_legal_form(name)


def _infer_jurisdiction_from_legal_form(name: str) -> str | None:
    """Infer jurisdiction from legal-form suffix in entity name.

    Many jurisdictions have unique legal-form suffixes that are strong
    jurisdiction indicators even when no address/phone/email is available.
    All patterns are applied to name.upper(). Order matters: more specific
    patterns before generic ones. No ISO2 appears more than once.

    Returns ISO2 code or None if no legal form is recognised.
    """
    _legal_form_to_iso2: list[tuple[str, str]] = [
        (r"\bUNIPESSOAL\s+LDA\b", "PT"),
        (r"\bLDA\b", "PT"),
        (r"\bLTDA\b", "BR"),
        (r"\bLTD\.?\s*STI\.?\b", "TR"),
        (r"\bA\.?S\.?\b", "TR"),
        (r"\bLIMITED\b", "GB"),
        (r"\bLTD\b", "GB"),
        (r"\bPLC\b", "GB"),
        (r"\bLLP\b", "GB"),
        (r"\bGMBH\b", "DE"),
        (r"\bAG\b", "DE"),
        (r"\bKG\b", "DE"),
        (r"\bSAS\b", "FR"),
        (r"\bSARL\b", "FR"),
        (r"\bEURL\b", "FR"),
        (r"\bS\.?R\.?L\.?\b", "IT"),
        (r"\bS\.?P\.?A\.?\b", "IT"),
        (r"\bS\.?\.?R\.?\.?L\.?\b", "RO"),
        (r"\bS\.?L\.?\b", "ES"),
        (r"\bBV\b", "NL"),
        (r"\bNV\b", "NL"),
        (r"\bSP\b.*\bZ\b.*\bO\b.*\bO\b", "PL"),
        (r"\bS\.?R\.?O\.?\b", "CZ"),
        (r"\bEOOD\b", "BG"),
        (r"\bAD\b", "BG"),
        (r"\bLLC\b", "AE"),
        (r"\bS\.?A\.?\b", "PT"),
    ]
    name_upper = name.upper()
    for pattern, iso2 in _legal_form_to_iso2:
        if re.search(pattern, name_upper):
            return iso2
    return None


# R-F1815: jurisdiction fallback chains. When the primary jurisdiction's
# registry adapter returns None, try related jurisdictions before giving up.
# Keyed by ISO2; value is an ordered list of fallback ISO2 codes to try.
# Language/cultural clusters: Portuguese-speaking, Spanish-speaking, etc.
_JURISDICTION_FALLBACK_CHAINS: dict[str, list[str]] = {
    # Portuguese-speaking: PT -> BR -> AO
    "PT": ["BR", "AO"],
    "BR": ["PT", "AO"],
    "AO": ["PT", "BR"],
    # Eastern Europe: PL -> CZ -> SK -> HU -> RO
    "PL": ["CZ", "SK", "HU", "RO"],
    "CZ": ["SK", "PL", "HU", "RO"],
    "SK": ["CZ", "PL", "HU", "RO"],
    "HU": ["RO", "SK", "CZ", "PL"],
    "RO": ["HU", "SK", "CZ", "PL"],
    # English-speaking: GB -> US
    "GB": ["US"],
    "US": ["GB"],
    # Middle East: AE -> SA
    "AE": ["SA"],
    "SA": ["AE"],
    # Africa: NG -> GH -> KE -> ZA
    "NG": ["GH", "KE", "ZA"],
    "GH": ["NG", "KE", "ZA"],
    "KE": ["NG", "GH", "ZA"],
    "ZA": ["NG", "GH", "KE"],
}


# R-F295: UK-entity detector for post-link-tree CH backfill. The DD identity
# layer's jurisdiction inference fires before the link-tree pulls page text,
# so a UK Ltd/plc/LLP subsidiary referenced only on the corporate website is
# invisible to the CH gate. This helper scans the link-tree output for the
# pair (UK-style entity-name suffix, UK address signal) and returns the
# extracted name + evidence quote so the orchestrator can fire CH manually.
_UK_ENTITY_NAME_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9.&'\- ]{1,80}?\s+"
    r"(?:Ltd|Limited|plc|PLC|p\.l\.c\.|LLP|Llp|UK\s+Ltd))\b",
)
_UK_POSTCODE_RE = re.compile(
    r"\b(?:[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2})\b",
)
_UK_CITY_SIGNALS = (
    "london", "manchester", "birmingham", "edinburgh", "glasgow",
    "bristol", "leeds", "liverpool", "sheffield", "newcastle",
    "cardiff", "belfast", "oxford", "cambridge", "united kingdom",
    "england", "scotland", "wales", "northern ireland",
)


def _detect_uk_entity_in_link_tree(tree) -> tuple[str | None, str]:
    """Scan a LinkTreeResult for a UK-Ltd entity name paired with a UK
    address signal. Returns (entity_name, evidence_quote) on hit, else
    (None, "")."""
    if not tree:
        return None, ""

    # Pool all text we can look at: fused fact values + contexts, plus
    # per-page titles and fact contexts. Keep it bounded.
    haystack_parts: list[str] = []
    for ff in (getattr(tree, "fused_facts", None) or [])[:50]:
        if getattr(ff, "value", ""):
            haystack_parts.append(str(ff.value))
        if getattr(ff, "first_context", ""):
            haystack_parts.append(str(ff.first_context))
    for page in (getattr(tree, "pages", None) or [])[:30]:
        if getattr(page, "title", ""):
            haystack_parts.append(str(page.title))
        for fact in (getattr(page, "facts", None) or [])[:20]:
            if getattr(fact, "value", ""):
                haystack_parts.append(str(fact.value))
            if getattr(fact, "context", ""):
                haystack_parts.append(str(fact.context))

    if not haystack_parts:
        return None, ""

    haystack = " | ".join(haystack_parts)[:60000]
    haystack_lower = haystack.lower()

    # UK address signal required to avoid HK/SG/IN/AU "Limited" false-positives.
    has_uk_address = (
        _UK_POSTCODE_RE.search(haystack) is not None
        or any(sig in haystack_lower for sig in _UK_CITY_SIGNALS)
    )
    if not has_uk_address:
        return None, ""

    # Take the FIRST plausible UK entity-name match.
    for m in _UK_ENTITY_NAME_RE.finditer(haystack):
        name = m.group(1).strip()
        # Filter out obvious non-entity matches: too short, dictionary-only,
        # or pure-lowercase prefix (the regex requires an initial capital
        # already, but defence in depth).
        if len(name) < 6 or len(name) > 100:
            continue
        # Discard if the leading token is a generic English word that
        # frequently precedes "Limited" in marketing text but isn't a name
        # (e.g., "Time Limited", "Stock Limited").
        first_token = name.split()[0].lower()
        if first_token in {
            "time", "stock", "edition", "supply", "supplies",
            "quantity", "quantities", "offer", "offers",
        }:
            continue
        start = max(0, m.start() - 80)
        end = min(len(haystack), m.end() + 80)
        evidence = haystack[start:end].replace("\n", " ").strip()
        return name, evidence

    return None, ""


# R-F301: jurisdiction-by-city detection from link-tree page text. The
# identity layer's `_infer_jurisdiction` runs BEFORE the link-tree fetches
# the website, so a company HQ stated only on its website is invisible.
# This map maps strong city + country tokens → ISO2. Restricted to defence
# / finance / tech / commercial centres that ARIA encounters in DD work.
_LINK_TREE_CITY_TO_ISO2 = {
    # United Kingdom (already covered by UK detector but mirror here for
    # the generic backfill code path)
    "london": "GB", "manchester": "GB", "birmingham": "GB",
    "edinburgh": "GB", "glasgow": "GB", "bristol": "GB",
    "leeds": "GB", "liverpool": "GB",
    "united kingdom": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    # Finland (Modirum HQ — direct miss on the 2026-05-11 DD)
    "helsinki": "FI", "tampere": "FI", "espoo": "FI", "turku": "FI",
    "oulu": "FI", "vantaa": "FI", "finland": "FI",
    # Other Nordic
    "stockholm": "SE", "gothenburg": "SE", "malmö": "SE", "malmo": "SE",
    "sweden": "SE",
    "copenhagen": "DK", "aarhus": "DK", "denmark": "DK",
    "oslo": "NO", "bergen": "NO", "norway": "NO",
    "reykjavík": "IS", "reykjavik": "IS", "iceland": "IS",
    # DACH
    "berlin": "DE", "munich": "DE", "münchen": "DE", "frankfurt": "DE",
    "hamburg": "DE", "stuttgart": "DE", "cologne": "DE", "köln": "DE",
    "germany": "DE", "deutschland": "DE",
    "vienna": "AT", "wien": "AT", "graz": "AT", "salzburg": "AT", "austria": "AT",
    "zurich": "CH", "zürich": "CH", "geneva": "CH", "basel": "CH",
    "bern": "CH", "prilly": "CH", "lausanne": "CH", "switzerland": "CH",
    # Baltic
    "tallinn": "EE", "tartu": "EE", "estonia": "EE",
    "riga": "LV", "latvia": "LV",
    "vilnius": "LT", "lithuania": "LT",
    # Western EU
    "paris": "FR", "lyon": "FR", "marseille": "FR", "toulouse": "FR",
    "france": "FR",
    "amsterdam": "NL", "rotterdam": "NL", "the hague": "NL",
    "eindhoven": "NL", "netherlands": "NL", "holland": "NL",
    "brussels": "BE", "antwerp": "BE", "belgium": "BE",
    "madrid": "ES", "barcelona": "ES", "valencia": "ES", "spain": "ES", "españa": "ES",
    "lisbon": "PT", "porto": "PT", "portugal": "PT",
    "rome": "IT", "milan": "IT", "turin": "IT", "naples": "IT",
    "florence": "IT", "italy": "IT", "italia": "IT",
    "dublin": "IE", "cork": "IE", "ireland": "IE", "eire": "IE",
    # Central / Eastern EU
    "warsaw": "PL", "kraków": "PL", "krakow": "PL", "gdansk": "PL",
    "wrocław": "PL", "wroclaw": "PL", "poland": "PL", "polska": "PL",
    "prague": "CZ", "praha": "CZ", "brno": "CZ", "czech": "CZ", "czechia": "CZ",
    "bratislava": "SK", "košice": "SK", "kosice": "SK", "slovakia": "SK",
    "budapest": "HU", "hungary": "HU",
    "bucharest": "RO", "cluj": "RO", "romania": "RO",
    "sofia": "BG", "bulgaria": "BG",
    "zagreb": "HR", "croatia": "HR",
    "ljubljana": "SI", "slovenia": "SI",
    "belgrade": "RS", "beograd": "RS", "serbia": "RS",
    "skopje": "MK", "macedonia": "MK", "north macedonia": "MK",
    # Americas
    "são paulo": "BR", "sao paulo": "BR", "rio de janeiro": "BR",
    "são josé dos campos": "BR", "sao jose dos campos": "BR",
    "cruzeiro": "BR", "santa rita do sapucaí": "BR",
    "brasília": "BR", "brasilia": "BR", "brazil": "BR", "brasil": "BR",
    "buenos aires": "AR", "argentina": "AR",
    "santiago": "CL", "chile": "CL",
    "lima": "PE", "peru": "PE",
    "bogotá": "CO", "bogota": "CO", "colombia": "CO",
    "mexico city": "MX", "ciudad de mexico": "MX", "guadalajara": "MX",
    "mexico": "MX", "méxico": "MX",
    # USA
    "new york": "US", "washington": "US", "san francisco": "US",
    "los angeles": "US", "houston": "US", "chicago": "US",
    "paeonian springs": "US", "virginia": "US", "california": "US",
    "united states": "US", "usa": "US",
    # Canada
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA",
    "canada": "CA",
    # Middle East / Gulf
    "dubai": "AE", "abu dhabi": "AE", "sharjah": "AE", "fujairah": "AE",
    "united arab emirates": "AE", "uae": "AE",
    "riyadh": "SA", "jeddah": "SA", "saudi arabia": "SA",
    "doha": "QA", "qatar": "QA",
    "kuwait city": "KW", "kuwait": "KW",
    "manama": "BH", "bahrain": "BH",
    "muscat": "OM", "oman": "OM",
    "tel aviv": "IL", "jerusalem": "IL", "haifa": "IL", "israel": "IL",
    # Turkey
    "istanbul": "TR", "ankara": "TR", "izmir": "TR", "türkiye": "TR", "turkey": "TR",
    # Africa
    "lagos": "NG", "abuja": "NG", "nigeria": "NG",
    "nairobi": "KE", "kenya": "KE",
    "cape town": "ZA", "johannesburg": "ZA", "pretoria": "ZA",
    "south africa": "ZA",
    "luanda": "AO", "angola": "AO",
    "accra": "GH", "ghana": "GH",
    # Asia
    "singapore": "SG",
    "tokyo": "JP", "osaka": "JP", "japan": "JP",
    "seoul": "KR", "south korea": "KR",
    "shanghai": "CN", "beijing": "CN", "shenzhen": "CN", "china": "CN",
    "hong kong": "HK",
    "taipei": "TW", "taiwan": "TW",
    "mumbai": "IN", "delhi": "IN", "bangalore": "IN", "bengaluru": "IN",
    "chennai": "IN", "india": "IN",
    # Oceania
    "sydney": "AU", "melbourne": "AU", "canberra": "AU", "brisbane": "AU",
    "australia": "AU",
    "auckland": "NZ", "wellington": "NZ", "new zealand": "NZ",
}

_ISO2_TO_COUNTRY_HINT = {
    "GB": "United Kingdom", "FI": "Finland", "SE": "Sweden",
    "DK": "Denmark", "NO": "Norway", "IS": "Iceland",
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "FR": "France", "NL": "Netherlands", "BE": "Belgium",
    "ES": "Spain", "PT": "Portugal", "IT": "Italy", "IE": "Ireland",
    "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria",
    "HR": "Croatia", "SI": "Slovenia", "RS": "Serbia", "MK": "North Macedonia",
    "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "PE": "Peru",
    "CO": "Colombia", "MX": "Mexico", "US": "United States", "CA": "Canada",
    "AE": "United Arab Emirates", "SA": "Saudi Arabia", "QA": "Qatar",
    "KW": "Kuwait", "BH": "Bahrain", "OM": "Oman", "IL": "Israel",
    "TR": "Turkey", "NG": "Nigeria", "KE": "Kenya", "ZA": "South Africa",
    "AO": "Angola", "GH": "Ghana",
    "SG": "Singapore", "JP": "Japan", "KR": "South Korea", "CN": "China",
    "HK": "Hong Kong", "TW": "Taiwan", "IN": "India",
    "AU": "Australia", "NZ": "New Zealand",
}


def _detect_jurisdiction_in_link_tree(tree) -> tuple[str | None, str]:
    """R-F301: scan a LinkTreeResult for city + country tokens → ISO2.
    Returns (iso2, evidence_quote) of the WINNING jurisdiction on hit,
    else (None, ""). For multi-jurisdiction inference (Modirum has FI HQ
    + BR ops + UAE admin + ...), use `_detect_all_jurisdictions_in_link_tree`."""
    all_juris = _detect_all_jurisdictions_in_link_tree(tree)
    if not all_juris:
        return None, ""
    # Winner is the highest-voted; evidence comes back from the all-list helper
    winner = all_juris[0]
    return winner["iso2"], winner["evidence"]


def _detect_all_jurisdictions_in_link_tree(tree) -> list[dict]:
    """R-F301 multi-jurisdiction follow-up (live observation 2026-05-11
    on modirumgespi.com): the previous winner-only logic picked BR because
    the page emphasises GESPI Brazil, but the Finnish PRH adapter (R-F302)
    couldn't fire because FI was the second-place vote, not first. This
    returns ALL jurisdictions ranked by vote so the caller can try
    every registry adapter in order until one returns data.

    Returns: [{iso2, country, score, evidence}, ...] sorted by score desc.
    Empty list when no detection."""
    if not tree:
        return []

    haystack_parts: list[str] = []
    for ff in (getattr(tree, "fused_facts", None) or [])[:60]:
        if getattr(ff, "value", ""):
            haystack_parts.append(str(ff.value))
        if getattr(ff, "first_context", ""):
            haystack_parts.append(str(ff.first_context))
    for page in (getattr(tree, "pages", None) or [])[:30]:
        if getattr(page, "title", ""):
            haystack_parts.append(str(page.title))
        for fact in (getattr(page, "facts", None) or [])[:25]:
            if getattr(fact, "value", ""):
                haystack_parts.append(str(fact.value))
            if getattr(fact, "context", ""):
                haystack_parts.append(str(fact.context))
    if not haystack_parts:
        return []

    haystack = " | ".join(haystack_parts)[:80000]
    haystack_lower = haystack.lower()

    votes: dict[str, int] = {}
    for token, iso2 in _LINK_TREE_CITY_TO_ISO2.items():
        if token in haystack_lower:
            weight = 2 if token == _ISO2_TO_COUNTRY_HINT.get(iso2, "").lower() else 1
            votes[iso2] = votes.get(iso2, 0) + weight
    if not votes:
        return []

    out: list[dict] = []
    for iso2, score in sorted(votes.items(), key=lambda kv: -kv[1]):
        # Require at least 2 points to make the list — discards single
        # incidental mentions.
        if score < 2:
            continue
        # Evidence quote — first matching token's surrounding text
        evidence = ""
        for token, t_iso2 in _LINK_TREE_CITY_TO_ISO2.items():
            if t_iso2 != iso2:
                continue
            idx = haystack_lower.find(token)
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(haystack_lower), idx + len(token) + 60)
                evidence = haystack[start:end].replace("\n", " ").strip()
                break
        out.append({
            "iso2": iso2,
            "country": _ISO2_TO_COUNTRY_HINT.get(iso2, iso2),
            "score": score,
            "evidence": evidence,
        })
    return out


def _brandify_name_for_search(name: str, target: dict) -> str:
    """R-F299: derive a search-friendly brand string from a name that may
    have been set from a hostname by the R-F153 fallback. If the name was
    NOT derived from a domain, return as-is.

    Examples:
      "modirumgespi.com"          → "modirumgespi"
      "duma-engineering.com"      → "duma engineering"
      "f3ir.uk"                   → "f3ir"
      "Modirum GESPI"             → "Modirum GESPI"   (untouched — has space)
    """
    if not name:
        return name
    derivation = target.get("_name_derivation") or ""
    looks_like_host = (
        "." in name
        and " " not in name
        and not name.startswith("(")
    )
    if not (looks_like_host or "hostname" in derivation):
        return name
    # Strip TLD (the last .xx / .xxx)
    stripped = re.sub(r"\.[A-Za-z]{2,6}$", "", name)
    # Hyphens and underscores become spaces
    stripped = re.sub(r"[-_]+", " ", stripped)
    # Collapse repeated whitespace
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped or name


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


DEFAULT_COST_CAP_USD = _env_float("ARIA_DD_COST_CAP_USD", 0.50)
DEFAULT_LAYER_TIMEOUT_S = _env_int("ARIA_DD_LAYER_TIMEOUT_S", 90)
DEEP_RESEARCH_ENABLED = (os.getenv("ARIA_DD_DEEP_RESEARCH", "1") or "1").strip() not in ("0", "false", "no", "off")
ORCHESTRATOR_ENABLED = (os.getenv("ARIA_DD_ORCHESTRATOR_ENABLED", "1") or "1").strip() not in ("0", "false", "no", "off")

REPORT_REDIS_KEY = "crucix:dd:report:{run_id}"
REPORT_INDEX_KEY = "crucix:dd:report_index"
# R-F877 (2026-05-25) — DD report bodies + index NO LONGER EXPIRE.
# Was `7 * 24 * 3600` (7-day TTL). A DD verdict is a compliance artifact
# (HARD_STOP / SAR-trigger) and knowledge — CLAUDE.md §7 is binding: "No TTL
# on knowledge. No eviction. Overflow → cold storage, never delete." The
# 7-day TTL also broke the R-F573 version-diff: a re-DD 8+ days after the
# previous run found the prior body aged out (the R-F575 cold tier only
# archived the INDEX entry, not the body) so it silently produced diff=None.
# The store is file/SQLite-backed (Upstash cancelled 2026-05-12), so there is
# no RAM-pressure reason to expire — same infinite-memory model as the
# knowledge store. `ex=None` → set_json writes with no expiry. Name kept
# (12 call sites) but semantically "persist forever".
REPORT_TTL_SECONDS = None


# =============================================================================
# LAYER RUNNERS — each layer is a coroutine that fills a section of the report
# =============================================================================

async def _run_identity_person(
    target: dict,
    report: ARKDDReport,
) -> bool:
    """Layer 1 (person mode) — Identity for a natural person.

    Runs:
      1. Name resolution → variant set (transliteration, short forms,
         particle handling, initials)
      2. Multi-variant sanctions screen — each variant is screened and
         matches are aggregated. Severity = worst across variants.
      3. PEP / ICC / Interpol topic classification from the match data.
      4. Role extraction from any supplied free-text context (title,
         organisation, nationality) so the synthesis layer has context.

    No Companies House, no CUI, no ghost score — those are company-only.
    Returns True on hard-stop (active sanctions hit).
    """
    t0 = time.time()
    report.identity.meta.started_at = datetime.now(timezone.utc).isoformat()

    name = (target.get("name") or target.get("entity") or target.get("query", "")).strip()
    nationality = target.get("nationality") or target.get("nationality_iso2")
    role = target.get("role") or target.get("title")
    organisation = target.get("organisation") or target.get("employer")
    dob = target.get("dob") or target.get("date_of_birth")

    report.identity.entity_name = name
    report.identity.entity_type = "person"
    report.identity.jurisdiction = target.get("jurisdiction") or nationality
    report.identity.jurisdiction_iso2 = target.get("jurisdiction_iso2")
    if role:
        report.identity.declared_activity = f"{role}" + (f" at {organisation}" if organisation else "")

    hard_stop = False

    # ── 1a. Name resolution ──
    try:
        from . import person_resolver
        resolution = person_resolver.resolve(
            name,
            nationality_iso2=target.get("jurisdiction_iso2"),
            max_variants=12,
        )
        report.identity.findings.append(Finding(
            severity="info",
            title=f"Name resolved: {len(resolution.variants)} variants ({resolution.script})",
            detail=(
                f"Canonical: {resolution.canonical}. "
                f"Components: given={resolution.components.given or '-'}, "
                f"particles={resolution.components.particles or '-'}, "
                f"surname={resolution.components.surname or '-'}. "
                f"First 5 variants: {', '.join(resolution.variants[:5])}."
            ),
            source="person_resolver.resolve",
            confidence="CONFIRMED",
        ))
    except Exception as e:
        logger.warning("Identity (person): name resolution failed: %s", e)
        resolution = None
        report.identity.data_gaps.append(f"name resolution failed: {str(e)[:120]}")

    # ── 1b. Multi-variant sanctions screen ──
    #
    # Each variant is screened separately against OpenSanctions. Matches
    # are aggregated and the worst severity wins. Token-overlap filtering
    # in classify_matches rejects short-string collisions (e.g. "Ali"
    # matching hundreds of unrelated sanctioned individuals named Ali).
    all_matches: list = []
    screened_variants: list[str] = []
    try:
        from . import sanctions as _sanc
        from ._sanctions_classify import classify_matches as _cm
        _screen_fn = getattr(_sanc, "screen_with_aliases", None) or getattr(_sanc, "fuzzy_screen", None)

        variants_to_screen: list[str] = []
        if resolution and resolution.variants:
            variants_to_screen = resolution.variants[:6]  # cost cap
        else:
            variants_to_screen = [name]

        for variant in variants_to_screen:
            if not variant or len(variant) < 4:
                continue
            try:
                _scr = await _screen_fn(variant) if _screen_fn else {"matches": []}
                screened_variants.append(variant)
                report.identity.meta.subcalls += 1
                _matches = _scr.get("matches") or []
                # Tag each match with which variant surfaced it for audit
                for _m in _matches:
                    if isinstance(_m, dict):
                        _m.setdefault("_variant", variant)
                all_matches.extend(_matches)
            except Exception as _e:
                logger.warning("Person screen failed for variant '%s': %s", variant, _e)

        # Store the aggregate screen result on the report for renderers.
        # R-F287 (2026-05-11): include explicit per-source verified-status
        # so the LLM renderer can NEVER fabricate "NOT CHECKED" claims for
        # sources OpenSanctions actually queried. screen_succeeded reflects
        # whether AT LEAST ONE variant was screened — if all variants
        # crashed, the screen genuinely failed and per-source UNAVAILABLE
        # is the honest answer.
        from ._sanctions_classify import derive_verified_sources as _dvs
        _screen_ok = len(screened_variants) > 0
        report.identity.sanctions_screen = {
            "matches": all_matches,
            "variants_screened": screened_variants,
            "verified_sources": _dvs(all_matches, screen_succeeded=_screen_ok),
        }

        classified = _cm(all_matches, query_name=name)
        worst = classified["worst_severity"]

        if worst == "hard_stop":
            report.identity.findings.append(Finding(
                severity="hard_stop",
                title=f"{name} on active sanctions list",
                detail=classified["summary"],
                source="sanctions.person_screen",
                confidence="CONFIRMED",
            ))
            hard_stop = True
        elif worst == "red":
            report.identity.findings.append(Finding(
                severity="red",
                title=f"{name} linked to crime/debarment/ICC list",
                detail=classified["summary"],
                source="sanctions.person_screen",
                confidence="PROBABLE",
            ))
        elif worst == "amber":
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"{name} on PEP / adverse-media list",
                detail=classified["summary"] + " — enhanced DD required on individual before contracting.",
                source="sanctions.person_screen",
                confidence="ASSESSED",
            ))
        elif worst == "info":
            report.identity.findings.append(Finding(
                severity="info",
                title=f"{name} on transparency / officeholder register",
                detail=classified["summary"] + " — informational only, not a refusal ground.",
                source="sanctions.person_screen",
                confidence="ASSESSED",
            ))
        else:
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Sanctions + PEP screen CLEAN across {len(screened_variants)} name variant(s)",
                detail=(
                    f"No matches for {name} across OFAC SDN, UK OFSI, EU Consolidated, "
                    f"UN 1267, ICC, Interpol Red Notices, or OpenSanctions PEP data. "
                    f"Variants tested: {', '.join(screened_variants[:8])}. "
                    f"This is a POSITIVE CLEAN result — treat as clearance under "
                    f"standard commercial PDD."
                ),
                source="sanctions.person_screen",
                confidence="CONFIRMED",
            ))
        # ── Family/associate edge detection ─────────────────────────────
        # 2026-04-12: if any sanctions match has family/associate relationships,
        # the subject inherits elevated risk (e.g., spouse of sanctioned person).
        for pm in classified.get("per_match") or []:
            match_obj = next((m for m in all_matches if isinstance(m, dict) and m.get("name") == pm.get("name")), None)
            if not match_obj:
                continue
            rels = match_obj.get("relationships") or []
            for rel in rels[:3]:
                kind = rel.get("kind", "relatedTo")
                target = rel.get("target", "unknown")
                # Family of a sanctioned entity inherits red risk
                inherited_sev = "red" if pm["severity"] in ("hard_stop", "red") else "amber"
                report.identity.findings.append(Finding(
                    severity=inherited_sev,
                    title=f"{name} — {kind} link to {target}",
                    detail=(
                        f"OpenSanctions reports {pm['name']} has a '{kind}' "
                        f"relationship with '{target}'. If the related party is "
                        f"sanctioned, {name} inherits elevated risk via association."
                    ),
                    source="sanctions.family_edges",
                    confidence="ASSESSED",
                ))

    except Exception as e:
        logger.warning("Identity (person): sanctions screen failed: %s", e)
        report.identity.findings.append(Finding(
            severity="amber", title="Sanctions screen failed", detail=str(e)[:200],
            source="sanctions", confidence="UNCERTAIN",
        ))
        report.identity.data_gaps.append("sanctions screen did not complete")

    # ── 1c. Role / context hints ──
    if role or organisation:
        report.identity.findings.append(Finding(
            severity="info",
            title=f"Context: {role or 'unknown role'}{' at ' + organisation if organisation else ''}",
            detail=(
                f"Role and employer were supplied with the query. "
                f"These narrow match disambiguation but do NOT substitute "
                f"for verification — the subject's identity must still be "
                f"cross-referenced against the named organisation's own records."
            ),
            source="person_resolver.context",
            confidence="ASSESSED",
        ))

    if dob:
        report.identity.findings.append(Finding(
            severity="info",
            title=f"DOB supplied: {dob}",
            detail="DOB is the highest-value disambiguator for common names.",
            source="person_resolver.context",
            confidence="CONFIRMED",
        ))

    # ── 1d. Data gaps ──
    if not nationality:
        report.identity.data_gaps.append("nationality not supplied — material disambiguator missing")
    if not dob:
        report.identity.data_gaps.append("DOB not supplied — recommended before contracting")
    if not role and not organisation:
        report.identity.data_gaps.append("role/employer not supplied — weakens variant disambiguation")

    # R-F1836 — domain-ownership (RDAP) as a Layer 1 signal, also for persons
    # who claim a personal/company website. Guarded no-op when no domain given.
    await _check_domain_ownership(target, report)

    report.identity.meta.duration_ms = int((time.time() - t0) * 1000)
    report.identity.meta.status = LayerStatus.OK.value
    return hard_stop


# ── R-F602 — Formalised shell-co indicator-row Findings ──────────────
#
# The 12-indicator ghost scorer in due_diligence_playbooks produces a
# rich breakdown but the orchestrator was only surfacing the TOTAL
# (e.g. "Ghost score 12/20 — RED"). The triggered indicator details
# ("Mail-drop address", "No LinkedIn presence", "Nominee director",
# etc.) were locked inside the identity.ghost_score dict and invisible
# to a client reading the report.
#
# This pure helper turns each triggered indicator into its own Finding
# so the report becomes AUDITABLE — a client can challenge "why is
# this scored 2/2 on mail-drop?" by reading the dedicated row.
#
# Cap at 12 (one per indicator). Severity:
#   ratio >= 0.5  → amber  (substantial weight relative to indicator max)
#   ratio <  0.5  → info   (partial trigger)
# GREEN classification → returns [] (don't clutter clean reports).
def _emit_ghost_indicator_findings(ghost: Any) -> list[Finding]:
    """Pure helper: GhostScoreResult → list of auditable indicator Findings.

    Only fires when overall classification is not GREEN. Each TRIGGERED
    indicator (points > 0) becomes its own Finding with severity tied
    to the indicator's weight ratio. Confidence is PROBABLE because the
    scoring model is heuristic, not Tier-1a authoritative.
    """
    if not ghost or not hasattr(ghost, "classification"):
        return []
    if ghost.classification == "GREEN":
        return []
    out: list[Finding] = []
    triggered = ghost.triggered() if hasattr(ghost, "triggered") else []
    for ind in triggered[:12]:
        ratio = (ind.points / max(ind.max_points, 1)) if ind.max_points else 0.0
        severity = "amber" if ratio >= 0.5 else "info"
        detail = (
            ind.reason
            if ind.reason
            else f"Indicator triggered at {ind.points}/{ind.max_points} weight."
        )
        out.append(Finding(
            severity=severity,
            title=f"Ghost indicator #{ind.id}: {ind.label} ({ind.points}/{ind.max_points})",
            detail=detail,
            source="due_diligence_playbooks.score_ghost_indicators",
            confidence="PROBABLE",
        ))
    return out


# ── R-F600 — Formalised litigation / court-record Findings ────────────
#
# court_records (R-F584) + dd_layer_extensions (R-F584/F585/F586/F587)
# already query CourtListener (US) + BAILII (UK) and stash the result
# inside `report.extensions["by_module"]["court_records"]` — but the
# individual case hits never surface as Findings rows in any report
# section. A client reading the DD report sees "litigation_history"
# under "extensions" (if at all) but no auditable "Smith v. ABC Corp
# · 2024-03-15 · S.D.N.Y." rows.
#
# This pure helper turns each hit into a Finding so the litigation
# history appears as first-class evidence in compliance, alongside
# FATF (R-F601) and shell-co indicators (R-F602).
#
# Severity:
#   ELEVATED (any US federal court hit) → amber headline + amber per-case
#   LOW (non-federal / BAILII only)     → info headline + info per-case
#   NONE                                → returns [] (no clutter)
#
# Confidence: PROBABLE. Court records are Tier-1a authoritative, but
# fuzzy-name matches need human disambiguation (the headline ALWAYS
# notes "verify name match against the entity"). Using PROBABLE
# bypasses the R-5005 gate's CONFIRMED→ASSESSED demotion path.
def _emit_court_record_findings(court_records_block: Any) -> list[Finding]:
    """Pure helper: court_records extension result → list of Findings.

    Inputs: the dict produced by
        dd_layer_extensions.run_court_records_check(...) or its slot in
        run_all_extensions()["by_module"]["court_records"]. Tolerates
        None / empty / missing fields.

    Returns at most 1 + 5 Findings:
      - 1 headline summarising us_count + uk_count
      - up to 5 individual case rows with citation URL inline
    """
    if not court_records_block or not isinstance(court_records_block, dict):
        return []
    hits = court_records_block.get("hits") or []
    # Defensive: upstream contract says list[dict] but tolerate garbage
    if not isinstance(hits, list):
        return []
    severity_label = (court_records_block.get("severity") or "NONE").upper()
    if severity_label == "NONE" or not hits:
        return []
    us_count = int(court_records_block.get("us_count") or 0)
    uk_count = int(court_records_block.get("uk_count") or 0)
    headline_severity = "amber" if severity_label == "ELEVATED" else "info"
    out: list[Finding] = []

    # Headline row
    summary = court_records_block.get("summary") or (
        f"Litigation history: {us_count} US case(s), {uk_count} UK/BAILII case(s)."
    )
    out.append(Finding(
        severity=headline_severity,
        title=f"Litigation history: {us_count} US · {uk_count} UK case(s)",
        detail=(
            summary
            + " VERIFY each case is the same entity (court records are "
            "Tier-1a authoritative but fuzzy-name matches require human "
            "disambiguation against the registered legal name)."
        ),
        source=(
            "dd_layer_extensions.run_court_records_check "
            "[from https://www.courtlistener.com + https://www.bailii.org]"
        ),
        confidence="PROBABLE",
    ))

    # Per-case rows — cap at 5 (the extension dict already enforces this
    # but defending the helper too).
    for hit in hits[:5]:
        if not isinstance(hit, dict):
            continue
        title = (hit.get("title") or "").strip() or "Untitled case"
        court = (hit.get("court") or "").strip() or "?"
        date = (hit.get("date") or "").strip() or "?"
        citation_url = (hit.get("citation_url") or "").strip()
        snippet = (hit.get("snippet") or "").strip()
        jurisdiction = (hit.get("jurisdiction") or "?").strip()
        source_name = (hit.get("source") or "court_records").strip()
        # Federal-court detection — narrowed from run_court_records_check
        # to avoid false-firing on "New York Supreme Court" (which is a
        # state trial court). Federal cases always carry one of the
        # specific federal-jurisdiction markers below.
        federal_kw = ("U.S.", "S.D.", "E.D.", "N.D.", "W.D.", "D.C.",
                      "Cir.", "Federal", "U.S. Supreme")
        is_federal = any(k in court for k in federal_kw)
        # Severity escalates for federal court hits
        case_severity = "amber" if (severity_label == "ELEVATED" and is_federal) else "info"

        detail_bits = [f"Court: {court}", f"Date: {date}"]
        if snippet:
            detail_bits.append(f"Snippet: {snippet[:240]}")
        if citation_url:
            detail_bits.append(f"URL: {citation_url}")
        out.append(Finding(
            severity=case_severity,
            title=f"Litigation [{jurisdiction}] {title[:120]}",
            detail=" · ".join(detail_bits),
            source=(
                f"{source_name} [from {citation_url}]"
                if citation_url else source_name
            ),
            confidence="PROBABLE",
        ))

    return out


# ── R-F610 — Formalised maritime AIS dark-voyage Findings ─────────────
#
# ais_gap_detector (R-F587) already runs inside dd_layer_extensions.
# run_ais_gap_check when the target carries `ais_positions` /
# `vessel_positions` — but the result (vessel_id + total_gaps +
# longest_gap_h + score + signals + severity) sits inside
# report.extensions["by_module"]["ais_gap_detector"] and never
# becomes a Finding row on report.compliance. Same pattern R-F600
# fixed for court records.
#
# This pure helper turns the AIS analysis into auditable Findings
# so dark-voyage indicators surface as first-class evidence in
# compliance alongside FATF (R-F601) + shell-co (R-F602) +
# litigation (R-F600).
#
# Severity:
#   ELEVATED (score ≥50 or any critical gap) → amber headline + amber signals
#   LOW (score 15..49)                       → info everything
#   NONE / no vessel data                    → []
#
# Confidence: PROBABLE. AIS gaps are factual (positions either exist
# or don't), but interpretation requires human assessment of context
# (legitimate maintenance vs sanctions-evasion). Bypasses the R-5005
# CONFIRMED→ASSESSED gate path.
def _emit_ais_findings(ais_block: Any) -> list[Finding]:
    """Pure helper: ais_gap_detector extension result → list of Findings.

    Inputs: dict from dd_layer_extensions.run_ais_gap_check(...) or
        its slot in by_module["ais_gap_detector"]. Tolerates None /
        empty / missing fields.

    Returns at most 1 + 5 Findings:
      - 1 headline: total_gaps + longest_gap_h + score
      - up to 5 individual signal rows
    """
    if not ais_block or not isinstance(ais_block, dict):
        return []
    severity_label = (ais_block.get("severity") or "NONE").upper()
    if severity_label == "NONE":
        return []
    signals_raw = ais_block.get("signals") or []
    if not isinstance(signals_raw, list):
        signals_raw = []
    headline_severity = "amber" if severity_label == "ELEVATED" else "info"
    vessel_id = ais_block.get("vessel_id") or "(unspecified)"
    total_gaps = int(ais_block.get("total_gaps") or 0)
    longest_gap_h = ais_block.get("longest_gap_h") or 0
    score = int(ais_block.get("score") or 0)

    out: list[Finding] = []

    # Headline row
    out.append(Finding(
        severity=headline_severity,
        title=(
            f"Maritime AIS [vessel={vessel_id}]: {total_gaps} gap(s), "
            f"longest {longest_gap_h}h, score {score}/100"
        ),
        detail=(
            f"AIS-gap analysis flagged {total_gaps} transponder gap(s); "
            f"longest gap {longest_gap_h}h; suspicion score {score}/100. "
            "AIS-off periods can indicate legitimate operations (port "
            "stay, antenna failure) OR sanctions-evasion / illicit "
            "trade. VERIFY by cross-referencing port-call records and "
            "AIS-on resumption coordinates against declared voyage."
        ),
        source=(
            "ais_gap_detector.detect_gaps (R-F587) "
            "[from vessel AIS positions in target]"
        ),
        confidence="PROBABLE",
    ))

    # Per-signal rows — cap at 5
    for sig in signals_raw[:5]:
        if not isinstance(sig, str) or not sig.strip():
            continue
        sig_lower = sig.lower()
        # Severity escalates for critical-tagged signals
        is_critical = (
            "critical" in sig_lower
            or "dark" in sig_lower
            or "anomal" in sig_lower
        )
        sig_severity = (
            "amber" if (severity_label == "ELEVATED" and is_critical)
            else "info"
        )
        out.append(Finding(
            severity=sig_severity,
            title=f"AIS signal: {sig[:120]}",
            detail=(
                f"Signal flagged by ais_gap_detector for vessel "
                f"{vessel_id}. Treat as indicator, not verdict — "
                "operator must contextualise."
            ),
            source="ais_gap_detector.detect_gaps (R-F587)",
            confidence="PROBABLE",
        ))

    return out


# ── R-F611 — Formalised cyber/cert-transparency Findings ─────────────
#
# cert_transparency (R-F585) inspects Certificate Transparency logs for
# the counterparty's domain and scores a "shell-broker fingerprint"
# (many apex domains, one issuer, short-lived certs, identical-name
# clusters). Output sits in report.extensions["by_module"][
# "cert_transparency"] but never becomes a Finding row. Same gap
# pattern R-F600 (litigation) / R-F610 (AIS) fixed.
#
# This pure helper turns the CT analysis into auditable Findings.
#
# Severity:
#   ELEVATED (shell_score ≥60) → amber headline + amber per-signal
#   LOW (shell_score 30..59)   → info everything
#   NONE / no domain inferable → []
#
# Confidence: PROBABLE. CT logs are factual (a cert exists or doesn't)
# but the shell-broker FINGERPRINT is a heuristic — needs operator
# corroboration with other indicators.
def _emit_cert_transparency_findings(ct_block: Any) -> list[Finding]:
    """Pure helper: cert_transparency extension result → list of
    Findings. Tolerates None / empty / missing fields.

    Returns at most 1 + 5 Findings:
      - 1 headline: cert_count + apex_count + shell_score
      - up to 5 individual signal rows
    """
    if not ct_block or not isinstance(ct_block, dict):
        return []
    severity_label = (ct_block.get("severity") or "NONE").upper()
    if severity_label == "NONE":
        return []
    signals_raw = ct_block.get("signals") or []
    if not isinstance(signals_raw, list):
        signals_raw = []
    headline_severity = "amber" if severity_label == "ELEVATED" else "info"
    query = ct_block.get("query") or "(unknown)"
    cert_count = int(ct_block.get("cert_count") or 0)
    apex_count = int(ct_block.get("apex_count") or 0)
    issuer_count = int(ct_block.get("issuer_count") or 0)
    shell_score = int(ct_block.get("shell_score") or 0)

    out: list[Finding] = []

    # Headline row
    out.append(Finding(
        severity=headline_severity,
        title=(
            f"Cyber footprint [{query}]: {cert_count} cert(s), "
            f"{apex_count} apex domain(s), {issuer_count} issuer(s), "
            f"shell-score {shell_score}/100"
        ),
        detail=(
            f"Certificate Transparency log analysis for '{query}': "
            f"{cert_count} certificates across {apex_count} apex "
            f"domains, issued by {issuer_count} distinct CA(s). "
            f"Shell-broker fingerprint score: {shell_score}/100. "
            "High scores indicate domain-spinning patterns common in "
            "shell-broker / typo-squat networks. VERIFY by inspecting "
            "the actual cert SAN list before flagging the counterparty."
        ),
        source=(
            "cert_transparency.detect_shell_pattern (R-F585) "
            "[from https://crt.sh log feed]"
        ),
        confidence="PROBABLE",
    ))

    # Per-signal rows — cap at 5
    for sig in signals_raw[:5]:
        if not isinstance(sig, str) or not sig.strip():
            continue
        sig_lower = sig.lower()
        # Critical signals: many apex domains with one issuer / short-lived
        # / identical-name clusters
        is_critical = any(k in sig_lower for k in (
            "shell", "typo", "squat", "cluster", "many_apex", "one_issuer",
            "short_lived", "anomal", "suspicious",
        ))
        sig_severity = (
            "amber" if (severity_label == "ELEVATED" and is_critical)
            else "info"
        )
        out.append(Finding(
            severity=sig_severity,
            title=f"CT signal: {sig[:120]}",
            detail=(
                f"Signal flagged by cert_transparency.detect_shell_pattern "
                f"for query '{query}'. Treat as indicator, not verdict — "
                "operator must corroborate with other DD evidence."
            ),
            source="cert_transparency.detect_shell_pattern (R-F585)",
            confidence="PROBABLE",
        ))

    return out


# ── R-F612 — Formalised ECCN keyword-indicator Findings ──────────────
#
# eccn_lookup (R-F581) is a deterministic keyword→ECCN lookup against
# the BIS Commerce Control List + Wassenaar Munitions List. When the
# extension wrapper (R-F586) returns hits, they sit in
# report.extensions["by_module"]["eccn_lookup"] but never become
# Finding rows on report.compliance. Same gap pattern fixed by
# R-F600/F610/F611 — last of the R-F584-F587 bundle to surface.
#
# Severity:
#   ELEVATED (max_count ≥ 2) → amber headline + amber per-hit
#   LOW (max_count 1)        → info everything
#   NONE                     → []
#
# Confidence: PROBABLE. Per Clause 3 + Clause 14: ECCN match is an
# INDICATOR, not a legal determination. Operator must consult
# licensed export counsel before sharing externally — this is
# baked into the detail text on EVERY hit.
def _emit_eccn_findings(eccn_block: Any) -> list[Finding]:
    """Pure helper: eccn_lookup extension result → list of Findings.

    Tolerates None / empty / missing fields. Each hit becomes a
    Finding citing the BIS / Wassenaar reference URL inline per
    Clause 15.
    """
    if not eccn_block or not isinstance(eccn_block, dict):
        return []
    severity_label = (eccn_block.get("severity") or "NONE").upper()
    if severity_label == "NONE":
        return []
    hits = eccn_block.get("hits") or []
    if not isinstance(hits, list) or not hits:
        return []
    headline_severity = "amber" if severity_label == "ELEVATED" else "info"
    out: list[Finding] = []

    # Headline summarising the hits
    summary = eccn_block.get("summary") or (
        f"{len(hits)} potential ECCN match(es) detected"
    )
    out.append(Finding(
        severity=headline_severity,
        title=f"Export-control indicator: {len(hits)} ECCN match(es)",
        detail=(
            f"{summary} "
            "INDICATOR ONLY — not a legal classification. Operator MUST "
            "consult licensed export counsel before applying for an "
            "export licence or sharing this assessment externally. "
            "Reference URLs link to the public BIS / Wassenaar source."
        ),
        source=(
            "eccn_lookup.lookup_by_keyword (R-F581) "
            "[from BIS Commerce Control List + Wassenaar Munitions List]"
        ),
        confidence="PROBABLE",
    ))

    # Per-hit rows — cap at 5
    for hit in hits[:5]:
        if not isinstance(hit, dict):
            continue
        eccn = (hit.get("eccn") or "").strip() or "?"
        title = (hit.get("title") or "").strip() or "(unnamed)"
        regime = (hit.get("regime") or "").strip()
        ref_url = (hit.get("reference_url") or "").strip()
        match_count = int(hit.get("match_count") or 0)
        match_kw = hit.get("match_keywords") or []
        if not isinstance(match_kw, list):
            match_kw = []
        kw_str = ", ".join(str(k) for k in match_kw[:5])
        # Severity escalates for high match-count
        hit_severity = (
            "amber" if (severity_label == "ELEVATED" and match_count >= 2)
            else "info"
        )

        detail_parts = []
        if regime:
            detail_parts.append(f"Regime: {regime}")
        if kw_str:
            detail_parts.append(f"Matched keywords: {kw_str}")
        detail_parts.append(f"Match count: {match_count}")
        detail_parts.append(
            "INDICATOR — operator consults licensed export counsel "
            "before any licensing decision."
        )
        if ref_url:
            detail_parts.append(f"Reference: {ref_url}")

        out.append(Finding(
            severity=hit_severity,
            title=f"ECCN indicator: {eccn} — {title[:100]}",
            detail=" · ".join(detail_parts),
            source=(
                f"eccn_lookup.lookup_by_keyword (R-F581) "
                f"[from {ref_url}]" if ref_url
                else "eccn_lookup.lookup_by_keyword (R-F581)"
            ),
            confidence="PROBABLE",
        ))

    return out


async def _check_domain_ownership(target: dict, report: ARKDDReport) -> None:
    """R-F1836 — Domain-ownership (RDAP) verification, run as a Layer 1
    (identity) signal.

    Moved here from the digital layer (was section 5e-bis in _run_digital):
      * Domain ownership is an IDENTITY question — who really owns the website an
        entity claims to operate is a who-are-they signal, best surfaced up front.
      * The digital layer is SKIPPED ENTIRELY in quick mode and whenever the
        identity layer hard-stops, so the check (whose own note said it was
        "worth having in quick mode too") never actually ran in those paths.
        Layer 1 always runs and runs BEFORE the hard-stop short-circuit, so the
        RDAP signal now fires in every mode.

    Cheap (one HTTPS call) and guarded — a no-op when the target carries no
    URL/domain. Reads the claimed jurisdiction from report.identity (already set
    earlier in the identity layer), so there is no cross-layer dependency/race
    (the old placement read report.identity.jurisdiction from a different layer).
    """
    name = (
        report.identity.entity_name
        or target.get("name") or target.get("entity")
        or target.get("query") or ""
    )
    _dom_candidate = (
        target.get("website")
        or target.get("domain")
        or target.get("url")
        or ""
    )
    # If the DD target itself looks like a URL (operator typed "run dd on
    # https://f3ir.com/"), pick it up here.
    if not _dom_candidate:
        for fld in ("name", "entity", "query"):
            v = target.get(fld) or ""
            if isinstance(v, str) and ("://" in v or (v.count(".") >= 1 and " " not in v and len(v) < 120)):
                if "://" in v or v.endswith((".com", ".net", ".org", ".io", ".co",
                                             ".ai", ".uk", ".de", ".fr", ".eu",
                                             ".ae", ".sa", ".qa", ".ru", ".cn",
                                             ".tr", ".br", ".ng", ".ma", ".za",
                                             ".ir")):
                    _dom_candidate = v
                    break
    if not _dom_candidate:
        return
    try:
        from . import domain_ownership_verifier as dov
        dom_result = await dov.verify_domain(
            _dom_candidate,
            claimed_entity_name=name,
            claimed_jurisdiction=(
                report.identity.jurisdiction_iso2
                or report.identity.jurisdiction
                or target.get("jurisdiction_iso2")
                or target.get("jurisdiction")
            ),
        )
        if dom_result.get("verified"):
            flags = dom_result.get("flags") or []
            sev = dov.severity_for(dom_result)
            # Preserve the original escalation intent: an entity mismatch, an
            # unregistered domain, or a brand-new registration are hard-stop-
            # worthy identity signals → red (severity_for maps the last to amber).
            if any(f in flags for f in (
                "REGISTRANT_ENTITY_MISMATCH",
                "DOMAIN_NOT_REGISTERED",
                "VERY_RECENTLY_REGISTERED",
            )):
                sev = "red"
            body = dov.render_finding(dom_result)
            report.identity.findings.append(Finding(
                severity=sev,
                title=f"Domain ownership (RDAP): {dom_result['domain']} — flags: {', '.join(flags) or 'none'}",
                detail=body + ("\n" + "\n".join(dom_result.get("signals") or []) if dom_result.get("signals") else ""),
                source="dd_orchestrator.domain_ownership_verifier",
                confidence="CONFIRMED",
            ))
        elif dom_result.get("reason"):
            report.identity.data_gaps.append(
                f"Domain RDAP check inconclusive for {dom_result.get('domain') or _dom_candidate}: {dom_result['reason']}"
            )
    except Exception as e:
        logger.debug("domain_ownership_verifier failed (non-fatal): %s", e)


async def _run_identity(
    target: dict,
    report: ARKDDReport,
) -> bool:
    """Layer 1 — Identity. Returns True if a hard-stop was triggered
    (sanctions hit), signalling the orchestrator to short-circuit."""
    entity_type = target.get("type") or EntityType.UNKNOWN.value
    # Person branch — separate logic path because persons don't have
    # Companies House, CUI, ghost score, or address pattern checks.
    # They DO need name-variant resolution, multi-variant sanctions
    # screening, and PEP classification.
    if entity_type == EntityType.PERSON.value or entity_type == "person":
        return await _run_identity_person(target, report)

    t0 = time.time()
    report.identity.meta.started_at = datetime.now(timezone.utc).isoformat()

    name = target.get("name") or target.get("entity") or target.get("query", "")
    jurisdiction = target.get("jurisdiction")
    jurisdiction_iso2 = target.get("jurisdiction_iso2")
    registration_number = target.get("registration_number")
    # Jurisdiction-specific registration IDs flow into registration_number
    # when the caller didn't supply one explicitly. This keeps the
    # downstream renderer, ghost scorer, and manual-registry hint
    # working on any jurisdiction we recognise.
    if not registration_number:
        for _k in ("cui", "nip", "cnpj", "cvr", "kvk", "siret", "vat"):
            if target.get(_k):
                registration_number = str(target[_k])
                break

    # ── Auto-detect jurisdiction from clues when not provided ──
    if not jurisdiction_iso2:
        jurisdiction_iso2 = _infer_jurisdiction(target, name, registration_number)
        if jurisdiction_iso2:
            jurisdiction = jurisdiction or _ISO2_TO_COUNTRY.get(jurisdiction_iso2, "")
            logger.info("Jurisdiction inferred: %s → %s", jurisdiction_iso2, jurisdiction)

    # ── Also try IČO / ico key as registration number for SK/CZ ──
    if not registration_number:
        for _k in ("ico", "ičo", "IČO", "ICO"):
            if target.get(_k):
                registration_number = str(target[_k]).replace(" ", "")
                if not jurisdiction_iso2:
                    jurisdiction_iso2 = "SK"
                    jurisdiction = "Slovak Republic"
                break

    report.identity.entity_name = name
    report.identity.entity_type = entity_type
    report.identity.jurisdiction = jurisdiction
    report.identity.jurisdiction_iso2 = jurisdiction_iso2
    report.identity.registration_number = registration_number

    # R-F1876 (direct-source-first R2): GLEIF authoritative enrichment. Fill any
    # registration fields the caller didn't supply with AUTHORITATIVE data from
    # the free, global, key-less GLEIF LEI registry — legal name / LEI /
    # jurisdiction / registered-as id / status / legal address. Independent of
    # web search. Matching is CONSERVATIVE (gleif.best_match) so a near-miss can
    # never inject another entity's data — the exact failure mode behind the
    # 2026-04-09 Modirum fabrication incident. Only fills EMPTY fields (never
    # overwrites caller/registry data). Best-effort; never raises.
    if entity_type != EntityType.PERSON.value and name:
        try:
            from . import gleif as _gleif
            _gl_hits = await _gleif.search_lei(name, country=jurisdiction_iso2)
            if not _gl_hits and jurisdiction_iso2:
                _gl_hits = await _gleif.search_lei(name)  # retry without the country filter
            _gl = _gleif.best_match(name, _gl_hits)
            if _gl:
                _filled = []
                if not report.identity.registration_number and _gl.get("registered_as"):
                    report.identity.registration_number = _gl["registered_as"]; _filled.append("reg#")
                if not report.identity.jurisdiction_iso2 and _gl.get("jurisdiction"):
                    report.identity.jurisdiction_iso2 = _gl["jurisdiction"]; _filled.append("jurisdiction")
                if not report.identity.registration_status and _gl.get("status"):
                    report.identity.registration_status = (_gl["status"] or "").lower(); _filled.append("status")
                if not report.identity.registered_address and _gl.get("legal_address"):
                    report.identity.registered_address = _gl["legal_address"]; _filled.append("address")
                report.identity.findings.append(Finding(
                    severity="info",
                    title=f"GLEIF: LEI {_gl.get('lei')} — {_gl.get('legal_name')}",
                    detail=(
                        f"Authoritative GLEIF record (free global LEI registry): legal name "
                        f"'{_gl.get('legal_name')}', jurisdiction {_gl.get('jurisdiction') or '?'}, "
                        f"registered as '{_gl.get('registered_as') or '?'}', entity status "
                        f"{_gl.get('status') or '?'}, LEI registration {_gl.get('registration_status') or '?'}. "
                        f"Source: {_gl.get('source_url')}. Fields filled from GLEIF: "
                        f"{', '.join(_filled) if _filled else 'none (all already known)'}."
                    ),
                    source="gleif.search_lei",
                    confidence="PROBABLE",
                ))
                logger.info("R-F1876 GLEIF match for %r: LEI=%s filled=%s", name[:60], _gl.get("lei"), _filled)
        except Exception as _gl_e:
            logger.debug("R-F1876 GLEIF enrichment failed (non-fatal): %s", _gl_e)

    hard_stop = False

    # Romanian CUI → incorporation-date analyzer. If the caller
    # supplies a CUI (directly, via registration_number on a RO
    # jurisdiction, or in free text extracted by the chat intent
    # detector), run the sequential-CUI analysis and emit a finding.
    # This runs BEFORE the ghost scorer so the orchestrator can
    # surface the CUI-derived incorporation estimate as a first-class
    # identity signal, not just as an internal input to ghost
    # indicator 11.
    if (jurisdiction_iso2 == "RO" or (jurisdiction or "").lower() == "romania") and (target.get("cui") or registration_number):
        try:
            from . import _romanian_cui as _ro_cui
            _analysis = _ro_cui.analyse_cui(target.get("cui") or registration_number)
            if _analysis and _analysis.estimated_incorporation:
                report.identity.incorporation_date = _analysis.estimated_incorporation.isoformat()
                report.identity.findings.append(Finding(
                    severity="info",
                    title=f"Romanian CUI {_analysis.cui} estimates incorporation ≈ {_analysis.estimated_incorporation.isoformat()}",
                    detail=(
                        f"Sequential-CUI analysis places incorporation at "
                        f"{_analysis.estimated_incorporation.isoformat()} "
                        f"(±{_analysis.uncertainty_months} months). "
                        f"Company age: {_analysis.age_months_now} months. "
                        f"{_analysis.notes}. "
                        f"VERIFY against ONRC portal (https://portal.onrc.ro) "
                        f"before relying on this date."
                    ),
                    source="_romanian_cui.analyse_cui",
                    confidence="ASSESSED",
                ))
                # Also cross-check claimed founding year if supplied
                _claimed = target.get("claimed_founding_year")
                if _claimed is not None:
                    _cmp = _ro_cui.compare_claimed_founding(_analysis.cui, int(_claimed))
                    if _cmp["severity"] in ("red", "hard_stop"):
                        report.identity.findings.append(Finding(
                            severity=_cmp["severity"],
                            title=f"Founding-year misrepresentation: claimed {_claimed} vs CUI-estimated {_cmp['estimated_incorporation_year']}",
                            detail=_cmp["detail"],
                            source="_romanian_cui.compare_claimed_founding",
                            confidence="PROBABLE",
                        ))
                        if _cmp["severity"] == "hard_stop":
                            hard_stop = True
        except Exception as e:
            logger.debug("Romanian CUI analysis failed (non-fatal): %s", e)

    # If the caller supplied a registered address (via chat intent or
    # direct API), use it as the initial identity signal. Registry
    # lookup may overwrite it with authoritative data later.
    supplied_address = target.get("registered_address")
    if supplied_address and not report.identity.registered_address:
        report.identity.registered_address = supplied_address
        # Residential-apartment pattern detection. A registered office
        # at a specific apartment number inside a named block is a
        # ghost-indicator signal (indicator 2 — no verifiable physical
        # premises). We add it as an amber finding so the LLM sees it
        # without the orchestrator having to ship an expensive registry
        # lookup first.
        _addr_lower = supplied_address.lower()
        _residential_patterns = (
            "apt.", "apt ", " ap.", " ap ", " ap,", "apartment",
            "flat ", "unit ", "sc. ", "bl. ", "et. ", "etaj ", "floor ",
        )
        if any(p in _addr_lower for p in _residential_patterns):
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"Registered address is a residential apartment",
                detail=(
                    f"'{supplied_address}' — the address decodes to an apartment "
                    f"inside a named block/staircase/floor, not a commercial office. "
                    f"This matches ghost-indicator 2 (no verifiable physical premises). "
                    f"Not a refusal ground on its own; requires verification against "
                    f"the national registry and cross-check against the number of "
                    f"other entities registered at the same address."
                ),
                source="dd_orchestrator.residential_address_pattern",
                confidence="ASSESSED",
            ))

        # ── Virtual-office / mail-drop detection ──
        # Catches CMRA addresses (e.g. North Miami Beach corridor,
        # Cheyenne WY mass-registration), PMB markers, and registered-
        # agent towers. Added 2026-04-17 after the F3 / SERBAN DD
        # learnings — a US LLC at a virtual-office address should
        # always carry an explicit signal.
        try:
            from . import virtual_office_registry
            _vo = virtual_office_registry.check_address(supplied_address)
            if _vo.get("is_virtual_office"):
                _sev = "red" if _vo.get("risk") == "high" else "amber"
                _provider = _vo.get("provider") or "virtual-office / mail-drop"
                _signals = " ".join(_vo.get("signals") or [])
                _notes = _vo.get("notes") or ""
                report.identity.findings.append(Finding(
                    severity=_sev,
                    title=f"Registered address is a virtual office / mail drop",
                    detail=(
                        f"'{supplied_address}' matches a known virtual-office "
                        f"or CMRA pattern: {_provider}. {_signals} {_notes} "
                        f"Operating business is unlikely to be physically at "
                        f"this address. Require counterparty to disclose the "
                        f"actual principal place of business before proceeding."
                    ).strip(),
                    source="dd_orchestrator.virtual_office_detector",
                    confidence="ASSESSED",
                ))
        except Exception as _vo_err:
            logger.debug("Virtual-office check failed (non-fatal): %s", _vo_err)

    # ── FinCEN BOI caveat for US entities ──
    # MOVED OUT of the `if supplied_address` guard 2026-04-17 PM so the
    # caveat also fires on URL-based / name-only DDs where the address
    # may only surface later from the registry adapter. US state registries
    # disclose registered agent + managers but NOT ultimate beneficial
    # ownership. UBO lives in FinCEN BOI filings (Corporate Transparency
    # Act, 2024) — not public.
    _all_address_hint = " ".join(filter(None, [
        (supplied_address or ""),
        (report.identity.registered_address or ""),
        (target.get("address") or ""),
    ])).lower()
    _jur_hint = (
        (target.get("jurisdiction_iso2") or "")
        or (jurisdiction_iso2 or "")
        or (target.get("jurisdiction") or "")
        or (jurisdiction or "")
    ).upper()
    _is_us = (
        _jur_hint in ("US", "USA", "UNITED STATES")
        or any(kw in _all_address_hint for kw in (
            "united states", "usa", "florida", "delaware", "california",
            "new york", "texas", "nevada", "wyoming",
        ))
        # LLC shape in entity name + no other jurisdiction inferred
        or ((name or "").lower().rstrip(".").endswith((" llc", ",llc", " inc", " inc."))
            and not _jur_hint)
    )
    if _is_us:
        report.identity.data_gaps.append(
            "UBO not visible on US public registry — US state Secretary "
            "of State records disclose registered agent + managers only. "
            "Ultimate beneficial ownership lives in FinCEN BOI filings "
            "(Corporate Transparency Act, effective 2024) which are NOT "
            "public. Require the counterparty to provide a copy of its "
            "FinCEN BOI report during KYC before contracting."
        )

    # ── 1a. Sanctions screen (always runs) ──
    #
    # Classification is by BOTH score AND OpenSanctions topic labels.
    # Not every match at score 1.00 is a hard stop — legitimate
    # corporate entities (BAE Systems, Lockheed Martin, Rolls-Royce)
    # routinely hit at 1.00 against transparency data like `corp.state`
    # (state-owned / strategic industry lists), which is NOT a sanction.
    # See _classify_sanctions_match() for the topic → severity mapping.
    try:
        from . import sanctions
        if hasattr(sanctions, "screen_with_aliases"):
            screen = await sanctions.screen_with_aliases(name)
        elif hasattr(sanctions, "fuzzy_screen"):
            screen = await sanctions.fuzzy_screen(name)
        else:
            screen = {"error": "no sanctions module entrypoint"}
            report.identity.data_gaps.append("sanctions module not exposing expected API")

        # R-F287 (2026-05-11) — attach explicit per-source verification
        # status so the renderer NEVER fabricates "NOT CHECKED" gaps for
        # sources OpenSanctions did query. A screen with no "error" key
        # AND with a "matches" array (even empty) counts as succeeded.
        from ._sanctions_classify import (
            classify_matches,
            derive_verified_sources as _dvs_co,
        )
        _matches_for_dvs = screen.get("matches") or []
        # R-F1696: a source-unavailable screen has error="sanctions_source_unavailable"
        # AND source_unavailable=True — it must NOT count as succeeded (else the
        # renderer fabricates verified-clean sources for a screen that never ran).
        _screen_ok_co = (
            not screen.get("error")
            and not screen.get("source_unavailable")
            and isinstance(_matches_for_dvs, list)
        )
        screen["verified_sources"] = _dvs_co(
            _matches_for_dvs, screen_succeeded=_screen_ok_co,
        )

        # R-F740 (2026-05-20) — explicit UK OFSI primary-source check
        # alongside the OpenSanctions aggregate screen. OpenSanctions
        # DOES include UK OFSI data transitively, but UK DD clients
        # need a citation_url anchored at ofsi.gov.uk, not at a
        # third-party aggregator. Pre-R-F740 dd_orchestrate's gap-table
        # listed "OFSI UK sanctions | tool has no OFSI adapter" — a
        # false claim because fcdo_sanctions.lookup() IS wired (live
        # since R-F569+) just not called from this orchestrator path.
        # Fail-open: OFSI failures don't block the OpenSanctions
        # verdict — the screen still completes and we just lose the
        # explicit primary-source citation.
        try:
            from .sources import fcdo_sanctions as _fcdo
            _ofsi_result = await _fcdo.lookup(name)
            _ofsi_hits = (_ofsi_result or {}).get("hits") or []
            if _ofsi_hits:
                _existing = list(screen.get("matches") or [])
                # Normalise OFSI hits into the same shape the
                # downstream classifier expects (name + list label
                # + score).
                for h in _ofsi_hits:
                    _existing.append({
                        "name":   h.get("name") or name,
                        "list":   "UK_OFSI",
                        "score":  float(h.get("score") or 1.0),
                        "regime": h.get("regime") or "",
                        "source": "uk_ofsi",
                        "citation_url": h.get("citation_url") or
                            "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml",
                        "designation_date": h.get("designation_date") or "",
                    })
                screen["matches"] = _existing
            # Always declare uk_ofsi as a verified source when the
            # lookup completed (zero hits is still a verified clean).
            if not (_ofsi_result or {}).get("error"):
                _vs = list(screen.get("verified_sources") or [])
                if "uk_ofsi" not in _vs:
                    _vs.append("uk_ofsi")
                screen["verified_sources"] = _vs
        except Exception as _ofsi_e:
            logger.debug(
                "R-F740: fcdo_sanctions.lookup failed for %s: %s",
                name, _ofsi_e,
            )

        report.identity.sanctions_screen = screen
        report.identity.meta.subcalls += 1

        matches = screen.get("matches") or []
        classified = classify_matches(matches, query_name=name)
        # The overall severity is the worst single match.
        if classified["worst_severity"] == "hard_stop":
            report.identity.findings.append(Finding(
                severity="hard_stop",
                title=f"Subject on active sanctions list",
                detail=classified["summary"],
                source="sanctions.screen_with_aliases",
                confidence="CONFIRMED",
            ))
            hard_stop = True
        elif classified["worst_severity"] == "red":
            report.identity.findings.append(Finding(
                severity="red",
                title=f"Subject linked to crime/debarment/export-risk list",
                detail=classified["summary"],
                source="sanctions.screen_with_aliases",
                confidence="PROBABLE",
            ))
        elif classified["worst_severity"] == "amber":
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"Subject on PEP or adverse-media list",
                detail=classified["summary"] + " — enhanced DD required, not a refusal ground.",
                source="sanctions.screen_with_aliases",
                confidence="ASSESSED",
            ))
        elif classified["worst_severity"] == "info":
            # Build detailed breakdown of each info-level match (datasets + topics)
            _info_matches = classified.get("per_match") or []
            _info_detail_parts = [classified["summary"]]
            for _im in _info_matches[:5]:
                if _im.get("severity") == "info" and not _im.get("noise_filtered"):
                    _ds = ", ".join(_im.get("datasets", [])[:3]) or "unspecified"
                    _tp = ", ".join(_im.get("topics", [])[:3]) or "untagged"
                    _info_detail_parts.append(
                        f"  → {_im.get('name', '?')} (score {_im.get('score', 0):.2f}, datasets: {_ds}, topics: {_tp})"
                    )
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Transparency/state-ownership matches ({len([m for m in _info_matches if m.get('severity') == 'info'])})",
                detail="\n".join(_info_detail_parts) + "\n— informational only, not a refusal ground.",
                source="sanctions.screen_with_aliases",
                confidence="ASSESSED",
            ))
        elif screen.get("source_unavailable") or screen.get("error") == "sanctions_source_unavailable":
            # R-F1696: ZERO matches but the source NEVER ANSWERED. This is the
            # catastrophic false-negative class — pre-fix this branch stamped
            # "Sanctions screen CLEAN — CONFIRMED — treat as clearance" on an
            # entity that was never actually screened (OpenSanctions down/auth/
            # rate-limited/breaker-open). NEVER report an unscreenable entity as
            # clear: surface it as UNVERIFIED + a hard data-gap so the verdict
            # cannot read as a clearance.
            _reasons = ", ".join((screen.get("source_reasons") or [])[:4]) or "source unreachable"
            report.identity.findings.append(Finding(
                severity="amber",
                title="Sanctions screen NOT performed — source unavailable",
                detail=(
                    f"{name} — the sanctions source (OpenSanctions / primary lists) "
                    f"could not be reached ({_reasons}), so NO screen was performed. "
                    f"This is UNVERIFIED — it is NOT a clearance. A re-screen against "
                    f"OFAC SDN / UK OFSI / EU / UN is required before relying on this."
                ),
                source="sanctions.screen_with_aliases",
                confidence="UNCERTAIN",
            ))
            report.identity.data_gaps.append(
                "sanctions screen did not complete — source unavailable "
                "(UNVERIFIED, must re-screen; not a clearance)"
            )
        else:
            # Clean screen — zero matches across the full alias/variant set,
            # AND the source actually answered (screened=True / no error).
            # Emit an explicit INFO-tier finding so consumers can see the
            # screen actually RAN and came back clean. Previously an empty
            # matches list produced no finding at all, which the LLM
            # (correctly) read as "sanctions screen not completed".
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Sanctions screen CLEAN",
                detail=(
                    f"{name} — no matches across OFAC SDN, UK OFSI, EU Consolidated, "
                    f"UN 1267, or OpenSanctions datasets. Fuzzy variants / aliases "
                    f"screened. This is a POSITIVE CLEAN result — treat as clearance "
                    f"under standard commercial DD."
                ),
                source="sanctions.screen_with_aliases",
                confidence="CONFIRMED",
            ))
    except Exception as e:
        logger.warning("Identity: sanctions screen failed: %s", e)
        report.identity.findings.append(Finding(
            severity="amber", title="Sanctions screen failed", detail=str(e)[:200],
            source="sanctions", confidence="UNCERTAIN",
        ))
        report.identity.data_gaps.append("sanctions screen did not complete")

    # ── 1a1. Primary-source parallel screen (SEC, OFAC, OFSI, UN, WB, ACLED) ──
    # Added 2026-04-18 after the DD-depth audit. The OpenSanctions call
    # above is convenient but aggregation-lagged; these six direct primary
    # sources run in parallel and surface findings with canonical
    # citations. Each source is wrapped in an individual try so one
    # failure cannot block the others. ~2-4s added to identity layer
    # worst-case (all six fetched in parallel, cached heavily).
    #
    # R-F569 (2026-05-16) — name-overlap gate on the per-source HARD_STOP
    # emissions below. Pre-R-F569 each adapter's `_hits[0]` was emitted
    # verbatim as a HARD_STOP finding with no similarity check beyond the
    # adapter's own internal fuzzy_filter threshold (0.70 for ofac_sdn —
    # too liberal). Live MVP fire-test 2026-05-16 logged
    #   Embraer S.A. → OFAC SDN "MARANER HOLDINGS LIMITED"  (false)
    #   Aselsan A.S. → OFAC SDN "Guillermo NIEBLAS NAVA"     (false)
    #   Aselsan A.S. → UN SC    "ALI SADDAM HUSSEIN AL-TIKRITI" (false)
    #   Acme Widgets → OFAC SDN "TAMIN KALAYE SABZ ARAS COMPANY" (false on fake input)
    # The gate below reuses _sanctions_classify._tokenize_entity_name /
    # _name_overlap (R-F277 / R-F351) so the per-source path applies the
    # same token-overlap discipline the topic-classifier already uses.
    # A hit is allowed to remain HARD_STOP only when it shares ≥1
    # meaningful 5+ char token with the query OR scores ≥0.95.
    from ._sanctions_classify import (
        _tokenize_entity_name as _r569_tokenize,
        _name_overlap as _r569_overlap,
    )

    def _r569_is_real_sanctions_hit(_hit: dict, _query: str) -> tuple[bool, str]:
        """Return (is_real_match, reason). False if the hit is fuzzy noise."""
        if not isinstance(_hit, dict) or not _query:
            return True, ""  # defensive — don't accidentally suppress
        _cand = (
            _hit.get("name") or _hit.get("primary_name")
            or _hit.get("full_name") or _hit.get("caption") or ""
        )
        if not _cand:
            return True, ""
        # Exact-or-near-exact scores bypass the gate (1.0 = identical
        # normalised form, ≥0.95 = trivial spelling/punctuation drift).
        _score = float(_hit.get("_match_score") or _hit.get("score") or 0.0)
        if _score >= 0.95:
            return True, f"score≥0.95 ({_score:.2f}) bypasses overlap gate"
        # Token-overlap discipline: at least one shared token ≥5 chars.
        _q_tokens = _r569_tokenize(_query)
        _c_tokens = _r569_tokenize(_cand)
        _shared = _q_tokens & _c_tokens
        if not _shared:
            return False, f"zero token overlap (query={list(_q_tokens)[:3]}, candidate={list(_c_tokens)[:3]})"
        # Single-overlap on short token = high false-positive risk (R-F351 pattern).
        if len(_shared) == 1:
            _only = next(iter(_shared))
            if len(_only) < 5:
                return False, f"single short-token overlap ('{_only}', <5 chars)"
        return True, f"shared tokens: {sorted(_shared)[:3]}"

    try:
        from .sources import (
            sec_edgar as _src_sec,
            ofac_sdn as _src_ofac,
            fcdo_sanctions as _src_ofsi,
            un_sc_sanctions as _src_un,
            worldbank_debarred as _src_wb,
            acled as _src_acled,
        )
        _src_results = await asyncio.gather(
            _src_sec.lookup(name),
            _src_ofac.lookup(name),
            _src_ofsi.lookup(name),
            _src_un.lookup(name),
            _src_wb.lookup(name),
            _src_acled.lookup(name, country=(jurisdiction or "")),
            return_exceptions=True,
        )
        _src_labels = ["sec_edgar", "ofac_sdn", "uk_ofsi", "un_sc", "wb_debarred", "acled"]

        for _lbl, _r in zip(_src_labels, _src_results):
            if isinstance(_r, Exception):
                logger.debug("Identity: primary source %s raised: %s", _lbl, _r)
                report.identity.data_gaps.append(f"primary source {_lbl} did not complete")
                continue
            if not isinstance(_r, dict):
                continue
            report.identity.meta.subcalls += 1

            if not _r.get("ok"):
                if _r.get("error"):
                    report.identity.data_gaps.append(
                        f"{_lbl}: {str(_r.get('error'))[:120]}"
                    )
                continue

            _hits = _r.get("hits") or []
            if not _hits:
                continue

            # ── Severity mapping per source (semantics differ) ──
            if _lbl == "ofac_sdn":
                _best = _hits[0]
                _real, _reason = _r569_is_real_sanctions_hit(_best, name)
                if _real:
                    report.identity.findings.append(Finding(
                        severity="hard_stop",
                        title=f"OFAC SDN match: {_best.get('name','?')}",
                        detail=(
                            f"Match score {_best.get('_match_score', 0):.2f}. "
                            f"Programme(s): {', '.join(_best.get('programs', []))}. "
                            f"Designated {_best.get('designation_date','?')}. "
                            f"50-percent-rule applies to subsidiaries. "
                            f"R-F569 gate: {_reason}."
                        ),
                        source="sources.ofac_sdn",
                        confidence="CONFIRMED" if _best.get("_match_score", 0) >= 0.9 else "PROBABLE",
                    ))
                    hard_stop = True
                else:
                    # R-F569: fuzzy noise — surface for operator review at INFO level
                    # so it doesn't block but stays auditable.
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"OFAC SDN fuzzy hit (filtered): {_best.get('name','?')}",
                        detail=(
                            f"Match score {_best.get('_match_score', 0):.2f}, "
                            f"but R-F569 name-overlap gate rejected: {_reason}. "
                            f"Not a refusal ground; surfaced for operator audit."
                        ),
                        source="sources.ofac_sdn",
                        confidence="ASSESSED",
                    ))

            elif _lbl == "un_sc":
                _best = _hits[0]
                _real, _reason = _r569_is_real_sanctions_hit(_best, name)
                if _real:
                    report.identity.findings.append(Finding(
                        severity="hard_stop",
                        title=f"UN Security Council match: {_best.get('name','?')}",
                        detail=(
                            f"Match score {_best.get('_match_score', 0):.2f}. "
                            f"Regime: {_best.get('regime','?')}. "
                            f"Listed {_best.get('designation_date','?')}. "
                            f"R-F569 gate: {_reason}."
                        ),
                        source="sources.un_sc_sanctions",
                        confidence="CONFIRMED" if _best.get("_match_score", 0) >= 0.9 else "PROBABLE",
                    ))
                    hard_stop = True
                else:
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"UN SC fuzzy hit (filtered): {_best.get('name','?')}",
                        detail=(
                            f"Match score {_best.get('_match_score', 0):.2f}, "
                            f"but R-F569 name-overlap gate rejected: {_reason}. "
                            f"Not a refusal ground; surfaced for operator audit."
                        ),
                        source="sources.un_sc_sanctions",
                        confidence="ASSESSED",
                    ))

            elif _lbl == "uk_ofsi":
                _best = _hits[0]
                _real, _reason = _r569_is_real_sanctions_hit(_best, name)
                if _real:
                    report.identity.findings.append(Finding(
                        severity="hard_stop",
                        title=f"UK OFSI match: {_best.get('name','?')}",
                        detail=(
                            f"Match score {_best.get('_match_score', 0):.2f}. "
                            f"Regime: {_best.get('regime','?')}. "
                            f"Group ID {_best.get('group_id','?')}. "
                            f"Designated {_best.get('designation_date','?')}. "
                            f"R-F569 gate: {_reason}."
                        ),
                        source="sources.fcdo_sanctions",
                        confidence="CONFIRMED" if _best.get("_match_score", 0) >= 0.9 else "PROBABLE",
                    ))
                    hard_stop = True
                else:
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"UK OFSI fuzzy hit (filtered): {_best.get('name','?')}",
                        detail=(
                            f"Match score {_best.get('_match_score', 0):.2f}, "
                            f"but R-F569 name-overlap gate rejected: {_reason}. "
                            f"Not a refusal ground; surfaced for operator audit."
                        ),
                        source="sources.fcdo_sanctions",
                        confidence="ASSESSED",
                    ))

            elif _lbl == "wb_debarred":
                _active = [h for h in _hits if h.get("status") == "active"]
                if _active:
                    _best = _active[0]
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"World Bank debarment (active): {_best.get('name','?')}",
                        detail=(
                            f"Grounds: {_best.get('grounds','?')}. "
                            f"Ineligible {_best.get('ineligibility_from','?')} → "
                            f"{_best.get('ineligibility_to','?')}. "
                            f"Cross-recognised by AfDB/AsDB/EBRD/IDB under MCEA 2010."
                        ),
                        source="sources.worldbank_debarred",
                        confidence="PROBABLE",
                    ))
                else:
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"World Bank debarment (expired): {_hits[0].get('name','?')}",
                        detail=(
                            f"Historical debarment, ineligibility ended "
                            f"{_hits[0].get('ineligibility_to','?')}. "
                            f"Relevant context, not a current refusal ground."
                        ),
                        source="sources.worldbank_debarred",
                        confidence="ASSESSED",
                    ))

            elif _lbl == "sec_edgar":
                # R-F613 (2026-05-17): surface ALL material hits, not just
                # first per severity bucket. Past gap: company with 5
                # recent RED 8-K events (cyber incident + exec departure
                # + investigation + restatement + going-concern) only
                # showed ONE in the DD report — credibility loss for
                # financial-health DD. Cap at 5 per bucket.
                _red_hits = [h for h in _hits if (h.get("severity_hint") or "").startswith("RED")]
                _amber_hits = [h for h in _hits if (h.get("severity_hint") or "").startswith("AMBER")]
                _info_hits = [h for h in _hits if
                              not (h.get("severity_hint") or "").startswith(("RED", "AMBER"))]

                for _b in _red_hits[:5]:
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"SEC 8-K material event: {_b.get('company_name','?')}",
                        detail=(
                            f"{_b.get('severity_hint','?')}. "
                            f"Filed {_b.get('filing_date','?')}. "
                            f"Items: {_b.get('items','?')}."
                        ),
                        source="sources.sec_edgar",
                        confidence="CONFIRMED",
                    ))
                for _b in _amber_hits[:5]:
                    report.identity.findings.append(Finding(
                        severity="amber",
                        title=f"SEC filing flagged: {_b.get('company_name','?')}",
                        detail=(
                            f"{_b.get('severity_hint','?')}. "
                            f"Filed {_b.get('filing_date','?')}. "
                            f"Items: {_b.get('items','?')}."
                        ),
                        source="sources.sec_edgar",
                        confidence="PROBABLE",
                    ))
                # INFO summary fires only when no red/amber present —
                # don't clutter when there's already a substantive finding.
                if _info_hits and not (_red_hits or _amber_hits):
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"SEC filings found: {len(_info_hits)} recent ({_info_hits[0].get('company_name','?')})",
                        detail=(
                            f"Most recent: {_info_hits[0].get('form','?')} filed "
                            f"{_info_hits[0].get('filing_date','?')}. "
                            f"Full filings available for financial DD review."
                        ),
                        source="sources.sec_edgar",
                        confidence="CONFIRMED",
                    ))

            elif _lbl == "acled":
                _sev = _r.get("severity_hint") or ""
                if _sev.startswith("RED"):
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"ACLED: entity named in political-violence events",
                        detail=(
                            f"{len(_hits)} events in last 180d involve similar actor name. "
                            f"Most recent: {_hits[0].get('event_date','?')} "
                            f"{_hits[0].get('event_type','?')} in {_hits[0].get('country','?')}."
                        ),
                        source="sources.acled",
                        confidence="PROBABLE",
                    ))
                elif _sev.startswith("INFO"):
                    report.identity.findings.append(Finding(
                        severity="info",
                        title="ACLED: operational-environment signal",
                        detail=_sev,
                        source="sources.acled",
                        confidence="ASSESSED",
                    ))
    except Exception as _e:
        # R-F118 (2026-05-09): surface the cause in the data_gap so the
        # operator sees WHY in the DD report (and on the chat output)
        # instead of only in fly logs. Previous message was just
        # "did not complete" — operator had to dig fly logs to learn
        # whether it was an import error, a network issue, or an arg
        # mismatch.
        logger.warning(
            "Identity: primary-source parallel screen failed: %s: %s",
            type(_e).__name__, _e, exc_info=True,
        )
        report.identity.data_gaps.append(
            f"primary-source parallel screen did not complete: "
            f"{type(_e).__name__}: {str(_e)[:160]}"
        )

    # ── 1a2. Extract contact names from email / phone / explicit fields ──
    # When the user provides emails like branislav.takac@btg.sk or
    # explicit contact_name / contact fields, extract person names and
    # add them to the director screening list.
    _directors_in = list(target.get("directors") or [])
    _contact_names_extracted = set()
    for _email_field in ("email", "contact_email"):
        _em = target.get(_email_field) or ""
        if "@" in _em:
            _local = _em.split("@")[0]
            # firstname.lastname or firstname_lastname patterns
            _parts = re.split(r'[._\-]', _local)
            if len(_parts) >= 2:
                _extracted = " ".join(p.capitalize() for p in _parts if len(p) > 1)
                if len(_extracted) > 4 and _extracted not in _contact_names_extracted:
                    _contact_names_extracted.add(_extracted)
                    _directors_in.append({"name": _extracted, "role": "Contact (from email)"})
    for _cn_field in ("contact_name", "contact", "representative"):
        _cn = (target.get(_cn_field) or "").strip()
        if _cn and len(_cn) > 3 and _cn not in _contact_names_extracted:
            _contact_names_extracted.add(_cn)
            _directors_in.append({"name": _cn, "role": "Contact"})

    # ── Named-officeholder sanctions screen ──
    # When the caller supplies directors / beneficial owners / named
    # representatives, each individual is sanctions-screened separately.
    if _directors_in:
        try:
            from . import sanctions as _sanc
            from ._sanctions_classify import classify_matches as _cm
            _screen_fn = getattr(_sanc, "screen_with_aliases", None) or getattr(_sanc, "fuzzy_screen", None)
            for _d in _directors_in[:8]:  # hard cap — don't hammer the API
                _nm = (_d.get("name") or "").strip()
                if not _nm or len(_nm) < 4:
                    continue
                try:
                    _dscreen = await _screen_fn(_nm) if _screen_fn else {"matches": []}
                    _dcls = _cm(_dscreen.get("matches") or [], query_name=_nm)
                    _role = _d.get("role") or "Officer"
                    report.identity.meta.subcalls += 1
                    if _dcls["worst_severity"] == "hard_stop":
                        report.identity.findings.append(Finding(
                            severity="hard_stop",
                            title=f"{_role} {_nm} on active sanctions list",
                            detail=_dcls["summary"],
                            source="sanctions.director_screen",
                            confidence="CONFIRMED",
                        ))
                        hard_stop = True
                    elif _dcls["worst_severity"] == "red":
                        report.identity.findings.append(Finding(
                            severity="red",
                            title=f"{_role} {_nm} linked to crime/debarment list",
                            detail=_dcls["summary"],
                            source="sanctions.director_screen",
                            confidence="PROBABLE",
                        ))
                    elif _dcls["worst_severity"] == "amber":
                        report.identity.findings.append(Finding(
                            severity="amber",
                            title=f"{_role} {_nm} on PEP / adverse-media list",
                            detail=_dcls["summary"] + " — enhanced DD required on individual.",
                            source="sanctions.director_screen",
                            confidence="ASSESSED",
                        ))
                    else:
                        report.identity.findings.append(Finding(
                            severity="info",
                            title=f"{_role} {_nm} — sanctions screen CLEAN",
                            detail=f"No matches for {_nm} across OFAC / UK OFSI / EU / UN / OpenSanctions datasets.",
                            source="sanctions.director_screen",
                            confidence="CONFIRMED",
                        ))
                except Exception as _e:
                    logger.warning("Director screen failed for %s: %s", _nm, _e)
                    report.identity.data_gaps.append(f"director sanctions screen failed for {_nm}")
        except Exception as e:
            logger.warning("Identity: director screen block failed: %s", e)

    # ── 1b. Companies House lookup (UK only) ──
    if jurisdiction_iso2 == "GB":
        try:
            from . import companies_house
            if hasattr(companies_house, "investigate_uk_entity"):
                ch_result = await companies_house.investigate_uk_entity(
                    company_number=registration_number,
                    company_name=None if registration_number else name,
                )
                report.identity.meta.subcalls += 1
                if isinstance(ch_result, dict):
                    profile = ch_result.get("profile") or ch_result.get("company") or {}
                    report.identity.registration_number = profile.get("company_number") or registration_number
                    report.identity.registration_status = profile.get("company_status")
                    report.identity.incorporation_date = profile.get("date_of_creation")
                    report.identity.registered_address = (profile.get("registered_office_address") or {}).get("address_snippet") if isinstance(profile.get("registered_office_address"), dict) else profile.get("registered_office_address")
                    report.identity.declared_activity = ", ".join(profile.get("sic_codes") or [])[:200] or profile.get("sic_description")
                    report.identity.directors = ch_result.get("officers") or []
                    report.identity.shareholders = ch_result.get("psc") or []

                    # ── PSC-reverse: screen each beneficial owner against sanctions ──
                    # 2026-04-12: "Which people control this company, and are any of
                    # them sanctioned?" Surfaces hidden risk from beneficial owners.
                    psc_list = ch_result.get("psc") or []
                    if psc_list:
                        from . import sanctions as _san
                        from ._sanctions_classify import classify_matches as _cm_psc, SEVERITY_RANK
                        for psc_member in psc_list[:10]:  # cap at 10 to control cost
                            psc_name = psc_member.get("name") or ""
                            if not psc_name or len(psc_name) < 3:
                                continue
                            if psc_member.get("ceased_on"):
                                continue  # skip former PSCs
                            try:
                                psc_matches = await _san.screen_entity(psc_name)
                                report.identity.meta.subcalls += 1
                                if psc_matches:
                                    psc_classified = _cm_psc(psc_matches, query_name=psc_name)
                                    psc_worst = psc_classified["worst_severity"]
                                    if SEVERITY_RANK.get(psc_worst, 0) >= SEVERITY_RANK.get("amber", 1):
                                        natures = ", ".join(psc_member.get("natures_of_control") or [])[:120]
                                        report.identity.findings.append(Finding(
                                            severity=psc_worst,
                                            title=f"PSC (beneficial owner) {psc_name} flagged: {psc_worst}",
                                            detail=(
                                                f"Person of Significant Control '{psc_name}' "
                                                f"(control: {natures or 'not specified'}) "
                                                f"matched: {psc_classified['summary'][:300]}"
                                            ),
                                            source="sanctions.psc_reverse",
                                            confidence="PROBABLE",
                                        ))
                                        if psc_worst == "hard_stop":
                                            hard_stop = True
                            except Exception as _psc_e:
                                logger.warning("R-F886 PSC (ownership) screen failed for %s: %s", psc_name, _psc_e)
        except Exception as e:
            logger.warning("Identity: companies_house lookup failed: %s", e)
            report.identity.data_gaps.append(f"companies_house lookup failed: {str(e)[:120]}")

        # ── 1c. Financial DD — shell company detection (UK) ──────────
        # 2026-04-13: pulls Companies House filing history, detects dormant,
        # micro-entity, overdue, formation agent addresses.
        if registration_number:
            try:
                from . import financial_dd
                fin_profile = await financial_dd.get_financial_profile(registration_number)
                report.identity.meta.subcalls += 1
                if not fin_profile.get("error"):
                    for f in financial_dd.financial_findings(fin_profile):
                        report.identity.findings.append(Finding(**f))
                    report.identity.attributes = report.identity.attributes if hasattr(report.identity, 'attributes') and report.identity.attributes else {}
                    if isinstance(report.identity.attributes, dict):
                        report.identity.attributes["financial"] = {
                            "accounts_type": fin_profile.get("accounts_type"),
                            "shell_risk_score": fin_profile.get("shell_risk_score"),
                            "shell_indicators": fin_profile.get("shell_indicators", []),
                            "financial_summary": fin_profile.get("financial_summary"),
                        }
            except Exception as _fin_err:
                logger.debug("Financial DD failed (non-fatal): %s", _fin_err)
    else:
        # Try multi-jurisdiction registry adapter
        try:
            from . import registry_adapters
            _addr_for_adapter = (
                target.get("registered_address")
                or target.get("address")
                or report.identity.registered_address
                or ""
            )
            # R-F1815: try primary jurisdiction, then fallback chain
            reg_result = await registry_adapters.lookup_entity(
                name=name,
                jurisdiction_iso2=jurisdiction_iso2,
                registration_number=registration_number,
                address=_addr_for_adapter,
            )
            # If primary returned None, try fallback jurisdictions
            if not reg_result and jurisdiction_iso2:
                _fallbacks = _JURISDICTION_FALLBACK_CHAINS.get(jurisdiction_iso2, [])
                for _fb_iso2 in _fallbacks:
                    if _fb_iso2 == jurisdiction_iso2:
                        continue
                    logger.info(
                        "R-F1815: primary jurisdiction %s returned no result, "
                        "trying fallback %s", jurisdiction_iso2, _fb_iso2,
                    )
                    reg_result = await registry_adapters.lookup_entity(
                        name=name,
                        jurisdiction_iso2=_fb_iso2,
                        registration_number=registration_number,
                        address=_addr_for_adapter,
                    )
                    if reg_result:
                        logger.info(
                            "R-F1815: fallback jurisdiction %s returned result for %s",
                            _fb_iso2, name,
                        )
                        # Record the fallback as a finding so the DD report
                        # shows which jurisdiction actually had the data
                        report.identity.findings.append(Finding(
                            severity="info",
                            title=f"Registry lookup via fallback jurisdiction: {_fb_iso2}",
                            detail=(
                                f"Primary jurisdiction {jurisdiction_iso2} had no adapter "
                                f"or returned no result. Found data in {_fb_iso2}."
                            ),
                            source="dd_orchestrator.jurisdiction_fallback",
                            confidence="CONFIRMED",
                        ))
                        break
            if reg_result:
                profile = reg_result.get("profile", {})
                report.identity.registration_number = profile.get("company_number") or registration_number
                report.identity.registration_status = profile.get("company_status")
                report.identity.incorporation_date = profile.get("date_of_creation")
                report.identity.registered_address = profile.get("registered_office_address")
                report.identity.declared_activity = ", ".join(profile.get("sic_codes") or [])[:200]
                report.identity.directors = reg_result.get("officers") or []
                report.identity.shareholders = reg_result.get("psc") or []
                report.identity.meta.subcalls += 1
                report.identity.findings.append(Finding(
                    severity="info",
                    title=f"Registry lookup: {reg_result.get('adapter', jurisdiction_iso2)} ({profile.get('company_status', 'unknown')})",
                    detail=f"Source: {reg_result.get('source_url', 'registry adapter')}",
                    source=f"registry_adapters.{reg_result.get('adapter', 'unknown')}",
                    confidence="CONFIRMED",
                ))
                # ── Virtual-office re-check on registry-returned address ──
                # If the registry returned an address and the supplied_address
                # check earlier did not fire (e.g. no address was supplied
                # initially), now is the time to check. This catches the
                # F3 case where the address only comes from Sunbiz/registry.
                _reg_address = profile.get("registered_office_address") or ""
                # data_gaps may include a US stub — also probe for any FL/DE
                # address pattern there so the detector has material.
                if _reg_address:
                    try:
                        from . import virtual_office_registry
                        _vo = virtual_office_registry.check_address(_reg_address)
                        if _vo.get("is_virtual_office"):
                            _sev = "red" if _vo.get("risk") == "high" else "amber"
                            report.identity.findings.append(Finding(
                                severity=_sev,
                                title=f"Virtual-office match on registry address",
                                detail=(
                                    f"'{_reg_address}' matches known virtual-office "
                                    f"corridor: {_vo.get('provider') or '?'}. "
                                    f"{' '.join(_vo.get('signals') or [])}"
                                ),
                                source="dd_orchestrator.virtual_office_detector.registry",
                                confidence="ASSESSED",
                            ))
                    except Exception as _vo_err:
                        logger.debug("Virtual-office check on registry addr failed: %s", _vo_err)
            else:
                # Adapter returned None — jurisdiction not supported or lookup failed
                jur_hint = _national_registry_hint(jurisdiction_iso2, jurisdiction)
                report.identity.data_gaps.append(
                    f"Registry lookup unavailable for {jurisdiction or jurisdiction_iso2 or 'unspecified jurisdiction'}"
                    f" — ARIA has Companies House coverage for GB only. "
                    f"Manual action: {jur_hint}"
                )
                # Track as capability gap.
                # R-F150 2026-05-10: removed local `import asyncio` here.
                # Python's local-binding rule treats any `import X` inside
                # a function as a marker that X is local FOR THE WHOLE
                # FUNCTION — so this import shadowed the module-level
                # `import asyncio` at line 46, and the earlier reference
                # at line 732 (asyncio.gather in primary-source parallel
                # screen) raised UnboundLocalError. Live evidence
                # 2026-05-10 11:39:28: dd_orchestrator.py:732 traceback.
                # Same pattern as F28 (lifespan_smoke_test_required.md).
                # Module-level import is sufficient — no re-import needed.
                try:
                    from . import capability_gaps
                    _t = asyncio.create_task(capability_gaps.record_gap(
                        gap_type="registry_lookup",
                        detail=f"No automated registry adapter for {jurisdiction_iso2 or jurisdiction or 'unknown'}",
                        source="dd_orchestrator._run_identity",
                    ))
                    _t.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Registry adapter failed: %s", e)
            jur_hint = _national_registry_hint(jurisdiction_iso2, jurisdiction)
            report.identity.data_gaps.append(
                f"Registry lookup failed for {jurisdiction or jurisdiction_iso2}: {str(e)[:100]}. "
                f"Manual action: {jur_hint}"
            )

    # ── 1c. Ghost-score from available signals ──
    # Feed whatever we've collected into the programmatic scorer. The
    # scorer treats MISSING keys as data gaps, so only include keys
    # where we actually have a non-None value. (Including None values
    # crashes the scorer because its `_need(key)` only checks key
    # presence, not truthiness — int(None) then raises.)
    try:
        from . import due_diligence_playbooks as _dd
        profile: dict = {
            "name": report.identity.entity_name,
            "jurisdiction": report.identity.jurisdiction_iso2 or report.identity.jurisdiction,
            "registration_number": report.identity.registration_number,
        }
        _age = _age_months(report.identity.incorporation_date)
        if _age is not None:
            profile["age_months"] = _age
        # R-F1636 — registry-gap enrichment: when the registry lookup was
        # absent/thin/junk, enrich identity from OSINT (Wayback vintage) + the
        # intel vault and persist it so the next DD recalls it at $0 (§15).
        # locals().get avoids a NameError if reg_result never bound on an error
        # path; never breaks the identity layer.
        try:
            if _registry_result_is_thin(locals().get("reg_result")):
                _enr_name = (report.identity.entity_name or target.get("name")
                             or target.get("entity") or "").strip()
                if _enr_name:
                    await _enrich_registry_gap(target, report, _enr_name)
        except Exception as _enr_e:
            logger.debug("R-F1636 registry-gap enrichment failed (non-fatal): %s", _enr_e)

        _act = _map_activity(report.identity.declared_activity)
        if _act is not None:
            profile["declared_activity"] = _act
        _tval = target.get("transaction_value_usd")
        if _tval:
            profile["transaction_value_usd"] = _tval

        # Serban-case detectors — CUI, website, claimed founding year.
        # Any of these passed via the target dict (from chat intent
        # detection, API body, or autonomous task watchlist entry)
        # gets threaded into the ghost scorer so indicators 11 and 12
        # fire when the evidence is there.
        _cui = target.get("cui") or target.get("registration_number")
        if _cui:
            profile["cui"] = _cui
        _website = target.get("website") or target.get("domain")
        if _website:
            profile["website"] = _website
        _claimed_year = target.get("claimed_founding_year")
        if _claimed_year is not None:
            profile["claimed_founding_year"] = _claimed_year
        _residential_address = report.identity.registered_address
        if _residential_address and any(p in _residential_address.lower() for p in (
            "apt.", "apt ", " ap.", " ap ", " ap,", "apartment", "flat ",
            "unit ", "sc. ", "bl. ", "et. ", "etaj ", "floor ",
        )):
            profile["registered_address_type"] = "residential"

        ghost = _dd.score_ghost_indicators(profile)
        report.identity.ghost_score = ghost.as_dict()
        report.identity.meta.subcalls += 1
        if ghost.classification in ("RED", "HARD STOP"):
            hard_stop = True
            report.identity.findings.append(Finding(
                severity="hard_stop" if ghost.classification == "HARD STOP" else "red",
                title=f"Ghost score {ghost.total}/20 — {ghost.classification}",
                detail=ghost.recommendation,
                source="due_diligence_playbooks.score_ghost_indicators",
                confidence="PROBABLE",
            ))
        elif ghost.classification.startswith("AMBER"):
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"Ghost score {ghost.total}/20 — {ghost.classification}",
                detail=ghost.recommendation,
                source="due_diligence_playbooks.score_ghost_indicators",
                confidence="ASSESSED",
            ))

        # ── R-F602 — Formalised shell-co indicator breakdown ──
        # Past observation 2026-05-16: ghost detector ran a 12-indicator
        # scoring model but only the TOTAL was surfaced as a Finding row.
        # The individual triggered indicators ("mail-drop address",
        # "no website", "nominee director", etc.) were buried inside the
        # identity.ghost_score dict and invisible to a client reading
        # the DD report. For credibility each triggered indicator gets
        # its own auditable Finding row, so the operator can SEE why a
        # score of 12/20 was reached and challenge specific points if
        # the underlying data is wrong.
        for f in _emit_ghost_indicator_findings(ghost):
            report.identity.findings.append(f)
    except Exception as e:
        logger.warning("Identity: ghost scoring failed: %s", e)
        report.identity.data_gaps.append(f"ghost score failed: {str(e)[:120]}")

    # R-F1836 — domain-ownership (RDAP) as a Layer 1 signal (moved from digital,
    # which is skipped in quick mode / on hard-stop). Non-fatal, guarded.
    await _check_domain_ownership(target, report)

    report.identity.meta.duration_ms = int((time.time() - t0) * 1000)
    report.identity.meta.status = LayerStatus.OK.value
    return hard_stop


async def _run_network(target: dict, report: ARKDDReport) -> None:
    """Layer 2 — Network. Composes network_walker.walk_network."""
    t0 = time.time()
    report.network.meta.started_at = datetime.now(timezone.utc).isoformat()
    try:
        from . import network_walker
        result = await network_walker.walk_network(
            entity_name=report.identity.entity_name,
            entity_type=report.identity.entity_type,
            jurisdiction_iso2=report.identity.jurisdiction_iso2,
            registration_number=report.identity.registration_number,
            pre_resolved_officers=report.identity.directors or [],
        )
        report.network.director_graph = result.get("director_graph", {})
        report.network.cross_linked_entities = result.get("cross_linked_entities", [])
        report.network.address_cluster = result.get("address_cluster", {})
        report.network.pep_connections = result.get("pep_connections", [])
        report.network.sanctions_network = result.get("sanctions_network", [])
        report.network.findings = [Finding(**f) for f in result.get("findings", [])]
        report.network.data_gaps = result.get("data_gaps", [])
        report.network.meta.subcalls = result.get("stats", {}).get("sanctions_screens", 0) + result.get("stats", {}).get("entities_walked", 0)
        report.network.meta.status = LayerStatus.OK.value

        # ── Multi-hop entity graph (2026-04-13) ──────────────────────
        # Build a traversable graph from the walk result and run multi-hop
        # risk search. This finds indirect sanctions exposure through shared
        # directors, PSCs, and family/associate relationships.
        try:
            from . import entity_graph
            graph = entity_graph.build_from_walk_result(
                seed_name=report.identity.entity_name,
                seed_type=report.identity.entity_type,
                seed_jurisdiction=report.identity.jurisdiction_iso2,
                seed_reg_number=report.identity.registration_number,
                walk_result=result,
                sanctions_results=report.identity.sanctions_screen.get("matches") if hasattr(report.identity, "sanctions_screen") and report.identity.sanctions_screen else None,
            )
            # Multi-hop search: find all paths to flagged entities (up to 3 hops)
            hop_results = graph.multi_hop_search(
                start_id=list(graph.nodes.keys())[0] if graph.nodes else "",
                max_depth=3,
            )
            # Store graph summary in report
            report.network.director_graph["multi_hop"] = hop_results.get("risk_summary", {})
            report.network.director_graph["graph_stats"] = hop_results.get("graph_stats", {})

            # Generate findings from multi-hop paths
            for path in hop_results.get("paths", [])[:5]:
                if path.get("terminal_risk") in ("red", "hard_stop"):
                    hops = path.get("length", 0)
                    terminal = path.get("nodes", [{}])[-1] if path.get("nodes") else {}
                    report.network.findings.append(Finding(
                        severity=path["terminal_risk"],
                        title=f"Indirect sanctions exposure ({hops}-hop): {terminal.get('label', 'unknown')}",
                        detail=f"Path: {' → '.join(n.get('label', '?') for n in path.get('nodes', []))}. Reason: {path.get('terminal_reason', '')[:200]}",
                        source="entity_graph.multi_hop",
                        confidence="PROBABLE",
                    ))

            # Persist graph for re-screening
            await graph.save(report.run_id)
            logger.info("Entity graph built: %d nodes, %d edges, %d flagged paths",
                        hop_results.get("graph_stats", {}).get("total_nodes", 0),
                        hop_results.get("graph_stats", {}).get("total_edges", 0),
                        hop_results.get("risk_summary", {}).get("flagged_nodes", 0))
        except Exception as eg_err:
            logger.warning("Entity graph construction failed (non-fatal): %s", eg_err)

        # ── R-F435 (2026-05-13) — UBO chain walker auto-trigger ─────
        # network_walker.walk_ubo_chain() exists but was previously only
        # invoked from tests. Wire it as part of Layer 2 so every company
        # DD attempts a recursive UBO walk. The walker has its own budget
        # caps (max_hops=3, max_total_nodes=50, officers_per_entity=10)
        # and self-terminates fast on uncovered jurisdictions, recording
        # an explicit coverage_gap entry — which is itself valuable
        # signal (operator sees "no registry adapter for jurisdiction X"
        # instead of silent failure). Persons skip this layer (no UBO).
        # Opt-out via ARIA_UBO_WALK_DISABLED=1 for ops.
        if (
            report.identity.entity_type == EntityType.COMPANY.value
            and not os.getenv("ARIA_UBO_WALK_DISABLED", "").strip()
        ):
            try:
                ubo_result = await network_walker.walk_ubo_chain(
                    entity_name=report.identity.entity_name,
                    entity_type="company",
                    jurisdiction_iso2=report.identity.jurisdiction_iso2,
                    registration_number=report.identity.registration_number,
                )
                # Flat node list preserves the existing reader contract at
                # line 4252 (iterates u.get("name")).
                report.network.ubo_chain = (
                    (ubo_result.get("graph") or {}).get("nodes") or []
                )
                report.network.ubo_chain_walk = ubo_result
                _stats = ubo_result.get("stats") or {}
                report.network.meta.subcalls += (
                    _stats.get("sanctions_screens", 0)
                    + _stats.get("registry_lookups", 0)
                )

                # Surface sanctioned_in_chain as Network findings
                for _s in (ubo_result.get("sanctioned_in_chain") or [])[:5]:
                    _sev = _s.get("severity") or "red"
                    if _sev not in ("hard_stop", "red", "amber"):
                        _sev = "red"
                    report.network.findings.append(Finding(
                        severity=_sev,
                        title=(
                            f"UBO chain sanctions: {_s.get('name', '?')} "
                            f"({_sev}, hop {_s.get('hop', '?')})"
                        ),
                        detail=(
                            f"Discovered via UBO walk from "
                            f"{_s.get('parent_entity', report.identity.entity_name)}. "
                            f"{_s.get('summary', '')[:240]}"
                        ),
                        source="network_walker.walk_ubo_chain",
                        confidence="PROBABLE",
                    ))
                # PEP-in-chain → amber findings
                for _p in (ubo_result.get("pep_in_chain") or [])[:5]:
                    report.network.findings.append(Finding(
                        severity="amber",
                        title=(
                            f"UBO chain PEP: {_p.get('name', '?')} "
                            f"(hop {_p.get('hop', '?')})"
                        ),
                        detail=(
                            f"PEP / adverse-media match discovered via UBO walk "
                            f"from {_p.get('parent_entity', report.identity.entity_name)}. "
                            f"{_p.get('summary', '')[:240]}"
                        ),
                        source="network_walker.walk_ubo_chain",
                        confidence="ASSESSED",
                    ))
                # Coverage gaps → data_gaps (operator-actionable: tells them
                # which jurisdictions ARIA can't yet walk)
                for _g in (ubo_result.get("coverage_gaps") or [])[:10]:
                    report.network.data_gaps.append(
                        f"UBO walk hop {_g.get('hop', '?')} "
                        f"({_g.get('entity_name', '?')}): "
                        f"{_g.get('reason', 'unknown')}"
                    )
                if _stats.get("budget_exhausted"):
                    report.network.data_gaps.append(
                        f"UBO walk budget exhausted at "
                        f"{_stats.get('total_nodes', '?')} nodes — increase "
                        f"max_total_nodes for deeper trace"
                    )

                logger.info(
                    "UBO walk %s: verdict=%s, %d nodes, %d edges, "
                    "%d sanctioned_in_chain, %d PEP_in_chain, %d coverage_gaps",
                    report.identity.entity_name[:40],
                    ubo_result.get("verdict", "?"),
                    _stats.get("total_nodes", 0),
                    _stats.get("total_edges", 0),
                    len(ubo_result.get("sanctioned_in_chain") or []),
                    len(ubo_result.get("pep_in_chain") or []),
                    len(ubo_result.get("coverage_gaps") or []),
                )
            except Exception as ubo_err:
                logger.warning(
                    "UBO walk failed (non-fatal): %s", ubo_err,
                )
                report.network.data_gaps.append(
                    f"UBO walk failed: {str(ubo_err)[:120]}"
                )

    except Exception as e:
        logger.warning("Network layer failed: %s", e)
        report.network.meta.status = LayerStatus.ERROR.value
        report.network.meta.error = str(e)[:200]
    report.network.meta.duration_ms = int((time.time() - t0) * 1000)


# ── R-F601 — Formalised FATF jurisdiction-risk Finding builder ────────────
#
# Past observation 2026-05-16: ARIA's self-assessment claimed "no FATF
# formalised layer". Verified — FATF status WAS loaded at
# risk_indices.FATF_STATUS_2025_FEB:178 but only surfaced as an embedded
# "FATF=grey" substring inside a single country-risk detail line, AND
# only when the overall headline hit RED/HARD_STOP. Grey-list
# jurisdictions (BG, NG, KE, PH, etc.) never got their own row, so a
# client reading the DD report didn't see FATF as an explicit signal.
#
# This pure helper builds the Finding regardless of overall country
# headline:
#   black  → hard_stop  (Call for Action — DPRK / IR / MM)
#   grey   → amber      (Increased Monitoring)
#   clear  → None       (silent — clean jurisdiction, no clutter)
#
# Pure function: no I/O, no async, deterministic — testable in
# isolation. Called from _run_compliance.
def _build_fatf_finding(
    *,
    fatf_status: Optional[str],
    iso2: Optional[str],
    jurisdiction: Optional[str],
) -> Optional[Finding]:
    """Return a Finding for FATF-listed jurisdictions, or None for clear.

    Inputs:
      fatf_status: 'black' | 'grey' | 'clear' | None — case-insensitive
      iso2:        ISO-3166-1 alpha-2 country code (display only)
      jurisdiction: Human-readable country name (display only)

    Source citation follows Clause 15 (inline URL); confidence is
    CONFIRMED because FATF is a Tier-1a authoritative source (Clause 24
    — government-publication anchor).
    """
    status = (fatf_status or "").lower().strip()
    if status not in ("black", "grey"):
        return None

    iso2_disp = iso2 or "?"
    juris_disp = jurisdiction or iso2_disp

    if status == "black":
        label = "FATF Call for Action (high-risk jurisdiction)"
        list_description = "high-risk / Call for Action list"
        implications = (
            "TRANSACTIONS BLOCKED by most major banks; "
            "DSP licence likely refused; reputational risk severe. "
            "Treat as HARD STOP unless explicit FCDO/OFSI authorisation."
        )
        severity = "hard_stop"
    else:  # grey
        label = "FATF Increased Monitoring (grey-list)"
        list_description = "jurisdictions under increased monitoring list"
        implications = (
            "Enhanced Due Diligence (EDD) required; "
            "UK banks apply friction on USD/EUR clearing; "
            "Tier-1 source for current status must be re-verified."
        )
        severity = "amber"

    detail = (
        f"Jurisdiction {iso2_disp} ({juris_disp}) appears on the FATF "
        f"{list_description}. Implications for defence broking: {implications} "
        "Snapshot: risk_indices.FATF_STATUS_2025_FEB (FATF updates 3x/year — "
        "verify currency against the live page)."
    )

    return Finding(
        severity=severity,
        title=f"FATF status: {status.upper()} — {label}",
        detail=detail,
        source=(
            "risk_indices.FATF_STATUS_2025_FEB "
            "[from https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html]"
        ),
        confidence="CONFIRMED",
    )


async def _run_compliance(target: dict, report: ARKDDReport) -> None:
    """Layer 4 — Compliance. Composes risk_indices + tech_classifier +
    international_law / global_export_control / regional_compliance via
    RAG queries through rag_store."""
    t0 = time.time()
    report.compliance.meta.started_at = datetime.now(timezone.utc).isoformat()

    # ── 4a. Country risk (qualitative — risk_indices) ──
    try:
        from . import risk_indices
        iso2 = report.identity.jurisdiction_iso2 or target.get("destination_iso2")
        if iso2:
            risk = risk_indices.get_country_risk(iso2, name=report.identity.jurisdiction or iso2)
            report.compliance.country_risk = risk.as_dict()
            report.compliance.meta.subcalls += 1
            headline = risk.headline_risk()
            if headline in ("RED", "HARD_STOP"):
                report.compliance.findings.append(Finding(
                    severity="hard_stop" if headline == "HARD_STOP" else "red",
                    title=f"Country risk: {headline}",
                    detail=f"CPI={risk.cpi_score} · Basel AML={risk.basel_aml} · FATF={risk.fatf_status} · OECD CRC={risk.oecd_crc}",
                    source="risk_indices.get_country_risk",
                    confidence="ASSESSED",
                ))

            # ── 4a-FATF (R-F601). Formalised FATF status finding. ──
            _fatf_finding = _build_fatf_finding(
                fatf_status=risk.fatf_status,
                iso2=iso2,
                jurisdiction=report.identity.jurisdiction or iso2,
            )
            if _fatf_finding is not None:
                report.compliance.findings.append(_fatf_finding)
                report.compliance.meta.subcalls += 1
    except Exception as e:
        logger.warning("Compliance: country risk failed: %s", e)
        report.compliance.data_gaps.append(f"country risk lookup failed: {str(e)[:120]}")

    # ── 4a-bis. Country macro overlay (quantitative — World Bank Indicators v2) ──
    # R-F160 (2026-05-10) — wires the WB Indicators v2 + Data360 adapter
    # (R-F158) into the jurisdiction_country_risk discipline (R-F152). Free
    # endpoint, no auth, complements the qualitative risk_indices output
    # with quantitative macro context (GDP, debt/GDP, military spend, WGI
    # governance scores). Only fires when an ISO code is available — for
    # entities with no jurisdiction (Layer 1 inferred to UNKNOWN), this
    # block is skipped per Clause 7 (knowing limits).
    try:
        from .sources import worldbank_indicators as _wbi
        iso2_for_overlay = report.identity.jurisdiction_iso2 or target.get("destination_iso2")
        if iso2_for_overlay:
            overlay = await _wbi.country_risk_overlay(iso2_for_overlay)
            if overlay.get("ok"):
                report.compliance.macro_overlay = overlay
                report.compliance.meta.subcalls += 1
                # Surface anomalous values as findings
                _gov = overlay.get("governance_wgi", {}) or {}
                _macro = overlay.get("macro", {}) or {}
                _defence = overlay.get("defence", {}) or {}
                # WGI <-1.0 = below 16th percentile globally on that dimension
                for _wgi_key, _wgi_val in _gov.items():
                    if isinstance(_wgi_val, (int, float)) and _wgi_val < -1.0:
                        report.compliance.findings.append(Finding(
                            severity="amber",
                            title=f"WGI low: {_wgi_key} = {_wgi_val:.2f}",
                            detail=(
                                f"World Bank Worldwide Governance Indicator '{_wgi_key}' for "
                                f"{iso2_for_overlay} is {_wgi_val:.2f} (below -1.0 = bottom 16% "
                                f"globally). Indicates structural governance weakness on this "
                                f"dimension — adds context to country-risk assessment."
                            ),
                            source=f"worldbank_indicators.country_risk_overlay [from {overlay.get('primary_source_url','')}]",
                            confidence="CONFIRMED",
                        ))
                # Debt/GDP > 100% = elevated sovereign-stress signal
                _debt = _macro.get("debt_to_gdp_pct")
                if isinstance(_debt, (int, float)) and _debt > 100:
                    report.compliance.findings.append(Finding(
                        severity="amber",
                        title=f"Sovereign debt elevated: {_debt:.1f}% of GDP",
                        detail=(
                            f"Central government debt for {iso2_for_overlay} = {_debt:.1f}% of GDP "
                            f"(WB threshold for fiscal-stress concern: ~100%). Adds context for "
                            f"sovereign-counterparty + payment-currency risk."
                        ),
                        source=f"worldbank_indicators.country_risk_overlay [from {overlay.get('primary_source_url','')}]",
                        confidence="CONFIRMED",
                    ))
                # Military spend > 5% of GDP = elevated militarisation signal
                _mil = _defence.get("military_spend_pct_gdp")
                if isinstance(_mil, (int, float)) and _mil > 5.0:
                    report.compliance.findings.append(Finding(
                        severity="amber",
                        title=f"Military spend elevated: {_mil:.1f}% of GDP",
                        detail=(
                            f"Military expenditure for {iso2_for_overlay} = {_mil:.1f}% of GDP "
                            f"(global average ~2-2.5%). Defence-sector context for procurement "
                            f"DD; not necessarily adverse but informs market-sizing + sanctions "
                            f"context."
                        ),
                        source=f"worldbank_indicators.country_risk_overlay [from {overlay.get('primary_source_url','')}]",
                        confidence="CONFIRMED",
                    ))
            elif overlay.get("error"):
                report.compliance.data_gaps.append(
                    f"WB Indicators overlay unavailable for {iso2_for_overlay}: {str(overlay.get('error'))[:120]}"
                )
    except Exception as e:
        logger.debug("Compliance: WB Indicators overlay failed (non-fatal): %s", e)

    # ── 4a-CULTURAL (R-F635) — cultural read for non-UK/US jurisdictions ──
    # Pulls a population-level cultural profile from cultural_atlas
    # (R-F634) when the counterparty jurisdiction is seeded and is
    # outside the operator's default-familiar west (GB/US).
    # Tagged PROBABLE + population-level by the atlas renderer —
    # never deterministic about the individual counterparty.
    try:
        _cult_iso2 = report.identity.jurisdiction_iso2 or target.get("destination_iso2") or ""
        _cult_iso2 = (_cult_iso2 or "").upper().strip()
        if _cult_iso2 and _cult_iso2 not in ("GB", "US"):
            from . import cultural_atlas as _ca
            _cult_block = _ca.render_context_block(_cult_iso2, depth="full")
            if _cult_block:
                report.compliance.cultural_context = _cult_block
                report.compliance.meta.subcalls += 1
    except Exception as e:
        logger.debug("Compliance: cultural_atlas lookup failed (non-fatal): %s", e)

    # ── 4a-EXPERT-KNOWLEDGE (R-F644) — wire R-F638..F643 expert layers ──
    # Direct response to the 2026-05-17 ADSM case. When the target dict
    # carries `goods_list`, `mou_text` or `end_user_statement`, fire the
    # six expert-knowledge modules and append their findings to
    # report.compliance.findings.
    #
    # Modules wired:
    #   R-F638 weapon_origin_catalogue        — per-item OEM+sanctions
    #   R-F639 eliminated_weapons_watchlist   — INF/CWC/BWC/Ottawa/CCM
    #   R-F640 evasion_typology_detector      — TR-RU-MENA + composites
    #   R-F641 end_user_granularity           — Saudi-Gov-generic catch
    #   R-F642 mou_clause_gate_analyser       — 4.5(e)-style gates
    #   R-F643 goods_list_aggregator_detector — calibre-mix + diversion
    #
    # All wired defensively — any single failure does NOT break compliance.
    try:
        from . import weapon_origin_catalogue as _woc
        from . import eliminated_weapons_watchlist as _ewl
        from . import evasion_typology_detector as _etd
        from . import end_user_granularity as _eug
        from . import mou_clause_gate_analyser as _mcga
        from . import goods_list_aggregator_detector as _glad

        # Parse goods_list from target — accept list[str] OR multi-line str
        _goods_list_raw = target.get("goods_list")
        _goods_items: list[str] = []
        if isinstance(_goods_list_raw, list):
            _goods_items = [str(x) for x in _goods_list_raw if x]
        elif isinstance(_goods_list_raw, str):
            _goods_items = [
                line.strip() for line in _goods_list_raw.splitlines()
                if line.strip()
            ]

        # Aggregate weapon-origin + sanctions matches per line
        _weapon_origins: list[str] = []
        _weapon_statuses: list[str] = []
        _has_inf = False
        _has_cwc_bwc = False
        _ewl_err_logged = False   # R-F886: record a banned-weapon engine failure once/run

        for _item in _goods_items[:30]:  # cap line-items processed
            # Weapon catalogue (R-F638)
            try:
                _w_finding = _woc.render_finding_for_text(_item)
                if _w_finding:
                    report.compliance.findings.append(Finding(
                        severity=_w_finding["severity"],
                        title=_w_finding["title"],
                        detail=_w_finding["detail"],
                        source=_w_finding["source"],
                        confidence=_w_finding["confidence"],
                    ))
                    _weapon_origins.append(_w_finding.get("origin_iso2", ""))
                    _weapon_statuses.append(_w_finding.get("sanctions_status", ""))
                    report.compliance.meta.subcalls += 1
                    _absorb_dd_compliance_catch(  # R-F896
                        report, target, _w_finding.get("severity", ""),
                        f"DD weapon-origin flag: {_w_finding.get('title', '')}",
                        _w_finding.get("detail", ""),
                    )
            except Exception as _e1:
                logger.warning("R-F886 weapon-catalogue line failed: %s", _e1)

            # Eliminated/banned watchlist (R-F639)
            try:
                _e_finding = _ewl.render_finding_for_text(_item)
                if _e_finding:
                    report.compliance.findings.append(Finding(
                        severity=_e_finding["severity"],
                        title=_e_finding["title"],
                        detail=_e_finding["detail"],
                        source=_e_finding["source"],
                        confidence=_e_finding["confidence"],
                    ))
                    if "inf" in (_e_finding.get("treaty") or "").lower():
                        _has_inf = True
                    if "chemical" in (_e_finding.get("treaty") or "").lower() \
                       or "biological" in (_e_finding.get("treaty") or "").lower():
                        _has_cwc_bwc = True
                    report.compliance.meta.subcalls += 1
                    # R-F892 — record the CATCH to the brain. A treaty-banned/
                    # eliminated weapon on a procurement list is ARIA's highest-
                    # value compliance action, but the success side was invisible
                    # to the brain (only the report carried it) — she never
                    # "remembered" what she screened out. Fire-and-forget so DD
                    # never blocks on the brain fan-out.
                    try:
                        from . import brain_hook as _bh892
                        _t892 = asyncio.create_task(_bh892.absorb_silent(
                            module="dd_orchestrator",
                            summary=(
                                "Banned/eliminated-weapon screen HIT: "
                                f"{_e_finding.get('title', '')}"
                            ),
                            detail=(_e_finding.get("detail", "") or "")[:600],
                            entity_name=str((target or {}).get("name", ""))[:120],
                            success=True,
                            confidence="CONFIRMED",
                            source_id="eliminated_weapons_watchlist:R-F639",
                        ))
                        _t892.add_done_callback(
                            lambda t: t.result()
                            if not t.cancelled() and not t.exception() else None
                        )
                    except Exception:
                        pass
            except Exception as _e2:
                # R-F886 — a banned-weapon (INF/CWC/BWC/Ottawa/CCM) screen
                # failing SILENTLY is a compliance miss nobody sees. Raise to
                # WARNING + record to error_log so the reconnected coder
                # (R-F884) sees it. NOTE (R-F891): the error_log_handler now
                # also catches the "ARIA.*" logger tree, so this WARNING reaches
                # the ledger per-line on its own; record_error is the higher-
                # signal structured alert (kept once/run for flood protection).
                logger.warning(
                    "eliminated-weapons watchlist FAILED on a goods line "
                    "(banned-weapon screen miss) [%s]: %s",
                    str(_item)[:80], _e2,
                )
                if not _ewl_err_logged:
                    _ewl_err_logged = True
                    try:
                        from . import self_improve as _si886
                        await _si886.record_error(
                            "compliance_engine_failure",
                            f"eliminated_weapons_watchlist.render_finding_for_text raised: {_e2}",
                            file="aria_service/intel/dd_orchestrator.py",
                            function="orchestrate_dd:eliminated_weapons",
                        )
                    except Exception as _e892:
                        # Below WARNING so it can't recurse through the ledger.
                        logger.debug("R-F892: failed to record EWL engine error: %s", _e892)

        # Aggregator pattern (R-F643)
        if _goods_items:
            try:
                _agg_finding = _glad.render_finding(
                    _goods_items,
                    buyer_country_iso2=(report.identity.jurisdiction_iso2
                                        or target.get("destination_iso2")),
                )
                if _agg_finding:
                    report.compliance.findings.append(Finding(
                        severity=_agg_finding["severity"],
                        title=_agg_finding["title"],
                        detail=_agg_finding["detail"],
                        source=_agg_finding["source"],
                        confidence=_agg_finding["confidence"],
                    ))
                    report.compliance.meta.subcalls += 1
                    _absorb_dd_compliance_catch(  # R-F896
                        report, target, _agg_finding.get("severity", ""),
                        f"DD aggregation-pattern flag: {_agg_finding.get('title', '')}",
                        _agg_finding.get("detail", ""),
                    )
            except Exception as _e3:
                logger.warning("R-F886 aggregator failed: %s", _e3)

        # Evasion typology (R-F640)
        try:
            _routing_iso2 = (target.get("routing_location_iso2")
                             or target.get("stockholding_location_iso2"))
            _eu_statement = target.get("end_user_statement") or ""
            _buyer_iso2 = (report.identity.jurisdiction_iso2
                           or target.get("destination_iso2"))
            # Best-effort: is the buyer's end-user specific?
            _eu_specific = False
            if _eu_statement and _buyer_iso2:
                _eu_specific = _eug.is_sufficient_for_euc(_eu_statement, _buyer_iso2)

            _ctx = _etd.DealContext(
                weapon_origins_iso2=tuple(o for o in _weapon_origins if o),
                weapon_sanctions_statuses=tuple(_weapon_statuses),
                routing_location_iso2=_routing_iso2,
                buyer_country_iso2=_buyer_iso2,
                buyer_named_end_user_specific=_eu_specific,
                has_inf_eliminated_items=_has_inf,
                has_categorically_banned_items=_has_cwc_bwc,
                goods_list_mixed_nato_and_non_nato_calibres=False,
                no_timeline_indicated=bool(target.get("no_timeline_indicated")),
                no_commission_discussed=bool(target.get("no_commission_discussed")),
                informal_channel_received=bool(target.get("informal_channel_received")),
            )
            for _typ_f in _etd.render_findings_for_ctx(_ctx):
                report.compliance.findings.append(Finding(
                    severity=_typ_f["severity"],
                    title=_typ_f["title"],
                    detail=_typ_f["detail"],
                    source=_typ_f["source"],
                    confidence=_typ_f["confidence"],
                ))
                report.compliance.meta.subcalls += 1
                _absorb_dd_compliance_catch(  # R-F896
                    report, target, _typ_f.get("severity", ""),
                    f"DD evasion-typology flag: {_typ_f.get('title', '')}",
                    _typ_f.get("detail", ""),
                )
        except Exception as _e4:
            logger.warning("R-F886 typology detector failed: %s", _e4)

        # End-user granularity (R-F641)
        try:
            _eu_statement = target.get("end_user_statement") or ""
            if _eu_statement:
                _buyer_iso2 = (report.identity.jurisdiction_iso2
                               or target.get("destination_iso2"))
                _eu_f = _eug.render_finding(_eu_statement, country_iso2=_buyer_iso2)
                report.compliance.findings.append(Finding(
                    severity=_eu_f["severity"],
                    title=_eu_f["title"],
                    detail=_eu_f["detail"],
                    source=_eu_f["source"],
                    confidence=_eu_f["confidence"],
                ))
                report.compliance.meta.subcalls += 1
                _absorb_dd_compliance_catch(  # R-F896
                    report, target, _eu_f.get("severity", ""),
                    f"DD end-user-granularity flag: {_eu_f.get('title', '')}",
                    _eu_f.get("detail", ""),
                )
        except Exception as _e5:
            logger.warning("R-F886 end-user granularity failed: %s", _e5)

        # MOU clause-gate analysis (R-F642)
        try:
            _mou_text = target.get("mou_text") or ""
            if _mou_text and len(_mou_text) > 200:
                for _gate_f in _mcga.render_finding(_mou_text)[:5]:
                    report.compliance.findings.append(Finding(
                        severity=_gate_f["severity"],
                        title=_gate_f["title"],
                        detail=_gate_f["detail"],
                        source=_gate_f["source"],
                        confidence=_gate_f["confidence"],
                    ))
                    report.compliance.meta.subcalls += 1
                    _absorb_dd_compliance_catch(  # R-F896
                        report, target, _gate_f.get("severity", ""),
                        f"DD MOU clause-gate flag: {_gate_f.get('title', '')}",
                        _gate_f.get("detail", ""),
                    )
        except Exception as _e6:
            logger.warning("R-F886 mou-gate analyser failed: %s", _e6)
    except Exception as _expert_err:
        logger.warning("R-F886 expert-knowledge compliance block failed (non-fatal): %s", _expert_err)

    # ── 4b. Export control classification ──
    product_text = target.get("product_description") or target.get("goods") or ""
    if product_text:
        try:
            from . import tech_classifier
            ec = tech_classifier.classify_export_control(product_text)
            report.compliance.export_control = ec
            report.compliance.meta.subcalls += 1
            if ec.get("multilateral"):
                for hit in ec.get("multilateral", []):
                    report.compliance.sanctions_regimes.append(hit.get("regime", ""))
            if ec.get("recommendation", "").startswith("ITAR"):
                report.compliance.licence_path = "DSP-5 / TAA (ITAR)"
            elif "EAR" in (ec.get("recommendation", "") or ""):
                report.compliance.licence_path = "BIS-748P / Licence Exception"
            elif ec.get("wassenaar_ml"):
                report.compliance.licence_path = "SIEL / SITCL (UK) or equivalent national ML route"
        except Exception as e:
            logger.warning("Compliance: export control classification failed: %s", e)
            report.compliance.data_gaps.append(f"export control classify failed: {str(e)[:120]}")
    else:
        report.compliance.data_gaps.append("No product/goods description — export control classification skipped")

    # ── 4c. Regional bloc matching via RAG ──
    try:
        from . import rag_store
        country = report.identity.jurisdiction or target.get("destination") or ""
        if country:
            regional = await rag_store.get_rag_context(
                f"{country} regional compliance framework defence arms transfer",
                max_chars=2000,
            )
            if regional and regional.strip():
                report.compliance.regional_bloc_requirements = [{
                    "query": f"{country} regional bloc",
                    "excerpt": regional[:800],
                    "source": "RAG:regional_compliance",
                }]
                report.compliance.meta.subcalls += 1
    except Exception as e:
        logger.warning("Compliance: regional bloc RAG failed: %s", e)
        report.compliance.data_gaps.append(f"regional bloc RAG failed: {str(e)[:120]}")

    report.compliance.meta.duration_ms = int((time.time() - t0) * 1000)
    report.compliance.meta.status = LayerStatus.OK.value


def _entity_distinctive_tokens(*names: str) -> list[str]:
    """R-F1631 — distinctive (non-generic) tokens of an entity name, accent-
    normalized + lowercased. Used to gate search results for ACTUAL entity-
    relevance so noise (unrelated academic papers, generic 'security' hits,
    tangential procurement notices) doesn't enter the DD as 'press coverage'.
    Returns [] for an all-generic name — the caller then keeps everything, so
    we never silently drop all coverage for an un-discriminable name."""
    import unicodedata
    _GENERIC = {
        "security", "services", "service", "inc", "ltd", "llc", "gmbh", "co",
        "company", "corp", "corporation", "group", "holding", "holdings",
        "international", "global", "the", "and", "for", "of", "sa", "ag", "plc",
        "as", "limited", "trading", "consulting", "solutions", "systems",
        "technologies", "technology", "defence", "defense", "guvenlik",
    }
    def _norm(s: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFKD", s or "")
            if not unicodedata.combining(c)
        ).lower()
    toks: list[str] = []
    for nm in names:
        for t in re.split(r"[^a-z0-9]+", _norm(nm)):
            if len(t) >= 3 and t not in _GENERIC and t not in toks:
                toks.append(t)
    return toks


def _press_hit_is_relevant(title: str, snippet: str, url: str, distinctive_tokens: list[str]) -> bool:
    """R-F1631 — True if a search hit actually references the entity (any
    distinctive token appears in title/snippet/url, accent-normalized). With no
    distinctive tokens (all-generic name) returns True — never drop everything."""
    if not distinctive_tokens:
        return True
    import unicodedata
    hay = "".join(
        c for c in unicodedata.normalize("NFKD", f"{title or ''} {snippet or ''} {url or ''}")
        if not unicodedata.combining(c)
    ).lower()
    return any(t in hay for t in distinctive_tokens)


# ── R-F1636 — registry-gap enrichment (rich data for no-registry targets) ────
def _extract_domain(target: dict, name: str) -> str:
    """Best-effort apex domain for the entity, for Wayback vintage enrichment."""
    from urllib.parse import urlparse
    for cand in (target.get("website"), target.get("url"), target.get("domain"),
                 target.get("name"), name):
        if not cand:
            continue
        s = str(cand).strip()
        if "://" in s:
            s = urlparse(s).netloc or s
        s = s.removeprefix("www.").split("/")[0].strip().lower()
        if "." in s and " " not in s and len(s) <= 80:
            return s
    return ""


def _registry_result_is_thin(reg_result) -> bool:
    """True when the registry adapter gave nothing usable: None, or a 'success'
    with no real identity (empty company number AND no directors). Catches the
    scrape-returns-boilerplate-junk case (e.g. the TR/MERSIS homepage scrape) —
    surfacing that as identity is worse than honestly enriching from OSINT."""
    if not reg_result:
        return True
    profile = reg_result.get("profile") or {}
    has_number = bool((profile.get("company_number") or "").strip())
    has_directors = bool(reg_result.get("officers"))
    return not (has_number or has_directors)


async def _wayback_earliest(domain: str) -> dict | None:
    """Earliest archived snapshot of a domain via the Wayback availability API
    (free, no key). The first-seen date is a vintage proxy when no registry
    incorporation date exists (a very young domain + recent contact = ghost
    risk). Returns {first_seen, snapshot_url} or None. Never raises."""
    if not domain:
        return None
    import httpx
    try:
        url = f"http://archive.org/wayback/available?url={domain}&timestamp=19960101"
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            snap = ((r.json() or {}).get("archived_snapshots") or {}).get("closest") or {}
            ts = snap.get("timestamp") or ""
            if len(ts) < 8:
                return None
            return {"first_seen": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}", "snapshot_url": snap.get("url", "")}
    except Exception:
        return None


async def _enrich_registry_gap(target: dict, report, name: str) -> None:
    """R-F1636 — when registry data is absent/thin/junk, enrich identity from
    FREE OSINT (Wayback vintage) + the intel VAULT (prior DDs), and WRITE the
    enrichment back to the vault so the next DD on this entity recalls it at $0
    (§15 pay-once-remember-forever). Never raises."""
    domain = _extract_domain(target, name)
    bits: list[str] = []

    # A-guard (folded in): a thin result may have populated identity with empty/
    # boilerplate values — clear obviously-unusable ones so the DD shows honest
    # "MISSING" instead of junk (e.g. a homepage-scrape fragment as 'address').
    _addr = (report.identity.registered_address or "")
    if _addr and (len(_addr) > 160 or "veri taban" in _addr.lower() or "mersis)" in _addr.lower()):
        report.identity.registered_address = ""
        report.identity.data_gaps.append("R-F1636: discarded non-address registry text (homepage-scrape artifact)")

    # 1. Vault recall first (§15) — did a prior DD already enrich this entity?
    try:
        from . import rag_store as _rs
        hits = await _rs.search(f"{name} registry enrichment vintage", top_k=3)
        if hits:
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Vault recall: {len(hits)} prior memory hit(s) on {name}",
                detail="Prior DD intel recalled from the intel vault (§15 pay-once-remember-forever).",
                source="intel_vault", confidence="ASSESSED",
            ))
            bits.append("vault_recall")
    except Exception:
        pass

    # 2. Wayback domain vintage — proxy for entity age (no incorporation date).
    if domain:
        wb = await _wayback_earliest(domain)
        if wb and wb.get("first_seen"):
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Domain vintage (Wayback): first archived {wb['first_seen']}",
                detail=(f"Domain {domain} first archived {wb['first_seen']} — vintage proxy "
                        f"(no registry incorporation date available)."),
                source=wb.get("snapshot_url") or "web.archive.org",
                confidence="ASSESSED",
            ))
            bits.append(f"wayback:{wb['first_seen']}")
        else:
            report.identity.data_gaps.append(
                f"R-F1636: no Wayback archive for {domain} — possibly a very new domain (age-risk signal)"
            )
            bits.append("wayback:none")

    # 3. Write enrichment to the VAULT so the next DD recalls it ($0) — §15.
    if bits:
        try:
            from . import brain_hook as _bh
            await _bh.absorb(
                module="dd_registry_enrichment",
                summary=f"Registry-gap enrichment for {name}: {', '.join(bits)}",
                detail=f"domain={domain}; jurisdiction={target.get('jurisdiction_iso2', '?')}",
                entity_name=name, success=True, confidence="ASSESSED", source_id="R-F1636",
            )
        except Exception:
            pass

    report.identity.data_gaps.append(
        "R-F1636: registry unavailable — identity enriched from OSINT/vault, NOT registry-verified; "
        "manual registry check still required before transacting."
    )


async def _run_digital(target: dict, report: ARKDDReport, llm: Any, _mode_is_deep: bool = False) -> None:
    """Layer 5 — Digital. web_search multilingual + RAG + neural + (opt.) deep_research.

    When _mode_is_deep is True (orchestrator mode="deep"), deep_researcher
    runs with depth="thorough" (8 search angles × 3 articles, ~30-60s
    and ~$0.10). Otherwise depth="quick" (3 × 2 = 6 articles, ~15s and
    ~$0.03). This keeps the default "standard" DD run under the
    per-run cost cap even with the LLM-backed investigation firing.
    """
    t0 = time.time()
    report.digital.meta.started_at = datetime.now(timezone.utc).isoformat()
    name = report.identity.entity_name or target.get("query", "")

    # R-F299: when name was derived from a hostname (R-F153 fallback) the
    # search query "modirumgespi.com defence procurement" returns nothing
    # useful — same-domain mentions only. Derive a brand-friendly form by
    # stripping the TLD and converting separators to spaces, then run the
    # search against THAT. The original hostname is still preserved in
    # target["name"] for storage/audit.
    name_for_search = _brandify_name_for_search(name, target)

    # ── 5a. Multilingual web search ──
    try:
        from . import web_search
        hits = await web_search.search_multilingual(
            f"{name_for_search} defence procurement",
            max_results=12,
        )
        # Convert SearchResult objects to Evidence dataclasses where possible
        press: list[Evidence] = []
        tier_counts: dict[str, int] = {}
        # R-F1631 — entity-relevance gate: drop search noise at ingestion so the
        # report's "press coverage" actually references the entity (not unrelated
        # academic papers / generic 'security' hits / tangential notices).
        _distinctive = _entity_distinctive_tokens(name_for_search, name, str(target.get("name") or ""))
        _dropped_irrelevant = 0
        for h in hits or []:
            if not _press_hit_is_relevant(
                getattr(h, "title", "") or "", getattr(h, "snippet", "") or "",
                getattr(h, "url", "") or "", _distinctive,
            ):
                _dropped_irrelevant += 1
                continue
            tier = getattr(h, "source_tier", None) or "UNVERIFIED"
            _url_for_tier = getattr(h, "url", None) or ""
            # R-F316 (2026-05-11): apply web_explorer._classify_tier to
            # every press URL. The 21:11 chat output reported 12 press
            # items, ALL tagged UNVERIFIED — the search-aggregator was
            # not classifying Reuters/BBC/Janes/etc. Now the tier
            # classifier runs ON the URL even if the upstream backend
            # didn't supply a source_tier.
            if tier == "UNVERIFIED" and _url_for_tier:
                try:
                    from .web_explorer import _classify_tier as _ct
                    _tier_guess = _ct(_url_for_tier)
                    if _tier_guess and _tier_guess != "UNVERIFIED":
                        tier = _tier_guess
                except Exception:
                    pass
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            press.append(Evidence(
                source=getattr(h, "title", "") or getattr(h, "url", ""),
                source_tier=tier,
                url=getattr(h, "url", None),
                snippet=(getattr(h, "snippet", "") or "")[:400],
                retrieved_at=datetime.now(timezone.utc).isoformat(),
            ))
        # R-F1874 (direct-source-first): mine the entity's OWN website as PRIMARY
        # evidence. A company's own site rarely blocks the datacenter IP the way
        # search engines do, so this is the independence backbone (CLAUDE.md §6) —
        # the DD has real content even when general web search is 403'd/empty.
        # Prepended so primary self-reported content leads the digital evidence.
        _site = ""
        for _c in (target.get("website"), target.get("url"), target.get("domain")):
            if _c and isinstance(_c, str) and _c.strip():
                _site = _c.strip()
                break
        if _site:
            if not _site.lower().startswith(("http://", "https://")):
                _site = "https://" + _site
            try:
                _mined = await _mine_entity_website(_site)
            except Exception as _mine_e:
                _mined = []
                logger.debug("R-F1874 site-mine failed: %s", _mine_e)
            if _mined:
                _primary: list[Evidence] = []
                for _pg in _mined:
                    _primary.append(Evidence(
                        source=f"{_pg.get('label', 'page')}: {_pg.get('title') or _pg.get('url')}"[:200],
                        source_tier="ENTITY_SITE",   # primary, self-reported
                        url=_pg.get("url"),
                        snippet=(_pg.get("text") or "")[:400],
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                    ))
                tier_counts["ENTITY_SITE"] = tier_counts.get("ENTITY_SITE", 0) + len(_primary)
                press = _primary + press     # primary content leads the digital layer
                logger.info("R-F1874: mined %d primary page(s) from entity site %s",
                            len(_primary), _site[:80])
        report.digital.press_coverage = press[:15]
        report.digital.source_tier_breakdown = tier_counts
        if _dropped_irrelevant:
            report.digital.data_gaps.append(
                f"R-F1631: {_dropped_irrelevant} search result(s) excluded as not "
                f"referencing '{name_for_search}' (entity-relevance filter)"
            )
        report.digital.meta.subcalls += 1
        # R-F188 (2026-05-11): when web_search returned 0 hits, run a
        # RAG-only fallback so the report still has SOMETHING for the
        # digital layer rather than rendering "no press coverage" as if
        # the entity were invisible. Tagged MEMORY_ONLY so the caller
        # can see the evidence came from cached memory, not live web.
        if not hits:
            try:
                from . import rag_store as _rs_dd
                rag_results = await _rs_dd.search(name, top_k=10)
                if rag_results:
                    memory_press: list[Evidence] = []
                    for hit in rag_results[:10]:
                        if not isinstance(hit, dict):
                            continue
                        memory_press.append(Evidence(
                            source=hit.get("source", "rag_memory"),
                            source_tier="MEMORY_ONLY",
                            url=hit.get("url"),
                            snippet=(hit.get("text") or hit.get("excerpt") or "")[:400],
                            retrieved_at=datetime.now(timezone.utc).isoformat(),
                        ))
                    report.digital.press_coverage = memory_press
                    tier_counts["MEMORY_ONLY"] = len(memory_press)
                    report.digital.source_tier_breakdown = tier_counts
                    report.digital.data_gaps.append(
                        "R-F188: live web returned 0 — served from RAG memory only"
                    )
                    # Flag on meta so report consumers see degradation
                    setattr(report.digital.meta, "degraded_search", True)
                    logger.info(
                        "[dd] R-F188 RAG-only fallback for %s — %d memory hits",
                        name[:60], len(memory_press),
                    )
            except Exception as _rfe:
                logger.debug("R-F188 RAG fallback failed: %s", _rfe)
    except Exception as e:
        logger.warning("Digital: web_search failed: %s", e)
        report.digital.data_gaps.append(f"web_search failed: {str(e)[:120]}")

    # ── 5b. RAG context ──
    try:
        from . import rag_store
        rag_ctx = await rag_store.get_rag_context(f"{name}", max_chars=2500)
        if rag_ctx and rag_ctx.strip():
            report.digital.knowledge_base_hits = [{"query": name, "excerpt": rag_ctx[:1500]}]
            report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: rag_store failed: %s", e)

    # ── 5c. Neural associations ──
    try:
        from . import neural_memory
        neural = await neural_memory.get_neural_context(name)
        if neural and neural.strip():
            # Pull out first N concept names from the neural block
            for line in neural.split("\n")[:8]:
                if line.strip().startswith("["):
                    report.digital.neural_associations.append(line.strip()[:200])
            report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: neural_memory failed: %s", e)

    # ── 5d. Knowledge base ──
    try:
        from . import knowledge
        kb = knowledge.search_knowledge(name)
        if kb and kb.strip():
            report.digital.knowledge_base_hits.append({"query": name, "excerpt": kb[:1500], "tier": "aria_knowledge"})
            report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: knowledge search failed: %s", e)

    # ── 5e. Deep research (opt-in, LLM-backed) ──
    # Real signature: investigate(llm, topic, depth="quick"|"thorough"|"exhaustive").
    # depth is a STRING enum, not an int, and there is no max_pages or
    # context kwarg. Previous code passed max_pages=10 depth=1 and
    # crashed with "unexpected keyword argument 'max_pages'" on every
    # DD run with DEEP_RESEARCH_ENABLED. Reported silently in
    # digital.data_gaps so the Serban v3 chat output surfaced it.
    #
    # DD orchestrator uses "quick" by default (3 search angles × 2
    # articles = 6 LLM calls — ~30s and ~$0.03 per run) so the
    # digital layer stays within the per-run cost cap. Callers who
    # want the full "thorough" or "exhaustive" depth can pass the
    # mode="deep" flag at orchestration time; the orchestrator maps
    # deep → "thorough" and all other modes → "quick".
    if DEEP_RESEARCH_ENABLED and llm is not None:
        try:
            from . import deep_researcher
            # Always "quick" inside DD — deep mode's value-add is the
            # link_investigator tree walk (rule-based, zero LLM cost).
            # "thorough" was firing 24 LLM calls that exhausted provider
            # rate limits before chat synthesis could run (New Akord
            # Security 2026-04-12 — 3 consecutive timeouts).
            dr_depth = "quick"
            # R-F1812 — base research stays "quick" (cost), but DO run the
            # bounded recursive person drill-down: it's the highest-value DD
            # signal ("who is behind it") and often the ONLY way to name people
            # when the corporate registry is missing/stubbed (e.g. Portugal).
            # Time-guarded inside investigate(); count env-tunable for cost.
            try:
                _dd_people = int(os.getenv("ARIA_DD_PEOPLE", "2"))
            except (ValueError, TypeError):
                _dd_people = 2
            # R-F1823 — seed the drill-down with people the DD ALREADY knows
            # (registry-listed directors), so a director with no web footprint is
            # still PEP/sanctions-investigated, not just listed. Names are
            # taint-sanitized inside investigate().
            _seed = []
            try:
                for _d in (report.identity.directors or []):
                    _nm = _d.get("name") if isinstance(_d, dict) else (_d if isinstance(_d, str) else None)
                    if _nm:
                        _seed.append(_nm)
            except Exception:
                _seed = []
            dr = await deep_researcher.investigate(
                llm, name, depth=dr_depth, investigate_people=_dd_people,
                seed_people=_seed or None)
            if isinstance(dr, dict):
                synth = dr.get("synthesis") or {}
                report.digital.web_footprint = {
                    "summary": (
                        dr.get("summary")
                        or synth.get("executive_summary")
                        or ""
                    )[:1500],
                    "articles_read": dr.get("articles_read", 0),
                    "facts_learned": dr.get("facts_learned", 0),
                    "search_angles": dr.get("search_angles", []),
                    "depth": dr_depth,
                }
                report.digital.meta.subcalls += 1
                # R-F1816 — carry the recursive person drill-down (R-F1812) into
                # the report so named individuals reach WA + web. Without this the
                # dossiers were computed then dropped ("Zero named individuals").
                _people = dr.get("people") or []
                if isinstance(_people, list) and _people:
                    report.digital.people = _people
                    # Surface a HIGH/CRITICAL person as a network-level finding so
                    # it drives the verdict, not just the digital section.
                    for _pp in _people:
                        _dos = _pp.get("dossier") or {} if isinstance(_pp, dict) else {}
                        _risk = (_dos.get("risk_assessment") or "").upper() if isinstance(_dos, dict) else ""
                        if _risk in ("HIGH", "CRITICAL"):
                            report.digital.findings.append(Finding(
                                severity="high" if _risk == "HIGH" else "critical",
                                title=f"Named individual {_pp.get('name','?')} ({_pp.get('role') or 'role unknown'}) — risk {_risk}",
                                source="deep_researcher.investigate_person",
                                confidence="ASSESSED",
                            ))
                # If investigate surfaced its own findings, merge them in.
                for f in (synth.get("key_findings") or [])[:5]:
                    if isinstance(f, str):
                        report.digital.findings.append(Finding(
                            severity="info",
                            title=f[:200],
                            source="deep_researcher.investigate",
                            confidence="ASSESSED",
                        ))
        except Exception as e:
            logger.warning("Digital: deep_research failed: %s", e)
            report.digital.data_gaps.append(f"deep_research failed: {str(e)[:120]}")

    # ── 5e-bis. Domain ownership verification (RDAP) ──
    # MOVED to Layer 1 / identity as of R-F1836 (see _check_domain_ownership):
    # domain ownership is an identity signal, and the digital layer is skipped
    # in quick mode / on hard-stop, so the check never ran there in those modes.
    # It now runs unconditionally inside _run_identity / _run_identity_person.

    # ── 5f. Link-investigator (deep mode only) ──
    # Recursive URL-tree walk seeded from the target's own website (if
    # supplied) or the top-tier press-coverage hit. Rule-based extraction
    # only by default — no LLM cost. Budgets enforced inside the module.
    if _mode_is_deep:
        seed_url = target.get("website") or target.get("domain")
        if not seed_url and report.digital.press_coverage:
            seed_url = next(
                (e.url for e in report.digital.press_coverage if e.url),
                None,
            )
        if seed_url:
            if not seed_url.startswith(("http://", "https://")):
                seed_url = "https://" + seed_url
            try:
                from . import link_investigator
                # R-F340 (2026-05-11): enable LLM-based fact extraction
                # for prose-heavy corporate pages. The rule-based
                # extractor only catches dates / amounts / emails /
                # registration numbers — it can't read prose like
                # "Mehmet Kibar, Chairman" or "Assan manufactures cold-
                # rolled aluminum coils". Live 23:06 evidence:
                # assangroup.com.tr DD fetched 6 corporate pages but
                # the fact list was just "year:1959 amount:$0".
                # When llm is available + we're in deep mode, pass it
                # through with a $0.20 budget cap (40-50 pages of LLM
                # extraction). Gated by ARIA_LINKTREE_LLM_ENABLED=0
                # for cost-sensitive deploys.
                _llm_for_linktree = None
                _llm_budget = 0.0
                if (llm is not None
                        and (os.getenv("ARIA_LINKTREE_LLM_ENABLED", "1") or "1")
                            .lower() not in ("0", "false", "no")):
                    _llm_for_linktree = llm
                    _llm_budget = _env_float(
                        "ARIA_LINKTREE_LLM_BUDGET_USD", 0.20,
                    )
                tree = await link_investigator.investigate_link_tree(
                    seed_url=seed_url,
                    query_context=name,
                    max_depth=2,
                    max_pages=20,
                    wall_budget_s=90,  # +30s for LLM extraction
                    cost_budget_usd=_llm_budget,
                    llm=_llm_for_linktree,
                )
                report.digital.web_footprint = dict(report.digital.web_footprint or {})
                report.digital.web_footprint["link_tree"] = {
                    "tree_id": tree.tree_id,
                    "seed_url": tree.seed_url,
                    "pages_fetched": tree.pages_fetched,
                    "pages_failed": tree.pages_failed,
                    "max_depth_reached": tree.max_depth_reached,
                    "fused_fact_count": len(tree.fused_facts),
                    "budget_exceeded": tree.budget_exceeded,
                    "duration_ms": tree.duration_ms,
                }
                # Surface high-confidence triangulated facts as findings.
                for ff in tree.fused_facts[:8]:
                    if ff.triangulation >= 2:
                        report.digital.findings.append(Finding(
                            severity="info",
                            title=f"link-tree: {ff.kind}={ff.value[:120]} (×{ff.triangulation} sources)",
                            source=f"link_investigator.{tree.tree_id}",
                            confidence="ASSESSED",
                        ))
                report.digital.meta.subcalls += 1

                # R-F301: post-link-tree jurisdiction detection. If the
                # identity layer couldn't infer jurisdiction (no phone,
                # no address, no email, no reg-num pattern), the website
                # text often contains the answer. Set jurisdiction_iso2
                # + country name from the link-tree page text so any
                # downstream layer (Layer 4 country-risk, registry
                # adapters, etc.) can use them.
                try:
                    if not report.identity.jurisdiction_iso2:
                        # R-F301 follow-up (live ev. 2026-05-11): try ALL
                        # detected jurisdictions, not just the winner.
                        # Modirum GESPI page mentions BR + FI + AE + RS +
                        # MK — winner was BR but the Finnish PRH adapter
                        # never got asked. Now we try each adapter in
                        # rank order; whichever returns data wins.
                        all_juris = _detect_all_jurisdictions_in_link_tree(tree)
                        winning_iso2 = None
                        winning_evidence = ""
                        adapter_attempts: list[dict] = []
                        if all_juris:
                            # Always set the highest-voted as the default
                            # jurisdiction for the record (so country-risk
                            # etc. have something to read), even if no
                            # adapter returns data.
                            winning_iso2 = all_juris[0]["iso2"]
                            winning_evidence = all_juris[0]["evidence"]
                            try:
                                from . import registry_adapters as _ra
                                for _candidate in all_juris[:4]:  # cap at top-4 jurisdictions
                                    _cand_iso2 = _candidate["iso2"]
                                    try:
                                        ra_result = await _ra.lookup_entity(
                                            name=name,
                                            jurisdiction_iso2=_cand_iso2,
                                            registration_number=None,
                                        )
                                    except Exception as _ra_inner:
                                        adapter_attempts.append({
                                            "iso2": _cand_iso2,
                                            "status": "errored",
                                            "reason": str(_ra_inner)[:120],
                                        })
                                        continue
                                    found = bool(
                                        ra_result and (
                                            ra_result.get("profile")
                                            or ra_result.get("found")
                                        )
                                    )
                                    adapter_attempts.append({
                                        "iso2": _cand_iso2,
                                        "status": "found" if found else "no_match",
                                    })
                                    if found:
                                        _ra_profile = ra_result.get("profile") or {}
                                        # First adapter to return data WINS
                                        # — it gets to set jurisdiction.
                                        winning_iso2 = _cand_iso2
                                        winning_evidence = _candidate["evidence"]
                                        if not report.identity.directors:
                                            report.identity.directors = (
                                                ra_result.get("officers") or []
                                            )
                                        if not report.identity.registration_number:
                                            report.identity.registration_number = (
                                                _ra_profile.get("company_number")
                                                or ra_result.get("registration_number")
                                            )
                                        if not report.identity.incorporation_date:
                                            report.identity.incorporation_date = (
                                                _ra_profile.get("date_of_creation")
                                                or ra_result.get("incorporation_date")
                                            )
                                        if not report.identity.registered_address:
                                            report.identity.registered_address = (
                                                _ra_profile.get("registered_office_address")
                                                or report.identity.registered_address
                                            )
                                        break
                            except Exception as _ra_e:
                                logger.debug(
                                    "R-F301 multi-juris registry backfill failed: %s",
                                    _ra_e,
                                )

                        if winning_iso2:
                            report.identity.jurisdiction_iso2 = winning_iso2
                            if not report.identity.jurisdiction:
                                report.identity.jurisdiction = (
                                    _ISO2_TO_COUNTRY_HINT.get(winning_iso2, winning_iso2)
                                )
                            _all_iso2s = ", ".join(
                                f"{j['iso2']}({j['score']})" for j in all_juris[:6]
                            )
                            _attempt_summary = ", ".join(
                                f"{a['iso2']}={a['status']}" for a in adapter_attempts
                            )
                            report.identity.findings.append(Finding(
                                severity="info",
                                title=(
                                    f"R-F301: multi-jurisdiction backfill — "
                                    f"all detected: {_all_iso2s}; winning: "
                                    f"{winning_iso2} "
                                    f"({_ISO2_TO_COUNTRY_HINT.get(winning_iso2, '?')})"
                                ),
                                detail=(
                                    f"Evidence: {winning_evidence[:200]}. "
                                    f"Adapter attempts: "
                                    f"{_attempt_summary or 'none'}"
                                ),
                                source="dd_orchestrator.jurisdiction_backfill",
                                confidence="ASSESSED",
                            ))
                            logger.info(
                                "R-F301: multi-juris backfill: candidates=[%s] "
                                "winner=%s (%s); adapter_attempts=[%s]",
                                _all_iso2s,
                                winning_iso2,
                                _ISO2_TO_COUNTRY_HINT.get(winning_iso2, "?"),
                                _attempt_summary or "none",
                            )

                            # R-F312 (2026-05-11): when the identity layer
                            # ran with jurisdiction=None and the sanctions
                            # screen got rejected because `modirumgespi.com`
                            # looked like a hostname (R-F311), we never got
                            # a real screen. Now that R-F301 has resolved
                            # the entity, re-fire the sanctions screen with
                            # the brandified name + UK Ltd alias (if we
                            # also did R-F295 UK backfill on the same run).
                            # This closes the "CLEAN ✅ but hollow" hole the
                            # operator saw on the 21:11 modirumgespi run.
                            try:
                                _brand_name = _brandify_name_for_search(
                                    name, target,
                                )
                                if _brand_name and _brand_name != name:
                                    from . import sanctions as _sanc2
                                    _refire = await _sanc2.screen_with_aliases(
                                        _brand_name,
                                        known_aliases=[name] if name else None,
                                    )
                                    if (_refire and not _refire.get("error")
                                            and _refire.get("matches")):
                                        # Promote findings into identity
                                        from ._sanctions_classify import (
                                            classify_matches as _cm_re,
                                            SEVERITY_RANK as _sr_re,
                                        )
                                        _classified_re = _cm_re(
                                            _refire["matches"],
                                            query_name=_brand_name,
                                        )
                                        _worst_re = _classified_re["worst_severity"]
                                        if _sr_re.get(_worst_re, 0) >= _sr_re.get("amber", 1):
                                            report.identity.findings.append(Finding(
                                                severity=_worst_re,
                                                title=(
                                                    f"R-F312: re-fired sanctions screen "
                                                    f"on brandified name '{_brand_name}' "
                                                    f"after R-F301 backfill — {_worst_re}"
                                                ),
                                                detail=_classified_re["summary"][:400],
                                                source="sanctions.r_f312_refire",
                                                confidence="PROBABLE",
                                            ))
                                            logger.info(
                                                "R-F312: brandified sanctions re-fire "
                                                "surfaced %s match on %r",
                                                _worst_re, _brand_name,
                                            )
                                        else:
                                            report.identity.findings.append(Finding(
                                                severity="info",
                                                title=(
                                                    f"R-F312: re-fired sanctions screen "
                                                    f"on brandified '{_brand_name}' — CLEAN"
                                                ),
                                                detail=(
                                                    f"Original identity-layer screen was "
                                                    f"rejected as 'not entity-shaped' "
                                                    f"because the hostname was passed raw "
                                                    f"(R-F311). After brandify the screen "
                                                    f"ran and returned no risk matches."
                                                ),
                                                source="sanctions.r_f312_refire",
                                                confidence="CONFIRMED",
                                            ))
                                    elif _refire and not _refire.get("error"):
                                        # Clean — emit honest "screen ran"
                                        # finding so chat output stops saying
                                        # "CLEAN ✅" without explanation.
                                        report.identity.findings.append(Finding(
                                            severity="info",
                                            title=(
                                                f"R-F312: re-fired sanctions screen "
                                                f"on brandified '{_brand_name}' — CLEAN"
                                            ),
                                            detail=(
                                                f"Original identity-layer screen was "
                                                f"rejected because the hostname was "
                                                f"passed raw (R-F311 entity-shape gate). "
                                                f"Brandified name screened CLEAN."
                                            ),
                                            source="sanctions.r_f312_refire",
                                            confidence="CONFIRMED",
                                        ))
                            except Exception as _refire_e:
                                logger.debug(
                                    "R-F312 sanctions re-fire failed: %s", _refire_e,
                                )
                except Exception as _jur_e:
                    logger.debug("R-F301 jurisdiction backfill skipped: %s", _jur_e)

                # R-F295: post-link-tree UK detection + CH backfill.
                # The live modirumgespi.com DD (2026-05-11) missed the UK
                # subsidiary `Modirum Defence Consultancy Ltd` because the
                # identity layer's jurisdiction inference fires BEFORE the
                # link-tree runs, so the UK signals on the company page are
                # never seen by the CH gate. Scan fused facts + page titles
                # now; if we find a UK Ltd/plc/LLP suffix paired with a UK
                # address signal AND the identity layer didn't already run
                # CH, fire a deferred CH lookup using the discovered name.
                try:
                    uk_name, uk_evidence = _detect_uk_entity_in_link_tree(tree)
                    if uk_name and not report.identity.registration_number:
                        from . import companies_house as _ch
                        ch_result = await _ch.investigate_uk_entity(
                            company_name=uk_name,
                        )
                        report.digital.meta.subcalls += 1
                        if isinstance(ch_result, dict) and ch_result.get("profile"):
                            profile = ch_result.get("profile") or {}
                            report.identity.registration_number = (
                                profile.get("company_number")
                                or report.identity.registration_number
                            )
                            report.identity.registration_status = (
                                profile.get("company_status")
                                or report.identity.registration_status
                            )
                            report.identity.incorporation_date = (
                                profile.get("date_of_creation")
                                or report.identity.incorporation_date
                            )
                            _addr = profile.get("registered_office_address")
                            if isinstance(_addr, dict):
                                report.identity.registered_address = (
                                    _addr.get("address_snippet")
                                    or report.identity.registered_address
                                )
                            elif _addr and not report.identity.registered_address:
                                report.identity.registered_address = _addr
                            if not report.identity.directors:
                                report.identity.directors = ch_result.get("officers") or []
                            if not report.identity.shareholders:
                                report.identity.shareholders = ch_result.get("psc") or []
                            if not report.identity.jurisdiction_iso2:
                                report.identity.jurisdiction_iso2 = "GB"
                                report.identity.jurisdiction = (
                                    report.identity.jurisdiction or "United Kingdom"
                                )
                            report.identity.findings.append(Finding(
                                severity="info",
                                title=(
                                    f"R-F295: UK entity '{uk_name}' identified via "
                                    f"link-tree backfill — CH={profile.get('company_number')}"
                                ),
                                detail=(
                                    f"Evidence: {uk_evidence[:180]}. "
                                    f"Companies House profile loaded post-digital-layer "
                                    f"because identity layer ran before web content was fetched."
                                ),
                                source="dd_orchestrator.uk_backfill",
                                confidence="PROBABLE",
                            ))
                            logger.info(
                                "R-F295: UK entity backfilled via link-tree: %s → CH=%s",
                                uk_name, profile.get("company_number"),
                            )
                except Exception as _ukfb_e:
                    logger.debug("R-F295 UK backfill skipped: %s", _ukfb_e)

                # ── R-F436 (2026-05-13) — page entity extraction → sanctions ──
                # Pre-R-F436 the rule extractor produced 0 person_name facts
                # from prose-heavy pages, so directors / chairmen / contacts
                # mentioned on the crawled site never re-fed sanctions
                # screening. The rule patterns now emit `person_name` facts
                # (title-prefix, role-suffix, role-prefix). Here we harvest
                # them, dedupe across pages, cap at 8, and per-name screen
                # against OpenSanctions with the entity name passed as
                # legal-name corroboration (R-F434 cap therefore does NOT
                # fire because the screen IS corroborated by the verified
                # entity context).
                try:
                    # R-F449 (2026-05-13) — kill-switch for consistency
                    # with the rest of the Digital chain. Operator can
                    # skip the page-entity sanctions screen if upstream
                    # rate-limited.
                    if os.getenv("ARIA_PAGE_ENTITY_SCREEN_DISABLED", "").strip():
                        # Re-use the existing R-F436 try/except so the
                        # `raise` is caught + debug-logged consistently
                        # with every other R-block kill-switch path.
                        raise RuntimeError(
                            "R-F449: page entity screen disabled via env"
                        )
                    _names_seen: set[str] = set()
                    _candidate_persons: list[str] = []
                    for ff in (tree.fused_facts or []):
                        if ff.kind != "person_name":
                            continue
                        _v = (ff.value or "").strip()
                        _vn = _v.lower()
                        if not _v or _vn in _names_seen:
                            continue
                        # Also skip if it matches the entity name itself
                        if name and _vn == (name or "").lower():
                            continue
                        _names_seen.add(_vn)
                        _candidate_persons.append(_v)
                        if len(_candidate_persons) >= 8:
                            break
                    if _candidate_persons:
                        from . import sanctions as _sanc_p
                        from ._sanctions_classify import (
                            classify_matches as _cm_p,
                            SEVERITY_RANK as _sr_p,
                        )
                        _extracted_persons_screened: list[dict] = []
                        for _person in _candidate_persons:
                            try:
                                _scr = await _sanc_p.screen_with_aliases(
                                    _person,
                                    # Passing the entity name as a known
                                    # alias sets _has_caller_supplied_aliases
                                    # to True (R-F444 renamed flag). This is
                                    # honest corroboration because the person
                                    # was named ON the entity's own website
                                    # — same context.
                                    known_aliases=[name] if name else None,
                                )
                            except Exception as _spe:
                                # R-F1348: a screen that THREW must be VISIBLE in
                                # the report — never silently dropped (a false
                                # negative the operator can't see is the worst
                                # failure mode for a compliance product).
                                logger.warning(
                                    "R-F436 person screen %r failed: %s",
                                    _person[:40], _spe,
                                )
                                _note_dd_screen_gap(report, _person, _spe)
                                continue
                            _matches = (_scr or {}).get("matches") or []
                            if not _matches:
                                _extracted_persons_screened.append({
                                    "person": _person,
                                    "severity": "info",
                                    "matched": False,
                                })
                                continue
                            _classified = _cm_p(_matches, query_name=_person)
                            _worst = _classified.get("worst_severity") or "info"
                            _extracted_persons_screened.append({
                                "person": _person,
                                "severity": _worst,
                                "matched": True,
                                "summary": _classified.get("summary", "")[:240],
                            })
                            # Surface non-info as Digital findings
                            if _sr_p.get(_worst, 0) >= _sr_p["amber"]:
                                _ftitle = (
                                    f"Site-extracted person {_person} → "
                                    f"sanctions {_worst}"
                                )
                                _fconf = {
                                    "hard_stop": "CONFIRMED",
                                    "red": "PROBABLE",
                                    "amber": "ASSESSED",
                                }.get(_worst, "ASSESSED")
                                report.digital.findings.append(Finding(
                                    severity=_worst,
                                    title=_ftitle,
                                    detail=(
                                        f"Name '{_person}' was extracted "
                                        f"from {name}'s own site (link-tree "
                                        f"{tree.tree_id}) and screened "
                                        f"against OpenSanctions with the "
                                        f"entity as legal-name "
                                        f"corroboration. "
                                        f"{_classified.get('summary', '')[:280]}"
                                    ),
                                    source=(
                                        f"page_entity_extractor + "
                                        f"sanctions.screen_with_aliases "
                                        f"[{tree.tree_id}]"
                                    ),
                                    confidence=_fconf,
                                ))
                        # Persist the per-person screen result on the
                        # web_footprint dict so renderers + audit can
                        # cite which names were actually screened.
                        report.digital.web_footprint["extracted_persons"] = (
                            _extracted_persons_screened
                        )
                        report.digital.meta.subcalls += len(_candidate_persons)
                        logger.info(
                            "R-F436: link-tree extracted %d persons, "
                            "screened against sanctions, %d non-info hits",
                            len(_candidate_persons),
                            sum(
                                1 for p in _extracted_persons_screened
                                if p.get("severity") not in ("info", None)
                            ),
                        )
                except Exception as _r436_e:
                    logger.warning(
                        "R-F436 page-entity screen skipped: %s", _r436_e,
                    )
                    _note_dd_screen_gap(report, "page-entity screening block", _r436_e)

                # ── R-F437 (2026-05-13) — Wayback historical pivot ────────
                # Live-only crawl misses the case where a now-clean entity
                # was a sanctioned shell five years ago, or where a director
                # resigned to cover an embarrassing affiliation. We fetch
                # snapshots of the seed URL at ~1y and ~3y back, run the
                # SAME person-name extractor, and surface persons who
                # appeared historically but are absent from the current
                # site. Each new historical person is sanctions-screened
                # with entity-name corroboration. Opt-out via
                # ARIA_WAYBACK_PIVOT_DISABLED=1.
                if not os.getenv("ARIA_WAYBACK_PIVOT_DISABLED", "").strip():
                    try:
                        from .crawl_enhancements import (
                            fetch_historical_snapshots as _fhs,
                        )
                        from .link_investigator import (
                            _extract_facts_rules as _efr,
                        )
                        _current_persons_lc: set[str] = {
                            (ff.value or "").lower()
                            for ff in (tree.fused_facts or [])
                            if ff.kind == "person_name" and ff.value
                        }
                        _snaps = await _fhs(seed_url)
                        # R-F449 — count wayback HTTP fetches that
                        # actually returned content. Pre-R-F449 only
                        # the per-person sanctions screens were counted
                        # so cost dashboards under-reported the chain.
                        report.digital.meta.subcalls += sum(
                            1 for s in _snaps if s.get("ok")
                        )
                        _historical_persons: list[dict] = []
                        for _snap in _snaps:
                            if not _snap.get("ok") or not _snap.get("html"):
                                continue
                            # Strip HTML naively — link_investigator's
                            # extractor expects plain text. The rule patterns
                            # are robust enough for HTML noise; we just
                            # remove tags to reduce false positives in
                            # attribute strings.
                            _html = _snap["html"]
                            _text = re.sub(r"<[^>]+>", " ", _html)
                            _text = re.sub(r"\s+", " ", _text)
                            _hist_facts = _efr(_text[:60000], _snap.get("snapshot_url") or seed_url)
                            for ff in _hist_facts:
                                if ff.kind != "person_name":
                                    continue
                                _v = (ff.value or "").strip()
                                if not _v:
                                    continue
                                _vn = _v.lower()
                                if _vn in _current_persons_lc:
                                    continue  # also on live site → R-F436 covered it
                                if name and _vn == (name or "").lower():
                                    continue
                                if _vn in {p["person"].lower() for p in _historical_persons}:
                                    continue  # dedupe across snapshots
                                _historical_persons.append({
                                    "person": _v,
                                    "snapshot_url": _snap.get("snapshot_url", ""),
                                    "snapshot_timestamp": _snap.get(
                                        "snapshot_timestamp", "",
                                    ),
                                    "years_back": _snap.get("years_back"),
                                })
                                if len(_historical_persons) >= 8:
                                    break
                            if len(_historical_persons) >= 8:
                                break
                        if _historical_persons:
                            from . import sanctions as _sanc_h
                            from ._sanctions_classify import (
                                classify_matches as _cm_h,
                                SEVERITY_RANK as _sr_h,
                            )
                            _hist_screened: list[dict] = []
                            for _hp in _historical_persons:
                                try:
                                    _scr = await _sanc_h.screen_with_aliases(
                                        _hp["person"],
                                        known_aliases=[name] if name else None,
                                    )
                                except Exception as _hspe:
                                    logger.warning(
                                        "R-F437 historical person screen %r failed: %s",
                                        _hp["person"][:40], _hspe,
                                    )
                                    _note_dd_screen_gap(report, _hp.get("person", "historical person"), _hspe)
                                    continue
                                _hmatches = (_scr or {}).get("matches") or []
                                _hclassified = _cm_h(_hmatches, query_name=_hp["person"]) if _hmatches else {"worst_severity": "info", "summary": ""}
                                _hworst = _hclassified.get("worst_severity") or "info"
                                _hist_screened.append({
                                    **_hp,
                                    "severity": _hworst,
                                    "summary": _hclassified.get("summary", "")[:240],
                                })
                                if _sr_h.get(_hworst, 0) >= _sr_h["amber"]:
                                    _hconf = {
                                        "hard_stop": "CONFIRMED",
                                        "red": "PROBABLE",
                                        "amber": "ASSESSED",
                                    }.get(_hworst, "ASSESSED")
                                    report.digital.findings.append(Finding(
                                        severity=_hworst,
                                        title=(
                                            f"Historical (~{_hp.get('years_back')}y) "
                                            f"site person {_hp['person']} → "
                                            f"sanctions {_hworst}"
                                        ),
                                        detail=(
                                            f"Name '{_hp['person']}' appears on the "
                                            f"Wayback snapshot of {seed_url} dated "
                                            f"{_hp.get('snapshot_timestamp', 'unknown')} "
                                            f"but NOT on the current live site. "
                                            f"Sanctions screen post-corroboration: "
                                            f"{_hclassified.get('summary', '')[:280]}"
                                        ),
                                        source=(
                                            f"wayback {_hp.get('snapshot_url', '')}"
                                        ),
                                        confidence=_hconf,
                                    ))
                            report.digital.web_footprint["historical_persons"] = (
                                _hist_screened
                            )
                            report.digital.meta.subcalls += len(_historical_persons)
                            logger.info(
                                "R-F437: wayback pivot found %d historical-only "
                                "persons (snapshots: %d), %d non-info hits",
                                len(_historical_persons),
                                sum(1 for s in _snaps if s.get("ok")),
                                sum(
                                    1 for h in _hist_screened
                                    if h.get("severity") not in ("info", None)
                                ),
                            )
                        else:
                            # Record the attempt + snapshot coverage even
                            # when no new persons surfaced — operator can
                            # see the wayback pivot ran.
                            report.digital.web_footprint["historical_persons"] = []
                            report.digital.web_footprint["wayback_snapshots"] = [
                                {
                                    "years_back": s.get("years_back"),
                                    "ok": s.get("ok"),
                                    "snapshot_timestamp": s.get("snapshot_timestamp", ""),
                                    "error": (s.get("error") or "")[:120],
                                }
                                for s in _snaps
                            ]
                    except Exception as _r437_e:
                        logger.warning(
                            "R-F437 wayback pivot skipped: %s", _r437_e,
                        )
                        _note_dd_screen_gap(report, "historical (wayback) screening block", _r437_e)

                # ── R-F438 (2026-05-13) — Certificate Transparency pivot ──
                # Operators commonly bundle several brand domains onto a
                # single TLS cert, which makes the CT log a free reverse-
                # WHOIS proxy. A sanctioned operator who rebrands behind
                # `freshcorp.com` typically still shares a cert with the
                # embarrassing `oldshell.com` for weeks. We query crt.sh
                # (community-run, no key), extract sibling hostnames from
                # SAN lists, exclude CDN/PaaS infra, and surface the top
                # related domains. Cost: 1 HTTP call per DD.
                # Opt-out via ARIA_CT_PIVOT_DISABLED=1.
                if not os.getenv("ARIA_CT_PIVOT_DISABLED", "").strip():
                    try:
                        from .domain_pivots import (
                            crtsh_lookup as _crtsh,
                            extract_related_domains as _erd,
                        )
                        # Derive apex from seed_url. NOTE: must use
                        # removeprefix("www.") not lstrip("www.") — the
                        # latter strips any of {w, .} from the left and
                        # mangles legitimate hostnames like "wedding.com"
                        # → "edding.com". Verifier caught pre-deploy.
                        from urllib.parse import urlparse as _urlparse
                        _parsed = _urlparse(seed_url)
                        _apex = (_parsed.netloc or "").lower()
                        if _apex.startswith("www."):
                            _apex = _apex[4:]
                        if _apex and "." in _apex:
                            _ct = await _crtsh(_apex)
                            if _ct.get("ok"):
                                _related = _erd(_ct.get("records") or [], _apex)
                                report.digital.web_footprint["ct_log_records"] = len(
                                    _ct.get("records") or []
                                )
                                report.digital.web_footprint["related_domains"] = (
                                    _related
                                )
                                report.digital.meta.subcalls += 1
                                # Surface a single info finding summarising
                                # the pivot — concrete data goes into the
                                # web_footprint for renderer detail.
                                if _related:
                                    _peers_str = ", ".join(
                                        r["host"] for r in _related[:6]
                                    )
                                    _suffix = (
                                        f" (+{len(_related) - 6} more)"
                                        if len(_related) > 6 else ""
                                    )
                                    report.digital.findings.append(Finding(
                                        severity="info",
                                        title=(
                                            f"CT pivot: {len(_related)} related "
                                            f"domain(s) share a TLS cert with "
                                            f"{_apex}"
                                        ),
                                        detail=(
                                            f"Sample: {_peers_str}{_suffix}. "
                                            f"Operator may want to re-DD any "
                                            f"that look ownership-related "
                                            f"(shared registrable root flagged "
                                            f"in web_footprint.related_domains)."
                                        ),
                                        source=(
                                            f"domain_pivots.crtsh_lookup "
                                            f"[crt.sh?q={_apex}]"
                                        ),
                                        confidence="ASSESSED",
                                    ))
                                logger.info(
                                    "R-F438: CT pivot on %s — %d records, "
                                    "%d related domains surfaced",
                                    _apex,
                                    len(_ct.get("records") or []),
                                    len(_related),
                                )
                            else:
                                report.digital.web_footprint["ct_log_error"] = (
                                    _ct.get("error") or "unknown"
                                )[:200]
                    except Exception as _r438_e:
                        logger.debug(
                            "R-F438 CT pivot skipped: %s", _r438_e,
                        )

                # ── R-F439 (2026-05-13) — unified procurement history ─────
                # Pre-R-F439 tender_monitor.py scanned NEW tenders per
                # portal on a schedule; the DD pipeline never asked
                # "what has this entity ever won/bid anywhere?". V1
                # backends (free, no key): USAspending (US federal
                # awards since FY2008) + UK Contracts Finder OCDS. Run
                # in parallel; treat per-backend failure as missing data
                # not "clean". Results land on the pre-existing
                # report.digital.procurement_history field plus a
                # web_footprint summary for renderers.
                # Opt-out: ARIA_PROCUREMENT_HISTORY_DISABLED=1.
                if (
                    report.identity.entity_type == EntityType.COMPANY.value
                    and not os.getenv("ARIA_PROCUREMENT_HISTORY_DISABLED", "").strip()
                ):
                    try:
                        from .procurement_history import query_entity_history
                        _ph = await query_entity_history(
                            report.identity.entity_name or name,
                            jurisdiction_iso2=report.identity.jurisdiction_iso2,
                        )
                        report.digital.procurement_history = (
                            _ph.get("consolidated") or []
                        )
                        report.digital.web_footprint["procurement_history_summary"] = {
                            "total_records": _ph.get("total_records", 0),
                            "by_buyer":      _ph.get("by_buyer", {}),
                            "backends_ok":   [
                                k for k, v in (_ph.get("backends") or {}).items()
                                if v.get("ok")
                            ],
                            "errors":        _ph.get("errors", []),
                        }
                        report.digital.meta.subcalls += 2  # 2 backends
                        # If history is found, emit an info finding so
                        # operator sees the pivot ran + headline figure.
                        _total = _ph.get("total_records", 0)
                        if _total > 0:
                            _buyers = list((_ph.get("by_buyer") or {}).keys())
                            _buyer_sample = ", ".join(_buyers[:4])
                            _suffix = (
                                f" (+{len(_buyers) - 4} more)"
                                if len(_buyers) > 4 else ""
                            )
                            report.digital.findings.append(Finding(
                                severity="info",
                                title=(
                                    f"Procurement history: {_total} award(s) "
                                    f"across "
                                    f"{len(_ph.get('backends', {}))} backends"
                                ),
                                detail=(
                                    f"Buyers: {_buyer_sample}{_suffix}. "
                                    f"Full list in "
                                    f"report.digital.procurement_history."
                                ),
                                source=(
                                    "procurement_history.query_entity_history "
                                    "[USAspending + UK Contracts Finder]"
                                ),
                                confidence="ASSESSED",
                            ))
                        else:
                            # Record the gap so operator can tell
                            # "queried, no results" from "never queried"
                            if not _ph.get("ok"):
                                report.digital.data_gaps.append(
                                    f"Procurement history backends all "
                                    f"failed: {'; '.join(_ph.get('errors', []))[:240]}"
                                )
                        logger.info(
                            "R-F439: procurement history for %s — "
                            "%d records, backends_ok=%s",
                            (report.identity.entity_name or name)[:40],
                            _total,
                            [
                                k for k, v in (_ph.get("backends") or {}).items()
                                if v.get("ok")
                            ],
                        )
                    except Exception as _r439_e:
                        logger.debug(
                            "R-F439 procurement history skipped: %s",
                            _r439_e,
                        )

                # ── R-F440 (2026-05-13) — DNS fingerprint + ASN owner ─────
                # Knowing the hosting ASN reveals shared infrastructure
                # (a "British" entity served from a sanctioned-jurisdiction
                # ASN, or two ostensibly-unrelated entities sharing an
                # obscure ASN). MX/NS records reveal mail + nameserver
                # providers that don't appear in WHOIS. Uses Cloudflare
                # DNS-over-HTTPS (no dependency) + api.iptoasn.com (free,
                # no key). Cost: ~5 HTTP requests per DD.
                # Opt-out: ARIA_DNS_FINGERPRINT_DISABLED=1.
                if not os.getenv("ARIA_DNS_FINGERPRINT_DISABLED", "").strip():
                    try:
                        from .domain_pivots import dns_fingerprint as _dnsfp
                        # Reuse the apex from R-F438; if R-F438 was skipped
                        # we derive it from seed_url.
                        from urllib.parse import urlparse as _urlparse2
                        _parsed2 = _urlparse2(seed_url)
                        _apex2 = (_parsed2.netloc or "").lower()
                        if _apex2.startswith("www."):
                            _apex2 = _apex2[4:]
                        if _apex2 and "." in _apex2:
                            _dns = await _dnsfp(_apex2)
                            if _dns.get("ok"):
                                report.digital.web_footprint["dns_fingerprint"] = {
                                    "domain":      _dns.get("domain"),
                                    "mx_count":    len(_dns.get("mx") or []),
                                    "ns_count":    len(_dns.get("ns") or []),
                                    "txt_count":   len(_dns.get("txt") or []),
                                    "primary_ip":  _dns.get("primary_ip"),
                                    "primary_asn": _dns.get("primary_asn"),
                                    "mx":          _dns.get("mx") or [],
                                    "ns":          _dns.get("ns") or [],
                                    "errors":      _dns.get("errors") or [],
                                }
                                report.digital.meta.subcalls += 5  # 4 DoH + 1 ASN
                                _asn = _dns.get("primary_asn") or {}
                                _asn_desc = _asn.get("description") or ""
                                _asn_cc = _asn.get("country") or ""
                                if _asn_desc:
                                    report.digital.findings.append(Finding(
                                        severity="info",
                                        title=(
                                            f"DNS/ASN: {_apex2} hosted on "
                                            f"AS{_asn.get('as_number')} "
                                            f"({_asn_desc[:80]}, {_asn_cc})"
                                        ),
                                        detail=(
                                            f"Primary IP {_dns.get('primary_ip')}; "
                                            f"MX records: {len(_dns.get('mx') or [])}; "
                                            f"NS records: {len(_dns.get('ns') or [])}; "
                                            f"TXT records: {len(_dns.get('txt') or [])}. "
                                            f"ASN country differing from declared "
                                            f"jurisdiction can indicate shared "
                                            f"or undisclosed hosting."
                                        ),
                                        source=(
                                            "domain_pivots.dns_fingerprint "
                                            "[Cloudflare DoH + api.iptoasn.com]"
                                        ),
                                        confidence="CONFIRMED",
                                    ))
                                # If declared jurisdiction differs from ASN
                                # country, surface as amber (worth checking).
                                _decl_iso2 = (
                                    report.identity.jurisdiction_iso2 or ""
                                ).upper()
                                if (
                                    _decl_iso2 and _asn_cc
                                    and _decl_iso2 != _asn_cc.upper()
                                    # Allow common multi-jurisdiction hosters
                                    # without crying wolf — pure heuristic.
                                    and _asn_cc.upper() not in ("US", "GB", "DE", "NL", "FR", "IE")
                                ):
                                    report.digital.findings.append(Finding(
                                        severity="amber",
                                        title=(
                                            f"ASN country {_asn_cc} differs "
                                            f"from declared jurisdiction "
                                            f"{_decl_iso2}"
                                        ),
                                        detail=(
                                            f"{_apex2} resolves to an IP in "
                                            f"AS{_asn.get('as_number')} "
                                            f"({_asn_desc[:80]}), registered "
                                            f"in {_asn_cc}. Entity declares "
                                            f"jurisdiction {_decl_iso2}. "
                                            f"Could be legitimate (CDN edge, "
                                            f"shared hoster) but worth checking "
                                            f"if combined with other red flags."
                                        ),
                                        source=(
                                            "domain_pivots.dns_fingerprint "
                                            "[ASN-vs-jurisdiction mismatch]"
                                        ),
                                        confidence="ASSESSED",
                                    ))
                                logger.info(
                                    "R-F440: DNS fingerprint %s — IP=%s "
                                    "ASN=%s (%s, %s)",
                                    _apex2,
                                    _dns.get("primary_ip"),
                                    _asn.get("as_number"),
                                    _asn_desc[:40],
                                    _asn_cc,
                                )
                            elif _dns.get("errors"):
                                report.digital.web_footprint["dns_fingerprint_error"] = (
                                    "; ".join(_dns.get("errors") or [])[:240]
                                )
                    except Exception as _r440_e:
                        logger.debug(
                            "R-F440 DNS fingerprint skipped: %s", _r440_e,
                        )

                # ── R-F441 (2026-05-13) — source-language adverse media ──
                # The 11-lang fanout (_LANG_FANOUT_TRIGGERS) detects which
                # target languages a query is relevant for but passes a
                # GENERIC search string. R-F441 builds adverse-media-
                # specific queries per language (corruption/sanctions/
                # fraud/money-laundering/bribery/embezzlement/investigation
                # in the native script) so the search probes the
                # untranslated reporting where the real evidence lives.
                # Opt-out: ARIA_ADVERSE_POLYGLOT_DISABLED=1.
                # R-F449 (2026-05-13) — empty-name guard. Pre-R-F449 an
                # entity with no resolved name would build polyglot
                # queries against an empty entity string — the OR-block
                # of bare adverse terms would dredge up unrelated
                # corruption / sanctions news and pollute
                # adverse_media_hits. Skip the block entirely instead.
                _r441_entity = (
                    report.identity.entity_name or name or ""
                ).strip()
                if (
                    not os.getenv("ARIA_ADVERSE_POLYGLOT_DISABLED", "").strip()
                    and _r441_entity
                ):
                    try:
                        from .adverse_media_polyglot import (
                            build_queries_for_languages as _bqfl,
                        )
                        from .web_explorer import (
                            detect_language_fanout as _dlf,
                        )
                        # Compose a "language hint" from entity name +
                        # declared country so the fanout fires per
                        # country/region. We don't run the actual
                        # cross-language search here (would add 10s+)
                        # — instead we surface the QUERIES on the
                        # web_footprint so the operator + downstream
                        # researchers know what to probe + dashboards
                        # can render the polyglot reach. The autonomous
                        # researcher already runs polyglot queries
                        # asynchronously via search_multilingual; this
                        # block exposes the per-DD adverse-media plan.
                        _lang_hint = (
                            f"{_r441_entity} "
                            f"{report.identity.jurisdiction or ''}"
                        ).strip()
                        _langs_detected = _dlf(_lang_hint)
                        # Always probe English baseline + auto-detected langs
                        _langs_to_query = ["en"] + [
                            l for l in _langs_detected if l != "en"
                        ]
                        _queries = _bqfl(_r441_entity, _langs_to_query)
                        if _queries:
                            report.digital.web_footprint["adverse_media_queries"] = [
                                {"lang": l, "query": q}
                                for l, q in _queries
                            ]
                            logger.info(
                                "R-F441: polyglot adverse-media — %d queries "
                                "across langs %s",
                                len(_queries),
                                [l for l, _ in _queries],
                            )

                            # ── R-F445 (2026-05-13) — execute the polyglot queries ──
                            # Pre-R-F445 R-F441 only SURFACED the queries on
                            # web_footprint with no executor — operator-visibility
                            # only, no actual search recall improvement.
                            # R-F445 wires them through search_multilingual so
                            # the adverse-media probes actually run. Cap at top 4
                            # languages and 5 results per language to bound cost.
                            # Opt-out via ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED=1.
                            _adverse_hits: list[dict] = []
                            if not os.getenv(
                                "ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED",
                                "",
                            ).strip():
                                try:
                                    from . import web_search as _ws
                                    import asyncio as _aio_p

                                    async def _run_one(lang, q):
                                        try:
                                            return lang, await _ws.search_multilingual(
                                                q,
                                                languages=[lang],
                                                max_results=5,
                                                translate_query=False,
                                            )
                                        except Exception as _wse:
                                            logger.debug(
                                                "R-F445 search_multilingual %r/%r failed: %s",
                                                lang, q[:40], _wse,
                                            )
                                            return lang, []

                                    _capped = _queries[:4]
                                    _results = await _aio_p.gather(
                                        *(_run_one(l, q) for l, q in _capped),
                                        return_exceptions=False,
                                    )
                                    for _lang, _res_list in _results:
                                        for _r in (_res_list or [])[:5]:
                                            _adverse_hits.append({
                                                "lang":    _lang,
                                                "title":   getattr(_r, "title", "")[:240],
                                                "url":     getattr(_r, "url", "")[:500],
                                                "snippet": (
                                                    getattr(_r, "snippet", "")
                                                    or getattr(_r, "summary", "")
                                                    or ""
                                                )[:400],
                                            })
                                    report.digital.web_footprint["adverse_media_hits"] = (
                                        _adverse_hits
                                    )
                                    report.digital.meta.subcalls += len(_capped)
                                    logger.info(
                                        "R-F445: polyglot adverse-media executed — "
                                        "%d langs probed, %d hits aggregated",
                                        len(_capped),
                                        len(_adverse_hits),
                                    )
                                except Exception as _r445_e:
                                    logger.debug(
                                        "R-F445 polyglot execute skipped: %s",
                                        _r445_e,
                                    )

                            _detail = (
                                f"Per-language adverse-term probes from "
                                f"source-language dictionaries (corruption / "
                                f"sanctions / fraud / money-laundering / "
                                f"bribery / etc). {len(_queries)} queries "
                                f"prepared; R-F445 executed top {min(4, len(_queries))} "
                                f"and aggregated "
                                f"{len(_adverse_hits)} hit(s) into "
                                f"web_footprint.adverse_media_hits."
                            )
                            report.digital.findings.append(Finding(
                                severity="info",
                                title=(
                                    f"Polyglot adverse-media: "
                                    f"{len(_queries)} language(s) — "
                                    f"{', '.join(l for l, _ in _queries)} "
                                    f"({len(_adverse_hits)} hit(s))"
                                ),
                                detail=_detail,
                                source=(
                                    "adverse_media_polyglot + "
                                    "web_search.search_multilingual"
                                ),
                                confidence="ASSESSED",
                            ))
                    except Exception as _r441_e:
                        logger.debug(
                            "R-F441 polyglot adverse media skipped: %s",
                            _r441_e,
                        )
            except Exception as e:
                logger.warning("Digital: link_investigator failed: %s", e)
                report.digital.data_gaps.append(f"link_investigator failed: {str(e)[:120]}")

    report.digital.meta.duration_ms = int((time.time() - t0) * 1000)
    report.digital.meta.status = LayerStatus.OK.value


async def _run_verification(target: dict, report: ARKDDReport) -> None:
    """Layer 3 — Source triangulation + conflict detection (NOT
    independent source verification).

    R-F393 (2026-05-13): the legacy "verification" name was a Phase A
    honesty bug — ARIA self-reported the layer as "wired-but-silent"
    after a Lukoil DD returned 0% grounded. The honest description is
    what this function actually does:

      (a) Count how many independent sources back each claim that
          Layers 1/2/4/5 already collected — `triangulated_claims`.
      (b) Compute `grounded_rate` = fraction of claims with >= 2
          sources (NOT a URL-verification rate).
      (c) Detect conflicts between sections (e.g. ghost=GREEN while
          country=HARD_STOP).
      (d) Pick the weakest confidence tag across all sections.

    What this function does NOT do (operator-visible via the new
    scope-flag fields on VerificationSection):

      *  Independent URL re-fetch and claim re-check against external
         sources — `source_verifier.py` exists but is not invoked from
         the orchestrator. `independent_source_verification_run` is
         set to False below to surface this honestly.
    """
    t0 = time.time()
    report.verification.meta.started_at = datetime.now(timezone.utc).isoformat()
    # R-F393: pin the honest scope on the section the moment the
    # function fires, so a mid-flight crash still leaves the truth
    # visible to downstream consumers.
    report.verification.independent_source_verification_run = False
    report.verification.scope_note = (
        "Layer 3 = source triangulation + conflict detection over "
        "Layers 1/2/4/5 outputs. Independent source verification "
        "(URL re-fetch via source_verifier) is NOT invoked — grounded_rate "
        "is a triangulation rate, not a URL-verified rate."
    )

    # Count sources per material claim. A "claim" here is a distinct
    # piece of evidence/finding from any section. The verifier counts
    # how many independent sources back each.
    sources_for_claim: dict[str, set[str]] = {}
    def _add(claim: str, src: str):
        sources_for_claim.setdefault(claim, set()).add(src)

    # Identity claims
    if report.identity.sanctions_screen:
        _add("identity:sanctions_checked", "sanctions")
    if report.identity.directors:
        _add("identity:directors_known", "companies_house")
    if report.identity.ghost_score:
        _add("identity:ghost_scored", "ghost_scorer")
    # Network claims
    if report.network.director_graph.get("nodes"):
        _add("network:graph_built", "network_walker")
    if report.network.pep_connections:
        _add("network:pep_checked", "sanctions")
    # Compliance
    if report.compliance.country_risk:
        _add("compliance:country_risk_known", "risk_indices")
    if report.compliance.export_control:
        _add("compliance:export_classified", "tech_classifier")
    if report.compliance.regional_bloc_requirements:
        _add("compliance:regional_framework_cited", "rag:regional_compliance")
    # Digital
    if report.digital.press_coverage:
        for p in report.digital.press_coverage[:10]:
            _add("digital:press_coverage", p.source or "press")
    if report.digital.knowledge_base_hits:
        _add("digital:knowledge_base_hits", "aria_knowledge")
    if report.digital.neural_associations:
        _add("digital:neural_associations", "neural_memory")

    triangulated = [
        {"claim": k, "sources": sorted(list(v)), "source_count": len(v)}
        for k, v in sources_for_claim.items()
    ]
    report.verification.triangulated_claims = triangulated

    # Grounded rate: fraction of claims backed by at least 2 independent
    # sources. Not identical to source_verifier's URL-based rate, but
    # the right shape for a DD report.
    if triangulated:
        grounded = sum(1 for t in triangulated if t["source_count"] >= 2)
        report.verification.grounded_rate = round(grounded / len(triangulated), 2)
    else:
        report.verification.grounded_rate = None

    # Conflict detection — look for contradictions in ghost score
    # classification vs country risk headline
    ghost_cls = (report.identity.ghost_score or {}).get("classification", "")
    country_headline = (report.compliance.country_risk or {}).get("headline_risk", "")
    if ghost_cls in ("GREEN",) and country_headline in ("RED", "HARD_STOP"):
        report.verification.conflicts.append({
            "type": "classification_mismatch",
            "detail": f"ghost={ghost_cls} but country={country_headline}",
            "resolution": "use worst-case — promote overall to country's level",
        })

    # Confidence floor: worst tag across all sections.
    # Includes Layer 5c (commercial_coherence) -- it runs BEFORE verification
    # in the orchestrator (lines 2737 vs 2845), so its anomalies/findings
    # are already populated by this point. Excluding it meant a HIGH-tier
    # commercial_coherence anomaly (e.g. licence-chain gap) wouldn't drag
    # the report's confidence floor down, which it should.
    # `report.verification` is included for consistency but contributes
    # nothing today (no findings populated until later in this same call).
    all_confidences = ["ASSESSED"]  # baseline
    for section in (
        report.identity,
        report.network,
        report.verification,
        report.compliance,
        report.digital,
        report.commercial_coherence,
    ):
        for f in getattr(section, "findings", []) or []:
            all_confidences.append(getattr(f, "confidence", "ASSESSED"))
    report.verification.confidence_floor = weakest_confidence(all_confidences)

    # Pull in unverified claim count from source_verifier IF we have
    # any tool_context blob to verify. The orchestrator isn't invoking
    # source_verifier against LLM outputs (no LLM outputs yet here),
    # so this is a structural placeholder.
    report.verification.unverified_claim_count = sum(
        1 for t in triangulated if t["source_count"] < 2
    )

    report.verification.meta.duration_ms = int((time.time() - t0) * 1000)
    report.verification.meta.status = LayerStatus.OK.value


async def _run_synthesis(target: dict, report: ARKDDReport) -> None:
    """Layer 6 — Synthesis. ACH matrix + final ghost score + risk
    classification + SAR trigger."""
    t0 = time.time()
    report.synthesis.meta.started_at = datetime.now(timezone.utc).isoformat()

    # ── 6a. Ghost score roll-up (authoritative) ──
    # Person DD doesn't have a ghost score — ghost detection is a
    # company-only signal (founding date, registered address pattern,
    # website age, etc.). Skip for persons so the synthesis layer
    # doesn't emit "Ghost score: 0/20 — GREEN" which is misleading.
    _is_person = (report.identity.entity_type or "").lower() == "person"
    ghost = report.identity.ghost_score or {}
    if _is_person:
        report.synthesis.ghost_score_total = 0
        report.synthesis.ghost_classification = ""
    else:
        report.synthesis.ghost_score_total = int(ghost.get("total") or 0)
        report.synthesis.ghost_classification = str(ghost.get("classification") or "GREEN")

    # ── 6b. Risk classification — worst-case aggregation ──
    # Tiers in ascending severity
    severity_rank = {
        "GREEN":       0,
        "AMBER-LIGHT": 1,
        "AMBER":       1,
        "AMBER-DARK":  2,
        "RED":         3,
        "HARD STOP":   4,
        "HARD_STOP":   4,
    }
    candidates: list[str] = []
    if report.synthesis.ghost_classification and not _is_person:
        candidates.append(report.synthesis.ghost_classification)
    if report.compliance.country_risk.get("headline_risk"):
        candidates.append(report.compliance.country_risk["headline_risk"])
    # Any hard_stop finding anywhere? Includes digital, verification, and
    # commercial_coherence -- the prior list of three layers missed
    # hard-stops surfaced by Layer 5c (e.g. licence-chain gaps that
    # constitute strict-liability offences in some jurisdictions) and
    # by digital-layer OSINT findings (e.g. confirmed sanctions match
    # surfaced by deep_research that didn't propagate up to identity).
    for section in (report.identity, report.network, report.compliance,
                    report.digital, report.verification,
                    report.commercial_coherence):
        for f in getattr(section, "findings", []) or []:
            if getattr(f, "severity", "") == "hard_stop":
                candidates.append("HARD_STOP")
                break

    # Commercial coherence contribution (2026-04-22, Layer 5c)
    # HIGH + sector_mismatch (fronting pattern) → RED.
    # HIGH alone → AMBER-DARK. ELEVATED → AMBER-LIGHT. GREEN → neutral.
    _cc_tier_for_risk = (report.commercial_coherence.tier or "GREEN").upper()
    _cc_has_sector_mismatch = any(
        a.get("kind") == "sector_mismatch"
        for a in (report.commercial_coherence.anomalies or [])
    )
    if _cc_tier_for_risk == "HIGH" and _cc_has_sector_mismatch:
        candidates.append("RED")
    elif _cc_tier_for_risk == "HIGH":
        candidates.append("AMBER-DARK")
    elif _cc_tier_for_risk == "ELEVATED":
        candidates.append("AMBER-LIGHT")

    if candidates:
        worst = max(candidates, key=lambda c: severity_rank.get(c, 0))
    else:
        worst = "GREEN"

    # Normalise to canonical RiskClassification values
    canonical_map = {
        "GREEN":        RiskClassification.GREEN.value,
        "AMBER-LIGHT":  RiskClassification.AMBER_LIGHT.value,
        "AMBER":        RiskClassification.AMBER_LIGHT.value,
        "AMBER-DARK":   RiskClassification.AMBER_DARK.value,
        "RED":          RiskClassification.RED.value,
        "HARD STOP":    RiskClassification.HARD_STOP.value,
        "HARD_STOP":    RiskClassification.HARD_STOP.value,
    }
    report.synthesis.risk_classification = canonical_map.get(worst, RiskClassification.GREEN.value)
    report.risk_classification = report.synthesis.risk_classification

    # ── 6b2. Confidence gate — never GREEN when verification is insufficient ──
    # If risk landed on GREEN but key identity signals are missing, bump
    # to AMBER-LIGHT with MANUAL REVIEW flag. This prevents a clean bill
    # when ARIA couldn't actually verify the entity.
    if report.risk_classification == RiskClassification.GREEN.value and not _is_person:
        _needs_manual = False
        _gate_reasons: list[str] = []

        # Registry not verified? Require actual substance — status alone is not enough.
        _has_registry_status = bool(report.identity.registration_status)
        _has_directors = bool(report.identity.directors)
        _has_inc_date = bool(report.identity.incorporation_date)
        _has_substance = _has_directors or (_has_registry_status and _has_inc_date)
        if not _has_substance:
            _needs_manual = True
            _missing = []
            if not _has_registry_status:
                _missing.append("registration status")
            if not _has_directors:
                _missing.append("directors/officers")
            if not _has_inc_date:
                _missing.append("incorporation date")
            _gate_reasons.append(f"registry verification incomplete (missing: {', '.join(_missing)})")

        # Too many data gaps? Fallback iteration includes
        # commercial_coherence so a Layer 5c gap (e.g. unknown payment
        # market, untraceable corporate substance) actually counts toward
        # the manual-review trigger.
        _total_gaps = len(report.data_gaps_summary) if hasattr(report, "data_gaps_summary") else 0
        if not hasattr(report, "data_gaps_summary"):
            _total_gaps = sum(
                len(getattr(s, "data_gaps", []) or [])
                for s in (report.identity, report.network, report.verification,
                          report.compliance, report.digital,
                          report.commercial_coherence)
            )
        if _total_gaps >= 3:
            _needs_manual = True
            _gate_reasons.append(f"{_total_gaps} unresolved data gaps")

        # Ghost score has unresolved indicators?
        _ghost = report.identity.ghost_score or {}
        _indicators = _ghost.get("indicators", [])
        if isinstance(_indicators, list):
            _unresolved = sum(1 for ind in _indicators if isinstance(ind, dict) and ind.get("value") == "?")
        else:
            _unresolved = sum(1 for v in _indicators.values() if v == "?") if isinstance(_indicators, dict) else 0
        if _unresolved >= 4:
            _needs_manual = True
            _gate_reasons.append(f"ghost score has {_unresolved} unresolved indicators")

        if _needs_manual:
            report.risk_classification = RiskClassification.AMBER_LIGHT.value
            report.synthesis.risk_classification = RiskClassification.AMBER_LIGHT.value
            report.identity.findings.append(Finding(
                severity="amber",
                title="Confidence gate: GREEN overridden to AMBER — manual review required",
                detail=f"Reasons: {'; '.join(_gate_reasons)}. ARIA cannot issue a GREEN clearance without sufficient verification.",
                source="dd_orchestrator.confidence_gate",
                confidence="ASSESSED",
            ))
            # R-F298: stamp the report so the BLUF assembly can tell the
            # difference between "AMBER because of a real risk finding"
            # and "AMBER because the data was too thin to issue GREEN".
            # The current AMBER-LIGHT BLUF says "can proceed with enhanced
            # DD" — that's wrong for confidence-gate-only AMBER, where
            # the honest reading is "INSUFFICIENT EVIDENCE, can't issue
            # a verdict either way".
            report.confidence_gate_triggered = True
            report.confidence_gate_reasons = list(_gate_reasons)
            _entity_name = report.identity.entity_name or target.get("name", "?")
            logger.info("Confidence gate: GREEN → AMBER for %s (%s)", _entity_name, "; ".join(_gate_reasons))

    # ── 6c. SAR trigger — UK POCA / FATF typology ──
    # Triggers (original):
    #   - sanctions hit on subject OR director
    #   - ghost score >= 12 (RED) combined with layered secrecy chain
    #   - transaction value >= 100k with no declared activity
    # Extended triggers (FATF typology / POCA indicators):
    #   - ghost score >= 12 AND no directors (layered secrecy)
    #   - multiple jurisdictions with no apparent business reason
    #   - PEP + opaque ownership (PSC not disclosed)
    #   - entity registered < 12 months with large scope
    #   - director on multiple sanctioned boards
    #   - address cluster with 5+ co-located entities (mail-drop)
    sar_reasons: list[str] = []
    if any("sanctions" in str(f.title).lower() and "hit" in str(f.title).lower()
           for f in report.identity.findings):
        sar_reasons.append("sanctions hit on identity layer")
    if any("hit on sanctions" in str(f.title).lower()
           for f in report.network.findings):
        sar_reasons.append("sanctions hit in network layer (one-hop)")
    if report.synthesis.ghost_score_total >= 12:
        sar_reasons.append(f"ghost score {report.synthesis.ghost_score_total}/20 at RED threshold")

    # SAR-ext-1: ghost score >= 12 AND no directors (layered secrecy)
    _has_directors = bool(report.identity.directors)
    if report.synthesis.ghost_score_total >= 12 and not _has_directors:
        sar_reasons.append("ghost score >= 12 with no directors on file — layered secrecy indicator")

    # SAR-ext-2: multiple jurisdictions with no apparent business reason
    _jurisdictions_seen: set[str] = set()
    if report.identity.jurisdiction_iso2:
        _jurisdictions_seen.add(report.identity.jurisdiction_iso2)
    for _cl in report.network.cross_linked_entities:
        _j = _cl.get("jurisdiction") or _cl.get("jurisdiction_iso2") or ""
        if _j:
            _jurisdictions_seen.add(_j.upper()[:2])
    if len(_jurisdictions_seen) >= 3 and not report.identity.declared_activity:
        sar_reasons.append(
            f"entity spans {len(_jurisdictions_seen)} jurisdictions with no declared activity — complex structuring"
        )

    # SAR-ext-3: PEP connection combined with opaque ownership (no PSC / UBO)
    _has_pep = bool(report.network.pep_connections)
    _has_ubo = bool(report.identity.ubo_chain)
    _has_shareholders = bool(report.identity.shareholders)
    if _has_pep and not _has_ubo and not _has_shareholders:
        sar_reasons.append("PEP connection with no disclosed PSC/UBO — opaque ownership")

    # SAR-ext-4: entity registered < 12 months with large transaction scope
    _inc_date_str = report.identity.incorporation_date
    if _inc_date_str and not _is_person:
        try:
            from datetime import date as _date_type
            _inc_date = _date_type.fromisoformat(str(_inc_date_str)[:10])
            _age_days = (datetime.now(timezone.utc).date() - _inc_date).days
            if _age_days < 365 and report.identity.declared_activity:
                sar_reasons.append(
                    f"entity incorporated < 12 months ago ({_age_days} days) — newco with declared activity"
                )
        except (ValueError, TypeError):
            pass  # unparseable date — skip

    # SAR-ext-5: director appears on multiple sanctioned entities' boards
    _director_sanctions_hits = [
        f for f in report.identity.findings
        if getattr(f, "source", "") == "sanctions.director_screen"
        and getattr(f, "severity", "") in ("hard_stop", "red")
    ]
    if len(_director_sanctions_hits) >= 2:
        sar_reasons.append(
            f"{len(_director_sanctions_hits)} directors flagged on sanctions/debarment lists — cross-board exposure"
        )

    # SAR-ext-6: address cluster — entity shares address with 5+ others (mail-drop)
    _addr_cluster = report.network.address_cluster or {}
    _max_colocated = 0
    for _addr, _info in _addr_cluster.items() if isinstance(_addr_cluster, dict) else []:
        _n = len(_info) if isinstance(_info, list) else int(_info.get("count", 0)) if isinstance(_info, dict) else 0
        _max_colocated = max(_max_colocated, _n)
    if _max_colocated >= 5:
        sar_reasons.append(
            f"address shared with {_max_colocated}+ entities — mail-drop indicator"
        )

    if sar_reasons:
        report.synthesis.sar_trigger = True
        report.synthesis.sar_rationale = " · ".join(sar_reasons)

    # ── 6d. ACH matrix (structural) ──
    # Three hypotheses by default:
    #   H1: entity is a legitimate counterparty suitable for BD
    #   H2: entity is a higher-risk counterparty requiring enhanced DD
    #   H3: entity is a shell / concealment vehicle — refuse
    hypotheses = {
        "H1_legit": {"label": "Legitimate BD counterparty", "support": 0, "against": 0},
        "H2_enhanced": {"label": "Higher-risk, enhanced DD required", "support": 0, "against": 0},
        "H3_shell": {"label": "Shell / concealment vehicle — refuse", "support": 0, "against": 0},
    }
    ghost_total = report.synthesis.ghost_score_total
    if ghost_total <= 3:
        hypotheses["H1_legit"]["support"] += 3
        hypotheses["H3_shell"]["against"] += 3
    elif ghost_total <= 7:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1
    elif ghost_total <= 11:
        hypotheses["H2_enhanced"]["support"] += 3
        hypotheses["H3_shell"]["support"] += 1
    elif ghost_total <= 15:
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 3
    else:
        hypotheses["H3_shell"]["support"] += 5
        hypotheses["H1_legit"]["against"] += 5

    # Country risk contribution
    country_headline = (report.compliance.country_risk or {}).get("headline_risk")
    if country_headline in ("RED", "HARD_STOP"):
        hypotheses["H2_enhanced"]["support"] += 1
        hypotheses["H3_shell"]["support"] += 1
    elif country_headline == "AMBER":
        hypotheses["H2_enhanced"]["support"] += 1

    # Sanctions hit → H3 strongly favoured
    if report.synthesis.sar_trigger:
        hypotheses["H3_shell"]["support"] += 5
        hypotheses["H1_legit"]["against"] += 5

    # ── ACH-ext-1: Verification quality ──
    _grounded = report.verification.grounded_rate
    _conflicts = report.verification.conflicts
    if _grounded is not None and _grounded < 0.4:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1
    elif _grounded is not None and _grounded >= 0.8:
        hypotheses["H1_legit"]["support"] += 1
    if _conflicts:
        hypotheses["H2_enhanced"]["support"] += 1
        hypotheses["H3_shell"]["support"] += 1
        hypotheses["H1_legit"]["against"] += 1

    # ── ACH-ext-2: Digital footprint ──
    _has_press = bool(report.digital.press_coverage)
    _has_procurement = bool(report.digital.procurement_history)
    if not _has_press and not _has_procurement and not _is_person:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H3_shell"]["support"] += 1
        hypotheses["H1_legit"]["against"] += 1
    elif _has_press and _has_procurement:
        hypotheses["H1_legit"]["support"] += 2

    # ── ACH-ext-3: Network red flags ──
    if report.network.pep_connections:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1
    if report.network.sanctions_network:
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 2

    # ── ACH-ext-4: Director screening ──
    _dir_sanctions = any(
        getattr(f, "source", "") == "sanctions.director_screen"
        and getattr(f, "severity", "") in ("hard_stop", "red")
        for f in report.identity.findings
    )
    if _dir_sanctions:
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 3

    # ── ACH-ext-5: Registration anomalies — empty company ──
    _no_directors = not report.identity.directors
    _no_inc_date = not report.identity.incorporation_date
    if _no_directors and _no_inc_date and not _is_person:
        hypotheses["H3_shell"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 2

    # ── ACH-ext-6: Export control ──
    _ec = report.compliance.export_control or {}
    _ec_class = (_ec.get("classification") or _ec.get("rating") or "").upper()
    if _ec_class in ("RESTRICTED", "CONTROLLED", "ML", "MTCR", "WA-CAT1", "WA-CAT2"):
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1

    # ── ACH-ext-7: Commercial coherence (Layer 5c, 2026-04-22) ──
    # A HIGH tier (score < 0.55) means the deal structure itself is
    # incoherent — strong signal for H2 enhanced-DD, and if combined with
    # other red signals pushes H3 (shell/fronting). ELEVATED is a soft
    # signal. GREEN is neutral.
    _cc_section = report.commercial_coherence
    _cc_tier = (_cc_section.tier or "GREEN").upper()
    if _cc_tier == "HIGH":
        hypotheses["H2_enhanced"]["support"] += 3
        hypotheses["H3_shell"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 2
    elif _cc_tier == "ELEVATED":
        hypotheses["H2_enhanced"]["support"] += 1
        hypotheses["H1_legit"]["against"] += 1
    # Sector mismatch (non-defence SIC claiming defence deal) — classic
    # fronting pattern. Treat as a hard H3 signal on its own.
    if any(
        a.get("kind") == "sector_mismatch"
        for a in (_cc_section.anomalies or [])
    ):
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 3

    # ── Determine winner ──
    report.synthesis.ach_matrix = {
        "hypotheses": hypotheses,
        "method": "balance of support minus against",
        "winner": max(hypotheses.items(), key=lambda kv: kv[1]["support"] - kv[1]["against"])[0],
    }

    # ── 6d2. Competing narratives ──
    _winner = report.synthesis.ach_matrix["winner"]
    _h = hypotheses
    _narratives: list[str] = []
    if _winner == "H1_legit":
        _narratives.append(
            f"Most likely: {report.identity.entity_name or 'entity'} is a legitimate counterparty. "
            f"Supported by ghost score {ghost_total}/20, "
            f"grounded rate {_grounded if _grounded is not None else 'N/A'}, "
            f"and {'presence' if _has_press else 'absence'} in public record."
        )
        _narratives.append(
            "Alternative: entity may still present enhanced-DD risks if verification gaps "
            "remain or counterparty operates in a higher-risk jurisdiction."
        )
    elif _winner == "H2_enhanced":
        _narratives.append(
            f"Most likely: {report.identity.entity_name or 'entity'} is a higher-risk counterparty "
            f"requiring enhanced due diligence before engagement. "
            f"ACH balance: H2 support={_h['H2_enhanced']['support']}, "
            f"H1 net={_h['H1_legit']['support'] - _h['H1_legit']['against']}."
        )
        _narratives.append(
            "Alternative (benign): entity may be legitimate but operating in a complex "
            "regulatory environment or recently incorporated, explaining data sparsity."
        )
        _narratives.append(
            "Alternative (adverse): entity could be a concealment vehicle with "
            "enough surface legitimacy to pass basic checks — deeper investigation warranted."
        )
    else:  # H3_shell
        _narratives.append(
            f"Most likely: {report.identity.entity_name or 'entity'} is a shell or concealment vehicle. "
            f"ACH strongly favours H3 (support={_h['H3_shell']['support']}, "
            f"against={_h['H3_shell']['against']}). "
            f"Ghost score {ghost_total}/20."
        )
        _narratives.append(
            "Alternative: entity may be a dormant but legitimate holding company "
            "with minimal public footprint — verify with the counterparty directly."
        )
    report.synthesis.competing_narratives = _narratives

    # ── 6e. Key findings — pull the highest-severity items across sections ──
    all_findings: list[Finding] = []
    for section in (
        report.identity, report.network, report.verification,
        report.compliance, report.digital, report.commercial_coherence,
    ):
        for f in getattr(section, "findings", []) or []:
            all_findings.append(f)
    severity_order = {"hard_stop": 0, "red": 1, "amber": 2, "info": 3}
    all_findings.sort(key=lambda f: severity_order.get(getattr(f, "severity", "info"), 4))
    report.synthesis.key_findings = all_findings[:10]

    # ── 6f. Residual unknowns = all data_gaps combined ──
    for section in (
        report.identity, report.network, report.verification,
        report.compliance, report.digital, report.commercial_coherence,
    ):
        for g in getattr(section, "data_gaps", []) or []:
            if g not in report.synthesis.residual_unknowns:
                report.synthesis.residual_unknowns.append(g)

    report.synthesis.meta.duration_ms = int((time.time() - t0) * 1000)
    report.synthesis.meta.status = LayerStatus.OK.value


# =============================================================================
# BOTTOM-LINE + RECOMMENDATION (programmatic, pre-LLM)
# =============================================================================

async def _assemble_bluf(report: ARKDDReport) -> None:
    """Populate report.bottom_line / recommendation / next_actions / confidence.

    Deterministic — no LLM call, just pattern matching over the sections
    so the orchestrator always returns a non-empty BLUF even when the
    cost cap prevented the LLM from running.
    """
    risk = report.risk_classification
    name = report.identity.entity_name or "subject"

    if risk == RiskClassification.HARD_STOP.value:
        report.bottom_line = (
            f"🔴 HARD STOP — {name} triggers a mandatory refusal. "
            "Do NOT proceed with the transaction."
        )
        report.recommendation = (
            "Refuse the engagement. File SAR if reporting thresholds are met. "
            "Preserve all investigation evidence for compliance record."
        )
        report.next_actions = [
            "Do not contact the counterparty further until compliance sign-off",
            "Escalate to ECJU / OFSI / DBT compliance desk as appropriate",
            "Assess SAR filing obligation under POCA 2002 / national AML law",
            "Lock the case file — preserve all evidence",
        ]
    elif risk == RiskClassification.RED.value:
        report.bottom_line = (
            f"🔴 RED — {name} is very likely unsuitable for onboarding in current form. "
            "Independent commercial DD required before any further engagement."
        )
        report.recommendation = (
            "Commission a commercial-grade DD report from LSEG / Sayari / Dow Jones / Orbis. "
            "Do NOT proceed on open-source findings alone. Re-evaluate after commercial DD."
        )
        report.next_actions = [
            "Commission commercial DD (Sayari / LSEG / Dow Jones / Orbis)",
            "Halt any in-progress contracting until commercial DD returns clean",
            "Document the current AMBER-DARK / RED grounds in the case file",
        ]
    elif risk == RiskClassification.AMBER_DARK.value:
        report.bottom_line = (
            f"🟠 AMBER-DARK — {name} shows structural concerns. Enhanced DD is required; "
            "do not proceed without independent verification of beneficial ownership."
        )
        report.recommendation = (
            "Obtain commercial DD report on beneficial ownership. Require signed EUC "
            "from end-user government. Screen signatory identities. Escalate any new red flag to RED."
        )
        report.next_actions = [
            "Obtain commercial UBO verification",
            "Require signed EUC from end-user authority",
            "Identity-verify all signatories against independent sources",
            "Re-run orchestrator weekly via watchlist until risk tier improves",
        ]
    elif risk == RiskClassification.AMBER_LIGHT.value:
        # R-F298: when AMBER was reached purely via the confidence gate
        # (data too thin to issue GREEN, NOT because of a real risk
        # finding), the honest BLUF is "INSUFFICIENT EVIDENCE", not "can
        # proceed with enhanced DD". Otherwise we paper over data-empty
        # DDs as if they had been investigated.
        if getattr(report, "confidence_gate_triggered", False):
            _gate_reasons = getattr(report, "confidence_gate_reasons", []) or []
            _reasons_str = "; ".join(_gate_reasons) if _gate_reasons else "insufficient verification"

            # R-F398 (2026-05-13): before emitting INSUFFICIENT EVIDENCE,
            # try a fallback web search on the entity name. ARIA self-
            # reported that "the dd_orchestrate has 9 layers but the
            # web-search layer appears to run only on real domains, not
            # on person names. Run brave_answer on each name + 'LinkedIn'
            # variation." A non-zero result count doesn't lift the gate
            # (the registry/director data is still missing — that's the
            # real failure) but it surfaces public hits so the operator
            # can see the public-intel surface that was unindexed.
            _fallback_hits: list[dict] = []
            try:
                _entity = (name or "").strip()
                if _entity and len(_entity) >= 3 and _entity.lower() != "subject":
                    from .researcher import _web_search as _ws
                    _variants = [_entity, f"{_entity} LinkedIn", f"{_entity} site:linkedin.com"]
                    for _v in _variants:
                        try:
                            _r = await asyncio.wait_for(_ws(_v), timeout=8.0)
                            if _r:
                                _fallback_hits.extend(list(_r)[:5])
                        except Exception:
                            continue
                    # Stitch into verification.data_gaps so the operator
                    # sees what public intel exists without it lifting
                    # the verdict (gate is still triggered legitimately).
                    if _fallback_hits:
                        report.verification.data_gaps.append(
                            f"R-F398 fallback web-search on '{_entity}' returned "
                            f"{len(_fallback_hits)} public hits (LinkedIn + general). "
                            f"Public-intel surface exists but the DD's registry / "
                            f"director / press layers did not pick it up — re-run "
                            f"with explicit hints (jurisdiction_iso2, website URL, "
                            f"or LinkedIn URL) to lift the confidence gate."
                        )
            except Exception:
                # Never break BLUF on fallback-search error.
                _fallback_hits = []

            _fallback_suffix = (
                f" R-F398 fallback search found {len(_fallback_hits)} public hit(s) — "
                f"see data_gaps."
                if _fallback_hits else ""
            )
            # R-F409 (2026-05-13): surface the auto-escalation status in the
            # BLUF so the operator sees "I tried deep too" — no more "why
            # did you only run shallow?". Operator-flagged 2026-05-13 08:18.
            _rf409_suffix = ""
            if getattr(report, "rf409_auto_escalated", False):
                _initial = getattr(report, "rf409_initial_mode", "standard")
                _rf409_suffix = (
                    f" R-F409: auto-escalated from {_initial}→deep, "
                    f"still insufficient. Operator action needed: supply "
                    f"jurisdiction_iso2 / website URL / known person name."
                )
            # R-F1592: when registry/identity is absent but OSINT WAS gathered
            # (press, digital findings, adverse media, fallback web hits), do
            # NOT bury it under a bare "INSUFFICIENT EVIDENCE" — lead with the
            # open-source briefing so the operator sees what WAS found, while
            # still honestly stating the registry gap. Operator 2026-06-15:
            # "she is not bringing any results" — the DD had 12 press items but
            # reported only "insufficient". The verdict/gate logic is unchanged;
            # only how the bottom line is framed when real OSINT exists.
            _press_n = len(getattr(report.digital, "press_coverage", []) or [])
            _dig_n = len(getattr(report.digital, "findings", []) or [])
            _am = getattr(report, "adverse_media", None) or {}
            _am_n = 0
            if isinstance(_am, dict):
                _am_n = int(_am.get("findings_count", 0) or 0) or len(_am.get("findings", []) or [])
            _osint_n = _press_n + _dig_n + _am_n + len(_fallback_hits)
            if _osint_n > 0:
                report.bottom_line = (
                    f"🟡 LIMITED REGISTRY DATA — {name}: corporate-registry identity "
                    f"could not be confirmed ({_reasons_str}), so this is an "
                    f"OPEN-SOURCE briefing, not a registry-verified DD — but OSINT "
                    f"WAS gathered: {_press_n} press item(s), {_dig_n} digital "
                    f"finding(s)"
                    + (f", {_am_n} adverse-media finding(s)" if _am_n else "")
                    + (f", {len(_fallback_hits)} public web hit(s)" if _fallback_hits else "")
                    + f". See the Digital/Press sections for the sources. Treat as "
                    f"'do-not-transact until registry-verified', NOT as 'nothing "
                    f"found'.{_fallback_suffix}{_rf409_suffix}"
                )
            else:
                report.bottom_line = (
                    f"🟡 INSUFFICIENT EVIDENCE — {name}: the DD did not gather "
                    f"enough data to issue a verdict. AMBER is a placeholder, "
                    f"not a substantive amber risk finding. "
                    f"Gate-triggered by: {_reasons_str}.{_fallback_suffix}{_rf409_suffix}"
                )
            report.recommendation = (
                "Re-run the DD in DEEP mode (or supply jurisdiction / "
                "registration number / website hints) before treating "
                "this as 'can proceed'. The current run did not exercise "
                "the layers that produce the registry / director / press "
                "evidence needed for a real classification."
            )
            report.next_actions = [
                "Re-run with mode=deep (or include the word 'deep' / "
                "'comprehensive' / 'full DD' in the request)",
                "Supply jurisdiction_iso2 if known",
                "Supply a website URL if not already provided — this "
                "unlocks the link-tree investigation path",
                "Resolve each data gap listed below; gate will lift once "
                "registry data + directors + incorporation date are present",
            ]
        else:
            report.bottom_line = (
                f"🟡 AMBER — {name} can proceed with enhanced due diligence. "
                "Resolve the gaps flagged below before contracting."
            )
            report.recommendation = (
                "Proceed with enhanced DD: require EUC, verify signatory identity, "
                "escalate any new red flag to RED. Close data gaps before contracting."
            )
            report.next_actions = [
                "Close the data gaps listed under residual unknowns",
                "Require EUC before any binding commitment",
                "Verify signatory identity via at least one independent source",
            ]
    else:
        report.bottom_line = (
            f"🟢 GREEN — {name} passes baseline due diligence. "
            "Standard contracting path available."
        )
        report.recommendation = (
            "Proceed with standard DD. No blocking concerns identified in the universal layer."
        )
        report.next_actions = [
            "Proceed with standard commercial process",
            "Apply regular sanctions-list re-screen on contract renewal",
        ]

    # ── Layer 5c tag on the BLUF (2026-04-22) ──
    # When commercial coherence is ELEVATED or HIGH, surface it in the
    # headline so operators see the structural concern next to the
    # sanctions/ghost-score/risk verdict — not buried in the body.
    _cc_section = report.commercial_coherence
    _cc_tier = (_cc_section.tier or "GREEN").upper()
    if _cc_tier in ("ELEVATED", "HIGH") and _cc_section.meta.status not in (
        LayerStatus.SKIPPED.value, LayerStatus.ERROR.value,
    ):
        _icon = "🟠" if _cc_tier == "ELEVATED" else "🔴"
        _n_issues = (
            len(_cc_section.anomalies) + len(_cc_section.licence_chain_gaps)
            + len(_cc_section.jurisdiction_flags)
        )
        _tag = (
            f"\n\n{_icon} Commercial coherence {_cc_tier} "
            f"(score {_cc_section.coherence_score:.2f}) — {_n_issues} issue(s) "
            f"flagged in Layer 5c. See Commercial Coherence section."
        )
        if report.bottom_line and _tag not in report.bottom_line:
            report.bottom_line = report.bottom_line + _tag

    report.confidence_tag = report.verification.confidence_floor or "ASSESSED"
    # Aggregate all data_gaps into the top-level summary so consumers
    # can surface them without walking the whole report tree.
    for section in (
        report.identity, report.network, report.verification,
        report.compliance, report.digital, report.commercial_coherence,
    ):
        for g in getattr(section, "data_gaps", []) or []:
            if g not in report.data_gaps_summary:
                report.data_gaps_summary.append(g)

    # ── Auto-create compliance case from DD result (2026-04-13) ──────
    # Every entity screened gets tracked in the compliance workflow for
    # lifecycle management (re-screening, approval, audit trail).
    try:
        from . import compliance_workflow
        await compliance_workflow.auto_create_from_dd(
            entity_name=report.identity.entity_name,
            entity_type=report.identity.entity_type,
            jurisdiction=report.identity.jurisdiction_iso2 or "",
            registration_number=report.identity.registration_number or "",
            risk_level=report.risk_classification.lower().replace("-", "_").replace("amber_light", "amber").replace("amber_dark", "red"),
            risk_summary=report.bottom_line[:300],
            dd_run_id=report.run_id,
        )
    except Exception as _cw_err:
        logger.debug("Compliance workflow auto-create failed (non-fatal): %s", _cw_err)


# =============================================================================
# PERSISTENCE
# =============================================================================

async def _persist_report(report: ARKDDReport) -> None:
    """Store the finished report in Redis + emit signals to brain_hook,
    intel_ledger, audit_log (RED/HARD_STOP only), mem0, and VLS (async,
    non-blocking).

    R-F573 (2026-05-16) — DD case file. Before persisting, compute the
    canonical_entity_id, resolve the version chain (looking up existing
    runs with the same canonical_id in the index), fetch the previous
    report if this is v2+, and attach a version_diff section. The three
    versioning fields (canonical_entity_id, version_number,
    previous_run_id) are added to the index entry so the
    /api/aria/dd/case/{canonical_entity_id} endpoint can fold N runs
    into a single case file.
    """
    try:
        from . import redis_store as rs
        from . import dd_versioning as _ver

        # R-F573: compute canonical entity ID + resolve version chain
        # before writing. Failure to compute the canonical_id is non-
        # fatal — we just persist as v1 / unkeyed (existing behaviour).
        canonical_id = None
        version_number = 1
        previous_run_id = None
        version_diff = None
        existing_index: list[dict] = []
        try:
            canonical_id = _ver.canonical_entity_id_from_report(report)
            existing_index = await rs.get_json(REPORT_INDEX_KEY) or []
            if canonical_id:
                version_number, previous_run_id = _ver.resolve_version_chain(
                    canonical_id, existing_index,
                )
        except Exception as _ver_err:
            logger.debug("dd_orchestrator: version resolve failed: %s", _ver_err)
            existing_index = []

        # Persist the full serialised report plus a pre-rendered markdown
        # copy. training_export.dd_reports reads `rendered` for the
        # fine-tune capture payload — without it the collector always
        # short-circuits at the word-count guard. Keeping it at write
        # time avoids having to reconstruct DDReport from a plain dict
        # on the read side.
        _body = report.as_dict()
        try:
            # R-F1786: render_markdown is sync CPU-heavy (cx ~132, full report
            # build). Run it off the event loop so the persist hook never
            # starves the single-process loop (cf. GIL-wedge class R-F1621/1747).
            _body["rendered"] = await asyncio.to_thread(
                report.render_markdown, concise=False
            )
        except Exception as _rm_err:
            logger.debug("render_markdown failed during persist: %s", _rm_err)

        # R-F573 (initial) + R-F591 (2026-05-16): set the versioning
        # fields on BOTH the dataclass (so /dd/orchestrate's synchronous
        # response carries them — pre-R-F591 it only got Nones because
        # report.as_dict() ran on the dataclass before this hook) AND
        # the persisted body (so /dd/report/{run_id} reads them back
        # from Redis). The dataclass now has the fields as first-class
        # attributes per ARKDDReport.canonical_entity_id et al.
        report.canonical_entity_id = canonical_id
        report.version_number = version_number
        report.previous_run_id = previous_run_id
        _body["canonical_entity_id"] = canonical_id
        _body["version_number"] = version_number
        _body["previous_run_id"] = previous_run_id

        # R-F573: when this is a re-run (version_number > 1), load the
        # previous report and compute a finding-level diff. Best-effort
        # — if the previous report can't be fetched we still persist with
        # diff=None. (R-F877 removed the 7-day body TTL, so the previous
        # body no longer ages out — diffs work at any re-DD interval.)
        if version_number > 1 and previous_run_id:
            try:
                previous_body = await rs.get_json(
                    REPORT_REDIS_KEY.format(run_id=previous_run_id),
                )
                version_diff = _ver.compute_version_diff(
                    previous_report=previous_body,
                    current_report=_body,
                )
                # R-F591: write to dataclass AND body (same rationale as
                # canonical_entity_id et al above).
                report.version_diff = version_diff
                _body["version_diff"] = version_diff
            except Exception as _diff_err:
                logger.debug("dd_orchestrator: version diff failed: %s", _diff_err)
                report.version_diff = None
                _body["version_diff"] = None
        else:
            report.version_diff = None
            _body["version_diff"] = None

        await rs.set_json(
            REPORT_REDIS_KEY.format(run_id=report.run_id),
            _body,
            ex=REPORT_TTL_SECONDS,
        )
        try:
            index = existing_index  # already loaded above for version resolution
            new_entry = {
                "run_id": report.run_id,
                "generated_at": report.generated_at,
                # Mirror entity name + jurisdiction into the columns the
                # dashboard table expects so the library renders both.
                "entity_name": report.identity.entity_name,
                "jurisdiction": report.identity.jurisdiction,
                # R-F130 (2026-05-10): write `severity` + `risk` +
                # `risk_classification` together so the library table
                # column populates regardless of which key the renderer
                # asks for.
                "severity": report.risk_classification,
                "risk": report.risk_classification,
                "risk_classification": report.risk_classification,
                "created_at": report.generated_at,
                # R-F573: case-file fields. Nullable so pre-R-F573
                # entries still load cleanly.
                "canonical_entity_id": canonical_id,
                "version_number": version_number,
                "previous_run_id": previous_run_id,
                # R-F607 (2026-05-16): per-user scoping fields. Nullable
                # for back-compat — pre-R-F607 entries persist with None
                # and are hidden from user-filtered list_reports calls.
                "user_id": getattr(report, "user_id", None),
                "user_email_lower": getattr(report, "user_email_lower", None),
                "user_email_domain": getattr(report, "user_email_domain", None),
                # R-F608 (2026-05-16): per-DD opt-out toggle. Default
                # True; list_reports treats missing as True so pre-R-F608
                # entries are shared to the company by default.
                "share_to_company": getattr(report, "share_to_company", True),
            }
            index.insert(0, new_entry)
            index = index[:500]
            await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
            # R-F575: mirror the index entry into the SQLite cold tier as a
            # redundant case-file chain store. Best-effort — failure is
            # non-fatal (the primary store now persists forever per R-F877).
            try:
                from . import dd_case_archive
                dd_case_archive.archive_entry(new_entry)
            except Exception as _arc_err:
                logger.debug("dd_orchestrator: cold-tier archive failed: %s", _arc_err)

            # R-F878 (2026-05-25) — auto-enroll the DD'd entity into the
            # watchlist. Pre-R-F878 orchestrate_dd NEVER called
            # add_to_watchlist, so the entities the team explicitly
            # scrutinises (a manual DD) were the ONLY ones that fell out of
            # re-screening — only autonomous-scan-discovered entities got
            # monitored. Closes that coherence hole: every completed DD
            # enrolls its canonical_entity_id so the daily re-screen tracks
            # it. Best-effort + guarded against thin/garbage names (same
            # entity guard rescreen uses to self-purge pollution).
            # Idempotent — add_to_watchlist dedups by name and only enriches.
            try:
                _wl_name = (report.identity.entity_name or "").strip()
                _enroll_ok = bool(_wl_name)
                try:
                    from . import sanctions as _sanc_enr
                    if hasattr(_sanc_enr, "_looks_like_entity_name"):
                        _enroll_ok = _enroll_ok and _sanc_enr._looks_like_entity_name(_wl_name)
                except Exception:
                    pass
                if _enroll_ok:
                    await add_to_watchlist({
                        "name": _wl_name,
                        "entity_type": report.identity.entity_type or None,
                        "jurisdiction": report.identity.jurisdiction or None,
                        "canonical_entity_id": canonical_id,
                        "source": "dd_auto_enroll",
                        "last_dd_run_id": report.run_id,
                        "last_risk": report.risk_classification,
                        "added_at": report.generated_at,
                    })
            except Exception as _enr_err:
                logger.debug("dd_orchestrator: watchlist auto-enroll failed: %s", _enr_err)
        except Exception as e:
            logger.debug("dd_orchestrator: report index write failed: %s", e)
    except Exception as e:
        logger.warning("dd_orchestrator: Redis persist failed: %s", e)

    # ── Self-metrics: declared calibration (final confidence tag as a score)
    # + coverage (subsystems that produced findings). Real calibration arrives
    # later when corrections are logged against this run_id.
    try:
        from . import self_metrics
        _conf_score = {
            "CONFIRMED": 1.0, "PROBABLE": 0.75, "ASSESSED": 0.5, "UNCERTAIN": 0.25,
        }.get((report.confidence_tag or "ASSESSED").upper(), 0.5)
        _domain = (report.identity.jurisdiction or "unknown").upper() or "unknown"
        await self_metrics.emit(
            "calibration", _domain, "dd_declared_confidence",
            _conf_score,
            context={"run_id": report.run_id, "tag": report.confidence_tag,
                     "risk": report.risk_classification},
            source_module="dd_orchestrator",
        )
        # Coverage: fraction of the 6 DD subsystems that produced at least one
        # finding (identity, network, compliance, digital, verification,
        # commercial_coherence). Synthesis uses `key_findings` not
        # `findings` and is the aggregator of the other layers, so it's
        # not counted as a separate signal source. The comment previously
        # said "6 subsystems" but the list had 5 -- commercial_coherence
        # was missing, so coverage was always undercounted by 1/6.
        _subs = [report.identity, report.network, report.compliance,
                 report.digital, report.verification,
                 report.commercial_coherence]
        _produced = sum(1 for s in _subs
                        if s and (getattr(s, "findings", None) or getattr(s, "entity_name", None)))
        await self_metrics.emit(
            "coverage", _domain, "dd_subsystem_yield",
            _produced / max(1, len(_subs)),
            context={"run_id": report.run_id, "subsystems_produced": _produced},
            source_module="dd_orchestrator",
        )
    except Exception as _sm:
        logger.debug("dd_orchestrator self_metrics failed: %s", _sm)

    # ── R-F1182: VLS cryptographic proof (fire-and-forget) ──────────────────
    # After the report is fully persisted, seal it with a cryptographic
    # proof (hash + ECDSA signature + chain link). Never blocks the DD
    # pipeline — errors are caught and wired to the brain.
    try:
        from . import verifiable_ledger as _vls
        from . import engine_wiring as _ew
        _ew.wire_success(
            module="dd_orchestrator",
            summary=f"VLS proof requested for {report.run_id}",
            source_id=report.run_id,
        )
        # Fire-and-forget: schedule but don't await
        _ew._dispatch_fire_and_forget(lambda: _vls.record_report(report))
    except Exception as _vls_e:
        logger.debug("dd_orchestrator: VLS hook failed (non-fatal): %s", _vls_e)

    # R-F1184: intel_ledger signal (fire-and-forget)
    # Every completed DD report produces a permanent signal in the intel
    # ledger so query_ledger() can surface DD findings.
    try:
        from . import intel_ledger as _il
        await _il.add_signal({
            "summary": f"DD report: {report.identity.entity_name} ({report.identity.entity_type}, {report.identity.jurisdiction_iso2 or 'unknown'}) - risk={report.risk_classification}",
            "source": "dd_orchestrator",
            "type": "dd_report",
            "severity": "high" if report.risk_classification in ("RED", "HARD_STOP", "NO-GO") else "medium",
            "tags": ["dd", str(report.risk_classification).lower() if report.risk_classification else "unknown"],
            "timestamp": report.generated_at,
        })
    except Exception as _il_e:
        logger.debug("dd_orchestrator: intel_ledger hook failed (non-fatal): %s", _il_e)

    # R-F1184: audit_log record for RED/HARD_STOP verdicts
    _risk_upper = (report.risk_classification or "").upper()
    if _risk_upper in ("RED", "HARD_STOP", "NO-GO"):
        try:
            from . import audit_log as _al
            await _al.record(
                action="dd_verdict",
                actor="aria.dd_orchestrator",
                entity_name=report.identity.entity_name or "",
                deal_id="",
                inputs={"target": report.target},
                outputs={
                    "risk_classification": report.risk_classification,
                    "bottom_line": str(report.bottom_line)[:500],
                    "run_id": report.run_id,
                    "canonical_entity_id": report.canonical_entity_id,
                },
                decision=report.risk_classification,
                confidence=report.confidence_tag or "ASSESSED",
                notes=f"DD run {report.run_id} returned {report.risk_classification} on {report.identity.entity_name}",
                feed_brain=True,
            )
        except Exception as _al_e:
            logger.debug("dd_orchestrator: audit_log hook failed (non-fatal): %s", _al_e)

    # R-F1184: mem0 notebook entry (fire-and-forget)
    # A brief notebook entry per DD run helps ARIA recall past investigations.
    if (report.risk_classification or "") not in ("ERROR",):
        try:
            from . import mem0 as _mem0
            if _mem0.is_enabled():
                _summary = (
                    f"DD report on {report.identity.entity_name} "
                    f"({report.identity.entity_type}, {report.identity.jurisdiction_iso2 or 'unknown'}): "
                    f"risk={report.risk_classification}. "
                    f"{str(report.bottom_line)[:300]}"
                )
                await _mem0.summarise_and_store(
                    user_message=f"Run DD on {report.identity.entity_name}",
                    aria_response=_summary,
                    session_id=f"dd_{report.run_id}",
                    llm=None,
                    user_label="operator",
                )
        except Exception as _mem0_e:
            logger.debug("dd_orchestrator: mem0 hook failed (non-fatal): %s", _mem0_e)



    # R-F1658: record in the persistent DD vault (SQLite, survives restarts)
    try:
        from .dd_vault import get_vault as _get_dd_vault
        _vault = _get_dd_vault()
        _vault.record_case(
            canonical_entity_id=getattr(report, "canonical_entity_id", None) or report.run_id,
            entity_name=getattr(report.identity, "entity_name", "unknown"),
            entity_type=getattr(report.identity, "entity_type", "company"),
            jurisdiction=getattr(report.identity, "jurisdiction", ""),
            registration_number=getattr(report.identity, "registration_number", ""),
            latest_report_id=report.run_id,
            findings_summary=_summarize_findings(report),
            risk_score=_compute_risk_score(report),
            risk_level=report.risk_classification or "unknown",
            tags=_extract_tags(report),
        )
    except Exception as _vault_err:
        logger.debug("dd_vault: record failed (non-fatal): %s", _vault_err)

# =============================================================================
# HELPERS
# =============================================================================

def _age_months(iso_date: Optional[str]) -> Optional[int]:
    if not iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.fromisoformat(iso_date)
        except Exception:
            return None
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.utcnow()
    delta = now - dt
    return max(0, delta.days // 30)


def _map_activity(declared: Optional[str]) -> Optional[str]:
    if not declared:
        return None
    d = declared.lower()
    if any(k in d for k in ("general trading", "holding", "investment holding", "management services", "not elsewhere classified")):
        return "generic_holding"
    return "specific_aligned"


# National corporate registries ARIA can recommend for manual lookup
# when the orchestrator doesn't have automated coverage. Keyed by
# ISO-2. Every entry names the authoritative free public registry
# so the user knows WHERE to look rather than just that ARIA
# couldn't do it. This closes the "unknown jurisdiction" gap that
# surfaced on the Serban Industries SRL / Romania run.
_NATIONAL_REGISTRY_HINTS: dict[str, str] = {
    # Europe
    "GB": "already covered automatically via Companies House",
    "GI": "check Gibraltar Companies House at https://www.companieshouse.gi — separate registry from UK CH; paid per extract. Also check Gibraltar Beneficial Ownership Register (Companies Act 2014, as amended 2019).",
    "IM": "check Isle of Man Companies Registry at https://services.gov.im/ded/services/companiesregistry — paid per extract",
    "JE": "check Jersey Financial Services Commission Registry at https://www.jerseyfsc.org — paid per extract",
    "GG": "check Guernsey Registry at https://www.greg.gg — paid per extract",
    "KY": "check Cayman Islands General Registry at https://www.ciregistry.ky — restricted access; UBO via Beneficial Ownership Transparency Act",
    "BM": "check Bermuda Registrar of Companies at https://www.gov.bm/department/registrar-companies — paid per extract",
    "VG": "check BVI Financial Services Commission at https://www.bvifsc.vg — restricted; BOSS (Beneficial Ownership Secure Search) system",
    "TC": "check Turks & Caicos Financial Services Commission — paid extracts only",
    "AI": "check Anguilla Commercial Registry (ACORN) — paid per extract",
    "RO": "check ONRC (Oficiul Național al Registrului Comerțului) at https://portal.onrc.ro — free public Romanian registry",
    "DE": "check Handelsregister at https://www.handelsregister.de — free German commercial register (fee per extract)",
    "FR": "check INFOGREFFE / Pappers at https://www.pappers.fr — free French commercial registry aggregator",
    "IT": "check Registro Imprese at https://www.registroimprese.it — Italian commercial register (fee per extract)",
    "ES": "check Registro Mercantil Central at https://www.rmc.es — Spanish commercial register (fee per extract)",
    "NL": "check KvK (Kamer van Koophandel) at https://www.kvk.nl — Dutch chamber of commerce",
    "BE": "check Crossroads Bank for Enterprises at https://kbopub.economie.fgov.be — Belgian company register, free",
    "AT": "check FirmenABC or Firmenbuch at https://www.firmenbuchabfrage.at — Austrian commercial register",
    "CH": "check Zefix at https://www.zefix.ch — Swiss central business names index, free",
    "IE": "check CRO (Companies Registration Office) at https://www.cro.ie — Irish registry",
    "PT": "check Portal da Empresa at https://bde.portaldocidadao.pt — Portuguese public business database",
    "PL": "check KRS (Krajowy Rejestr Sądowy) at https://ekrs.ms.gov.pl — Polish national court register, free",
    "CZ": "check Czech Business Register at https://or.justice.cz — free",
    "SK": "check Slovak Business Register at https://www.orsr.sk — free",
    "HU": "check E-cégjegyzék at https://e-cegjegyzek.hu — Hungarian commercial register",
    "BG": "check Bulgarian Trade Register at https://portal.registryagency.bg — free",
    "HR": "check Sudski registar at https://sudreg.pravosudje.hr — Croatian court register",
    "SI": "check AJPES at https://www.ajpes.si — Slovenian Agency for Public Legal Records",
    "GR": "check GEMI (General Commercial Registry) at https://www.businessregistry.gr",
    "SE": "check Bolagsverket at https://www.bolagsverket.se — Swedish Companies Registration Office",
    "DK": "check CVR (Det Centrale Virksomhedsregister) at https://datacvr.virk.dk — Danish CVR, free",
    "FI": "check YTJ (Business Information System) at https://tietopalvelu.ytj.fi — Finnish business info, free",
    "NO": "check Brønnøysundregistrene at https://w2.brreg.no — Norwegian business registry, free",
    "LU": "check LBR (Luxembourg Business Registers) at https://www.lbr.lu — paid per extract",
    "EE": "check Estonian Business Registry (e-Business Register) at https://ariregister.rik.ee — free",
    "LT": "check Lithuanian Centre of Registers at https://www.registrucentras.lt",
    "LV": "check Latvian UR (Uzņēmumu reģistrs) at https://www.ur.gov.lv",
    "CY": "check Cyprus Department of Registrar of Companies at https://www.companies.gov.cy",
    "MT": "check Malta Business Registry at https://mbr.mt",
    # Americas
    "US": "check the relevant US state Secretary of State (Delaware https://icis.corp.delaware.gov is the most common for holding companies); SEC EDGAR at https://www.sec.gov/edgar for public filers",
    "CA": "check the relevant province (Federal Corporations Canada at https://ised-isde.canada.ca) or provincial registry",
    "BR": "check Receita Federal CNPJ lookup at https://solucoes.receita.fazenda.gov.br/Servicos/cnpjreva — free Brazilian corporate tax ID registry",
    "MX": "check the national Mexican corporate registry at https://psm.economia.gob.mx (public commercial filings)",
    "AR": "check AFIP CUIT lookup or Inspección General de Justicia (IGJ) at https://www.argentina.gob.ar/justicia/igj",
    "CL": "check the Chilean Registro de Comercio / CMF registry",
    "CO": "check RUES (Registro Único Empresarial y Social) at https://www.rues.org.co — Colombian national business registry",
    # Middle East
    "AE": "check Dubai DED Trade Licence Info at https://eservices.dubaided.gov.ae and the Abu Dhabi DED equivalent; DIFC Public Register at https://www.difc.ae/public-register; ADGM Public Register at https://www.adgm.com/public-registers",
    "SA": "check Saudi Ministry of Commerce Commercial Registration search at https://mc.gov.sa",
    "QA": "check Qatar Ministry of Commerce and Industry registry",
    "KW": "check Kuwait Public Authority for Industry commercial registry",
    "BH": "check Bahrain Ministry of Industry and Commerce — Sijilat at https://www.sijilat.bh",
    "OM": "check Oman Ministry of Commerce, Industry and Investment Promotion registry",
    "IL": "check Israeli Registrar of Companies at https://ica.justice.gov.il",
    "TR": "check Mersis (Ticaret Sicili Kayıtları) at https://www.mersis.gtb.gov.tr — Turkish central trade registry",
    # Asia
    "CN": "check National Enterprise Credit Information Publicity System at https://www.gsxt.gov.cn — Chinese AIC registry",
    "IN": "check MCA21 (Ministry of Corporate Affairs) at https://www.mca.gov.in — Indian corporate registry",
    "JP": "check the National Tax Agency corporate number system at https://www.houjin-bangou.nta.go.jp",
    "KR": "check DART (Data Analysis, Retrieval and Transfer System) at https://dart.fss.or.kr — Korean financial disclosures",
    "SG": "check BizFile+ at https://www.bizfile.gov.sg — ACRA Singapore, full public register",
    "MY": "check SSM (Suruhanjaya Syarikat Malaysia) at https://www.ssm-einfo.my",
    "ID": "check AHU Online at https://ahu.go.id — Indonesian Ministry of Law and Human Rights registry",
    "TH": "check DBD (Department of Business Development) at https://datawarehouse.dbd.go.th",
    "VN": "check Vietnamese National Business Registration Portal at https://dangkykinhdoanh.gov.vn",
    "PH": "check SEC Philippines at https://www.sec.gov.ph",
    "PK": "check SECP at https://www.secp.gov.pk",
    "BD": "check RJSC (Registrar of Joint Stock Companies and Firms) at http://www.roc.gov.bd",
    # Africa
    "NG": "check CAC (Corporate Affairs Commission) at https://pre.cac.gov.ng",
    "ZA": "check CIPC at https://www.cipc.co.za — South African Companies and Intellectual Property Commission",
    "KE": "check eCitizen Business Registration Service at https://brs.ecitizen.go.ke",
    "GH": "check Ghana Registrar-General's Department at https://www.rgd.gov.gh",
    "AO": "check SIAC (Single Enterprise Counter) / Ministério da Justiça Angola — manual registry lookup, no public online portal",
    "MZ": "check Mozambique Ministry of Justice corporate registry — manual only, no public online portal",
    "EG": "check Egyptian GAFI (General Authority for Investment) at https://www.gafi.gov.eg",
    "MA": "check OMPIC at https://www.directinfo.ma — Moroccan Office of Industrial and Commercial Property",
    # Post-Soviet / CIS
    "RU": "check EGRUL (ЕГРЮЛ) at https://egrul.nalog.ru — Russian Federal Tax Service corporate registry (CAUTION: sanctions-regime jurisdiction, avoid automated connectivity)",
    "UA": "check YouControl at https://youcontrol.com.ua or Ministry of Justice USR at https://usr.minjust.gov.ua",
    "KZ": "check Kazakhstan Ministry of Justice legal entities registry",
    "BY": "check Unified State Register of Belarus — manual lookup required",
    "AM": "check Armenia State Register of Legal Persons",
    "GE": "check LEPL National Agency of Public Registry at https://napr.gov.ge — Georgian NAPR",
    "AZ": "check Azerbaijani Tax Ministry legal entity registry",
    "UZ": "check Uzbek Ministry of Justice legal entity registry",
    # Oceania
    "AU": "check ASIC (Australian Securities and Investments Commission) at https://asic.gov.au",
    "NZ": "check NZ Companies Register at https://companies-register.companiesoffice.govt.nz",
}


def _national_registry_hint(iso2: Optional[str], jurisdiction: Optional[str]) -> str:
    """Return a specific, actionable manual-lookup instruction for the
    national corporate registry of a given jurisdiction. Used in
    data_gap messages so the LLM (and the human reader) know exactly
    where to look manually when ARIA's automated coverage doesn't
    reach the target country.
    """
    if iso2 and iso2 in _NATIONAL_REGISTRY_HINTS:
        return _NATIONAL_REGISTRY_HINTS[iso2]
    # Best-effort fallback by jurisdiction name
    if jurisdiction:
        return (
            f"run a manual search of the {jurisdiction} national corporate "
            f"registry (consult FATF country profile for the authoritative "
            f"source) and attach the result to the DD record."
        )
    return (
        "run a manual search of the target country's national corporate registry "
        "(FATF country profiles list the authoritative source) and attach the "
        "result to the DD record."
    )


# Sanctions-match classification now lives in _sanctions_classify.py
# so both dd_orchestrator and network_walker share the same topic →
# severity logic. The inline copy was removed to eliminate drift.


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

# ══════════════════════════════════════════════════════════════════════════
# Entity-name sanity gate
# ══════════════════════════════════════════════════════════════════════════
#
# Rejects obvious garbage names before the 7-layer pipeline runs.
# Garbage names produce noise findings that then poison mem0 /
# claim_ledger for future conversations. Prevention > cleanup.

_URL_SCHEME_FRAGMENTS = {
    "http", "https", "ftp", "ftps", "ws", "wss",
    "file", "about", "data", "mailto",
}

# R-F659 (2026-05-17): tokens/labels that are never a real entity name.
# Mirrors the on-demand crawler's R-F654 set so the DD-pipeline entry gate
# rejects the same garbage class that the crawler now refuses. If the
# operator queries "Acme Widgets" or "test corp", the DD pipeline refuses
# at the gate instead of producing noise findings on placeholder strings.
_DD_PLACEHOLDER_TOKENS = frozenset({
    "acme", "widgets", "widget", "example", "foo", "bar", "baz",
    "qux", "test", "tests", "sample", "samples", "todo", "tbd",
    "placeholder", "dummy", "mock", "demo",
})

# R-F659: hard whitelist of entity_type values that may enter the DD
# pipeline. EntityType.UNKNOWN is intentionally excluded — if we don't
# know what kind of entity it is, we can't run the right layer mix and
# the resulting findings won't be sound. Callers must classify upstream.
_DD_VALID_ENTITY_TYPES = frozenset({
    "company", "person", "address", "vessel", "aircraft",
    "organisation", "organization", "oem",  # synonyms accepted; canonicalised downstream
})


def _validate_entity_type_for_dd(target: dict) -> tuple[bool, str]:
    """R-F659 — hard-whitelist of entity_type values that may enter the
    DD pipeline. Returns (is_valid, reject_reason).

    EntityType.UNKNOWN is intentionally excluded. If the caller didn't
    classify, we refuse to run rather than guess. The design-analysis
    requirement was 'entity_type IN (org, person, oem)'; we kept a
    slightly broader allow-set to preserve existing legitimate
    vessel/aircraft/address DD flows, while still enforcing the
    'typed-input, refuse UNKNOWN' principle."""
    if not isinstance(target, dict):
        return False, "target must be a dict"
    entity_type = (target.get("type") or "").strip().lower()
    if not entity_type:
        return False, "entity_type missing (required)"
    if entity_type == "unknown":
        return False, "entity_type=UNKNOWN refused (caller must classify before DD)"
    if entity_type not in _DD_VALID_ENTITY_TYPES:
        return False, (
            f"entity_type {entity_type!r} not in allowed set "
            f"{sorted(_DD_VALID_ENTITY_TYPES)}"
        )
    return True, ""


def _validate_entity_name_extras(name: str) -> tuple[bool, str]:
    """R-F659 — name heuristics that supplement _validate_entity_name.

    Runs AFTER the existing name validation + R-F153 website fallback
    have done their work. Rejects names that are technically well-formed
    strings but obvious junk:
      - first-token is purely numeric ('283', '2026')
      - first-token is in _DD_PLACEHOLDER_TOKENS (RFC 2606 / tutorial)
      - hyphenated label where every segment is a placeholder
        ('acme-widgets')

    Returns (is_valid, reject_reason). On valid, reject_reason is empty.
    """
    if not name or not name.strip():
        return False, "empty"
    label = name.strip().lower()
    # Strip common corporate suffixes before the placeholder check so
    # "Acme Ltd" still gets caught.
    _stripped = re.sub(
        r"\b(ltd|llc|inc|gmbh|sa|sarl|spa|plc|ltda|s\.?a\.?)\.?$",
        "", label,
    ).strip()
    first_token = _stripped.split()[0] if _stripped.split() else _stripped
    if first_token.isdigit():
        return False, f"name first-token is purely numeric ({first_token!r})"
    if first_token in _DD_PLACEHOLDER_TOKENS:
        return False, f"name starts with placeholder token {first_token!r}"
    if "-" in first_token:
        segments = [s for s in first_token.split("-") if s]
        if segments and all(s in _DD_PLACEHOLDER_TOKENS for s in segments):
            return False, f"name hyphenated-placeholder label {first_token!r}"
    return True, ""


def _validate_entity_name(name: str) -> tuple[bool, str]:
    """Return (is_valid, reject_reason). Reject reason is empty on valid."""
    if not name or not name.strip():
        return False, "empty"
    s = name.strip()

    # Scheme fragments — "https", "http", "ftp" etc. come from URL
    # regex captures that terminated at `/` or `:`.
    if s.lower().strip(":/") in _URL_SCHEME_FRAGMENTS:
        return False, f"looks like a URL scheme fragment, not an entity"

    # Pure punctuation / whitespace
    if not re.search(r"[A-Za-z0-9]", s):
        return False, "no alphanumeric characters"

    # R-F153 (2026-05-10) — sentence-fragment / prompt-fragment detection.
    # The DD library was storing names like "this company, which has nothing
    # to do" because the LLM tool-call extracted the literal user-phrase
    # rather than the company name. The aria.sanctions module already
    # rejected these in screening but the report library still stored them
    # as entity_name (live: dd_d38befa3fd4 on 2026-05-10 12:39). Catch the
    # pattern here so the report's entity field is always meaningful.
    _s_lower = s.lower()
    _fragment_starts = (
        "this company", "that company", "the company", "a company",
        "this entity", "that entity", "the entity",
        "this person", "that person", "the person",
        "this business", "that business",
        "this firm", "that firm", "the firm",
        "this organisation", "this organization",
    )
    for _fi in _fragment_starts:
        if _s_lower.startswith(_fi):
            return False, f"looks like a prompt fragment starting with '{_fi}', not a real entity name"
    _fragment_phrases = (
        " which has ", " which is ", " that has ", " that is ",
        " can you ", " could you ", " please ", " do a full ",
        " has nothing to do ", " is being ", " was being ",
    )
    for _pp in _fragment_phrases:
        if _pp in _s_lower:
            return False, f"contains prompt-fragment phrase '{_pp.strip()}'; not a real entity name"

    # Very short all-lowercase strings with no spaces — likely garbage
    # (single words like 'http', 'com', 'www'). Real entity names are
    # usually capitalised OR longer than 4 chars. Domain names are
    # rescued by the `.` check below.
    if len(s) <= 4 and s.islower() and "." not in s:
        return False, f"too short ({len(s)} chars, lowercase, no dot)"

    # Over-long input
    if len(s) > 500:
        return False, f"too long ({len(s)} chars)"

    # Starts with a scheme separator
    if s.startswith((":", "/", "\\", "?", "#", "&", "=")):
        return False, "starts with a URL delimiter"

    return True, ""


# ── R-F1110: Sweep intelligence layer ────────────────────────────────────────
# Queries the brain for recent signals from the 49-source Node sweep that
# are relevant to the target entity and jurisdiction. This bridges the gap
# between the DD orchestrator (7 vendor integrations) and the sweep
# (49 sources, 5-7 min cadence).


@wired(
    module="dd_orchestrator.sweep_intelligence",
    summary="Sweep intelligence query completed",
    detail="Layer 5b sweep intelligence query for target entity",
    gap_type="sweep_intelligence_failure",
)
async def _run_sweep_intelligence(target: dict, report: ARKDDReport) -> None:
    """Layer 5b — Sweep intelligence. Queries brain for recent sweep signals.

    The Node sweep runs every 5-7 minutes and collects data from 49 sources
    (sanctions, procurement, defence news, conflict events, trade data, etc.).
    This function queries the brain_hook stats and intel_ledger for signals
    relevant to the target's name and jurisdiction.

    This is a best-effort enrichment — if the brain is not reachable or
    returns no data, the section is marked as having data gaps but does
    NOT block the DD report.
    """
    t0 = time.time()
    report.sweep_data.meta.started_at = datetime.now(timezone.utc).isoformat()

    entity_name = (target.get("name") or target.get("entity") or target.get("query", "")).lower()
    jurisdiction = target.get("jurisdiction_iso2", "")

    try:
        from . import brain_hook as _bh
        from . import intel_ledger as _il

        # 1. Get recent brain signals (last 7 days)
        stats = await _bh.get_stats()
        modules = stats.get("modules", {})

        # 2. Scan for signals relevant to the target
        sweep_signals = []
        for mod_name, mod_stats in modules.items():
            last_signal = mod_stats.get("last_signal_ago_h", 999)
            if last_signal < 168:  # within 7 days
                sweep_signals.append({
                    "module": mod_name,
                    "last_signal_ago_h": last_signal,
                    "signal_count": mod_stats.get("signal_count", 0),
                })

        report.sweep_data.recent_signals = sweep_signals

        # 3. Query intel ledger for entity-relevant signals
        if entity_name:
            try:
                ledger_entries = await _il.get_recent(limit=100)
                # Filter to entity-relevant entries
                entity_words = set(entity_name.lower().split())
                filtered = []
                for entry in ledger_entries or []:
                    text = ((entry.get("summary") or "") + " " + (entry.get("detail") or "")).lower()
                    if any(w in text for w in entity_words if len(w) > 2):
                        filtered.append(entry)
                ledger_entries = filtered[:20]
                for entry in ledger_entries or []:
                    source = (entry.get("source") or "").lower()
                    summary = (entry.get("summary") or "")
                    detail = (entry.get("detail") or "")

                    # Classify by source type
                    if any(s in source for s in ("sanctions", "ofac", "ofsi", "un_sc", "fcdo")):
                        report.sweep_data.sanctions_updates.append(entry)
                    elif any(s in source for s in ("tender", "procurement", "sam", "ted")):
                        report.sweep_data.procurement_alerts.append(entry)
                    elif any(s in source for s in ("gdelt", "acled", "conflict", "reliefweb")):
                        report.sweep_data.jurisdiction_events.append(entry)
                    elif any(s in source for s in ("defense_news", "global_defence", "sipri", "janes")):
                        report.sweep_data.relevant_news.append(entry)
                    elif any(s in source for s in ("comtrade", "trade", "export", "import")):
                        report.sweep_data.trade_signals.append(entry)
                    else:
                        report.sweep_data.relevant_news.append(entry)

            except Exception as e:
                report.sweep_data.data_gaps.append(f"ledger query failed: {str(e)[:80]}")

        # 4. Generate findings from sweep data with provenance
        # Each finding carries source URL + timestamp so the DD report cites them.
        # Dedupe against existing report findings to avoid asserting the same
        # signal twice.
        existing_titles = {f.title for f in report.sweep_data.findings}
        existing_sources = {f.source for f in report.sweep_data.findings}

        if report.sweep_data.sanctions_updates:
            _title = f"{len(report.sweep_data.sanctions_updates)} recent sanctions signals"
            if _title not in existing_titles:
                _sources = []
                for _su in report.sweep_data.sanctions_updates[:5]:
                    _url = _su.get("source_url") or _su.get("url", "")
                    _ts = _su.get("timestamp", "")
                    if _url:
                        _sources.append(f"{_url} ({_ts})" if _ts else _url)
                report.sweep_data.findings.append(Finding(
                    severity="info",
                    title=_title,
                    detail=(
                        f"Sweep detected {len(report.sweep_data.sanctions_updates)} "
                        f"sanctions-related signals in the last 7 days. "
                        f"Sources: {'; '.join(_sources[:3])}"
                    ),
                    source="sweep_intelligence",
                    confidence="CONFIRMED",
                ))

        if report.sweep_data.procurement_alerts:
            _title = f"{len(report.sweep_data.procurement_alerts)} procurement signals"
            if _title not in existing_titles:
                _sources = []
                for _pa in report.sweep_data.procurement_alerts[:5]:
                    _url = _pa.get("source_url") or _pa.get("url", "")
                    _ts = _pa.get("timestamp", "")
                    if _url:
                        _sources.append(f"{_url} ({_ts})" if _ts else _url)
                report.sweep_data.findings.append(Finding(
                    severity="info",
                    title=_title,
                    detail=(
                        f"Sweep detected {len(report.sweep_data.procurement_alerts)} "
                        f"procurement/tender signals. "
                        f"Sources: {'; '.join(_sources[:3])}"
                    ),
                    source="sweep_intelligence",
                    confidence="CONFIRMED",
                ))

        if report.sweep_data.jurisdiction_events:
            _title = f"{len(report.sweep_data.jurisdiction_events)} jurisdiction events"
            if _title not in existing_titles:
                _sources = []
                for _je in report.sweep_data.jurisdiction_events[:5]:
                    _url = _je.get("source_url") or _je.get("url", "")
                    _ts = _je.get("timestamp", "")
                    if _url:
                        _sources.append(f"{_url} ({_ts})" if _ts else _url)
                report.sweep_data.findings.append(Finding(
                    severity="info",
                    title=_title,
                    detail=(
                        f"Sweep detected {len(report.sweep_data.jurisdiction_events)} "
                        f"conflict/event signals for this jurisdiction. "
                        f"Sources: {'; '.join(_sources[:3])}"
                    ),
                    source="sweep_intelligence",
                    confidence="CONFIRMED",
                ))

        report.sweep_data.meta.subcalls = (
            len(sweep_signals) + len(ledger_entries or [])
        )

    except ImportError as e:
        report.sweep_data.data_gaps.append(f"brain_hook/intel_ledger not available: {str(e)[:80]}")
    except Exception as e:
        report.sweep_data.data_gaps.append(f"sweep intelligence failed: {str(e)[:80]}")

    report.sweep_data.meta.duration_ms = int((time.time() - t0) * 1000)
    report.sweep_data.meta.status = (
        LayerStatus.OK.value if not report.sweep_data.data_gaps
        else LayerStatus.PARTIAL.value
    )


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def _dd_interactive_keepalive() -> None:
    """R-F1855 — refresh brain_hook.mark_interactive() every few seconds for the
    whole lifetime of a DD.

    A lot of heavy background CPU work already DEFERS while a user request is in
    flight (R-F1754 encode-absorb backoff, the semantic-index queue, eagle_eye
    codebase indexing, brain_hook_bg) — but only for the ~8s `_interactive_active`
    window after the triggering chat request. A DD runs for minutes, so that
    window expired mid-DD and the autonomous encode/index work resumed, GIL-
    starving the event loop (live wedge 2026-06-23/24: 6-10s R-F703 stalls, DD
    blew its budget → 'DD produces nothing'). Keeping the window alive for the
    DD's duration lets the EXISTING backoff actually cover long DDs, so the DD
    wins the loop. Bounded: cancelled in orchestrate_dd's finally and by the DD's
    own hard deadline; the deferrals it triggers have their own max-defer caps."""
    try:
        from . import brain_hook as _bh1855
    except Exception:
        return
    while True:
        try:
            _bh1855.mark_interactive()
        except Exception:
            pass
        await asyncio.sleep(4)


async def orchestrate_dd(
    target: dict,
    *,
    llm: Any = None,
    mode: str = "standard",
    cost_cap_usd: float | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    user_email: str | None = None,
    share_to_company: bool = True,
    total_budget_s: float | None = None,
) -> "ARKDDReport":
    """R-F1628 — HARD overall deadline (robust end to 'DD hangs forever').

    The 7-layer impl clamps PER-LAYER timeouts to the budget, but unclamped work
    (predictor forecast, final synthesis, brain absorbs) can still overrun: a
    deep DD measured 781s against a 660s budget and returned NOTHING — the
    WhatsApp poll window closed → no delivery, the recurring symptom. This
    wrapper GUARANTEES the call returns within budget + a synthesis margin,
    returning the ACCUMULATED PARTIAL report (with an honest 'time-budget
    reached' bottom line) instead of hanging. Makes the hang structurally
    impossible regardless of how slow/unreachable external sources are.

    Tunable: ARIA_DD_TOTAL_BUDGET_S (660), ARIA_DD_HARD_MARGIN_S (150).
    """
    budget = (
        float(total_budget_s) if total_budget_s is not None
        else float(_env_int("ARIA_DD_TOTAL_BUDGET_S", 660))
    )
    hard = budget + float(_env_int("ARIA_DD_HARD_MARGIN_S", 150))
    holder: dict = {}
    # R-F1855: keep the interactive-yield window alive for the whole DD so the
    # existing _interactive_active()-gated background work stays deferred and the
    # DD wins the event loop (cancelled in finally; bounded by the hard deadline).
    _ka_task = asyncio.create_task(_dd_interactive_keepalive())
    try:
        return await asyncio.wait_for(
            _orchestrate_dd_impl(
                target,
                llm=llm,
                mode=mode,
                cost_cap_usd=cost_cap_usd,
                trace_id=trace_id,
                user_id=user_id,
                user_email=user_email,
                share_to_company=share_to_company,
                total_budget_s=total_budget_s,
                _report_holder=holder,
            ),
            timeout=hard,
        )
    except asyncio.TimeoutError:
        _name = str(target.get("name") or target.get("entity") or target.get("query") or "the target")
        logger.error(
            "[dd_orchestrator] R-F1628 HARD DEADLINE hit at %.0fs (budget %.0fs) for %s "
            "— returning partial (impl overran on unclamped work).",
            hard, budget, _name[:60],
        )
        rep = holder.get("report")
        if rep is None:
            rep = ARKDDReport(target=target, orchestrator_mode=mode, trace_id=trace_id)
            rep.identity.entity_name = _name
            rep.identity.entity_type = target.get("type") or EntityType.UNKNOWN.value
        # R-F1830: flag the hard-deadline path as time-boxed too (the _clamp
        # path already does, dd_orchestrator.py ~7454). Callers use this to
        # decide whether to launch a full-depth background completion.
        rep.time_boxed = True  # type: ignore[attr-defined]
        rep.risk_classification = RiskClassification.AMBER_LIGHT.value
        rep.bottom_line = (
            f"⏱ TIME-BUDGET REACHED — the DD on {_name} was stopped at ~{int(hard)}s to "
            f"guarantee a response (it exceeded the {int(budget)}s budget, usually slow or "
            f"unreachable external sources). This is a PARTIAL briefing from the layers that "
            f"completed — re-run, or narrow scope (supply jurisdiction / registration number / "
            f"website) for a full result. Treat as 'incomplete', not 'nothing found'."
        )
        try:
            rep.data_gaps_summary.append(
                f"DD exceeded the {int(budget)}s time budget — partial returned (R-F1628)"
            )
        except Exception:
            pass
        # §25 — a slow DD is a self-heal trigger; tell the brain (best-effort, bounded).
        try:
            from . import brain_hook
            await asyncio.wait_for(
                brain_hook.absorb(
                    module="dd_orchestrator",
                    summary=f"DD hard-deadline hit ({int(hard)}s) for {_name[:40]} — partial returned",
                    entity_name=_name, success=False, confidence="ASSESSED",
                ),
                timeout=5,
            )
        except Exception:
            pass
        return rep
    finally:
        _ka_task.cancel()  # R-F1855 — stop the interactive keepalive


async def _orchestrate_dd_impl(
    target: dict,
    *,
    llm: Any = None,
    mode: str = "standard",
    cost_cap_usd: float | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    user_email: str | None = None,
    share_to_company: bool = True,
    total_budget_s: float | None = None,
    _report_holder: "dict | None" = None,
) -> ARKDDReport:
    """Run the 7-layer DD orchestrator on a target entity.

    Args:
        target: dict with keys:
            - name / entity / query (required)
            - type: "company" | "person" | "address" | "vessel" | ...
            - jurisdiction_iso2: ISO-2 country code (optional)
            - jurisdiction: full country name (optional)
            - registration_number: national registry number (optional)
            - product_description: goods/service description (optional,
              for export-control classification)
            - transaction_value_usd: proposed deal value (optional,
              for ghost-score proportionality check)
        llm: LLMProvider for the digital layer's optional deep_research
             call. Pass None to skip the LLM-backed deep_research step.
        mode: "quick" (skip network + digital deep_research) |
              "standard" (full sequential walk) |
              "deep" (standard + future watchlist diff)
        cost_cap_usd: override the default run cost cap.
        trace_id: link to an existing trace (from chat_ep / autonomous task).

    Returns:
        ARKDDReport — fully populated; also persisted to Redis and
        ready to be delivered via autonomous/delivery.py.
    """
    if not ORCHESTRATOR_ENABLED:
        raise RuntimeError("DD orchestrator disabled via ARIA_DD_ORCHESTRATOR_ENABLED=0")
    if not target or not (target.get("name") or target.get("entity") or target.get("query")):
        raise ValueError("target must include 'name', 'entity', or 'query'")

    # ── R-F659 (2026-05-17) entity-type whitelist gate ──────────────────────
    # Hard input validation per the design-analysis principle: "nothing
    # without entity_type IN (org, person, oem) and a passing name-heuristic
    # enters the DD pipeline." If the caller didn't classify the entity,
    # we refuse rather than guess — UNKNOWN-type runs produced noise findings
    # that then polluted mem0 and claim_ledger for future conversations.
    _type_ok, _type_reason = _validate_entity_type_for_dd(target)
    if not _type_ok:
        raise ValueError(
            f"R-F659: refusing DD on unclassified target: {_type_reason}. "
            f"Pass target['type'] as one of {sorted(_DD_VALID_ENTITY_TYPES)}."
        )

    # ── Entity-name sanity gate (2026-04-17 21:40 fix; extended R-F153 2026-05-10) ──
    # R-F1177: URL-to-entity enrichment. If the target has a website/URL but
    # no valid name, fetch the website and extract the company name from the
    # <title> tag before falling through to name validation. This handles the
    # common case where a user says "do a DD on https://myskyegroove.com".
    # R-F1831: also enrich when the "name" is itself a URL/bare domain (the
    # chat intent passes "modirumgespi.com" through as the name). Without this
    # the guard short-circuits on the URL-shaped name and the org is never
    # resolved → every layer runs against a dead domain string. (The resolution
    # logic lives in _enrich_target_from_url; this is the call-site gate that
    # was suppressing it.)
    _existing_name_g = (target.get("name") or target.get("entity") or "").strip()
    if not _existing_name_g or _looks_like_url_or_domain(_existing_name_g):
        target = await _enrich_target_from_url(target)

    # Original: rejected URL scheme fragments ("https") that came from the
    # intent regex stripping at "/". Extended: rejects prompt-fragment names
    # ("this company, which has nothing to do") AND derives a usable name
    # from intent.website when the explicit name fails validation. Without
    # the website fallback the only option was raise → operator gets nothing
    # back from a chat that DID have a real URL embedded.
    _raw_name = (target.get("name") or target.get("entity") or target.get("query", "")).strip()
    _is_valid, _reject_reason = _validate_entity_name(_raw_name)
    if not _is_valid:
        # R-F153 — fallback: try deriving entity name from intent.website /
        # intent.url before refusing. The 2026-05-10 12:39 dd_d38befa3fd4
        # case: intent had name='this company, which has nothing to do'
        # AND website='lngtradinginternationalpanamasa.com'. The website
        # is a perfectly good entity name; use it.
        _website = (target.get("website") or target.get("url") or "").strip()
        if _website:
            try:
                from urllib.parse import urlparse as _urlparse
                _seed = _website if "://" in _website else f"https://{_website}"
                _host = (_urlparse(_seed).hostname or "").lower().strip(".")
                if _host.startswith("www."):
                    _host = _host[4:]
                if _host:
                    _is_valid_host, _ = _validate_entity_name(_host)
                    if _is_valid_host:
                        logger.info(
                            "[dd_orchestrator] R-F153 entity-name fallback: explicit name %r "
                            "failed validation (%s); using website hostname %r",
                            _raw_name, _reject_reason, _host,
                        )
                        # Mutate target so downstream layers + report storage
                        # see the corrected name. Preserve original name in a
                        # supplementary field for audit.
                        target = {
                            **target,
                            "name": _host,
                            "_original_name_rejected": _raw_name,
                            "_name_derivation": "website_hostname_fallback_R-F153",
                        }
                        _raw_name = _host
                        _is_valid = True
            except Exception:
                logger.debug("R-F153 website-fallback parse failed", exc_info=True)
        if not _is_valid:
            raise ValueError(
                f"Refusing DD on malformed entity name {_raw_name!r}: {_reject_reason}. "
                f"If this came from URL parsing, pass the domain (e.g. 'f3ir.com') "
                f"instead of the scheme. If you have a URL, pass it as `website` "
                f"or `url` and the orchestrator will derive the entity name from it."
            )

    # ── R-F659 (2026-05-17) name-heuristic extras ───────────────────────────
    # The name passed the existing sanity gate (and any R-F153 website
    # fallback) — now apply the additional heuristics from the 2026-05-17
    # design analysis: reject purely-numeric labels ("283", "2026") and
    # RFC 2606 / tutorial placeholder words ("acme widgets", "test corp"),
    # which slipped past _validate_entity_name because they're technically
    # well-formed strings.
    _extras_ok, _extras_reason = _validate_entity_name_extras(_raw_name)
    if not _extras_ok:
        raise ValueError(
            f"R-F659: refusing DD on placeholder/numeric entity name "
            f"{_raw_name!r}: {_extras_reason}."
        )

    cost_cap = cost_cap_usd if cost_cap_usd is not None else DEFAULT_COST_CAP_USD
    t_run_start = time.time()

    # ── R-F1572: overall wall-clock budget (root-cause of WA DD overrun) ──────
    # Per-layer asyncio.wait_for caps DON'T bound the total: a single deep run
    # can chain identity(90)+network(90)+compliance(90)+digital(180)+extensions
    # (45)+… and R-F409 (routes/aria.py) re-runs the WHOLE DD on insufficient
    # evidence — so a sparse target (e.g. an NGO website with no registry
    # footprint) blew past the WhatsApp async-push poll window (15 min). The
    # user then got a dead-end "try again", re-sent, and spawned a duplicate
    # 15-min job. This budget guarantees the run finishes WELL inside the push
    # window, so the poll/callback always delivers a (possibly time-boxed)
    # report. Default 11 min leaves headroom for BLUF assembly + the callback
    # round-trip inside the 15-min budget. Callers that chain runs (R-F409
    # escalation) pass the REMAINING budget so the total still fits.
    _total_budget_s = (
        float(total_budget_s) if total_budget_s is not None
        else float(_env_int("ARIA_DD_TOTAL_BUDGET_S", 660))
    )
    _overall_deadline = t_run_start + _total_budget_s

    # R-F1879 — reserve a tail of the budget for verification + synthesis. The
    # HEAVY data-gathering layers clamp to (budget − reserve); verification +
    # synthesis clamp to the full budget (i.e. the reserved tail). Before this,
    # those two final layers used FIXED 30s/10s timeouts that ignored the budget
    # entirely — the "impl overran on unclamped work" the hard deadline caught.
    _SYNTH_RESERVE_S = float(_env_int("ARIA_DD_SYNTH_RESERVE_S", 15))
    _heavy_deadline = _overall_deadline - _SYNTH_RESERVE_S

    def _clamp(t: float) -> float:
        """Clamp a HEAVY-layer timeout to the remaining budget MINUS the
        synthesis reserve, so verification + synthesis are guaranteed time to
        build the report from whatever the completed layers collected. When the
        heavy budget is exhausted, returns a tiny value so the layer fails fast
        (TimeoutError → marked ERROR, never raises). Never returns 0
        (asyncio.wait_for(timeout=0) can hang on already-scheduled work).
        """
        rem = _heavy_deadline - time.time()
        if rem <= 0:
            return 0.05
        return min(t, rem)

    def _clamp_final(t: float) -> float:
        """Clamp the final verification/synthesis layers to the FULL remaining
        budget (the reserved tail). Bounded like every other layer — never an
        unclamped fixed timeout — but allowed to use the reserve the heavy
        layers left. Floors at 0.05s when even the hard tail is gone."""
        rem = _overall_deadline - time.time()
        if rem <= 0:
            return 0.05
        return min(t, rem)

    report = ARKDDReport(
        target=target,
        orchestrator_mode=mode,
        trace_id=trace_id,
    )
    report.identity.entity_name = target.get("name") or target.get("entity") or target.get("query", "")
    report.identity.entity_type = target.get("type") or EntityType.UNKNOWN.value

    # R-F1628: expose the live report to the hard-deadline wrapper so a budget
    # timeout returns the ACCUMULATED partial (whatever layers completed), not a
    # bare stub. Stashed as early as possible so even an early timeout has it.
    if _report_holder is not None:
        _report_holder["report"] = report

    # Hook into cost_tracker so every LLM call made by the layers is
    # attributed to "dd_orchestrator".
    cost_tracker_token = None
    try:
        from . import cost_tracker
        cost_tracker_token = cost_tracker.set_feature("dd_orchestrator")
    except Exception:
        pass

    # ── Predictor: forecast likely-failure axes BEFORE any layer runs.
    # A predicted gap is a closed gap. Surface past mistakes to the brain
    # so the run is informed by prior corrections. Never blocks — if the
    # predictor degrades, we proceed without it.
    try:
        from . import predictor
        _forecast = await predictor.forecast(
            task_type="dd",
            domain=(target.get("jurisdiction_iso2") or "unknown"),
            entity_type=target.get("type"),
            context={"mode": mode, "run_id": report.run_id},
        )
        report.pre_task_forecast = _forecast  # ignored by schema, carried on instance
        if _forecast["likely_failures"]:
            logger.warning(
                "[dd_orchestrator] predictor forecast for %s/%s: confidence=%.2f, "
                "likely_failures=%d, past_mistakes=%d",
                target.get("name", "?")[:40],
                target.get("jurisdiction_iso2", "?"),
                _forecast["overall_confidence"],
                len(_forecast["likely_failures"]),
                len(_forecast["past_mistakes"]),
            )
            try:
                from . import brain_hook
                top = "; ".join(
                    f"{f['axis']}: {f['reason'][:100]}" for f in _forecast["likely_failures"][:3]
                )
                await brain_hook.absorb(
                    module="predictor",
                    summary=f"DD pre-run forecast {target.get('name', '?')[:40]}: "
                            f"conf={_forecast['overall_confidence']:.2f} — {top}",
                    entity_name=target.get("name", ""),
                    success=True,
                    confidence="ASSESSED",
                )
            except Exception:
                pass
    except Exception as _pe:
        logger.debug("predictor forecast failed (non-fatal): %s", _pe)

    try:
        # ── LAYER 1: IDENTITY ──
        layer_name = "identity"
        report.layers_run.append(layer_name)
        try:
            hard_stop = await asyncio.wait_for(
                _run_identity(target, report),
                timeout=DEFAULT_LAYER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            report.identity.meta.status = LayerStatus.ERROR.value
            report.identity.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S}s"
            hard_stop = False

        # Short-circuit on sanctions hit: skip network/digital, keep
        # compliance + verification + synthesis so the user still gets a
        # structured HARD_STOP report with the reasoning.
        if hard_stop:
            logger.info("[dd_orchestrator] hard stop triggered in identity layer — short-circuiting")
            for layer in ("network", "digital"):
                if layer not in report.layers_skipped:
                    report.layers_skipped.append(layer)
            report.network.meta.status = LayerStatus.SKIPPED.value
            report.digital.meta.status = LayerStatus.SKIPPED.value
        else:
            # ── LAYER 2: NETWORK (unless quick mode) ──
            if mode != "quick":
                layer_name = "network"
                report.layers_run.append(layer_name)
                try:
                    await asyncio.wait_for(_run_network(target, report), timeout=_clamp(DEFAULT_LAYER_TIMEOUT_S))
                except asyncio.TimeoutError:
                    report.network.meta.status = LayerStatus.ERROR.value
                    report.network.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S}s"
            else:
                if "network" not in report.layers_skipped:
                    report.layers_skipped.append("network")
                report.network.meta.status = LayerStatus.SKIPPED.value

        # ── LAYER 4: COMPLIANCE ── (always — it's cheap and load-bearing)
        layer_name = "compliance"
        report.layers_run.append(layer_name)
        try:
            await asyncio.wait_for(_run_compliance(target, report), timeout=_clamp(DEFAULT_LAYER_TIMEOUT_S))
        except asyncio.TimeoutError:
            report.compliance.meta.status = LayerStatus.ERROR.value
            report.compliance.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S}s"

        # ── LAYER 5: DIGITAL (unless quick mode OR short-circuited) ──
        if mode != "quick" and not hard_stop:
            layer_name = "digital"
            report.layers_run.append(layer_name)
            try:
                await asyncio.wait_for(
                    _run_digital(target, report, llm, _mode_is_deep=(mode == "deep")),
                    timeout=_clamp(DEFAULT_LAYER_TIMEOUT_S * 2),
                )
            except asyncio.TimeoutError:
                report.digital.meta.status = LayerStatus.ERROR.value
                report.digital.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S * 2}s"
        elif mode == "quick":
            if "digital" not in report.layers_skipped:
                report.layers_skipped.append("digital")
            report.digital.meta.status = LayerStatus.SKIPPED.value

        # ──         # ?? LAYER 5b: SWEEP INTELLIGENCE (R-F1110) ??
        # Queries the brain for recent signals from the 49-source Node sweep.
        # Best-effort enrichment -- never blocks the DD report.
        if not hard_stop:
            layer_name = "sweep_intelligence"
            report.layers_run.append(layer_name)
            try:
                await asyncio.wait_for(
                    _run_sweep_intelligence(target, report),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                report.sweep_data.meta.status = LayerStatus.ERROR.value
                report.sweep_data.meta.error = "timeout after 15s"
            except Exception as _si_err:
                report.sweep_data.meta.status = LayerStatus.ERROR.value
                report.sweep_data.meta.error = str(_si_err)[:200]
                logger.warning("[dd_orchestrator] Layer 5b sweep intelligence failed (non-fatal): %s", _si_err)

# ?? LAYER 5c: COMMERCIAL COHERENCE ASSESSMENT (2026-04-22) ?? ──
        # Assesses corporate / commercial / legal coherence of the deal
        # structure against jurisdiction norms. Observer, never a gate.
        # Its anomalies feed Layer 5b deception scoring below, and its
        # coherence score feeds Layer 6 synthesis via the findings it
        # emits. Enabled by default; can be disabled with
        # ARIA_LAYER_5C_ENABLED=0 for emergency bypass.
        #
        # See aria_service/intel/commercial_coherence.py + the reference
        # framework doc (Downloads/aria_global_legal_framework.html).
        _coherence_text = ""
        if os.getenv("ARIA_LAYER_5C_ENABLED", "1") != "0" and not hard_stop:
            layer_name = "commercial_coherence"
            report.layers_run.append(layer_name)
            try:
                from . import commercial_coherence as _cc
                _section = await asyncio.wait_for(
                    _cc.assess_commercial_coherence(target, report),
                    timeout=10,  # pure data-driven — no network calls
                )
                _coherence_text = _cc.anomaly_text_for_deception(_section)
                if _section.tier != "GREEN":
                    logger.info(
                        "[dd_orchestrator] Layer 5c %s on %s: score=%.2f, "
                        "%d anomalies, %d licence-chain gaps",
                        _section.tier,
                        report.identity.entity_name or "?",
                        _section.coherence_score,
                        len(_section.anomalies),
                        len(_section.licence_chain_gaps),
                    )
            except asyncio.TimeoutError:
                report.commercial_coherence.meta.status = LayerStatus.ERROR.value
                report.commercial_coherence.meta.error = "timeout after 10s"
            except Exception as _cc_err:
                report.commercial_coherence.meta.status = LayerStatus.ERROR.value
                report.commercial_coherence.meta.error = str(_cc_err)[:200]
                logger.warning("[dd_orchestrator] Layer 5c failed (non-fatal): %s", _cc_err)
        else:
            if "commercial_coherence" not in report.layers_skipped:
                report.layers_skipped.append("commercial_coherence")
            report.commercial_coherence.meta.status = LayerStatus.SKIPPED.value

        # ── LAYER 5b: DECEPTION SCORING (Clause 16 — 2026-04-18 evening) ──
        # Run the validated deception risk analyser over ANY counterparty
        # free-text we have collected. Sources (in priority order):
        #   1. target['counterparty_text'] / target['message_text'] / target['communication']
        #      — the actual message from the counterparty if the caller passed it
        #   2. target['capability_statement'] / target['narrative'] / target['description']
        #      — pitch / proposal text for proposal-context scoring
        #   3. digital.findings narratives — what we discovered in OSINT
        #   4. Layer 5c commercial coherence anomalies (2026-04-22) — so the
        #      deception analyser sees structural incoherence alongside the
        #      counterparty's own words.
        # The score lands on report.deception so synthesis + verification
        # can weight it and the bottom-line renderer can surface ELEVATED/HIGH.
        # Past incident 2026-04-16 — ARIA shipped responses claiming
        # "Deception Detection... protocols running" when the module was
        # NEVER called from the runtime. This wires it for real.
        try:
            from . import deception_detection as _dd
            _texts: list[tuple[str, str]] = []
            for _key in ("counterparty_text", "message_text", "communication", "claim"):
                _v = (target.get(_key) or "").strip()
                if _v and len(_v) >= 50:
                    _texts.append(("entity_claim", _v))
            for _key in ("capability_statement", "narrative", "description", "proposal"):
                _v = (target.get(_key) or "").strip()
                if _v and len(_v) >= 50:
                    _texts.append(("proposal", _v))
            # Also pick up digital-layer findings narratives
            for _f in (report.digital.findings or [])[:3]:
                _narr = getattr(_f, "evidence_text", "") or getattr(_f, "summary", "") or ""
                if _narr and len(_narr) >= 100:
                    _texts.append(("business_communication", _narr))
            # Layer 5c anomalies — treated as business_communication so the
            # deception analyser weights them against business baselines.
            if _coherence_text and len(_coherence_text) >= 50:
                _texts.append(("business_communication", _coherence_text))

            if _texts:
                analyser = _dd.ARIADeceptionAnalyser()
                _max_score = 0.0
                _max_tier = _dd.DeceptionRiskTier.LOW
                _all_signals: list[str] = []
                for _ctx, _text in _texts:
                    score = await analyser.analyse_async(
                        _text[:6000],
                        context_type=_ctx,
                        reference_entity=report.identity.entity_name or "",
                    )
                    if score.raw_score > _max_score:
                        _max_score = score.raw_score
                        _max_tier = score.tier
                    _all_signals.extend(s.category for s in score.signals_detected)
                # Attach to report (schema-tolerant — instance attribute)
                try:
                    report.deception = {
                        "max_score": round(_max_score, 3),
                        "tier": _max_tier.value,
                        "texts_analysed": len(_texts),
                        "signals": sorted(set(_all_signals))[:10],
                        "requires_eDD": _max_tier in (
                            _dd.DeceptionRiskTier.ELEVATED,
                            _dd.DeceptionRiskTier.HIGH,
                        ),
                    }
                except Exception:
                    pass
                if _max_tier in (_dd.DeceptionRiskTier.ELEVATED, _dd.DeceptionRiskTier.HIGH):
                    logger.warning(
                        "[dd_orchestrator] DECEPTION %s on %s: score=%.2f signals=%s",
                        _max_tier.value, report.identity.entity_name or "?",
                        _max_score, sorted(set(_all_signals))[:5],
                    )
        except Exception as _de:
            logger.debug("[dd_orchestrator] deception scoring failed (non-fatal): %s", _de)

        # ── LAYER 5b.2: CITED-ARTIFACT VERIFICATION (R-F451 — 2026-05-13) ──
        # Extracted from the SERBAN letter learning 2026-04-17: counterparties
        # cite fake document IDs (NCNDA-KOR-MCT-0126, LOI-2024-0917, etc.) to
        # manufacture a false sense of an ongoing structured process. The
        # `cited_artifact_verifier` module has had a clean implementation +
        # tests since then, but the DD audit on 2026-05-13 confirmed it was
        # DEAD CODE in production — no call sites in routes, aria_engine, or
        # dd_orchestrator. R-F451 closes the wiring gap so the verifier
        # actually runs on counterparty text during DD.
        #
        # We re-use the same `_texts` corpus that the deception analyser saw,
        # filtered to entity_claim contexts (counterparty's own words —
        # narrative or digital findings are noise here). If any cited IDs
        # exist that we have no record of, we bump deception.requires_eDD
        # and add an "unverified_citation" signal so synthesis weights it.
        try:
            from . import cited_artifact_verifier as _cav
            _ctp_text = " ".join(
                _text for _ctx, _text in (_texts or [])
                if _ctx == "entity_claim"
            ).strip()
            if _ctp_text:
                _cav_result = _cav.verify_cited_artifacts(
                    _ctp_text,
                    counterparty=report.identity.entity_name or None,
                )
                _unv = int(_cav_result.get("unverified") or 0)
                _ver = int(_cav_result.get("verified") or 0)
                if _unv or _ver:
                    # Attach to report.deception (schema-tolerant — instance
                    # attribute, same pattern as the score block above)
                    try:
                        _dep = getattr(report, "deception", None) or {}
                        _dep["cited_artifacts"] = {
                            "verified":   _ver,
                            "unverified": _unv,
                            "summary":    (_cav_result.get("summary") or "")[:300],
                            "cited":      _cav_result.get("cited") or [],
                        }
                        if _unv > 0:
                            # Treat unverified IDs as a deception signal —
                            # mirrors `capability_gaps.py:101` which already
                            # has "unverified_citation" registered.
                            _existing_signals = _dep.get("signals") or []
                            if "unverified_citation" not in _existing_signals:
                                _existing_signals = list(_existing_signals) + ["unverified_citation"]
                                _dep["signals"] = _existing_signals[:10]
                            # Unverified citations escalate to eDD even if
                            # the language-based analyser stayed LOW.
                            _dep["requires_eDD"] = True
                        report.deception = _dep
                    except Exception:
                        pass
                    if _unv > 0:
                        logger.warning(
                            "[dd_orchestrator] R-F451 — %d unverified cited "
                            "document ID(s) on %s: %s",
                            _unv,
                            report.identity.entity_name or "?",
                            [a.get("doc_id") for a in (_cav_result.get("cited") or [])
                             if not a.get("verified")][:5],
                        )
        except Exception as _cav_err:
            logger.debug(
                "[dd_orchestrator] R-F451 cited_artifact_verifier "
                "failed (non-fatal): %s",
                _cav_err,
            )

        # ── LAYER 8: COUNTER-INTELLIGENCE (R-F121 — wired 2026-05-10) ──
        # Sweeps recent intel_ledger signals about this entity for the
        # patterns that none of the prior layers can see: narrative-shift
        # (positive press timed against negative event), coordinated press
        # (≥3 tier-3 sources publishing in the same window), tier
        # contradiction (tier-1 says listed, tier-3 says clean). Result
        # attached to report.counter_intelligence; brain absorbs material
        # alerts.  Fail-open — never blocks DD on its own errors.
        layer_name = "counter_intelligence"
        report.layers_run.append(layer_name)
        try:
            from . import counter_intelligence as _ci
            _ci_result = await asyncio.wait_for(
                _ci.scan_entity(report.identity.entity_name or "", window_days=30),
                timeout=8,
            )
            try:
                report.counter_intelligence = _ci_result  # type: ignore[attr-defined]
            except Exception:
                pass
            if isinstance(_ci_result, dict) and _ci_result.get("composite_score", 0) >= 0.5:
                logger.warning(
                    "[dd_orchestrator] COUNTER-INTEL alert on %s: score=%.2f patterns=%s",
                    report.identity.entity_name or "?",
                    _ci_result.get("composite_score", 0),
                    list((_ci_result.get("patterns") or {}).keys())[:3],
                )
        except asyncio.TimeoutError:
            logger.warning("[dd_orchestrator] counter-intel timed out (non-fatal)")
        except Exception as _ci_err:
            logger.debug("[dd_orchestrator] counter-intel failed (non-fatal): %s", _ci_err)

        # ── LAYER 9: SANCTIONS DIVERGENCE (R-F122 — wired 2026-05-10) ──
        # Cross-list jurisdictional divergence: entity listed by US OFAC
        # but not UK OFSI? UN SC silent while EU acts? This is the
        # compliance-officer ground-truth question and the prior identity
        # layer's parallel screen reports presence/absence per source —
        # this layer tells the operator the *meaning* of that pattern.
        # Result attached to report.sanctions_divergence.
        layer_name = "sanctions_divergence"
        report.layers_run.append(layer_name)
        try:
            from . import sanctions_divergence as _sdiv
            _sdiv_result = await asyncio.wait_for(
                _sdiv.analyze_divergence(report.identity.entity_name or ""),
                timeout=10,
            )
            try:
                report.sanctions_divergence = _sdiv_result  # type: ignore[attr-defined]
            except Exception:
                pass
            if (
                isinstance(_sdiv_result, dict)
                and _sdiv_result.get("matches", 0) > 0
                and _sdiv_result.get("divergence_count", 0) >= 1
            ):
                logger.warning(
                    "[dd_orchestrator] SANCTIONS DIVERGENCE on %s: listed=%s, silent=%s",
                    report.identity.entity_name or "?",
                    _sdiv_result.get("jurisdictions_listed"),
                    _sdiv_result.get("jurisdictions_not_listed"),
                )
        except asyncio.TimeoutError:
            logger.warning("[dd_orchestrator] sanctions divergence timed out (non-fatal)")
        except Exception as _sdiv_err:
            logger.debug(
                "[dd_orchestrator] sanctions divergence failed (non-fatal): %s",
                _sdiv_err,
            )

        # ── LAYER 10: FORENSIC (Benford + TBML — R-F123, wired 2026-05-10) ──
        # Apply Benford's Law to financial figures collected for this
        # entity (procurement history values + caller-provided figures)
        # and run the TBML transaction classifier over caller-provided
        # transaction line items. Conservative gate: Benford only if
        # ≥50 distinct values; TBML only if transactions list present.
        # When neither gate fires the layer self-skips silently.
        layer_name = "forensic"
        report.layers_run.append(layer_name)
        _forensic_out: dict[str, Any] = {}
        try:
            _values: list[float] = []
            for _key in ("financials", "values", "amounts", "figures", "contract_values"):
                _v = target.get(_key)
                if isinstance(_v, list):
                    for _x in _v:
                        try:
                            _values.append(float(_x))
                        except Exception:
                            pass
            for _ph in (report.network.procurement_history or [])[:200]:
                if not isinstance(_ph, dict):
                    continue
                for _k in ("value", "amount", "contract_value", "award_value"):
                    _vv = _ph.get(_k)
                    if _vv is None:
                        continue
                    try:
                        _values.append(float(_vv))
                    except Exception:
                        pass
            if len(_values) >= 50:
                from . import forensic_benford as _fb
                _benford = _fb.benford_test(_values)
                if isinstance(_benford, dict):
                    _forensic_out["benford"] = {
                        "n":          _benford.get("n"),
                        "chi_square": _benford.get("chi_square"),
                        "p_value":    _benford.get("p_value"),
                        "tier":       _benford.get("tier"),
                        "narrative":  _fb.benford_narrative(_benford),
                    }
                    if str(_benford.get("tier") or "").upper() == "HIGH":
                        logger.warning(
                            "[dd_orchestrator] BENFORD anomaly on %s: chi2=%s, p=%s, n=%s",
                            report.identity.entity_name or "?",
                            _benford.get("chi_square"),
                            _benford.get("p_value"),
                            _benford.get("n"),
                        )
            _txns = target.get("transactions")
            if isinstance(_txns, list) and _txns:
                from . import tbml_detection as _tbml
                _tbml_results: list[dict[str, Any]] = []
                for _t in _txns[:25]:
                    if not isinstance(_t, dict):
                        continue
                    try:
                        _r = await asyncio.wait_for(_tbml.analyze_transaction(_t), timeout=6)
                        if isinstance(_r, dict):
                            _tbml_results.append(_r)
                    except Exception:
                        continue
                if _tbml_results:
                    _forensic_out["tbml"] = {
                        "transactions_analysed": len(_tbml_results),
                        "high_anomalies": sum(
                            1 for _r in _tbml_results
                            if str(_r.get("anomaly_tier") or "").upper() == "HIGH"
                        ),
                        "results": _tbml_results,
                    }
            if _forensic_out:
                try:
                    report.forensic = _forensic_out  # type: ignore[attr-defined]
                except Exception:
                    pass
                logger.info(
                    "[dd_orchestrator] forensic layer ran: keys=%s",
                    sorted(_forensic_out.keys()),
                )
            else:
                if "forensic" not in report.layers_skipped:
                    report.layers_skipped.append("forensic")
        except Exception as _fx_err:
            logger.debug("[dd_orchestrator] forensic layer failed (non-fatal): %s", _fx_err)

        # ── R-F584/585/586/587 (2026-05-16) — extension shim block ──
        # Runs the 4 capability modules shipped earlier on 2026-05-16:
        #   - R-F584 court_records   → CourtListener + Bailii lookup
        #   - R-F585 cert_transparency → crt.sh shell-domain detector
        #   - R-F586 eccn_lookup       → deterministic ECCN keyword match
        #   - R-F587 ais_gap_detector  → vessel transponder gap analysis
        # Bundled via dd_layer_extensions.run_all_extensions() so this
        # single block replaces 4 scattered inserts (minimises merge
        # collision with the parallel agent's dd_orchestrator edits).
        # Each shim is fail-safe; the wrapper itself never raises.
        # Position: AFTER forensic / BEFORE verification — so the new
        # findings can feed verification's grounded-rate computation.
        try:
            from . import dd_layer_extensions as _dlx
            _dlx_result = await asyncio.wait_for(
                _dlx.run_all_extensions(target, report, timeout_per_module=15.0),
                timeout=_clamp(45.0),
            )
            # Attach onto report.extensions if attribute exists; else log.
            try:
                report.extensions = _dlx_result  # type: ignore[attr-defined]
            except Exception:
                pass
            if _dlx_result.get("ran"):
                logger.info(
                    "[dd_orchestrator] R-F584-F587 extensions ran=%s skipped=%s max_severity=%s",
                    _dlx_result.get("ran"),
                    _dlx_result.get("skipped"),
                    _dlx_result.get("max_severity"),
                )
            # ── R-F600 — Promote court-record hits to compliance Findings ──
            # The extension wrapper stores litigation hits inside
            # by_module["court_records"]["hits"] but never surfaces them
            # as Finding rows. This block turns each hit into an
            # auditable row on report.compliance.findings so a client
            # reading the DD report sees "Smith v. ABC Corp · 2024-03-15
            # · S.D.N.Y." as first-class evidence.
            try:
                _cr_block = (_dlx_result.get("by_module") or {}).get("court_records")
                for _cr_f in _emit_court_record_findings(_cr_block):
                    report.compliance.findings.append(_cr_f)
            except Exception as _cr_err:
                logger.debug(
                    "[dd_orchestrator] R-F600 court-record Finding emission failed (non-fatal): %s",
                    _cr_err,
                )
            # ── R-F610 — Promote AIS dark-voyage signals to Findings ──
            # ais_gap_detector (R-F587) already produces a structured
            # block when vessel positions are supplied. Surface the
            # signals as Findings so maritime DD on a counterparty's
            # vessels shows dark-voyage evidence in the report.
            try:
                _ais_block = (_dlx_result.get("by_module") or {}).get("ais_gap_detector")
                for _ais_f in _emit_ais_findings(_ais_block):
                    report.compliance.findings.append(_ais_f)
            except Exception as _ais_err:
                logger.debug(
                    "[dd_orchestrator] R-F610 AIS Finding emission failed (non-fatal): %s",
                    _ais_err,
                )
            # ── R-F611 — Promote cert-transparency signals to Findings ──
            # cert_transparency (R-F585) scores a shell-broker fingerprint
            # from Certificate Transparency logs. Surface high-score
            # signals as Findings so cyber/domain-spinning evidence
            # reaches the compliance section.
            try:
                _ct_block = (_dlx_result.get("by_module") or {}).get("cert_transparency")
                for _ct_f in _emit_cert_transparency_findings(_ct_block):
                    report.compliance.findings.append(_ct_f)
            except Exception as _ct_err:
                logger.debug(
                    "[dd_orchestrator] R-F611 cert_transparency Finding emission failed (non-fatal): %s",
                    _ct_err,
                )
            # ── R-F612 — Promote ECCN keyword-indicator hits to Findings ──
            # eccn_lookup (R-F581/F586) returns potential ECCN matches
            # for the equipment text. Each hit is an INDICATOR (Clause 3,
            # Clause 14) — operator must consult licensed export counsel.
            # Promoting to Findings surfaces export-control risk in the
            # compliance section so a client reading the DD report sees
            # the regulated nature of the goods.
            try:
                _ec_block = (_dlx_result.get("by_module") or {}).get("eccn_lookup")
                for _ec_f in _emit_eccn_findings(_ec_block):
                    report.compliance.findings.append(_ec_f)
            except Exception as _ec_err:
                logger.debug(
                    "[dd_orchestrator] R-F612 ECCN Finding emission failed (non-fatal): %s",
                    _ec_err,
                )
        except asyncio.TimeoutError:
            logger.warning("[dd_orchestrator] R-F584-F587 extension bundle timed out (non-fatal)")
        except Exception as _dlx_err:
            logger.debug(
                "[dd_orchestrator] R-F584-F587 extensions failed (non-fatal): %s",
                _dlx_err,
            )

        # ── LAYER 3: VERIFICATION (runs over what the previous layers collected) ──
        layer_name = "verification"
        report.layers_run.append(layer_name)
        try:
            # R-F1879: clamped to the reserved tail (was a fixed 30s that ignored
            # the budget — the unclamped overrun the hard deadline caught).
            await asyncio.wait_for(_run_verification(target, report), timeout=_clamp_final(30))
        except asyncio.TimeoutError:
            report.verification.meta.status = LayerStatus.ERROR.value
            report.verification.meta.error = "timeout (budget-clamped)"

        # ── LAYER 6: SYNTHESIS ──
        layer_name = "synthesis"
        report.layers_run.append(layer_name)
        try:
            # R-F1879: clamped to the reserved tail (was a fixed 10s that ignored
            # the budget). Synthesis still gets the reserve the heavy layers left.
            await asyncio.wait_for(_run_synthesis(target, report), timeout=_clamp_final(10))
        except asyncio.TimeoutError:
            report.synthesis.meta.status = LayerStatus.ERROR.value
            report.synthesis.meta.error = "timeout (budget-clamped)"

        # ── R-F1572: flag a time-boxed run so the BLUF/footer is honest ──
        # If the HEAVY-gathering budget was hit, the data layers were cut short
        # by _clamp() — the report is real but partial. Mark it so downstream
        # delivery can say so rather than implying full coverage.
        # R-F1879: key off _heavy_deadline (budget − synthesis reserve), NOT
        # _overall_deadline. Heavy layers now fail fast at _heavy_deadline, so a
        # tight/exhausted budget finishes BEFORE _overall_deadline — checking the
        # overall deadline missed those genuinely time-boxed runs.
        if time.time() >= _heavy_deadline:
            try:
                report.time_boxed = True  # type: ignore[attr-defined]
            except Exception:
                pass
            logger.warning(
                "[dd_orchestrator] R-F1572 time-boxed run for %s — overall "
                "budget %.0fs exhausted; delivering partial report from %d "
                "completed layer(s)",
                report.identity.entity_name or target.get("name", "?"),
                _total_budget_s,
                len(report.layers_run),
            )

    finally:
        if cost_tracker_token is not None:
            try:
                from . import cost_tracker
                cost_tracker.reset_feature(cost_tracker_token)
            except Exception:
                pass

    # ── BLUF + assembly ──
    await _assemble_bluf(report)

    # ── R-F157: discipline coverage check (Stage A wiring per ecosystem-audit) ──
    # Per dd_disciplines.py framework — for every entity_type, a defined set
    # of disciplines should be covered by a complete DD. This block surfaces
    # which were covered + which weren't so the operator + downstream consumer
    # see the gaps explicitly. NOT a blocker: even if coverage is partial,
    # the report still ships — gaps surface in `report.discipline_coverage`.
    try:
        from . import dd_disciplines as _dd_disc
        from . import regulated_commodity_pack as _rcp

        # Heuristic entity-type detection. Conservative: defaults to
        # defence_broker (ARIA's primary positioning); switches to
        # commodity_broker when commodity heuristics fire on the target.
        _is_commodity, _commodity_class = _rcp.is_commodity_dd_target(
            entity_name=report.identity.entity_name or target.get("name", "") or "",
            url=target.get("website") or target.get("url", "") or "",
            sector_hint=target.get("sector", "") or "",
        )
        if _is_commodity and _commodity_class == "lng":
            _entity_type = "commodity_broker_oil_lng"
        elif _is_commodity and _commodity_class in ("crude_oil", "refined_products", "natural_gas"):
            _entity_type = "commodity_broker_crude"
        elif _is_commodity:
            _entity_type = "commodity_broker_oil_lng"  # default for ambiguous commodity
        else:
            _entity_type = "defence_broker"

        # Map executed layers → covered disciplines. Conservative: a layer
        # that ran AND produced findings or non-empty meta counts as covering
        # its core disciplines. Layers that ran but returned empty (no data
        # found) still count as covered (the absence-of-findings is itself
        # a finding) but are tagged for transparency.
        _covered: list[str] = []

        def _section_active(section) -> bool:
            """R-F297: a layer counts as 'covered' only if it produced REAL
            data, not just an OK status with empty findings. Previously any
            section with status=OK was treated as covered even when its
            findings list was empty and its only data_gap was "manual
            registry lookup unavailable" — this caused the discipline
            framework to claim 'ubo_chain covered' / 'adverse_media
            covered' on runs where nothing was actually retrieved, which
            in turn made the BLUF / chat summary read 'AMBER-LIGHT, looks
            fine' on data-empty runs.

            New rule — a section is active iff AT LEAST ONE of:
              (a) a non-info finding (severity in red/amber/hard_stop)
              (b) an info finding whose detail is substantive (>40 chars)
              (c) a populated structured field (directors / press /
                  country_risk / shareholders / etc.)
            Empty findings + only 'unavailable' data_gaps → INACTIVE.
            """
            if not section:
                return False

            # (a) Any non-info finding counts
            findings = getattr(section, "findings", None) or []
            for f in findings:
                sev = (getattr(f, "severity", "") or "").lower()
                if sev in ("red", "amber", "hard_stop", "hard-stop", "no-go"):
                    return True
                # (b) info finding with substantive detail
                detail = getattr(f, "detail", "") or ""
                if len(detail) >= 40:
                    return True
                # info with a substantive title also counts (some layers
                # store the meat in the title, e.g. CH backfill)
                title = getattr(f, "title", "") or ""
                if len(title) >= 60:
                    return True

            # (c) populated structured fields
            for fname in (
                "directors", "shareholders", "press_coverage",
                "officer_links", "psc_chain", "ubo_chain",
                "sanctions_matches", "country_risk", "regulatory_enforcement",
                "registration_number", "incorporation_date",
                "registered_address", "declared_activity",
            ):
                fv = getattr(section, fname, None)
                if not fv:
                    continue
                # Skip stub structures (e.g. empty dict, empty list)
                if isinstance(fv, (list, dict, set, tuple)) and len(fv) == 0:
                    continue
                return True

            return False

        if _section_active(report.identity):
            _covered.extend(["identity_verification", "sanctions_screening", "ubo_chain"])
            # PEP screen typically rides on identity sub-calls
            _covered.append("pep_screening")
        if _section_active(report.network):
            _covered.extend(["ubo_chain", "operational_substance"])
        if _section_active(report.verification):
            _covered.extend(["adverse_media"])  # partial — Stage B will deepen
        if _section_active(report.compliance):
            _covered.extend([
                "jurisdiction_country_risk", "anti_bribery_corruption",
                "regulatory_enforcement", "litigation_history",
            ])
            # Defence-specific compliance layers (when running defence DD)
            if _entity_type in ("defence_broker", "defence_oem"):
                _covered.extend([
                    "end_use_verification", "reexport_diversion_risk",
                    "technology_classification",
                ])
        if _section_active(report.digital):
            _covered.extend(["adverse_media", "reputational_intelligence"])
        # Commercial coherence layer (5c) covers contractual structure when commodity
        if getattr(report, "commercial_coherence", None) and _section_active(report.commercial_coherence):
            if _entity_type.startswith("commodity_"):
                _covered.extend(["contractual_structure", "price_cap_attestation"])
        if _section_active(report.synthesis):
            # Synthesis touches financial soundness via aggregation
            _covered.append("financial_soundness")

        # De-dup
        _covered = sorted(set(_covered))

        _coverage = _dd_disc.discipline_coverage_check(_covered, _entity_type)
        # Annotate the report with the coverage result. Use a plain dict
        # attribute since ARKDDReport schema may not have a dedicated field;
        # downstream serialisation includes attributes via __dict__.
        report.discipline_coverage = {
            "entity_type_detected": _entity_type,
            "commodity_classification": _commodity_class if _is_commodity else None,
            "result": _coverage,
            "framework_version": "dd_disciplines.py R-F152",
            "note": (
                "Stage A wiring (R-F157) — coverage based on which orchestrator "
                "layers ran. Stage B (adverse-media query templates → real deep "
                "search) will refine the adverse_media coverage signal from "
                "'partial' to 'verified'. Stage C will tighten commodity discipline "
                "matching when entity type is detected with higher confidence."
            ),
        }
        logger.info(
            "[R-F157] discipline coverage: entity_type=%s covered=%d/%d (%.1f%%) gate_passes=%s",
            _entity_type, len(_coverage["covered"]), len(_coverage["required"]),
            _coverage["coverage_pct"], _coverage["gate_passes"],
        )
    except Exception as _cov_err:
        logger.debug("[R-F157] discipline coverage check failed (non-fatal): %s", _cov_err)
        try:
            report.discipline_coverage = {"error": str(_cov_err)[:200], "framework_version": "dd_disciplines.py R-F152"}
        except Exception:
            pass

    # ── R-F160: adverse-media deep search on RED-classification (Stage B wiring policy) ──
    # Per operator decision 2026-05-10: adverse-media deep search (Stage B
    # function from R-F159) fires automatically on RED / HARD_STOP / NO-GO
    # verdicts. Rationale: the marginal cost (30-50 search-backend calls per
    # DD) is justified for high-risk classifications where the operator most
    # needs depth. GREEN / AMBER DDs use the existing Layer 5 web_search +
    # deep_research only — no extra cost. On-demand path also exposed via
    # POST /api/aria/dd/adverse-media-search for operator-initiated runs.
    # Findings appended to report as report.adverse_media (separate from
    # report.digital so dashboards can render the depth distinctly).
    try:
        _risk_for_am = (report.risk_classification or "").upper()
        # R-F300: previously fired only on RED / HARD_STOP / NO-GO. But the
        # exact population that benefits MOST from adverse-media depth is
        # AMBER runs where the confidence gate down-rated a thin GREEN —
        # we have weak data, so we should go deeper, not give up. Extended
        # trigger: any AMBER variant plus any run where the discipline
        # coverage came back below 60% (per R-F157 framework).
        _coverage_pct = 100.0
        try:
            if report.discipline_coverage:
                _coverage_pct = float(
                    (report.discipline_coverage.get("result") or {})
                    .get("coverage_pct", 100.0)
                )
        except Exception:
            pass
        _am_triggered = (
            _risk_for_am in ("RED", "HARD_STOP", "NO-GO")
            or _risk_for_am.startswith("AMBER")
            or _coverage_pct < 60.0
        )
        # R-F1572: this 30-template × 5-result × 10-year deep search runs AFTER
        # the layers + BLUF and is OUTSIDE the layer clamp — it was the dominant
        # overrun for sparse targets, because AMBER / <60%-coverage (exactly an
        # NGO/website with no registry footprint) trigger it. Bound it to the
        # overall budget: skip if the budget is spent, else cap the call.
        _am_budget = _overall_deadline - time.time()
        if _am_triggered and _am_budget < 20.0:
            logger.warning(
                "[R-F1572] adverse-media deep search SKIPPED for %s — overall "
                "budget exhausted (%.0fs left); report is time-boxed",
                report.identity.entity_name or "?", _am_budget,
            )
            try:
                report.adverse_media = {
                    "skipped": "overall_budget_exhausted",
                    "framework_version": "R-F1572 time-box",
                }
            except Exception:
                pass
        _should_run_am = _am_triggered and _am_budget >= 20.0
        if _should_run_am:
            from . import researcher as _res
            # Pull director/UBO names from the network layer if present
            _director_names: list[str] = []
            _ubo_names: list[str] = []
            try:
                for n in getattr(report.network, "directors", []) or []:
                    _name = getattr(n, "name", None) or (n.get("name") if isinstance(n, dict) else None)
                    if _name:
                        _director_names.append(_name)
                for u in getattr(report.network, "ubo_chain", []) or []:
                    _name = getattr(u, "name", None) or (u.get("name") if isinstance(u, dict) else None)
                    if _name:
                        _ubo_names.append(_name)
            except Exception:
                pass
            # Sector hint from target dict
            _sectors: list[str] = []
            _sec_hint = (target.get("sector") or "").lower()
            if _sec_hint:
                _sectors.append(_sec_hint)
            # If commodity entity detected earlier, add commodity sector tag
            try:
                if report.discipline_coverage and report.discipline_coverage.get("commodity_classification"):
                    _sectors.append("oil")  # generic commodity hint for trade-press templates
            except Exception:
                pass
            if not _sectors:
                _sectors = ["defence"]  # ARIA's default vertical
            _am_result = await asyncio.wait_for(
                _res.run_adverse_media_deep_search(
                    entity_name=report.identity.entity_name,
                    director_names=_director_names[:3],  # cap to 3 to bound search cost
                    ubo_names=_ubo_names[:2],            # cap to 2
                    sectors=_sectors,
                    years_back=10,
                    max_templates=30,
                    max_results_per_template=5,
                ),
                timeout=min(_am_budget, 240.0),  # R-F1572: never exceed the budget
            )
            report.adverse_media = _am_result
            # R-F300 follow-up: log now reflects R-F300 expanded triggers
            # (AMBER variants + low-coverage), not just RED.
            _trigger_reason = (
                "RED" if _risk_for_am in ("RED", "HARD_STOP", "NO-GO")
                else f"AMBER ({_risk_for_am})" if _risk_for_am.startswith("AMBER")
                else f"low-coverage ({_coverage_pct:.0f}%)"
            )
            logger.info(
                "[R-F160/F300] adverse-media deep search (%s-trigger): %d findings across %d source classes in %.1fs",
                _trigger_reason,
                _am_result.get("findings_count", 0),
                len(_am_result.get("coverage_by_class", {}) or {}),
                _am_result.get("execution_time_seconds", 0),
            )
    except Exception as _am_err:
        logger.debug("[R-F160] adverse-media deep search failed (non-fatal): %s", _am_err)
        try:
            report.adverse_media = {"error": str(_am_err)[:200], "framework_version": "researcher.run_adverse_media_deep_search R-F159"}
        except Exception:
            pass

    # ── Verification gate (2026-04-18) ──
    # On RED verdicts, run a second-opinion pass through a different
    # provider and compare the structured decision. If they disagree
    # we stamp CRITICAL_UNVERIFIED and the downstream handler knows
    # to block auto-send. If they agree we stamp VERIFIED BY
    # DISAGREEMENT so the confidence is earned, not claimed.
    # Adds ~20-40s to a RED DD only; GREEN/AMBER passes through
    # untouched so routine DD latency is unchanged.
    try:
        _risk = (report.risk_classification or "").upper()
        if _risk in ("RED", "HARD_STOP", "NO-GO") and llm is not None:
            from ..learning import verification_gate as _vg
            # Build a concise secondary-opinion prompt from what we
            # already have. The secondary doesn't re-run tools — it
            # reasons over the same evidence to see if it reaches
            # the same verdict independently.
            evidence_brief = (
                f"Entity: {report.identity.entity_name}\n"
                f"Jurisdiction: {report.identity.jurisdiction_iso2 or 'unknown'}\n"
                f"Sanctions screen: {report.identity.sanctions_screen or 'CLEAN'}\n"
                f"Data gaps: {', '.join(report.data_gaps_summary[:10]) if report.data_gaps_summary else 'none'}\n"
                f"Findings (identity): {'; '.join(getattr(f, 'title', '')[:120] for f in report.identity.findings[:6])}\n"
                f"Findings (network):  {'; '.join(getattr(f, 'title', '')[:120] for f in report.network.findings[:4])}\n"
                f"Findings (digital):  {'; '.join(getattr(f, 'title', '')[:120] for f in report.digital.findings[:4])}\n"
            )
            sys_prompt = (
                "You are a defence-compliance auditor reviewing evidence "
                "on one specific entity. Return a concise verdict:\n"
                "  Risk: RED / AMBER / GREEN\n"
                "  Sanctions: HIT / CLEAN\n"
                "  Recommendation: HALT / PROCEED\n"
                "  Confidence: [CONFIRMED / PROBABLE / ASSESSED / UNCERTAIN]\n"
                "Be brief — reasoning in 6-10 lines max. Do NOT invent "
                "new facts. Only reason over the evidence given."
            )
            primary_narrative = report.bottom_line or (report.synthesis.rationale or "")
            if primary_narrative and len(primary_narrative) > 50:
                # Run ONLY the secondary via a different provider — use
                # whatever narrative is already in the report as the primary
                sec_provider = _vg.pick_secondary_provider(
                    llm, exclude_name=getattr(llm, "_last_used_name", "") or ""
                )
                secondary_narrative = ""
                if sec_provider is None:
                    # CRITICAL-grade DD output but no independent secondary
                    # — count it so /verification/stats surfaces the gap
                    # instead of silently reading 0/0/0.
                    try:
                        await _vg.record_skipped("no_secondary_provider")
                    except Exception:
                        pass
                if sec_provider is not None:
                    try:
                        _t_sec_start = time.time()
                        _r = await asyncio.wait_for(
                            sec_provider.complete(
                                sys_prompt, evidence_brief,
                                max_tokens=400, timeout=45.0,
                            ),
                            timeout=50.0,
                        )
                        secondary_narrative = getattr(_r, "text", "") or ""
                        logger.info(
                            "[dd_orchestrator] verification secondary pass on %s — %.1fs",
                            sec_provider.name, time.time() - _t_sec_start,
                        )
                        if not secondary_narrative:
                            try:
                                await _vg.record_skipped("secondary_empty")
                            except Exception:
                                pass
                    except Exception as _sec_err:
                        try:
                            await _vg.record_skipped("secondary_call_failed")
                        except Exception:
                            pass
                        logger.warning(
                            "[dd_orchestrator] verification secondary pass failed: %s",
                            _sec_err,
                        )
                if secondary_narrative:
                    vres = await _vg.verify(
                        primary_narrative, secondary_narrative,
                        metadata={"risk_classification": _risk},
                    )
                    # Attach to report — schema-tolerant (the field may
                    # not exist in all dataclass variants, so use setattr).
                    try:
                        report.verification_gate = {
                            "verdict": vres["verdict"],
                            "severity": vres["disagreement"]["severity"] if vres.get("disagreement") else "NONE",
                            "disagreements": vres["disagreement"]["disagreements"] if vres.get("disagreement") else [],
                            "recommendation": vres.get("recommendation"),
                            "primary_provider": getattr(llm, "_last_used_name", "") or "fallback",
                            "secondary_provider": getattr(sec_provider, "name", "") if sec_provider else "",
                        }
                    except Exception:
                        pass
                    # Stamp the BLUF so any downstream renderer surfaces it
                    tag = (
                        "\n\n🛡 [VERIFIED BY DISAGREEMENT — both providers agree]"
                        if vres["verdict"] == "CRITICAL_VERIFIED"
                        else f"\n\n⚠ [CRITICAL — PROVIDERS DISAGREE — {vres['disagreement']['severity']}] "
                             f"Block auto-send; human adjudication required."
                    )
                    if report.bottom_line and tag not in report.bottom_line:
                        report.bottom_line = report.bottom_line + tag
    except Exception as _vg_err:
        logger.warning(
            "[dd_orchestrator] verification gate failed (non-fatal): %s", _vg_err
        )

    report.total_duration_ms = int((time.time() - t_run_start) * 1000)
    report.layer_costs_usd = {
        "identity":             report.identity.meta.cost_usd,
        "network":              report.network.meta.cost_usd,
        "verification":         report.verification.meta.cost_usd,
        "compliance":           report.compliance.meta.cost_usd,
        "digital":              report.digital.meta.cost_usd,
        "commercial_coherence": report.commercial_coherence.meta.cost_usd,
        "synthesis":            report.synthesis.meta.cost_usd,
    }
    report.total_cost_usd = sum(report.layer_costs_usd.values())

    # R-F119 (2026-05-09): the per-layer meta.cost_usd fields above are
    # rarely populated by individual layers (DD layers don't track cost
    # internally — they call the wrapped LLM and MeteredProvider records
    # to cost_tracker out-of-band). Sum from cost_tracker by feature
    # window so report.total_cost_usd reflects what was actually spent.
    # Operator-visible fix: 'Cost: $0.0000' on every DD report (TARA
    # AEROSPACE on 2026-05-09 was the trigger).
    if report.total_cost_usd == 0:
        try:
            from . import cost_tracker as _ct
            recent = await _ct.list_recent_calls(limit=200)
            window_total = 0.0
            window_calls = 0
            for c in (recent or []):
                if not isinstance(c, dict):
                    continue
                ts = c.get("ts") or 0
                if ts < t_run_start:
                    continue
                feat = c.get("feature") or ""
                if feat == "dd_orchestrator" or not feat or feat == "uncategorized":
                    window_total += float(c.get("cost_usd") or 0.0)
                    window_calls += 1
            if window_total > 0:
                report.total_cost_usd = round(window_total, 6)
                # Stamp synthesis layer as the carrier for the aggregate
                # since per-layer attribution isn't available
                report.synthesis.meta.cost_usd = round(window_total, 6)
                report.layer_costs_usd["synthesis"] = round(window_total, 6)
                logger.info(
                    "[dd_orchestrator] cost backfill from cost_tracker: "
                    "$%.4f over %d calls in window",
                    window_total, window_calls,
                )
        except Exception as _ce:
            logger.debug("[dd_orchestrator] cost backfill failed: %s", _ce)

    # ── R-F607 (2026-05-16) — stamp originating user identity on the
    # report before persist so list_reports() can filter to "your own
    # runs" and so R-F608's company-shared visibility can OR-match on
    # email domain. All three fields are best-effort: if the caller
    # didn't supply them (autonomous loops, internal admin calls) they
    # stay None and the entry becomes admin-only in the user-filtered
    # list path.
    if user_id:
        report.user_id = user_id
    if user_email:
        _norm = (user_email or "").strip().lower()
        if _norm:
            report.user_email_lower = _norm
            if "@" in _norm:
                report.user_email_domain = _norm.split("@", 1)[1] or None

    # R-F608 (2026-05-16) — per-DD company-share toggle. Default True;
    # operator sets False on the orchestrate request to keep this run
    # private even from same-company colleagues. list_reports honours
    # the resulting share_to_company key on the index entry.
    report.share_to_company = bool(share_to_company)

    # ── Persist + deliver ──
    await _persist_report(report)

    # ── Brain hook: feed all learning tiers ──
    try:
        from . import brain_hook
        _dd_summary = (
            f"DD report on {report.identity.entity_name} "
            f"({report.identity.entity_type}, {report.identity.jurisdiction_iso2 or 'unknown jurisdiction'}): "
            f"risk={report.risk_classification}. {report.bottom_line[:300]}"
        )
        _dd_detail_parts = [_dd_summary]
        _sanctions = report.identity.sanctions_screen or {}
        if _sanctions.get("matches"):
            _dd_detail_parts.append(f"Sanctions: {len(_sanctions['matches'])} matches")
        if report.network.findings:
            _dd_detail_parts.append(f"Network: {'; '.join(getattr(f, 'title', '')[:100] for f in report.network.findings[:5])}")
        if report.digital.findings:
            _dd_detail_parts.append(f"Digital: {'; '.join(getattr(f, 'title', '')[:100] for f in report.digital.findings[:5])}")
        _cc_for_brain = report.commercial_coherence
        if (_cc_for_brain.tier or "GREEN").upper() != "GREEN":
            _dd_detail_parts.append(
                f"Coherence: {_cc_for_brain.tier} "
                f"(score {_cc_for_brain.coherence_score:.2f}, "
                f"{len(_cc_for_brain.anomalies)} anomalies, "
                f"{len(_cc_for_brain.licence_chain_gaps)} licence-chain gaps)"
            )
        if report.data_gaps_summary:
            _dd_detail_parts.append(f"Data gaps: {', '.join(report.data_gaps_summary[:5])}")
        # R-F1878: fire-and-forget — do NOT await. This completion signal is
        # telemetry; awaiting brain_hook.absorb here blocked DD completion for up
        # to ~29s (GIL-bound embed) and tripped the brain_hook circuit. The
        # tracked background task + brain_hook's own background tiers absorb it
        # off the DD path.
        _fire_signal(
            module="dd_orchestrator",
            summary=_dd_summary,
            detail=" | ".join(_dd_detail_parts),
            entity_name=report.identity.entity_name or "",
            success=report.risk_classification != "ERROR",
            source_id=report.run_id,
            confidence="PROBABLE",
            gap_type="knowledge_gap" if report.data_gaps_summary else None,
            gap_detail=f"DD data gaps for {report.identity.entity_name}: {', '.join(report.data_gaps_summary[:5])}" if report.data_gaps_summary else None,
        )
    except Exception as e:
        logger.warning("dd_orchestrator: brain_hook signal failed (non-fatal): %s", e)

    # R-F305: ecosystem awareness. Before returning, stamp the report with
    # a per-layer activity snapshot so the chat/dashboard/self_diagnostic
    # can SEE which layers actually produced data versus which ran but
    # returned empty (wired-but-silent — the failure mode that drove the
    # 2026-05-11 operator complaint "ARIA going backwards").
    try:
        report.ecosystem_status = _build_ecosystem_status(report)
        # Surface a top-level WARNING-severity finding if ≥2 layers were
        # wired-but-silent — that's a signal something upstream went wrong
        # (e.g. mode=standard skipped link-tree, registry adapter offline).
        silent = [
            k for k, v in report.ecosystem_status.get("layers", {}).items()
            if v.get("state") == "wired_but_silent"
        ]
        if len(silent) >= 2:
            logger.warning(
                "[R-F305] %d DD layers wired-but-silent on run %s: %s",
                len(silent), report.run_id, silent,
            )
    except Exception as _eco_e:
        logger.debug("R-F305 ecosystem_status emit failed: %s", _eco_e)

    logger.info(
        "[dd_orchestrator] run %s complete — entity=%s risk=%s cost=$%.4f duration=%dms layers=%s",
        report.run_id,
        report.identity.entity_name,
        report.risk_classification,
        report.total_cost_usd,
        report.total_duration_ms,
        ",".join(report.layers_run),
    )
    return report


def _build_ecosystem_status(report) -> dict:
    """R-F305: build a per-layer activity snapshot for ecosystem awareness.

    For each DD section, classify state:
      "active"             — produced real data (findings or structured fields)
      "wired_but_silent"   — ran (status=OK) but returned nothing useful
      "skipped"            — meta.status SKIPPED/DISABLED
      "errored"            — meta.status ERROR
      "not_run"            — never started (no meta.started_at)
    """
    sections_by_name = {
        "identity": getattr(report, "identity", None),
        "network": getattr(report, "network", None),
        "verification": getattr(report, "verification", None),
        "compliance": getattr(report, "compliance", None),
        "digital": getattr(report, "digital", None),
        "commercial_coherence": getattr(report, "commercial_coherence", None),
        "synthesis": getattr(report, "synthesis", None),
    }
    # R-F317 (2026-05-11): synthesis layer label honesty. The chat run on
    # modirumgespi.com (21:11) reported synthesis as "wired-but-silent"
    # even though the BLUF + risk_classification + recommendation were
    # all generated — because synthesis populates report.bottom_line etc.
    # at the top level, not inside report.synthesis. If the report has a
    # bottom_line + risk_classification, synthesis effectively ran and
    # should not be flagged as silent. We patch its findings list here
    # (read-only, before _section_active runs) to surface this.
    try:
        syn = sections_by_name.get("synthesis")
        if syn is not None and not getattr(syn, "findings", None):
            top_bluf = getattr(report, "bottom_line", "") or ""
            top_rec = getattr(report, "recommendation", "") or ""
            # R-F317: ONLY check bottom_line + recommendation (these are
            # empty by default). risk_classification has a non-empty
            # default ("green") so it would trigger active on every empty
            # synthesis — which is wrong.
            if top_bluf.strip() or top_rec.strip():
                # Synthesis did produce output, just stored top-level.
                # Add a synthetic finding for the ecosystem snapshot so it
                # registers as active.
                from .dd_schema import Finding as _F_syn
                _top_risk = getattr(report, "risk_classification", "") or "?"
                try:
                    syn.findings = list(getattr(syn, "findings", []) or []) + [
                        _F_syn(
                            severity="info",
                            title=(
                                f"R-F317: synthesis produced BLUF="
                                f"{_top_risk}: "
                                f"{(top_bluf[:140] or '(empty)')}..."
                            ),
                            detail=(
                                f"Risk classification: {_top_risk}; "
                                f"Recommendation: {(top_rec or '(empty)')[:200]}"
                            ),
                            source="dd_orchestrator.synthesis_passthrough",
                            confidence="ASSESSED",
                        )
                    ]
                except Exception:
                    pass
    except Exception:
        pass
    layers: dict[str, dict] = {}
    for sname, section in sections_by_name.items():
        if section is None:
            layers[sname] = {"state": "not_run", "findings": 0, "data_gaps": 0}
            continue
        meta = getattr(section, "meta", None)
        status = (getattr(meta, "status", "") or "").upper() if meta else ""
        started = getattr(meta, "started_at", "") if meta else ""
        n_findings = len(getattr(section, "findings", []) or [])
        n_gaps = len(getattr(section, "data_gaps", []) or [])

        if not started:
            state = "not_run"
        elif status in ("SKIPPED", "DISABLED"):
            state = "skipped"
        elif status in ("ERROR", "ERRORED"):
            state = "errored"
        else:
            # Re-use the same honesty rule as _section_active (R-F297).
            real_data = False
            for fd in (getattr(section, "findings", []) or []):
                sev = (getattr(fd, "severity", "") or "").lower()
                if sev in ("red", "amber", "hard_stop", "hard-stop", "no-go"):
                    real_data = True
                    break
                detail = getattr(fd, "detail", "") or ""
                if len(detail) >= 40:
                    real_data = True
                    break
                title = getattr(fd, "title", "") or ""
                if len(title) >= 60:
                    real_data = True
                    break
            if not real_data:
                for fname in (
                    "directors", "shareholders", "press_coverage",
                    "officer_links", "psc_chain", "ubo_chain",
                    "sanctions_matches", "country_risk",
                    "regulatory_enforcement", "registration_number",
                    "incorporation_date", "registered_address",
                    "declared_activity",
                ):
                    fv = getattr(section, fname, None)
                    if not fv:
                        continue
                    if isinstance(fv, (list, dict, set, tuple)) and len(fv) == 0:
                        continue
                    real_data = True
                    break
            state = "active" if real_data else "wired_but_silent"

        layers[sname] = {
            "state": state,
            "status": status.lower() if status else "",
            "findings": n_findings,
            "data_gaps": n_gaps,
        }

    # Aggregate health summary
    n_active = sum(1 for v in layers.values() if v["state"] == "active")
    n_silent = sum(1 for v in layers.values() if v["state"] == "wired_but_silent")
    n_skipped = sum(1 for v in layers.values() if v["state"] == "skipped")
    n_error = sum(1 for v in layers.values() if v["state"] == "errored")
    n_not_run = sum(1 for v in layers.values() if v["state"] == "not_run")

    return {
        "layers": layers,
        "summary": {
            "active": n_active,
            "wired_but_silent": n_silent,
            "skipped": n_skipped,
            "errored": n_error,
            "not_run": n_not_run,
            "total": len(layers),
        },
        "health_signal": (
            "DEGRADED" if (n_error or n_silent >= 3)
            else "PARTIAL" if (n_silent >= 1 or n_not_run >= 2)
            else "HEALTHY"
        ),
        "interpretation": (
            "wired_but_silent layers are the 'going backwards' signal: code "
            "ran but produced nothing. Check whether the orchestrator was in "
            "the right mode (deep vs standard) and whether the registry / "
            "search adapters reached their endpoints."
        ),
    }


# =============================================================================
# WATCHLIST (Redis-backed, used by autonomous task)
# =============================================================================

WATCHLIST_KEY = "crucix:dd:watchlist"


@fail_wire(module="dd_orchestrator", gap_type="engine_failure", control_flow_exempt=("ValueError",))
async def add_to_watchlist(target: dict) -> dict:
    """Add a target to the DD watchlist. Target must include at least
    a name. Idempotent — dedupes by name."""
    from . import redis_store as rs
    current = await rs.get_json(WATCHLIST_KEY) or []
    name = (target.get("name") or target.get("entity") or "").strip()
    if not name:
        raise ValueError("target must include a name")
    for w in current:
        if (w.get("name") or "").strip().lower() == name.lower():
            # R-F878 — already present: backfill canonical_entity_id /
            # entity_type / jurisdiction when this DD supplies them and the
            # existing entry lacks them, so the R-F876 re-screen can match by
            # canonical id (not re-baseline from CLEAN). Also refresh the
            # last-DD pointer. Keeps enrollment idempotent BUT enriching.
            _changed = False
            for _k in ("canonical_entity_id", "entity_type", "jurisdiction"):
                if target.get(_k) and not w.get(_k):
                    w[_k] = target[_k]
                    _changed = True
            for _k in ("last_dd_run_id", "last_risk"):
                if target.get(_k) and w.get(_k) != target[_k]:
                    w[_k] = target[_k]
                    _changed = True
            if _changed:
                await rs.set_json(WATCHLIST_KEY, current)
            return {"ok": True, "note": "already on watchlist",
                    "count": len(current), "enriched": _changed}
    current.insert(0, target)
    current = current[:200]
    await rs.set_json(WATCHLIST_KEY, current)
    return {"ok": True, "added": target, "count": len(current)}


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def remove_from_watchlist(name: str) -> dict:
    from . import redis_store as rs
    current = await rs.get_json(WATCHLIST_KEY) or []
    before = len(current)
    current = [w for w in current if (w.get("name") or "").strip().lower() != (name or "").strip().lower()]
    await rs.set_json(WATCHLIST_KEY, current)
    return {"ok": True, "removed": before - len(current), "count": len(current)}


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def get_watchlist() -> list[dict]:
    from . import redis_store as rs
    return await rs.get_json(WATCHLIST_KEY) or []


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def get_report(run_id: str) -> dict | None:
    from . import redis_store as rs
    return await rs.get_json(REPORT_REDIS_KEY.format(run_id=run_id))


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def get_case_file(
    canonical_entity_id: str,
    *,
    include_reports: bool = False,
) -> dict:
    """R-F573 — return the full version chain for one DD case file.

    Reads the report index from Redis, merges with the R-F575 SQLite
    cold-tier archive (so entries older than the 7-day Redis TTL still
    appear), filters by canonical_entity_id, sorts newest-first.

    When include_reports=True also fetches each run's body from Redis
    — costlier, used by the dashboard's "case file detail" view.
    Archived-only runs that no longer have a body in Redis return
    `body_archived=true` instead.
    """
    from . import redis_store as rs
    from . import dd_versioning as _ver
    from . import dd_case_archive

    # 1. Hot tier (Redis) — current 7-day index
    index = await rs.get_json(REPORT_INDEX_KEY) or []
    redis_versions = _ver.filter_index_by_canonical_id(canonical_entity_id, index)
    # 2. Cold tier (SQLite) — up to 90 days
    archived_versions = dd_case_archive.lookup_by_canonical_id(canonical_entity_id)
    # 3. Merge — dedupe by run_id, prefer Redis entry (newer metadata)
    seen_run_ids: set[str] = set()
    merged: list[dict] = []
    for v in redis_versions:
        rid = v.get("run_id")
        if rid and rid not in seen_run_ids:
            seen_run_ids.add(rid)
            merged.append({**v, "_source_tier": "redis"})
    for v in archived_versions:
        rid = v.get("run_id")
        if rid and rid not in seen_run_ids:
            seen_run_ids.add(rid)
            merged.append({**v, "_source_tier": "sqlite_archive"})
    # 4. Sort newest-first across the merged set
    merged.sort(key=lambda e: e.get("generated_at") or "", reverse=True)

    out: dict = {
        "canonical_entity_id": canonical_entity_id,
        "total_versions": len(merged),
        "redis_versions": len(redis_versions),
        "archived_versions": len(archived_versions),
        "versions": [
            {
                "run_id": v.get("run_id"),
                "version_number": v.get("version_number"),
                "generated_at": v.get("generated_at"),
                "risk_classification": v.get("risk_classification"),
                "previous_run_id": v.get("previous_run_id"),
                "entity_name": v.get("entity_name"),
                "jurisdiction": v.get("jurisdiction"),
                "source_tier": v.get("_source_tier"),
            }
            for v in merged
        ],
    }
    if include_reports:
        bodies: list[dict] = []
        for v in merged:
            rid = v.get("run_id")
            if not rid:
                continue
            body = await rs.get_json(REPORT_REDIS_KEY.format(run_id=rid))
            if body:
                bodies.append(body)
            else:
                bodies.append({
                    "run_id": rid,
                    "body_archived": True,
                    "note": (
                        "Report body aged out of Redis 7-day TTL; index "
                        "entry preserved in R-F575 SQLite cold tier. "
                        "Re-run DD on this entity to regenerate a fresh body."
                    ),
                })
        out["reports"] = bodies
    return out


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def list_reports(
    limit: int = 50,
    *,
    user_id: str | None = None,
    user_email_domain: str | None = None,
) -> list[dict]:
    """Return the report index, opportunistically repairing entries whose
    stored entity_name fails the current validator. R-F162 (2026-05-11)
    extends R-F153: when an index entry has a bad name (e.g. the live
    2026-05-10 12:39 'this company, which has nothing to do' case), try
    to derive a usable name from the stored target_input.website / url
    on the full report blob. If repair succeeds the index entry AND the
    report blob's identity.entity_name are both updated in place.

    R-F607 (2026-05-16) — per-user scoping. When ``user_id`` is passed,
    only entries whose stored ``user_id`` matches are returned. Pre-R-F607
    entries (user_id=None) are intentionally hidden from user-filtered
    lists — only the admin / no-filter branch (user_id=None) sees them.

    R-F608 (2026-05-16) — company-shared visibility. When
    ``user_email_domain`` is ALSO passed alongside user_id, entries are
    returned if EITHER the entry's user_id matches OR the entry's
    user_email_domain matches AND the entry hasn't opted out of company
    sharing. Domain-only matching means colleagues @arkmurus.com see
    each other's runs without the requester having to know specific
    user_ids.
    """
    from . import redis_store as rs
    index = await rs.get_json(REPORT_INDEX_KEY) or []
    if not index:
        return []

    _changed = False
    for i, entry in enumerate(index[:limit]):
        if not isinstance(entry, dict):
            continue
        name = (entry.get("entity_name") or "").strip()
        if not name:
            continue
        _ok, _ = _validate_entity_name(name)
        if _ok:
            continue
        # Bad name in index — try to repair from the full report blob.
        run_id = entry.get("run_id")
        if not run_id:
            continue
        try:
            blob = await rs.get_json(REPORT_REDIS_KEY.format(run_id=run_id))
        except Exception:
            blob = None
        if not isinstance(blob, dict):
            continue
        # Probe candidates: ARKDDReport already stores the raw trigger
        # input as `target`, so we read website / url / domain from there
        # first. Fall back to the legacy `target_input` key (some older
        # entries may not have target).
        cand_sources = []
        for src_key in ("target", "target_input"):
            ti = blob.get(src_key) if isinstance(blob.get(src_key), dict) else None
            if not ti:
                continue
            for k in ("website", "url", "domain"):
                v = (ti.get(k) or "").strip() if ti else ""
                if v:
                    cand_sources.append(v)
        # Fallback: scan identity.findings for any URL-shaped string.
        ident = blob.get("identity") or {}
        for fld in ("website", "url", "domain"):
            v = (ident.get(fld) or "").strip() if isinstance(ident, dict) else ""
            if v:
                cand_sources.append(v)

        new_name: str | None = None
        for raw in cand_sources:
            try:
                from urllib.parse import urlparse as _urlparse
                seed = raw if "://" in raw else f"https://{raw}"
                host = (_urlparse(seed).hostname or "").lower().strip(".")
                if host.startswith("www."):
                    host = host[4:]
                if not host:
                    continue
                ok2, _ = _validate_entity_name(host)
                if ok2:
                    new_name = host
                    break
            except Exception:
                continue

        if new_name:
            logger.info(
                "[dd_orchestrator] R-F162 retroactive rename: %s '%s' -> '%s'",
                run_id, name, new_name,
            )
            entry["entity_name"] = new_name
            entry["_original_name_rejected"] = name
            entry["_name_derivation"] = "list_reports_repair_R-F162"
            index[i] = entry
            _changed = True
            # Mirror into the stored report blob so /dd/report/{run_id}
            # also reads the corrected name.
            try:
                if isinstance(blob.get("identity"), dict):
                    blob["identity"]["entity_name"] = new_name
                    blob["identity"]["_original_name_rejected"] = name
                await rs.set_json(
                    REPORT_REDIS_KEY.format(run_id=run_id),
                    blob,
                    ex=REPORT_TTL_SECONDS,
                )
            except Exception as e:
                logger.debug("R-F162 blob mirror write failed: %s", e)

    # R-F575 (2026-05-16) — opportunistic canonical_entity_id backfill
    # for pre-R-F573 index entries. We compute the key from the stored
    # entity_name + jurisdiction + entity_type and write it back so the
    # /api/aria/dd/case/{canonical_entity_id} chain self-heals on read.
    # Cheap (in-memory only) when most entries are already populated.
    try:
        from . import dd_versioning as _ver
        from . import dd_case_archive as _arc
    except Exception:
        _ver = None
        _arc = None
    if _ver is not None:
        for i, entry in enumerate(index[:limit]):
            if not isinstance(entry, dict):
                continue
            if entry.get("canonical_entity_id"):
                continue
            ent_name = (entry.get("entity_name") or "").strip()
            if not ent_name:
                continue
            # Pull entity_type + jurisdiction_iso2 + registration_number
            # from the full report body — the index entry doesn't carry
            # those columns historically.
            run_id = entry.get("run_id")
            try:
                blob = (await rs.get_json(REPORT_REDIS_KEY.format(run_id=run_id))) or {}
            except Exception:
                blob = {}
            ident = (blob.get("identity") or {}) if isinstance(blob, dict) else {}
            cid = _ver.canonical_entity_id(
                entity_type=ident.get("entity_type") or "company",
                name=ent_name,
                jurisdiction_iso2=(ident.get("jurisdiction_iso2") or "").strip() or None,
                registration_number=ident.get("registration_number") or None,
                imo=ident.get("imo_number") or None,
                dob=ident.get("date_of_birth") or None,
                nationality_iso2=ident.get("nationality_iso2") or None,
            )
            if not cid:
                continue
            entry["canonical_entity_id"] = cid
            entry["_backfilled_by"] = "R-F575_list_reports_backfill"
            # Also write the body so /dd/report/{run_id} sees the field.
            if isinstance(blob, dict):
                blob.setdefault("canonical_entity_id", cid)
                try:
                    await rs.set_json(
                        REPORT_REDIS_KEY.format(run_id=run_id), blob,
                        ex=REPORT_TTL_SECONDS,
                    )
                except Exception:
                    pass
            index[i] = entry
            _changed = True
            # Mirror to cold tier so old entries also enter the case-file index
            if _arc is not None:
                try:
                    _arc.archive_entry(entry)
                except Exception:
                    pass

    if _changed:
        try:
            await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
        except Exception as e:
            logger.debug("R-F162 index repair write failed: %s", e)

    # R-F607 / R-F608 — apply user-scoped filter AFTER repair/backfill so
    # admin-side rewrites still happen unconditionally. The filter only
    # narrows what we hand back to the requester; the persisted index
    # always contains the full set so admin / autonomous paths can still
    # see everything.
    if user_id is not None or user_email_domain is not None:
        filtered: list[dict] = []
        _norm_domain = (user_email_domain or "").strip().lower() or None
        for entry in index:
            if not isinstance(entry, dict):
                continue
            entry_user = entry.get("user_id") or None
            entry_domain = (entry.get("user_email_domain") or "").strip().lower() or None
            # Owner match (R-F607)
            if user_id and entry_user and entry_user == user_id:
                filtered.append(entry)
                continue
            # Same-company match (R-F608) — respect explicit opt-out by
            # honouring share_to_company=False when present. Default
            # (missing key) is shared, matching the operator requirement
            # that DD is company-visible by default for users on the
            # same email domain.
            if (
                _norm_domain
                and entry_domain
                and entry_domain == _norm_domain
                and entry.get("share_to_company", True) is not False
            ):
                filtered.append(entry)
                continue
        return filtered[:limit]

    return index[:limit]


# =============================================================================
# R-F575 — CASE-FILE SPLIT / MERGE OPERATOR OVERRIDES
# =============================================================================

@fail_wire(module="dd_orchestrator", gap_type="engine_failure", control_flow_exempt=("ValueError",))
async def split_case(
    canonical_entity_id: str,
    run_ids_to_extract: list[str],
    *,
    new_canonical_id_suffix: str | None = None,
) -> dict:
    """Extract a subset of runs from one canonical_entity_id into a new
    sibling case file.

    Use when the auto-canonicalisation wrongly collapsed two different
    entities (e.g. two distinct John Smiths land on
    `person:john_smith:GB:????`).

    Args:
      canonical_entity_id: the existing case file containing the runs.
      run_ids_to_extract: which runs to peel off into a new case file.
      new_canonical_id_suffix: optional override; if None we append
          ":split:<timestamp>" to keep the new key human-readable.

    Returns:
      {moved: int, from: str, to: str, run_ids: [str, ...]}
    """
    from . import redis_store as rs
    from . import dd_case_archive

    if not canonical_entity_id or not run_ids_to_extract:
        raise ValueError("canonical_entity_id and run_ids_to_extract are required")

    suffix = new_canonical_id_suffix or f"split:{int(datetime.now(timezone.utc).timestamp())}"
    new_canonical = f"{canonical_entity_id}:{suffix}"

    index = await rs.get_json(REPORT_INDEX_KEY) or []
    moved_run_ids: list[str] = []
    for i, entry in enumerate(index):
        if not isinstance(entry, dict):
            continue
        if entry.get("canonical_entity_id") != canonical_entity_id:
            continue
        if entry.get("run_id") not in run_ids_to_extract:
            continue
        entry["canonical_entity_id"] = new_canonical
        entry["_split_from"] = canonical_entity_id
        entry["_split_at"] = datetime.now(timezone.utc).isoformat()
        index[i] = entry
        moved_run_ids.append(entry["run_id"])
        # Mirror to cold tier
        try:
            dd_case_archive.update_canonical_id(entry["run_id"], new_canonical)
        except Exception as e:
            logger.debug("split_case cold-tier update failed: %s", e)
        # Mirror to report body
        try:
            blob = await rs.get_json(REPORT_REDIS_KEY.format(run_id=entry["run_id"]))
            if isinstance(blob, dict):
                blob["canonical_entity_id"] = new_canonical
                blob["_split_from"] = canonical_entity_id
                await rs.set_json(
                    REPORT_REDIS_KEY.format(run_id=entry["run_id"]),
                    blob,
                    ex=REPORT_TTL_SECONDS,
                )
        except Exception as e:
            logger.debug("split_case body mirror failed: %s", e)

    if not moved_run_ids:
        return {
            "moved": 0,
            "from": canonical_entity_id,
            "to": new_canonical,
            "run_ids": [],
            "warning": "no matching runs found",
        }

    try:
        await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
    except Exception as e:
        logger.debug("split_case index write failed: %s", e)

    return {
        "moved": len(moved_run_ids),
        "from": canonical_entity_id,
        "to": new_canonical,
        "run_ids": moved_run_ids,
    }


@fail_wire(module="dd_orchestrator", gap_type="engine_failure", control_flow_exempt=("ValueError",))
async def merge_cases(
    from_canonical_id: str,
    into_canonical_id: str,
) -> dict:
    """Reparent every run currently under from_canonical_id into
    into_canonical_id.

    Use when the auto-canonicalisation wrongly forked the same entity
    across spellings/jurisdictions/registration numbers.
    """
    from . import redis_store as rs
    from . import dd_case_archive

    if not from_canonical_id or not into_canonical_id:
        raise ValueError("from_canonical_id and into_canonical_id are required")
    if from_canonical_id == into_canonical_id:
        return {"moved": 0, "from": from_canonical_id, "into": into_canonical_id,
                "warning": "same canonical id — no-op"}

    index = await rs.get_json(REPORT_INDEX_KEY) or []
    moved_run_ids: list[str] = []
    for i, entry in enumerate(index):
        if not isinstance(entry, dict):
            continue
        if entry.get("canonical_entity_id") != from_canonical_id:
            continue
        entry["canonical_entity_id"] = into_canonical_id
        entry["_merged_from"] = from_canonical_id
        entry["_merged_at"] = datetime.now(timezone.utc).isoformat()
        index[i] = entry
        moved_run_ids.append(entry["run_id"])
        try:
            dd_case_archive.update_canonical_id(entry["run_id"], into_canonical_id)
        except Exception as e:
            logger.debug("merge_cases cold-tier update failed: %s", e)
        try:
            blob = await rs.get_json(REPORT_REDIS_KEY.format(run_id=entry["run_id"]))
            if isinstance(blob, dict):
                blob["canonical_entity_id"] = into_canonical_id
                blob["_merged_from"] = from_canonical_id
                await rs.set_json(
                    REPORT_REDIS_KEY.format(run_id=entry["run_id"]),
                    blob,
                    ex=REPORT_TTL_SECONDS,
                )
        except Exception as e:
            logger.debug("merge_cases body mirror failed: %s", e)

    if not moved_run_ids:
        return {
            "moved": 0,
            "from": from_canonical_id,
            "into": into_canonical_id,
            "warning": "no matching runs found",
        }

    try:
        await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
    except Exception as e:
        logger.debug("merge_cases index write failed: %s", e)

    return {
        "moved": len(moved_run_ids),
        "from": from_canonical_id,
        "into": into_canonical_id,
        "run_ids": moved_run_ids,
    }


@fail_wire(module="dd_orchestrator", gap_type="engine_failure", control_flow_exempt=("ValueError",))
async def delete_report(run_id: str) -> dict:
    """Remove a single DD report + its index entry. R-F162 (2026-05-11) —
    the prior fix for the 'this company, which has nothing to do' case
    relied on an operator-deletable surface that didn't exist. Operators
    can now drop bad reports from the library directly."""
    from . import redis_store as rs
    if not run_id or not isinstance(run_id, str):
        raise ValueError("run_id required")
    blob_existed = False
    try:
        blob_existed = await rs.delete(REPORT_REDIS_KEY.format(run_id=run_id))
    except Exception as e:
        logger.warning("delete_report blob delete failed for %s: %s", run_id, e)
    removed_from_index = 0
    try:
        index = await rs.get_json(REPORT_INDEX_KEY) or []
        before = len(index)
        index = [e for e in index if not (isinstance(e, dict) and e.get("run_id") == run_id)]
        removed_from_index = before - len(index)
        if removed_from_index:
            await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
    except Exception as e:
        logger.warning("delete_report index write failed for %s: %s", run_id, e)
    logger.info(
        "[dd_orchestrator] R-F162 delete_report %s: blob=%s index_entries=%d",
        run_id, blob_existed, removed_from_index,
    )
    return {
        "ok": True,
        "run_id": run_id,
        "blob_deleted": bool(blob_existed),
        "index_entries_removed": removed_from_index,
    }


# =============================================================================
# WATCHLIST AUTO-RE-SCREEN
# =============================================================================

WATCHLIST_ALERTS_KEY = "crucix:aria:dd:watchlist:alerts"
_RESCREEN_MAX_ENTITIES = 50
_RESCREEN_ALERT_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _derive_status(classified: dict) -> str:
    """Map classify_matches worst_severity to a simple tri-state."""
    sev = classified.get("worst_severity", "clean")
    if sev in ("hard_stop", "red"):
        return "HIT"
    if sev in ("amber",):
        return "PEP"
    return "CLEAN"


def _derive_status_from_findings(findings: list[dict]) -> str:
    """Derive status from a report's identity findings list."""
    for f in findings:
        sev = f.get("severity", "")
        src = f.get("source", "")
        if "sanctions" not in src and "person_screen" not in src:
            continue
        if sev in ("hard_stop", "red"):
            return "HIT"
        if sev == "amber":
            return "PEP"
    return "CLEAN"


def _derive_score_from_matches(matches: list[dict]) -> float:
    """Best match score from a sanctions screen result."""
    if not matches:
        return 0.0
    return max((m.get("score", 0) for m in matches if isinstance(m, dict)), default=0.0)


def _buyer_matches_entity(buyer: str, entity_name: str) -> bool:
    """Loose match: deals store buyer as a free-text string. We match on
    case-insensitive substring in either direction, with a min token length
    of 4 to avoid matching on short common words."""
    if not buyer or not entity_name:
        return False
    b = buyer.strip().lower()
    e = entity_name.strip().lower()
    if len(e) < 4 or len(b) < 4:
        return False
    return e in b or b in e


async def _fan_out_alert_to_deals(alert: dict) -> list[dict]:
    """When a watchlist entity worsens, find every open deal whose buyer
    matches and tag it. Returns list of {id, buyer, country, stage} impacted.
    Silent on errors — never block the parent rescreen cycle."""
    try:
        from . import deal_pipeline
    except Exception as e:
        logger.debug("deal_pipeline unavailable for fan-out: %s", e)
        return []

    entity = alert.get("entity", "")
    change_type = alert.get("change_type", "")
    try:
        open_deals = await deal_pipeline.get_pipeline(include_closed=False)
    except Exception as e:
        logger.warning("[watchlist fan-out] pipeline read failed: %s", e)
        return []

    impacted: list[dict] = []
    tag = f"sanctions_alert_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    note = f"Counterparty '{entity}' flagged ({change_type}) by watchlist re-screen."

    for deal in open_deals:
        if not _buyer_matches_entity(deal.get("buyer", ""), entity):
            continue
        deal_id = deal.get("id")
        if not deal_id:
            continue
        try:
            await deal_pipeline.update_lead(
                deal_id,
                tags=[tag, "FLAGGED_FOR_RESCREEN"],
                notes=note,
            )
            impacted.append({
                "id": deal_id,
                "buyer": deal.get("buyer", ""),
                "country": deal.get("country", ""),
                "stage": deal.get("stage", ""),
            })
        except Exception as e:
            logger.warning("[watchlist fan-out] update %s failed: %s", deal_id, e)

    if impacted:
        logger.warning(
            "[watchlist fan-out] %s (%s) impacts %d open deal(s)",
            entity, change_type, len(impacted),
        )
        # ── Brain hook: ARIA needs to know that a worsening sanctions event
        # has just rippled into the active pipeline. Without this she'd see
        # the rescreen alert and the deal tag separately and never connect
        # them. Rule Zero: she sees everything.
        try:
            from . import brain_hook
            deal_summary = ", ".join(f"{d['id']} ({d.get('buyer','')[:30]})" for d in impacted[:5])
            await brain_hook.absorb(
                module="dd_orchestrator",
                summary=(
                    f"Sanctions worsening on '{entity}' ({change_type}) impacted "
                    f"{len(impacted)} open deal(s): {deal_summary}"
                ),
                entity_name=entity,
                success=False,  # worsening sanctions = adverse event for the pipeline
                confidence="CONFIRMED",
                gap_type="counterparty_risk_materialised",
                gap_detail=f"Deals now flagged FOR_RESCREEN: {[d['id'] for d in impacted]}",
            )
        except Exception as e:
            logger.debug("fan-out brain_hook absorb failed (non-fatal): %s", e)

        # ── Audit log: a watchlist worsening that touches a deal is a
        # compliance-grade event. Record per deal so each deal's compliance
        # file picks it up cleanly. ──
        try:
            from . import audit_log
            for d in impacted:
                await audit_log.record(
                    action="watchlist_alert",
                    actor="dd_orchestrator._fan_out_alert_to_deals",
                    entity_name=entity,
                    deal_id=d["id"],
                    inputs={"change_type": change_type, "alert_ts": alert.get("timestamp")},
                    outputs={
                        "deal_tagged": True,
                        "deal_buyer": d.get("buyer"),
                        "deal_stage": d.get("stage"),
                    },
                    decision=f"DEAL_FLAGGED_FOR_RESCREEN ({change_type})",
                    confidence="CONFIRMED",
                    notes=f"Sanctions worsening on '{entity}' triggered rescreen flag on deal {d['id']}",
                )
        except Exception as e:
            logger.debug("fan-out audit record failed (non-fatal): %s", e)
    return impacted


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def rescreen_watchlist(llm=None) -> dict:
    """Re-screen every watchlist entity (sanctions + PEP only, no LLM).

    Returns summary dict with entities_screened, changes_detected, errors,
    and duration_ms. Alerts are persisted in Redis for later retrieval.
    """
    import json as _json
    t0 = time.monotonic()
    from . import redis_store as rs

    watchlist = await rs.get_json(WATCHLIST_KEY) or []
    if not watchlist:
        return {"entities_screened": 0, "changes_detected": [], "errors": [],
                "duration_ms": 0}

    # F57 fix 2026-04-28: opportunistic purge of polluted entries before
    # spending cycle budget on them. Past auto-escalations from before
    # the validator was wired added search-query strings to the watchlist
    # (e.g. "SAM.gov defence military security procurement global last 7
    # days 2026"). Each daily re-screen wasted budget logging "rejecting
    # non-entity input" for them. Strip those entries here so the
    # watchlist self-cleans over the next few cycles.
    try:
        from . import sanctions as _sanc
        if hasattr(_sanc, "_looks_like_entity_name"):
            before = len(watchlist)
            watchlist = [
                w for w in watchlist
                if _sanc._looks_like_entity_name((w.get("name") or w.get("entity") or "").strip())
            ]
            removed = before - len(watchlist)
            if removed:
                await rs.set_json(WATCHLIST_KEY, watchlist)
                logger.info(
                    "[watchlist purge] removed %d polluted entries (search "
                    "queries / sentence fragments) before re-screen",
                    removed,
                )
    except Exception as e:
        logger.debug("[watchlist purge] non-fatal: %s", e)

    # Enforce cost cap: max 50 entities per cycle
    entities = watchlist[:_RESCREEN_MAX_ENTITIES]

    changes: list[dict] = []
    errors: list[dict] = []

    # Import sanctions module and classifier once
    try:
        from . import sanctions
        from ._sanctions_classify import classify_matches
    except Exception as e:
        return {"entities_screened": 0, "changes_detected": [], "errors": [
            {"entity": "*", "error": f"sanctions module import failed: {e}"}],
            "duration_ms": int((time.monotonic() - t0) * 1000)}

    # R-F798 (2026-05-22) — per-entity wall-time ceiling. Live evidence
    # 2026-05-22 16:03:49 UTC: [Watchlist] Re-screen: 1 entities, 1
    # changes, 0 errors, 1,197,936ms (19m 58s). A single wedged
    # sanctions screen burned ~20 min while other entities sat behind
    # it. Cap each entity at _RESCREEN_PER_ENTITY_TIMEOUT_S (default
    # 60s, env-tunable). On timeout the entity is recorded as an
    # error and the loop moves on, preserving the other entities'
    # chance to run within the cycle.
    import asyncio as _aio_rf798
    _per_entity_timeout = float(
        os.environ.get("ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S", "60.0")
    )

    for entry in entities:
        name = (entry.get("name") or entry.get("entity") or "").strip()
        if not name:
            continue
        try:
            # --- Run quick sanctions screen (no LLM, no deep research) ---
            # R-F798: wrap the screen + downstream Redis reads in a
            # single per-entity timeout. If anything wedges, the
            # entity is recorded as an error and the loop continues.
            async def _screen_one_entity():
                if hasattr(sanctions, "screen_with_aliases"):
                    return await sanctions.screen_with_aliases(name)
                if hasattr(sanctions, "fuzzy_screen"):
                    return await sanctions.fuzzy_screen(name)
                return None

            if _per_entity_timeout > 0:
                try:
                    screen = await _aio_rf798.wait_for(
                        _screen_one_entity(), timeout=_per_entity_timeout,
                    )
                except _aio_rf798.TimeoutError:
                    errors.append({
                        "entity": name,
                        "error": (
                            f"sanctions screen timeout (>"
                            f"{_per_entity_timeout:.1f}s) — R-F798 cap"
                        ),
                    })
                    continue
            else:
                screen = await _screen_one_entity()

            if screen is None:
                errors.append({"entity": name, "error": "no sanctions entrypoint"})
                continue

            matches = screen.get("matches") or []
            classified = classify_matches(matches, query_name=name)
            new_status = _derive_status(classified)
            new_score = _derive_score_from_matches(matches)

            # --- Load previous status from the most recent DD report ---
            old_status = "CLEAN"
            old_score = 0.0
            old_run_id = None

            # R-F876 (2026-05-25) — match the prior report by CANONICAL ENTITY
            # ID, not raw entity_name. The old `entity_name.lower() ==` match
            # missed cosmetic variants ("Embraer SA" vs "Embraer S.A.") → no
            # prior report found → baseline fell back to CLEAN/0.0 → a spurious
            # score_change alert fired (the live "CLEAN→CLEAN 0.00→0.833" the
            # operator saw in the logs). Prefer canonical-id equality; fall
            # back to a normalised-name match (suffix/punctuation-insensitive)
            # so legacy index entries without a canonical_entity_id still
            # resolve to the right prior report.
            from . import dd_versioning as _ver
            index = await rs.get_json(REPORT_INDEX_KEY) or []
            _wl_cid = (entry.get("canonical_entity_id") or "").strip() or _ver.canonical_entity_id(
                entity_type=entry.get("entity_type"),
                name=name,
                jurisdiction_iso2=entry.get("jurisdiction") or entry.get("jurisdiction_iso2"),
                registration_number=entry.get("registration_number"),
            )
            _wl_norm = _ver.normalize_name(name)
            _name_match_run_id = None
            for idx_entry in index:
                _idx_cid = (idx_entry.get("canonical_entity_id") or "").strip()
                if _wl_cid and _idx_cid and _idx_cid == _wl_cid:
                    old_run_id = idx_entry.get("run_id")   # canonical-id hit (best)
                    break
                if _name_match_run_id is None and _ver.normalize_name(
                    idx_entry.get("entity_name") or ""
                ) == _wl_norm:
                    _name_match_run_id = idx_entry.get("run_id")
            else:
                # No canonical-id hit → use the normalised-name fallback (which
                # collapses "Embraer SA"/"Embraer S.A." to one key).
                old_run_id = _name_match_run_id

            if old_run_id:
                prev_report = await rs.get_json(REPORT_REDIS_KEY.format(run_id=old_run_id))
                if prev_report:
                    identity = prev_report.get("identity") or {}
                    prev_findings = identity.get("findings") or []
                    old_status = _derive_status_from_findings(prev_findings)
                    prev_screen = identity.get("sanctions_screen") or {}
                    old_score = _derive_score_from_matches(prev_screen.get("matches") or [])

            # --- Compare ---
            change_type = None
            detail = ""

            if old_status == "CLEAN" and new_status == "HIT":
                change_type = "new_hit"
                detail = f"Previously clean, now sanctioned. Top match: {classified.get('summary', '')[:200]}"
            elif old_status == "HIT" and new_status == "CLEAN":
                change_type = "removed"
                detail = "Previously sanctioned, now clean across all lists."
            elif old_status != "PEP" and new_status == "PEP":
                change_type = "new_pep"
                detail = f"New PEP/adverse-media match. {classified.get('summary', '')[:200]}"
            elif abs(new_score - old_score) > 0.1:
                change_type = "score_change"
                detail = f"Best match score changed from {old_score:.2f} to {new_score:.2f}."

            if change_type:
                alert = {
                    "entity": name,
                    "run_id": old_run_id or "none",
                    "change_type": change_type,
                    "old_status": old_status,
                    "new_status": new_status,
                    "old_score": round(old_score, 3),
                    "new_score": round(new_score, 3),
                    "detail": detail,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                # Fan out to linked deals on worsening changes only — don't
                # tag deals when a counterparty is removed from a list or
                # a match score drops. "score_change" with a drop is
                # informational; with a rise it's a risk signal.
                worsening = (
                    change_type in ("new_hit", "new_pep")
                    or (change_type == "score_change" and new_score > old_score)
                )
                if worsening:
                    impacted = await _fan_out_alert_to_deals(alert)
                    if impacted:
                        alert["impacted_deals"] = impacted

                changes.append(alert)

                # Persist alert in Redis
                await rs.lpush(WATCHLIST_ALERTS_KEY, _json.dumps(alert, default=str))
                await rs.ltrim(WATCHLIST_ALERTS_KEY, 0, 499)  # cap at 500
                await rs.expire(WATCHLIST_ALERTS_KEY, _RESCREEN_ALERT_TTL_SECONDS)

        except Exception as e:
            errors.append({"entity": name, "error": str(e)})

    duration_ms = int((time.monotonic() - t0) * 1000)

    # ── Brain hook: feed every cycle into learning so ARIA tracks the rate
    # of change across her watchlist over time. She should know how often
    # her counterparties churn on/off sanctions lists, not just react to
    # individual alerts. Rule Zero: she sees the whole pattern.
    try:
        from . import brain_hook
        change_breakdown = {}
        for c in changes:
            ct = c.get("change_type", "unknown")
            change_breakdown[ct] = change_breakdown.get(ct, 0) + 1
        worsening_count = sum(
            1 for c in changes
            if c.get("change_type") in ("new_hit", "new_pep")
            or (c.get("change_type") == "score_change" and c.get("new_score", 0) > c.get("old_score", 0))
        )
        await brain_hook.absorb(
            module="dd_orchestrator",
            summary=(
                f"Watchlist re-screen: {len(entities)} entities scanned, "
                f"{len(changes)} changes detected ({worsening_count} worsening), "
                f"{len(errors)} errors. Breakdown: {change_breakdown or 'no changes'}"
            ),
            success=(len(errors) == 0),
            confidence="CONFIRMED",
            gap_type="rescreen_errors" if errors else None,
            gap_detail=f"Errors on entities: {[e.get('entity') for e in errors[:5]]}" if errors else None,
        )
    except Exception as e:
        logger.debug("rescreen brain_hook absorb failed (non-fatal): %s", e)

    return {
        "entities_screened": len(entities),
        "changes_detected": changes,
        "errors": errors,
        "duration_ms": duration_ms,
    }


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def get_watchlist_alerts(since_hours: int = 24, user_id: str = "") -> list[dict]:
    """Retrieve recent watchlist re-screen alerts from Redis.

    R-F51: when user_id is supplied, every alert carries an additional
    `read` boolean derived from the per-user "read-until" timestamp
    persisted at crucix:aria:watchlist:read_until:<userId>. Alerts older
    than the read_until timestamp are marked read=True; newer ones
    read=False. This lets the FE compute an unread-count badge without
    a separate roundtrip.
    """
    import json as _json
    from datetime import datetime as _dt
    from . import redis_store as rs

    raw_list = await rs.lrange(WATCHLIST_ALERTS_KEY, 0, 499)
    if not raw_list:
        return []

    # Read-until timestamp per user (best-effort; no error if Redis
    # is unavailable — alerts come back marked unread, which is the
    # conservative default).
    read_until_ts = 0.0
    if user_id:
        try:
            ru = await rs.get(f"crucix:aria:watchlist:read_until:{user_id}")
            if ru:
                read_until_ts = float(ru)
        except Exception:
            pass

    cutoff = _dt.now(timezone.utc).timestamp() - (since_hours * 3600)
    alerts: list[dict] = []
    for raw in raw_list:
        try:
            alert = _json.loads(raw) if isinstance(raw, str) else raw
            ts_str = alert.get("timestamp", "")
            ts = 0.0
            if ts_str:
                ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    continue
            if user_id:
                alert = {**alert, "read": (ts > 0 and ts <= read_until_ts)}
            alerts.append(alert)
        except Exception:
            continue
    return alerts


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def mark_watchlist_alerts_read(user_id: str) -> dict:
    """R-F51: mark all currently visible alerts as read for this user
    by stamping the per-user read-until timestamp. Idempotent — calling
    twice in a row is harmless.
    """
    if not user_id:
        return {"ok": False, "reason": "user_id required"}
    from . import redis_store as rs
    ts = datetime.now(timezone.utc).timestamp()
    try:
        await rs.set(f"crucix:aria:watchlist:read_until:{user_id}", str(ts))
        # 90 days TTL — old read-until stamps are pruned naturally.
        await rs.expire(f"crucix:aria:watchlist:read_until:{user_id}", 90 * 86400)
    except Exception as e:
        return {"ok": False, "reason": f"redis error: {e}"}
    return {"ok": True, "read_until": ts}


@fail_wire(module="dd_orchestrator", gap_type="engine_failure")
async def get_watchlist_unread_count(user_id: str, since_hours: int = 168) -> int:
    """R-F51: light-weight unread badge probe. Counts alerts in the last
    `since_hours` (default 7d) that arrived after the per-user read-until
    timestamp. Skips the JSON re-shape get_watchlist_alerts performs.
    """
    if not user_id:
        return 0
    alerts = await get_watchlist_alerts(since_hours=since_hours, user_id=user_id)
    return sum(1 for a in alerts if not a.get("read", False))
