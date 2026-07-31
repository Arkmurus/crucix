"""Regex-based fact extractor — zero LLM.

Purpose
───────
Past incident 2026-04-18: the per-article LLM call in
deep_researcher._analyse_article() was extracting facts (dates, revenue
figures, named people, locations) by asking Claude/DeepSeek to
structure the article. On a 10-article research run that's 10 LLM
calls just for extraction, before any synthesis. Typical cost on
Anthropic at 4.7: $0.02-0.06 per research turn just in extraction.

90% of those extractions are pattern-matching work an LLM should never
have been doing — dates, currency amounts, phone numbers, email
addresses, named executives, company registration IDs, revenue claims,
employee counts. This module does all of that with regex + tight
heuristics. The LLM is now only responsible for SYNTHESIS (turning
facts into a narrative), not extraction.

Public API
──────────
  extract(text: str, base_url: str = "") -> dict
      Returns {
          "dates":       [{"iso":"2024-03-15", "raw":"March 15, 2024", ...}],
          "amounts":     [{"currency":"USD","value":1500000000,"raw":"$1.5 billion",...}],
          "emails":      [str],
          "phones":      [str],
          "urls":        [str],
          "entities":    [{"type":"org|person","name":str,"contexts":[str]}],
          "founded":     [{"year":int,"raw":str,"confidence":str}],
          "revenue":     [{"currency":str,"value":int,"year":int,"raw":str}],
          "headcount":   [{"count":int,"raw":str,"year":int|None}],
          "reg_numbers": [{"jurisdiction":str,"number":str,"raw":str}],
          "ceos":        [str],     # named chief execs / founders
          "addresses":   [str],
      }

All functions pure — no network, no LLM, no disk I/O.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.extractors.facts")


# ── R-F3564 — every extractor failure here was swallowed to a debug log ──────
#
# `_try` wraps TEN extractors, among them `_extract_reg_numbers` and
# `_extract_ceos`. A silent failure in either strips registration numbers or
# named officers out of a DD, and the report then reads "not found" — a claim
# nothing verified. The empty list a crash produces is indistinguishable from
# the empty list a page with no such data produces, so the DD cannot tell the
# difference and neither could the brain.
#
# Failure-side wiring is deduped 1h (R-F66) and capped at 500 (R-F1669), so a
# systematically broken extractor reports once an hour rather than once a page.
def _fact_part_failed(part: str, exc: Exception) -> None:
    logger.debug("[facts] %s failed: %s", part, exc)
    try:
        from ..engine_wiring import wire_failure
        wire_failure(
            module="extractors_facts",
            detail=f"{part} extraction failed: {type(exc).__name__}: {exc}"[:400],
            gap_type="engine_failure",
            source="extractors/facts.py",
        )
    except Exception:                       # noqa: BLE001
        pass    # wiring must never break extraction


def _facts_run_succeeded(counts: dict) -> None:
    """Report the COUNTS extracted, so "ran and found nothing" is legible."""
    try:
        from ..engine_wiring import wire_success
        wire_success(
            module="extractors_facts",
            summary="fact extraction completed",
            detail=" ".join(f"{k}={v}" for k, v in sorted(counts.items())),
            confidence="CONFIRMED",
        )
    except Exception:                       # noqa: BLE001
        pass


# ── Dates ──────────────────────────────────────────────────────────────────
# We capture multiple date forms and normalise to ISO (YYYY-MM-DD) where
# possible. Year-only mentions go into founded/headcount context, not dates.

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_PATTERNS = [
    # ISO: 2024-03-15
    (re.compile(r"\b(20\d{2}|19\d{2})-(\d{2})-(\d{2})\b"), "iso"),
    # US: March 15, 2024 or Mar 15, 2024
    (re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2}|19\d{2})\b",
        re.IGNORECASE,
    ), "month_day_year"),
    # UK/EU: 15 March 2024
    (re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\.?\s+(20\d{2}|19\d{2})\b",
        re.IGNORECASE,
    ), "day_month_year"),
    # Numeric: 15/03/2024 or 03/15/2024 — ambiguous, keep raw only
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2}|19\d{2})\b"), "numeric"),
]


def _extract_dates(text: str) -> list[dict]:
    """Dates found in text, normalised to ISO where unambiguous."""
    out: list[dict] = []
    seen: set[str] = set()
    for pattern, shape in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(0)
            if raw in seen:
                continue
            seen.add(raw)
            iso = ""
            try:
                if shape == "iso":
                    iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                elif shape == "month_day_year":
                    month = _MONTH_NAMES.get(m.group(1).lower().rstrip("."))
                    day = int(m.group(2))
                    year = int(m.group(3))
                    if month and 1 <= day <= 31:
                        iso = f"{year:04d}-{month:02d}-{day:02d}"
                elif shape == "day_month_year":
                    day = int(m.group(1))
                    month = _MONTH_NAMES.get(m.group(2).lower().rstrip("."))
                    year = int(m.group(3))
                    if month and 1 <= day <= 31:
                        iso = f"{year:04d}-{month:02d}-{day:02d}"
                # numeric shape left ambiguous — no iso
            except Exception:
                iso = ""
            out.append({"raw": raw, "iso": iso, "shape": shape})
            if len(out) >= 100:
                return out
    return out


# ── Amounts / currencies ───────────────────────────────────────────────────
# Handles $, £, €, ¥, ₹, AED, SAR, SGD, etc. with scale words (million,
# billion, bn, M, B, k).

_CURRENCY_SYMBOLS = {
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR",
    "₽": "RUB", "₩": "KRW", "R$": "BRL", "A$": "AUD", "C$": "CAD",
    "S$": "SGD", "HK$": "HKD",
}
_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "CHF", "JPY", "CNY", "INR", "BRL", "AUD", "CAD",
    "SGD", "AED", "SAR", "KRW", "ZAR", "TRY", "PLN", "CZK", "HUF", "RON",
    "SEK", "NOK", "DKK", "NGN", "KES", "MXN", "ILS",
}
_SCALE_WORDS = {
    "thousand": 1_000, "k": 1_000,
    "million": 1_000_000, "m": 1_000_000, "mm": 1_000_000,
    "billion": 1_000_000_000, "bn": 1_000_000_000, "b": 1_000_000_000,
    "trillion": 1_000_000_000_000, "tn": 1_000_000_000_000, "tr": 1_000_000_000_000,
}

# Matches: $1.5 billion, £500m, EUR 2bn, USD 1,500,000, 1.2M USD
_AMOUNT_RE = re.compile(
    r"""(?ix)
    (?:
        # Leading symbol + number + optional scale + optional trailing code
        (?P<sym>\$|£|€|¥|₹|₽|₩|R\$|A\$|C\$|S\$|HK\$)\s*
        (?P<num1>\d[\d,]*(?:\.\d+)?)
        \s*(?P<scale1>thousand|billion|million|trillion|bn|tn|mm|[kmbt])?\b
        \s*(?P<code1>USD|EUR|GBP|CHF|JPY|CNY|INR|BRL|AUD|CAD|SGD|AED|SAR|KRW|ZAR|TRY|PLN|CZK|HUF|RON|SEK|NOK|DKK|NGN|KES|MXN|ILS)?
        |
        # Leading code + number + optional scale
        (?P<code2>USD|EUR|GBP|CHF|JPY|CNY|INR|BRL|AUD|CAD|SGD|AED|SAR|KRW|ZAR|TRY|PLN|CZK|HUF|RON|SEK|NOK|DKK|NGN|KES|MXN|ILS)\s*
        (?P<num2>\d[\d,]*(?:\.\d+)?)
        \s*(?P<scale2>thousand|billion|million|trillion|bn|tn|mm|[kmbt])?\b
        |
        # Number + scale + currency code trailing
        (?P<num3>\d[\d,]*(?:\.\d+)?)
        \s*(?P<scale3>thousand|billion|million|trillion|bn|tn|mm|[kmbt])?
        \s+(?P<code3>USD|EUR|GBP|CHF|JPY|CNY|INR|BRL|AUD|CAD|SGD|AED|SAR|KRW|ZAR|TRY|PLN|CZK|HUF|RON|SEK|NOK|DKK|NGN|KES|MXN|ILS)\b
    )
    """
)


def _extract_amounts(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        num_str = (
            m.group("num1") or m.group("num2") or m.group("num3") or ""
        ).replace(",", "")
        scale_str = (
            m.group("scale1") or m.group("scale2") or m.group("scale3") or ""
        ).lower()
        sym = m.group("sym") or ""
        code = (
            m.group("code1") or m.group("code2") or m.group("code3") or ""
        ).upper()
        currency = _CURRENCY_SYMBOLS.get(sym) or code or "UNKNOWN"
        try:
            value = float(num_str)
            multiplier = _SCALE_WORDS.get(scale_str, 1) if scale_str else 1
            value_final = int(value * multiplier)
        except Exception:
            continue
        out.append({
            "raw": raw[:80],
            "currency": currency,
            "value": value_final,
            "scale": scale_str or "",
        })
        if len(out) >= 60:
            return out
    return out


# ── Emails, phones, URLs ───────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
_URL_RE = re.compile(
    r"https?://[^\s<>\"'\]\)]+",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    # Phone candidates must have at least ONE separator (space/dash/dot/
    # paren) between digit groups, or start with +. This excludes pure
    # runs of digits — registration numbers, IDs, CIKs — that would
    # otherwise masquerade as phone numbers. The final digit-count
    # filter (_extract_contacts) still requires 7-15 digits total.
    r"\+\d{1,4}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{1,4}[\s\-.]?\d{1,4}(?:[\s\-.]?\d{1,4})?"
    r"|"
    r"\(?\d{1,4}\)?[\s\-.]+\d{1,4}[\s\-.]+\d{1,4}(?:[\s\-.]?\d{1,4})?",
)


def _extract_contacts(text: str) -> tuple[list[str], list[str], list[str]]:
    """Returns (emails, phones, urls). Deduplicated, capped.

    Phones require at least one separator between digit groups — a bare
    run of 7-15 digits won't match. This excludes registration numbers
    (IČO 46356466, HRB 12345, CIK 0000040533) from the phone bucket.
    """
    emails = sorted({m.group(0).lower() for m in _EMAIL_RE.finditer(text)})[:50]
    urls = sorted({m.group(0) for m in _URL_RE.finditer(text)})[:80]
    phones = []
    phones_seen: set[str] = set()
    for m in _PHONE_RE.finditer(text):
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 7 or len(digits) > 15:
            continue
        # Require at least one separator — else it's just digits
        if not re.search(r"[\s\-.()]", raw):
            continue
        if raw in phones_seen:
            continue
        phones_seen.add(raw)
        phones.append(raw)
        if len(phones) >= 30:
            break
    return emails, phones, urls


# ── Founded year / incorporation date ──────────────────────────────────────

_FOUNDED_RE = re.compile(
    r"\b(?:founded|established|incorporated|formed|created|set\s+up|"
    r"founding\s+year|year\s+of\s+(?:incorporation|founding))\s*"
    r"(?:in\s+|on\s+|:\s*|—\s*|-\s*)?"
    r"((?:(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+)?(?:19|20)\d{2})"
    r"\b",
    re.IGNORECASE,
)


def _extract_founded(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for m in _FOUNDED_RE.finditer(text):
        raw_year_str = m.group(1)
        year_match = re.search(r"(19|20)\d{2}", raw_year_str)
        if not year_match:
            continue
        year = int(year_match.group(0))
        if year in seen:
            continue
        seen.add(year)
        out.append({
            "year": year,
            "raw": m.group(0).strip()[:120],
            "confidence": "PROBABLE",
        })
        if len(out) >= 5:
            break
    return out


# ── Revenue claims ─────────────────────────────────────────────────────────
# Pattern: "$1.2B revenue in 2023" / "revenue of £500m (2024)" / "€2bn sales"

_REVENUE_CTX_RE = re.compile(
    r"""(?ix)
    (?:revenue|turnover|sales|top[\-\s]?line|income|takings|
       \b(?:gross|net)\s+(?:income|revenue|sales)\b)
    .{0,60}?
    """
)


def _extract_revenue(text: str) -> list[dict]:
    """Find revenue-context amount mentions. Pairs each amount with any
    4-digit year in the neighbourhood."""
    out: list[dict] = []
    amount_matches = list(_AMOUNT_RE.finditer(text))
    rev_spans: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in _REVENUE_CTX_RE.finditer(text)
    ]

    for amt in amount_matches:
        for rs, re_ in rev_spans:
            # Amount within ±80 chars of a revenue keyword
            if abs(amt.start() - re_) < 80 or abs(rs - amt.end()) < 80:
                raw_near = text[max(0, min(amt.start(), rs) - 20):
                                max(amt.end(), re_) + 20]
                year_m = re.search(r"\b(20\d{2}|19\d{2})\b", raw_near)
                # Parse the amount through the dedicated extractor
                num_str = (
                    amt.group("num1") or amt.group("num2") or amt.group("num3") or ""
                ).replace(",", "")
                scale_str = (
                    amt.group("scale1") or amt.group("scale2") or amt.group("scale3") or ""
                ).lower()
                sym = amt.group("sym") or ""
                code = (
                    amt.group("code1") or amt.group("code2") or amt.group("code3") or ""
                ).upper()
                currency = _CURRENCY_SYMBOLS.get(sym) or code or "UNKNOWN"
                try:
                    value = int(float(num_str) * _SCALE_WORDS.get(scale_str, 1))
                except Exception:
                    continue
                out.append({
                    "currency": currency,
                    "value": value,
                    "year": int(year_m.group(1)) if year_m else None,
                    "raw": raw_near.strip()[:160],
                })
                break
        if len(out) >= 15:
            break
    return out


# ── Employee count / headcount ─────────────────────────────────────────────

_HEADCOUNT_RE = re.compile(
    r"""(?ix)
    (?:
        (?:has|have|employs|with|over|more\s+than|approximately|about|around|roughly)?\s*
        (\d[\d,]*)\s*\+?\s*
        (?:employees|staff|workforce|headcount|people\s+on\s+staff|fte|full[\-\s]?time\s+equivalents?)
        |
        (?:employees|staff|workforce|headcount)\s*[:—–-]\s*(\d[\d,]*)\s*\+?
    )
    """
)


def _extract_headcount(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for m in _HEADCOUNT_RE.finditer(text):
        count_str = (m.group(1) or m.group(2) or "").replace(",", "")
        if not count_str:
            continue
        try:
            count = int(count_str)
        except Exception:
            continue
        if count < 5 or count > 5_000_000 or count in seen:
            continue
        seen.add(count)
        # Look for a year near the match
        ctx = text[max(0, m.start() - 60):m.end() + 60]
        year_m = re.search(r"\b(20\d{2}|19\d{2})\b", ctx)
        out.append({
            "count": count,
            "raw": m.group(0).strip()[:120],
            "year": int(year_m.group(1)) if year_m else None,
        })
        if len(out) >= 8:
            break
    return out


# ── Registration numbers ───────────────────────────────────────────────────

_REG_PATTERNS: list[tuple[str, re.Pattern]] = [
    # UK Companies House — 8 digits
    ("UK", re.compile(r"\b(?:company\s+(?:number|no|reg(?:istration)?)|companies\s+house|registered\s+in\s+england(?:\s+and\s+wales)?)[\s:No.#]*?(\d{7,8})\b", re.IGNORECASE)),
    # German HRB / HRA
    ("DE", re.compile(r"\bHR[AB]\s*(\d+)\b")),
    # Romanian CUI / CIF
    ("RO", re.compile(r"\bCUI\s*[:#]?\s*(\d{4,9})\b", re.IGNORECASE)),
    # Czech / Slovak IČO — 8 digits
    ("CZ_SK", re.compile(r"\bI[CČ]O\s*[:#]?\s*(\d{6,8})\b", re.IGNORECASE)),
    # French SIREN / SIRET
    ("FR", re.compile(r"\bSIRE[NT]\s*[:#]?\s*([\d\s]{9,14})\b", re.IGNORECASE)),
    # Spanish NIF / CIF
    ("ES", re.compile(r"\b[NC]IF\s*[:#]?\s*([A-Z]\d{7}[A-Z0-9]|\d{8}[A-Z])\b")),
    # Polish NIP / KRS
    ("PL", re.compile(r"\b(?:NIP|KRS)\s*[:#]?\s*(\d{7,10})\b", re.IGNORECASE)),
    # Brazilian CNPJ
    ("BR", re.compile(r"\bCNPJ\s*[:#]?\s*(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b", re.IGNORECASE)),
    # SEC CIK (10 digits, often padded)
    ("US_SEC", re.compile(r"\bCIK\s*[:#]?\s*(\d{4,10})\b", re.IGNORECASE)),
    # Turkish MERSIS
    ("TR", re.compile(r"\bMERSIS\s*[:#]?\s*(\d{16})\b", re.IGNORECASE)),
]


def _extract_reg_numbers(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for jurisdiction, pattern in _REG_PATTERNS:
        for m in pattern.finditer(text):
            num = m.group(1).strip().replace(" ", "")
            key = (jurisdiction, num)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "jurisdiction": jurisdiction,
                "number": num,
                "raw": m.group(0).strip()[:80],
            })
            if len(out) >= 20:
                return out
    return out


# ── Executives / named people in leadership context ────────────────────────

_CEO_ROLES = (
    "CEO", "Chief Executive Officer", "Chief Executive", "Managing Director",
    "President", "Chairman", "Chairwoman", "Chair",
    "Founder", "Co-Founder", "Co-founder",
    "CFO", "Chief Financial Officer",
    "COO", "Chief Operating Officer",
    "CTO", "Chief Technology Officer",
)

_CEO_RE = re.compile(
    # Name then role: "Jane Doe, CEO" / "Dr. Jane Doe — CEO"
    r"\b((?:Dr|Mr|Ms|Mrs|Prof)\.?\s+)?([A-Z][a-zA-Z'\-]+\s+(?:[A-Z][a-zA-Z'\-]+\s+)?[A-Z][a-zA-Z'\-]+)"
    r"[,\s—–-]+\s*(" + "|".join(re.escape(r) for r in _CEO_ROLES) + r")\b"
    r"|"
    # Role then name (comma/dash/colon separator): "CEO Jane Doe",
    # "Chief Executive, Jane Doe", "CEO: Michal Strnad"
    r"\b(" + "|".join(re.escape(r) for r in _CEO_ROLES) + r")\s*[,:—–-]+\s*"
    r"(?:is\s+)?((?:Dr|Mr|Ms|Mrs|Prof)\.?\s+)?([A-Z][a-zA-Z'\-]+\s+(?:[A-Z][a-zA-Z'\-]+\s+)?[A-Z][a-zA-Z'\-]+)"
)


def _extract_ceos(text: str) -> list[str]:
    """Extract names in leadership roles. Regex has two alternatives:
      Alt A (name first): title-prefix=g1, name=g2, role=g3
      Alt B (role first): role=g4, title-prefix=g5, name=g6
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _CEO_RE.finditer(text):
        name = (m.group(2) or m.group(6) or "").strip()
        role = (m.group(3) or m.group(4) or "").strip()
        if not name or not role:
            continue
        name_norm = re.sub(r"\s+", " ", name).strip()
        if len(name_norm.split()) < 2 or len(name_norm) < 6:
            continue
        entry = f"{name_norm} ({role})"
        if entry in seen:
            continue
        seen.add(entry)
        out.append(entry)
        if len(out) >= 15:
            break
    return out


# ── Capitalised entity mentions (orgs + people) ────────────────────────────
# Cheap NER — captures multi-word capitalised noun phrases. Good-enough for
# surfacing "who is mentioned" without spaCy.

_ENTITY_RE = re.compile(
    r"\b("
    r"[A-Z][a-zA-Z0-9&-]+(?:\s+[A-Z][a-zA-Z0-9&-]+){1,5}"
    r"(?:\s+(?:Group|Holdings|Industries|Technologies|Systems|Aerospace|Defense|Defence|Limited|Ltd|Corp|Corporation|Inc|Plc|PLC|GmbH|SA|SAS|AB|AS|ASA|Oy|SpA|AG|KG|LLC|NV|BV|OOD|EAD|a\.s\.|s\.r\.o\.|s\.p\.a\.))?"
    r")\b"
)

# Filter list — stop words that look capitalised but aren't entities
_ENTITY_STOPLIST = {
    "The Company", "The Group", "The Organization", "The Organisation",
    "The United States", "The United Kingdom", "The Czech Republic",
    "This Year", "Last Year", "Next Year",
    "New York", "Los Angeles", "Hong Kong", "South Korea", "New Jersey",
    "Middle East", "South America", "Latin America",
}


def _extract_entity_mentions(text: str, limit: int = 30) -> list[dict]:
    """Grab capitalised noun phrases as candidate entities. Scored by
    frequency — multi-mention entities are more reliable signals."""
    counts: dict[str, int] = {}
    for m in _ENTITY_RE.finditer(text):
        name = m.group(0).strip()
        if len(name) < 6 or name in _ENTITY_STOPLIST:
            continue
        # Reject all-caps (usually acronyms or section headers)
        if name.isupper() and len(name.split()) == 1:
            continue
        counts[name] = counts.get(name, 0) + 1
    # Sort by mention count, then first-occurrence order
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return [{"name": n, "mentions": c} for n, c in ranked[:limit]]


# ── Addresses ──────────────────────────────────────────────────────────────

_POSTAL_RE = re.compile(
    # UK-shape postcode + surrounding line
    r"\b(?:[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b"
    # or US ZIP
    r"|\b\d{5}(?:-\d{4})?\b"
    # or numeric street line
    r"|\b\d{1,5}\s+[A-Z][a-zA-Z]+\s+(?:Street|Road|Avenue|Boulevard|Lane|Drive|Way|Court|Square|Circle|St|Rd|Ave|Blvd)\b",
)


def _extract_addresses(text: str) -> list[str]:
    """Rough address extraction — postcode + surrounding context."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _POSTAL_RE.finditer(text):
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 20)
        ctx = text[start:end].replace("\n", " ")
        ctx = re.sub(r"\s+", " ", ctx).strip()
        if len(ctx) < 10 or ctx in seen:
            continue
        seen.add(ctx)
        out.append(ctx[:200])
        if len(out) >= 15:
            break
    return out


# ── Public entry point ─────────────────────────────────────────────────────

def extract(text: str, *, base_url: str = "") -> dict:
    """Run the full fact-extraction pipeline on a block of text.

    Returns the structured facts dict. Empty dict on empty input.
    Safe to call on any string; no exceptions propagated.
    """
    if not text:
        return _empty_result()
    if len(text) > 200_000:
        # Cap very long inputs — extractors scan multiple regex passes,
        # OOM-ish on 1MB+ inputs. 200k is already ~40 pages of prose.
        text = text[:200_000]

    try:
        emails, phones, urls = _extract_contacts(text)
    except Exception as e:
        _fact_part_failed("contacts", e)
        emails, phones, urls = [], [], []

    def _try(fn, default, field: str):
        """`field` is the OUTPUT KEY, passed explicitly and not derived from
        `fn.__name__`. The consumer needs to know WHICH FACT was lost —
        "reg_numbers" maps to a DD line, an internal function name does not — and
        a derived name degrades to "<lambda>" the moment the callable is wrapped
        or swapped, which is exactly when the label matters most."""
        try:
            return fn(text)
        except Exception as e:
            _fact_part_failed(field, e)
            return default

    out = {
        "dates": _try(_extract_dates, [], "dates"),
        "amounts": _try(_extract_amounts, [], "amounts"),
        "emails": emails,
        "phones": phones,
        "urls": urls,
        "entities": _try(_extract_entity_mentions, [], "entities"),
        "founded": _try(_extract_founded, [], "founded"),
        "revenue": _try(_extract_revenue, [], "revenue"),
        "headcount": _try(_extract_headcount, [], "headcount"),
        "reg_numbers": _try(_extract_reg_numbers, [], "reg_numbers"),
        "ceos": _try(_extract_ceos, [], "ceos"),
        "addresses": _try(_extract_addresses, [], "addresses"),
    }
    _facts_run_succeeded({k: len(v) for k, v in out.items()})
    return out


def _empty_result() -> dict:
    return {
        "dates": [], "amounts": [], "emails": [], "phones": [], "urls": [],
        "entities": [], "founded": [], "revenue": [], "headcount": [],
        "reg_numbers": [], "ceos": [], "addresses": [],
    }
