"""
ARIA Cited-Source Verifier — deterministic hallucination detection by
checking that every URL ARIA cites in an answer was actually fetched
during the same request.

Why this exists
═══════════════
LLMs hallucinate citations. They invent plausible-looking URLs from
training data, sometimes with the right domain but a fake path. For a
defence-procurement intelligence agent, a fabricated source link is
worse than no link — it gives the team false confidence that the claim
is grounded.

This module catches that deterministically. No LLM-as-judge, no
expensive eval — pure regex extraction + set membership. Runs on every
chat call automatically and records a per-request verification record
for trend tracking.

How it works
════════════
    1. The chat endpoint runs a tool (crawl_website / investigate /
       read_article / web search) which produces a tool_context string
       containing the URLs that were ACTUALLY FETCHED in this request.
    2. The LLM generates a response that may cite URLs.
    3. After aria_chat returns, verify_response() extracts URLs from
       both strings and computes:
         - cited_grounded: URLs in response AND in tool_context
         - cited_unverified: URLs in response NOT in tool_context
         - grounded_rate: cited_grounded / total_cited
    4. The verification record is persisted to Redis with a 30-day TTL
       and indexed for /verify queries.
    5. If grounded_rate is below a configurable floor AND there are at
       least 2 unverified citations, fire diagnose_failure so the
       self-improve loop sees the hallucination signal the same way it
       sees runtime errors.

Important caveats
═════════════════
- "Unverified" doesn't always mean "hallucinated". A URL the LLM cites
  from its training data (e.g. wikipedia.org/wiki/X) might be real but
  wasn't fetched in this request. We report this as "unverified", not
  "hallucinated", and the rate as "grounded_rate", not "hallucination
  rate". The team interprets the signal.
- We only verify when a tool actually ran. Pure-conversational replies
  with no tool_context have nothing to ground against and are skipped.
- Domain-only citations (just "reuters.com" with no path) are matched
  loosely — the LLM often shortens URLs in prose.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from . import redis_store as rs

logger = logging.getLogger("aria.source_verifier")

VERIFICATIONS_KEY = "crucix:aria:verifier:index"
VERIFICATION_KEY_PREFIX = "crucix:aria:verifier:record:"
VERIFICATION_TTL = 30 * 86400  # 30 days

# Below this grounded_rate AND with ≥2 unverified citations, we treat
# the response as suspicious and fire the self-improve diagnostic.
SUSPICIOUS_RATE_THRESHOLD = 0.5

# URL extraction regex — captures http(s) URLs with conservative tail
# trimming so trailing punctuation in prose ("...see https://x.com/y.")
# doesn't get glued to the URL.
URL_RE = re.compile(
    r"https?://[^\s<>\"'\)\]\}]+",
    re.IGNORECASE,
)
# Trailing punctuation that should be stripped from extracted URLs —
# Markdown brackets and prose punctuation gets caught by the main regex.
URL_TRAILING_TRIM = ".,;:!?\"')]}"

# R-F894 — bare-domain citation inside a `[from …]` marker, e.g.
#   [from whitehouse.gov/administration/donald-j-trump]
#   [from govtrack.us: "Trump is President…"]
#   [from factually.co]
# Captures (domain, optional-path). Requires a real dotted TLD so prose refs
# like "[from Britannica biography]" or "[from snippet #N]" do NOT match. The
# path stops at the first `]`, whitespace, or `:` so the quote/snippet tail is
# excluded.
_BARE_DOMAIN_CITATION_RE = re.compile(
    r"\[from\s+"
    r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,})"  # domain.tld
    r"(/[^\]\s:]*)?",                                           # optional path
    re.IGNORECASE,
)

# Pre-Phase-3 fix 2026-04-09: clause 15 of the constitution tells the LLM
# to cite tool-derived facts using one of these inline marker formats:
#   - [from <url>]                       (URL — picked up by URL_RE)
#   - [from snippet #N] / [snippet #N]   (deep_research snippet ref)
#   - [from EXTRACT N] / [EXTRACT N]     (deep_research verbatim extract ref)
#   - [from ATTACHED DOCUMENT: <name>]   (uploaded document ref)
#   - [from RAG] / [RAG — CONFIRMED]     (RAG-store retrieval ref)
# All of these markers ONLY exist when a tool actually ran and produced
# output, so they count as grounded citations even though they don't
# contain a URL. Without this fix the verifier returned `no_citations`
# on tool-using turns where the LLM correctly cited every fact via the
# new marker formats — under-counting the grounded rate dramatically.
TOOL_REF_RE = re.compile(
    r"\[(?:from\s+)?"
    r"(?:snippet\s*#?\d+"
    r"|EXTRACT\s+\d+"
    r"|ATTACHED\s+DOCUMENT\s*:\s*[^\]]+"
    r"|RAG(?:\s*[—–-]\s*[A-Z]+)?"
    r")\]",
    re.IGNORECASE,
)

# Additional internal-citation patterns produced by the constitutional
# Clause 15 "inline citation" discipline. These are strong grounding
# signals because they name an internal RAG source the LLM cannot
# fabricate without retrieval:
#
#   [CONFIRMED — RAG: intlaw:sanctions_law]
#   [CONFIRMED — RAG: global_export_control:national_regimes]
#   [CONFIRMED — RAG: clause_library:brokering_commission, 2026-04-11]
#   [CONFIRMED — Knowledge Base]
#   [CONFIRMED — Knowledge Base: EUC_BEFORE_LICENCE]
#   [CONFIRMED — mem0 2026-04-11]
#   [PROBABLE — RAG: …]
#   [ASSESSED — static table]
#
# Without this pattern, smoke-test responses with 8 inline RAG
# citations were still classified `no_citations` by the verifier
# because the URL extractor only looks for http(s):// URLs. That in
# turn caused the confidence footer to demote [CONFIRMED] to
# [ASSESSED] despite the model citing correctly.
INTERNAL_REF_RE = re.compile(
    r"\[(?:CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)"
    r"\s*[—–-]\s*"
    r"(?:RAG\s*:|Knowledge\s+Base|mem0|static\s+table|clause_library|intlaw)"
    r"[^\]]*\]",
    re.IGNORECASE,
)

# Orchestrator-run citations produced by dd_orchestrator. Every DD
# report the LLM cites from carries [from dd_orchestrate:{run_id}]
# inline markers. These are mechanically-generated run IDs the LLM
# cannot fabricate without actually having received the report, so
# they count as grounded citations.
DD_REPORT_REF_RE = re.compile(
    r"\[from\s+dd_orchestrate\s*:\s*dd_[0-9a-f]+\]",
    re.IGNORECASE,
)

# Clause 19 search-doctrine markers — the synthesis layer uses these to
# distinguish memory recall from web-fetched facts, flag contradictions,
# and surface single-source / seeding warnings. The verifier counts
# them as grounding signals because they encode provenance.
DOCTRINE_MARKER_RE = re.compile(
    r"\[(?:"
    r"MEMORY"              # LLM training-data or mem0 recall
    r"|WEB"                # tool-fetched via search_doctrine.search
    r"|CONFLICT\s*:\s*[^\]]+"  # explicit contradiction surfacing
    r"|UNVERIFIED_SINGLE_SOURCE"
    r"|SUSPECTED_SEEDING"
    r"|PRIMARY_FOLLOWED"
    r"|INSUFFICIENT_PUBLIC_INTEL"
    r")\]",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    """Pull URLs out of a string, dedupe, and normalise.

    Returns the URLs in first-seen order so the caller can preserve
    citation order if they care.
    """
    if not text:
        return []
    raw = list(URL_RE.findall(text))
    # R-F894 — bare-domain citations. The LLM frequently cites "[from
    # whitehouse.gov/administration/...]" or "[from govtrack.us: \"...\"]"
    # WITHOUT an http(s):// scheme, so URL_RE missed them entirely → the
    # verifier returned no_citations on well-sourced answers and the
    # officeholder guard FALSELY demoted them to UNCERTAIN (live 2026-05-26:
    # "who is the US president" cited 12 gov/news domains yet showed 0 grounded /
    # NO_CITATIONS). Capture the bare domain (+ optional path) inside a [from …]
    # marker and treat it as an https:// citation so it grounds against the
    # tool_context by domain like any other URL. Requires a real dotted TLD, so
    # "[from Britannica biography]" / "[from snippet #N]" are NOT matched.
    for _dom, _path in _BARE_DOMAIN_CITATION_RE.findall(text):
        raw.append("https://" + _dom + (_path or ""))
    seen: set[str] = set()
    out: list[str] = []
    for u in raw:
        # Strip trailing prose punctuation
        while u and u[-1] in URL_TRAILING_TRIM:
            u = u[:-1]
        if not u or len(u) < 10:
            continue
        # Normalise: lowercase scheme+host, drop trailing slash
        try:
            p = urlparse(u)
            host = (p.netloc or "").lower()
            path = (p.path or "").rstrip("/")
            normalised = f"{p.scheme.lower()}://{host}{path}"
            if p.query:
                normalised += f"?{p.query}"
            if normalised in seen:
                continue
            seen.add(normalised)
            out.append(normalised)
        except Exception:
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def _domain_of(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        # Strip leading www. so reuters.com == www.reuters.com for matching
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _looks_suspicious(url: str) -> bool:
    """Heuristic flag for URLs that look fabricated even before we check
    the tool context. Catches the obvious garbage: random hex paths,
    made-up TLDs, or paths longer than the entire rest of the URL.
    Used as an additional severity signal — not a primary verdict.
    """
    if not url:
        return True
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        path = p.path or ""
        # No host or extremely short host
        if not host or len(host) < 4:
            return True
        # Hex-soup paths (>20 chars of pure hex are usually fake IDs)
        if re.fullmatch(r"/[0-9a-f]{20,}", path):
            return True
        # Very long random-looking last segment
        if path:
            last = path.rstrip("/").split("/")[-1]
            if len(last) > 60 and re.fullmatch(r"[0-9a-zA-Z\-_]+", last):
                # Could be a real article slug, so just a soft signal
                return False
    except Exception:
        return True
    return False


def count_tool_refs(text: str) -> int:
    """Count clause-15 inline marker citations in a response.

    Two pattern families are recognised:

    1. TOOL_REF_RE — explicit tool citations the tool-execution layer
       wrote into the context ([from snippet #N], [EXTRACT N], [from
       ATTACHED DOCUMENT: ...], [from RAG]). These exist only when a
       tool produced output.

    2. INTERNAL_REF_RE — constitutional Clause 15 inline citations of
       internal RAG / Knowledge Base / mem0 / static-table sources
       (e.g. [CONFIRMED — RAG: global_export_control:national_regimes]).
       These exist only when the LLM has actually retrieved from the
       named source. The LLM cannot fabricate the internal source
       names (they're mechanically-generated module keys).

    Both count as grounding. Used by verify_response() as a fallback
    when no URLs are cited but the markers are.
    """
    if not text:
        return 0
    return (
        len(TOOL_REF_RE.findall(text))
        + len(INTERNAL_REF_RE.findall(text))
        + len(DD_REPORT_REF_RE.findall(text))
        + len(DOCTRINE_MARKER_RE.findall(text))
    )


def verify_response(response_text: str, tool_context: str) -> dict:
    """Compute the grounding verdict for one chat response.

    Returns a dict with:
      - cited_urls            : list of URLs the LLM cited in its response
      - fetched_urls          : list of URLs present in the tool context
      - grounded              : URLs in response AND in tool context (exact OR domain match)
      - unverified            : URLs in response NOT matched in tool context
      - grounded_rate         : grounded / cited (None if no citations)
      - suspicious_count      : number of unverified URLs that also fail
                                the looks-suspicious heuristic
      - tool_refs             : count of clause-15 inline marker citations
                                ([from snippet #N], [EXTRACT N], etc.)
      - verdict               : "grounded" | "partial" | "ungrounded" | "no_citations" | "no_tool"
    """
    cited = extract_urls(response_text or "")
    fetched = extract_urls(tool_context or "")
    tool_refs = count_tool_refs(response_text or "")

    # An `[ATTACHED DOCUMENT ... END ATTACHED DOCUMENT]` block counts as a
    # first-class grounding source. Contract reviews, document audits, and
    # OCR'd screenshots all cite "the document" / "Clause 5" / "the BVI
    # law clause" rather than a URL — those assertions ARE grounded in
    # the user-provided text even though there is no URL to extract. The
    # metacognitive honesty check was flagging legitimate contract
    # reviews as dishonest because the verifier didn't recognise this
    # surface. Presence of the block promotes the message out of
    # "no_tool" / "no_citations" into "grounded" on the merits of the
    # attached content itself.
    #
    # Raw text (full message OR tool_context) is checked because the
    # WhatsApp listener injects the block into the user message, not
    # into the tool_context slot.
    has_attached_doc = (
        "[ATTACHED DOCUMENT" in (tool_context or "")
        and "[END ATTACHED DOCUMENT]" in (tool_context or "")
    )

    if not tool_context and not has_attached_doc:
        return {
            "cited_urls": cited,
            "fetched_urls": [],
            "grounded": [],
            "unverified": cited,  # No tool ran — nothing to verify against
            "grounded_rate": None,
            "suspicious_count": sum(1 for u in cited if _looks_suspicious(u)),
            "tool_refs": tool_refs,
            "verdict": "no_tool",
        }
    if not cited:
        # Pre-Phase-3 fix 2026-04-09: previously this branch always returned
        # `no_citations` on tool-using turns where the LLM cited only via
        # the new clause-15 markers ([from snippet #N], [EXTRACT N], etc).
        # Now: if the response contains tool-ref markers (which only exist
        # when a tool produced output), treat as grounded — the LLM cannot
        # fabricate these markers without a tool block in context.
        if tool_refs > 0:
            return {
                "cited_urls": [],
                "fetched_urls": fetched,
                "grounded": [],
                "unverified": [],
                "grounded_rate": 1.0,
                "suspicious_count": 0,
                "tool_refs": tool_refs,
                "verdict": "grounded",
            }
        # Document-first grounding: an attached document in context +
        # a response that quotes / references specific clauses counts
        # as grounded even without URLs. We treat the presence of the
        # block as sufficient evidence that the response was built from
        # verifiable text the user supplied.
        if has_attached_doc:
            return {
                "cited_urls": [],
                "fetched_urls": [],
                "grounded": ["[ATTACHED DOCUMENT]"],
                "unverified": [],
                "grounded_rate": 1.0,
                "suspicious_count": 0,
                "tool_refs": tool_refs,
                "verdict": "grounded",
                "source": "attached_document",
            }
        return {
            "cited_urls": [],
            "fetched_urls": fetched,
            "grounded": [],
            "unverified": [],
            "grounded_rate": None,
            "suspicious_count": 0,
            "tool_refs": 0,
            "verdict": "no_citations",
        }

    fetched_set = set(fetched)
    fetched_domains = {_domain_of(u) for u in fetched if _domain_of(u)}

    grounded: list[str] = []
    unverified: list[str] = []
    for url in cited:
        # Exact match wins. Domain match is a softer fallback for the
        # case where the LLM cites a real article we did fetch but
        # paraphrases/abbreviates the URL.
        if url in fetched_set:
            grounded.append(url)
        elif _domain_of(url) in fetched_domains:
            grounded.append(url)  # domain-match counts as grounded
        else:
            unverified.append(url)

    rate = len(grounded) / len(cited) if cited else None
    if rate is None:
        verdict = "no_citations"
    elif rate >= 0.9:
        verdict = "grounded"
    elif rate >= 0.5:
        verdict = "partial"
    else:
        verdict = "ungrounded"

    return {
        "cited_urls": cited,
        "fetched_urls": fetched,
        "grounded": grounded,
        "unverified": unverified,
        "grounded_rate": round(rate, 3) if rate is not None else None,
        "suspicious_count": sum(1 for u in unverified if _looks_suspicious(u)),
        "tool_refs": tool_refs,
        "verdict": verdict,
    }


# ── Persistence ────────────────────────────────────────────────────────────

async def record_verification(
    verification: dict,
    *,
    request_id: str = "",
    session_id: str = "",
    user: str = "",
    question_preview: str = "",
    response_preview: str = "",
    tool_used: str = "",
) -> dict:
    """Persist a verification record + update the index. Returns the
    saved record so the caller can attach the id to its response."""
    vid = f"vfy_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    record = {
        "id": vid,
        "ts": time.time(),
        "request_id": request_id or "",
        "session_id": session_id or "",
        "user": (user or "")[:120],
        "question_preview": (question_preview or "")[:300],
        "response_preview": (response_preview or "")[:600],
        "tool_used": tool_used or "",
        **verification,
    }
    try:
        await rs.set_json(f"{VERIFICATION_KEY_PREFIX}{vid}", record, ex=VERIFICATION_TTL)
        index = await rs.get_json(VERIFICATIONS_KEY) or []
        index.insert(0, {
            "id": vid,
            "ts": record["ts"],
            "verdict": verification.get("verdict"),
            "grounded_rate": verification.get("grounded_rate"),
            "cited_count": len(verification.get("cited_urls", [])),
            "unverified_count": len(verification.get("unverified", [])),
            "tool_used": tool_used or "",
            "question_preview": record["question_preview"][:140],
        })
        index = index[:500]
        await rs.set_json(VERIFICATIONS_KEY, index, ex=VERIFICATION_TTL)
    except Exception as e:
        logger.warning("record_verification persist failed: %s", e)

    # Fire diagnose_failure for clearly ungrounded responses with multiple
    # bad citations — same path used by runtime errors and user 👎.
    grounded_rate = verification.get("grounded_rate")
    unverified = verification.get("unverified") or []
    if (
        grounded_rate is not None
        and grounded_rate < SUSPICIOUS_RATE_THRESHOLD
        and len(unverified) >= 2
    ):
        try:
            from . import self_improve
            err_summary = (
                f"ARIA cited {len(unverified)} URL(s) not present in the tool context "
                f"(grounded_rate={grounded_rate:.2f}). "
                f"Question: {record['question_preview'][:200]}. "
                f"Suspect citations: {unverified[:5]}"
            )
            import asyncio as _aio
            _aio.create_task(self_improve.diagnose_failure(
                "ungrounded_citations",
                err_summary,
                {
                    "verification_id": vid,
                    "grounded_rate": grounded_rate,
                    "unverified_count": len(unverified),
                    "tool_used": tool_used or "",
                },
            ))
        except Exception as e:
            logger.debug("diagnose_failure dispatch failed: %s", e)

    return record


async def get_verification(vid: str) -> dict | None:
    try:
        return await rs.get_json(f"{VERIFICATION_KEY_PREFIX}{vid}")
    except Exception:
        return None


async def list_verifications(
    limit: int = 30,
    verdict_filter: str | None = None,
) -> list[dict]:
    try:
        index = await rs.get_json(VERIFICATIONS_KEY) or []
        if verdict_filter:
            index = [e for e in index if e.get("verdict") == verdict_filter]
        return index[: max(1, min(limit, 500))]
    except Exception:
        return []


async def get_verification_stats() -> dict:
    """Aggregate counts + grounded rates.

    Returns BOTH a true 24h rolling rate and the lifetime (last-500-entry)
    rate. Previously `rolling_grounded_rate` and its alias
    `avg_grounded_rate` were averaged across all 500 entries -- with
    typical traffic that spans days to weeks, so the metric was a stale
    long-term average mislabeled as "rolling". operating_modes.py reads
    avg_grounded_rate to decide NORMAL vs AUDIT transitions, so the wrong
    semantics meant the mode-shift never actually responded to current
    24h conditions. Fix: filter the average to entries within the 24h
    cutoff (matching recent_24h), keep the lifetime number under a new
    field for any caller that genuinely wants it.
    """
    try:
        index = await rs.get_json(VERIFICATIONS_KEY) or []
        by_verdict: dict[str, int] = {}
        recent_24h = 0
        cutoff = time.time() - 86400
        rate_sum_24h = 0.0
        rate_n_24h = 0
        rate_sum_all = 0.0
        rate_n_all = 0
        for e in index:
            v = e.get("verdict", "unknown")
            by_verdict[v] = by_verdict.get(v, 0) + 1
            ts = e.get("ts", 0)
            r = e.get("grounded_rate")
            if r is not None:
                rate_sum_all += r
                rate_n_all += 1
            if ts >= cutoff:
                recent_24h += 1
                if r is not None:
                    rate_sum_24h += r
                    rate_n_24h += 1
        rolling_rate_24h = (
            round(rate_sum_24h / rate_n_24h, 3) if rate_n_24h > 0 else None
        )
        lifetime_rate = (
            round(rate_sum_all / rate_n_all, 3) if rate_n_all > 0 else None
        )

        # R-F590 (2026-05-16) — when the 24h window has zero entries
        # with grounded_rate set (typical when all recent verifications
        # are "no_tool" / "no_citations" — see verdicts above), fall
        # back to lifetime_rate so operating_modes.py + the dashboard
        # have a non-null signal to read instead of treating a quiet
        # 24h window as "unknown ground". The data_source tag tells
        # the consumer which window the number came from so it can
        # still distinguish "fresh 24h evidence" from "lifetime stable
        # baseline" when that matters.
        if rolling_rate_24h is not None:
            effective_rate = rolling_rate_24h
            data_source = "24h_window"
        elif lifetime_rate is not None:
            effective_rate = lifetime_rate
            data_source = "lifetime_fallback"
        else:
            effective_rate = None
            data_source = "no_data"

        return {
            "total": len(index),
            "by_verdict": by_verdict,
            "recent_24h": recent_24h,
            # The "rolling" name and its alias are the operating-mode
            # input -- those consumers want a current-conditions signal.
            # R-F590: now lifetime-fallback-aware instead of nullable.
            "rolling_grounded_rate": effective_rate,
            "avg_grounded_rate": effective_rate,
            "rate_sample_size": rate_n_24h,
            "data_source": data_source,
            # R-F590: 24h breakdown so consumers can see WHY the
            # effective rate may have fallen back to lifetime.
            "grounded_attempts_24h": rate_n_24h,
            "total_attempts_24h": recent_24h,
            # Lifetime average kept under a clearly-named field for any
            # caller that wants long-horizon trend (none today, but
            # leaving the door open without ambiguity).
            "lifetime_grounded_rate": lifetime_rate,
            "lifetime_sample_size": rate_n_all,
        }
    except Exception as e:
        return {"error": str(e)}
