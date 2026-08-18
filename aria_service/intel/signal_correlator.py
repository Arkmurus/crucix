"""ARIA Signal Correlato

r — connects the dots across intelligence sources.

The difference between a news ticker and an intelligence analyst is
CORRELATION: seeing that "Angola budget increase" + "new defence minister"
+ "SIMPORTEX tender" + "competitor Baykar absent" = HIGH PRIORITY window.

This module reads all signal sources (ledger, pipeline, contacts, knowledge,
brain hook) and produces CORRELATED INSIGHTS — compound assessments that
no single source could provide alone.

Correlation types:
  OPPORTUNITY_WINDOW  — budget + procurement + political alignment
  COMPETITIVE_VACUUM  — competitor absent/weak + active requirement
  RELATIONSHIP_LEVERAGE — warm contact + active opportunity in their org
  URGENCY_SIGNAL      — deadline approaching + lead stale + competitor active
  MARKET_HEATING      — multiple signals in same country within 7 days
  RISK_CONVERGENCE    — sanctions + compliance + political instability overlap"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger("aria.signal_correlator")

# ── Correlation weights ───────────────────────────────────────────────────────
# Each signal type contributes to an opportunity score. When multiple
# signals converge on the same country/entity within a time window,
# the correlation engine fires a compound insight.

SIGNAL_WEIGHTS = {
    "budget_increase": 3.0,
    "new_minister": 2.5,
    "active_tender": 4.0,
    "competitor_win": -2.0,  # negative — competitor took the space
    "competitor_absent": 2.0,
    "warm_contact": 2.0,
    "pipeline_lead": 1.5,
    "procurement_signal": 3.0,
    "conflict_escalation": 1.0,  # drives urgency but also risk
    "sanctions_change": 2.0,
    "election_transition": 1.5,
    "fms_notification": 2.5,
    "defence_news": 1.0,
    "osint_signal": 0.5,
}

CORRELATION_WINDOW_DAYS = 14  # signals within 14 days are "correlated"
MIN_CORRELATION_SCORE = 5.0   # minimum score to generate an insight
MIN_SIGNALS_FOR_INSIGHT = 2   # need at least 2 different signal types

# ── R-F3521 — temporal compounding ────────────────────────────────────────────
# Time was a BINARY filter here: `if ts < cutoff: continue`. A signal 13 days old
# counted exactly as much as one from this morning, and everything older than 14
# days was discarded outright. The ledger retains ~100 years by design (§7 — no
# TTL on knowledge; live 2026-07-30: 72,729 signals), so the correlator was
# throwing away almost all of ARIA's own memory on every call.
#
# What that costs is the ability to COMPOUND: whether a country's activity is
# accelerating, merely sustained, or decaying is invisible when the only question
# asked is "did this land inside a fortnight". Two countries with identical
# 14-day scores are indistinguishable today even when one has been building for
# three months and the other appeared on Tuesday.
#
# chain_correlator.py does NOT cover this. It models 12–18 month causal chains
# from STRUCTURAL shifts (coup, sanctions change, budget announcement) gated at
# MIN_SEVERITY 0.35. Ordinary signal tempo between 14 and 90 days is seen by
# neither module.
#
# THE ANTI-INFLATION PROPERTY, which is the whole design constraint:
# widening a window raises every score and fires more insights — "a grade that
# improves without new evidence IS the false clean". So historical evidence is
# applied as an ANNOTATION on insights that have ALREADY been generated, never as
# an input to generating them. It cannot create an insight, suppress one, or move
# a score by any amount. That is structural, not a promise: _annotate_trajectories
# runs after _generate_insight and only adds fields.
HISTORICAL_WINDOW_DAYS = 90   # 15..90d informs TRAJECTORY only, never the score

# A trajectory is a claim about the world, so it obeys the same independence rule
# as MARKET_HEATING (R-F3487): it is computed from independent ORIGINS, not from
# signal volume. Otherwise syndication drives the trend line and the fabrication
# this product exists to prevent simply reappears on the time axis.
_MIN_TRAJECTORY_ORIGINS = int(os.getenv("ARIA_TRAJECTORY_MIN_ORIGINS", "2"))

# Ratio of active-band rate to historical-band rate needed to call a direction.
_TRAJECTORY_ACCEL_RATIO = 2.0
_TRAJECTORY_DECAY_RATIO = 0.5

# Trajectory vocabulary. UNKNOWN is load-bearing and tri-state in the same sense
# as the phase gates: "could not measure" is not "measured and found flat".
TRAJECTORY_ACCELERATING = "ACCELERATING"
TRAJECTORY_SUSTAINED = "SUSTAINED"
TRAJECTORY_DECAYING = "DECAYING"
TRAJECTORY_EMERGING = "EMERGING"
TRAJECTORY_UNKNOWN = "UNKNOWN"


async def correlate_signals() -> list[dict]:
    """Run full cross-source correlation and return compound insights.

    Reads from:
      - intel_ledger (30-day rolling signals)
      - deal_pipeline (active leads)
      - contact_intelligence (warm contacts)
      - brain_hook stats (module activity)

    Returns a list of correlated insights, each with:
      - country, score, signals (what contributed), insight_type, recommendation
    """
    from . import intel_ledger, deal_pipeline, contact_intelligence

    insights: list[dict] = []
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=CORRELATION_WINDOW_DAYS)).isoformat()

    # ── Gather signals by country ─────────────────────────────────────────
    country_signals: dict[str, list[dict]] = {}

    # 1. Intel ledger signals
    try:
        ledger = await intel_ledger._load()
        for sig in ledger.get("signals", []):
            ts = sig.get("ts", "")
            if ts < cutoff:
                continue
            for country in sig.get("countries", []):
                country_lower = country.lower()
                country_signals.setdefault(country_lower, [])

                sig_type = _classify_signal(sig)
                # R-F3487 — carry PROVENANCE, not just a source label. Without
                # the url the independence check can only compare source strings,
                # and without a story fingerprint it cannot detect syndication at
                # all (same text, four domains). intel_ledger already persists
                # the url on every signal, so this costs nothing.
                _text = sig.get("text", "")
                country_signals[country_lower].append({
                    "type": sig_type,
                    "text": _text[:200],
                    "source": sig.get("source", ""),
                    "url": sig.get("url", ""),
                    "story": _story_fingerprint(_text),
                    "ts": ts,
                    "weight": SIGNAL_WEIGHTS.get(sig_type, 0.5),
                })
    except Exception as e:
        logger.debug("Ledger correlation failed: %s", e)
        # R-F2008/§21a — the intel_ledger is the PRIMARY correlation source; if it
        # can't be read the chain is degraded, so the brain must know (success is
        # already wired below). Fire-and-forget; never breaks correlation.
        try:
            wire_failure(module="signal_correlator",
                         detail=f"ledger read failed during correlation: {str(e)[:200]}",
                         gap_type="agent_cycle_failure", source="signal_correlator:correlate")
        except Exception:
            pass

    # 2. Pipeline leads
    try:
        leads = await deal_pipeline.get_pipeline()
        for lead in leads:
            country = lead.get("country", "").lower()
            if not country:
                continue
            country_signals.setdefault(country, [])
            country_signals[country].append({
                "type": "pipeline_lead",
                "text": f"[{lead.get('stage', '?')}] {lead.get('requirement', '')[:100]}",
                "source": f"pipeline:{lead.get('id', '')}",
                "ts": lead.get("created_at", ""),
                "weight": SIGNAL_WEIGHTS["pipeline_lead"],
            })
    except Exception as e:
        logger.debug("Pipeline correlation failed: %s", e)

    # 3. Warm contacts
    try:
        contacts = await contact_intelligence.get_contacts()
        for c in contacts:
            country = c.get("country", "").lower()
            status = c.get("_status", "UNKNOWN")
            if not country or status not in ("ACTIVE", "COOLING"):
                continue
            country_signals.setdefault(country, [])
            country_signals[country].append({
                "type": "warm_contact",
                "text": f"{c.get('name', '?')} at {c.get('org', '?')} ({c.get('role', '')})",
                "source": f"contact:{c.get('id', '')}",
                "ts": c.get("last_contact_date", ""),
                "weight": SIGNAL_WEIGHTS["warm_contact"],
            })
    except Exception as e:
        logger.debug("Contact correlation failed: %s", e)

    # ── Score and generate insights ───────────────────────────────────────
    for country, signals in country_signals.items():
        if len(signals) < MIN_SIGNALS_FOR_INSIGHT:
            continue

        # Unique signal types
        signal_types = set(s["type"] for s in signals)
        if len(signal_types) < MIN_SIGNALS_FOR_INSIGHT:
            continue

        total_score = sum(s["weight"] for s in signals)
        if total_score < MIN_CORRELATION_SCORE:
            continue

        insight = _generate_insight(country, signals, signal_types, total_score)
        if insight:
            insights.append(insight)

    # R-F3521 — temporal context, applied AFTER every insight and score is final.
    # Ordering is the anti-inflation guarantee: historical evidence annotates, it
    # never participates. Sorting is still by score alone for the same reason —
    # an ACCELERATING trajectory must not silently reorder the operator's list.
    await _annotate_trajectories(insights)

    # Sort by score descending
    insights.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Brain hook
    if insights:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="signal_generator",
                summary=f"Signal correlation: {len(insights)} compound insights across {len(country_signals)} countries. Top: {insights[0]['country']} (score {insights[0]['score']:.1f})",
                success=True,
                confidence="ASSESSED",
            )
        except Exception:
            pass

    logger.info("[correlator] %d insights from %d countries", len(insights), len(country_signals))
    return insights


# R-F3521 — the historical band is 15..90-day-old data. Recomputing it on every
# chat message is wrong regardless of how fast the scan is: at live ledger scale
# (72,729 signals) it measured 39ms for one country and 98ms for three, and
# correlate_signals runs per message. The band cannot meaningfully change inside
# a few minutes, so it is cached PER COUNTRY — per country rather than per
# insight-set so two different queries share the work.
#
# Kept resettable, and reset by an autouse fixture in the test file. A leaked
# module-level cache is the exact mechanism behind the 15 order-dependent
# failures closed by R-F3449; a cache that survives between tests makes the
# second test observe the first one's answer.
_HIST_CACHE_TTL_S = int(os.getenv("ARIA_TRAJECTORY_CACHE_TTL_S", "300"))
_hist_cache: dict[str, tuple[float, dict]] = {}


def _reset_trajectory_cache() -> None:
    """Drop the historical-band cache. For tests and for an operator-forced recompute."""
    _hist_cache.clear()


def _historical_origins_by_country(
    ledger_signals: list, countries: set[str], now: datetime
) -> dict[str, dict]:
    """R-F3521 — independent origins in the 15..90d band, for named countries only.

    Restricted to countries that ALREADY produced an insight, which is normally a
    handful. That matters: correlate_signals runs on the chat request path
    (aria_engine._sync_correlation_context) over a ledger holding ~72k signals, so
    an unrestricted second pass would put real per-message latency on every reply.
    The membership test below is a set lookup per signal; descriptor building and
    the union-find happen only for the few countries that matter.

    Story identity MUST match the active band's (``_story_fingerprint`` over the
    full text). R-F3525: it originally used the ledger's cheaper dedup prefix
    (``text[:150]``) here, which merged historical stories that the active band
    kept separate — an under-counted denominator that made 53 of 54 live
    countries read ACCELERATING. Whenever two measurements are compared to each
    other, the same instrument has to produce both.
    """
    # Serve what is still fresh; scan only for the rest. If every requested
    # country is cached the ledger is not walked at all.
    mono = time.monotonic()
    out: dict[str, dict] = {}
    pending = set()
    for c in countries:
        hit = _hist_cache.get(c)
        if hit and hit[0] > mono:
            out[c] = dict(hit[1])
        else:
            pending.add(c)
    if not pending:
        return out

    countries = pending
    active_cutoff = (now - timedelta(days=CORRELATION_WINDOW_DAYS)).isoformat()
    hist_cutoff = (now - timedelta(days=HISTORICAL_WINDOW_DAYS)).isoformat()

    # Deduped descriptors per country. A set, not a list: feeding the union-find
    # the same (publisher, story) pair a thousand times cannot change its answer,
    # so carrying the duplicates would only cost memory on the request path.
    buckets: dict[str, set] = {c: set() for c in countries}
    counts: dict[str, int] = {c: 0 for c in countries}

    for sig in ledger_signals:
        ts = sig.get("ts", "")
        # Strictly OUTSIDE the active window and inside the historical one. The
        # bands must not overlap or the same signal would be counted on both
        # sides of the comparison and every country would look flat.
        if not ts or ts >= active_cutoff or ts < hist_cutoff:
            continue
        sig_countries = sig.get("countries") or []
        if not sig_countries:
            continue
        # Membership FIRST. Building the descriptor before this test cost 131ms
        # per call at live ledger scale (72,729 signals) because it normalised
        # text for every country in the band, not the two or three an insight
        # actually asked about. Measured, not guessed.
        wanted = [c.lower() for c in sig_countries if c.lower() in buckets]
        if not wanted:
            continue
        text = sig.get("text", "") or ""
        # R-F3525 — the SAME identity the active band uses. The first cut used
        # the ledger's dedup prefix (text[:150]) here while correlate_signals
        # fingerprints the FULL text, so historical signals sharing a boilerplate
        # lead-in merged into one origin while identical-shaped active ones did
        # not. That under-counts the DENOMINATOR of the rate comparison, and it
        # showed: live, 53 of 54 countries read ACCELERATING. A verdict that
        # fires on 98% of subjects is measuring the instrument, not the world.
        # Two bands compared against each other must be measured the same way.
        story = _story_fingerprint(text)
        url = (sig.get("url") or "").strip() or (sig.get("source") or "").strip()
        for c in wanted:
            counts[c] += 1
            buckets[c].add((url, story))

    expiry = mono + _HIST_CACHE_TTL_S
    for c, descriptors in buckets.items():
        sources = [{"url": u, "story": s} if s else {"url": u} for u, s in descriptors]
        failed = False
        try:
            from .dd_independent_verifier import count_independent_origins
            origins = int(count_independent_origins(sources)) if sources else 0
        except Exception as exc:
            # Same rule as _independent_origins: NEVER inflate on failure. Zero
            # historical origins yields UNKNOWN below, not a confident verdict.
            logger.warning("[R-F3521] historical origin count failed for %s: %s", c, exc)
            origins, failed = 0, True
        entry = {"signals": counts[c], "origins": origins}
        out[c] = entry
        # Never cache a failure. Caching origins=0 from a broken counter would
        # pin a wrong answer for the full TTL and make the failure look like a
        # measurement.
        if not failed:
            _hist_cache[c] = (expiry, dict(entry))
    return out


def _trajectory(active_origins: int, hist: dict, baseline_ratio: float = 1.0) -> dict:
    """R-F3521 — direction of travel from independent-origin RATES, not volume.

    Rates, not totals: the bands are different lengths (14d vs 76d), so comparing
    raw counts would call almost everything "decaying" purely because the
    historical band is five times longer.

    R-F3526 — measured RELATIVE TO THE CORPUS, via ``baseline_ratio``. A country's
    own before/after ratio cannot distinguish "the world got busier" from "ARIA
    started collecting more", and on the live box it was the latter: 53 of 54
    countries read ACCELERATING, corpus-wide 4.82x against a median country 3.93x.
    Every country was moving with the tide, and the tide was our own ingestion
    growth. Dividing by the corpus baseline removes the collection-rate change
    and leaves only movement relative to everything else.

    ``baseline_ratio`` defaults to 1.0 — "no corpus-wide change" — so the pure
    rate comparison can still be unit-tested in isolation. _annotate_trajectories
    supplies the measured value, or refuses to call a direction at all.

    Returns UNKNOWN rather than guessing whenever the evidence cannot support a
    direction. "Could not measure" is not "measured and found flat" — the same
    tri-state discipline the phase gates use for `pass`.
    """
    hist_origins = int(hist.get("origins") or 0)
    total = int(active_origins or 0) + hist_origins

    if total < _MIN_TRAJECTORY_ORIGINS:
        return {"trajectory": TRAJECTORY_UNKNOWN,
                "basis": (f"insufficient independent evidence to call a direction "
                          f"({total} origin(s) across 90 days; need "
                          f"{_MIN_TRAJECTORY_ORIGINS})")}

    active_rate = active_origins / float(CORRELATION_WINDOW_DAYS)
    hist_days = float(HISTORICAL_WINDOW_DAYS - CORRELATION_WINDOW_DAYS)
    hist_rate = hist_origins / hist_days if hist_days > 0 else 0.0

    if hist_origins == 0:
        # No independent prior coverage at all. Genuinely new activity — which is
        # useful and is NOT the same claim as "accelerating from a known base".
        return {"trajectory": TRAJECTORY_EMERGING,
                "basis": (f"{active_origins} independent origin(s) in the last "
                          f"{CORRELATION_WINDOW_DAYS} days with no independent "
                          f"coverage in the preceding {int(hist_days)}")}

    ratio = active_rate / hist_rate if hist_rate > 0 else float("inf")
    base = float(baseline_ratio) if baseline_ratio and baseline_ratio > 0 else 1.0
    relative = ratio / base

    if relative >= _TRAJECTORY_ACCEL_RATIO:
        label = TRAJECTORY_ACCELERATING
    elif relative <= _TRAJECTORY_DECAY_RATIO:
        label = TRAJECTORY_DECAYING
    else:
        label = TRAJECTORY_SUSTAINED

    against = ("" if abs(base - 1.0) < 1e-9 else
               f", vs {base:.1f}x across all tracked countries")
    return {"trajectory": label,
            "basis": (f"{active_origins} independent origin(s) in {CORRELATION_WINDOW_DAYS}d "
                      f"vs {hist_origins} in the preceding {int(hist_days)}d "
                      f"({ratio:.1f}x the prior rate{against})")}


# R-F3526 — how many countries are needed before a corpus baseline means anything.
# With one country the baseline IS that country, so its relative ratio is 1.0 and
# it would always read SUSTAINED — an answer that looks measured and is not.
_MIN_BASELINE_COUNTRIES = int(os.getenv("ARIA_TRAJECTORY_MIN_BASELINE_COUNTRIES", "5"))


def _corpus_baseline(insights: list[dict], hist: dict) -> tuple[Optional[float], str]:
    """R-F3526 — the corpus-wide active:historical rate ratio, or a refusal.

    This is the correction for ARIA's own collection rate. A country's before/after
    ratio answers "is there more reporting than there was", which is NOT the
    question — it cannot separate a busier world from a busier crawler. Measured
    live 2026-07-30: corpus-wide 4.82x against a median country of 3.93x, so 53 of
    54 countries read ACCELERATING purely because ingestion had grown ~4-5x after
    this week's news-pipeline work (R-F3486/R-F3494/R-F3509).

    Dividing each country by this baseline asks the answerable question instead:
    is this country moving differently from everything else we track?

    Returns (None, reason) when no honest baseline exists. UNKNOWN is the correct
    output there — an unnormalised direction would be a false finding, and this
    engine's whole purpose is to not produce those.
    """
    pairs = []
    for i in insights:
        c = str(i.get("country", "")).lower()
        h = hist.get(c) or {}
        a = int(i.get("independent_origins") or 0)
        ho = int(h.get("origins") or 0)
        if ho > 0:
            pairs.append((a, ho))

    if len(pairs) < _MIN_BASELINE_COUNTRIES:
        return None, (
            f"no corpus baseline available ({len(pairs)} country/countries with "
            f"historical coverage; need {_MIN_BASELINE_COUNTRIES}) — a direction "
            f"here could not be separated from a change in ARIA's own collection "
            f"rate, so none is claimed"
        )

    total_active = sum(a for a, _ in pairs)
    total_hist = sum(h for _, h in pairs)
    if total_hist <= 0 or total_active <= 0:
        return None, "no corpus baseline available (empty band totals)"

    hist_days = float(HISTORICAL_WINDOW_DAYS - CORRELATION_WINDOW_DAYS)
    baseline = (total_active / float(CORRELATION_WINDOW_DAYS)) / (total_hist / hist_days)
    return (baseline if baseline > 0 else None), ""


async def _annotate_trajectories(insights: list[dict]) -> None:
    """R-F3521 — add temporal context to insights that have ALREADY been generated.

    Deliberately a post-pass that MUTATES rather than a parameter to
    _generate_insight. The anti-inflation property is then structural instead of
    a promise in a docstring: this function has no way to create an insight,
    remove one, or change a score, because by the time it runs those decisions are
    made and it only writes new keys.

    That ordering is the whole design. Widening a correlation window is the
    textbook way to make an engine look smarter while making it wrong more often
    — every score rises, more insights fire, and nothing new was actually learnt.

    Never raises: a failure here degrades trajectories to UNKNOWN and leaves every
    existing insight untouched.
    """
    if not insights:
        return
    try:
        from . import intel_ledger
        ledger = await intel_ledger._load()
        signals = ledger.get("signals", []) or []
        countries = {str(i.get("country", "")).lower() for i in insights}
        countries.discard("")
        # OFF the event loop. This is a pure-CPU scan of an in-memory list with no
        # I/O and no shared mutation, and correlate_signals sits on the chat
        # request path (aria_engine._sync_correlation_context) — the same place
        # R-F3475 found trafilatura blocking and causing live stalls. Measured at
        # live ledger scale it is tens of ms, which is small but is exactly the
        # kind of "small" that accumulates into a stall report.
        hist = await asyncio.to_thread(
            _historical_origins_by_country, signals, countries,
            datetime.now(timezone.utc))
        baseline, baseline_reason = _corpus_baseline(insights, hist)
        for insight in insights:
            c = str(insight.get("country", "")).lower()
            h = hist.get(c) or {"signals": 0, "origins": 0}
            if baseline is None:
                insight["trajectory"] = TRAJECTORY_UNKNOWN
                insight["trajectory_basis"] = baseline_reason
                insight["historical_signal_count"] = h["signals"]
                insight["historical_independent_origins"] = h["origins"]
                continue
            t = _trajectory(int(insight.get("independent_origins") or 0), h, baseline)
            insight["trajectory"] = t["trajectory"]
            insight["trajectory_basis"] = t["basis"]
            insight["historical_signal_count"] = h["signals"]
            insight["historical_independent_origins"] = h["origins"]
    except Exception as exc:
        logger.warning("[R-F3521] trajectory annotation failed: %s", exc)
        try:
            wire_failure(
                module="signal_correlator",
                detail=f"trajectory annotation failed, insights left untouched: {exc}"[:400],
                gap_type="engine_failure",
                source="signal_correlator:_annotate_trajectories")
        except Exception:
            pass


def _story_fingerprint(text: str) -> str:
    """R-F3487 — stable id for the underlying STORY, so syndicated copies of one
    wire report collapse to a single origin.

    Whitespace-normalised and case-folded, so trivial reformatting between
    syndication partners does not read as a different story. Mirrors
    news_archive.content_hash; empty text yields "" so an unfingerprintable
    signal falls back to publisher-family grouping rather than inventing a
    unique origin for itself.
    """
    norm = " ".join((text or "").split()).casefold()
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _classify_signal(sig: dict) -> str:
    """Classify a ledger signal into a correlation category."""
    text = (sig.get("text", "") + " " + sig.get("type", "")).lower()

    if any(kw in text for kw in ["budget", "spending", "expenditure", "allocation", "appropriation"]):
        if any(kw in text for kw in ["increase", "rise", "grow", "boost", "expand", "approve"]):
            return "budget_increase"
    if any(kw in text for kw in ["minister", "appointed", "cabinet", "reshuffle", "inaugurated"]):
        return "new_minister"
    if any(kw in text for kw in ["tender", "rfp", "rfq", "procurement", "bid", "contract award"]):
        return "active_tender"
    if any(kw in text for kw in ["baykar", "aselsan", "elbit", "norinco", "catic", "rosoboronexport"]):
        if any(kw in text for kw in ["won", "signed", "awarded", "delivered", "contract"]):
            return "competitor_win"
    if any(kw in text for kw in ["fms", "dsca", "foreign military sale"]):
        return "fms_notification"
    if any(kw in text for kw in ["sanctions", "embargo", "blacklist", "designated"]):
        return "sanctions_change"
    if any(kw in text for kw in ["election", "transition", "inaugurat", "sworn in"]):
        return "election_transition"
    if any(kw in text for kw in ["conflict", "attack", "insurgent", "military operation", "bombing"]):
        return "conflict_escalation"
    if sig.get("type") == "tender" or sig.get("type") == "bd_tender":
        return "active_tender"
    if sig.get("type") == "osint":
        return "osint_signal"
    return "defence_news"


_MIN_INDEPENDENT_ORIGINS = int(os.getenv("ARIA_CORRELATION_MIN_ORIGINS", "2"))


def _independent_origins(signals: list[dict]) -> int:
    """R-F3487 — how many INDEPENDENT sources are behind these signals.

    MARKET_HEATING fired on ``len(signals) >= 4`` alone. Combined with URL-only
    dedup upstream, four syndicated copies of one wire report — same story, four
    domains — read as a heating market. Reporting VOLUME is not evidence of
    real-world CHANGE, and telling a customer a market is heating because Reuters
    was republished four times is the exact fabrication this product exists to
    prevent (memory: "single-source = fabrication"; "one false positive destroys
    the USP").

    This delegates to dd_independent_verifier.count_independent_origins, which is
    already hardened by live evals: it is a union-find over publisher UNION story,
    so same-story/different-publisher collapses to one origin (syndication) and
    same-publisher/different-story also collapses to one (a publisher is not N
    witnesses). It is conservative by construction — the merge can only REDUCE
    the count, never inflate it.

    NOT built on intel/corroboration.py: that module has zero production callers
    and its fixtures were green while it scored 0/20 on real data. The USP gate
    must not rest on an unproven engine.

    On any failure this returns 1 (single origin), never a higher number:
    R-F3388's rule is that the false-positive rate on independence MUST be 0,
    while a conservative undercount is acceptable.
    """
    if not signals:
        return 0
    try:
        # R-F3547 — the propaganda-collapse rule moved into
        # dd_independent_verifier.count_independent_witnesses so the correlation
        # cards and news corroboration share ONE definition of an independent
        # witness. It used to live only here, which is how the news grader ended
        # up with no notion of it at all.
        from .dd_independent_verifier import count_independent_witnesses
        sources = []
        # R-F3536 — propaganda-tier channels are ONE origin between them, however
        # many of them repost a claim.
        #
        # The union-find collapses same-story and same-publisher, but two war
        # aggregators rewording the same claim look like two publishers with two
        # stories, so "✓ 2 publishers/channels" was rendered as corroboration on
        # the correlation cards — with 4 of 6 raw items on the live dashboard
        # coming from a single channel. ARIA's constitution already states these
        # sources' CONTENT IS NOT FACT and may never reach [CONFIRMED]
        # (aria_engine._PROPAGANDA_SOURCE_HINTS, after the 2026-04-09 Lebanon
        # fabrication). A rule that says a claim from one of them is unconfirmed,
        # but two of them are corroboration, contradicts itself. Same list, one
        # derivation point: they collapse into a single synthetic origin.
        for s in signals:
            loc = (s.get("url") or "").strip() or (s.get("source") or "").strip()
            story = (s.get("story") or s.get("content_hash") or "").strip()
            entry = {"url": loc, "source": (s.get("source") or "").strip()}
            if story:
                entry["story"] = story
            sources.append(entry)
        return int(count_independent_witnesses(sources))
    except Exception as exc:
        logger.warning(
            "[R-F3487] independence count failed — treating as SINGLE origin "
            "(never inflate): %s", exc)
        try:
            wire_failure(
                module="signal_correlator",
                detail=f"independent-origin count failed, collapsed to 1: {exc}"[:400],
                gap_type="engine_failure",
                source="signal_correlator:_independent_origins")
        except Exception:
            pass
        return 1


def _generate_insight(country: str, signals: list[dict], signal_types: set, score: float) -> dict | None:
    """Generate a compound insight from correlated signals."""
    # R-F3487 — computed once; every branch below reports it so a reader can see
    # WHY the insight was emitted, not just that it was.
    _origins = _independent_origins(signals)
    has_budget = "budget_increase" in signal_types
    has_tender = "active_tender" in signal_types or "procurement_signal" in signal_types
    has_minister = "new_minister" in signal_types or "election_transition" in signal_types
    has_contact = "warm_contact" in signal_types
    has_pipeline = "pipeline_lead" in signal_types
    has_competitor = "competitor_win" in signal_types
    has_fms = "fms_notification" in signal_types

    # Determine insight type
    if has_budget and has_tender and (has_minister or has_contact):
        insight_type = "OPPORTUNITY_WINDOW"
        emoji = "🟢"
        recommendation = (
            f"HIGH-PRIORITY: {country.title()} has budget momentum, active procurement, "
            f"{'and a new decision-maker creating a fresh engagement window' if has_minister else 'and a warm contact for direct approach'}. "
            f"ACT NOW — this window typically lasts 90-120 days."
        )
    elif has_tender and not has_competitor and has_contact:
        insight_type = "COMPETITIVE_VACUUM"
        emoji = "🔵"
        recommendation = (
            f"{country.title()} has active procurement with no detected competitor presence "
            f"and a warm relationship. First-mover advantage available — prepare proposal."
        )
    elif has_contact and has_pipeline:
        insight_type = "RELATIONSHIP_LEVERAGE"
        emoji = "🟡"
        recommendation = (
            f"Warm contact in {country.title()} aligns with an active pipeline lead. "
            f"Leverage the relationship to qualify and advance the deal."
        )
    elif has_competitor and has_tender:
        insight_type = "URGENCY_SIGNAL"
        emoji = "🔴"
        recommendation = (
            f"Competitor active in {country.title()} alongside live procurement. "
            f"If we don't engage now, we lose this cycle. Assess our differentiation."
        )
    elif len(signals) >= 4 and _origins >= _MIN_INDEPENDENT_ORIGINS:
        # R-F3487 — volume ALONE cannot claim a heating market. This now requires
        # the signals to span >= _MIN_INDEPENDENT_ORIGINS independent publishers,
        # so syndicated copies of one wire report can no longer clear the bar.
        insight_type = "MARKET_HEATING"
        emoji = "🟠"
        recommendation = (
            f"{country.title()} has {len(signals)} signals from {_origins} independent "
            f"sources in {CORRELATION_WINDOW_DAYS} days — market is heating. "
            f"Review pipeline coverage and contact freshness."
        )
    elif len(signals) >= 4:
        # Enough volume, but it traces back to a single origin. Report it
        # HONESTLY rather than suppressing it or inflating it: the operator still
        # wants to know the story exists, and must not be told it is corroborated.
        insight_type = "SINGLE_ORIGIN_REPORTS"
        emoji = "⚪"
        recommendation = (
            f"{country.title()}: multiple reports trace to {_origins or 1} independent "
            f"source. This is reporting volume, NOT corroborated change — treat as "
            f"a lead to verify, not as evidence. Seek a second independent source "
            f"before acting."
        )
    elif "sanctions_change" in signal_types and ("conflict_escalation" in signal_types or has_tender):
        insight_type = "RISK_CONVERGENCE"
        emoji = "⚠️"
        recommendation = (
            f"Compliance + instability signals in {country.title()}. "
            f"Review export control implications before advancing any deals."
        )
    else:
        # R-F2008 — this cluster ALREADY passed the score >= MIN_CORRELATION_SCORE
        # and >= 2-signal-type gate in correlate_signals(). The old `return None`
        # here silently DROPPED genuine opportunities that didn't match a specific
        # multi-dimensional pattern — e.g. a budget increase + active tender with
        # no warm contact yet (a textbook live window). The end-to-end chain test
        # caught exactly this: news -> ledger -> correlate produced 0 insights for
        # Angola despite budget+tender scoring 7.0. Never drop a gate-passing
        # cluster; the missing relationship is the action item, not a reason to hide.
        if has_budget and has_tender:
            insight_type, emoji = "OPPORTUNITY_WINDOW", "🟢"
            recommendation = (
                f"{country.title()} shows budget momentum + active procurement "
                f"({len(signals)} signals) but no warm contact detected yet — "
                f"qualify the buyer and open a channel NOW; this is a live window."
            )
        else:
            insight_type, emoji = "MARKET_SIGNAL", "🟠"
            recommendation = (
                f"{country.title()}: {len(signals)} correlated signals across "
                f"{len(signal_types)} types (score {round(score, 1)}) — review for opportunity."
            )

    return {
        "country": country.title(),
        "score": round(score, 1),
        "insight_type": insight_type,
        "emoji": emoji,
        "recommendation": recommendation,
        "signal_count": len(signals),
        # R-F3487 — the independence evidence, on EVERY insight. signal_count is
        # how many reports; independent_origins is how many witnesses. Publishing
        # only the first is what let volume masquerade as corroboration.
        "independent_origins": _origins,
        "independently_corroborated": _origins >= _MIN_INDEPENDENT_ORIGINS,
        # R-F3521 — present on EVERY insight so no consumer has to test for the
        # key's existence. UNKNOWN until _annotate_trajectories measures it, which
        # is honest for a direct caller of _generate_insight (several tests): it
        # has no historical band to reason from, and absent must not read as flat.
        "trajectory": TRAJECTORY_UNKNOWN,
        "trajectory_basis": "not yet measured",
        "signal_types": sorted(signal_types),
        "signals": [{"type": s["type"], "text": s["text"][:150], "source": s["source"]} for s in signals[:10]],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Format for injection into chat context ────────────────────────────────────

def _trajectory_suffix(insight: dict) -> str:
    """R-F3523 — the CARRIER for the trajectory on text surfaces.

    Added because R-F3521 computed a trajectory that no consumer
    could see: correlate_signals emitted the fields, and every formatter — the
    chat context, the daily briefing, and the /opportunities route's _shape() —
    dropped them on the way out. Live-verified as unreachable before this existed.
    A value the engine computes but nothing can read is not a shipped capability;
    it is a producer with no carrier, and the surfaces are where that gets caught.

    UNKNOWN renders as nothing rather than as the word "UNKNOWN". Printing it on
    every insight that lacks 90 days of history would be noise, and would read as
    a finding rather than as the absence of one.
    """
    t = str(insight.get("trajectory") or "")
    if not t or t == TRAJECTORY_UNKNOWN:
        return ""
    return f", {t.lower()}"


async def get_correlation_context(query: str, max_insights: int = 3) -> str:
    """Generate correlation context for a chat query.

    Called by aria_engine before the LLM call. Returns formatted
    correlation insights relevant to the query's country/entity.
    """
    insights = await correlate_signals()
    if not insights:
        return ""

    # Filter to relevant country if mentioned in query
    query_lower = query.lower()
    relevant = [i for i in insights if i["country"].lower() in query_lower]
    if not relevant:
        # Fall back to top insights
        relevant = insights[:max_insights]
    else:
        relevant = relevant[:max_insights]

    if not relevant:
        return ""

    lines = ["CORRELATED INTELLIGENCE — signals connected across sources:"]
    for insight in relevant:
        lines.append(
            f"  {insight['emoji']} [{insight['insight_type']}] {insight['country']} "
            f"(score {insight['score']}, {insight['signal_count']} signals"
            f"{_trajectory_suffix(insight)}): "
            f"{insight['recommendation']}"
        )
    return "\n".join(lines)


# ── Format for daily briefing ─────────────────────────────────────────────────

async def assess_coverage_confidence(country: str) -> dict:
    """Assess how much intelligence ARIA has on a given country.

    Returns a confidence score (0-1) and a breakdown showing:
    - RAG document count
    - Ledger signal count
    - Knowledge fact count
    - Contact count
    - Source freshness
    - Coverage verdict: DEEP / ADEQUATE / THIN / ZERO

    This is injected into chat responses so ARIA never presents thin
    coverage as deep intelligence without warning.
    """
    from . import rag_store, intel_ledger, knowledge, contact_intelligence

    country_lower = country.lower().strip()
    score = 0.0
    breakdown = {}

    # 1. RAG documents mentioning this country
    try:
        results = await rag_store.search(country, top_k=20)
        doc_count = len(results)
        breakdown["rag_documents"] = doc_count
        score += min(doc_count / 20, 1.0) * 0.3  # max 0.3 from RAG
    except Exception:
        breakdown["rag_documents"] = 0

    # 2. Ledger signals for this country
    try:
        ledger = await intel_ledger._load()
        signals = [s for s in ledger.get("signals", [])
                   if country_lower in " ".join(s.get("countries", [])).lower()]
        breakdown["ledger_signals"] = len(signals)
        score += min(len(signals) / 30, 1.0) * 0.25  # max 0.25 from ledger
    except Exception:
        breakdown["ledger_signals"] = 0

    # 3. Knowledge facts
    try:
        # R-F4141 (C-171) — TWO defects at this one line.
        #
        # 1. It ran the 2.28s O(corpus) scan ON the event loop. Measured
        #    live by R-F4137's instrument: signal_correlator was the sole
        #    on-loop caller after C-170 was fixed, 11 calls / 13.99s, all
        #    on-loop, max 3.42s.
        #
        # 2. `search_knowledge` returns a formatted STRING, never a list
        #    (its own docstring says so, and routes/aria.py:10120 carries a
        #    2026-04-21 comment about exactly this confusion). So
        #    `len(facts) if isinstance(facts, list) else 0` was
        #    ALWAYS 0: the knowledge component of coverage confidence has
        #    never contributed its 0.2, silently capping the score at 0.8,
        #    and `breakdown["knowledge_facts"]` always reported 0. Same
        #    shape as C-169, where a wrong assumption about this module's
        #    API capped resolver confidence at 0.5.
        #
        # `search_fact_records` is the list-returning entry point, so the
        # count is now real rather than structurally zero.
        facts = await asyncio.to_thread(
            knowledge.search_fact_records, country, 20)
        fact_count = len(facts) if isinstance(facts, list) else 0
        breakdown["knowledge_facts"] = fact_count
        score += min(fact_count / 10, 1.0) * 0.2  # max 0.2 from KB
    except Exception:
        breakdown["knowledge_facts"] = 0

    # 4. Contacts in country
    try:
        contacts = await contact_intelligence.get_contacts(country=country)
        breakdown["contacts"] = len(contacts)
        score += min(len(contacts) / 5, 1.0) * 0.15  # max 0.15 from contacts
    except Exception:
        breakdown["contacts"] = 0

    # 5. Pipeline leads
    try:
        from . import deal_pipeline
        leads = await deal_pipeline.get_pipeline(country=country)
        breakdown["pipeline_leads"] = len(leads)
        score += min(len(leads) / 3, 1.0) * 0.1  # max 0.1 from pipeline
    except Exception:
        breakdown["pipeline_leads"] = 0

    # Determine verdict
    if score >= 0.7:
        verdict = "DEEP"
    elif score >= 0.4:
        verdict = "ADEQUATE"
    elif score >= 0.15:
        verdict = "THIN"
    else:
        verdict = "ZERO"

    return {
        "country": country,
        "score": round(score, 2),
        "verdict": verdict,
        "breakdown": breakdown,
        "warning": (
            f"⚠️ THIN COVERAGE: ARIA has limited intelligence on {country}. "
            f"Data quality may be insufficient for client-facing use. "
            f"Recommend fresh research before briefing."
        ) if verdict in ("THIN", "ZERO") else "",
    }


async def generate_correlation_briefing() -> str:
    """Generate correlation section for the daily team briefing."""
    insights = await correlate_signals()
    if not insights:
        return ""

    lines = [
        "",
        "*🔗 CORRELATED INTELLIGENCE*",
        f"_{len(insights)} compound insight(s) detected:_",
    ]
    for i in insights[:5]:
        lines.append(f"{i['emoji']} *{i['country']}* — {i['insight_type']} "
                     f"(score {i['score']}{_trajectory_suffix(i)})")
        lines.append(f"  {i['recommendation'][:200]}")
        lines.append("")


    # R-F996 — wire to brain
    wire_success(
        module="signal_correlator",
        summary="Signal correlation",
        source_id="signal_correlator:R-F996",
    )
    return "\n".join(lines)
