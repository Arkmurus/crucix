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

import datetime as _dt
import logging
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
       health_verdict: STRONG|STABLE|WEAK|DISTRESSED|UNKNOWN, summary: str}
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

        base.update({
            "data_available": True,
            "latest_fy": latest_fy,
            "financials": {str(y): {k: v for k, v in series[y].items() if v is not None} for y in years},
            "ratios": {k: v for k, v in ratios.items() if v is not None},
            "altman_z": z,
            "altman_zone": zone,
            "distress_flags": flags,
            "health_verdict": verdict,
        })
        base["summary"] = _build_summary(matched_title, latest_fy, latest, ratios, z, zone, flags, verdict)
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
            hits = await ch.search_companies(name, limit=1)
            if hits:
                number = str(hits[0].get("company_number") or "").strip()
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
FINANCIAL_CAPABILITIES: dict = {
    # R-F2782/R-F2817 — GB statutory filings from Companies House.
    "registry_accounts": _enrich_with_registry_accounts,
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
    return [c for c in FINANCIAL_CAPABILITIES if c not in have]


def _stamp_capabilities(profile: dict) -> None:
    """Record the capabilities that produced this profile, preserving unknown ones."""
    have = set((profile or {}).get(_CAPABILITY_KEY) or [])
    profile[_CAPABILITY_KEY] = sorted(have | set(FINANCIAL_CAPABILITIES))


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

    # 3) SEARCH — web financial footprint (cross-jurisdiction), when SEC has no structured data.
    if use_search and not result.get("data_available"):
        try:
            fp = await _search_financial_footprint(name, jurisdiction_iso2)
            if fp and fp.get("found"):
                result["search_footprint"] = fp
                result["summary"] = (
                    (result.get("summary", "") + " ").strip()
                    + f" Search surfaced {len(fp['sources'])} public financial reference(s) "
                      "(links available) — figures not yet extracted (never-false-clean: still UNKNOWN)."
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
        _mt = result.get("matched_title")
        findings.append({
            "title": f"Financial health: {verdict}" + (f" — {_mt} (SEC EDGAR)" if _mt else ""),
            "detail": result.get("summary", ""),
            # Finding severity enum: info | amber | red | hard_stop.
            "severity": {"DISTRESSED": "red", "WEAK": "amber", "STABLE": "info",
                         "STRONG": "info", "UNKNOWN": "info"}.get(verdict, "info"),
            "source": src,
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
