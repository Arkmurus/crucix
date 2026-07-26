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

async def _get(path: str, _attempt: int = 0) -> dict | None:
    """GET from Companies House API. Returns parsed JSON, or None on genuine 404 /
    persistent failure. R-F2511 — 429 (rate-limit) and timeouts are TRANSIENT and are
    RETRIED with backoff (respecting Retry-After) rather than silently returning empty;
    on persistent failure `_mark_unavailable` flags the async context so the caller can
    surface a data-gap (never-false-clean). Only a real 404 returns None-as-not-found."""
    if not is_enabled():
        return None
    url = f"{_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:  # no-breaker: Companies House is a free authoritative source; breaker belongs at the caller (DD pipeline)
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                if _attempt < _MAX_RETRIES:
                    _ra = (resp.headers.get("Retry-After") or "").strip()
                    _wait = min(8.0, float(_ra)) if _ra.isdigit() else _BACKOFF_BASE * (_attempt + 1)
                    logger.warning("Companies House rate limited (429) — retry %d/%d after %.1fs (%s)",
                                   _attempt + 1, _MAX_RETRIES, _wait, path)
                    await asyncio.sleep(_wait)
                    return await _get(path, _attempt + 1)
                logger.warning("Companies House rate limited (429) — exhausted %d retries (%s)", _MAX_RETRIES, path)
                _mark_unavailable("rate_limited")
                return None
            if resp.status_code != 200:
                logger.debug("CH API %s returned %d", path, resp.status_code)
                return None
            return resp.json()
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
        # Transient network/timeout — retry with backoff before giving up.
        if _attempt < _MAX_RETRIES:
            await asyncio.sleep(_BACKOFF_BASE * (_attempt + 1))
            return await _get(path, _attempt + 1)
        logger.debug("CH API request failed after %d retries: %s", _MAX_RETRIES, e)
        _mark_unavailable("timeout")
        return None
    except Exception as e:
        logger.debug("CH API request failed: %s", e)
        return None


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
                "name_match": round(_company_name_match(query, str(r.get("title") or "")), 3),
            }
            for r in results
        ]
        top = max((c["name_match"] for c in scored), default=0.0)
        tied = [c for c in scored if c["name_match"] >= top - 1e-9]
        win_num = str(winner.get("company_number") or "")
        win_status = str(winner.get("company_status") or "").lower()
        win_dissolved = "active" not in win_status
        active_alts = [c for c in scored
                       if c["company_number"] != win_num and "active" in c["status"].lower()]
        reasons: list[str] = []
        if len(tied) > 1:
            reasons.append(
                f"{len(tied)} candidates share the top name match ({top:.2f}) — the "
                "choice between them rests on status and search rank, not on the name")
        if win_dissolved and active_alts:
            reasons.append(
                f"the selected company is {win_status or 'not active'} while "
                f"{len(active_alts)} ACTIVE company/companies match this name "
                f"(e.g. {active_alts[0]['company_number']} {active_alts[0]['title']}) — "
                "confirm which legal entity is the intended counterparty")
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
            r = await client.get(url, headers=_headers())
            return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def _get_document_content(dm_url: str, mime: str) -> str | None:
    """Fetch a CH document's content (iXBRL). /content 302-redirects to a short-lived
    signed URL that must be fetched WITHOUT the CH auth header (it has its own)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:  # no-breaker: best-effort
            r = await client.get(f"{dm_url}/content", headers={**_headers(), "Accept": mime})
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    return None
                r2 = await client.get(loc)  # signed URL — no CH auth header
                return r2.text if r2.status_code == 200 else None
            return r.text if r.status_code == 200 else None
    except Exception as e:
        logger.debug("CH document content fetch failed: %s", e)
        return None


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
        company_number = (
            _pick_best_company(company_name, results, _resolution) or {}
        ).get("company_number")

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
