"""SEC EDGAR — entity-scoped lookup for DD.

Coverage
────────
  - lookup_by_name(name)       → company_tickers.json search + submissions fetch
  - lookup_by_cik(cik)         → direct submissions fetch
  - recent_filings(cik, forms) → recent 10-K/10-Q/8-K/13D/13G/3-4-5 filings

What DD gets from SEC EDGAR
───────────────────────────
  - Confirmed US public company identity (CIK as a durable identifier)
  - Recent material events (8-K filings flag cybersecurity incidents,
    defence-contract awards, executive departures, investigations)
  - 10-K / 10-Q financial filings (audited revenue, segments, risk
    factors) — much richer than Companies-House-equivalent filings
  - Ownership disclosures (13D/13G filings show who owns >5%)
  - Insider transactions (Form 3/4/5 — executive share dealings)

Free, no API key, user-agent header is the only requirement.

Past incident driving this module
─────────────────────────────────
2026-04-18: DD audit discovered that despite `apis/sources/sec_edgar.mjs`
existing on the Node side, its output was briefing-shaped (today's
global 8-Ks) and never reached the Python DD orchestrator. Any US
entity going through DD got ZERO SEC filings — only whatever the
company voluntarily published on its own website. For a pro DD
service this is unacceptable.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import _common
from ..engine_wiring import wired

logger = logging.getLogger("aria.sources.sec_edgar")

_SOURCE = "sec_edgar"
_AUTH = "anonymous"  # just a user-agent header
_BASE_DATA = "https://data.sec.gov"
_BASE_WWW = "https://www.sec.gov"
_TICKERS_URL = f"{_BASE_WWW}/files/company_tickers.json"

# SEC requires a descriptive User-Agent with a contact; they rate-limit
# to 10 req/sec per IP. Both data.sec.gov and www.sec.gov honour this.
_UA = "ARIA-DD-Orchestrator research@arkmurus.com"

# In-process cache for the 10k-company ticker file — refreshed daily.
_TICKER_CACHE: dict[str, Any] = {"fetched_at": 0.0, "companies": []}
_TICKER_TTL_S = 86400  # 24h


async def _load_tickers() -> list[dict]:
    """Fetch the SEC's company_tickers.json, cache for 24h.

    The file is a dict keyed by int → {"cik_str": int, "ticker": str,
    "title": str}. We normalise into a list of dicts for searchability.
    """
    now = time.time()
    if _TICKER_CACHE["companies"] and (now - _TICKER_CACHE["fetched_at"] < _TICKER_TTL_S):
        return _TICKER_CACHE["companies"]

    data = await _common.http_get_json(_TICKERS_URL, headers={"User-Agent": _UA})
    if not isinstance(data, dict):
        logger.warning("[sec_edgar] ticker file fetch returned unexpected shape")
        return _TICKER_CACHE["companies"]  # keep stale cache if present

    companies = []
    for _idx, entry in data.items():
        if not isinstance(entry, dict):
            continue
        cik = entry.get("cik_str")
        if cik is None:
            continue
        companies.append({
            "cik": int(cik),
            "cik_str": str(cik).zfill(10),
            "ticker": (entry.get("ticker") or "").upper(),
            "title": entry.get("title") or "",
        })
    _TICKER_CACHE["companies"] = companies
    _TICKER_CACHE["fetched_at"] = now
    logger.info("[sec_edgar] ticker cache refreshed (%d companies)", len(companies))
    return companies


async def _fetch_submissions(cik: str | int) -> dict | None:
    """Get the full submissions index for one CIK.

    Returns the raw SEC JSON — `filings.recent` has parallel arrays of
    form / filingDate / accessionNumber / primaryDocument / etc.
    """
    cik_str = str(cik).zfill(10)
    url = f"{_BASE_DATA}/submissions/CIK{cik_str}.json"
    return await _common.http_get_json(url, headers={"User-Agent": _UA})


def _parse_recent_filings(submissions: dict, limit: int = 20) -> list[dict]:
    """Turn the parallel-arrays shape into a flat list of filing dicts."""
    if not submissions:
        return []
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    acc_nums = recent.get("accessionNumber") or []
    primary = recent.get("primaryDocument") or []
    report_dates = recent.get("reportDate") or []
    items = recent.get("items") or []

    cik_str = str(submissions.get("cik") or "").zfill(10)
    name = submissions.get("name") or ""

    out = []
    for i in range(min(len(forms), limit)):
        acc = acc_nums[i] if i < len(acc_nums) else ""
        doc = primary[i] if i < len(primary) else ""
        acc_nodash = acc.replace("-", "")
        filing_url = (
            f"{_BASE_WWW}/Archives/edgar/data/{int(cik_str)}/"
            f"{acc_nodash}/{doc}" if acc and doc else
            f"{_BASE_WWW}/cgi-bin/browse-edgar?action=getcompany&CIK={cik_str}&type={forms[i]}"
        )
        out.append({
            "form": forms[i],
            "filing_date": dates[i] if i < len(dates) else "",
            "report_date": report_dates[i] if i < len(report_dates) else "",
            "accession_number": acc,
            "primary_document": doc,
            "items": items[i] if i < len(items) else "",
            "filing_url": filing_url,
            "company_name": name,
            "cik": cik_str,
        })
    return out


# ── Material-event keywords — 8-K items that matter for DD ─────────────────
# Not used to FILTER out filings, but to annotate each hit with a severity
# hint so the orchestrator knows a cybersecurity incident is worth a RED
# finding while a routine Regulation FD filing is INFO.
_MATERIAL_HINTS: dict[str, str] = {
    "cybersecurity": "RED — cybersecurity incident disclosed",
    "ransomware": "RED — ransomware / data breach disclosed",
    "data breach": "RED — data breach disclosed",
    "resignation": "AMBER — executive resignation / departure",
    "termination": "AMBER — executive termination",
    "sanction": "RED — sanctions-related filing",
    "export control": "RED — export-control-related filing",
    "debarment": "RED — debarment / suspension filing",
    "investigation": "AMBER — disclosed investigation",
    "bankruptcy": "RED — bankruptcy / financial distress",
    "going concern": "RED — going-concern doubt raised",
    "material weakness": "AMBER — internal-control material weakness",
    "restatement": "AMBER — financial restatement",
    "acquisition": "INFO — acquisition disclosed",
    "merger": "INFO — merger disclosed",
}


def _annotate_severity(filing: dict) -> str:
    """Return a severity tag based on filing form + item description."""
    form = filing.get("form", "")
    items = (filing.get("items") or "").lower()

    # 8-K items carry the most-watched material events
    if form == "8-K":
        for needle, tag in _MATERIAL_HINTS.items():
            if needle in items:
                return tag
        return "INFO — 8-K material event"
    if form == "10-K":
        return "INFO — annual report (review risk factors)"
    if form == "10-Q":
        return "INFO — quarterly report"
    if form.startswith("SC 13"):
        return "INFO — beneficial-ownership disclosure"
    if form.startswith("4"):
        return "INFO — insider transaction"
    return "INFO"


# ── Public API ─────────────────────────────────────────────────────────────

@wired(module="sources.sec_edgar", summary="SEC EDGAR lookup for {name}")
async def lookup(
    name: str,
    *,
    cik: str | int | None = None,
    limit: int = 15,
    threshold: float = 0.85,
) -> dict:
    """Entity-scoped SEC EDGAR lookup.

    Args:
        name: Company name. Required unless cik is provided.
        cik: Optional SEC CIK if the caller already knows it (faster path).
        limit: Max recent filings to return per matched company.
        threshold: Fuzzy-match cutoff when resolving name → CIK.

    Returns the canonical source-result dict. `hits` is a list of filing
    dicts, each with form / filing_date / accession_number / filing_url
    / cik / company_name / severity_hint.

    R-F572 (2026-05-16) — defaults hardened. Pre-R-F572 the 0.62
    threshold produced false-positive filer matches that polluted DD
    reports with unrelated SEC issuers, every time:
      Rosoboronexport → "E.ON SE"           (German utility)
      Aselsan A.S.    → "ASHLAND INC."      (US chemicals)
      Acme Widgets    → "CME GROUP INC."    (US exchange)
    Threshold bumped 0.62 → 0.85 + name-overlap gate after the fuzzy
    filter, mirroring the R-F569 sanctions-match pattern. Real SEC
    issuer matches (e.g. "Embraer S.A." → "EMBRAER S.A./ADR" — token
    'embraer' overlaps) still pass cleanly.
    """
    started = time.time()
    query = {"name": name, "cik": cik}
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH,
        citation_url=(
            f"{_BASE_WWW}/cgi-bin/browse-edgar?action=getcompany"
            f"&{'CIK=' + str(cik) if cik else 'company=' + (name or '').replace(' ', '+')}"
        ),
    )

    try:
        # Resolve name → CIK(s) if needed
        matched: list[dict] = []
        if cik is not None:
            matched = [{"cik_str": str(cik).zfill(10), "title": name or ""}]
        elif name:
            tickers = await _load_tickers()
            if not tickers:
                return _common.error_result(
                    _SOURCE, query,
                    "ticker file unavailable (SEC EDGAR may be rate-limiting)",
                    auth=_AUTH, started_at=started,
                )
            # Fuzzy match on title
            scored = _common.fuzzy_filter(
                tickers, name, name_key="title",
                threshold=threshold, max_hits=5,
            )
            # R-F572 (2026-05-16) — name-overlap gate after fuzzy_filter.
            # Pre-R-F572 the score-only filter let through pairs that
            # share a high SequenceMatcher ratio on short strings (CME
            # GROUP / Acme Widgets — different normalised tokens, but the
            # raw ratio survived 0.62). The gate adds token-overlap
            # discipline:
            #   - score≥0.95 (near-exact normalised match) → pass.
            #     Handles e.g. BAE Systems plc → BAE SYSTEMS PLC/ADR,
            #     where the only surviving token after corp-suffix strip
            #     is "bae" (3 chars) but score=1.0 confirms the match.
            #   - ≥2 shared meaningful tokens → pass.
            #   - 1 shared token of ≥5 chars → pass (Embraer / Aselsan).
            #   - Anything else → reject (logs at debug level for audit).
            from .._sanctions_classify import _tokenize_entity_name
            _query_tokens = _tokenize_entity_name(name)
            gated = []
            for _hit in scored:
                _cand = (_hit.get("title") or "").strip()
                if not _cand:
                    continue
                _score = float(_hit.get("_match_score") or 0.0)
                if _score >= 0.95:
                    # Exact-or-near-exact normalised match — trust it.
                    gated.append(_hit)
                    continue
                _cand_tokens = _tokenize_entity_name(_cand)
                _shared = _query_tokens & _cand_tokens
                if len(_shared) >= 2:
                    gated.append(_hit)
                elif len(_shared) == 1 and len(next(iter(_shared))) >= 5:
                    gated.append(_hit)
                else:
                    logger.debug(
                        "[sec_edgar] R-F572 gate rejected '%s' vs '%s': "
                        "shared tokens=%s (score=%.3f)",
                        name, _cand, list(_shared), _score,
                    )
            matched = gated
        else:
            return _common.error_result(
                _SOURCE, query, "must supply name or cik",
                auth=_AUTH, started_at=started,
            )

        all_hits: list[dict] = []
        for m in matched[:3]:  # top 3 matches only — avoid rate-limit storms
            subs = await _fetch_submissions(m["cik_str"])
            if not subs:
                continue
            filings = _parse_recent_filings(subs, limit=limit)
            for f in filings:
                f["severity_hint"] = _annotate_severity(f)
                f["match_score"] = m.get("_match_score", 1.0)
                all_hits.append(f)

        result["hits"] = all_hits
        return _common.finalise(result, started)

    except Exception as e:
        logger.warning("[sec_edgar] lookup failed for %r: %s", name, e)
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )


async def is_available() -> bool:
    """Health-check ping for the cross-server health endpoint."""
    try:
        data = await _common.http_get_json(
            _TICKERS_URL, headers={"User-Agent": _UA}, timeout=6.0,
        )
        return isinstance(data, dict) and len(data) > 100
    except Exception:
        return False
