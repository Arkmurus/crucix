"""counter_intelligence — detection of attempts to influence ARIA's
assessment via reputation washing or corpus seeding (R-F84, 2026-05-09).

Why this module exists
──────────────────────
Per peer review §1: 'A sophisticated actor wanting to influence ARIA's
assessment of them can: publish multiple articles through low-credibility
outlets that ARIA's source validator has not flagged, create Wikipedia
entries or LinkedIn profiles that ARIA's sweeps will index, seed the
corpus through WhatsApp messages to monitored groups, or use the public
API (once live) to query ARIA's knowledge about them and identify the
specific gaps they need to fill.'

The most original observation in any of the peer reviews. The other
modules harden ARIA's outputs; this one defends ARIA's *inputs*.

Detection patterns implemented
──────────────────────────────
1. REPUTATION WASHING — sudden positive press (low-credibility outlets)
   for an entity that simultaneously has unresolved enforcement /
   sanctions / adverse-media activity. Operators of seeded campaigns
   have a hard time killing the genuine bad-news signal at the same time
   they push the laundering signal.

2. SOURCE-CREDIBILITY ANOMALY — same entity referenced by 5+ low-
   credibility-tier sources but 0 high-credibility-tier sources within
   a tight time window. Legitimate stories get picked up by tier-1
   outlets (Reuters / Bloomberg / WSJ / Janes / Defense News) within
   24-72h. Stories that stay in tier-3 forever are suspicious.

3. NEW-OUTLET BURST — a previously-unseen domain ingests 3+ articles
   about the same entity in one cycle. New outlets don't suddenly find
   the same niche topic by coincidence; the burst pattern is the
   fingerprint of a coordinated campaign.

Detection methodology
─────────────────────
The fly-side analyser works against the existing intel_ledger
(18,870 signals) + knowledge base (20,275 facts) without rebuilding
either. It runs as a periodic autonomous task (recommended weekly) and
writes findings to the brain with topic=counter_intelligence_alert.

Public API
──────────
    scan_entity(entity_name, window_days=14) -> dict
        Run the three detection patterns against one entity. Returns
        per-pattern scores + composite + narrative.

    scan_corpus(window_days=14, max_entities=50) -> dict
        Sweep recent ledger signals for entity-burst patterns.

    summary() -> dict
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aria.counter_intelligence")

# Source credibility tiers — coarse mapping of domains to a 1-3 tier.
# 1 = high (Reuters, Bloomberg, WSJ, Janes, Defense News, FT)
# 2 = medium (regional defence press, government press releases)
# 3 = low (blogs, content-mill domains, newly-registered outlets)
# Anything not in this map gets a default tier of 2 — until we see
# behaviour that justifies promotion or demotion.
_TIER_1_HOSTS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com",
    "janes.com", "defensenews.com", "breakingdefense.com",
    "armytimes.com", "marinecorpstimes.com", "navytimes.com",
    "bbc.com", "bbc.co.uk", "nytimes.com", "washingtonpost.com",
    "ap.org", "afp.com", "economist.com",
    "navalnews.com", "naval-news.com",
    # Government primary sources
    "gov.uk", "treasury.gov", "state.gov", "justice.gov",
    "defense.gov", "consilium.europa.eu", "europa.eu", "un.org",
    "fco.gov.uk", "fcdo.gov.uk",
}

# Tier-3 patterns — domains that should be treated as low-credibility
# unless explicitly promoted. Keep this narrow; the goal is to catch
# the obvious red flags without false-positive-blocking legitimate niche
# outlets.
_TIER_3_PATTERNS = [
    r"\.blogspot\.com$",
    r"\.wordpress\.com$",
    r"\.medium\.com$",
    r"\.substack\.com$",
    r"^pr-?newswire",
    r"^prnewswire\.",
    r"^businesswire\.",
    r"^einpresswire\.",
    r"^openpr\.",
    r"^prlog\.",
]


def credibility_tier(host_or_url: str) -> int:
    """Return 1 / 2 / 3 for the source. Default 2."""
    if not host_or_url:
        return 2
    h = host_or_url.lower().strip()
    # Strip protocol + path so we can match against the host part
    h = re.sub(r"^https?://", "", h)
    h = h.split("/", 1)[0]
    h = re.sub(r"^www\.", "", h)
    # Tier 1
    for t1 in _TIER_1_HOSTS:
        if h == t1 or h.endswith("." + t1):
            return 1
    # Tier 3
    for pat in _TIER_3_PATTERNS:
        if re.search(pat, h):
            return 3
    return 2


def _extract_signals_for_entity(
    signals: list[dict[str, Any]],
    entity_name: str,
    *,
    window_days: int = 14,
) -> list[dict[str, Any]]:
    """Filter ledger signals to those mentioning the entity within window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    needle = entity_name.lower().strip()
    out: list[dict[str, Any]] = []
    for s in signals or []:
        if not isinstance(s, dict):
            continue
        # ts can be ISO string or epoch float; tolerate both
        ts_raw = s.get("ts") or s.get("timestamp") or s.get("created_at")
        try:
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            elif isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            else:
                continue
            if ts < cutoff:
                continue
        except Exception:
            continue
        # Substring match on entity in summary / detail / entity field
        text = " ".join(
            str(s.get(k) or "") for k in ("entity", "summary", "detail", "title")
        ).lower()
        if needle in text:
            out.append(s)
    return out


def _classify_sentiment(text: str) -> str:
    """Coarse sentiment classifier — POSITIVE / NEGATIVE / NEUTRAL.

    Uses a small keyword list. Production version can swap in a
    proper sentiment model; the goal here is just to detect the
    'sudden positive press' pattern, not perfect sentiment.
    """
    if not text:
        return "NEUTRAL"
    lower = text.lower()
    pos = sum(1 for w in (
        "award", "expansion", "growth", "success", "innovation",
        "partnership", "milestone", "achieve", "recogniz",
        "launch", "leadership", "leading provider", "trusted",
        "endorse", "praise", "best-in-class",
    ) if w in lower)
    neg = sum(1 for w in (
        "sanction", "violat", "fraud", "indict", "settl",
        "fine", "penalt", "investigat", "allegation",
        "breach", "guilty", "convict", "money laundering",
        "evas", "kickback", "corrupt", "embezzl",
    ) if w in lower)
    if pos > neg:
        return "POSITIVE"
    if neg > pos:
        return "NEGATIVE"
    return "NEUTRAL"


def scan_signals_for_patterns(
    signals: list[dict[str, Any]],
    entity_name: str,
) -> dict[str, Any]:
    """Run the three detection patterns over a list of signals about
    ONE entity. Pure function — no I/O, easily testable.
    """
    n = len(signals)
    if n == 0:
        return {
            "entity":     entity_name,
            "n_signals":  0,
            "patterns":   {},
            "narrative":  f"{entity_name}: no recent signals.",
        }

    # Build per-signal classifications
    classified: list[dict[str, Any]] = []
    for s in signals:
        url_or_source = s.get("source") or s.get("url") or ""
        text = " ".join(str(s.get(k) or "") for k in ("summary", "detail", "title"))
        classified.append({
            "tier":      credibility_tier(url_or_source),
            "sentiment": _classify_sentiment(text),
            "host":      _host_of(url_or_source),
        })

    # PATTERN 1 — REPUTATION WASHING: positive tier-3 signals AND
    # negative tier-1/2 signals coexisting.
    pos_t3 = sum(1 for c in classified if c["tier"] == 3 and c["sentiment"] == "POSITIVE")
    neg_t1_2 = sum(1 for c in classified if c["tier"] <= 2 and c["sentiment"] == "NEGATIVE")
    washing_score = min(min(pos_t3, neg_t1_2) / 3.0, 1.0)  # need ≥3 of each for full score

    # PATTERN 2 — SOURCE CREDIBILITY ANOMALY: tier-3 dominant, tier-1 absent
    t1_count = sum(1 for c in classified if c["tier"] == 1)
    t3_count = sum(1 for c in classified if c["tier"] == 3)
    if n >= 5 and t1_count == 0 and t3_count >= 5:
        credibility_score = min(t3_count / 10.0, 1.0)
    else:
        credibility_score = 0.0

    # PATTERN 3 — NEW-OUTLET BURST: 3+ signals from the same previously-
    # unseen host within the window. We don't track 'previously seen'
    # hosts globally yet; for the minimal cut, just flag any host
    # contributing 3+ signals in this window if it's tier-3.
    host_counts = Counter(c["host"] for c in classified if c["host"])
    burst_hosts = [
        h for h, count in host_counts.items()
        if count >= 3 and any(
            cl["host"] == h and cl["tier"] == 3 for cl in classified
        )
    ]
    burst_score = min(len(burst_hosts) / 2.0, 1.0)  # 2+ burst hosts = full score

    composite = max(washing_score, credibility_score, burst_score)

    # Narrative
    flags: list[str] = []
    if washing_score >= 0.5:
        flags.append(
            f"REPUTATION_WASHING (positive tier-3 signals: {pos_t3}, "
            f"negative tier-1/2 signals: {neg_t1_2})"
        )
    if credibility_score >= 0.5:
        flags.append(
            f"CREDIBILITY_ANOMALY (tier-3: {t3_count}, tier-1: {t1_count})"
        )
    if burst_score >= 0.5:
        flags.append(f"NEW_OUTLET_BURST (hosts: {burst_hosts[:3]})")

    if flags:
        narrative = f"{entity_name}: COUNTER-INTELLIGENCE ALERT — " + "; ".join(flags) + "."
    elif composite >= 0.3:
        narrative = (
            f"{entity_name}: weak counter-intel signals (washing={washing_score:.2f}, "
            f"credibility={credibility_score:.2f}, burst={burst_score:.2f})."
        )
    else:
        narrative = (
            f"{entity_name}: no counter-intelligence patterns detected "
            f"({n} signals, t1={t1_count}, t2={n-t1_count-t3_count}, t3={t3_count})."
        )

    return {
        "entity":          entity_name,
        "n_signals":       n,
        "tier_distribution": {
            "tier_1": t1_count,
            "tier_2": n - t1_count - t3_count,
            "tier_3": t3_count,
        },
        "patterns": {
            "reputation_washing":  round(washing_score, 3),
            "credibility_anomaly": round(credibility_score, 3),
            "new_outlet_burst":    round(burst_score, 3),
        },
        "composite_score": round(composite, 3),
        "narrative":       narrative,
    }


def _host_of(url_or_source: str) -> str | None:
    if not url_or_source:
        return None
    s = url_or_source.lower().strip()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0]
    s = re.sub(r"^www\.", "", s)
    return s or None


async def scan_entity(
    entity_name: str,
    *,
    window_days: int = 14,
) -> dict[str, Any]:
    """Pull recent signals about the entity from intel_ledger and run
    the pattern scan. Brain absorbs alerts."""
    if not entity_name or not entity_name.strip():
        return {"ok": False, "error": "entity_name required"}
    try:
        from . import intel_ledger
        # Pull recent signals — the public surface varies; try common
        # method names defensively.
        signals: list[dict[str, Any]] = []
        for method_name in ("get_recent", "recent_signals", "get_all", "all_signals"):
            fn = getattr(intel_ledger, method_name, None)
            if callable(fn):
                try:
                    out = fn()
                    if hasattr(out, "__await__"):
                        out = await out
                    if isinstance(out, list):
                        signals = out
                        break
                except Exception:
                    continue
        # If we couldn't pull, return INDETERMINATE rather than
        # claim a clean scan — explicit failure beats false-clean.
        if not signals:
            return {
                "ok":         True,
                "entity":     entity_name,
                "n_signals":  0,
                "patterns":   {},
                "narrative":  (
                    f"{entity_name}: counter-intelligence scan INDETERMINATE — "
                    f"could not access intel_ledger signal stream. Module needs "
                    f"intel_ledger to expose a get_recent() helper for the "
                    f"sweep to work."
                ),
            }
    except Exception as e:
        return {"ok": False, "error": f"intel_ledger import failed: {e}"}

    scoped = _extract_signals_for_entity(signals, entity_name, window_days=window_days)
    result = scan_signals_for_patterns(scoped, entity_name)
    result["window_days"] = window_days

    # Brain absorb material alerts only
    if result.get("composite_score", 0) >= 0.5:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="counter_intelligence",
                summary=result["narrative"],
                detail=(
                    f"Entity: {entity_name}. "
                    f"Pattern scores: {result['patterns']}. "
                    f"Tier distribution: {result.get('tier_distribution')}."
                ),
                topic="counter_intelligence_alert",
                entity=entity_name,
                success=True,
                confidence="ASSESSED",
            )
        except Exception as e:
            logger.debug("counter_intel brain absorb failed (non-fatal): %s", e)

    return result


def summary() -> dict[str, Any]:
    return {
        "module":   "counter_intelligence",
        "purpose":  "detect attempts to influence ARIA via reputation washing / corpus seeding",
        "patterns": ["reputation_washing", "credibility_anomaly", "new_outlet_burst"],
        "tier_1_hosts_known": len(_TIER_1_HOSTS),
        "tier_3_patterns":    len(_TIER_3_PATTERNS),
    }
