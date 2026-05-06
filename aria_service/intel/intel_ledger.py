"""
Intelligence Ledger — permanent signal store.
Ported from lib/aria/intel_ledger.mjs.

Retention: previously 30-day rolling, now permanent (100-year retention
sentinel + 1M-signal cap). Operator explicitly asked for forever memory.

Persistence layout (F110, 2026-04-30 — mirrors F94 knowledge):
  primary  : disk JSON at /data/aria_signals.json (atomic write)
  hydrate  : disk → legacy Redis blob → empty
  snapshot : periodic copy to Redis for off-host backup (every
             SNAPSHOT_INTERVAL_S, only if dirty since last snapshot)

Why this shape:
  Pre-F110, every add_signal/ingest_sweep_signals call did a full
  rs.set_json(KEY, _cache) — at 4043 signals × ~600 bytes that's a
  ~2.5 MB Redis SET per write. Sweep cycles can ingest 50+ signals
  in a burst, and Upstash silently truncates values > 1 MB, which is
  what cost us 2587 signals on 2026-04-29 (memo: F87/F88). Mirroring
  F94's pattern: disk is canonical, Redis is a 10-min off-host snapshot.
  Public API (add_signal / ingest_sweep_signals / query_ledger / get_*
  AND the de-facto-public _load() that 5 modules read) is unchanged.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.ledger")

KEY = "crucix:intel_ledger"
MAX_SIGNALS = 1_000_000
RETENTION_DAYS = 36500  # 100 years — effectively permanent

# R-F36 (2026-05-06): mirrors F111's knowledge-snapshot fix. The 10-min
# Redis snapshot of the ledger crossed Upstash's 4 MB warn threshold at
# ~13.7k signals (4.51 MB raw) and would hit the tier cap in days. Signal
# JSON compresses ~5-8× with gzip, so a base64+gzip wrapper buys multi-month
# headroom. Magic prefix lets the loader distinguish gzipped values from
# legacy raw-JSON blobs written before this fix.
_GZ_PREFIX = "GZ1:"


def _encode_snapshot(obj: dict) -> str:
    raw = json.dumps(obj, default=str).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    return _GZ_PREFIX + base64.b64encode(gz).decode("ascii")


def _decode_snapshot(value: Any) -> dict | None:
    """Decode a Redis ledger snapshot. Returns None if empty/unparseable.
    Accepts both new gzipped payloads (prefix `GZ1:`) and legacy raw-JSON
    blobs so existing snapshots migrate forward on the next read. Tolerates
    str, bytes, and the dict that legacy `rs.get_json` test fixtures hand
    back so swapping the call site doesn't break them."""
    if not value:
        return None
    if isinstance(value, dict):
        return value if "signals" in value else None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    if value.startswith(_GZ_PREFIX):
        try:
            gz = base64.b64decode(value[len(_GZ_PREFIX):])
            raw = gzip.decompress(gz)
            data = json.loads(raw)
            if isinstance(data, dict) and "signals" in data:
                return data
        except Exception as e:
            logger.warning("intel_ledger: gzip snapshot decode failed: %s", e)
        return None
    try:
        data = json.loads(value)
        if isinstance(data, dict) and "signals" in data:
            return data
    except Exception:
        pass
    return None

_cache: dict | None = None

# Debounced-flush state. Writes mark _dirty; a single background task
# flushes to disk after FLUSH_DEBOUNCE_S. Sweep ingest of 50+ signals in a
# burst coalesces into one disk write instead of N Redis SETs.
_dirty: bool = False
_dirty_since_snapshot: bool = False
_flush_task: asyncio.Task | None = None
_flusher_started: bool = False
_flusher_stop = False
FLUSH_DEBOUNCE_S = 2.0
SNAPSHOT_INTERVAL_S = 600.0  # 10 min — Redis off-host backup cadence


def _resolve_disk_path() -> str:
    """Match knowledge.py / rag_store.py resolution rules so the same volume
    is used. Override with ARIA_LEDGER_PATH for tests / dev shells. Falls
    back to the OS temp dir on hosts without /data (Windows dev, CI)."""
    override = os.getenv("ARIA_LEDGER_PATH", "").strip()
    if override:
        return override
    if Path("/data").exists() and os.access("/data", os.W_OK):
        return "/data/aria_signals.json"
    fallback = os.path.join(tempfile.gettempdir(), "aria_signals.json")
    logger.warning(
        "intel_ledger: /data volume not mounted — falling back to %s. "
        "State will NOT persist across restarts. Mount a fly.io volume "
        "at /data to enable persistence.",
        fallback,
    )
    return fallback


_DISK_PATH = _resolve_disk_path()


def _read_from_disk() -> dict | None:
    try:
        with open(_DISK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "signals" in data:
            return data
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("intel_ledger: disk load failed at %s: %s", _DISK_PATH, e)
    return None


def _write_to_disk_atomic(data: dict) -> None:
    """Atomic write via temp file + rename so a crash mid-write can't
    corrupt the canonical signals file."""
    target = _DISK_PATH
    target_dir = os.path.dirname(target) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".aria_signals.", suffix=".json.tmp", dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def _flush_to_disk() -> None:
    """Serialize the cache and write to disk in a thread executor (json.dump
    is sync C; doing it on the event loop blocks every other coroutine for
    the duration of the dump)."""
    global _dirty, _dirty_since_snapshot
    if not _cache or not _dirty:
        return
    snapshot = _cache  # write-by-reference is safe — we don't mutate
    try:
        await asyncio.to_thread(_write_to_disk_atomic, snapshot)
        _dirty = False
        _dirty_since_snapshot = True
        # F87 observability (preserved from pre-F110 _save): log signal
        # count at flush time on 250-signal increments so the operator can
        # spot trajectory drops between two boots from the disk-flush log.
        try:
            n = len(_cache.get("signals", []) or [])
            if n and n % 250 == 0:
                logger.info("intel_ledger flush checkpoint: %d signals", n)
        except Exception:
            pass
    except Exception as e:
        logger.error("intel_ledger: disk flush failed: %s", e)


async def _flush_loop() -> None:
    """Background coroutine: every FLUSH_DEBOUNCE_S, flush dirty cache to
    disk; every SNAPSHOT_INTERVAL_S, also push a Redis snapshot."""
    global _dirty_since_snapshot
    last_snapshot = time.monotonic()
    while not _flusher_stop:
        try:
            await asyncio.sleep(FLUSH_DEBOUNCE_S)
            await _flush_to_disk()
            now = time.monotonic()
            if (
                _dirty_since_snapshot
                and (now - last_snapshot) >= SNAPSHOT_INTERVAL_S
                and _cache
            ):
                try:
                    payload = _encode_snapshot(_cache)
                    await rs.set(KEY, payload)
                    _dirty_since_snapshot = False
                    last_snapshot = now
                    logger.info(
                        "intel_ledger: Redis snapshot written (%d signals, %d bytes gzip)",
                        len(_cache.get("signals", [])), len(payload),
                    )
                except Exception as e:
                    logger.warning("intel_ledger: Redis snapshot failed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("intel_ledger: flush loop error: %s", e)


def _ensure_flusher() -> None:
    """Start the debounced flusher if a running loop exists. No-op in sync
    test contexts (no loop) — those should call flush() explicitly if they
    need persistence."""
    global _flush_task, _flusher_started
    if _flusher_started:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _flush_task = loop.create_task(_flush_loop())
    _flusher_started = True


# ── Entity extraction lists ──────────────────────────────────────────────────

COUNTRIES = [
    "Angola", "Mozambique", "Guinea-Bissau", "Cape Verde", "São Tomé", "Nigeria",
    "Kenya", "Ghana", "Senegal", "Ivory Coast", "Cameroon", "Ethiopia", "Rwanda",
    "Uganda", "Tanzania", "Morocco", "Algeria", "Egypt", "Tunisia", "Libya",
    "South Africa", "Namibia", "Botswana", "Zimbabwe", "Zambia", "DRC", "Congo",
    "Mali", "Burkina Faso", "Niger", "Chad", "Sudan", "South Sudan", "Somalia",
    "Djibouti", "Eritrea", "Madagascar", "Indonesia", "Philippines", "Vietnam",
    "Thailand", "Myanmar", "Malaysia", "Singapore", "India", "Pakistan",
    "Bangladesh", "Sri Lanka", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Oman",
    "Bahrain", "Iraq", "Jordan", "Lebanon", "Turkey", "Israel", "Iran",
    "Poland", "Romania", "Ukraine", "Brazil", "Colombia", "Mexico", "Peru", "Chile",
]

PRODUCTS = {
    "ammunition": ["ammunition", "ammo", "round", "mortar", "shell"],
    "vehicles": ["vehicle", "armoured", "apc", "ifv", "mrap", "tank"],
    "aircraft": ["aircraft", "fighter", "helicopter", "drone", "uav"],
    "naval": ["vessel", "frigate", "corvette", "submarine", "destroyer"],
    "missiles": ["missile", "rocket", "sam", "patriot", "javelin"],
    "radar": ["radar", "air defense", "shorad", "ewi"],
    "small_arms": ["rifle", "pistol", "machine gun", "carbine"],
    "surveillance": ["surveillance", "isr", "reconnaissance", "sigint"],
    "training": ["training", "exercise", "drill", "simulation"],
}

OEMS = [
    "Lockheed", "Boeing", "Raytheon", "BAE Systems", "Leonardo", "Rheinmetall",
    "Thales", "Turkish Aerospace", "Baykar", "Elbit", "Rafael", "IAI",
    "Paramount", "Denel", "Norinco", "AVIC", "Poly Technologies", "Embraer",
    "Otokar", "FNSS", "Aselsan", "Hanwha", "Hyundai Rotem", "KAI",
    "Damen", "Navantia", "Fincantieri", "MBDA", "Saab", "Kongsberg",
    "General Dynamics", "Northrop", "L3Harris",
]


def _extract_entities(text: str) -> dict:
    tl = text.lower()
    countries = [c for c in COUNTRIES if c.lower() in tl]
    products = [cat for cat, kws in PRODUCTS.items() if any(k in tl for k in kws)]
    oems = [o for o in OEMS if o.lower() in tl]
    return {"countries": countries, "products": products, "oems": oems}


async def _load() -> dict:
    """Hydrate the cache once. Order: disk → legacy Redis blob (one-shot
    migration) → empty default. Subsequent calls hit the in-memory cache
    without I/O. NOTE: 5 sibling modules (chain_correlator, competitor_tracker,
    narrative_monitor, signal_correlator, plus tests) consume this directly
    via `await intel_ledger._load()` — the leading underscore is advisory.
    Return shape `{"signals": [...], "version": int}` MUST be preserved."""
    global _cache
    if _cache is not None:
        return _cache

    # 1. Prefer disk — canonical store post-F110.
    data = _read_from_disk()
    if data:
        _cache = data
        logger.info(
            "intel_ledger: loaded %d signals from disk (%s)",
            len(_cache.get("signals", [])), _DISK_PATH,
        )
        _prune()
        _ensure_flusher()
        return _cache

    # 2. Disk empty — try the Redis snapshot (gzip post-R-F36, or legacy
    #    raw-JSON pre-R-F36) and migrate it forward to disk.
    raw = await rs.get(KEY)
    legacy = _decode_snapshot(raw)
    if legacy and isinstance(legacy, dict) and "signals" in legacy:
        _cache = legacy
        logger.warning(
            "intel_ledger: hydrated from legacy Redis blob (%d signals) — "
            "migrating to disk %s",
            len(_cache.get("signals", [])), _DISK_PATH,
        )
        try:
            await asyncio.to_thread(_write_to_disk_atomic, _cache)
            logger.info("intel_ledger: legacy Redis → disk migration complete")
        except Exception as e:
            logger.error("intel_ledger: legacy migration to disk failed: %s", e)
        _prune()
        _ensure_flusher()
        return _cache

    # 3. Cold start with no prior state.
    _cache = {"signals": [], "version": 1}
    _prune()
    _ensure_flusher()
    return _cache


def _prune() -> None:
    if not _cache:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    _cache["signals"] = [s for s in _cache["signals"] if s.get("ts", "") >= cutoff]
    if len(_cache["signals"]) > MAX_SIGNALS:
        _cache["signals"] = _cache["signals"][:MAX_SIGNALS]


async def _save() -> None:
    """Mark the cache dirty. Actual disk I/O is debounced through
    _flush_loop so sweep bursts (50+ signals/cycle) coalesce into a single
    write. Pre-F110 this did rs.set_json on every call — see module docstring."""
    global _dirty
    if not _cache:
        return
    _dirty = True
    _ensure_flusher()


async def flush() -> None:
    """Force an immediate disk flush. Call from shutdown hooks or tests
    that need to assert on-disk state without waiting for the debounce."""
    await _flush_to_disk()


async def shutdown() -> None:
    """Stop the background flusher and write any pending changes. Wired
    into main.py lifespan teardown next to knowledge.shutdown()."""
    global _flusher_stop, _flush_task
    _flusher_stop = True
    if _flush_task:
        _flush_task.cancel()
        try:
            await _flush_task
        except (asyncio.CancelledError, Exception):
            pass
        _flush_task = None
    await _flush_to_disk()


# ── Public API ───────────────────────────────────────────────────────────────

async def init() -> None:
    await _load()
    logger.info(f"Intel ledger loaded: {len((_cache or {}).get('signals', []))} signals")


async def add_signal(payload: dict) -> str:
    """Add a single signal to the ledger.

    Accepts a dict with at least 'summary' or 'title'. Used by the
    autonomous delivery pipeline to push brain_lead signals from
    completed tasks into the permanent ledger.
    """
    db = await _load()
    text = payload.get("summary") or payload.get("title") or ""
    if not text:
        return "skipped:empty"
    source = payload.get("source", "autonomous")
    if _is_propaganda_source(source):
        return "skipped:propaganda"
    ent = _extract_entities(text)
    now = datetime.now(timezone.utc).isoformat()
    # Clause 17 — attach source tier to every signal carrying a URL so the
    # ledger itself becomes provenance-aware. Tier classification is pure
    # (no network, no redis), safe to run inline.
    url = payload.get("url", "")
    source_tier = ""
    source_score = 0.0
    if url:
        try:
            from . import verified_intel as _vi
            _tier = _vi.SourceTierClassifier().classify(url)
            source_tier = _tier.value
            source_score = _vi.TIER_SCORES[_tier]
        except Exception:
            pass
    db["signals"].insert(0, {
        "text": text[:500],
        "source": source,
        "type": payload.get("type", "brain_lead"),
        "url": url,
        "countries": ent["countries"],
        "products": ent["products"],
        "oems": ent["oems"],
        "severity": payload.get("severity", "medium"),
        "ts": payload.get("timestamp") or now,
        "tags": payload.get("tags", []),
        "source_tier": source_tier,
        "source_tier_score": source_score,
    })
    _prune()
    await _save()

    # Signal brain about the new intel
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="conflict_tracker" if payload.get("type") == "osint" else "deep_researcher",
            summary=f"Ledger signal: {text[:200]}",
            success=True,
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return "ok"


async def purge_signals_by_keyword(keywords: list[str], dry_run: bool = False) -> dict:
    """Remove signals from the ledger whose text contains ANY of the given
    keywords (case-insensitive). Designed for surgical cleanup of polluted
    signals — e.g. fabricated current-event claims that bled into multiple
    chat replies.

    Returns a summary dict with the number matched, removed, and a sample
    of the matched texts so callers can verify before committing.

    When dry_run=True, returns the same shape but does not actually delete.
    """
    db = await _load()
    if not keywords:
        return {"matched": 0, "removed": 0, "sample": [], "dry_run": dry_run}
    needles = [k.lower() for k in keywords if k]
    matched_signals = []
    surviving = []
    for s in db.get("signals", []):
        text = (s.get("text", "") or "").lower()
        source = (s.get("source", "") or "").lower()
        if any(n in text or n in source for n in needles):
            matched_signals.append(s)
        else:
            surviving.append(s)

    sample = [
        {"text": s.get("text", "")[:200], "source": s.get("source", ""), "ts": s.get("ts", "")}
        for s in matched_signals[:10]
    ]

    if not dry_run and matched_signals:
        db["signals"] = surviving
        await _save()
        logger.warning(
            "Ledger purge: removed %d signal(s) matching keywords=%s",
            len(matched_signals), keywords,
        )

    return {
        "matched": len(matched_signals),
        "removed": 0 if dry_run else len(matched_signals),
        "remaining": len(surviving) if not dry_run else len(db.get("signals", [])),
        "sample": sample,
        "dry_run": dry_run,
        "keywords": keywords,
    }


# Propaganda-tier sources — these channels are monitored for OSINT
# situational awareness via the sweep cycle but their content is NOT
# trustworthy enough to enter the chat-injection layer. Keeping them out
# of the intel ledger entirely is the cleanest defence: every downstream
# code path (query_ledger, _build_intel_context, the LLM prompt) becomes
# safe by construction. The list mirrors apis/sources/telegram.mjs
# DEFAULT_CHANNELS — when new biased channels are added there, this set
# must be updated in lockstep.
#
# Past incident 2026-04-09: a single intelslava "Lebanon airstrikes 112
# killed" post propagated into the Vision International ammunition RFQ
# analysis, the Modirum Gespi investigation, AND the Ghana defence
# minister query, with [CONFIRMED] tags. Even after constitution clause
# 13 + the relevance filter + a manual purge, the sweep cycle kept
# re-ingesting fresh propaganda content every cycle and the bleed
# returned. The only structural fix is to block these sources at the
# ledger boundary.
_PROPAGANDA_SOURCES = {
    # Russian state / Russian-aligned
    "intelslava", "mod_russia", "rvvoenkor", "readovkanews", "readovka",
    # Conflict Intelligence Team is sometimes Russian-aligned content
    "cig_telegram",
    # Ukrainian state / Ukrainian-aligned (also single-perspective)
    "deepstateua", "operativnozsu", "generalstaffzsu", "legitimniy",
    "ukraine frontline",
    # Generic single-channel buckets that proved high-noise in 2026-04-09
    "telegram", "tg",
}


def _is_propaganda_source(source: str) -> bool:
    """Return True if this source identifier matches the propaganda set.
    Case-insensitive substring match — catches both 'intelslava' as a
    full source string and 'telegram:intelslava' as a prefixed one."""
    if not source:
        return False
    s = source.lower().strip()
    if s in _PROPAGANDA_SOURCES:
        return True
    return any(p in s for p in _PROPAGANDA_SOURCES if len(p) > 3)


async def ingest_sweep_signals(current_data: dict) -> int:
    """Parse sweep data, extract entities, dedup, store. Returns count added.

    Propaganda-tier sources (intelslava, mod_russia, CIG_telegram, etc.)
    are SKIPPED at ingest time — they never enter the ledger and therefore
    can never be auto-injected into chat replies. This is the structural
    fix for the 2026-04-09 Lebanon contamination incident; clause 13 and
    the relevance filter handle the same content if it slips through via
    other paths, but the ledger boundary is the cleanest place to block.
    """
    db = await _load()
    existing = {s.get("text", "")[:150].lower() for s in db["signals"]}
    added = 0
    skipped_propaganda = 0
    now = datetime.now(timezone.utc).isoformat()

    def _add(text: str, source: str, sig_type: str, url: str = "", severity: str = "medium"):
        nonlocal added, skipped_propaganda
        if not text:
            return
        if _is_propaganda_source(source):
            skipped_propaganda += 1
            return
        if text[:150].lower() in existing:
            return
        ent = _extract_entities(text)
        db["signals"].insert(0, {
            "text": text[:500], "source": source, "type": sig_type, "url": url,
            "countries": ent["countries"], "products": ent["products"], "oems": ent["oems"],
            "severity": severity, "ts": now,
        })
        existing.add(text[:150].lower())
        added += 1

    # OSINT urgent
    for s in (current_data.get("tg", {}).get("urgent") or []):
        _add(s.get("text", ""), s.get("channel", "OSINT"), "osint")

    # Correlations
    for c in (current_data.get("correlations") or []):
        for sig in (c.get("topSignals") or [])[:2]:
            _add(sig.get("text", ""), c.get("region", ""), "correlation", severity=c.get("severity", "medium"))

    # Defence news (may be list of dicts or list of strings)
    for d in (current_data.get("defenseNews") or []):
        if isinstance(d, str):
            _add(d, "defence_news", "defense_news")
        elif isinstance(d, dict):
            _add(d.get("title", ""), d.get("source", "defence_news"), "defense_news", d.get("link", ""))

    # Tenders
    items = (current_data.get("procurementTenders") or {}).get("items") or []
    for t in items:
        if isinstance(t, str):
            _add(t, "tender", "tender")
        elif isinstance(t, dict):
            _add(t.get("title") or t.get("text", ""), t.get("source", "tender"), "tender", t.get("link", ""))

    # BD brain leads
    brain = (current_data.get("bdIntelligence") or {}).get("brain") or {}
    for l in (brain.get("salesLeads") or []):
        _add(f"{l.get('market','')}: {l.get('lead','')}", "brain", "brain_lead")

    _prune()
    await _save()
    if skipped_propaganda > 0:
        logger.info(
            "Ledger ingested %d new signals (%d propaganda-tier signals skipped at boundary)",
            added, skipped_propaganda,
        )
    else:
        logger.info("Ledger ingested %d new signals", added)
    return added


def _recency_multiplier(ts_iso: str, now: datetime | None = None) -> float:
    """Score multiplier based on signal age — fresh signals dominate stale ones.

    today / <2d  → 2.5x
    2-7 days     → 1.5x
    7-14 days    → 1.0x   (baseline)
    14-21 days   → 0.7x
    >21 days     → 0.4x

    Without this weighting, a 28-day-old correlation outranks today's tender simply
    because it has more keyword matches — disastrous for procurement timing.
    """
    if not ts_iso:
        return 0.4
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.4
    now = now or datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400
    if age_days < 2: return 2.5
    if age_days < 7: return 1.5
    if age_days < 14: return 1.0
    if age_days < 21: return 0.7
    return 0.4


def query_ledger(query: str) -> str:
    """Time-weighted, entity-aware search for prompt injection.

    Returns a formatted string for LLM context. Recent signals score 2.5x
    higher than 3-week-old ones — restoring the "what's hot now" intuition
    that a senior analyst would apply.
    """
    if not _cache or not _cache["signals"]:
        return ""
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return ""

    now = datetime.now(timezone.utc)
    query_lower = query.lower()

    scored: list[tuple[float, dict]] = []
    for s in _cache["signals"]:
        score = 0.0
        for c in s.get("countries", []):
            if c.lower() in query_lower:
                score += 5
        for o in s.get("oems", []):
            if o.lower() in query_lower:
                score += 4
        for p in s.get("products", []):
            if p.lower() in query_lower:
                score += 4
        text = s.get("text", "").lower()
        for w in words:
            if w in text:
                score += 2

        # Severity boost — high-severity signals matter more even if older
        sev = (s.get("severity") or "medium").lower()
        if sev == "high":   score += 2
        elif sev == "low":  score -= 1

        if score > 0:
            # Apply temporal weighting AFTER content scoring
            score *= _recency_multiplier(s.get("ts", ""), now)
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:12]
    if not top:
        return ""

    lines = [f"\n[INTELLIGENCE LEDGER — recent signals ({len(_cache['signals'])} total, permanent, recency-weighted)]"]
    for score, s in top:
        age = ""
        if s.get("ts"):
            try:
                dt = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
                days = (now - dt).days
                hrs = (now - dt).total_seconds() / 3600
                if hrs < 24: age = f" ({int(hrs)}h ago)"
                else: age = f" ({days}d ago)"
            except Exception:
                pass
        lines.append(f"- [{s.get('type','?')}] {s.get('text','')[:180]}{age}")
    return "\n".join(lines)


async def get_country_situation(country: str) -> dict:
    db = await _load()
    signals = [s for s in db["signals"] if country.lower() in [c.lower() for c in s.get("countries", [])]]
    return {
        "country": country,
        "signalCount": len(signals),
        "recentSignals": signals[:20],
    }


async def get_stats() -> dict:
    db = await _load()
    by_type: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for s in db["signals"]:
        t = s.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        for c in s.get("countries", []):
            by_country[c] = by_country.get(c, 0) + 1
    return {
        "totalSignals": len(db["signals"]),
        "byType": by_type,
        "byCountry": dict(sorted(by_country.items(), key=lambda x: x[1], reverse=True)[:15]),
    }
