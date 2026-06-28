"""citation_audit — verify cited claims against the actual cited source
content (R-F78, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'The grounded_rate metric measures citation presence
— whether claims have a source attached. It does not measure citation
accuracy — whether the source actually says what ARIA claims it says.
A system with 0.87 grounded_rate could still have 13% of cited claims
that the source does not support, plus an unknown percentage of claims
where the source is cited correctly but ARIA's interpretation of it is
wrong.'

What this module does
─────────────────────
For a response containing inline citations (URLs), this module:

1. Extracts every (claim, cited_url) pair using the existing inline-
   citation pattern recognised by aria_engine's response_verifier.

2. For each cited URL, fetches the page content (with a short timeout
   and conservative size cap), strips HTML to text, and runs a token-
   overlap check between the claim text and the source content.

3. Classifies each claim as:
   - SUPPORTED: ≥60% of distinctive claim tokens appear in source
   - PARTIAL:   30-60% overlap (cited correctly but interpreted loosely)
   - UNSUPPORTED: <30% overlap (citation error or hallucination)
   - UNREACHABLE: source URL didn't return content (404, timeout, paywall)

4. Returns a structured audit report + a citation_grounded_rate that
   measures *accuracy*, not just presence (vs aria_engine.grounded_rate
   which measures presence).

Caveats
───────
- Token overlap is a coarse proxy. A semantically correct paraphrase
  with low overlap will be classified UNSUPPORTED. False-positives on
  the strict side keep the audit conservative.

- Paywall content (Wall Street Journal, FT, Bloomberg) returns the
  paywall page, not the article. We classify these UNREACHABLE
  rather than UNSUPPORTED, so the report's accuracy metric isn't
  poisoned by paywalls.

- This is the audit primitive — it doesn't run continuously. Use:
    POST /api/aria/citations/verify {"response": "...", "max_urls": 10}
  to audit one response on demand.

Public API
──────────
    extract_claim_citation_pairs(response_text) -> list[(claim, url)]
    verify_response(response_text, max_urls=8) -> dict
    summary() -> dict
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger("aria.citation_audit")

# Match a claim sentence followed by an inline URL in any of the styles
# the response_verifier emits:
#   "ARIA claims X. https://example.com/Y"
#   "ARIA claims X (https://example.com/Y)"
#   "ARIA claims X [https://example.com/Y]"
#   "ARIA claims X — see https://example.com/Y"
# The regex captures the sentence preceding the URL up to the previous
# sentence boundary or paragraph break.
_CITATION_RE = re.compile(
    r"([^.!?\n]{20,500}[.!?])\s*[\(\[]?\s*"
    r"(https?://[^\s<>\"')\]}]+)"
    r"\s*[\)\]]?",
    re.MULTILINE,
)

# Stopwords removed from the token-overlap check — they don't carry
# distinctive meaning and skew the overlap toward false-positives.
_STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be
       because been before being below between both but by could did do
       does doing down during each few for from further had has have
       having he her here hers herself him himself his how i if in into
       is it its itself just me more most my myself nor not of off on
       once only or other our ours ourselves out over own same she
       should so some such than that the their theirs them themselves
       then there these they this those through to too under until up
       very was we were what when where which while who whom why will
       with you your yours yourself yourselves""".split()
)
_TOKEN_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9'\-]{2,}\b")

_SUPPORTED_THRESHOLD = 0.60
_PARTIAL_THRESHOLD   = 0.30


def _tokenise(text: str) -> set[str]:
    """Lowercased tokens minus stopwords."""
    if not text:
        return set()
    tokens = _TOKEN_RE.findall(text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) >= 3}


def _strip_html(html: str) -> str:
    """Conservative HTML→text. Drops scripts/styles, decodes entities,
    collapses whitespace. Avoids BeautifulSoup dependency for this
    minimal cut."""
    if not html:
        return ""
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#?\w+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_claim_citation_pairs(response_text: str) -> list[tuple[str, str]]:
    """Extract (claim, cited_url) tuples from a response."""
    if not response_text:
        return []
    pairs: list[tuple[str, str]] = []
    for m in _CITATION_RE.finditer(response_text):
        claim = m.group(1).strip()
        url = m.group(2).strip().rstrip(".,;)]")
        if not claim or not url:
            continue
        pairs.append((claim, url))
    return pairs


async def _fetch_text(url: str, *, timeout: float = 15.0) -> tuple[str, str]:
    """Fetch URL → (status_label, plain_text). status_label is
    'OK' / 'http_NNN' / 'timeout' / 'fetch_error' / 'too_large' /
    'paywall_suspected'."""
    try:
        import httpx
    except ImportError:
        return ("fetch_error", "")
    try:
        from . import url_safety as _us
        async with httpx.AsyncClient(  # no-breaker: citation audit is read-only best-effort
            timeout=timeout,
            follow_redirects=False,  # R-F1825: safe_get revalidates each hop
            headers={"User-Agent": "AriaCitationAudit/1.0 (defence-DD; aria@arkmurus.com)"},
        ) as client:
            resp = await _us.safe_get(client, url)  # R-F1825 (C2-broaden): SSRF guard on cited URL
    except Exception as e:
        kind = "timeout" if "timeout" in str(e).lower() else "fetch_error"
        logger.debug("citation audit fetch %s: %s", url, e)
        return (kind, "")

    if resp.status_code != 200:
        return (f"http_{resp.status_code}", "")

    content = resp.text or ""
    if len(content) > 800_000:  # 800k chars max
        return ("too_large", "")

    text = _strip_html(content)

    # Coarse paywall heuristic — pages where the visible body text is
    # short relative to the HTML and contains 'subscribe' / 'paywall'.
    if (len(text) < 800 and (
            "subscribe" in text.lower()
            or "paywall" in text.lower()
            or "sign in" in text.lower())):
        return ("paywall_suspected", text)

    return ("OK", text)


async def verify_response(
    response_text: str,
    *,
    max_urls: int = 8,
    fetch_timeout: float = 15.0,
) -> dict[str, Any]:
    """Audit one response's citations.

    Returns:
        {
          "ok": bool,
          "total_citations":     int,
          "citations_audited":   int,
          "supported":           int,
          "partial":             int,
          "unsupported":         int,
          "unreachable":         int,
          "citation_grounded_rate": float,   # supported / audited
          "per_citation":        list[dict],
          "narrative":           str,
        }
    """
    pairs = extract_claim_citation_pairs(response_text)
    if not pairs:
        return {
            "ok":                       True,
            "total_citations":          0,
            "citations_audited":        0,
            "supported":                0,
            "partial":                  0,
            "unsupported":              0,
            "unreachable":              0,
            "citation_grounded_rate":   None,
            "per_citation":             [],
            "narrative":                "No citations detected in the response.",
        }

    # Cap to avoid runaway fetch
    pairs_capped = pairs[:max_urls]
    per_citation: list[dict[str, Any]] = []
    counts = {"SUPPORTED": 0, "PARTIAL": 0, "UNSUPPORTED": 0, "UNREACHABLE": 0}

    for claim, url in pairs_capped:
        status, text = await _fetch_text(url, timeout=fetch_timeout)

        if status != "OK":
            counts["UNREACHABLE"] += 1
            per_citation.append({
                "claim":    claim[:200],
                "url":      url,
                "verdict":  "UNREACHABLE",
                "reason":   status,
                "overlap":  None,
            })
            continue

        claim_tokens = _tokenise(claim)
        source_tokens = _tokenise(text)
        if not claim_tokens or not source_tokens:
            counts["UNREACHABLE"] += 1
            per_citation.append({
                "claim":    claim[:200],
                "url":      url,
                "verdict":  "UNREACHABLE",
                "reason":   "no_tokens",
                "overlap":  None,
            })
            continue
        intersect = claim_tokens & source_tokens
        overlap = len(intersect) / len(claim_tokens)
        if overlap >= _SUPPORTED_THRESHOLD:
            verdict = "SUPPORTED"
        elif overlap >= _PARTIAL_THRESHOLD:
            verdict = "PARTIAL"
        else:
            verdict = "UNSUPPORTED"
        counts[verdict] += 1
        per_citation.append({
            "claim":   claim[:200],
            "url":     url,
            "verdict": verdict,
            "overlap": round(overlap, 3),
            "tokens_claim":    len(claim_tokens),
            "tokens_matched":  len(intersect),
        })

    audited = sum(counts.values())
    # citation_grounded_rate is the FRACTION OF AUDITED CITATIONS that
    # are SUPPORTED. UNREACHABLE citations don't dilute the metric in
    # either direction (they're excluded from the denominator).
    audited_with_verdict = counts["SUPPORTED"] + counts["PARTIAL"] + counts["UNSUPPORTED"]
    if audited_with_verdict > 0:
        cgr = counts["SUPPORTED"] / audited_with_verdict
    else:
        cgr = None

    if cgr is None:
        narrative = (
            f"Citation audit: {audited} citation(s); none reachable "
            f"(all UNREACHABLE — paywalls / timeouts / errors)."
        )
    elif cgr >= 0.85:
        narrative = (
            f"Citation audit OK — {counts['SUPPORTED']}/{audited_with_verdict} "
            f"SUPPORTED ({cgr*100:.0f}%); {counts['PARTIAL']} partial, "
            f"{counts['UNSUPPORTED']} unsupported."
        )
    elif cgr >= 0.60:
        narrative = (
            f"Citation audit MIXED — {counts['SUPPORTED']}/{audited_with_verdict} "
            f"SUPPORTED ({cgr*100:.0f}%); {counts['PARTIAL']} partial, "
            f"{counts['UNSUPPORTED']} unsupported. Review weak citations."
        )
    else:
        narrative = (
            f"Citation audit LOW — only {counts['SUPPORTED']}/{audited_with_verdict} "
            f"SUPPORTED ({cgr*100:.0f}%). Investigate {counts['UNSUPPORTED']} "
            f"unsupported claims for hallucination risk."
        )

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="citation_audit",
        summary="Verify Response",
        source_id="citation_audit:R-F996",
    )

    return {
        "ok":                       True,
        "total_citations":          len(pairs),
        "citations_audited":        audited,
        "supported":                counts["SUPPORTED"],
        "partial":                  counts["PARTIAL"],
        "unsupported":              counts["UNSUPPORTED"],
        "unreachable":              counts["UNREACHABLE"],
        "citation_grounded_rate":   round(cgr, 3) if cgr is not None else None,
        "per_citation":             per_citation,
        "narrative":                narrative,
        "thresholds": {
            "supported_min_overlap": _SUPPORTED_THRESHOLD,
            "partial_min_overlap":   _PARTIAL_THRESHOLD,
        },
    }


def summary() -> dict[str, Any]:
    return {
        "module":              "citation_audit",
        "purpose":             "verify cited claims against actual source content (vs grounded_rate which measures presence)",
        "supported_threshold": _SUPPORTED_THRESHOLD,
        "partial_threshold":   _PARTIAL_THRESHOLD,
    }

# R-F2119 §21a — wire failure handler for citation_audit
try:
    wire_failure(module="citation_audit", detail="module shutdown",
                gap_type="engine_failure", source="citation_audit:shutdown")
except Exception:
    pass
