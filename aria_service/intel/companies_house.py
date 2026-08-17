"""ARIA Companies House Integration — UK company registry lookups.

Free API, no key required for basic endpoints. Provides:
  - Company profile (name, address, SIC codes, incorporation date, status)
  - Officers (directors past and present)
  - PSC (Persons of Significant Control — beneficial ownership)
  - Filing history (accounts, confirmation statements)
  - Registered address verification

This closes ghost detection checklist items that were showing '?' in
investigation outputs:
  - Item 1: Incorporation date → company profile
  - Item 2: Physical premises → registered address
  - Item 3: Directors → officer list
  - Item 6: Procurement history → filing history (proxy)
  - Item 10: Shared address → registered address cross-check

API docs: https://developer.company-information.service.gov.uk/
Rate limit: 600 requests/5 minutes with an API key.

Feature-gated: ARIA_COMPANIES_HOUSE_ENABLED env var (default ON).
REQUIRED: COMPANIES_HOUSE_API_KEY. The CH REST API authenticates every request
(HTTP Basic, key as username); WITHOUT the key every call 401s and returns NOTHING
(verified live 2026-07-08 R-F2501: search_companies=0, get_officers=0). The key is
free at developer.company-information.service.gov.uk. Until it is set, GB DDs cannot
gather directors / incorporation date / PSC and correctly data-starve to INSUFFICIENT.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import contextvars
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from .wire import fail_wire  # R-F1789 §21 brain-wiring
from . import url_safety

logger = logging.getLogger("aria.intel.companies_house")

_BASE_URL = "https://api.company-information.service.gov.uk"
_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
_TIMEOUT = 15.0

# R-F2511 — CH 429 (rate-limit: 600 req/5min free tier) + timeouts are TRANSIENT.
# _get retries them with backoff instead of silently returning empty, which made GB
# DDs report 0 officers under load (the DD fires search+profile+officers+PSC+filing,
# and standalone returned 3 while in-DD returned 0 with no error). On persistent
# unavailability we flag a per-async-context ContextVar so the caller surfaces a
# data-gap (never-false-clean: a rate-limited empty must NOT read as "verified: no
# directors"). ContextVar is task-local → safe under concurrent DDs.
_MAX_RETRIES = int(os.getenv("COMPANIES_HOUSE_MAX_RETRIES", "3"))
_BACKOFF_BASE = 1.5
_ch_unavailable: contextvars.ContextVar = contextvars.ContextVar("ch_unavailable", default=None)


def _mark_unavailable(reason: str) -> None:
    try:
        _ch_unavailable.set(reason)
    except Exception:
        pass


def consume_unavailable() -> str | None:
    """Return + CLEAR the last CH-unavailability reason in this async context
    (None if all calls were healthy). Callers read this after a lookup to decide
    whether an empty result is 'genuinely no data' vs 'CH was unavailable'."""
    try:
        r = _ch_unavailable.get()
        _ch_unavailable.set(None)
        return r
    except Exception:
        return None


@fail_wire(module="companies_house", gap_type="api_missing")
def is_enabled() -> bool:
    val = os.getenv("ARIA_COMPANIES_HOUSE_ENABLED", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def missing_key_gap() -> str | None:
    """R-F2501 — the actionable data-gap notice when CH is enabled but has NO API key
    (the CH REST API 401s without it → GB DDs get zero registry data → INSUFFICIENT).
    Returns None when the key is set. Single source of truth so the DD identity layer
    surfaces the ROOT cause + the operator action, not a vague 'registry incomplete'."""
    if is_enabled() and not _API_KEY:
        return (
            "Companies House API key not configured — GB registry data (directors, "
            "incorporation date, PSC/beneficial owners) is UNAVAILABLE, so this GB DD "
            "cannot be registry-verified. Set COMPANIES_HOUSE_API_KEY (free at "
            "developer.company-information.service.gov.uk) to enable decision-grade GB "
            "due diligence."
        )
    return None


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if _API_KEY:
        import base64
        # CH API uses HTTP Basic auth with key as username, empty password
        encoded = base64.b64encode(f"{_API_KEY}:".encode()).decode()
        h["Authorization"] = f"Basic {encoded}"
    return h


# ── Company number extraction ──────────────────────────────────────────────

_UK_COMPANY_NUMBER_RE = re.compile(r"\b(?:SC|NI|OC|SO|NC|R0|IP|AC|FC|GE|LP|SL|NP|CE|CS|PC|RS)?(\d{6,8})\b")


@fail_wire(module="companies_house", gap_type="api_missing")
def extract_company_number(text: str) -> str | None:
    """Try to extract a UK company number from text."""
    m = _UK_COMPANY_NUMBER_RE.search(text)
    if m:
        num = m.group(0)
        # Pad to 8 digits if numeric only
        if num.isdigit():
            num = num.zfill(8)
        return num
    return None


# ── API calls ──────────────────────────────────────────────────────────────

# ── R-F3404 — WHY the request returned nothing ───────────────────────────────
#
# `_get` collapses five different outcomes into `None`: a genuine 404, an exhausted
# rate-limit, a timeout, a non-200, and any exception. For most callers that is fine —
# they treat absence as "not found" and the `_mark_unavailable` flag carries the
# never-false-clean signal separately.
#
# It is NOT fine for /company/{n}/insolvency, where **404 is the answer**: Companies
# House returns 404 for a company with no insolvency history (PROBED 2026-07-29 against
# 04300718, a solvent company). An adapter built on `_get` alone would report "no
# insolvency" identically for a clean company and for a rate-limited request — a false
# clean manufactured at the transport layer, before any DD logic runs.
#
# So the outcome travels back as a value. The vocabulary deliberately mirrors
# `dd_evidence_standard.RetrievalOutcome`, where SUCCESS / ZERO_RESULTS / NO_MATCH are
# ANSWERS and TIMEOUT / RATE_LIMITED / SOURCE_UNAVAILABLE are not — rather than inventing
# a second spelling of the same distinction.
OUTCOME_OK = "ok"                      # 200, parsed
OUTCOME_NOT_FOUND = "not_found"        # genuine 404 — an ANSWER, not a failure
OUTCOME_RATE_LIMITED = "rate_limited"  # 429, retries exhausted — NOT an answer
OUTCOME_TIMEOUT = "timeout"            # network/timeout, retries exhausted — NOT an answer
OUTCOME_HTTP_ERROR = "http_error"      # any other non-200 — NOT an answer
OUTCOME_ERROR = "error"                # unexpected exception — NOT an answer
OUTCOME_DISABLED = "disabled"          # no API key configured — NOT an answer

#: Outcomes that mean the register ANSWERED. Anything else must surface as a data gap.
ANSWERED_OUTCOMES: frozenset[str] = frozenset({OUTCOME_OK, OUTCOME_NOT_FOUND})


async def _get_outcome(path: str, _attempt: int = 0) -> tuple[dict | None, str]:
    """GET from Companies House, returning (parsed_json_or_None, outcome).

    This is the single HTTP path — `_get` delegates to it, so there is one retry policy
    and one place where an outcome is decided. Forking it would recreate the
    two-aggregators-disagreeing shape this codebase keeps paying for.
    """
    if not is_enabled():
        return None, OUTCOME_DISABLED
    url = f"{_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:  # no-breaker: Companies House is a free authoritative source; breaker belongs at the caller (DD pipeline)
            resp = await url_safety.safe_get(client, url, headers=_headers())
            if resp.status_code == 404:
                return None, OUTCOME_NOT_FOUND
            if resp.status_code == 429:
                if _attempt < _MAX_RETRIES:
                    _ra = (resp.headers.get("Retry-After") or "").strip()
                    _wait = min(8.0, float(_ra)) if _ra.isdigit() else _BACKOFF_BASE * (_attempt + 1)
                    logger.warning("Companies House rate limited (429) — retry %d/%d after %.1fs (%s)",
                                   _attempt + 1, _MAX_RETRIES, _wait, path)
                    await asyncio.sleep(_wait)
                    return await _get_outcome(path, _attempt + 1)
                logger.warning("Companies House rate limited (429) — exhausted %d retries (%s)", _MAX_RETRIES, path)
                _mark_unavailable("rate_limited")
                return None, OUTCOME_RATE_LIMITED
            if resp.status_code != 200:
                logger.debug("CH API %s returned %d", path, resp.status_code)
                return None, OUTCOME_HTTP_ERROR
            return resp.json(), OUTCOME_OK
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
        # Transient network/timeout — retry with backoff before giving up.
        if _attempt < _MAX_RETRIES:
            await asyncio.sleep(_BACKOFF_BASE * (_attempt + 1))
            return await _get_outcome(path, _attempt + 1)
        logger.debug("CH API request failed after %d retries: %s", _MAX_RETRIES, e)
        _mark_unavailable("timeout")
        return None, OUTCOME_TIMEOUT
    except Exception as e:
        logger.debug("CH API request failed: %s", e)
        return None, OUTCOME_ERROR


async def _get(path: str, _attempt: int = 0) -> dict | None:
    """GET from Companies House API. Returns parsed JSON, or None on genuine 404 /
    persistent failure. R-F2511 — 429 (rate-limit) and timeouts are TRANSIENT and are
    RETRIED with backoff (respecting Retry-After) rather than silently returning empty;
    on persistent failure `_mark_unavailable` flags the async context so the caller can
    surface a data-gap (never-false-clean). Only a real 404 returns None-as-not-found.

    R-F3404 — now a thin wrapper over `_get_outcome`. Behaviour is byte-for-byte the
    same for all ~20 existing callers; new callers that must distinguish "the register
    said no" from "the register did not answer" call `_get_outcome` directly.
    """
    data, _outcome = await _get_outcome(path, _attempt)
    return data


@fail_wire(module="companies_house", gap_type="api_missing")
async def search_companies(query: str, limit: int = 5) -> list[dict]:
    """Search for companies by name."""
    data = await _get(f"/search/companies?q={query}&items_per_page={limit}")
    if not data:
        return []
    items = data.get("items") or []
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="companies_house",
        summary="Search Companies",
        source_id="companies_house:R-F996",
    )

    return [
        {
            "company_number": item.get("company_number"),
            "title": item.get("title"),
            "company_status": item.get("company_status"),
            "date_of_creation": item.get("date_of_creation"),
            "address_snippet": item.get("address_snippet"),
            "company_type": item.get("company_type"),
        }
        for item in items
    ]


# ── R-F3014 — best-match company resolution (never blindly results[0]) ──────
_GENERIC_COMPANY_TOKENS = frozenset({
    "limited", "ltd", "plc", "llp", "lp", "uk", "gb", "group", "holdings",
    "holding", "the", "and", "co", "company", "international", "services",
})
_DEAD_COMPANY_STATUSES = (
    "dissolved", "closed", "closed-on", "converted-closed", "removed",
    "liquidation",
)


def _name_tokens(s: str) -> set[str]:
    import re
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def _company_name_match(query: str, title: str) -> float:
    """Jaccard overlap of DISTINCTIVE tokens (generic corporate suffixes removed),
    so an exact 'COHORT PLC' beats 'COHORT SECURITY SYSTEMS LTD' for query
    'Cohort plc' — coverage alone would score both 1.0. 1.0 == same distinctive name."""
    q = _name_tokens(query) - _GENERIC_COMPANY_TOKENS
    t = _name_tokens(title) - _GENERIC_COMPANY_TOKENS
    q = q or _name_tokens(query)
    t = t or _name_tokens(title)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


def _normalised_legal_name(s: str) -> str:
    """R-F3461 — the FULL legal name, order and suffix preserved.

    `_company_name_match` above is a Jaccard over DISTINCTIVE tokens, so it deliberately
    discards word ORDER and generic corporate suffixes. That is right for ranking near
    matches and wrong for deciding whether the subject was actually identified: for the
    query "Babcock International Group PLC" it scores ALL THREE of

        BABCOCK INTERNATIONAL GROUP PLC     <- the exact legal name
        BABCOCK GROUP INTERNATIONAL LIMITED <- different company, words reordered
        BABCOCK INTERNATIONAL LIMITED       <- different company

    at exactly 1.00, because each reduces to {babcock, international}. The report then
    told the reader its subject was AMBIGUOUS and "inferred, not confirmed", on a run
    where the register held a verbatim match for the name supplied.

    Comparing the whole normalised string restores the distinction the token set threw
    away. Punctuation and spacing vary between the register and how people type a name,
    so those are normalised; nothing else is.
    """
    import re as _re
    return _re.sub(r"[^a-z0-9 ]+", "", str(s or "").lower()).strip()


def _exact_legal_name_matches(query: str, title: str) -> bool:
    """True when the register title IS the name that was searched for."""
    q, t = _normalised_legal_name(query), _normalised_legal_name(title)
    return bool(q) and q == t


def _company_status_is_dead(status: str) -> bool:
    """Return whether a registry status cannot safely identify a live subject."""
    normalised = str(status or "").strip().lower()
    return any(dead in normalised for dead in _DEAD_COMPANY_STATUSES)


def _is_overseas_entity(row: dict) -> bool:
    """A Register-of-Overseas-Entities record: an 'OE'-prefixed number or an overseas
    company_type. It has no officers/PSC at the standard company endpoints, so it is
    the WRONG target for a trading-company DD when a normal company shares the name."""
    num = str((row or {}).get("company_number") or "").strip().upper()
    ctype = str((row or {}).get("company_type") or "").lower()
    return num.startswith("OE") or "overseas" in ctype


def _pick_best_company(query: str, results: list[dict],
                       _decision: dict | None = None) -> dict:
    """R-F3014 — choose the best CH search hit instead of blindly results[0].
    Rank: distinctive-name match, then a NON-overseas trading company, then an
    active status, then the original search rank. Prevents a same-named Overseas
    Entity (e.g. OE003509 'COHORT PLC', Jersey) from being resolved for a DD whose
    real subject is the trading company (05684823).

    ── R-F3123 — THE PICK MUST BE DISCLOSED, NOT SILENT ────────────────────
    THE DEFECT, measured on two real runs of the SAME query (Mitie, 2026-07-26):

        query "MITIE FACILITIES MANAGEMENT LIMITED" -> 6 Companies House records
          07281729  dissolved  2010-06-11  MITIE FACILITIES MANAGEMENT LIMITED
          02938041  active     1994-06-13  MITIE LIMITED
          00906936  active     1967-05-24  MITIE TECHNICAL FACILITIES MANAGEMENT LTD
          + 3 more

    One run resolved 07281729 (exact name, DISSOLVED); an earlier report resolved
    02938041 (matched on a FORMER name, ACTIVE — a different legal entity). Both are
    defensible readings of an ambiguous name, and NEITHER report said the name was
    ambiguous. The customer received a confident file on an entity they may not have
    meant, with no way to tell.

    The RANKING is not the bug — an exact name match SHOULD win. The bug is silence.
    A DD that states an identity it merely inferred is fabricating the one field
    everything else hangs off. So the selection now records what it saw and why it
    chose, and flags the cases a human must adjudicate:

      * more than one candidate at the top name score  -> tie
      * winner dissolved while an ACTIVE alternative exists -> dissolved_over_active
      * winner is not an exact distinctive-name match  -> inexact

    `_decision` is an optional out-parameter (the same additive pattern as R-F3105/
    R-F3108), so every existing caller is untouched.
    """
    if not results:
        if _decision is not None:
            _decision.update({"resolved": None, "candidates": [], "ambiguous": False,
                              "reasons": ["no candidates returned"]})
        return {}

    def _rank(item):
        idx, row = item
        return (
            # R-F3461 — an EXACT full legal-name match outranks everything. Without this
            # the tie between three different companies was broken by status and search
            # rank, so the verbatim match could lose to a reordered name.
            1 if _exact_legal_name_matches(query, str(row.get("title") or "")) else 0,
            _company_name_match(query, str(row.get("title") or "")),
            0 if _is_overseas_entity(row) else 1,
            1 if "active" in str(row.get("company_status") or "").lower() else 0,
            -idx,
        )

    winner = max(enumerate(results), key=_rank)[1]

    if _decision is not None:
        scored = [
            {
                "company_number": str(r.get("company_number") or ""),
                "title": str(r.get("title") or ""),
                "status": str(r.get("company_status") or ""),
                "incorporated": str(r.get("date_of_creation") or ""),
                "is_overseas_entity": _is_overseas_entity(r),
                "name_match": round(_company_name_match(query, str(r.get("title") or "")), 3),
            }
            for r in results
        ]
        top = max((c["name_match"] for c in scored), default=0.0)
        tied = [c for c in scored if c["name_match"] >= top - 1e-9]
        win_num = str(winner.get("company_number") or "")
        win_status = str(winner.get("company_status") or "").lower()
        win_dissolved = _company_status_is_dead(win_status)
        active_alts = [c for c in scored
                       if c["company_number"] != win_num and "active" in c["status"].lower()]
        # R-F3461 — an exact full legal-name match is an IDENTIFICATION, not a tie.
        exact = [c for c in scored if _exact_legal_name_matches(query, c["title"])]
        exact_non_overseas = [c for c in exact if not c["is_overseas_entity"]]
        exact_is_winner = (
            (len(exact) == 1 and exact[0]["company_number"] == win_num)
            or (
                len(exact_non_overseas) == 1
                and exact_non_overseas[0]["company_number"] == win_num
            )
        )

        reasons: list[str] = []
        if len(exact) > 1 and not exact_is_winner:
            # Two companies genuinely registered under the same legal name is rare and IS
            # ambiguous — and it is a SHARPER statement than the generic tie, so it is
            # tested first. Ordered the other way it never fired, because a set of exact
            # matches is also a set of top-scoring matches.
            reasons.append(
                f"{len(exact)} companies are registered under this exact legal name — "
                "the register itself does not distinguish them; confirm by registration "
                "number")
        elif len(tied) > 1 and not exact_is_winner:
            reasons.append(
                f"{len(tied)} candidates share the top name match ({top:.2f}) — the "
                "choice between them rests on status and search rank, not on the name")
        if win_dissolved and active_alts:
            reasons.append(
                f"the selected company is {win_status or 'not active'} while "
                f"{len(active_alts)} ACTIVE company/companies match this name "
                f"(e.g. {active_alts[0]['company_number']} {active_alts[0]['title']}) — "
                "confirm which legal entity is the intended counterparty")
        elif win_dissolved:
            reasons.append(
                f"the only resolved name is {win_status or 'not active'} — confirm a "
                "live registration number before due diligence")
        if top < 1.0:
            reasons.append(
                f"no candidate is an exact distinctive-name match (best {top:.2f}) — "
                "the subject was inferred from a partial name match")
        _decision.update({
            "query": query,
            "resolved": win_num,
            "resolved_title": str(winner.get("title") or ""),
            "resolved_status": str(winner.get("company_status") or ""),
            "candidate_count": len(scored),
            "candidates": scored[:8],
            "ambiguous": bool(reasons),
            "reasons": reasons,
        })
    return winner


@fail_wire(module="companies_house", gap_type="api_missing")
def resolve_company_search(query: str, results: list[dict]) -> tuple[dict | None, dict]:
    """Resolve a registry search only when its identity decision is safe.

    Returns ``(company, decision)``. ``company`` is ``None`` for empty, dead,
    partial, or genuinely ambiguous matches so callers cannot accidentally feed
    an inferred registration number into identity-dependent downstream work.
    """
    decision: dict = {}
    selected = _pick_best_company(query, results, decision) or None
    if selected is None or decision.get("ambiguous"):
        return None, decision
    return selected, decision


@fail_wire(module="companies_house", gap_type="api_missing")
def _accounts_block(accounts: dict | None) -> dict:
    """Normalise the Companies House `accounts` object (R-F2782).

    Returns a stable shape whatever CH sends — an absent or malformed block
    yields `filed=False` with empty fields rather than raising or, worse,
    looking like a company with clean accounts. `filed` is deliberately keyed
    off a real `made_up_to` date: a company that has never filed has an
    `accounts` object containing only a `next_due`, and that must not read as
    evidence of filing.

    `distress_flags` are SIGNALS, not a verdict — see the note at the call site.
    """
    a = accounts if isinstance(accounts, dict) else {}
    last = a.get("last_accounts") if isinstance(a.get("last_accounts"), dict) else {}

    made_up_to = last.get("made_up_to") or ""
    acct_type = (last.get("type") or "").strip().lower()
    overdue = bool(a.get("overdue"))

    flags: list[str] = []
    if overdue:
        # Late statutory accounts are a standard early-distress indicator.
        flags.append("accounts_overdue")
    if acct_type == "dormant":
        flags.append("dormant_accounts")
    if not made_up_to:
        flags.append("no_accounts_filed")

    return {
        "filed": bool(made_up_to),
        "last_made_up_to": made_up_to,
        "last_type": acct_type,
        "period_start_on": last.get("period_start_on") or "",
        "period_end_on": last.get("period_end_on") or "",
        "next_due": a.get("next_due") or "",
        "next_made_up_to": a.get("next_made_up_to") or "",
        "overdue": overdue,
        "accounting_reference_date": a.get("accounting_reference_date") or {},
        "distress_flags": flags,
        # Explicit so no downstream consumer mistakes filing metadata for a
        # solvency assessment (R-F2782 phase 2 supplies the figures).
        "has_figures": False,
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_company_profile(company_number: str) -> dict | None:
    """Get full company profile."""
    data = await _get(f"/company/{company_number}")
    if not data:
        return None
    addr = data.get("registered_office_address") or {}
    # R-F2782 — normalise once, derive both keys from it. The old inline
    # `(data.get("accounts") or {}).get("next_due")` raised AttributeError on a
    # truthy non-dict (the `or {}` guard only catches falsy), and having two
    # readers of the same field invited them to drift apart.
    accounts = _accounts_block(data.get("accounts"))
    return {
        "company_number": data.get("company_number"),
        "company_name": data.get("company_name"),
        "company_status": data.get("company_status"),
        "company_type": data.get("type"),
        "date_of_creation": data.get("date_of_creation"),
        "date_of_cessation": data.get("date_of_cessation"),
        "sic_codes": data.get("sic_codes") or [],
        "registered_address": {
            "line1": addr.get("address_line_1", ""),
            "line2": addr.get("address_line_2", ""),
            "locality": addr.get("locality", ""),
            "postal_code": addr.get("postal_code", ""),
            "country": addr.get("country", ""),
        },
        # ── R-F3024 — NAME HISTORY. Companies House returns this on every profile
        # and ARIA threw it away, so a report could not see a name change at all.
        # Live 2026-07-25 on 07833187 (EFT CONSULT LTD): it traded as ENGINEERING
        # FOR THE FUTURE LIMITED until 2025-12-24 — while 11346584, at the SAME
        # registered address, took that name on the SAME day. A name swap between
        # two co-located companies is a textbook DD flag, and the report scored the
        # entity commercial-coherence 1.0/GREEN "no structural anomalies" and ghost
        # 0/28 because nothing in it had ever heard of the old name.
        # Shape: [{"name":…, "effective_from":…, "ceased_on":…}, …]
        "previous_company_names": data.get("previous_company_names") or [],
        "has_been_liquidated": data.get("has_been_liquidated", False),
        "has_charges": data.get("has_charges", False),
        "has_insolvency_history": data.get("has_insolvency_history", False),
        "accounts_next_due": accounts["next_due"] or None,
        # R-F2782 — keep the WHOLE accounts block, not just next_due.
        #
        # This call already fetched all of it and threw the rest away, so every
        # non-US entity reached financial_health with nothing but a due date and
        # landed on "UNKNOWN — not a US-listed filer" (financial_health.py:290).
        # A live deep DD on BAE Systems (FTSE-100, fully public UK filings) came
        # back with financial capacity UNKNOWN for exactly this reason.
        #
        # `type` (full | small | micro-entity | dormant) is a substance signal,
        # `made_up_to` dates the evidence, and `overdue` is a standard distress
        # flag. All primary-source, all free, all already in `data`.
        #
        # NB this is EVIDENCE, not a health verdict — there are no revenue or
        # solvency figures here, so it must NOT be used to answer the DD
        # `financial_capacity` question. Figures need the CH Document API
        # (iXBRL); that is R-F2782 phase 2. Closing the gate on metadata alone
        # would manufacture a false clean, which is the one thing DD may not do.
        "accounts": accounts,
        "confirmation_next_due": (data.get("confirmation_statement") or {}).get("next_due"),
        "last_full_members_list": data.get("last_full_members_list_date"),
    }


_OFFICER_ID_RE = re.compile(r"/officers/([^/]+)/appointments")

# Generous ceiling so a pathological company cannot spin the pager forever.
# Exceeding it is REPORTED (see below), never silently truncated.
_MAX_OFFICERS = 500
_OFFICERS_PAGE = 100


def _officer_id_from_links(item: dict) -> str:
    """Extract the CH officer id from `links.officer.appointments` (R-F2828).

    Shape: `/officers/<officer_id>/appointments`. This id is the ONLY thing that
    lets a director be followed to their other appointments — and it is what
    makes a person->company edge ANCHORED to a primary source rather than a name
    match, which is the Grade-A bar (R-F2726). Without it every such edge would
    be a name match, i.e. the fabrication class we spent 11 R-numbers removing.
    """
    try:
        link = ((item.get("links") or {}).get("officer") or {}).get("appointments") or ""
        m = _OFFICER_ID_RE.search(str(link))
        return m.group(1) if m else ""
    except Exception:
        return ""


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_officers(company_number: str) -> list[dict]:
    """Get current and past officers (directors), ALL pages (R-F2828).

    Two defects fixed here, both found by probing the raw CH payload live:

    1. ANCHORS WERE DISCARDED. The mapping kept 8 display fields and threw away
       `links.officer.appointments` (carrying the officer id) and `person_number`.
       The DD therefore held 35 BAE officers it could not follow anywhere, so no
       ANCHORED person->company edge could be built from them at all.

    2. SILENT TRUNCATION. The call took CH's default page and returned it whole,
       so BAE Systems reported 35 officers against `total_results: 73` — under
       half the officer record, with nothing anywhere saying so. For a question
       about ownership and control that is a completeness defect, and a silent
       one is the worst kind (cf. the never-false-clean rule).

    If a company exceeds `_MAX_OFFICERS`, the result is capped but the truncation
    is REPORTED through the existing R-F2511 unavailability channel, so a caller
    that checks `consume_unavailable()` cannot mistake a capped list for the
    whole register.
    """
    collected: list[dict] = []
    start = 0
    total = 0
    while True:
        data = await _get(
            f"/company/{company_number}/officers"
            f"?items_per_page={_OFFICERS_PAGE}&start_index={start}"
        )
        if not data:
            break
        page = data.get("items") or []
        total = int(data.get("total_results") or 0)
        collected.extend(page)
        start += len(page)
        # Stop on: empty page (defensive against a non-advancing pager),
        # reaching the reported total, or hitting the safety ceiling.
        if not page or start >= total or len(collected) >= _MAX_OFFICERS:
            break

    if total and len(collected) < total:
        _mark_unavailable(f"officers_truncated:{len(collected)}_of_{total}")

    return [
        {
            "name": item.get("name"),
            "role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on"),
            "resigned_on": item.get("resigned_on"),
            "nationality": item.get("nationality"),
            "country_of_residence": item.get("country_of_residence"),
            "occupation": item.get("occupation"),
            "is_current": item.get("resigned_on") is None,
            # R-F2828 — primary-source anchors. Empty string (never None) so a
            # consumer can test truthiness without a None-check, and an absent
            # anchor is explicit rather than missing.
            "officer_id": _officer_id_from_links(item),
            "person_number": item.get("person_number") or "",
            "appointment_link": (item.get("links") or {}).get("self") or "",
        }
        for item in collected[:_MAX_OFFICERS]
    ]


def _exemption_is_active(item: dict, today: str) -> bool:
    """True when an exemption period is CURRENT (R-F2830).

    CH returns `exempt_from` and, once the exemption has lapsed, `exempt_to`.
    An EXPIRED exemption must never be used to explain a presently-empty PSC
    register — doing so would excuse opacity with a lapsed fact. BAE Systems is
    the live example: `psc_exempt_as_trading_on_uk_regulated_market` is active
    (no `exempt_to`), while `disclosure_transparency_rules_chapter_five_applies`
    expired on 2023-02-02.

    Dates are ISO `YYYY-MM-DD`, so string comparison is ordering-correct. A
    malformed/absent `exempt_from` returns False: we do not assume an exemption
    we cannot date.
    """
    try:
        frm = str(item.get("exempt_from") or "")
        if not frm or frm > today:
            return False
        to = str(item.get("exempt_to") or "")
        return (not to) or to >= today
    except Exception:
        return False


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_psc_exemptions(company_number: str) -> dict:
    """Why a company may lawfully have no PSC register (R-F2830).

    An EMPTY PSC register is ambiguous and the ambiguity is decision-critical:
    a company that discloses no beneficial owners looks opaque — potentially
    evasive — whereas a company exempt because it trades on a UK regulated
    market is behaving entirely normally. Reporting the first when the truth is
    the second is a false ACCUSATION, the mirror of a false clean, and both are
    the same sin: asserting what the evidence does not show (cf. R-F2791 /
    R-F2693 at opposite polarities).

    Returns a stable shape. `checked` distinguishes "we looked and there are no
    exemptions" from "we could not look" — an unreachable endpoint must never
    read as "no exemption exists".
    """
    out: dict = {
        "checked": False,
        "has_active_exemption": False,
        "active": [],
        "expired": [],
        "source_url": (
            "https://find-and-update.company-information.service.gov.uk/"
            f"company/{company_number}/persons-with-significant-control"
        ),
    }
    data = await _get(f"/company/{company_number}/exemptions")
    if data is None:
        # 404 = no exemptions filed for this company, which IS an answer, but we
        # cannot distinguish it here from an unavailable source; `_get` already
        # flags genuine unavailability via _mark_unavailable, so callers that
        # check consume_unavailable() can tell the two apart.
        out["checked"] = True
        return out

    out["checked"] = True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    exemptions = data.get("exemptions")
    if not isinstance(exemptions, dict):
        return out

    for key, block in exemptions.items():
        if not isinstance(block, dict):
            continue
        etype = block.get("exemption_type") or key
        for item in (block.get("items") or []):
            if not isinstance(item, dict):
                continue
            rec = {
                "exemption_type": etype,
                "exempt_from": item.get("exempt_from") or "",
                "exempt_to": item.get("exempt_to") or "",
            }
            if _exemption_is_active(item, today):
                out["active"].append(rec)
            else:
                out["expired"].append(rec)

    out["has_active_exemption"] = bool(out["active"])
    return out


def explain_empty_psc(psc_count: int, exemptions: dict, unavailable: str | None = None) -> str:
    """Frame an empty PSC register honestly (R-F2830).

    Order matters and encodes the honesty rules:
      1. source unavailable  -> UNKNOWN, never "no owners" (R-F2511);
      2. genuinely exempt    -> say so, with the exemption type and date;
      3. empty + no exemption-> state the fact WITHOUT implying evasion, and say
                                what it would take to resolve;
      4. non-empty           -> no explanation needed.
    An EXPIRED exemption never reaches branch 2 — it is called out instead,
    because a lapsed exemption alongside an empty register is a real question,
    not a reassurance.
    """
    if unavailable:
        return ("Beneficial ownership could NOT be retrieved from Companies House "
                f"({unavailable}) — ownership is UNKNOWN, not confirmed absent.")
    if psc_count > 0:
        return ""
    if not exemptions.get("checked"):
        return ("No PSC entries returned and the exemption register was not checked — "
                "ownership is UNKNOWN, not confirmed absent.")
    if exemptions.get("has_active_exemption"):
        a = exemptions["active"][0]
        return (
            "No PSC entries — the company holds an ACTIVE exemption "
            f"({a['exemption_type']}, from {a['exempt_from'] or 'an unstated date'}). "
            "This is a lawful basis for an empty register, typically a company trading "
            "on a UK regulated market whose ownership is disclosed under market rules "
            "instead. It is NOT an indication of concealment."
        )
    if exemptions.get("expired"):
        e = exemptions["expired"][0]
        return (
            "No PSC entries, and the only exemption on file has EXPIRED "
            f"({e['exemption_type']}, to {e['exempt_to'] or 'an unstated date'}). "
            "An empty register with no current exemption is unexplained and should be "
            "resolved before relying on ownership."
        )
    return (
        "No PSC entries and no exemption on file. This is a statement of the register's "
        "contents, NOT evidence that the company has no beneficial owners — ownership "
        "remains UNVERIFIED and needs a direct filing or shareholder-register check."
    )


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_psc(company_number: str) -> list[dict]:
    """Get Persons of Significant Control (beneficial ownership)."""
    data = await _get(f"/company/{company_number}/persons-with-significant-control")
    if not data:
        return []
    items = data.get("items") or []
    out: list[dict] = []
    for item in items:
        # R-F2726 — preserve `identification` for CORPORATE PSCs. For a
        # corporate-entity PSC the API returns its own registry identifier
        # (registration_number + country/place registered) — the ANCHOR that turns
        # "controlled by X" from a name-match guess into a VERIFIED controlled_by
        # edge (Grade A). Individual PSCs have no identification; that field is None.
        _ident = item.get("identification") or {}
        out.append({
            "name": item.get("name"),
            "kind": item.get("kind"),
            "natures_of_control": item.get("natures_of_control") or [],
            "nationality": item.get("nationality"),
            "country_of_residence": item.get("country_of_residence"),
            "notified_on": item.get("notified_on"),
            "ceased_on": item.get("ceased_on"),
            "is_current": item.get("ceased_on") is None,
            "identification": {
                "registration_number": _ident.get("registration_number"),
                "country_registered": _ident.get("country_registered"),
                "legal_form": _ident.get("legal_form"),
                "place_registered": _ident.get("place_registered"),
            } if _ident else None,
        })
    return out


# ── R-F3542 — the SECOND PSC HOP: an actual ownership walk ───────────────────
#
# THE GAP, observed on four consecutive delivered reports. `get_psc` returns the
# subject's PSCs and R-F2726 carefully preserves `identification.registration_number`
# for corporate ones — the ANCHOR that makes a control edge Grade A. **Nothing ever
# called `get_psc` again with it.** The chain stopped at hop one, so a corporate PSC was
# as far as ARIA could ever see, and "who ultimately owns this" had no answer.
#
# On Bidvest Noonan (dd_75d996233394) the corporate PSC Crane Midco Limited (06648599)
# was walkable in Companies House and terminates at a JSE-listed parent — a clean,
# decision-relevant UBO answer ("controlled by a listed group; no individual UBO above
# threshold") that the report simply never went and got.
#
# NOT the same thing as `network_walker.walk_ubo_chain`, which despite its name walks
# DIRECTORSHIPS (officers → their other appointments) and emits nothing that is an
# ownership relationship. That is the R-F3539 category error. This walks OWNERSHIP only.
#
# HONESTY PROPERTIES, each of which is a way the walk can stop:
#   * ANCHORED ONLY. A hop is taken only via `identification.registration_number`.
#     Resolving a controller by NAME is the fabrication R-F2703/R-F2726 removed.
#   * NON-UK IS A DECLARED GAP. Companies House holds UK companies; a corporate PSC
#     registered elsewhere ends the walk WITH A NAMED REASON, never silently.
#   * CYCLES AND CAPS ARE DECLARED. A truncated walk that reads as complete is a false
#     clean about ownership, so `complete` is False and `gaps` says which limit bit.
# An unanchored or foreign controller therefore leaves the chain INCOMPLETE — which is
# the honest answer, and the one R-F3027's `controlled_by_unanchored` already relies on.
_PSC_WALK_MAX_HOPS = 4
_PSC_WALK_MAX_NODES = 20

#: Companies House registration numbers are UK-only. `country_registered` is free text
#: ("England", "United Kingdom", "Scotland", "Wales", "Northern Ireland", "England and
#: Wales"), so match generously — a false NON-UK reading only costs a declared gap,
#: whereas a false UK reading would send a lookup for a company that is not there.
_UK_REGISTERED_MARKERS = ("united kingdom", "england", "wales", "scotland",
                          "northern ireland", "great britain", "uk", "gb")


def _psc_is_corporate(p: dict) -> bool:
    return "corporate" in str(p.get("kind") or "").lower()


def _psc_registered_uk(ident: dict) -> bool | None:
    """True/False/None — None means the register did not say, which is NOT 'not UK'."""
    where = " ".join(str(ident.get(k) or "") for k in
                     ("country_registered", "place_registered")).strip().lower()
    if not where:
        return None
    return any(m in where for m in _UK_REGISTERED_MARKERS)


@fail_wire(module="companies_house", gap_type="api_missing")
async def walk_psc_ownership(company_number: str, *,
                             max_hops: int = _PSC_WALK_MAX_HOPS,
                             max_nodes: int = _PSC_WALK_MAX_NODES) -> dict:
    """Walk the OWNERSHIP chain upward via corporate PSCs, anchored at every hop.

    Returns::

        {"root": "<regno>", "nodes": [...], "edges": [...], "ultimate": [...],
         "gaps": [...], "complete": bool, "hops_walked": int}

    `complete` is True only when every branch ended at a natural terminus — an
    individual/legal-person PSC, or a company with NO corporate PSC. Any cap, cycle,
    missing anchor or foreign registry sets it False and records a gap, because a chain
    that stops early and says nothing is indistinguishable from one that reached the top.
    """
    root = str(company_number or "").strip()
    if not root:
        return {"root": "", "nodes": [], "edges": [], "ultimate": [], "complete": False,
                "gaps": ["no company number supplied"], "hops_walked": 0}

    nodes: list[dict] = []
    edges: list[dict] = []
    ultimate: list[dict] = []
    gaps: list[str] = []
    seen: set[str] = {root}
    frontier = [(root, 0)]
    hops_walked = 0
    complete = True

    while frontier:
        regno, hop = frontier.pop(0)
        if hop >= max_hops:
            complete = False
            gaps.append(
                f"ownership walk stopped at hop {hop} for {regno} — max_hops={max_hops} "
                "reached; the chain above this point is NOT established")
            continue
        try:
            pscs = await get_psc(regno) or []
        except Exception as e:  # noqa: BLE001 — a failed hop is a GAP, never a silent end
            complete = False
            gaps.append(f"could not read PSCs of {regno}: {type(e).__name__}")
            continue

        current = [p for p in pscs if p.get("is_current")]
        nodes.append({"company_number": regno, "hop": hop, "psc_count": len(current)})
        hops_walked = max(hops_walked, hop)

        if not current:
            # A company with no PSC is a terminus, but an EMPTY PSC register is also
            # what an exempt or non-compliant company looks like — say which is unknown
            # rather than presenting it as "ownership fully traced".
            gaps.append(f"{regno} lists no current PSC — terminus, or an empty/exempt "
                        "register; not evidence of no owner")
            continue

        for p in current:
            name = str(p.get("name") or "").strip()
            if not _psc_is_corporate(p):
                ultimate.append({"name": name, "kind": p.get("kind"),
                                 "via": regno, "hop": hop + 1,
                                 "natures_of_control": p.get("natures_of_control") or []})
                continue

            ident = p.get("identification") or {}
            nxt = str(ident.get("registration_number") or "").strip()
            uk = _psc_registered_uk(ident)

            if not nxt:
                complete = False
                gaps.append(
                    f"corporate PSC {name!r} of {regno} has NO registration number — "
                    "not anchorable, so the chain above it is not established "
                    "(resolving it by name would be a guess)")
                continue
            if uk is False:
                complete = False
                gaps.append(
                    f"corporate PSC {name!r} of {regno} is registered outside the UK "
                    f"({ident.get('country_registered') or ident.get('place_registered')}) "
                    "— Companies House cannot be walked further; use the home registry")
                continue

            edges.append({"from": regno, "to": nxt, "controller_name": name,
                          "hop": hop + 1, "anchored": True,
                          "natures_of_control": p.get("natures_of_control") or []})
            if nxt in seen:
                complete = False
                gaps.append(f"ownership cycle detected at {nxt} — walk stopped")
                continue
            if len(seen) >= max_nodes:
                complete = False
                gaps.append(f"ownership walk stopped — max_nodes={max_nodes} reached")
                continue
            seen.add(nxt)
            frontier.append((nxt, hop + 1))

    return {"root": root, "nodes": nodes, "edges": edges, "ultimate": ultimate,
            "gaps": gaps, "complete": complete, "hops_walked": hops_walked}


# ── R-F3404 — three free endpoints the DD has never consulted ────────────────
#
# All three are on the key already deployed, cost nothing per call, and were PROBED live
# on 2026-07-29. Each returns a dict carrying `checked: bool` rather than a bare list,
# because for every one of them an EMPTY result is a meaningful finding — no charges, no
# insolvency, no disqualification — and an empty result is only a finding when the
# register actually answered. `checked: False` must never be rendered as clean.

def _unchecked(outcome: str, what: str) -> dict:
    """The register did not answer. Carries the reason so the DD can name it."""
    return {
        "checked": False,
        "outcome": outcome,
        "reason": (
            "Companies House API key not configured" if outcome == OUTCOME_DISABLED
            else "Companies House rate limit exhausted" if outcome == OUTCOME_RATE_LIMITED
            else "Companies House timed out" if outcome == OUTCOME_TIMEOUT
            else f"Companies House returned an error ({outcome})"
        ),
        "detail": f"{what} NOT established — re-check required, this is not a clear result",
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_charges(company_number: str) -> dict:
    """Charges register — security, liens and prior claims over the company's assets.

    Fundamental #12. The company profile carries only a `has_charges` BOOLEAN, which
    cannot tell a buyer whether a debenture sits over the assets they are about to pay
    for, who holds it, or whether it is still outstanding.

    PROBED 2026-07-29 (04300718): HTTP 200 with total_count / unfiltered_count.
    A 404 here means the company has no charges filed — an answer, not a failure.
    """
    number = (company_number or "").strip().upper()
    if not number:
        return _unchecked(OUTCOME_ERROR, "Charges")
    data, outcome = await _get_outcome(f"/company/{number}/charges")
    if outcome not in ANSWERED_OUTCOMES:
        return _unchecked(outcome, "Charges")
    if outcome == OUTCOME_NOT_FOUND or not data:
        return {"checked": True, "outcome": outcome, "total_count": 0,
                "outstanding_count": 0, "items": []}
    items = [c for c in (data.get("items") or []) if isinstance(c, dict)]
    outstanding = [
        c for c in items
        if str(c.get("status") or "").strip().lower() in {"outstanding", "part-satisfied"}
    ]
    return {
        "checked": True,
        "outcome": outcome,
        "total_count": int(data.get("total_count") or len(items) or 0),
        "unfiltered_count": data.get("unfiltered_count"),
        "outstanding_count": len(outstanding),
        "items": [
            {
                "charge_code": c.get("charge_code"),
                "status": c.get("status"),
                "created_on": c.get("created_on"),
                "satisfied_on": c.get("satisfied_on"),
                "classification": (c.get("classification") or {}).get("description"),
                "persons_entitled": [
                    p.get("name") for p in (c.get("persons_entitled") or [])
                    if isinstance(p, dict) and p.get("name")
                ],
            }
            for c in items[:50]
        ],
        "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{number}/charges",
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_insolvency(company_number: str) -> dict:
    """Insolvency register — past or current insolvency proceedings.

    Fundamental #11. The profile carries only `has_insolvency_history`, so "was there an
    insolvency, when, and of what kind" was unanswerable.

    THE TRAP THIS FUNCTION EXISTS TO AVOID. Companies House returns **404 for a company
    with no insolvency history** (PROBED 2026-07-29 against solvent 04300718). Through
    `_get` that 404 is indistinguishable from a rate-limit or a timeout, so an adapter
    written the obvious way would report "no insolvency" for a company it never managed
    to check. `_get_outcome` separates the two, and only OUTCOME_NOT_FOUND is allowed to
    mean "clean".
    """
    number = (company_number or "").strip().upper()
    if not number:
        return _unchecked(OUTCOME_ERROR, "Insolvency history")
    data, outcome = await _get_outcome(f"/company/{number}/insolvency")
    if outcome not in ANSWERED_OUTCOMES:
        return _unchecked(outcome, "Insolvency history")
    if outcome == OUTCOME_NOT_FOUND or not data:
        # The register answered: this company has no insolvency case on file.
        return {"checked": True, "outcome": outcome, "case_count": 0, "cases": [],
                "detail": "No insolvency case is recorded at Companies House"}
    cases = [c for c in (data.get("cases") or []) if isinstance(c, dict)]
    return {
        "checked": True,
        "outcome": outcome,
        "case_count": len(cases),
        "cases": [
            {
                "type": c.get("type"),
                "number": c.get("number"),
                "dates": c.get("dates"),
                "practitioners": [
                    p.get("name") for p in (c.get("practitioners") or [])
                    if isinstance(p, dict) and p.get("name")
                ],
            }
            for c in cases[:25]
        ],
        "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{number}/insolvency",
    }


#: Honorifics and post-nominals. These are NOT names, and passing them to a free-text
#: register search is how R-F3451 happened: the officer string Companies House returns is
#: "COMISKEY, Aedamar Ita, Dr", and the token "Dr" prefix-matched DREAM HOME TRAVELS,
#: DREX TECHNOLOGIES and NATIONAL IRANIAN DRILLING — three entries reported to a customer
#: as disqualification "name matches" against a sitting FTSE director.
_NAME_NOISE = frozenset({
    "dr", "mr", "mrs", "ms", "miss", "mx", "sir", "dame", "lord", "lady", "prof",
    "professor", "rev", "reverend", "hon", "rt", "capt", "captain", "col", "colonel",
    "maj", "major", "gen", "general", "jr", "sr", "ii", "iii", "iv",
    "obe", "mbe", "cbe", "kbe", "dbe", "qc", "kc", "phd", "frcs", "cbe.", "the",
})


def _person_name_parts(name: str) -> tuple[str, list[str]]:
    """Split a Companies House officer string into (surname, forenames).

    CH renders officers as ``SURNAME, Forename Middle, Title``. The surname is the
    discriminator: two people can share a forename, but a register row whose name does
    not contain this person's SURNAME cannot be this person.
    """
    raw = (name or "").strip()
    if not raw:
        return "", []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2:
        surname = parts[0]
        fore_src = parts[1]
    else:
        toks = raw.split()
        surname = toks[-1] if toks else ""
        fore_src = " ".join(toks[:-1])
    forenames = [t for t in fore_src.replace(".", " ").split()
                 if t.lower() not in _NAME_NOISE and len(t) > 1]
    if surname.lower() in _NAME_NOISE:
        surname = ""
    return surname, forenames


#: Legal-form tokens that mark a register row as an ENTITY rather than a natural person.
#: R-F3515 — "KING ROYAL TECHNOLOGIES CO. LTD" was returned as a disqualification match
#: against a director named Stephen Anthony KING, because "king" was one of its tokens.
_CORPORATE_FORM_TOKENS = frozenset({
    "ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "corp", "corporation",
    "co", "company", "gmbh", "ag", "sa", "sas", "srl", "spa", "bv", "nv", "pte", "pty",
    "oy", "ab", "as", "aps", "kft", "zoo", "sarl", "holdings", "group",
})


def _candidate_surname(title: str) -> str:
    """The SURNAME a register row belongs to, or "" when it is not a natural person.

    R-F3515 — POSITION MATTERS, and the first fix ignored it. Requiring the officer's
    surname to appear ANYWHERE in the row matched:
      * "Amar ISMAEL" / "Amar NADEEM" for an officer surnamed AMAR — AMAR is those
        people's FORENAME, so they are two unrelated individuals
      * "KING ROYAL TECHNOLOGIES CO. LTD" for an officer surnamed KING — a company

    Companies House renders these rows as "Forename SURNAME" (and occasionally
    "SURNAME, Forename"), so the surname is positional and recoverable. A row carrying a
    legal-form token is an entity and can never be the individual being screened.
    """
    raw = str(title or "")
    # Drop alias parentheticals: "Kevin GREGORY (AKA CHARLES HENRY)" is Mr Gregory.
    raw = re.sub(r"\([^)]*\)", " ", raw)

    # Is-it-an-entity is decided on the WHOLE row, before any rendering branch. Two ways
    # this was wrong on the first cut, both found by the tests rather than by reading:
    #   * punctuation hid the form token — "DREX TECHNOLOGIES S.A." strips to "s.a", which
    #     never equals "sa", so a company parsed as a person surnamed "s.a"
    #   * the check sat inside the no-comma branch only, so "DREX TECHNOLOGIES, S.A."
    #     would have parsed as a person surnamed "drex"
    def _bare(tok: str) -> str:
        return re.sub(r"[^a-z0-9]", "", tok.lower())

    if any(_bare(t) in _CORPORATE_FORM_TOKENS for t in raw.replace(",", " ").split()):
        return ""

    if "," in raw:
        # "SURNAME, Forename" — the register's other rendering.
        head = raw.split(",", 1)[0]
        toks = [t.strip(".") for t in head.split() if t.strip(".")]
        return toks[-1].lower() if toks else ""
    toks = [t.strip(".,") for t in raw.split() if t.strip(".,")]
    if not toks:
        return ""
    return toks[-1].lower()


def _disq_candidate_is_same_name(title: str, surname: str, forenames: list[str]) -> tuple[bool, bool]:
    """(keeps, forename_also_matches) for one register row.

    The row's SURNAME must equal the officer's surname. R-F3451 required only that the
    surname appear somewhere in the row, which still produced fabricated matches against
    real people (see `_candidate_surname`). Forename agreement is reported but not
    required: the register lists former and alternate names, and dropping on forename
    alone would risk a false NEGATIVE on a genuine disqualification — the dangerous
    direction here.
    """
    if not surname:
        return False, False
    cand_surname = _candidate_surname(title)
    if not cand_surname or cand_surname != surname.lower():
        return False, False
    hay = {t.strip(".,()").lower() for t in str(title or "").split()}
    return True, any(f.lower() in hay for f in forenames)


@fail_wire(module="companies_house", gap_type="api_missing")
async def search_disqualified_officers(name: str, limit: int = 20) -> dict:
    """Disqualified-directors register.

    Fundamental #16, and a check no ARIA DD has ever performed — `disqualified-directors`
    appears exactly once in the tree, as a domain fragment in an adverse-media allowlist.

    PROBED 2026-07-29: `/search/disqualified-officers?q=Smith` returns 67 results.

    NAME-MATCH DISCIPLINE. This endpoint matches on NAME ALONE, so a hit is a CANDIDATE,
    never a determination — the R-F3089 name-coincidence class, about a named human
    being. Callers must corroborate on date of birth or address before asserting that
    THIS officer is THAT disqualified person; the returned rows carry the fields needed
    to do so, and `match_basis` states the limitation so no consumer can forget it.
    """
    raw_name = (name or "").strip()
    if len(raw_name) < 3:
        return _unchecked(OUTCOME_ERROR, "Disqualification check")
    # R-F3451 — search on the NAME, not on the honorific. The raw CH officer string
    # ("SURNAME, Forename Middle, Title") was previously sent verbatim to a free-text
    # endpoint that matches tokens independently, so "Dr" and middle names pulled back
    # entries belonging to unrelated people and companies.
    _surname, _forenames = _person_name_parts(raw_name)
    q = " ".join([_surname] + _forenames[:1]).strip() or raw_name
    if len(q) < 3:
        return _unchecked(OUTCOME_ERROR, "Disqualification check")
    from urllib.parse import quote_plus
    data, outcome = await _get_outcome(
        f"/search/disqualified-officers?q={quote_plus(q)}&items_per_page={max(1, min(100, limit))}"
    )
    if outcome not in ANSWERED_OUTCOMES:
        return _unchecked(outcome, "Disqualification check")
    if outcome == OUTCOME_NOT_FOUND or not data:
        # Same SHAPE as the answered branch — a consumer must not have to know which
        # branch produced the dict to read it.
        return {"checked": True, "outcome": outcome, "query": q,
                "searched_name": raw_name, "surname_required": _surname,
                "raw_results": 0, "discarded_name_coincidence": 0,
                "total_results": 0, "candidates": [],
                "match_basis": "name_only",
                "surname_filter_applied": True,
                "filter_note": (
                    f"Rows are kept only where the entry's name contains the surname "
                    f"{_surname!r}. A disqualification recorded under a different surname "
                    f"(for example a former or married name) would not be found by this "
                    f"search."),
                "detail": f"No disqualified officer matching {q!r} is on the register"}
    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    raw_total = int(data.get("total_results") or len(items) or 0)

    # R-F3451 — a row whose name does not contain this officer's SURNAME cannot be this
    # officer. Reporting one as a "name match" is an accusation generated by tokenisation,
    # about a named human being, in a customer-facing report. `total_results` is the count
    # AFTER this filter because that is the number every consumer treats as "hits"; the
    # unfiltered figure stays visible as `raw_results` so the filter itself is auditable.
    kept: list[dict] = []
    for i in items:
        ok, fore_ok = _disq_candidate_is_same_name(i.get("title") or "", _surname, _forenames)
        if not ok:
            continue
        kept.append({
            "title": i.get("title"),
            "address_snippet": i.get("address_snippet"),
            "date_of_birth": i.get("date_of_birth"),
            "disqualification_start_on": (i.get("disqualification_start_on")
                                          or i.get("appointment_count")),
            "link": (i.get("links") or {}).get("self"),
            "forename_also_matches": fore_ok,
        })

    return {
        "checked": True,
        "outcome": outcome,
        "query": q,
        "searched_name": raw_name,
        "surname_required": _surname,
        "raw_results": raw_total,
        "discarded_name_coincidence": max(0, len(items) - len(kept)),
        "total_results": len(kept),
        # DELIBERATE WORDING: candidates, not matches. A name match is not an identity.
        "match_basis": "name_only",
        "surname_filter_applied": True,
        "corroboration_required": (
            "Matched on NAME ONLY. Confirm date of birth and address against the "
            "officer's registry record before treating this as the same person."
        ),
        "filter_note": (
            f"Rows are kept only where the entry's name contains the surname "
            f"{_surname!r}. A disqualification recorded under a different surname "
            f"(for example a former or married name) would not be found by this search."
        ),
        "candidates": kept[:limit],
        "source_url": "https://find-and-update.company-information.service.gov.uk/register-of-disqualifications",
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def search_officers(name: str, limit: int = 20) -> list[dict]:
    """Search for officers (directors / PSCs) by name.

    Returns items with `officer_id` and `appointments_url` so the caller
    can drill down into company appointments. Each entry carries a
    short address snippet + date_of_birth (partial — month/year only).

    This is the PSC-reverse entry point: "give me every UK company this
    person has been an officer of."
    """
    import urllib.parse as _urlp
    q = _urlp.quote(name or "")
    if not q:
        return []
    data = await _get(f"/search/officers?q={q}&items_per_page={limit}")
    if not data:
        return []
    out: list[dict] = []
    for item in data.get("items") or []:
        links = item.get("links") or {}
        self_link = links.get("self") or ""  # e.g. "/officers/<id>/appointments"
        officer_id = ""
        if "/officers/" in self_link:
            officer_id = self_link.split("/officers/")[-1].split("/")[0]
        dob = item.get("date_of_birth") or {}
        out.append({
            "title": item.get("title"),
            "officer_id": officer_id,
            "appointments_url": self_link,
            "address_snippet": item.get("address_snippet", ""),
            "description": item.get("description", ""),
            "date_of_birth": {"month": dob.get("month"), "year": dob.get("year")} if dob else None,
        })
    return out


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_officer_appointments(officer_id: str, limit: int = 50) -> list[dict]:
    """List every company appointment for a given officer.

    Foundational for PSC-reverse: answers "which UK companies is this
    person a director / PSC of?" Returns both active and resigned roles
    so the caller can flag recently-resigned directors as a risk signal.
    """
    if not officer_id:
        return []
    data = await _get(f"/officers/{officer_id}/appointments?items_per_page={limit}")
    if not data:
        return []
    out: list[dict] = []
    for item in data.get("items") or []:
        appointed_to = item.get("appointed_to") or {}
        out.append({
            "company_name": item.get("appointed_to", {}).get("company_name")
                or item.get("name_elements", {}).get("forename"),
            "company_number": appointed_to.get("company_number"),
            "company_status": appointed_to.get("company_status"),
            "officer_role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on"),
            "resigned_on": item.get("resigned_on"),
            "is_current": item.get("resigned_on") is None,
            "occupation": item.get("occupation"),
            "nationality": item.get("nationality"),
        })
    return out


@fail_wire(module="companies_house", gap_type="api_missing")
async def psc_reverse_lookup(name: str, max_officers: int = 5, max_apts_per_officer: int = 30) -> dict:
    """One-call answer to "which UK companies is this person tied to?"

    Steps:
      1. search_officers(name) — get up to `max_officers` candidates
      2. for each, get_officer_appointments(officer_id)
      3. merge + dedupe by company_number + flag recent resignations

    Returns `{candidates: [...], appointments: [...], summary: str}`.
    Low-signal if name is too common (e.g. "John Smith") — callers should
    require a dob or locality to disambiguate before acting on it.
    """
    candidates = await search_officers(name, limit=max_officers)
    if not candidates:
        return {"candidates": [], "appointments": [], "summary": "No matching officers."}
    all_apts: list[dict] = []
    seen_companies: set[str] = set()
    for cand in candidates[:max_officers]:
        oid = cand.get("officer_id") or ""
        if not oid:
            continue
        apts = await get_officer_appointments(oid, limit=max_apts_per_officer)
        for a in apts:
            cnum = a.get("company_number") or ""
            if cnum and cnum in seen_companies:
                continue
            if cnum:
                seen_companies.add(cnum)
            a["matched_via_officer_id"] = oid
            a["matched_via_name"] = cand.get("title", "")
            all_apts.append(a)
    current_count = sum(1 for a in all_apts if a.get("is_current"))
    resigned_last_12mo = 0
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        cutoff = (_dt.now(_tz.utc) - _td(days=365)).date()
        for a in all_apts:
            r = a.get("resigned_on")
            if r:
                try:
                    if _dt.fromisoformat(r).date() >= cutoff:
                        resigned_last_12mo += 1
                except Exception:
                    pass
    except Exception:
        pass
    summary = (
        f"{len(candidates)} officer candidate(s) matched '{name}' — "
        f"{len(all_apts)} appointments across {len(seen_companies)} UK companies "
        f"({current_count} current, {resigned_last_12mo} resigned in last 12mo)."
    )
    if len(candidates) > 1:
        summary += " ⚠️ Multiple candidates — dob or locality required to disambiguate."
    return {
        "candidates": candidates,
        "appointments": all_apts,
        "companies_total": len(seen_companies),
        "appointments_current": current_count,
        "appointments_resigned_last_12mo": resigned_last_12mo,
        "summary": summary,
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_filing_history(company_number: str, limit: int = 10) -> list[dict]:
    """Get recent filing history."""
    data = await _get(f"/company/{company_number}/filing-history?items_per_page={limit}")
    if not data:
        return []
    items = data.get("items") or []
    return [
        {
            "category": item.get("category"),
            "type": item.get("type"),
            "description": item.get("description"),
            "date": item.get("date"),
            "action_date": item.get("action_date"),
        }
        for item in items
    ]


# ── R-F3016 — Companies House iXBRL accounts figure extraction (R-F2782 Phase 2) ──
# Small & micro UK companies file statutory accounts as iXBRL (inline XBRL) via the
# CH Document API; large listed PLCs upload PDF group accounts (no iXBRL). We extract
# the BALANCE SHEET only — the P&L (turnover/profit) is filleted under the small-
# company exemption, so it is not publicly filed. A verdict from this is a SOLVENCY
# read, never a profitability claim (never-false-clean).

# iXBRL concept LOCAL name (namespace-stripped, lowercased) → normalized figure key.
# Only unambiguous entity-level TOTALS — breakdown concepts (Creditors, Equity) carry
# multiple dimensional values per year and are deliberately excluded.
_UK_BS_CONCEPTS: dict[str, str] = {
    "netassetsliabilities": "net_assets",
    "netassetsliabilitiesincludingpensionassetliability": "net_assets",
    "netcurrentassetsliabilities": "net_current_assets",
    "totalassetslesscurrentliabilities": "total_assets_less_current_liabilities",
    "currentassets": "current_assets",
    "fixedassets": "fixed_assets",
    "cashbankonhand": "cash",
}


def _ixbrl_attr(attrs: str, key: str) -> str:
    m = re.search(rf'\b{key}="([^"]*)"', attrs)
    return m.group(1) if m else ""


def _ixbrl_number(inner: str, attrs: str):
    """Parse an ix:nonFraction value: strip nested tags, honour sign="-" and scale.
    A dash means a filed NIL (0.0); unparseable content returns None (skip)."""
    text = re.sub(r"<[^>]+>", "", inner).replace(",", "").replace("\xa0", "").strip()
    if text in ("", "-", "—", "–"):
        return 0.0 if text else None
    try:
        val = float(text)
    except ValueError:
        return None
    if _ixbrl_attr(attrs, "sign") == "-":
        val = -val
    scale = _ixbrl_attr(attrs, "scale")
    if scale:
        try:
            val *= 10 ** int(scale)
        except ValueError:
            pass
    return val


def _parse_ixbrl_balance_sheet(doc: str) -> dict:
    """Pure iXBRL parser → {figure_key: {"current": float, "prior": float}}.

    Maps each ix:nonFraction fact to its reporting date via contextRef, prefers the
    non-dimensional (plain) context when a concept is tagged more than once, and keeps
    the two most recent years (current + prior) so a trend can be read."""
    ctx_date: dict[str, str] = {}
    ctx_plain: dict[str, bool] = {}
    for m in re.finditer(r'<(?:\w+:)?context\b[^>]*\bid="([^"]+)"(.*?)</(?:\w+:)?context>',
                         doc, re.DOTALL | re.IGNORECASE):
        cid, body = m.group(1), m.group(2)
        dm = re.search(r'<(?:\w+:)?(?:instant|endDate)>\s*(\d{4}-\d{2}-\d{2})', body)
        if dm:
            ctx_date[cid] = dm.group(1)
            ctx_plain[cid] = re.search(r'<(?:\w+:)?(?:segment|scenario)\b', body) is None
    collected: dict[str, dict[str, list]] = {}
    for m in re.finditer(r'<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>',
                         doc, re.DOTALL | re.IGNORECASE):
        attrs, inner = m.group(1), m.group(2)
        name = _ixbrl_attr(attrs, "name")
        if not name:
            continue
        key = _UK_BS_CONCEPTS.get(name.split(":")[-1].lower())
        if not key:
            continue
        val = _ixbrl_number(inner, attrs)
        if val is None:
            continue
        date = ctx_date.get(_ixbrl_attr(attrs, "contextRef"), "")
        collected.setdefault(key, {}).setdefault(date, []).append(
            (ctx_plain.get(_ixbrl_attr(attrs, "contextRef"), True), val))
    out: dict[str, dict] = {}
    for key, by_date in collected.items():
        def _pick(d: str):
            plain = [v for p, v in by_date[d] if p]
            return plain[0] if plain else by_date[d][0][1]
        dated = sorted([d for d in by_date if d], reverse=True)
        if dated:
            entry = {"current": _pick(dated[0])}
            if len(dated) > 1:
                entry["prior"] = _pick(dated[1])
            out[key] = entry
        elif by_date:
            out[key] = {"current": _pick(next(iter(by_date)))}
    return out


async def _get_json_url(url: str) -> dict | None:
    """GET a full URL (not a _BASE_URL path) with CH auth → JSON. The document
    metadata link is on a different host (document-api.*)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:  # no-breaker: best-effort accounts-figure fetch
            r = await url_safety.safe_get(client, url, headers=_headers())
            return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def _get_document_content(dm_url: str, mime: str) -> str | None:
    """Fetch a CH document's content (iXBRL). /content 302-redirects to a short-lived
    signed URL that must be fetched WITHOUT the CH auth header (it has its own)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:  # no-breaker: best-effort
            content_url = f"{dm_url}/content"
            # This first request needs CH auth, while the signed redirect must not
            # receive it. Validate explicitly, then make exactly one non-following
            # request; the redirect is fetched separately through safe_get below.
            url_safety.assert_safe_url(content_url)
            r = await client.get(  # no-ssrf-check: content_url validated immediately above
                content_url, headers={**_headers(), "Accept": mime}
            )
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    return None
                # The signed location is controlled by the upstream response. Validate
                # it and every further redirect, while deliberately omitting CH auth.
                r2 = await url_safety.safe_get(client, loc)
                return r2.text if r2.status_code == 200 else None
            return r.text if r.status_code == 200 else None
    except Exception as e:
        logger.debug("CH document content fetch failed: %s", e)
        return None


@fail_wire(module="companies_house", gap_type="api_missing")
async def fetch_accounts_figures(company_number: str) -> dict | None:
    """R-F3016 — BALANCE-SHEET figures (current + prior year) from the latest Companies
    House iXBRL accounts. Never raises.

    Return contract (R-F3017):
      * `{... "figures": {...}}`  — figures extracted.
      * `{... "figures": None, "unavailable_reason": ...}` — an accounts filing EXISTS
        but yields no figures. Reasons: `accounts_not_machine_readable` (filed as a
        scanned/PDF document — every large listed PLC's group accounts; verified live
        2026-07-25: Cohort PLC 05684823's 2025 accounts are a 129-page TIFF scan,
        `Producer: libtiff/tiff2pdf`, zero text layer, no iXBRL resource) or
        `ixbrl_no_balance_sheet_figures` (iXBRL present, no recognised tags).
      * `None` — nothing proven: CH disabled/unavailable, or no accounts filed at all.
    Callers that gate on `fig.get("figures")` are unaffected by the middle case.

    Balance sheet ONLY — small/micro companies fillet the P&L under the small-company
    exemption, so turnover/profit are not publicly filed. Any verdict built from this is
    a solvency read, never a profitability claim (never-false-clean)."""
    number = (company_number or "").strip()
    if not number or not is_enabled() or not _API_KEY:
        return None
    try:
        fh = await _get(f"/company/{number}/filing-history?category=accounts&items_per_page=6")
        if not fh:
            return None
        dm = mime = made_up = atype = None
        # R-F3017 — remember the LATEST accounts filing even when it carries no
        # iXBRL, so a None answer can say WHY (see the unavailable_reason return
        # below) instead of collapsing to an unexplained blank.
        latest_filed = latest_type = latest_formats = None
        latest_pages = None
        for it in (fh.get("items") or []):
            if it.get("category") != "accounts":
                continue
            _dm = (it.get("links") or {}).get("document_metadata")
            if not _dm:
                continue
            meta = await _get_json_url(_dm)
            _resources = list(((meta or {}).get("resources") or {}).keys())
            if latest_filed is None:
                latest_filed = ((it.get("description_values") or {}).get("made_up_date")
                                or it.get("action_date") or it.get("date"))
                latest_type = it.get("description")
                latest_formats = _resources
                latest_pages = (meta or {}).get("pages")
            xh = [m for m in _resources if "xhtml" in m]
            if xh:
                dm, mime = _dm, xh[0]
                made_up = ((it.get("description_values") or {}).get("made_up_date")
                           or it.get("action_date") or it.get("date"))
                atype = it.get("description")
                break
        if not dm:
            # R-F3017 — PDF-only (e.g. a large PLC's group accounts). Previously a
            # bare None, which the report rendered as "financial capacity is
            # unknown" with no reason — indistinguishable from "we never looked"
            # and from "the company filed nothing". Both are false. Return the
            # EVIDENCE we do hold (a filing exists, when, what type, what format)
            # with an explicit machine-readable reason. `figures` stays None so
            # every existing caller (`if not fig or not fig.get("figures")`)
            # behaves exactly as before.
            if latest_filed is None:
                return None      # no accounts filing at all — nothing proven
            return {
                "company_number": number,
                "figures": None,
                "unavailable_reason": "accounts_not_machine_readable",
                "made_up_to": latest_filed,
                "accounts_type": latest_type,
                "document_formats": latest_formats or [],
                "pages": latest_pages,
                "source_url": ("https://find-and-update.company-information.service.gov.uk/"
                               f"company/{number}/filing-history"),
            }
        doc = await _get_document_content(dm, mime)
        if not doc:
            return None
        figures = _parse_ixbrl_balance_sheet(doc)
        if not figures:
            # R-F3017 — iXBRL was there but carried no balance-sheet tags we
            # recognise. A DIFFERENT fact from "filed as a scanned PDF", and the
            # report should not conflate the two.
            return {
                "company_number": number,
                "figures": None,
                "unavailable_reason": "ixbrl_no_balance_sheet_figures",
                "made_up_to": made_up,
                "accounts_type": atype,
                "document_formats": [mime],
                "source_url": ("https://find-and-update.company-information.service.gov.uk/"
                               f"company/{number}/filing-history"),
            }
        return {
            "company_number": number,
            "figures": figures,
            "made_up_to": made_up,
            "accounts_type": atype,
            "source_url": ("https://find-and-update.company-information.service.gov.uk/"
                           f"company/{number}/filing-history"),
        }
    except Exception as e:
        logger.debug("fetch_accounts_figures failed for %s: %s", number, e)
        return None


# ── High-level investigation helper ────────────────────────────────────────

@fail_wire(module="companies_house", gap_type="api_missing")
async def investigate_uk_entity(
    company_number: str | None = None,
    company_name: str | None = None,
) -> dict:
    """Full UK entity investigation — profile + officers + PSC + filings.

    Pass either company_number (direct lookup) or company_name (search first).
    Returns a structured dict ready for the ghost detection checklist.
    """
    if not is_enabled():
        return {"error": "Companies House integration disabled"}

    # Resolve company number from name if needed
    _resolution: dict = {}
    if not company_number and company_name:
        results = await search_companies(company_name, limit=3)
        if not results:
            return {
                "found": False,
                "query": company_name,
                "error": "No UK company found matching this name",
            }
        # R-F3014 — do NOT blindly take results[0]. An Overseas Entity (ROE,
        # "OE"-prefixed) named the same as the trading company ranks high in CH
        # search but has no officers/PSC at the standard endpoints — so a "Cohort plc"
        # DD resolved OE003509 (Jersey) instead of the real 05684823 defence group and
        # reported empty ownership. Prefer the best name match on a non-overseas active
        # company; fall back to overseas only when it is genuinely the best hit.
        # R-F3123 — capture WHY this company was chosen, so the DD can disclose an
        # ambiguous name instead of asserting an identity it merely inferred.
        selected, _resolution = resolve_company_search(company_name, results)
        company_number = (selected or {}).get("company_number")
        # R-F4099 — disclosure after the fact is not a resolution control. Before
        # this gate, an ambiguous name still drove profile, officer, PSC and filing
        # requests for the inferred winner; every downstream fact inherited the
        # unresolved identity. Stop at the shared investigation boundary and make
        # callers request the registration number. Explicit-number investigations
        # never enter this branch and remain unchanged.
        if _resolution.get("ambiguous"):
            return {
                "found": False,
                "resolution_required": True,
                "query": company_name,
                "resolution": _resolution,
                "error": (
                    "Company identity is ambiguous; confirm the Companies House "
                    "registration number before due diligence"
                ),
            }

    if not company_number:
        return {"error": "No company number or name provided"}

    # Fetch all data in parallel-ish (sequential but fast)
    profile = await get_company_profile(company_number)
    if not profile:
        return {
            "found": False,
            "company_number": company_number,
            "error": "Company not found at Companies House",
        }

    officers = await get_officers(company_number)
    psc = await get_psc(company_number)
    filings = await get_filing_history(company_number, limit=10)

    current_officers = [o for o in officers if o.get("is_current")]
    current_psc = [p for p in psc if p.get("is_current")]

    # R-F2726 — ANCHORED controlled_by relationships. A corporate PSC that carries
    # its own registry number is a VERIFIED control edge (Grade A: the controller
    # is identified by a primary-source registry id, not a name match — cf. R-F2703,
    # which correctly refused to publish name-match "relationships"). Individual /
    # legal-person PSCs remain in psc.current as ownership facts, but are NOT emitted
    # as corporate control edges (no anchor → not Grade A).
    #
    # R-F3027 — a corporate controller with NO registry number must not vanish.
    # Companies House makes `identification.registration_number` OPTIONAL for a
    # corporate PSC, and plenty of real UK controllers omit it. Verified live
    # 2026-07-25 on 07833187: `Raven Delta Limited`, kind
    # `corporate-entity-person-with-significant-control`, ownership-of-shares
    # 75-to-100%, right-to-appoint-and-remove-directors — and its `identification`
    # holds only {legal_form, legal_authority}. The Grade-A anchor test below
    # (`regno and "corporate" in kind`) therefore skipped it SILENTLY, so the one
    # entity with 75-100% control of the subject appeared nowhere in the control
    # graph and the DD reported ownership as answered.
    #
    # Grade A is still reserved for anchored edges — resolving this name against the
    # register would be exactly the name-match fabrication R-F2703/R-F2726 removed.
    # The fix is to carry it as an UNANCHORED controller and let the report say so.
    controlled_by = []
    controlled_by_unanchored = []
    for p in current_psc:
        ident = p.get("identification") or {}
        regno = str(ident.get("registration_number") or "").strip()
        kind = str(p.get("kind") or "").lower()
        # R-F3037 — a LEGAL-PERSON PSC is a controller too, and the most
        # consequential kind for a defence DD. Companies House uses
        # `legal-person-person-with-significant-control` for governments,
        # statutory bodies and other non-corporate legal entities — verified live
        # 2026-07-25 on PEARSON ENGINEERING LIMITED (01876136), whose PSC is
        # "Government Companies Authority, State Of Israel" with NO registration
        # number. The kind test below was `"corporate" in kind`, so such a
        # controller matched NEITHER list: not anchored (no regno) and not
        # un-anchored (not "corporate") — it vanished entirely. Foreign STATE
        # ownership of a UK defence supplier is precisely the fact a DD exists to
        # surface, and it was the one shape that could not reach the report.
        _is_controller_kind = ("corporate" in kind) or ("legal-person" in kind)
        if _is_controller_kind and not regno:
            controlled_by_unanchored.append({
                "relationship": "controlled_by",
                "controller_name": p.get("name"),
                "controller_registration_number": "",
                "controller_legal_form": ident.get("legal_form"),
                "controller_country_registered": ident.get("country_registered"),
                "natures_of_control": p.get("natures_of_control", []),
                "anchor": "none — Companies House supplied no registration number",
                "grade": "B",
                # R-F3037 — say WHICH kind. "Corporate controller" would misdescribe a
                # state/statutory body, and for a defence subject that distinction is
                # the whole point of the line.
                "controller_kind": ("legal-person" if "legal-person" in kind else "corporate"),
                "note": (
                    ("State / statutory (legal-person) controller"
                     if "legal-person" in kind else "Corporate controller")
                    + " disclosed by the subject's own PSC filing. NOT resolved to a "
                      "registry entity: Companies House carries no registration number "
                      "for it, and resolving the name against the register would be a "
                      "name match, not an identification."
                ),
            })
        if regno and "corporate" in kind:
            controlled_by.append({
                "relationship": "controlled_by",
                "controller_name": p.get("name"),
                "controller_registration_number": regno,
                "controller_country_registered": ident.get("country_registered"),
                "controller_legal_form": ident.get("legal_form"),
                "natures_of_control": p.get("natures_of_control", []),
                "anchor": "companies_house_psc_identification",
                "grade": "A",  # anchored to a primary-source registry number
            })

    # Ghost detection signals
    ghost_signals = []
    creation_date = profile.get("date_of_creation", "")
    if creation_date:
        try:
            created = datetime.strptime(creation_date, "%Y-%m-%d")
            age_days = (datetime.now() - created).days
            if age_days < 730:  # less than 2 years
                ghost_signals.append(
                    f"RECENT INCORPORATION: {creation_date} ({age_days} days ago)"
                )
        except ValueError:
            pass

    if not current_officers:
        ghost_signals.append("NO CURRENT DIRECTORS listed at Companies House")

    if not current_psc:
        ghost_signals.append("NO PSC (beneficial ownership) disclosed")

    if len(filings) == 0:
        ghost_signals.append("ZERO FILINGS — no accounts or confirmation statements")

    if profile.get("has_been_liquidated"):
        ghost_signals.append("COMPANY HAS BEEN LIQUIDATED")

    if profile.get("has_insolvency_history"):
        ghost_signals.append("INSOLVENCY HISTORY on record")

    # Check if registered at a known formation agent address
    addr = profile.get("registered_address", {})
    addr_line = f"{addr.get('line1', '')} {addr.get('postal_code', '')}".strip()
    _FORMATION_AGENT_MARKERS = [
        "128 city road", "20-22 wenlock road", "71-75 shelton street",
        "86-90 paul street", "Unit 4E Enterprise Court",
        "suite", "floor", "virtual office",
    ]
    addr_lower = addr_line.lower()
    for marker in _FORMATION_AGENT_MARKERS:
        if marker in addr_lower:
            ghost_signals.append(
                f"REGISTERED AT KNOWN FORMATION AGENT ADDRESS: {addr_line}"
            )
            break

    status = profile.get("company_status", "")
    if status and status != "active":
        ghost_signals.append(f"COMPANY STATUS: {status} (not active)")

    investigation = {
        "found": True,
        "company_number": company_number,
        # R-F3123 — how this company_number was arrived at. Empty when the caller
        # supplied the number directly (nothing was inferred, nothing to disclose).
        "resolution": _resolution,
        "profile": profile,
        "officers": {
            "current": current_officers,
            "past": [o for o in officers if not o.get("is_current")],
            "total": len(officers),
        },
        "psc": {
            "current": current_psc,
            "total": len(psc),
        },
        "controlled_by": controlled_by,  # R-F2726 — anchored (Grade-A) corporate control edges
        # R-F3027 — corporate controllers CH gave us no registration number for.
        # Disclosed, un-anchored, and never silently dropped.
        "controlled_by_unanchored": controlled_by_unanchored,
        "filings": {
            "recent": filings,
            "total_shown": len(filings),
        },
        "ghost_signals": ghost_signals,
        "ghost_signal_count": len(ghost_signals),
        "risk_note": (
            f"⚠️ {len(ghost_signals)} ghost detection signal(s) found"
            if ghost_signals else "No ghost detection signals from Companies House data"
        ),
    }

    # Brain signal — every UK entity investigation is a primary-source
    # compliance signal. Ghost-signal count feeds capability_gap so the
    # predictor warns on similar formation-agent / shell-company patterns.
    try:
        from . import brain_hook as _bh
        company_name_resolved = profile.get("company_name", "") or company_number
        await _bh.absorb(
            module="companies_house",
            summary=(
                f"Companies House investigation: {company_name_resolved} "
                f"({company_number}) — {len(ghost_signals)} ghost signals, "
                f"{len(current_officers)} directors, {len(current_psc)} PSC"
            ),
            detail="; ".join(ghost_signals[:6])[:1500] if ghost_signals else
                   f"profile={profile.get('company_status','?')}, accounts={profile.get('accounts',{}).get('next_due','')}",
            entity_name=company_name_resolved,
            success=True,
            gap_type=("shell_company_pattern" if len(ghost_signals) >= 3 else None),
            gap_detail=(f"{len(ghost_signals)} ghost signals on {company_name_resolved}: "
                        f"{', '.join(ghost_signals[:3])}"
                        if len(ghost_signals) >= 3 else None),
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return investigation


@fail_wire(module="companies_house", gap_type="api_missing")
def format_for_prompt(investigation: dict) -> str:
    """Format a CH investigation result as a context block for the LLM prompt."""
    if investigation.get("resolution_required"):
        resolution = investigation.get("resolution") or {}
        candidates = resolution.get("candidates") or []
        listed = "; ".join(
            f"{c.get('title')} ({c.get('status')}, {c.get('company_number')})"
            for c in candidates[:4]
        )
        reasons = " ".join(str(r) for r in resolution.get("reasons") or [])
        return (
            "\n[COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]\n"
            f"I cannot safely identify '{resolution.get('query') or investigation.get('query')}'. "
            f"{reasons}\nCandidates: {listed}\n"
            "Ask the user to confirm the Companies House registration number. Do not "
            "continue due diligence on an inferred company."
        )
    if not investigation.get("found"):
        return ""

    profile = investigation.get("profile", {})
    officers = investigation.get("officers", {})
    psc = investigation.get("psc", {})
    filings = investigation.get("filings", {})
    signals = investigation.get("ghost_signals", [])

    lines = [
        "\n[COMPANIES HOUSE — UK REGISTRY DATA]",
        f"Company: {profile.get('company_name')} ({profile.get('company_number')})",
        f"Status: {profile.get('company_status')} | Type: {profile.get('company_type')}",
        f"Incorporated: {profile.get('date_of_creation')}",
        f"SIC codes: {', '.join(profile.get('sic_codes', []))}",
    ]

    addr = profile.get("registered_address", {})
    addr_str = ", ".join(v for v in [addr.get("line1"), addr.get("locality"), addr.get("postal_code")] if v)
    if addr_str:
        lines.append(f"Registered address: {addr_str}")

    if officers.get("current"):
        lines.append(f"\nDirectors ({len(officers['current'])} current):")
        for o in officers["current"][:5]:
            lines.append(f"  - {o['name']} ({o.get('role', 'director')}, appointed {o.get('appointed_on', '?')})")

    if psc.get("current"):
        lines.append(f"\nPSC / Beneficial Ownership ({len(psc['current'])} current):")
        for p in psc["current"][:5]:
            controls = ", ".join(p.get("natures_of_control", []))[:100]
            lines.append(f"  - {p['name']} — {controls}")
    else:
        lines.append("\nPSC: NONE DISCLOSED")

    # R-F2726 — anchored control edges: a corporate PSC identified by its own
    # registry number is a VERIFIED "controlled_by" relationship (Grade A). Present
    # it as such so the LLM can state it as fact (with the anchor), never as a guess.
    controlled_by = investigation.get("controlled_by") or []
    if controlled_by:
        lines.append(f"\nAnchored control (VERIFIED via corporate-PSC registry number):")
        for c in controlled_by[:5]:
            natures = ", ".join(c.get("natures_of_control", []))[:80]
            lines.append(
                f"  - Controlled by {c.get('controller_name')} "
                f"(reg {c.get('controller_registration_number')}, "
                f"{c.get('controller_country_registered') or '?'})"
                + (f" — {natures}" if natures else "")
            )

    if filings.get("recent"):
        lines.append(f"\nRecent filings ({filings['total_shown']}):")
        for f_item in filings["recent"][:5]:
            lines.append(f"  - {f_item.get('date', '?')}: {f_item.get('description', f_item.get('category', '?'))}")

    if signals:
        lines.append(f"\n⚠️ GHOST DETECTION SIGNALS ({len(signals)}):")
        for s in signals:
            lines.append(f"  - {s}")

    return "\n".join(lines)

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
