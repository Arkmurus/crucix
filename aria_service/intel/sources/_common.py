"""Shared helpers for DD source adapters.

Each adapter uses these so we get consistent shape, timing, error
handling, and similarity scoring without rewriting the boilerplate.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.sources._common")


def now_iso() -> str:
    """ISO-8601 UTC timestamp — every adapter stamps its result."""
    return datetime.now(timezone.utc).isoformat()


def empty_result(source: str, query: dict, auth: str = "none", citation_url: str = "") -> dict:
    """Return the canonical empty-but-successful shape. Callers populate
    hits/hit_count as they go."""
    return {
        "source": source,
        "ok": True,
        "query": query,
        "hits": [],
        "hit_count": 0,
        "query_time_ms": 0,
        "fetched_at": now_iso(),
        "auth": auth,
        "error": None,
        "citation_url": citation_url,
    }


def error_result(
    source: str,
    query: dict,
    error: str,
    auth: str = "none",
    citation_url: str = "",
    started_at: float | None = None,
) -> dict:
    """Canonical error shape. `ok=False` signals the fetch failed; the
    orchestrator surfaces this as a data_gap, not a clean screen."""
    return {
        "source": source,
        "ok": False,
        "query": query,
        "hits": [],
        "hit_count": 0,
        "query_time_ms": int((time.time() - started_at) * 1000) if started_at else 0,
        "fetched_at": now_iso(),
        "auth": auth,
        "error": (error or "")[:300],
        "citation_url": citation_url,
    }


# ── R-F3101 — EVERY ADAPTER MUST BE ABLE TO EXPRESS FAILURE ─────────────────
#
# THE ROOT, found while scoping evidence governance (2026-07-26). The gap
# assessment's item 3 is "adapter enforcement": no source activity without a
# recorded outcome. It cannot be built on the adapters as they are — an AST scan of
# `sources/` found FIVE with public entry points returning dicts that carry NO
# outcome field at all:
#
#   court_records.search_all      -> {entity, hits, us_count, uk_count, sources}
#   eccn_lookup.lookup_by_eccn    -> {version, categories}
#   cert_transparency.detect_*    -> {score, signals}
#   gleif.build_profile / lookup  -> (no ok)
#   ais_gap_detector.detect_gaps  -> (no ok)
#
# `EvidenceRecord.retrieval_outcome` is a REQUIRED field. An adapter that cannot say
# whether it succeeded has no honest value to put there, so wiring it to the
# evidence contract would mean inventing one — which is the failure mode the whole
# contract exists to prevent.
#
# And this is not only architectural. It is already a live false clean: an adapter
# that returns `{"hits": []}` on a fetch FAILURE is read downstream as "we looked and
# found nothing" (see R-F3102 for the court-records case, proven).
#
# `outcome` is deliberately a SEPARATE vocabulary from `ok`. `ok=False` says "this
# failed"; it does not distinguish "the source answered, with nothing" from "the
# source never answered", and only the second is a coverage gap the report must
# disclose. Mirrors `RetrievalOutcome` in dd_evidence_standard so an adapter result
# can be lifted into an EvidenceRecord without a second act of interpretation.
OUTCOME_OK = "ok"                    # source answered; hits may legitimately be empty
OUTCOME_EMPTY = "empty"              # source answered and returned nothing (a real negative)
OUTCOME_UNAVAILABLE = "unavailable"  # source did not answer — NOT a negative result
OUTCOME_TIMEOUT = "timeout"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED = "skipped"          # deliberately not attempted (no key, not applicable)

#: Outcomes that mean "this source did NOT give us an answer". A caller must never
#: read one of these as an absence of findings.
NON_ANSWERING_OUTCOMES = frozenset(
    {OUTCOME_UNAVAILABLE, OUTCOME_TIMEOUT, OUTCOME_ERROR, OUTCOME_SKIPPED}
)


def source_outcome(result: Any) -> str:
    """R-F3101 — the retrieval outcome of any adapter result, however shaped.

    Reads an explicit `outcome` first, then falls back to the legacy `ok`/`error`/
    `source_unavailable` triple that the seven `_common`-based adapters already use.
    Returns `OUTCOME_ERROR` for a result that carries NO outcome signal at all —
    deliberately, and this is the whole point: an unknown outcome must never be
    reported as a successful empty screen. Failing closed here is what makes the
    conformance guard (R-F3103) meaningful rather than cosmetic."""
    if not isinstance(result, dict):
        return OUTCOME_ERROR
    explicit = str(result.get("outcome") or "").strip().lower()
    if explicit:
        return explicit
    if result.get("source_unavailable") or result.get("stale"):
        return OUTCOME_UNAVAILABLE
    if "ok" in result:
        if not result.get("ok"):
            return OUTCOME_ERROR
        return OUTCOME_EMPTY if not (result.get("hits") or []) else OUTCOME_OK
    return OUTCOME_ERROR


def answered(result: Any) -> bool:
    """R-F3101 — True only when the source actually answered.

    The one question every caller must ask before treating an empty result as a
    negative finding. `answered(r) is False` means UNKNOWN, never CLEAR."""
    return source_outcome(result) not in NON_ANSWERING_OUTCOMES


def stamp_outcome(result: dict, outcome: str, *, detail: str = "",
                  module: str = "") -> dict:
    """R-F3101 — record the outcome on an adapter result, in place.

    Additive by construction: it only ever ADDS `outcome` (and `ok`/`error` when
    absent), so an existing caller reading the old keys is unaffected. That property
    is what lets the five non-conforming adapters be brought into the contract
    without a coordinated change to every reader.

    ── R-F3113 — AND IT REACHES THE BRAIN ──────────────────────────────────
    R-F3101-R-F3106 made a dead source legible TO THE CALLER and stopped there. That
    is only half of §21a, which requires both the success AND the failure branch to
    reach the brain: a source that stops answering was recorded in a returned dict
    that nothing outside the request ever saw, so ARIA could not know a source had
    gone dark, `gap_detector` had nothing to pick up, and the self-heal loop (§21c)
    could not act. "Wired" is a definition, not a vibe — a local ring buffer is DARK.

    The SUCCESS branch is already wired: every adapter here carries `@wired`. This
    closes the failure half, at the single choke point, so no adapter can be brought
    into the contract and still fail darkly.

    `skipped` is deliberately NOT wired. It means "not attempted on purpose" — a
    missing optional credential, a query too short to send — and it is already
    surfaced as a DD data_gap. Wiring it would flood the gap ledger with a standing
    configuration fact and drown the signal this exists to carry.
    """
    if not isinstance(result, dict):
        return result
    result["outcome"] = outcome
    result.setdefault("ok", outcome in (OUTCOME_OK, OUTCOME_EMPTY))
    if detail and not result.get("error"):
        result["error"] = detail[:300]
    if outcome in NON_ANSWERING_OUTCOMES and outcome != OUTCOME_SKIPPED:
        _wire_non_answer(result, outcome, detail=detail, module=module)
    return result


def _wire_non_answer(result: dict, outcome: str, *, detail: str = "",
                     module: str = "") -> None:
    """R-F3113 — a source that did not answer is a GAP; tell the brain.

    `gap_type="source_failure"` is already registered in `capability_gaps`
    (VALID_GAP_TYPES), and `wire_failure` de-dupes on a 1h window, so a persistently
    dark source records once per hour rather than once per DD. Best-effort and
    silent: an observability wire must never take down the retrieval it observes."""
    try:
        from ..engine_wiring import wire_failure
        src = str(result.get("source") or "unknown")
        wire_failure(
            module=module or f"sources.{src}",
            detail=(f"source did not answer ({outcome})"
                    + (f": {detail[:160]}" if detail else "")),
            gap_type="source_failure",
            source=f"sources.{src}:R-F3113",
        )
    except Exception:       # noqa: BLE001 — observability must never break retrieval
        pass


def finalise(result: dict, started_at: float) -> dict:
    """Fill in hit_count + query_time_ms once the adapter has assembled
    its hits list. Returns the same dict for chaining."""
    result["hit_count"] = len(result.get("hits") or [])
    result["query_time_ms"] = int((time.time() - started_at) * 1000)
    return result


def mark_stale_if_expired(result: dict, cache: dict, ttl_s: float) -> dict:
    """R-F2167 — flag a result as served from a STALE cache.

    Each sanctions adapter's `_load_records()` serves its old in-process cache
    when the upstream refresh FAILS (feed down / rate-limited / no key), and
    only updates `cache["fetched_at"]` on a SUCCESSFUL refresh. So if the cache
    is now past its TTL, the records we just screened against are STALE — the
    screen did NOT run against current data. An empty hit-list on stale data is
    NOT a clearance: a newly-designated entity may simply be absent from the old
    snapshot (a silent false-negative — the worst output a compliance tool can
    emit). Flag `stale`/`source_unavailable` so the DD orchestrator raises an
    UNVERIFIED data_gap + a non-GREEN verdict, mirroring the R-F1696 guard on
    the OpenSanctions-aggregate path. Best-effort — never raises into a screen.
    """
    try:
        if (time.time() - float(cache.get("fetched_at", 0.0))) >= ttl_s:
            result["stale"] = True
            result["source_unavailable"] = True
    except Exception:
        pass
    return result


# ── Name normalisation ─────────────────────────────────────────────────────
# Sanctions-list names are messy ("BAYKAR DEFENSE INDUSTRIES AND AEROSPACE
# TECHNOLOGIES JOINT-STOCK COMPANY LTD."), query names are short ("Baykar").
# Fuzzy matching needs both sides stripped of corporate suffixes and
# punctuation before comparison.

_SUFFIX_TOKENS = {
    # English
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
    "plc", "lp", "llp", "co", "company", "holdings", "group", "pvt",
    "private",
    # German
    "gmbh", "ag", "kg", "ohg", "se",
    # French
    "sa", "sas", "sarl", "eurl",
    # Italian / Spanish / Portuguese
    "srl", "spa", "sl", "sa", "lda", "ltda",
    # Nordic
    "oy", "ab", "as", "asa",
    # Slavic
    "spol", "sro", "zrt", "sp", "z", "o",
    # Turkish
    "as", "ltd", "sti",
    # Arabic-Latinised
    "llc", "co", "company",
    # Defence-industry specifics often appended
    "defense", "defence", "systems", "industries", "industrie",
    "technologies", "technology", "aerospace", "aviation",
}


def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, drop corporate suffixes. Used for
    fuzzy comparison and as a dedupe key within result sets."""
    if not name:
        return ""
    # Lowercase and collapse whitespace, strip most punctuation
    n = re.sub(r"[^\w\s&-]", " ", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    # Drop corporate suffix tokens
    tokens = [t for t in n.split() if t not in _SUFFIX_TOKENS and len(t) > 1]
    return " ".join(tokens)


def similarity(a: str, b: str) -> float:
    """Return 0.0-1.0 similarity after normalisation. Uses difflib's
    SequenceMatcher ratio — deterministic, no external deps, good enough
    for the narrow "fuzzy match a cleaned name" job we need.

    R-F569 (2026-05-16) — substring boost is now gated. Pre-R-F569 any
    a-in-b or b-in-a got at least 0.85 even when the shared substring
    was 2-3 chars. That made "ES" in "ES SECURITIES" boost an Embraer
    query to 0.78+, false-flagging a clean Brazilian aerospace giant.
    Guard: the shorter string must be ≥5 chars AND constitute ≥40% of
    the longer one. Otherwise we fall through to plain SequenceMatcher.
    """
    a_n = normalise_name(a)
    b_n = normalise_name(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    # If one is a substring of the other, boost — but only if the
    # substring is long enough to be discriminating.
    if a_n in b_n or b_n in a_n:
        shorter = a_n if len(a_n) <= len(b_n) else b_n
        longer = b_n if shorter is a_n else a_n
        if len(shorter) >= 5 and len(shorter) / max(len(longer), 1) >= 0.4:
            return max(0.85, difflib.SequenceMatcher(None, a_n, b_n).ratio())
    return difflib.SequenceMatcher(None, a_n, b_n).ratio()


def fuzzy_filter(
    hits: list[dict],
    query_name: str,
    name_key: str = "name",
    threshold: float = 0.62,
    max_hits: int = 25,
) -> list[dict]:
    """Score each hit by name similarity and keep those above threshold.
    Adds a `_match_score` key to each surviving hit so the caller can
    sort or display the best matches first."""
    if not query_name or not hits:
        return []
    scored: list[tuple[float, dict]] = []
    for h in hits:
        cand = (h.get(name_key) or "").strip()
        if not cand:
            # Check alias fields if the primary is empty
            cand = (h.get("primary_name") or h.get("full_name") or "").strip()
        s = similarity(query_name, cand)
        # Also try any alias / a.k.a. fields
        aliases = h.get("aliases") or h.get("aka") or []
        if isinstance(aliases, list):
            for a in aliases:
                if isinstance(a, str):
                    s = max(s, similarity(query_name, a))
                elif isinstance(a, dict):
                    s = max(s, similarity(query_name, a.get("name") or ""))
        if s >= threshold:
            h["_match_score"] = round(s, 3)
            scored.append((s, h))
    scored.sort(key=lambda p: p[0], reverse=True)
    return [h for (_s, h) in scored[:max_hits]]


# ── Cached HTTP client helper ──────────────────────────────────────────────
# Every adapter needs the same pattern: short per-request timeout,
# user-agent header, retry once on connection error. This lives here so
# we don't copy-paste it 6 times.

_DEFAULT_TIMEOUT_S = 12.0
_DEFAULT_UA = "ARIA-DD-Orchestrator/1.0 (contact via /api/aria/health)"

# R-F751 (2026-05-20) — bound the SSL handshake + DNS connect phase so
# a stalled upstream can't park the event loop for many seconds.
# `ssl.wrap_bio` / DNS lookups run synchronously inside the async httpx
# pipeline; without an explicit connect_timeout, slow handshakes leak
# into main-loop stall time (wedge_675_1779301744.log captured 6.23s
# inside ssl.wrap_bio called from self_diagnostic._check_smoke → un_sc
# _sanctions.is_available). 3s caps the worst-case stall per probe;
# real outages still surface as a timeout exception within budget.
_DEFAULT_CONNECT_TIMEOUT_S = 3.0


def _make_httpx_timeout(total: float):
    """R-F751: build a per-phase httpx Timeout that caps the connect/SSL
    handshake at _DEFAULT_CONNECT_TIMEOUT_S. Without this, a slow SSL
    handshake parks the main loop inside ssl.wrap_bio for the full
    `total` window (wedge_675 captured 6.23s here).
    """
    import httpx
    return httpx.Timeout(
        total,
        connect=_DEFAULT_CONNECT_TIMEOUT_S,
    )


async def http_get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> Any:
    """Fetch a URL and parse JSON. Returns None on any error. Adapters
    that need finer control (auth headers, XML parsing, retries) build
    their own client — this is the happy-path helper."""
    import httpx

    h = {"User-Agent": _DEFAULT_UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    try:
        # R-F751: per-phase timeout bounds SSL handshake stall.
        async with httpx.AsyncClient(
            timeout=_make_httpx_timeout(timeout),
            follow_redirects=True,
        ) as client:
            r = await client.get(url, params=params, headers=h)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.debug("http_get_json %s failed: %s", url, e)
        return None


async def http_get_text(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> str | None:
    """Fetch a URL and return text. For XML/CSV/HTML sources."""
    import httpx

    h = {"User-Agent": _DEFAULT_UA}
    if headers:
        h.update(headers)
    try:
        # R-F751: per-phase timeout bounds SSL handshake stall.
        async with httpx.AsyncClient(
            timeout=_make_httpx_timeout(timeout),
            follow_redirects=True,
        ) as client:
            r = await client.get(url, params=params, headers=h)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.debug("http_get_text %s failed: %s", url, e)
        return None
