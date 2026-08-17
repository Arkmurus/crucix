"""R-F2322 — real financial-health DD (Phase 1: SEC EDGAR).

Counterparty DD is not decision-grade without a financial-health read. Before this,
ARIA had only UK Companies-House shell-flag detection (financial_dd.py) + country risk;
there was NO revenue / ratio / balance-sheet / distress analysis for any entity.

This module answers, for a US-listed counterparty, from FREE SEC EDGAR XBRL data
(§6 — no paid source, no key; SEC only requires a descriptive User-Agent):
  - Is it solvent + a going concern?  (Altman Z''-score + distress flags)
  - Is it healthy or distressed?      (liquidity / leverage / profitability ratios + trend)
  - Multi-year revenue / net-income / balance-sheet series.

HONESTY (mirrors ARIA's never-false-clean rule, applied to financials): when the entity
is not found in EDGAR (private / non-US / no CIK match) or companyfacts is empty, the
result is an explicit UNKNOWN / NOT_PUBLICLY_FILED — NEVER a fabricated or falsely-healthy
verdict. "No data" and "healthy" are distinct outcomes (data_available flag).

Phase 2 (later): UK Companies House iXBRL accounts. Phase 3: capitalisation-vs-deal-size,
peer benchmarking.
"""
from __future__ import annotations

import asyncio          # R-F3124 — bounded document read + model call
import datetime as _dt
import logging
import os               # R-F3146 — temp file for the already-fetched issuer document
import re   # R-F3028 — sentence-split for superseded-summary replacement
import tempfile         # R-F3146 — ditto; avoids re-downloading a ~300-page report
from typing import Any

from .sources import sec_edgar as _sec
from .sources import _common
from .circuit_breaker import get_breaker
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.financial_health")

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

# us-gaap concept tags, in fallback order (first tag with data wins). Companies tag the
# same line item differently across filers/years, so each metric needs a candidate list.
_TAGS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "ebit": ["OperatingIncomeLoss",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
}

# Income-statement (FLOW) metrics span a period, so a valid annual figure must cover
# ~one fiscal year. Balance-sheet (INSTANT) metrics are point-in-time (no `start`), so
# they are NOT duration-checked. This split is what lets us reject transition-period
# (10-KT) and bankruptcy/acquisition stub rows that carry fp=FY but cover a partial year.
_FLOW_TAGS: set[str] = {"revenue", "net_income", "ebit"}


async def _resolve_cik(name: str) -> tuple[str, str] | None:
    """(cik10, matched_title) for ``name`` via the SEC ticker map + the R-F572 name-overlap
    gate (so 'Rosoboronexport' does NOT false-match 'E.ON SE'). None when no confident match."""
    tickers = await _sec._load_tickers()
    if not tickers:
        return None
    scored = _common.fuzzy_filter(tickers, name, name_key="title", threshold=0.85, max_hits=5)
    if not scored:
        return None
    from ._sanctions_classify import _tokenize_entity_name
    q_tokens = _tokenize_entity_name(name)
    for hit in scored:
        cand = (hit.get("title") or "").strip()
        if not cand:
            continue
        score = float(hit.get("_match_score") or 0.0)
        cand_tokens = _tokenize_entity_name(cand)
        shared = q_tokens & cand_tokens
        # Tightened (R-F2322 review): attaching the WRONG company's financials is a
        # decision-grade error, so a single-token match needs high similarity too.
        if score >= 0.95 or len(shared) >= 2 or (len(shared) == 1 and len(next(iter(shared))) >= 5 and score >= 0.92):
            cik = hit.get("cik_str") or (str(hit.get("cik")).zfill(10) if hit.get("cik") is not None else None)
            if cik:
                return str(cik).zfill(10), cand
    return None


async def _fetch_company_facts(cik10: str) -> dict | None:
    return await _common.http_get_json(
        _FACTS_URL.format(cik10=cik10), headers={"User-Agent": _sec._UA}
    )


def _is_annual_duration(start: str, end: str) -> bool:
    """True iff [start, end] spans ~one fiscal year (350–380 days — covers 52/53-week
    fiscal calendars and leap years). Rejects BOTH short stubs (quarterly/YTD or a
    transition-10-KT part-year) AND long extended-year transition periods (>1yr) — either
    can carry fp=FY + form 10-K* and would otherwise be mistaken for the annual figure,
    UNDER- or OVER-stating revenue / net income / EBIT. Missing/unparseable start fails
    closed (rejected) so only a verified ~annual period is ever used for a FLOW metric."""
    if not start or not end:
        return False
    try:
        days = (_dt.date.fromisoformat(end[:10]) - _dt.date.fromisoformat(start[:10])).days
    except Exception:
        return False
    return 350 <= days <= 380


def _extract_annual(facts: dict, candidates: list[str], require_duration: bool = False) -> dict[int, float]:
    """{fiscal_year: value} MERGED across ALL candidate tags (FY 10-K only).

    Filers migrate concept tags across eras (e.g. Apple: legacy `Revenues` pre-ASC-606,
    then `RevenueFromContractWithCustomerExcludingAssessedTax`), so we UNION the candidates
    by year — returning the first non-empty tag would give a stale, truncated series. Per
    fiscal year we keep the entry with the latest `end` (handles restatements / amended
    filings).

    ``require_duration`` (set only for FLOW / income-statement tags via `_FLOW_TAGS`)
    additionally requires each row to span ~one fiscal year (`_is_annual_duration`), so a
    partial-year (10-KT / stub) OR extended-year row cannot win the latest-`end` tie-break
    and be reported as the annual figure. Balance-sheet INSTANT tags leave it False — they
    are point-in-time (no `start`) and must never be duration-filtered."""
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    merged: dict[int, tuple[str, float]] = {}
    for tag in candidates:
        node = gaap.get(tag)
        if not isinstance(node, dict):
            continue
        for e in ((node.get("units") or {}).get("USD") or []):
            if not (e.get("form") or "").startswith("10-K") or e.get("fp") != "FY":
                continue
            fy = e.get("fy")
            val = e.get("val")
            if fy is None or val is None:
                continue
            end = e.get("end") or ""
            if require_duration and not _is_annual_duration(e.get("start") or "", end):
                continue  # not a verified ~annual period — exclude from the flow series
            prev = merged.get(int(fy))
            if prev is None or end >= prev[0]:
                merged[int(fy)] = (end, float(val))
    return {y: v[1] for y, v in merged.items()}


def _safe_div(a, b):
    try:
        if a is None or b is None or b == 0:
            return None
        return a / b
    except Exception:
        return None


def _compute_ratios(f: dict) -> dict:
    """Ratios from one year's figures. None on any missing/zero denominator (never fabricate)."""
    return {
        "current_ratio": _round(_safe_div(f.get("assets_current"), f.get("liabilities_current"))),
        "debt_to_equity": _round(_safe_div(f.get("liabilities"), f.get("equity"))) if (f.get("equity") or 0) > 0 else None,
        "liabilities_to_assets": _round(_safe_div(f.get("liabilities"), f.get("assets"))),
        "net_margin": _round(_safe_div(f.get("net_income"), f.get("revenue"))),
        "return_on_assets": _round(_safe_div(f.get("net_income"), f.get("assets"))),
        "return_on_equity": _round(_safe_div(f.get("net_income"), f.get("equity"))) if (f.get("equity") or 0) > 0 else None,
    }


def _round(x, n=4):
    return round(x, n) if isinstance(x, (int, float)) else None


def _altman_z2(f: dict):
    """Altman Z''-score (the model for non-manufacturers / private / emerging-market firms —
    the right variant for a general counterparty). Needs assets_current, liabilities_current,
    assets, retained_earnings, ebit, equity, liabilities. Returns (score, zone) or (None, None).

    Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
      X1 = (current assets - current liabilities) / total assets
      X2 = retained earnings / total assets
      X3 = EBIT / total assets
      X4 = book equity / total liabilities
    Zones: > 2.6 SAFE · 1.1–2.6 GREY · < 1.1 DISTRESS.
    """
    ta = f.get("assets")
    tl = f.get("liabilities")
    if not ta or ta == 0 or tl is None:
        return None, None
    ca, cl = f.get("assets_current"), f.get("liabilities_current")
    re_, ebit, eq = f.get("retained_earnings"), f.get("ebit"), f.get("equity")
    if None in (ca, cl, re_, ebit, eq):
        return None, None
    x1 = (ca - cl) / ta
    x2 = re_ / ta
    x3 = ebit / ta
    x4 = _safe_div(eq, tl)
    if x4 is None:
        return None, None
    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
    zone = "SAFE" if z > 2.6 else ("GREY" if z >= 1.1 else "DISTRESS")
    return round(z, 2), zone


def _distress_flags(latest: dict, series: dict) -> list[str]:
    flags = []
    if (latest.get("equity") is not None) and latest["equity"] < 0:
        flags.append("negative shareholders' equity")
    if (latest.get("net_income") is not None) and latest["net_income"] < 0:
        flags.append("net loss in latest fiscal year")
    cr = _safe_div(latest.get("assets_current"), latest.get("liabilities_current"))
    if cr is not None and cr < 1:
        flags.append(f"current ratio below 1.0 ({cr:.2f}) — short-term liquidity strain")
    lta = _safe_div(latest.get("liabilities"), latest.get("assets"))
    if lta is not None and lta > 0.8:
        flags.append(f"high leverage — liabilities are {lta:.0%} of assets")
    # revenue declining 2+ consecutive years
    rev_years = sorted(y for y in series if series[y].get("revenue") is not None)
    if len(rev_years) >= 3:
        r = [series[y]["revenue"] for y in rev_years[-3:]]
        if r[0] > r[1] > r[2]:
            flags.append("revenue declined for 2 consecutive years")
    return flags


def _verdict(data_available: bool, z_zone, flags: list[str], has_financials: bool,
             has_solvency_signal: bool = True) -> str:
    if not data_available or not has_financials:
        return "UNKNOWN"
    n = len(flags)
    critical = any("negative shareholders" in f for f in flags)
    if z_zone == "DISTRESS" or critical or n >= 3:
        return "DISTRESSED"
    if z_zone == "GREY" or n >= 1:
        return "WEAK"
    # A POSITIVE verdict must rest on a real solvency signal (Altman Z'' or a balance-sheet
    # leverage/liquidity ratio) — not just an income line. Without one, stay UNKNOWN rather
    # than assert health on thin data (never-false-clean).
    if not has_solvency_signal:
        return "UNKNOWN"
    if z_zone == "SAFE" and n == 0:
        return "STRONG"
    return "STABLE"


def _fmt_money(v):
    if not isinstance(v, (int, float)):
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:.2f}B"
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


async def _assess_sec_edgar(name: str, cik: str | None = None) -> dict:
    """SEC EDGAR structured-financials assessment for ``name``.

    Result shape (always returns a dict; NEVER raises):
      {source, entity, cik, data_available: bool, reason (when not),
       currency, latest_fy, financials: {year: {revenue, net_income, ...}},
       ratios: {...}, altman_z: float|None, altman_zone, distress_flags: [...],
       health_verdict: STRONG|STABLE|WEAK|DISTRESSED|UNKNOWN, summary: str,
       latest_fy_age_years: int|None, financials_are_stale: bool}

    R-F3748 (DR-1 D-06) — `latest_fy_age_years` / `financials_are_stale` say how
    OLD the position is. A verdict from a five-year-old filing is a LAST KNOWN
    position, not a current one, and must not be read as the latter. The verdict
    itself is never altered by age — that would invent a finding.
    """
    base = {
        "source": "sec_edgar_financials",
        "entity": name,
        "cik": cik,
        "data_available": False,
        "currency": "USD",
        "financials": {},
        "ratios": {},
        "altman_z": None,
        "altman_zone": None,
        "distress_flags": [],
        "health_verdict": "UNKNOWN",
    }
    cb = get_breaker("financial:sec_edgar", failure_threshold=5, cooldown_seconds=300)
    if cb.is_open():
        base["reason"] = "SEC EDGAR temporarily unavailable (cooldown) — financial health UNKNOWN, not clean"
        base["summary"] = "Financial health could not be verified (source cooling). Treat as UNKNOWN."
        return base
    try:
        cik10 = str(cik).zfill(10) if cik else None
        matched_title = name
        if not cik10:
            resolved = await _resolve_cik(name)
            if not resolved:
                base["reason"] = "not a US-listed filer (no SEC EDGAR match) — financials not publicly filed"
                base["summary"] = (
                    f"{name} is not found in SEC EDGAR (not US-listed). Public financial statements "
                    "are NOT available from this source — financial health is UNKNOWN, not a clean bill. "
                    "For UK entities, Companies House accounts apply (Phase 2)."
                )
                cb.record_success()
                return base
            cik10, matched_title = resolved
        base["cik"] = cik10
        base["matched_title"] = matched_title

        facts = await _fetch_company_facts(cik10)
        if not isinstance(facts, dict) or not (facts.get("facts") or {}).get("us-gaap"):
            cb.record_failure(reason="empty_facts")
            base["reason"] = "SEC EDGAR returned no XBRL financial facts for this CIK"
            base["summary"] = "No structured financials available from SEC EDGAR — UNKNOWN, not clean."
            return base

        # Build per-metric annual series → per-year financials.
        per_metric = {m: _extract_annual(facts, tags, require_duration=(m in _FLOW_TAGS))
                      for m, tags in _TAGS.items()}
        years = sorted({y for s in per_metric.values() for y in s}, reverse=True)[:5]
        if not years:
            cb.record_failure(reason="no_annual")
            base["reason"] = "no annual (10-K) figures in SEC EDGAR facts"
            base["summary"] = "No annual financial statements found — UNKNOWN, not clean."
            return base
        series = {y: {m: per_metric[m].get(y) for m in _TAGS} for y in years}
        latest_fy = years[0]
        latest = series[latest_fy]

        ratios = _compute_ratios(latest)
        z, zone = _altman_z2(latest)
        flags = _distress_flags(latest, series)
        has_fin = any(v is not None for v in latest.values())
        # A positive (STABLE/STRONG) verdict needs a real solvency signal, not just an
        # income line — Altman Z'' computed, or a balance-sheet leverage/liquidity ratio.
        has_solvency = (z is not None) or (ratios.get("current_ratio") is not None) \
            or (ratios.get("liabilities_to_assets") is not None) or (ratios.get("debt_to_equity") is not None)
        verdict = _verdict(True, zone, flags, has_fin, has_solvency_signal=has_solvency)
        if verdict == "UNKNOWN":
            # income-only data → not enough to assert health; keep the figures but be honest.
            base.update({
                "data_available": False,
                "reason": "only income data available (no balance sheet) — insufficient to assess solvency",
                "partial_financials": {str(y): {k: v for k, v in series[y].items() if v is not None} for y in years},
            })
            base["summary"] = (f"{matched_title}: partial SEC data (income only, no balance sheet) — "
                               "financial health UNKNOWN, not a clean bill.")
            cb.record_success()
            return base

        # ── R-F3748 (DR-1 D-06) — stamp the AGE, not just the year ────────────
        #
        # This module's whole discipline is "UNKNOWN, not clean" (see the header):
        # absent data never reads as healthy. But data that EXISTS and is OLD had
        # no such guard. `latest_fy` was recorded, and nothing anywhere compared
        # it to the current year — a repo-wide search found no age arithmetic on
        # it at all. So a STABLE verdict computed from a five-year-old filing was
        # returned with exactly the same authority as one from last quarter, and
        # the reader had to notice the FY and do the subtraction themselves.
        #
        # That is the same failure this file already refuses in the absent case:
        # a verdict that claims more currency than its evidence supports. D-06
        # asks for "LAST_KNOWN_WITH_AGE or refuse"; the vintage was there, the AGE
        # was not.
        #
        # The verdict itself is NOT altered. Downgrading STABLE to WEAK because a
        # filing is old would invent a financial finding — the fabrication this
        # module exists to prevent. Age is reported ALONGSIDE the verdict so the
        # reader can discount it, which is what "LAST_KNOWN_WITH_AGE" means.
        _fy_age = None
        try:
            if isinstance(latest_fy, int) or str(latest_fy).isdigit():
                _fy_age = _dt.datetime.now(_dt.timezone.utc).year - int(latest_fy)
        except Exception:       # a malformed FY must not break the assessment
            _fy_age = None

        base.update({
            "data_available": True,
            "latest_fy": latest_fy,
            "latest_fy_age_years": _fy_age,
            "financials_are_stale": (_fy_age is not None and _fy_age >= 2),
            "financials": {str(y): {k: v for k, v in series[y].items() if v is not None} for y in years},
            "ratios": {k: v for k, v in ratios.items() if v is not None},
            "altman_z": z,
            "altman_zone": zone,
            "distress_flags": flags,
            "health_verdict": verdict,
        })
        base["summary"] = _build_summary(matched_title, latest_fy, latest, ratios, z, zone, flags, verdict)
        # R-F3748 — the age must reach the READER, not just the payload. A caller
        # that renders only `summary` (and several do) would otherwise still show
        # an aged verdict as current.
        if base.get("financials_are_stale"):
            base["summary"] += (
                f" NOTE: this is the LAST KNOWN position, from FY{latest_fy} — "
                f"{_fy_age} years old. It is not evidence of the company's "
                f"current financial state; more recent filings may exist "
                f"elsewhere or may be overdue."
            )
        cb.record_success()
        try:
            wire_success(
                module="financial_health",
                summary=f"financial DD {matched_title}: {verdict} (Z''={z}, FY{latest_fy})",
                source_id="financial_health:sec_edgar",
            )
        except Exception:
            pass
        return base
    except Exception as e:
        cb.record_failure(reason="exception")
        logger.warning("financial_health assess failed for %r: %s", name, e)
        try:
            wire_failure(module="financial_health", detail=f"assess failed: {e}",
                         gap_type="source_failure", source="financial_health")
        except Exception:
            pass
        base["reason"] = f"financial health check errored: {e}"
        base["summary"] = "Financial health could not be verified (error) — UNKNOWN, not clean."
        return base


def _build_summary(title, fy, latest, ratios, z, zone, flags, verdict) -> str:
    parts = [f"{title} — FY{fy} (SEC EDGAR)."]
    rev, ni = latest.get("revenue"), latest.get("net_income")
    if rev is not None:
        parts.append(f"Revenue {_fmt_money(rev)}" + (f", net {'income' if (ni or 0) >= 0 else 'loss'} {_fmt_money(ni)}" if ni is not None else "") + ".")
    cr = ratios.get("current_ratio")
    de = ratios.get("debt_to_equity")
    bits = []
    if cr is not None:
        bits.append(f"current ratio {cr:.2f}")
    if de is not None:
        bits.append(f"debt/equity {de:.2f}")
    if bits:
        parts.append("Ratios: " + ", ".join(bits) + ".")
    if z is not None:
        parts.append(f"Altman Z''={z} ({zone}).")
    if flags:
        parts.append("Flags: " + "; ".join(flags) + ".")
    parts.append(f"Health: {verdict}.")
    return " ".join(parts)


async def _search_financial_footprint(name: str, jurisdiction_iso2: str = "") -> dict:
    """The SEARCH element (operator directive): cross-jurisdiction financial-info discovery
    via web search — surfaces publicly-available financial references (annual reports,
    filings, disclosures, financial news) for entities SEC EDGAR doesn't cover, so ARIA
    adds value in ANY jurisdiction, not only US/UK. Runs inside the DD context, so it uses
    the same (Brave-primary) search pipeline. Does NOT fabricate figures — returns the
    sources found so the DD can cite/verify them."""
    try:
        from . import web_search
    except Exception:
        return {"found": False, "sources": []}
    q = f'"{name}" (annual report OR financial statements OR revenue OR turnover OR filing)'
    try:
        hits = await web_search.search(q, max_results=10)
    except Exception:
        return {"found": False, "sources": []}
    # R-F2346 — RELEVANCE GATE. web_search also returns internal RAG hits (memory:// URLs)
    # and generic keyword matches (e.g. a dance school, a council directory) that are NOT
    # about this entity — surfacing them as "financial references" is misleading noise on a
    # decision-grade report. Keep only real http(s) URLs whose title/snippet/url actually
    # reference the entity (same relevance discipline the digital layer uses, R-F1631).
    from ._sanctions_classify import _tokenize_entity_name
    _ent_tokens = _tokenize_entity_name(name)

    # R-F2492 — require a STRONG financial-DOCUMENT signal. The old set included bare
    # generic tokens (revenue / turnover / results / earnings / accounts) that match
    # marketing pages, sports "results", login "accounts" etc., so a generic web hit
    # (e.g. kara5.com) got rendered as a "financial reference" on a decision-grade
    # report. Keep only terms that denote an actual financial document / disclosure.
    _financial_terms = {
        "annual report", "annual accounts", "statutory accounts",
        "financial statement", "financial statements", "financial results",
        "audited accounts", "audited financial", "balance sheet",
        "income statement", "cash flow statement", "profit and loss",
        "form 10-k", "form 20-f", "form 6-k", "10-k", "20-f",
        "sec filing", "regulatory filing", "annual filing",
        "disclosure statement", "financial disclosure",
        "prospectus", "quarterly report", "interim report", "earnings report",
    }
    # R-F2492 — block social / professional-profile domains WHOLESALE (not just the
    # /in/ /pub/ sub-paths): a LinkedIn *company* page, a Facebook page, etc. is a
    # profile, never a financial document. Substring match on the host segment.
    _blocked_financial_domains = {
        "linkedin.com", "facebook.com", "instagram.com", "x.com",
        "twitter.com", "youtube.com", "tiktok.com", "pinterest.com",
        "medium.com", "reddit.com", "wikipedia.org",
    }

    def _relevant(title: str, snippet: str, url: str) -> bool:
        lower_url = (url or "").lower()
        if any(blocked in lower_url for blocked in _blocked_financial_domains):
            return False
        if not _ent_tokens:
            entity_relevant = True  # name too short to discriminate — don't over-filter
        else:
            hay_entity = f"{title} {snippet} {url}".lower()
            entity_relevant = any(tok in hay_entity for tok in _ent_tokens)
        if not entity_relevant:
            return False
        hay = f"{title} {snippet} {url}".lower()
        return any(term in hay for term in _financial_terms)

    sources = []
    for r in (hits or []):
        url = (getattr(r, "url", "") or "").strip()
        title = (getattr(r, "title", "") or "").strip()
        snippet = (getattr(r, "snippet", "") or "").strip()
        if not (url and title):
            continue
        if not url.lower().startswith(("http://", "https://")):
            continue  # drop memory:// / rag:// / non-web pseudo-URLs — not citable references
        if not _relevant(title, snippet, url):
            continue  # drop entity-irrelevant keyword matches
        sources.append({"title": title[:160], "url": url, "snippet": snippet[:240]})
        if len(sources) >= 6:
            break
    return {
        "found": bool(sources),
        "sources": sources,
        "summary": (f"{len(sources)} public financial reference(s) found via search"
                    if sources else "no entity-relevant public financial references found via search"),
    }


# ── R-F3124 — THE ONE REMAINING ROUTE TO A LISTED GROUP'S FINANCIALS ────────
#
# R-F3017 established, by live probe, that four routes are DEAD for a large PLC:
#   1. CH iXBRL           — large groups file none at all
#   2. CH accounts PDF    — a 129-page TIFF SCAN (pypdf extracts 0 chars)
#   3. FCA NSM API        — open and free, but IGNORES the query (match_all, uniform
#                           score 1.0; only from/size honoured) so an issuer cannot
#                           be targeted
#   4. Subsidiary walk    — a FABRICATION TRAP: walking Cohort's directors surfaced
#                           THALES entities via a shared non-exec, and a naive
#                           "group member" heuristic would have billed Thales UK's
#                           finances as Cohort's
# and concluded: "the only remaining route is the issuer's own published annual
# report — search-located, non-deterministic, needs an arithmetic self-check".
#
# That route is buildable now because it needs exactly the two surfaces the DD is
# pinned to: BRAVE finds the issuer's report, CLAUDE reads it. `financial_capacity`
# is the question the Mitie report left UNRESOLVED, and it is the one a counterparty
# decision most often turns on.
#
# NON-DETERMINISTIC MEANS GUARDED, NOT TRUSTED. An LLM reading a PDF can hallucinate
# a number, and a fabricated solvency figure is the single worst output this product
# could emit. Four gates, ALL of which must pass before a figure is allowed to answer
# the question — any failure leaves the existing honest UNKNOWN exactly as it was:
#
#   G1 PROVENANCE  the document must be on the issuer's OWN domain (an annual report
#                  hosted anywhere else is not the issuer speaking)
#   G2 TEXT LAYER  a scanned PDF yields no text; we do NOT OCR a balance sheet and
#                  then call it a solvency assessment (route 2's lesson)
#   G3 GROUNDING   every figure must arrive with a verbatim quote from the document
#   G4 ARITHMETIC  net_assets must equal total_assets − total_liabilities within
#                  tolerance. This is the check R-F3017 prescribed: a model that
#                  invents figures will not produce a balance sheet that balances.
_ISSUER_FIN_TOLERANCE = 0.02          # 2% — rounding/presentation differences only
_ISSUER_FIN_MAX_CHARS = 120_000       # cap what we hand the model
#: R-F3125 — a browser-shaped UA. Issuer sites commonly refuse a bare client; that is
#: an ACCESS obstacle to report honestly, never evidence about the filing itself.
_ISSUER_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _issuer_domain_matches(url: str, name: str) -> bool:
    """R-F3124 G1 — is this document on the issuer's OWN website?

    A third party's summary of a company's accounts is not the company's accounts.
    Matches a distinctive name token against the registrable domain, so
    `mitie.com/.../Mitie-Annual-Report-2026.pdf` passes for "Mitie Group PLC" while
    `investing.com/equities/mitie-group` does not."""
    from ._sanctions_classify import _tokenize_entity_name
    try:
        host = (url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    except Exception:
        return False
    host = host.split(":")[0]
    if not host:
        return False
    # Match against the WHOLE host, flattened. Taking a single "registrable label"
    # is wrong in both directions: `www.mitie.com` yields "www", and `mitie.co.uk`
    # yields "co". Flattening also keeps the test on the HOST only, so a third-party
    # page that merely mentions the issuer in its PATH (uk.investing.com/equities/
    # mitie-group) is correctly rejected — that page is not the issuer speaking.
    flat = host.replace("-", "").replace(".", "")
    toks = {t for t in _tokenize_entity_name(name) if len(t) >= 4}
    return any(t in flat for t in toks)


# R-F3135: queries that surface the ISSUER'S OWN publication.
#
# Deliberately NO `(a OR b OR c)` block. The generic footprint query
# (`_search_financial_footprint`) uses one, and per R-F3051..R-F3056 Brave returns
# HTTP 200 while SILENTLY DROPPING the quoted phrase when an OR-block is present —
# so the single search feeding this route was both untargeted AND degraded.
#
# Ordered most-specific first; the first query that yields an issuer-domain document
# wins, so the common case costs one search.
_ISSUER_DOC_QUERIES = (
    '"{name}" annual report and accounts filetype:pdf',
    '"{name}" annual report filetype:pdf',
    '"{name}" investor relations annual report',
)


async def _search_issuer_domain_documents(name: str, limit: int = 10) -> list:
    """R-F3135 — find the issuer's OWN report. Returns only issuer-domain hits.

    THE DEFECT, proven by live probe against Babcock International Group plc
    (2026-07-26). `_search_financial_footprint` returned four sources and NOT ONE was
    the issuer's own site:

        wsj.com/market-data/quotes/UK/XLON/BAB/financials      G1: False
        companycheck.co.uk/company/02342138/...                G1: False
        financialfilings.com/companies/babcock-...             G1: False
        uk.advfn.com/stock-market/london/babcock-BAB/financials G1: False

    G1 (`_issuer_domain_matches`) then correctly rejected all four — a third party's
    summary of a company's accounts is not the company's accounts. So a route NAMED
    "issuer report" could never fire for ANY subject, and every listed group read
    "financials unverified" regardless of budget. R-F3131 gave this op 150s; time was
    never the constraint. Babcock's actual accounts sit on babcockinternational.com
    and were never searched for.

    The gate was right; the SEARCH never looked in the right place. This does not
    relax G1 — it feeds it candidates that can pass.

    CORRECTION (same day, from the live Babcock DD dd_6e11c978dc86) — the claim above
    that this route could never fire for ANY subject is TOO STRONG, and is withdrawn.
    In PRODUCTION the footprint search DID return issuer-domain documents:
    `gates.provenance` came back **true**, and the compliance section listed
    babcockinternational.com/.../Babcock-Annual-Report-and-Financial-Statements-2025.pdf.
    The four-aggregator result that motivated this function came from a LOCAL probe
    whose search backends were rate-limited (duckduckgo breaker OPEN, no Brave scope),
    so it under-reported what production actually sees. The real cause of "financials
    unverified" on that report was downstream — see R-F3146 (the document was fetched
    twice inside one budget, then truncated past its own balance sheet).

    This function still earns its place: it GUARANTEES an issuer-domain document is
    offered first rather than trusting a generic query's ranking, and it is fail-safe
    (no issuer document found = prior behaviour exactly). But it did NOT fix Babcock,
    and must not be cited as having done so.
    """
    from . import web_search

    out: list = []
    seen: set = set()
    for tmpl in _ISSUER_DOC_QUERIES:
        try:
            hits = await web_search.search(tmpl.format(name=name), max_results=limit)
        except Exception as e:                     # never let search kill the route
            logger.debug("[R-F3135] issuer-doc search failed (%s): %s", tmpl, e)
            continue
        for h in hits or []:
            # web_search.search returns SearchResult DATACLASSES, not dicts — an
            # `isinstance(h, dict)` filter here silently drops every hit and the route
            # reports "no issuer document" with a perfectly healthy search behind it.
            # Caught by live probe before shipping; downstream
            # (`extract_issuer_financials`) calls .get(), so normalise to dicts.
            if hasattr(h, "to_dict"):
                try:
                    h = h.to_dict()
                except Exception:
                    h = {"url": getattr(h, "url", ""), "title": getattr(h, "title", ""),
                         "snippet": getattr(h, "snippet", "")}
            elif not isinstance(h, dict):
                h = {"url": getattr(h, "url", ""), "title": getattr(h, "title", ""),
                     "snippet": getattr(h, "snippet", "")}
            url = str(h.get("url") or h.get("link") or "").strip()
            # memory:// hits are ARIA's own RAG, not the issuer publishing (R-F2346).
            if not url or url.startswith("memory://") or url in seen:
                continue
            seen.add(url)
            if _issuer_domain_matches(url, name):
                out.append(h)
        if out:
            break
    return out


def _arithmetic_reconciles(figures: dict) -> tuple[bool, str]:
    """R-F3124 G4 — does the balance sheet balance?

    THE anti-fabrication gate. A model inventing plausible-looking figures will not
    satisfy net_assets == total_assets − total_liabilities; a model reading a real
    balance sheet will. Returns (ok, explanation) and REFUSES on anything missing —
    an unverifiable figure must never answer a solvency question."""
    try:
        ta = float(figures.get("total_assets"))
        tl = float(figures.get("total_liabilities"))
        na = float(figures.get("net_assets"))
    except (TypeError, ValueError):
        return False, "figures incomplete or non-numeric — cannot reconcile"
    if ta <= 0:
        return False, "total assets non-positive — not a usable balance sheet"
    implied = ta - tl
    if abs(implied - na) > abs(ta) * _ISSUER_FIN_TOLERANCE:
        return False, (
            f"balance sheet does NOT reconcile: net assets {na:,.0f} vs "
            f"assets {ta:,.0f} − liabilities {tl:,.0f} = {implied:,.0f}")
    return True, (
        f"reconciles: net assets {na:,.0f} ≈ assets {ta:,.0f} − liabilities "
        f"{tl:,.0f} = {implied:,.0f}")


# R-F3146 — anchors that mark the CONSOLIDATED BALANCE SHEET in an annual report.
_BALANCE_ANCHORS = (
    "total assets", "total liabilities", "net assets",
    "statement of financial position", "balance sheet",
    "total equity", "non-current assets", "current liabilities",
)
# Occurrences strong enough to anchor a window on.
_BALANCE_PRIMARY_ANCHORS = (
    "total assets", "statement of financial position", "balance sheet",
)


def _pdf_text_layer(data: bytes, max_pages: int = 800) -> str:
    """R-F3165 — pull a PDF's text layer with PyMuPDF. No OCR, no table extraction.

    Synchronous and CPU-bound on purpose: the caller runs it via asyncio.to_thread so
    a 300-page report cannot block the event loop (a DD runs ~15-25 concurrent LLM
    calls alongside this).

    Returns "" when there is no text layer, which is the caller's signal to fall back
    to the full document_reader pipeline — a genuine scan is the only case that needs
    OCR, and a scan cannot support a solvency verdict anyway (R-F3017 route 2).
    """
    import fitz  # PyMuPDF — already a dependency of document_reader's OCR strategy
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return "".join(
            doc[i].get_text() for i in range(min(doc.page_count, max_pages))
        )
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _financial_excerpt(text: str, limit: int = 0) -> str:
    """R-F3146 — hand the extractor the BALANCE SHEET, not the first N characters.

    THE DEFECT: the prompt was built from `text[:_ISSUER_FIN_MAX_CHARS]` — the FIRST
    120k characters. In a FTSE annual report that is the strategic report, governance
    and remuneration sections; the consolidated balance sheet sits well past halfway.
    So even a document that parsed perfectly would have had its figures TRUNCATED AWAY
    before the model ever saw them, and the route would report "figures incomplete" on
    a report that plainly contains them — a false data gap sourced from our own slicing.

    Picks the window densest in balance-sheet anchors, so the model reads the statement
    it is being asked about. Falls back to the head only when no anchor is present,
    which preserves the previous behaviour for short or unusual documents.
    """
    limit = limit or _ISSUER_FIN_MAX_CHARS
    if not text:
        return ""
    if len(text) <= limit:
        return text
    low = text.lower()
    lead = limit // 4                      # keep some context before the statement
    best_start, best_score = None, -1
    for anchor in _BALANCE_PRIMARY_ANCHORS:
        pos = 0
        while True:
            i = low.find(anchor, pos)
            if i < 0:
                break
            start = max(0, i - lead)
            window = low[start:start + limit]
            score = sum(window.count(a) for a in _BALANCE_ANCHORS)
            if score > best_score:
                best_score, best_start = score, start
            pos = i + len(anchor)
    if best_start is None:
        return text[:limit]
    return text[best_start:best_start + limit]


async def extract_issuer_financials(
    sources: list[dict], name: str, llm: Any = None, *, timeout: float = 45.0,
) -> dict:
    """R-F3124 — read the issuer's OWN annual report and, only if every gate passes,
    answer financial capacity.

    Returns {"ok": bool, "reason": str, ...figures} and NEVER raises. `ok=False`
    leaves the caller's existing honest UNKNOWN untouched — that is the default, and
    the bar to move off it is deliberately high.
    """
    out: dict = {"ok": False, "reason": "not attempted", "gates": {}}
    if not llm:
        out["reason"] = "no LLM available to read the document"
        return out

    # G1 — the issuer's own domain, and a document that looks like a report.
    cand = None
    for s_ in (sources or []):
        u = str((s_ or {}).get("url") or "")
        if not u.lower().startswith("http"):
            continue
        if not _issuer_domain_matches(u, name):
            continue
        blob = f"{(s_ or {}).get('title','')} {u}".lower()
        if any(k in blob for k in ("annual report", "annual-report", "financial statement",
                                   "results", "accounts", ".pdf")):
            cand = s_
            break
    if not cand:
        out["reason"] = ("no annual report found on the issuer's own domain — a third "
                         "party's summary is not the issuer's accounts")
        out["gates"]["provenance"] = False
        return out
    out["gates"]["provenance"] = True
    url = str(cand.get("url"))

    # ── G2 — RETRIEVABLE, *THEN* a real text layer ──────────────────────────
    #
    # R-F3125 — G2 CONFLATED TWO DIFFERENT OBSTACLES AND NAMED THE WRONG ONE.
    # Measured on the real Mitie annual report (2026-07-26): the shipped G2 reported
    # "document has no usable text layer (0 chars) — a scanned filing cannot support
    # a solvency assessment". The document is NOT a scan. The URL returns
    # **HTTP 403 with a Cloudflare 'Just a moment…' challenge page** — the fetch was
    # BLOCKED. Both end in zero text, so the code inferred the wrong cause and told
    # the customer something false about the issuer's filing.
    #
    # That matters because the remedies are opposite: a scan is a dead end (OCR is
    # not a basis for a solvency verdict — R-F3017 route 2), while a bot challenge is
    # a FETCH-CAPABILITY problem that a headless browser can solve. A DD that
    # misnames its own obstacle sends the reader to fix the wrong thing — the same
    # defect class as R-F3054 naming the wrong failing condition.
    #
    # So: retrieve explicitly first and report the HTTP reality, then judge the text.
    _pre_status = None
    _pre_bytes = b""
    try:
        import httpx as _hx
        async with _hx.AsyncClient(timeout=min(timeout, 60.0), follow_redirects=True) as _c:
            _r = await _c.get(url, headers={"User-Agent": _ISSUER_FETCH_UA})
            _pre_status = _r.status_code
            _pre_bytes = _r.content or b""
    except Exception as e:
        out["reason"] = f"could not reach the document: {type(e).__name__}: {str(e)[:100]}"
        out["gates"]["retrievable"] = False
        return out

    if _pre_status != 200:
        out["reason"] = (
            f"the issuer's document could not be retrieved (HTTP {_pre_status}) — the "
            "site refused the request. This is an ACCESS obstacle, not a statement "
            "about the filing: the figures may well be published and readable.")
        out["gates"]["retrievable"] = False
        return out
    if not _pre_bytes[:4] == b"%PDF" and b"<html" in _pre_bytes[:400].lower():
        out["reason"] = (
            "the issuer's URL returned an HTML page rather than the document — "
            "typically a bot/consent interstitial. ACCESS obstacle, not a scanned "
            "filing.")
        out["gates"]["retrievable"] = False
        return out
    out["gates"]["retrievable"] = True

    text = ""
    # ── R-F3146 — parse the bytes we ALREADY HAVE, and name a timeout honestly ──
    #
    # THE DEFECT, measured on the live Babcock DD (dd_6e11c978dc86, 2026-07-26):
    #     issuer_financials: {"ok": false,
    #       "reason": "retrieved the document but could not parse it: ",
    #       "gates": {"provenance": true, "retrievable": true, "text_layer": false}}
    #
    # G1 and G2 PASSED — production found and fetched Babcock's own annual report. The
    # reason string ends at the colon because `str(asyncio.TimeoutError())` is the EMPTY
    # STRING, so the `except Exception` below rendered a timeout as an unexplained parse
    # failure. The customer was told the document was unreadable; it was not.
    #
    # WHY IT TIMED OUT — and this is the root, not the 45s (§1 forbids bumping it):
    # `read_document(url)` re-resolves the URL through `_resolve_source`, which
    # DOWNLOADS THE FILE AGAIN (document_reader.py:1152-1170) — even though G2 above
    # already holds the complete bytes in `_pre_bytes`. A FTSE annual report is
    # hundreds of pages and tens of MB, so the budget paid for the same transfer twice
    # before any parsing began. Handing the parser the bytes we already have removes an
    # entire redundant network fetch of a large file — that is the failure class, and
    # it also stops us hitting the issuer's CDN twice per DD.
    _tmp_path = None
    try:
        from . import document_reader as _dr
        # ── R-F3165: read the TEXT LAYER directly; do not buy the OCR pipeline ──
        #
        # MEASURED (Babcock, dd_52cc50527dd0): "did not finish parsing within 45s
        # (9,339,633 bytes)". R-F3146 removed the double download and named the
        # timeout honestly, which exposed the next real constraint rather than
        # hiding it — the PARSE itself does not fit.
        #
        # `read_document` is a 4-strategy pipeline built for unknown documents:
        # pdfplumber text (document_reader.py:196) → pdfplumber TABLE extraction
        # (:208) → Tesseract OCR (:220) → LLM vision. pdfplumber is an order of
        # magnitude slower than PyMuPDF on a ~300-page annual report, and table
        # extraction and OCR are pure cost here: a balance sheet's figures live in
        # the TEXT LAYER, and a filing with no text layer cannot support a solvency
        # verdict anyway (R-F3017 route 2).
        #
        # So take the cheap, correct path first and keep the full pipeline as the
        # fallback for genuine scans. §1: this removes the work, it does not raise
        # the timeout.
        if _pre_bytes[:4] == b"%PDF":
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(_pdf_text_layer, _pre_bytes), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                raise
            except Exception as e:                    # damaged PDF → try the pipeline
                logger.debug("[R-F3165] text-layer read failed (%s) — falling back "
                             "to the full reader", type(e).__name__)
                text = ""
        if len(text.strip()) < 2000:
            # Either not a PDF, or no usable text layer — this is what the heavy
            # pipeline (OCR/vision) exists for.
            _suffix = ".pdf" if _pre_bytes[:4] == b"%PDF" else ".html"
            _fd, _tmp_path = tempfile.mkstemp(suffix=_suffix, prefix="aria_issuer_")
            with os.fdopen(_fd, "wb") as _fh:
                _fh.write(_pre_bytes)
            # §3b — verified: document_reader.read_document(source, llm=..) -> ExtractionResult
            _res = await asyncio.wait_for(_dr.read_document(_tmp_path), timeout=timeout)
            text = str(getattr(_res, "text", "") or "")
    except (asyncio.TimeoutError, TimeoutError):
        out["reason"] = (
            f"the issuer's document did not finish parsing within {timeout:.0f}s "
            f"({len(_pre_bytes):,} bytes). This is a PROCESSING limit on our side — it "
            "is NOT a statement about the filing, which may be perfectly readable.")
        out["gates"]["text_layer"] = False
        return out
    except Exception as e:
        # An exception whose str() is empty must never render as "could not parse it: ".
        _msg = str(e).strip() or type(e).__name__
        out["reason"] = f"retrieved the document but could not parse it: {_msg[:120]}"
        out["gates"]["text_layer"] = False
        return out
    finally:
        if _tmp_path:
            try:
                os.unlink(_tmp_path)
            except OSError:
                pass
    if len(text.strip()) < 2000:
        out["reason"] = (
            f"document retrieved ({len(_pre_bytes):,} bytes) but carries no usable text "
            f"layer ({len(text.strip())} chars) — a scanned filing cannot support a "
            "solvency assessment (see R-F3017 route 2)")
        out["gates"]["text_layer"] = False
        return out
    out["gates"]["text_layer"] = True

    # G3 — grounded extraction. Every figure must arrive with a verbatim quote.
    prompt = (
        "You are reading a company's published annual report. Extract ONLY figures "
        "that appear VERBATIM in the text. Do NOT infer, estimate, convert or "
        "calculate any value. If a figure is not clearly stated, use null.\n\n"
        "Return STRICT JSON with exactly these keys:\n"
        '{"currency": str|null, "period_end": str|null, "units": "absolute"'
        '|"thousands"|"millions"|null, "total_assets": number|null, '
        '"total_liabilities": number|null, "net_assets": number|null, '
        '"revenue": number|null, "cash": number|null, "quotes": '
        '{"total_assets": str, "total_liabilities": str, "net_assets": str}}\n\n'
        "Each quote must be a short VERBATIM substring of the document showing "
        "that figure. Report figures in the SAME units you state in `units`.\n\n"
        # R-F3146: the balance-sheet window, NOT the first 120k chars (which in an
        # annual report is the strategic report, and excludes the figures asked for).
        f"DOCUMENT:\n{_financial_excerpt(text)}"
    )
    try:
        # §3b — verified: llm.complete(system, prompt, max_tokens=, timeout=) -> .text
        _r = await llm.complete(
            "ARIA — annual-report figure extractor. Verbatim only; never infer.",
            prompt, max_tokens=900, timeout=timeout,
        )
        raw = str(getattr(_r, "text", "") or "")
    except Exception as e:
        out["reason"] = f"model call failed: {str(e)[:120]}"
        return out
    from .llm_json import parse_llm_json
    figures = parse_llm_json(raw, default={}, source="financial_health:R-F3124")
    if not isinstance(figures, dict) or not figures:
        out["reason"] = "model did not return parseable JSON"
        return out

    _q = figures.get("quotes") if isinstance(figures.get("quotes"), dict) else {}
    _hay = text.lower()
    _ungrounded = [
        k for k in ("total_assets", "total_liabilities", "net_assets")
        if figures.get(k) is not None
        and not (str(_q.get(k) or "").strip()[:40].lower() in _hay
                 and len(str(_q.get(k) or "").strip()) >= 8)
    ]
    if _ungrounded:
        out["reason"] = (
            "figure(s) not grounded in a verbatim quote from the document: "
            + ", ".join(_ungrounded))
        out["gates"]["grounding"] = False
        return out
    out["gates"]["grounding"] = True

    # G4 — THE anti-fabrication gate. A balance sheet that does not balance is not
    # evidence; it is a model's guess wearing a number.
    ok, why = _arithmetic_reconciles(figures)
    out["gates"]["arithmetic"] = ok
    if not ok:
        out["reason"] = why
        return out

    out.update({
        "ok": True,
        "reason": why,
        "source_url": url,
        "source_title": str(cand.get("title") or "")[:160],
        "currency": figures.get("currency"),
        "period_end": figures.get("period_end"),
        "units": figures.get("units"),
        "total_assets": figures.get("total_assets"),
        "total_liabilities": figures.get("total_liabilities"),
        "net_assets": figures.get("net_assets"),
        "revenue": figures.get("revenue"),
        "cash": figures.get("cash"),
        "quotes": _q,
    })
    return out


def _verdict_from_issuer_report(iss: dict) -> str:
    """R-F3124 — a health verdict from the issuer's stated balance sheet.

    Deliberately CONSERVATIVE and structural. This is a solvency read from a
    reconciled balance sheet, not a ratio model — SEC EDGAR (route 1) remains the
    only path to an Altman Z''. Claiming more than the document supports would be
    the same overreach the gate chain exists to prevent.
    """
    try:
        ta = float(iss.get("total_assets"))
        tl = float(iss.get("total_liabilities"))
        na = float(iss.get("net_assets"))
    except (TypeError, ValueError):
        return "UNKNOWN"
    # ORDER MATTERS. `total_assets <= 0` is tested FIRST because it means "no usable
    # balance sheet", and an all-zero extraction would otherwise fall into the
    # net_assets<=0 branch and be reported as DISTRESSED — a false ACCUSATION of
    # insolvency built from missing data, which is the mirror image of a false clean
    # and just as damaging to a counterparty.
    if ta <= 0:
        return "UNKNOWN"
    if na <= 0:
        return "DISTRESSED"          # liabilities exceed assets — balance-sheet insolvent
    equity_ratio = na / ta
    if equity_ratio >= 0.30:
        return "STRONG"
    if equity_ratio >= 0.10:
        return "STABLE"
    return "WEAK"


def _is_gb(jurisdiction_iso2: str) -> bool:
    """True for United Kingdom jurisdiction codes (R-F2782).

    Companies House covers GB only, so this gates the registry-accounts lookup.
    An EMPTY/unknown jurisdiction returns False on purpose: we do not guess a
    jurisdiction and then present GB filings as if they were the subject's.
    Accepts the common spellings callers actually send (GB/UK/GBR).
    """
    return (jurisdiction_iso2 or "").strip().upper() in {"GB", "UK", "GBR"}


async def _uk_registry_accounts(name: str, registration_number: str = "") -> dict | None:
    """Companies House statutory-accounts EVIDENCE for a GB entity (R-F2782 phase 1).

    Returns filing metadata — when accounts were last made up to, what type they
    are (full/small/micro/dormant), whether they are overdue — or None when CH
    has nothing or is unavailable.

    ★ THIS IS EVIDENCE, NOT A VERDICT. There are no revenue or solvency figures
    here, so the caller must NOT set `data_available` / `has_financials` from it:
    `_verdict()` keys UNKNOWN off exactly those two, and that is the behaviour we
    want to preserve. Answering financial capacity from filing dates would be a
    false clean. Figures arrive with the CH Document API (iXBRL) in phase 2.

    Never raises — an unavailable registry must degrade to None (a data gap),
    never to something that reads like a clean result (R-F2719).
    """
    try:
        from . import companies_house as ch
        if not ch.is_enabled():
            return None

        number = (registration_number or "").strip()
        if not number:
            hits = await ch.search_companies(name, limit=3)
            selected, _resolution = ch.resolve_company_search(name, hits)
            if selected:
                number = str(selected.get("company_number") or "").strip()
        if not number:
            return None

        profile = await ch.get_company_profile(number)
        if not profile:
            return None
        accounts = profile.get("accounts") or {}
        if not accounts.get("filed") and not accounts.get("distress_flags"):
            return None

        return {
            "source": "companies_house",
            "company_number": profile.get("company_number") or number,
            "company_name": profile.get("company_name") or name,
            "company_status": profile.get("company_status") or "",
            "accounts": accounts,
            # Primary-source URL so the evidence is citable, not asserted.
            "source_url": (
                "https://find-and-update.company-information.service.gov.uk/"
                f"company/{profile.get('company_number') or number}/filing-history"
            ),
            "has_figures": False,
        }
    except Exception as e:
        logger.debug("UK registry accounts lookup failed: %s", e)
        return None


def _registry_accounts_summary(reg: dict) -> str:
    """One honest sentence about filed accounts — never a health claim."""
    acc = reg.get("accounts") or {}
    bits: list[str] = []
    if acc.get("filed"):
        made_up = acc.get("last_made_up_to") or "an unstated date"
        atype = acc.get("last_type") or "unspecified"
        bits.append(f"Companies House shows {atype} accounts made up to {made_up}")
    else:
        bits.append("Companies House shows NO accounts filed")

    flags = acc.get("distress_flags") or []
    if "accounts_overdue" in flags:
        bits.append("accounts are OVERDUE (a standard early-distress signal)")
    if "dormant_accounts" in flags:
        bits.append("the company filed as DORMANT")

    return (
        ". ".join(bits)
        + ". Figures are not extracted from these filings, so financial health "
          "remains UNKNOWN — this is filing evidence, not a solvency assessment."
    )


async def _enrich_with_registry_accounts(
    result: dict,
    name: str,
    jurisdiction_iso2: str,
    registration_number: str = "",
) -> bool:
    """Attach GB registry-accounts evidence to `result` in place (R-F2782/R-F2817).

    Returns True when evidence was added, False otherwise. Never raises.

    Shared by BOTH the fresh-assessment path and the vault path. R-F2817: a
    cached profile written before R-F2782 has no `registry_accounts`, and the
    vault serves anything younger than 30 days, so without this the new evidence
    was invisible on every already-assessed entity for up to a month (verified
    live: BAE and Rolls-Royce both returned from_vault=True and no evidence).
    One helper, two call sites — so the cached and fresh paths cannot drift.

    Still does NOT touch `data_available` / `has_financials`: this raises the
    EVIDENCE grade, it does not answer financial capacity (see _uk_registry_accounts).
    """
    if result.get("registry_accounts"):
        return False
    if not _is_gb(jurisdiction_iso2):
        return False
    try:
        reg = await _uk_registry_accounts(name, registration_number)
        if not reg:
            return False
        result["registry_accounts"] = reg
        result["summary"] = (
            (result.get("summary", "") + " ").strip()
            + " " + _registry_accounts_summary(reg)
        ).strip()
        for _flag in (reg.get("accounts") or {}).get("distress_flags", []):
            if _flag not in result.setdefault("distress_flags", []):
                result["distress_flags"].append(_flag)
        return True
    except Exception as e:
        logger.debug("registry accounts enrichment failed: %s", e)
        return False


def _uk_balance_sheet_verdict(figures: dict) -> dict | None:
    """R-F3016 — a BOUNDED solvency verdict from filed balance-sheet figures. Returns
    None when neither net assets nor net current assets are present (nothing to say).
    NEVER claims profitability — the P&L is not publicly filed for small companies, so
    this reads solvency (net assets) + liquidity (working capital) + YoY trend only."""
    def _cur(k):
        v = (figures.get(k) or {}).get("current")
        return v if isinstance(v, (int, float)) else None

    def _pri(k):
        v = (figures.get(k) or {}).get("prior")
        return v if isinstance(v, (int, float)) else None

    na, na_p = _cur("net_assets"), _pri("net_assets")
    nca = _cur("net_current_assets")
    if na is None and nca is None:
        return None
    reasons: list[str] = []
    verdict = "STABLE"
    if na is not None:
        if na < 0:
            verdict = "DISTRESSED"
            reasons.append(f"balance-sheet INSOLVENT — net liabilities of £{abs(na):,.0f} "
                           "(liabilities exceed assets)")
        else:
            reasons.append(f"positive net assets of £{na:,.0f}")
    if nca is not None:
        if nca < 0:
            reasons.append(f"working-capital DEFICIT — net current liabilities of "
                           f"£{abs(nca):,.0f} (short-term liquidity risk)")
            if verdict != "DISTRESSED":
                verdict = "WEAK"
        else:
            reasons.append(f"positive working capital of £{nca:,.0f}")
    if na is not None and na_p is not None:
        delta = na - na_p
        trend = "improved" if delta > 0 else ("declined" if delta < 0 else "held flat")
        reasons.append(f"net assets {trend} year-on-year (£{na_p:,.0f} → £{na:,.0f})")
        if verdict == "STABLE" and delta > 0:
            verdict = "STRONG"
        elif verdict == "STABLE" and delta < 0:
            verdict = "WEAK"
    return {"verdict": verdict, "reasons": reasons}


#: R-F3028 — sentences that assert we have NO figures. Once figures exist they are
#: not context, they are contradictions, and a reader cannot tell which half to
#: believe. Matched case-insensitively against each sentence of the prior summary.
_SUPERSEDED_SUMMARY_MARKERS = (
    "financial health is unknown",
    "financial health remains unknown",
    "figures are not extracted",
    "no financial data",
    "not a us-listed filer",
    "no figures available",
    "insufficient to assess solvency",
)


def _replace_superseded_summary(prior: str, fresh: str) -> str:
    """R-F3028 — drop sentences of `prior` that a now-available figure contradicts,
    then lead with `fresh`. Sentences that carry OTHER information (e.g. which
    registry was searched) are kept: the goal is coherence, not amnesia."""
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(prior or "").strip()):
        s = sentence.strip()
        if not s:
            continue
        if any(m in s.lower() for m in _SUPERSEDED_SUMMARY_MARKERS):
            continue
        kept.append(s)
    tail = " ".join(kept).strip()
    return (fresh.strip() + (" " + tail if tail else "")).strip()


def _refresh_derived_text(profile: dict) -> None:
    """R-F3043 — re-derive EXPLANATORY text on a vault read, in place.

    THE DEFECT (live, dd_f4a7635c6efa). R-F3041 corrected the balance-sheet basis
    line so a FULL-accounts filer is no longer told it uses the small-company
    exemption. The next DD still printed the old sentence, because
    `uk_balance_sheet.basis` had been FROZEN into the vault profile by an earlier
    run and `assess()` returns that profile verbatim (`from_vault: True`).

    The distinction that matters: FIGURES are evidence and must be cached (§15,
    pay-once). Sentences ABOUT those figures are derived — they encode this build's
    understanding, so caching them means a wording or accuracy fix cannot reach any
    already-assessed entity for the whole 30-day freshness window. That is the
    R-F2834 failure class ("the feature looked broken in production while being
    perfectly correct"), in its explanatory-text form.

    Only regenerates text from data already in the profile — never fetches, never
    changes a verdict, and never raises."""
    try:
        ub = profile.get("uk_balance_sheet")
        if isinstance(ub, dict) and ub.get("figures"):
            ub["basis"] = _balance_sheet_basis(ub.get("accounts_type"))
        unavail = profile.get("financial_figures_unavailable")
        if isinstance(unavail, dict) and unavail.get("reason"):
            unavail["explanation"] = _figures_unavailable_explanation({
                "unavailable_reason": unavail.get("reason"),
                "made_up_to": unavail.get("made_up_to"),
                "accounts_type": unavail.get("accounts_type"),
                "pages": unavail.get("pages"),
            })
    except Exception as e:      # derived text must never break a served profile
        logger.debug("derived-text refresh skipped: %s", e)


def _balance_sheet_basis(accounts_type) -> str:
    """R-F3041 — state the basis of the solvency read WITHOUT asserting an
    exemption the filer may not use.

    The small-company exemption (filleted P&L) is real and worth naming when the
    filing is small/micro/abridged. On a FULL or GROUP filing it is simply false —
    those accounts do include a P&L; this reader just does not extract it. Saying
    otherwise misattributes our own scope limit to the company's filing choices."""
    t = str(accounts_type or "").lower()
    small = any(k in t for k in ("small", "micro", "abridged", "filleted", "dormant"))
    base = ("solvency read from the balance sheet only — turnover and profit are not "
            "extracted by this reader")
    if small:
        return (base + ", and are not publicly filed under the small-company exemption "
                       "for this filing type")
    return base + " (this is a scope limit of the reader, not of the filing)"


def _figures_unavailable_explanation(fig: dict) -> str:
    """R-F3017 — one honest sentence for WHY filed accounts yield no figures.

    The gap this closes: a large listed PLC's report said only "financial capacity is
    unknown", which reads identically to "no accounts were ever filed" and to "we did
    not look". All three are different facts. Companies House holds large-group
    accounts as SCANNED documents (no text layer, no iXBRL) — proven live on Cohort
    PLC 05684823 — so the honest statement names the filing AND the obstacle."""
    made = (fig.get("made_up_to") or "").strip() or "an unstated date"
    atype = (fig.get("accounts_type") or "").strip()
    pages = fig.get("pages")
    reason = fig.get("unavailable_reason")
    if reason == "ixbrl_no_balance_sheet_figures":
        return (
            f"Companies House holds accounts made up to {made}"
            f"{' (' + atype + ')' if atype else ''}, and they are machine-readable, but "
            "carry no balance-sheet tags this reader recognises — solvency NOT assessed."
        )
    return (
        f"Companies House holds accounts made up to {made}"
        f"{' (' + atype + ')' if atype else ''}"
        f"{f', {pages} pages' if isinstance(pages, int) and pages else ''}, filed as a "
        "scanned/PDF document with no machine-readable (iXBRL) figures — the filing is "
        "EVIDENCE of an up-to-date statutory filing, but solvency was NOT assessed from "
        "it. Large listed groups file this way; figures would need the issuer's own "
        "published annual report."
    )


async def _enrich_with_registry_figures(
    result: dict,
    name: str,
    jurisdiction_iso2: str,
    registration_number: str = "",
) -> bool:
    """R-F3016 (R-F2782 phase 2) — Companies House iXBRL BALANCE-SHEET figures → a
    bounded SOLVENCY verdict. Unlike phase-1 metadata this DOES answer financial
    capacity: on real filed figures it sets data_available / has_financials /
    health_verdict. Balance sheet only (P&L filleted for small companies) → a solvency
    read, never a profitability claim. Large listed PLCs file PDF group accounts (no
    iXBRL) → returns False (honest UNKNOWN). Never raises."""
    if result.get("has_financials"):
        return False
    if not _is_gb(jurisdiction_iso2):
        return False
    try:
        from . import companies_house as ch
        if not ch.is_enabled():
            return False
        number = (registration_number or "").strip()
        if not number:
            hits = await ch.search_companies(name, limit=3)
            selected, _resolution = ch.resolve_company_search(name, hits)
            if selected:
                number = str(selected.get("company_number") or "").strip()
        if not number:
            return False
        fig = await ch.fetch_accounts_figures(number)
        if not fig or not fig.get("figures"):
            # R-F3017 — no figures, but we may know WHY. Record the evidence
            # (a filing exists, made up to X, filed in format Y) so the report
            # states an EVIDENCED unknown instead of a bare one. Never sets
            # has_financials/data_available: this does not answer capacity, it
            # explains why capacity cannot be answered.
            if isinstance(fig, dict) and fig.get("unavailable_reason"):
                result["financial_figures_unavailable"] = {
                    "reason": fig["unavailable_reason"],
                    "made_up_to": fig.get("made_up_to"),
                    "accounts_type": fig.get("accounts_type"),
                    "document_formats": fig.get("document_formats") or [],
                    "pages": fig.get("pages"),
                    "source_url": fig.get("source_url"),
                    "explanation": _figures_unavailable_explanation(fig),
                }
                result["summary"] = (
                    (result.get("summary", "") + " ").strip()
                    + " " + _figures_unavailable_explanation(fig)
                ).strip()
                return True
            return False
        verdict = _uk_balance_sheet_verdict(fig["figures"])
        if not verdict:
            return False
        result["data_available"] = True
        result["has_financials"] = True
        result["health_verdict"] = verdict["verdict"]
        result["uk_balance_sheet"] = {
            "figures": fig["figures"],
            "made_up_to": fig.get("made_up_to"),
            "accounts_type": fig.get("accounts_type"),
            "reasons": verdict["reasons"],
            "source_url": fig.get("source_url"),
            # R-F3041 — do not cite the small-company exemption on a FULL filer.
            # Live on dd_71553f511d72 (Supacat, accounts-with-accounts-type-FULL,
            # net assets £19.97m) the basis line still read "not publicly filed
            # under the small-company exemption" — an exemption that entity does
            # not use. The true, filer-agnostic statement is that THIS reader
            # extracts the balance sheet only; the exemption is named only when
            # the filing type says it applies.
            "basis": _balance_sheet_basis(fig.get("accounts_type")),
        }
        # ── R-F3028 — REPLACE the superseded narrative, do not append to it ──
        #
        # THE DEFECT (live, dd_16db41eb5fa8). The pre-existing summary was the
        # SEC-EDGAR one: "financial health is UNKNOWN, not a clean bill… Figures are
        # not extracted from these filings, so financial health remains UNKNOWN".
        # Appending the Companies House result produced one paragraph that said
        # UNKNOWN twice and then quoted net assets of £69,482 — under the title
        # "Financial health: STRONG", at confidence CONFIRMED. Every sentence was
        # individually true and the paragraph as a whole was incoherent.
        #
        # Once real figures are extracted, the "no figures available" narrative is
        # SUPERSEDED — it is not additional context, it is a statement this run has
        # just falsified. `source`/`reason` are re-stamped for the same reason: they
        # still read `sec_edgar_financials` / "not a US-listed filer" while the
        # verdict now comes from Companies House.
        result["summary"] = _replace_superseded_summary(
            result.get("summary", ""),
            f"Companies House filed accounts (made up to "
            f"{fig.get('made_up_to') or 'an unstated date'}): "
            + "; ".join(verdict["reasons"])
            + ". " + _balance_sheet_basis(fig.get("accounts_type")).capitalize() + ".",
        )
        result["source"] = "companies_house_accounts"
        result["reason"] = ("solvency assessed from Companies House filed iXBRL "
                            "balance-sheet figures")
        return True
    except Exception as e:
        logger.debug("UK registry figures enrichment failed: %s", e)
        return False


# ── R-F2834 — VAULT CAPABILITY VERSIONING ────────────────────────────────────
#
# THE DEFECT THIS CLOSES. assess() serves any vault profile younger than
# max_age_days (30) and the vault carried NO capability/schema version. A profile
# written BEFORE an evidence source existed therefore kept suppressing that source
# for the whole freshness window — the code deployed, tested and correct, the
# entity simply never running it.
#
# It already cost us live, twice: R-F2782 phase 1 shipped GB registry-accounts
# evidence, and two deep DDs (BAE, Rolls-Royce) both returned from_vault=True with
# no registry evidence because their profiles predated the feature. The feature
# looked broken in production while being perfectly correct.
#
# R-F2817 fixed it for ONE field by hardcoding a backfill call on the vault-hit
# path. That left the general defect open: every future evidence source needed
# someone to remember another hardcoded call, and a forgotten one fails SILENTLY —
# masked for 30 days and indistinguishable from "this entity has no such evidence".
# Absence presented as absence-of-evidence is the false-clean class we refuse.
#
# THE CONTRACT: adding an evidence source = adding ONE entry here. Profiles are
# stamped with the capabilities that produced them; a vault hit backfills only what
# is missing, re-stamps, and re-persists (pay-once, §15). A capability whose
# enricher FAILS is deliberately NOT stamped, so it retries on the next read rather
# than recording evidence that was never gathered.
async def _enrich_with_issuer_report(
    result: dict,
    name: str,
    jurisdiction_iso2: str,
    registration_number: str = "",
) -> bool:
    """R-F3128 — the R-F3124 issuer-report route, as a REGISTERED capability.

    THE DEFECT (QinetiQ, dd_a56444e7647e, 2026-07-26). R-F3124 was wired inline in
    `assess()` step 3 only. `assess()` returns a vault profile VERBATIM when it is
    younger than `max_age_days`, so an entity assessed even minutes earlier never
    reached step 3 and the report still read "figures not yet extracted" — the
    pre-R-F3124 text. That is precisely the masking R-F2834 was built to end
    ("a profile written before a new evidence source existed would keep suppressing
    it for the whole freshness window"), recurring because a new capability was added
    without REGISTERING it.

    As a capability it runs on BOTH paths: fresh assessment and vault backfill. The
    capability is stamped only on success (see backfill_missing_capabilities), so a
    blocked fetch or a refused gate retries on the next read instead of freezing an
    UNKNOWN for 30 days.

    Returns True only when the four gates passed and the profile now ANSWERS
    financial capacity.
    """
    if result.get("data_available") and result.get("has_financials"):
        return False                      # already answered by a stronger route
    fp = result.get("search_footprint") or {}
    sources = fp.get("sources") or []
    if not sources:
        try:
            fp = await _search_financial_footprint(name, jurisdiction_iso2)
            if fp and fp.get("found"):
                result["search_footprint"] = fp
                sources = fp.get("sources") or []
        except Exception as e:
            logger.debug("[R-F3128] footprint search failed: %s", e)
    # R-F3135 — the footprint search returns AGGREGATORS (wsj, advfn, companycheck),
    # every one of which G1 correctly rejects, so this route could never fire. Search
    # the issuer's own domain explicitly and put those documents first; G1 is unchanged.
    try:
        _issuer_srcs = await _search_issuer_domain_documents(name)
    except Exception as e:
        logger.debug("[R-F3135] issuer-domain search failed: %s", e)
        _issuer_srcs = []
    if _issuer_srcs:
        _seen_urls = {str((s or {}).get("url") or "") for s in _issuer_srcs}
        sources = _issuer_srcs + [
            s for s in sources if str((s or {}).get("url") or "") not in _seen_urls
        ]
        result["issuer_document_search"] = {
            "found": len(_issuer_srcs),
            "top": str((_issuer_srcs[0] or {}).get("url") or "")[:300],
        }

    if not sources:
        return False
    llm = _dd_llm_for_capability()
    iss = await extract_issuer_financials(sources, name, llm)
    result["issuer_financials"] = iss
    if not iss.get("ok"):
        try:
            wire_failure(
                module="financial_health",
                detail=f"R-F3128 issuer-report not usable for {name[:60]}: "
                       f"{str(iss.get('reason'))[:160]}",
                gap_type="knowledge_gap", source="financial_health:R-F3128")
        except Exception:
            pass
        return False
    result["data_available"] = True
    result["has_financials"] = True
    result["issuer_report_verified"] = True
    result["health_verdict"] = _verdict_from_issuer_report(iss)
    result["summary"] = (
        f"Financial position read from the issuer's own published report "
        f"({iss.get('source_title') or iss.get('source_url')}): net assets "
        f"{iss.get('net_assets'):,} {iss.get('currency') or ''} "
        f"({iss.get('units') or 'absolute'}) at "
        f"{iss.get('period_end') or 'the stated period end'}. "
        f"Balance sheet {iss.get('reason')}. Figures are quoted verbatim from the "
        f"document and arithmetically reconciled — not inferred."
    ).strip()
    try:
        wire_success(
            module="financial_health",
            summary=f"R-F3128 issuer-report financials verified for {name[:60]}",
            source_id="financial_health:R-F3128")
    except Exception:
        pass
    return True


def _dd_llm_for_capability():
    """R-F3128 — resolve the live provider for a capability that has no caller LLM.

    Mirrors dd_orchestrator._resolve_dd_llm (R-F3087): a backfill runs outside any
    HTTP request, so there is no injected provider. Returns None when unavailable —
    the gate chain then refuses honestly rather than guessing."""
    try:
        from ..main import app as _app
        return getattr(getattr(_app, "state", None), "llm_provider", None)
    except Exception:
        return None


FINANCIAL_CAPABILITIES: dict = {
    # R-F2782/R-F2817 — GB statutory filings from Companies House (metadata only).
    "registry_accounts": _enrich_with_registry_accounts,
    # R-F3016 — GB iXBRL balance-sheet FIGURES → solvency verdict (answers capacity).
    "registry_figures": _enrich_with_registry_figures,
    # R-F3124/R-F3128 — the issuer's OWN published annual report, behind four
    # gates. REGISTERED so a vault-cached profile cannot mask it (R-F2834).
    "issuer_report": _enrich_with_issuer_report,
}

_CAPABILITY_KEY = "_capabilities"


def current_capabilities() -> list:
    """The capability set a profile written by THIS build should carry.

    Derived from the registry rather than hand-maintained: a second list would
    drift from the first, which is exactly how the nav gate rotted in R-F2822.
    """
    return list(FINANCIAL_CAPABILITIES)


def missing_capabilities(profile: dict) -> list:
    """Capabilities this build has that `profile` was not produced with.

    An UNSTAMPED profile is missing everything — that is the BAE / Rolls-Royce
    case. A profile stamped by a NEWER build (rollback) reports nothing missing
    rather than crashing or pointlessly re-enriching.
    """
    have = set((profile or {}).get(_CAPABILITY_KEY) or [])
    # R-F3161 — a stamp is not proof on its own. Profiles ALREADY IN THE VAULT carry
    # `issuer_report` next to a stored transient failure (the live Babcock record:
    # `_capabilities` includes it while `issuer_financials.ok` is false with
    # "could not parse it: "). Fixing only the WRITE path would leave every such
    # profile frozen until it aged out, so treat a stamp whose own stored result did
    # not complete as MISSING. Existing poisoned records then self-heal on next read.
    return [
        c for c in FINANCIAL_CAPABILITIES
        if c not in have or _capability_retry_needed(c, profile)
    ]


def _capability_retry_needed(cap_id: str, profile: dict) -> bool:
    """R-F3161 — did `cap_id`'s last attempt fail for a TRANSIENT reason?

    A capability stamp means "this build's source was CONSULTED and reached a
    conclusion". Two very different outcomes were being collapsed into it:

      * CONSULTED, FOUND NOTHING — a real negative. Stamp it; re-running wastes
        money and breaks pay-once-remember-forever (§15).
      * COULD NOT COMPLETE — timed out, fetch refused, document unparseable, no
        model available. That is NOT evidence about the entity, and stamping it
        freezes an UNKNOWN in the vault for the whole freshness window.

    MEASURED (Babcock, dd_440ef012b068): the profile carried
    `_capabilities: ["issuer_report", ...]` while `issuer_financials` held
    `{"ok": false, "reason": "retrieved the document but could not parse it: "}`.
    Because `issuer_report` was stamped, `missing_capabilities()` reported nothing
    missing, the backfill never re-ran, and the STALE failure blob was served from
    the vault (`from_vault: true`, `vault_age_days: 0.0`) on every subsequent DD.

    That made R-F3146 — already live in the image — completely unreachable: the code
    that fixes the parse never executed, and the report reproduced the old reason
    string byte-for-byte. It is the R-F2834 masking defect recurring, in the very
    mechanism R-F3128 introduced to prevent it, and it is exactly what R-F3128's
    docstring already promised ("stamped only on success, so a blocked fetch or a
    refused gate retries on the next read instead of freezing an UNKNOWN").
    """
    if cap_id != "issuer_report":
        return False
    iss = (profile or {}).get("issuer_financials")
    if not isinstance(iss, dict) or not iss:
        return False                      # never attempted here — nothing to judge
    if iss.get("ok"):
        return False                      # answered
    # G1 said the issuer publishes no such document. We looked; that is a real
    # negative, not a malfunction — stamp it and stop paying to re-look.
    gates = iss.get("gates") if isinstance(iss.get("gates"), dict) else {}
    if gates.get("provenance") is False:
        return False
    # Everything else (fetch refused, timeout, unparseable, no LLM, model error)
    # is an obstacle on OUR side. Leave unstamped so the next read retries.
    return True


def _stamp_capabilities(profile: dict) -> None:
    """Record the capabilities that produced this profile, preserving unknown ones.

    R-F3161: a capability whose last attempt failed TRANSIENTLY is deliberately left
    unstamped so the next read retries it. Stamping everything unconditionally — the
    prior behaviour — made one bad fetch permanent.
    """
    have = set((profile or {}).get(_CAPABILITY_KEY) or [])
    earned = {
        cap for cap in FINANCIAL_CAPABILITIES
        if not _capability_retry_needed(cap, profile)
    }
    # Never REMOVE a stamp another build earned; only withhold one we did not.
    have -= {c for c in FINANCIAL_CAPABILITIES if _capability_retry_needed(c, profile)}
    profile[_CAPABILITY_KEY] = sorted(have | earned)


async def backfill_missing_capabilities(
    profile: dict,
    *,
    name: str,
    jurisdiction_iso2: str,
    registration_number: str = "",
) -> bool:
    """Run the enrichers this profile predates. Returns True if anything changed.

    Each capability is stamped ONLY on success. A failure leaves it missing so the
    next read retries — stamping on failure would make the profile look complete
    while carrying no evidence, re-creating the very defect this closes.
    """
    changed = False
    for cap_id in missing_capabilities(profile):
        enricher = FINANCIAL_CAPABILITIES.get(cap_id)
        if enricher is None:
            continue
        try:
            attached = await enricher(
                profile, name, jurisdiction_iso2, registration_number
            )
        except Exception as e:  # noqa: BLE001 — a broken source must not fail the DD
            logger.warning(
                "[R-F2834] capability %r failed for %r: %s — left UNSTAMPED so it "
                "retries on the next read", cap_id, name, e,
            )
            continue
        # Stamp on a clean run even when it attached nothing: the source was
        # consulted, and "consulted, found nothing" is valid negative evidence.
        #
        # R-F3161 — but only when the enricher actually REACHED that conclusion. It
        # signals a transient obstacle by RETURNING FALSE (not by raising), so the
        # `except` above never sees it and a timed-out fetch was being stamped as
        # "consulted". Leave those unstamped so the next read retries.
        if _capability_retry_needed(cap_id, profile):
            logger.info(
                "[R-F3161] capability %r did not complete for %r — left UNSTAMPED so "
                "it retries (stamping would freeze this UNKNOWN in the vault)",
                cap_id, name,
            )
            continue
        have = set(profile.get(_CAPABILITY_KEY) or [])
        have.add(cap_id)
        profile[_CAPABILITY_KEY] = sorted(have)
        if attached:
            changed = True
    return changed


async def assess(
    name: str,
    *,
    jurisdiction_iso2: str = "",
    registration_number: str = "",
    entity_type: str = "company",
    canonical_id: str | None = None,
    use_vault: bool = True,
    use_search: bool = True,
    max_age_days: float = 30.0,
) -> dict:
    """Multi-jurisdiction financial-health assessment (the public entry point).

    Pipeline (operator directive 2026-07-02 — Vault + Search, not Vault-only, not US/UK-only):
      1. VAULT (memory): a fresh registered profile for this entity (ANY jurisdiction) → serve it.
      2. SEARCH — SEC EDGAR (structured US financials).
      3. SEARCH — web financial footprint (cross-jurisdiction) when SEC has no structured data.
      4. REGISTER the merged profile back to the Vault so ARIA accumulates value-added
         company info over time across jurisdictions (§7/§15 pay-once-remember-forever).
    Never raises; honest UNKNOWN when nothing is found anywhere (never a false clean bill).
    """
    name = (name or "").strip()
    if not name:
        return {"source": "financial_health", "entity": name, "data_available": False,
                "health_verdict": "UNKNOWN", "financials": {}, "ratios": {},
                "summary": "No entity name provided — financial health UNKNOWN."}

    canonical = canonical_id
    if not canonical:
        try:
            from . import dd_versioning as _ver
            canonical = _ver.canonical_entity_id(
                entity_type=entity_type, name=name,
                jurisdiction_iso2=jurisdiction_iso2 or None,
                registration_number=registration_number or None,
            )
        except Exception:
            canonical = None

    # ── R-F2834: capability versioning (see FINANCIAL_CAPABILITIES below) ──────
    # 1) VAULT — pay-once, any jurisdiction.
    if use_vault and canonical:
        try:
            import time as _t
            from .dd_vault import get_vault
            cached = get_vault().get_financial_profile(canonical)
            if cached and cached.get("_vault_updated_at"):
                age_days = (_t.time() - float(cached["_vault_updated_at"])) / 86400.0
                if age_days <= max_age_days:
                    cached["from_vault"] = True
                    cached["vault_age_days"] = round(age_days, 1)
                    # R-F2817 — the vault has no capability/schema version, so a
                    # profile written before a new evidence source existed would
                    # keep suppressing it for the whole freshness window. Backfill
                    # the missing evidence on read, then persist it so this stays
                    # pay-once (§15) rather than re-fetching on every assessment.
                    # R-F2834 — was a hardcoded call for ONE source (R-F2817), so
                    # every future evidence source would be silently masked for the
                    # whole 30-day window until someone remembered to add another.
                    # Now: run whatever THIS build has that the profile predates.
                    if use_search and await backfill_missing_capabilities(
                        cached, name=name, jurisdiction_iso2=jurisdiction_iso2,
                        registration_number=registration_number,
                    ):
                        cached["vault_enriched"] = True
                        try:
                            get_vault().set_financial_profile(
                                canonical, cached, entity_name=name,
                                jurisdiction=jurisdiction_iso2)
                        except Exception as e:
                            logger.debug("vault re-register after enrichment failed: %s", e)
                    _refresh_derived_text(cached)     # R-F3043
                    return cached
        except Exception as e:
            logger.debug("vault financial lookup failed: %s", e)

    # 2) SEARCH — SEC EDGAR structured (US-listed).
    result = await _assess_sec_edgar(name)
    result["canonical_entity_id"] = canonical

    # 2b) REGISTRY ACCOUNTS — GB statutory filings (R-F2782 phase 1).
    #
    # Runs only when SEC EDGAR produced nothing, i.e. exactly the non-US case that
    # previously fell straight through to a link-only footprint and an evidence-free
    # UNKNOWN. A live deep DD on BAE Systems (FTSE-100, fully public UK filings)
    # returned financial capacity UNKNOWN for this reason.
    #
    # It deliberately does NOT touch `data_available` / `has_financials`, so
    # `_verdict()` still returns UNKNOWN: filing metadata raises the EVIDENCE grade
    # (dated, primary-source, citable) without answering the financial-capacity
    # question. Never-false-clean is the point of this ticket, not a side condition.
    if use_search and not result.get("data_available"):
        await _enrich_with_registry_accounts(
            result, name, jurisdiction_iso2, registration_number)

    # 2c) REGISTRY FIGURES — GB iXBRL balance-sheet figures (R-F2782 phase 2, R-F3016).
    # Unlike phase-1 metadata, this DOES answer financial capacity (data_available /
    # has_financials) from real filed figures → a bounded SOLVENCY verdict. GB small/mid
    # companies only; large listed PLCs file PDF group accounts (no iXBRL) → stays UNKNOWN.
    if use_search and not result.get("has_financials"):
        await _enrich_with_registry_figures(
            result, name, jurisdiction_iso2, registration_number)

    # 3) SEARCH — web financial footprint (cross-jurisdiction), when SEC has no structured data.
    if use_search and not result.get("data_available"):
        try:
            fp = await _search_financial_footprint(name, jurisdiction_iso2)
            if fp and fp.get("found"):
                result["search_footprint"] = fp
                # R-F3128 — ONE implementation. This was an inline copy, which is
                # how the vault path (assess() returns a cached profile verbatim)
                # skipped it entirely on QinetiQ. It is now the registered
                # `issuer_report` capability, so fresh and backfill share it.
                await _enrich_with_issuer_report(
                    result, name, jurisdiction_iso2, registration_number)
                if not result.get("data_available"):
                    _iss = result.get("issuer_financials") or {}
                    result["summary"] = (
                        (result.get("summary", "") + " ").strip()
                        + f" Search surfaced {len(fp['sources'])} public financial "
                          "reference(s) (links available). The issuer's own report was "
                          f"NOT usable for a solvency read: {_iss.get('reason', 'not attempted')} "
                          "(never-false-clean: still UNKNOWN)."
                    ).strip()
        except Exception as e:
            logger.debug("financial search footprint failed: %s", e)

    # 4) REGISTER to the Vault (accumulate across jurisdictions).
    if use_vault and canonical:
        try:
            from .dd_vault import get_vault
            # R-F2834 — stamp the capability set that produced this profile. An
            # unstamped fresh profile looks stale to the very next read and would
            # be re-enriched pointlessly, breaking pay-once (§15).
            _stamp_capabilities(result)
            get_vault().set_financial_profile(
                canonical, result, entity_name=name, jurisdiction=jurisdiction_iso2)
        except Exception as e:
            logger.debug("vault financial register failed: %s", e)

    return result


def financial_health_findings(result: dict) -> list[dict]:
    """Map an assessment into DD Finding dicts (title/detail/severity/source/confidence)."""
    if not isinstance(result, dict):
        return []
    # R-F3460 — the source label must name where the figures ACTUALLY came from.
    #
    # THE DEFECT (live, Babcock International Group PLC). The report carried:
    #
    #   "Financial health: STRONG ... read from the issuer's own published report
    #    (Annual Report and Financial Statements 2026) ... Source: sec_edgar_financials"
    #
    # while a finding two sections above said, correctly, that EDGAR holds only ADR
    # registration forms for this entity and "does NOT evidence this entity's financials".
    # The label was hardcoded for every finding regardless of provenance, so the report
    # attributed the numbers to the one source it had just told the reader was empty.
    #
    # A reader who checks provenance before relying on a figure is the reader this
    # product is for; sending them to the wrong source is worse than saying nothing.
    # The `_uk` branch below already derived its own label — the issuer-report path
    # (R-F3128) simply never got one.
    if result.get("issuer_report_verified"):
        src = "issuer_annual_report"
    else:
        src = "sec_edgar_financials"
    verdict = result.get("health_verdict", "UNKNOWN")
    findings: list[dict] = []
    if not result.get("data_available"):
        # Honest UNKNOWN finding — explicitly NOT a clean bill of health.
        findings.append({
            # Source-scoped (R-F2328): SEC EDGAR only covers US-listed filers, so this states
            # "no US/SEC filings" — NOT "no financials anywhere". A non-US entity may still
            # file with its home registry (e.g. UK Companies House, surfaced by the identity
            # layer), so an absolute "not publicly filed" would contradict that data. Stays
            # honestly UNKNOWN either way (never a clean bill).
            "title": "Financial health — no US-listed (SEC EDGAR) filings",
            "detail": result.get("summary") or result.get("reason")
                      or "No SEC/EDGAR (US-listed) financials available — UNKNOWN, not a clean bill.",
            "severity": "info",
            "source": src,
            "confidence": "CONFIRMED",
        })
    else:
        _uk = result.get("uk_balance_sheet")
        _mt = result.get("matched_title")
        # R-F3016 — a UK balance-sheet verdict cites Companies House, not SEC EDGAR, and
        # is explicitly a solvency (not profitability) read.
        # R-F3460 — the TITLE names the source too. "Financial health: STRONG" with no
        # qualifier reads as a filings-derived verdict; the issuer's own annual report is
        # a primary source but a different one, and the reader is entitled to know which.
        _title = (f"Financial health: {verdict} — UK filed accounts (balance sheet)"
                  if _uk else
                  f"Financial health: {verdict} — issuer's published annual report"
                  if result.get("issuer_report_verified") else
                  f"Financial health: {verdict}" + (f" — {_mt} (SEC EDGAR)" if _mt else ""))
        findings.append({
            "title": _title,
            "detail": result.get("summary", ""),
            # Finding severity enum: info | amber | red | hard_stop.
            "severity": {"DISTRESSED": "red", "WEAK": "amber", "STABLE": "info",
                         "STRONG": "info", "UNKNOWN": "info"}.get(verdict, "info"),
            "source": "companies_house_accounts" if _uk else src,
            "confidence": "CONFIRMED",
        })
        for flag in result.get("distress_flags", []):
            findings.append({
                "title": "Financial distress signal",
                "detail": flag,
                "severity": "red" if "negative shareholders" in flag else "amber",
                "source": src,
                "confidence": "CONFIRMED",
            })
    # Search element (any jurisdiction) — value-added public financial references.
    fp = result.get("search_footprint") or {}
    if fp.get("found") and fp.get("sources"):
        refs = "; ".join(s.get("url", "") for s in fp["sources"][:5] if s.get("url"))
        findings.append({
            "title": f"Financial references found via search ({len(fp['sources'])})",
            "detail": f"Public financial references (annual reports / filings / disclosures): {refs}. "
                      "Figures not extracted — for analyst review.",
            "severity": "info",
            "source": "financial_search",
            "confidence": "PROBABLE",
        })
    return findings
