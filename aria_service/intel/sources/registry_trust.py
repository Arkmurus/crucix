"""R-F3442 — County Court Judgments (CCJs) from the Registry Trust register.

WHY THIS EXISTS. IS-17b's pass condition says it outright: *"A CCJ is a money judgment:
its ABSENCE is a material finding, so an unsearched register is not a clean one."* Until
now nothing could answer it, so a DD that a buyer relied on for a credit-risk decision
simply had no CCJ line. The operator's requirement is that electing a CCJ search must
actually DELIVER one — a report that says "not completed" is honest, but honesty is not
capability.

ACCESS, verified 2026-07-29 against registry-trust.org.uk:
  * Registry Trust maintains the statutory Register of Judgments, Orders and Fines for
    England & Wales on behalf of the Ministry of Justice. It is the ONLY authoritative
    source; there is no free substitute and no open dataset.
  * There is **no public API**. What they sell, under commercial contract, is Bulk Data
    Services, Special Requests, Aggregated Datasets and Monitoring (new judgments against
    a company). 5.4M+ active E&W records, ~134,822 processed monthly.
    Contact: business@registry-trust.org.uk.
  * TrustOnline is the per-search consumer/business web service (~£6-£10 per search).

SO THE BACKEND IS PLUGGABLE, and the DATASET one is preferred:

  1. ``dataset``  — a licensed bulk extract on disk (``REGISTRY_TRUST_DATA_PATH``).
     Unlimited local lookups, no per-search fee, and it answers instantly. This is the
     shape their actual product takes and the one a DD engine should use.
  2. ``api``      — an HTTP endpoint IF one is provided under contract
     (``REGISTRY_TRUST_API_URL`` + ``REGISTRY_TRUST_API_KEY``).

**We do not scrape TrustOnline.** It is a paid service behind terms of use; automating it
would be both a licensing breach and a fragile dependency on someone's HTML. If neither
backend is configured this module says so and returns ``searched=False`` — it never
guesses, and it never lets "no data" become "no judgments".

FLIPPING IT ON is a credential change, not a code change: sign the data agreement, set
``REGISTRY_TRUST_DATA_PATH`` (or the API pair), and every elected CCJ search starts
answering. That is the same env-driven activation pattern CLAUDE.md §16 uses for ARIA-LLM.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Cache the parsed dataset; a bulk extract is large and static between refreshes.
_CACHE: dict[str, Any] = {"rows": None, "loaded_at": 0.0, "path": "", "error": ""}
_CACHE_TTL_S = 6 * 3600

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s&]")
#: Trailing company-form words. "ACME LTD" and "Acme Limited" must match the same record.
_SUFFIXES = (
    "limited", "ltd", "plc", "llp", "lp", "cic", "ltd.", "co", "company",
    "incorporated", "inc", "corporation", "corp",
)


def normalise_name(name: str) -> str:
    """Fold a party name to a comparable key.

    Deliberately conservative: case, punctuation, whitespace and trailing company-form
    words only. It does NOT drop words or do fuzzy matching, because a CCJ attributed to
    the wrong company is a defamatory error — the R-F3217/R-F3222 name-coincidence class,
    which is exactly what this engine exists to avoid.
    """
    s = _PUNCT.sub(" ", str(name or "").lower())
    s = _WS.sub(" ", s).strip()
    parts = s.split()
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _mode() -> str:
    if (os.getenv("REGISTRY_TRUST_DATA_PATH") or "").strip():
        return "dataset"
    if (os.getenv("REGISTRY_TRUST_API_URL") or "").strip() and \
       (os.getenv("REGISTRY_TRUST_API_KEY") or "").strip():
        return "api"
    return ""


def is_configured() -> bool:
    """True when a licensed backend is available. False is a SPEND decision, not a bug."""
    return bool(_mode())


def configuration_hint() -> str:
    """What the operator must do, in their terms. Shown verbatim on the DD form."""
    return (
        "No CCJ backend configured. Registry Trust is the only authoritative source for "
        "England & Wales and has no public API; they supply bulk data under commercial "
        "contract (business@registry-trust.org.uk). Set REGISTRY_TRUST_DATA_PATH to a "
        "licensed extract, or REGISTRY_TRUST_API_URL + REGISTRY_TRUST_API_KEY if a "
        "contracted endpoint is provided. No code change is needed."
    )


def _load_dataset(path: str) -> list[dict]:
    """Load a licensed extract. CSV or JSONL, chosen by extension then by sniffing.

    A malformed dataset must NOT read as an empty register — that would turn a supply
    problem into a clean CCJ line for every subject. Errors are raised to the caller,
    which converts them into an unsearched outcome.
    """
    rows: list[dict] = []
    lower = path.lower()
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        if lower.endswith(".jsonl") or lower.endswith(".ndjson"):
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        elif lower.endswith(".json"):
            data = json.load(fh)
            rows = data if isinstance(data, list) else list(data.get("judgments") or [])
        else:
            rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"CCJ dataset at {path} parsed to ZERO rows")
    return rows


def _rows() -> list[dict]:
    path = (os.getenv("REGISTRY_TRUST_DATA_PATH") or "").strip()
    now = time.time()
    if (_CACHE["rows"] is not None and _CACHE["path"] == path
            and now - float(_CACHE["loaded_at"]) < _CACHE_TTL_S):
        return _CACHE["rows"]
    rows = _load_dataset(path)
    _CACHE.update({"rows": rows, "loaded_at": now, "path": path, "error": ""})
    logger.info("[R-F3442] loaded %d CCJ records from %s", len(rows), path)
    return rows


def _get(row: dict, *names: str) -> str:
    """Read the first present field, tolerating supplier column-naming differences."""
    for n in names:
        for key in (n, n.lower(), n.upper(), n.replace("_", " "), n.replace("_", "")):
            if key in row and row[key] not in (None, ""):
                return str(row[key]).strip()
    return ""


def _match(row: dict, key: str, postcode: str) -> bool:
    if normalise_name(_get(row, "defendant_name", "name", "party", "defendant")) != key:
        return False
    if not postcode:
        return True
    # A postcode was supplied, so use it to DISAMBIGUATE, never to exclude a record that
    # simply does not carry one — dropping those would under-report judgments.
    rp = _get(row, "postcode", "defendant_postcode", "post_code").replace(" ", "").upper()
    return (not rp) or rp == postcode.replace(" ", "").upper()


async def search_judgments(name: str, *, postcode: str = "",
                           jurisdiction: str = "GB") -> dict:
    """Search the CCJ register for ``name``.

    Returns a dict that always states whether the register was actually consulted:

        {"searched": bool, "backend": str, "judgments": [...], "count": int,
         "reason": str, "as_of": str, "source": "Registry Trust ..."}

    ``searched=False`` NEVER means "clean". The caller must render it as a data gap; a
    CCJ absence only counts as a finding when the register was genuinely read.
    """
    mode = _mode()
    if not mode:
        return {"searched": False, "backend": "", "judgments": [], "count": 0,
                "reason": "not_configured", "detail": configuration_hint(),
                "source": "Registry Trust (Register of Judgments, Orders and Fines)"}

    key = normalise_name(name)
    if not key:
        return {"searched": False, "backend": mode, "judgments": [], "count": 0,
                "reason": "empty_name",
                "source": "Registry Trust (Register of Judgments, Orders and Fines)"}

    try:
        if mode == "dataset":
            import asyncio
            rows = await asyncio.to_thread(_rows)      # file I/O off the event loop
            hits = [r for r in rows if _match(r, key, postcode)]
            judgments = [{
                "case_number": _get(r, "case_number", "case_no", "judgment_number"),
                "court": _get(r, "court", "court_name"),
                "amount": _get(r, "amount", "judgment_amount", "value"),
                "currency": _get(r, "currency") or "GBP",
                "judgment_date": _get(r, "judgment_date", "date", "date_of_judgment"),
                "status": _get(r, "status", "judgment_status") or "registered",
                "satisfied": _get(r, "satisfied", "satisfaction_status"),
                "defendant_name": _get(r, "defendant_name", "name", "party", "defendant"),
                "postcode": _get(r, "postcode", "defendant_postcode", "post_code"),
            } for r in hits]
            return {
                "searched": True, "backend": "dataset", "judgments": judgments,
                "count": len(judgments), "reason": "",
                "as_of": (os.getenv("REGISTRY_TRUST_DATA_AS_OF") or "").strip(),
                "source": "Registry Trust (Register of Judgments, Orders and Fines)",
            }

        # api backend
        import httpx
        url = (os.getenv("REGISTRY_TRUST_API_URL") or "").strip().rstrip("/")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{url}/judgments",
                params={"name": name, "postcode": postcode, "jurisdiction": jurisdiction},
                headers={"Authorization": f"Bearer {os.getenv('REGISTRY_TRUST_API_KEY','')}",
                         "Accept": "application/json"},
            )
        if resp.status_code != 200:
            return {"searched": False, "backend": "api", "judgments": [], "count": 0,
                    "reason": f"http_{resp.status_code}",
                    "source": "Registry Trust (Register of Judgments, Orders and Fines)"}
        payload = resp.json() or {}
        judgments = list(payload.get("judgments") or payload.get("results") or [])
        return {"searched": True, "backend": "api", "judgments": judgments,
                "count": len(judgments), "reason": "",
                "as_of": str(payload.get("as_of") or ""),
                "source": "Registry Trust (Register of Judgments, Orders and Fines)"}

    except Exception as e:
        # A backend that broke is UNSEARCHED, never empty. This is the whole never-
        # false-clean contract for a register whose absence is itself a finding.
        logger.warning("[R-F3442] CCJ search failed (%s): %s", mode, e)
        return {"searched": False, "backend": mode, "judgments": [], "count": 0,
                "reason": f"error:{type(e).__name__}", "detail": str(e)[:200],
                "source": "Registry Trust (Register of Judgments, Orders and Fines)"}
