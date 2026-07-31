"""R-F3424 — UK employment tribunal decisions, as a DD source.

WHY IT EARNS ITS PLACE. Fundamental #17 (litigation) was answered by CourtListener (US
federal) and a BAILII RSS proxy (senior UK courts). Neither carries employment tribunals,
which is where a UK services employer — facilities management, security, cleaning,
logistics, exactly ARIA's market — actually appears as a defendant. A company can have a
clean High Court record and dozens of tribunal findings against it.

VERIFIED LIVE 2026-07-29, not read off documentation:
    GET https://www.gov.uk/api/search.json
        ?filter_format=employment_tribunal_decision&q=Mitie&count=3
    -> total 503, results carrying title / link / public_timestamp / description
No API key, no registration.

── THE DISCRIMINATION THIS ADAPTER EXISTS TO MAKE ──────────────────────────────
Decision titles are formatted "<claimant> v <respondent>: <case number>", e.g.

    "Mr R Furey v Mitie Ltd and Mitie Group plc: 2402320/2020"

The RESPONDENT is the employer. A full-text search returns every decision whose text
mentions the name, so a subject can appear:
  * as RESPONDENT   — a claim was brought against them. This is the DD-relevant fact.
  * as CLAIMANT     — they brought a claim. A different fact entirely, and reporting it
                      as "litigation against them" is simply wrong.
  * in the body only — named in someone else's case (a TUPE transferor, a related
                      company, a witness's employer).

Counting all three together is how "503 results for Mitie" becomes a false picture. The
adapter parses the title and says which side the subject is on; the DD drives severity
off the RESPONDENT position only.

Case numbers are returned because they are the corroboration handle: a tribunal case
number is unique and is what turns a name match into a verifiable record.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ._common import (
    OUTCOME_EMPTY,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    empty_result,
    error_result,
    finalise,
    normalise_name,
    stamp_outcome,
)

logger = logging.getLogger("aria.sources.employment_tribunal")

_BASE = "https://www.gov.uk/api/search.json"
_PUBLIC = "https://www.gov.uk/employment-tribunal-decisions"
_SOURCE = "employment_tribunal"
_FORMAT = "employment_tribunal_decision"

#: No `Accept` header. The Gazette (R-F3403) returned HTTP 500 for an explicit
#: `Accept: application/json`; gov.uk does not, but the same minimal-headers discipline
#: costs nothing and removes a class of surprise.
_HEADERS = {"User-Agent": "ARIA-DD/1.0 (+https://imaria.io)"}

_MAX_RESULTS = 20

#: "<claimant> v <respondent>: <case number>". The separator is a lone "v" or "v.",
#: space-delimited, so it cannot match the letter inside a word.
#:
#: The case group allows "and" because a joined decision carries several numbers —
#: "Mitie Ltd: 3313506/2023 and 3314330/2023". Without it the group failed to match, the
#: optional trailer collapsed, and the RESPONDENT field absorbed the numbers
#: ("Mitie Ltd: 3313506/2023 and 3314330/20…"). Side detection still worked, but the
#: corroboration handle — the case number, the one field that turns a name match into a
#: verifiable record — came back empty on exactly the multi-claim decisions that matter
#: most.
_TITLE_RE = re.compile(
    r"^(?P<claimant>.+?)\s+v\.?\s+(?P<respondent>.+?)"
    r"(?::\s*(?P<case>[\d/][\d/\-,\s]*(?:and\s+[\d/][\d/\-,\s]*)*))?$",
    re.IGNORECASE,
)


def parse_title(title: str) -> dict:
    """Split a decision title into claimant / respondent / case number.

    Returns empty strings rather than None so a caller can format without guarding, and
    returns the WHOLE title as `respondent` only when there is no " v " at all — an
    unparsed title must not silently become a claimant-side match.
    """
    t = " ".join(str(title or "").split())
    if not t:
        return {"claimant": "", "respondent": "", "case_number": "", "parsed": False}
    m = _TITLE_RE.match(t)
    if not m:
        return {"claimant": "", "respondent": t, "case_number": "", "parsed": False}
    return {
        "claimant": (m.group("claimant") or "").strip(),
        "respondent": (m.group("respondent") or "").strip(),
        "case_number": (m.group("case") or "").strip(),
        "parsed": True,
    }


def _side_of(subject: str, parsed: dict) -> str:
    """Which side of the 'v' the subject is on: respondent | claimant | neither."""
    subj = normalise_name(subject)
    if not subj:
        return "neither"
    if subj in normalise_name(parsed.get("respondent", "")):
        return "respondent"
    if subj in normalise_name(parsed.get("claimant", "")):
        return "claimant"
    return "neither"


from ..engine_wiring import wired  # R-F3557 (§21a)


@wired(module="employment_tribunal", summary="employment-tribunal decision search completed", gap_type="source_failure")
async def search_decisions(
    name: str,
    *,
    limit: int = _MAX_RESULTS,
    timeout: float = 20.0,
) -> dict:
    """Search published employment tribunal decisions for `name`.

    Every hit carries `side` (respondent / claimant / neither) and the case number, so
    the caller can separate "a claim was brought against this employer" from "this name
    appears somewhere in a decision".
    """
    started = time.time()
    q = (name or "").strip()
    query = {"q": q, "format": _FORMAT}

    if len(q) < 3:
        res = empty_result(_SOURCE, query, citation_url=_PUBLIC)
        return stamp_outcome(res, "skipped",
                             detail="query too short to search a national index",
                             module="sources.employment_tribunal")

    params = {
        "filter_format": _FORMAT,
        "q": q,
        "count": str(max(1, min(50, limit))),
        "fields": "title,link,public_timestamp,description",
        "order": "-public_timestamp",
    }

    payload = None
    fetch_error = ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(_BASE, params=params, headers=_HEADERS)
            if resp.status_code != 200:
                fetch_error = f"gov.uk search returned HTTP {resp.status_code}"
            else:
                payload = resp.json()
    except Exception as exc:
        fetch_error = f"{type(exc).__name__}: {exc}"

    if not isinstance(payload, dict):
        # A fetch failure must never be reported as "no tribunal claims" — that is a
        # false clean on the exact question a services employer is asked.
        res = error_result(_SOURCE, query, fetch_error or "gov.uk search did not answer",
                           citation_url=_PUBLIC, started_at=started)
        return stamp_outcome(res, OUTCOME_TIMEOUT,
                             detail=fetch_error or "no response from gov.uk",
                             module="sources.employment_tribunal")

    res = empty_result(_SOURCE, query, citation_url=_PUBLIC)
    # ── gov.uk OR-MATCHES THE QUERY WORDS. THIS NUMBER IS NOT A RELEVANCE SIGNAL ──
    #
    # MEASURED 2026-07-29: q="Silverbrook Capital Management" returns total=31098 —
    # Al-Khair Foundation, an NHS trust, Bakkavor Foods — because the index matches
    # "Capital" and "Management" independently. Reporting that as "31,098 employment
    # tribunal results" for a small asset manager would be a catastrophic false
    # positive, and `hit_count` is no better: the first page was 20 unrelated
    # decisions.
    #
    # `respondent_count` below is the only honest relevance signal, and on that same
    # query it was ZERO. Consumers must drive off it, never off the totals — which is
    # why the name says `total_index_matches` rather than anything suggesting they are
    # about the subject.
    res["total_index_matches"] = int(payload.get("total") or 0)
    res["total_is_or_matched"] = True
    res["relevance_note"] = (
        "gov.uk OR-matches query words, so total_index_matches and hit_count include "
        "decisions that merely share a word with the name. Only respondent_count "
        "reflects claims brought AGAINST this subject."
    )

    for item in (payload.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        parsed = parse_title(title)
        link = str(item.get("link") or "").strip()
        res["hits"].append({
            "title": title,
            "url": f"https://www.gov.uk{link}" if link.startswith("/") else link,
            "decided": str(item.get("public_timestamp") or "").strip(),
            "claimant": parsed["claimant"],
            "respondent": parsed["respondent"],
            "case_number": parsed["case_number"],
            "title_parsed": parsed["parsed"],
            # The DD-relevant distinction. `respondent` = a claim was brought AGAINST
            # the subject; `claimant` = the subject brought it; `neither` = the name
            # appears in the decision but on no party line.
            "side": _side_of(q, parsed),
        })

    res["respondent_count"] = sum(1 for h in res["hits"] if h["side"] == "respondent")
    res["corroboration_required"] = (
        "Matched on decision text. Confirm the respondent's registered name and the "
        "case number before treating a decision as being about this company."
    )
    finalise(res, started)
    return stamp_outcome(res, OUTCOME_OK if res["hit_count"] else OUTCOME_EMPTY,
                         module="sources.employment_tribunal")
