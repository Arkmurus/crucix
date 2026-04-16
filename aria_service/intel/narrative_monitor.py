"""
ARIA Cognitive Warfare Narrative Monitor

Detects coordinated messaging campaigns and information operations
in defence markets. Runs hourly against the intel ledger + news
signals to identify:
  1. Narrative spikes — same phrases/themes appearing in 3+ sources within 48h
  2. Source coordination — multiple "independent" outlets pushing identical framing
  3. Market manipulation — rumours about contract awards/losses designed to
     influence competitors or government decision-makers
  4. OEM reputation attacks — coordinated negative coverage of specific OEMs
     in specific markets (e.g., "Turkish drones unreliable" campaign in Africa)
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger("aria.narrative_monitor")

# ── Negative sentiment keywords for OEM attack detection ─────────────────
_NEGATIVE_KEYWORDS = [
    "unreliable", "failed", "defective", "cancelled", "rejected",
    "banned", "unsafe", "obsolete", "crashed", "malfunctioned",
    "recalled", "faulty", "inferior", "substandard", "dangerous",
    "grounded", "withdrawn", "abandoned", "incompetent", "unfit",
]

_NEGATIVE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _NEGATIVE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Coordination detection threshold — fraction of shared key phrases
# between two signals from different sources to flag coordination.
COORDINATION_THRESHOLD = 0.6

# Minimum n-gram frequency across signals to count as a "key phrase"
MIN_PHRASE_FREQUENCY = 2

# Spike detection: current window count must exceed this multiplier
# times the rolling average to trigger a spike alert.
DEFAULT_SPIKE_MULTIPLIER = 3


# ── Helpers ──────────────────────────────────────────────────────────────

def _extract_ngrams(text: str, min_n: int = 3, max_n: int = 5) -> list[str]:
    """Extract n-grams (3-5 word sequences) from text.

    Uses simple whitespace tokenisation and lowercasing.
    Strips punctuation from tokens to normalise matching.
    """
    if not text:
        return []
    # Tokenise: lowercase, strip non-alphanumeric edges
    tokens = [
        re.sub(r"[^a-z0-9]", "", w)
        for w in text.lower().split()
    ]
    tokens = [t for t in tokens if len(t) > 1]  # drop single-char noise

    ngrams: list[str] = []
    for n in range(min_n, max_n + 1):
        for i in range(len(tokens) - n + 1):
            ngrams.append(" ".join(tokens[i : i + n]))
    return ngrams


def _jaccard_phrases(phrases_a: set[str], phrases_b: set[str]) -> float:
    """Jaccard similarity between two phrase sets."""
    if not phrases_a or not phrases_b:
        return 0.0
    intersection = phrases_a & phrases_b
    union = phrases_a | phrases_b
    return len(intersection) / len(union) if union else 0.0


def _signals_in_window(signals: list[dict], window_hours: int) -> list[dict]:
    """Filter signals to those within the last window_hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    return [s for s in signals if (s.get("ts") or "") >= cutoff]


# ── A) scan_narratives ───────────────────────────────────────────────────

async def scan_narratives(window_hours: int = 48) -> dict:
    """Pull recent signals, group by country+topic, detect coordination.

    Returns:
        {
            "clusters": [cluster_dicts],
            "alerts": [alert_dicts],
            "scan_window_hours": N,
            "signals_analysed": N,
        }
    """
    from . import intel_ledger

    try:
        db = await intel_ledger._load()
    except Exception as exc:
        logger.error("narrative_monitor: failed to load ledger: %s", exc)
        return {
            "clusters": [], "alerts": [],
            "scan_window_hours": window_hours,
            "signals_analysed": 0,
            "error": str(exc),
        }

    all_signals = db.get("signals", [])
    recent = _signals_in_window(all_signals, window_hours)

    if not recent:
        return {
            "clusters": [], "alerts": [],
            "scan_window_hours": window_hours,
            "signals_analysed": 0,
        }

    # Group signals by (country, product_category) pairs
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sig in recent:
        countries = sig.get("countries") or ["_global"]
        products = sig.get("products") or ["_general"]
        for country in countries:
            for product in products:
                groups[(country, product)].append(sig)

    clusters: list[dict] = []
    alerts: list[dict] = []

    for (country, topic), group_signals in groups.items():
        if len(group_signals) < 2:
            continue

        # Extract key phrases per signal
        signal_phrases: list[tuple[dict, set[str]]] = []
        all_ngrams: Counter[str] = Counter()

        for sig in group_signals:
            text = sig.get("text", "")
            ngrams = _extract_ngrams(text)
            all_ngrams.update(ngrams)
            signal_phrases.append((sig, set(ngrams)))

        # Key phrases: n-grams appearing in 2+ signals
        key_phrases = {
            phrase for phrase, count in all_ngrams.items()
            if count >= MIN_PHRASE_FREQUENCY
        }

        if not key_phrases:
            continue

        # Score coordination: pairwise comparison of signals from different sources
        coordination_pairs: list[dict] = []
        for i in range(len(signal_phrases)):
            for j in range(i + 1, len(signal_phrases)):
                sig_a, phrases_a = signal_phrases[i]
                sig_b, phrases_b = signal_phrases[j]

                # Only flag coordination between DIFFERENT sources
                src_a = (sig_a.get("source") or "").lower().strip()
                src_b = (sig_b.get("source") or "").lower().strip()
                if src_a == src_b:
                    continue

                # Restrict to shared key phrases only
                shared_key_a = phrases_a & key_phrases
                shared_key_b = phrases_b & key_phrases
                score = _jaccard_phrases(shared_key_a, shared_key_b)

                if score >= COORDINATION_THRESHOLD:
                    coordination_pairs.append({
                        "source_a": src_a,
                        "source_b": src_b,
                        "score": round(score, 3),
                        "shared_phrases": sorted(shared_key_a & shared_key_b)[:10],
                    })

        # Compute cluster-level coordination score
        if not coordination_pairs:
            continue

        avg_coordination = (
            sum(p["score"] for p in coordination_pairs) / len(coordination_pairs)
        )

        cluster = {
            "country": country,
            "topic": topic,
            "signal_count": len(group_signals),
            "key_phrase_count": len(key_phrases),
            "coordination_score": round(avg_coordination, 3),
            "coordination_pairs": coordination_pairs[:10],
            "top_phrases": sorted(key_phrases, key=lambda p: all_ngrams[p], reverse=True)[:10],
            "sources": sorted({(s.get("source") or "unknown") for s in group_signals}),
        }
        clusters.append(cluster)

        # Generate alert if coordination exceeds threshold
        if avg_coordination >= COORDINATION_THRESHOLD:
            alert = {
                "type": "coordinated_campaign",
                "country": country,
                "topic": topic,
                "coordination_score": round(avg_coordination, 3),
                "source_count": len(cluster["sources"]),
                "signal_count": len(group_signals),
                "summary": (
                    f"Possible coordinated messaging detected: {len(coordination_pairs)} "
                    f"source pair(s) in {country}/{topic} sharing >{COORDINATION_THRESHOLD:.0%} "
                    f"of key phrases within {window_hours}h window."
                ),
            }
            alerts.append(alert)

    # Sort clusters by coordination score descending
    clusters.sort(key=lambda c: c["coordination_score"], reverse=True)

    # Feed brain on notable findings
    if alerts:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="narrative_monitor",
                summary=(
                    f"Narrative scan: {len(alerts)} coordination alert(s) "
                    f"across {len(clusters)} cluster(s) from {len(recent)} signals "
                    f"({window_hours}h window)."
                ),
                success=True,
                confidence="ASSESSED",
            )
        except Exception:
            pass

    return {
        "clusters": clusters,
        "alerts": alerts,
        "scan_window_hours": window_hours,
        "signals_analysed": len(recent),
    }


# ── B) detect_narrative_spike ────────────────────────────────────────────

async def detect_narrative_spike(
    country: str,
    topic: str,
    threshold: int = DEFAULT_SPIKE_MULTIPLIER,
) -> Optional[dict]:
    """Check if a (country, topic) pair has spiked in signal volume.

    Compares the last 48h count against a 30-day rolling average.

    Returns:
        Spike alert dict if detected, else None.
    """
    from . import intel_ledger

    try:
        db = await intel_ledger._load()
    except Exception as exc:
        logger.error("narrative_monitor: spike detection failed: %s", exc)
        return None

    all_signals = db.get("signals", [])
    country_lower = country.lower()
    topic_lower = topic.lower()

    def _matches(sig: dict) -> bool:
        """Check if signal matches country and topic."""
        sig_countries = [c.lower() for c in (sig.get("countries") or [])]
        sig_products = [p.lower() for p in (sig.get("products") or [])]
        sig_text = (sig.get("text") or "").lower()
        country_match = country_lower in sig_countries or country_lower in sig_text
        topic_match = (
            topic_lower in sig_products
            or topic_lower in sig_text
            or topic_lower in (sig.get("type") or "").lower()
        )
        return country_match and topic_match

    now = datetime.now(timezone.utc)
    cutoff_48h = (now - timedelta(hours=48)).isoformat()
    cutoff_30d = (now - timedelta(days=30)).isoformat()

    current_signals = [
        s for s in all_signals
        if (s.get("ts") or "") >= cutoff_48h and _matches(s)
    ]
    month_signals = [
        s for s in all_signals
        if (s.get("ts") or "") >= cutoff_30d and _matches(s)
    ]

    current_count = len(current_signals)
    # 30-day average normalised to 48h windows: 30 days = 15 windows of 48h
    month_count = len(month_signals)
    avg_per_window = month_count / 15.0 if month_count > 0 else 0.0

    if avg_per_window <= 0 or current_count < 2:
        return None

    spike_ratio = current_count / avg_per_window

    if spike_ratio < threshold:
        return None

    result = {
        "country": country,
        "topic": topic,
        "current_count": current_count,
        "average_count": round(avg_per_window, 2),
        "spike_ratio": round(spike_ratio, 2),
        "contributing_signals": [
            {
                "text": s.get("text", "")[:200],
                "source": s.get("source", ""),
                "ts": s.get("ts", ""),
            }
            for s in current_signals[:10]
        ],
    }

    # Feed brain
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="narrative_monitor",
            summary=(
                f"Narrative spike detected: {country}/{topic} — "
                f"{current_count} signals in 48h vs {avg_per_window:.1f} avg "
                f"(ratio {spike_ratio:.1f}x)."
            ),
            success=True,
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return result


# ── C) detect_oem_attack ─────────────────────────────────────────────────

async def detect_oem_attack(
    oem_name: str,
    window_hours: int = 72,
) -> Optional[dict]:
    """Search intel ledger for coordinated negative sentiment about an OEM.

    Cross-references with source tiers: Tier 4/5 sources pushing negative
    OEM narratives are flagged as suspicious.

    Returns:
        Attack detection dict if suspicious pattern found, else None.
    """
    from . import intel_ledger

    try:
        db = await intel_ledger._load()
    except Exception as exc:
        logger.error("narrative_monitor: OEM attack detection failed: %s", exc)
        return None

    all_signals = db.get("signals", [])
    recent = _signals_in_window(all_signals, window_hours)
    oem_lower = oem_name.lower()

    # Find signals mentioning the OEM
    oem_signals = []
    for sig in recent:
        text = (sig.get("text") or "").lower()
        sig_oems = [o.lower() for o in (sig.get("oems") or [])]
        if oem_lower in text or oem_lower in sig_oems:
            oem_signals.append(sig)

    if not oem_signals:
        return None

    # Check for negative sentiment
    negative_signals = []
    for sig in oem_signals:
        text = sig.get("text", "")
        matches = _NEGATIVE_RE.findall(text)
        if matches:
            negative_signals.append({
                **sig,
                "_negative_keywords": [m.lower() for m in matches],
            })

    if not negative_signals:
        return None

    # Source tier breakdown
    tier_breakdown: dict[str, int] = defaultdict(int)
    suspicious_sources: list[str] = []
    for sig in negative_signals:
        tier = sig.get("source_tier") or "unknown"
        tier_breakdown[tier] += 1
        # Tier 4/5 or unknown pushing negative narratives = suspicious
        if tier in ("tier_4", "tier_5", "unknown", ""):
            source = sig.get("source", "unknown")
            if source not in suspicious_sources:
                suspicious_sources.append(source)

    # Affected markets
    markets: set[str] = set()
    for sig in negative_signals:
        for c in (sig.get("countries") or []):
            markets.add(c)

    # Coordination check: are multiple different sources pushing the same
    # negative language?
    source_set = {(s.get("source") or "unknown") for s in negative_signals}
    suspected_coordination = len(source_set) >= 2 and len(negative_signals) >= 3

    if len(negative_signals) < 2:
        return None

    result = {
        "oem": oem_name,
        "negative_signal_count": len(negative_signals),
        "source_tier_breakdown": dict(tier_breakdown),
        "suspected_coordination": suspected_coordination,
        "suspicious_sources": suspicious_sources[:10],
        "markets_affected": sorted(markets),
        "window_hours": window_hours,
        "sample_signals": [
            {
                "text": s.get("text", "")[:200],
                "source": s.get("source", ""),
                "keywords": s.get("_negative_keywords", []),
                "ts": s.get("ts", ""),
            }
            for s in negative_signals[:8]
        ],
    }

    # Feed brain
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="narrative_monitor",
            summary=(
                f"OEM attack pattern: {oem_name} — {len(negative_signals)} negative "
                f"signals from {len(source_set)} source(s) in {window_hours}h. "
                f"Markets: {', '.join(sorted(markets)[:5])}. "
                f"Coordination: {'suspected' if suspected_coordination else 'not detected'}."
            ),
            success=True,
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return result


# ── D) generate_narrative_briefing ───────────────────────────────────────

async def generate_narrative_briefing() -> str:
    """Generate a WhatsApp-friendly narrative environment section
    for the daily team briefing.

    Summarises active narrative campaigns detected in the last 7 days.
    """
    from . import intel_ledger

    lines: list[str] = []

    # 1. Run a broad 7-day scan
    scan = await scan_narratives(window_hours=168)  # 7 days
    clusters = scan.get("clusters") or []
    alerts = scan.get("alerts") or []

    # 2. Check all known OEMs for attack patterns (last 7 days)
    oem_attacks: list[dict] = []
    for oem in intel_ledger.OEMS:
        try:
            attack = await detect_oem_attack(oem, window_hours=168)
            if attack:
                oem_attacks.append(attack)
        except Exception as exc:
            logger.debug("OEM attack check failed for %s: %s", oem, exc)

    # 3. Check top markets for spikes
    spikes: list[dict] = []
    try:
        db = await intel_ledger._load()
        # Find the most active countries in recent signals
        country_counts: Counter[str] = Counter()
        recent = _signals_in_window(db.get("signals", []), 168)
        for sig in recent:
            for c in (sig.get("countries") or []):
                country_counts[c] += 1

        for country, _ in country_counts.most_common(10):
            for topic in ["aircraft", "naval", "vehicles", "missiles", "ammunition"]:
                try:
                    spike = await detect_narrative_spike(country, topic)
                    if spike:
                        spikes.append(spike)
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Spike detection loop failed: %s", exc)

    # Nothing to report
    if not alerts and not oem_attacks and not spikes:
        return ""

    lines.append("")
    lines.append("*NARRATIVE ENVIRONMENT*")

    if alerts:
        lines.append(f"_{len(alerts)} coordinated messaging pattern(s) detected:_")
        for alert in alerts[:5]:
            lines.append(
                f"  - *{alert['country']}* / {alert['topic']}: "
                f"coordination score {alert['coordination_score']:.0%}, "
                f"{alert['source_count']} sources, "
                f"{alert['signal_count']} signals"
            )
        lines.append("")

    if oem_attacks:
        lines.append(f"_{len(oem_attacks)} OEM reputation attack pattern(s):_")
        for atk in oem_attacks[:5]:
            coord = " [COORDINATED]" if atk["suspected_coordination"] else ""
            markets = ", ".join(atk["markets_affected"][:3])
            lines.append(
                f"  - *{atk['oem']}*: {atk['negative_signal_count']} negative signals{coord}"
                f" — markets: {markets or 'global'}"
            )
        lines.append("")

    if spikes:
        lines.append(f"_{len(spikes)} narrative spike(s):_")
        for sp in spikes[:5]:
            lines.append(
                f"  - *{sp['country']}* / {sp['topic']}: "
                f"{sp['current_count']} signals in 48h "
                f"({sp['spike_ratio']:.1f}x vs average)"
            )
        lines.append("")

    return "\n".join(lines)
