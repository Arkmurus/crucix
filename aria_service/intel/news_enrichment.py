"""R-F3499 — selective deep enrichment for archived news.

WHY SELECTIVE
─────────────
Normal RSS ingestion keeps at most a 500-character feed description
(news_monitor.py:569,607) and never fetches the article body; the intel ledger
then keeps 500 chars and the brain absorbs 200 (intel_ledger.py:608). That is
enough for headline monitoring, coarse classification and fast alerts. It is not
enough to know WHO made a claim, to separate reported fact from quoted
allegation, to see the caveats, or to find the supporting numbers — which are
precisely the distinctions this product sells.

Deep-fetching everything is wrong in the other direction: cost and latency would
scale with feed volume rather than with value (§17), and permanently storing full
bodies for every publisher creates real copyright and personal-data retention
exposure. CLAUDE.md §18 already treats source licensing as a first-class
constraint — Find Case Law is free and keyless yet deliberately unused pending a
licence reading, and "costing nothing is not the same as being permitted".

So: archive EVERY observation (R-F3485), and read the body only where it can
change the answer.

THE HONESTY PROPERTY
────────────────────
The point of recording ``extraction_status`` is not bookkeeping. A 500-char feed
summary and a fully read article are different grades of evidence, and a shallow
headline must never be presentable as though the article had been read. So the
status travels with the record, a FAILED fetch is recorded as failed rather than
silently left looking un-attempted, and ``cap_confidence_for_extraction`` refuses
to let feed-only evidence carry HIGH confidence. Unknown statuses are treated as
shallow — never assume a record was read because the label is unfamiliar.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.intel.news_enrichment")

# Tiers whose reporting is authoritative enough to be worth reading in full.
_ENRICH_TIERS = frozenset({"tier_1a", "tier_1b", "1a", "1b"})

# Relevance at or above this is worth the fetch regardless of tier.
_ENRICH_RELEVANCE = float(os.getenv("ARIA_NEWS_ENRICH_RELEVANCE", "0.75"))

# Bounded excerpt retained in the archive. The full body is NOT inlined by
# default; body_ref points at licence-governed storage when a source permits it.
_ENRICH_EXCERPT_MAX = int(os.getenv("ARIA_NEWS_ENRICH_EXCERPT_MAX", "6000"))

# Minimum body length that counts as a real read. Below this the fetch returned
# a nav shell, a consent wall or a stub, and calling that "enriched" would be a
# lie about the evidence.
#
# Deliberately LOW (200, not 400+). The floor exists to exclude consent walls and
# navigation shells, which are typically under ~200 chars of extracted text — not
# to impose a minimum article length. A genuine wire brief can be ~300 chars, and
# marking one `enrichment_failed` would assert "could not read it" about an
# article that WAS read. Both directions are dishonest; this errs toward
# believing a short read over inventing a failure, because the confidence cap
# already prevents a thin record from carrying HIGH confidence anyway.
_MIN_BODY_CHARS = int(os.getenv("ARIA_NEWS_ENRICH_MIN_CHARS", "200"))

STATUS_FEED_ONLY = "feed_only"
STATUS_ENRICHED = "enriched"
STATUS_FAILED = "enrichment_failed"

# Only this status means "the article was actually read".
_READ_STATUSES = frozenset({STATUS_ENRICHED})


def should_enrich(
    article: dict[str, Any],
    *,
    watched_entities: Optional[set[str]] = None,
    budget_remaining: int = 1,
) -> tuple[bool, str]:
    """Deterministic, explainable enrichment decision.

    Returns ``(should, reason)``. The reason is mandatory in both directions: a
    silent refusal is unauditable, and an unexplained fetch is unbudgetable.
    """
    if budget_remaining <= 0:
        return False, "enrichment budget exhausted for this cycle"

    tier = str(article.get("tier") or article.get("source_tier") or "").strip().lower()
    if tier in _ENRICH_TIERS:
        return True, f"authoritative source tier ({tier})"

    haystack = " ".join(str(article.get(k) or "") for k in
                        ("title", "summary", "source")).casefold()
    for ent in (watched_entities or set()):
        e = str(ent or "").strip().casefold()
        if e and e in haystack:
            return True, f"watched entity present ({e})"

    try:
        score = float(article.get("relevance_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= _ENRICH_RELEVANCE:
        return True, f"high topical relevance ({score:.2f})"

    return False, (
        f"tier={tier or 'unknown'} relevance={score:.2f} — below the enrichment "
        f"threshold; the feed summary remains the evidence"
    )


def cap_confidence_for_extraction(confidence: str, extraction_status: str) -> str:
    """Refuse HIGH confidence for evidence that was never actually read.

    A 500-char feed description cannot support the same claim strength as a read
    article. Anything that is not explicitly ``enriched`` — including an unknown
    or empty status — is treated as shallow, because assuming a record was read
    on the strength of an unfamiliar label is exactly the failure this guards.
    """
    conf = str(confidence or "").strip().upper()
    if str(extraction_status or "").strip().lower() in _READ_STATUSES:
        return conf
    # Downgrade WITHIN the caller's own vocabulary. The first version returned
    # "ASSESSED", which belongs to brain_hook's enum, not the news signal's
    # {LOW, MEDIUM, HIGH} — it would have injected an unknown value into a field
    # other code compares against that set. A cap must lower confidence, not
    # change what kind of thing the field is.
    return "MEDIUM" if conf == "HIGH" else conf


async def _fetch_body(url: str) -> str:
    """Fetch readable body text for one URL. Isolated so it can be faked in tests.

    R-F3676 — this used to read:

        html = await _ws.fetch_url_text(url) if hasattr(_ws, "fetch_url_text") else ""
        if not html:
            return ""

    ``web_search.fetch_url_text`` DOES NOT EXIST, and never did. The ``hasattr``
    guard turned that into a silent ``""``, so this function returned an empty
    body on every single call and the whole R-F3499/R-F3509 selective deep read
    — engine, budget, wiring, tests — has never once read an article. The failure
    was invisible because it was recorded as a *plausible* outcome: every attempt
    landed in ``enrichment_failed`` with "body too short to be a real read", which
    reads like a consent wall or a nav shell rather than a missing function.

    Measured live 2026-08-04, and it is the tell: 343 enrichment failures, EVERY
    ONE of them "(0 chars)" — never 40, never 200, which is what a real mix of
    paywalls and stubs looks like. Archive-wide: ``feed_only`` 1,472,
    ``enrichment_failed`` 343, ``enriched`` ZERO. Every article ARIA holds is
    title + RSS blurb.

    This is the §3b defect class exactly ("before writing ANY call to a function,
    verify it exists"), and the ``hasattr`` is what made it survive: a plain call
    would have raised ImportError on the first poll.

    So there is NO capability guard here now. ``researcher._fetch_article_text``
    is the established body fetcher — ``deep_researcher`` calls it in seven
    places — and it already does fetch AND structured extraction (SSRF-guarded
    via ``safe_get``, with paywall/archive, Lightpanda and Playwright fallbacks,
    and the CPU-bound regex walk on a worker thread per R-F719). Calling
    ``extract_structured_html_async`` on its result as well would double-extract
    text that is already text. If the import ever breaks, it must raise —
    ``enrich_archived_article`` catches it, records ``enrichment_failed`` with
    the real reason and wires a gap, which is what should have happened here.
    """
    from .researcher import _fetch_article_text
    return str(await _fetch_article_text(url) or "")


async def enrich_archived_article(article_id: str) -> dict[str, Any]:
    """Read the body for one archived article and record the outcome honestly.

    Never raises. Always leaves the record with a truthful ``extraction_status``:
    a failure is written as ``enrichment_failed`` rather than left looking
    un-attempted, so a later reader can tell "not tried" from "tried and could
    not read it" — and neither can be mistaken for "read".
    """
    from . import news_archive as _na

    out = {"article_id": article_id, "enriched": False, "reason": "", "chars": 0}
    rec = await _na.get_article(article_id)
    if not rec:
        out["reason"] = "article not in archive"
        return out

    url = rec.get("canonical_url") or ""
    try:
        body = await _fetch_body(url)
    except Exception as exc:
        await _na.set_extraction_status(article_id, STATUS_FAILED, detail=str(exc)[:300])
        out["reason"] = f"fetch failed: {exc}"
        wire_failure(
            module="news_enrichment",
            detail=f"deep fetch failed for {url[:120]}: {exc}"[:400],
            gap_type="source_failure",
            source="news_enrichment:enrich_archived_article",
        )
        return out

    body = (body or "").strip()
    if len(body) < _MIN_BODY_CHARS:
        # A nav shell / consent wall / stub. Recording this as "enriched" would
        # assert the article was read when it was not.
        await _na.set_extraction_status(
            article_id, STATUS_FAILED,
            detail=f"body too short to be a real read ({len(body)} chars)")
        out["reason"] = f"body too short ({len(body)} chars)"
        return out

    await _na.set_extraction_status(
        article_id, STATUS_ENRICHED, excerpt=body[:_ENRICH_EXCERPT_MAX])
    out.update({"enriched": True, "chars": len(body), "reason": "body read"})
    try:
        wire_success(
            module="news_enrichment",
            summary=f"deep-read {len(body)} chars for archived article {article_id}",
            source_id=f"news_enrichment:{article_id}",
        )
    except Exception:
        pass
    return out
