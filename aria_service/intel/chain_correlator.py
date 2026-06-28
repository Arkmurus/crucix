"""ARIA Chain Correlator — long-horizon causal chain.

signal_correlator.py already handles short-window (14-day) convergence —
"budget + tender + warm contact all landed this fortnight → opportunity".
That module is untouched and still loaded by the briefing and chat paths.

This sibling module handles the OTHER kind of correlation: the 12–18 month
causal chain from a structural geopolitical shift to a procurement window,
with a relationship activation lead-time back-propagated to today.

The thesis (Priority 1 in the 2026-04-17 session notes):

    A geopolitical shift at T (coup, new minister, sanctions change, budget
    announcement, alliance shift, major election) predicts a procurement
    window at T+12..18mo. To be positioned to win inside that window, we
    need to start activating the relationship at roughly T+9mo — which,
    relative to today, is often already late.

Within-region scope confirmed with Antonio: we chain events inside each
region, applied with parity across every region per aria_global_positioning.md.
Inter-region spillover (e.g., Russia-Ukraine → European rearmament) is
deferred to Priority 1b.

Output fans out through the seams already wired for everything else:
  - brain_hook.absorb(module="chain_correlator", ...) for learning
  - predictor.forecast() registers a "procurement_window" prediction so
    the hit rate is scored over time
  - generate_chain_briefing() is injected into the 05:45 UTC daily briefing
  - get_chain_context() decorates chat replies with a [CHAIN: ...] marker
  - relationship_activation_nudges() is consumed by contact_intelligence

Storage:
  crucix:aria:chain_correlator:shifts         rolling 365d, max 500
  crucix:aria:chain_correlator:windows        rolling (keyed by shift)
  crucix:aria:chain_correlator:confirmed      append-only, max 1000
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import hashlib
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import redis_store as rs
from . import country_taxonomy as ct

logger = logging.getLogger("aria.chain_correlator")

# ── Tunables ──────────────────────────────────────────────────────────────

SHIFT_LOOKBACK_DAYS = 90          # fresh shifts to pull from ledger
SHIFT_RETENTION_DAYS = 36500      # 100 years — permanent; was 365d before 2026-04-21
WINDOW_OPEN_MONTHS = 12           # procurement window opens 12mo after shift
WINDOW_CLOSE_MONTHS = 18          # procurement window closes 18mo after shift
ACTIVATION_LEAD_MONTHS = 9        # start engaging at shift + 9mo
CLOSE_LOOKBACK_MONTHS = 24        # when closing chains, how far back to look
MAX_SHIFTS = 100_000
MAX_CONFIRMED = 100_000
MIN_SEVERITY = 0.35               # below this we don't even store the shift
ACTIVATION_NUDGE_WINDOW_DAYS = 30 # surface nudges within ±30d of due date

SHIFTS_KEY = "crucix:aria:chain_correlator:shifts"
WINDOWS_KEY = "crucix:aria:chain_correlator:windows"
CONFIRMED_KEY = "crucix:aria:chain_correlator:confirmed"
# R-F343: dedupe set of window IDs already registered with predictor/ground-truth.
FORECAST_REGISTERED_KEY = "crucix:aria:chain_correlator:forecast_registered"

SHIFT_TYPES = (
    "regime_change",
    "conflict_escalation",
    "sanctions_change",
    "budget_announcement",
    "alliance_shift",
    "major_election",
)

# Keyword dictionaries used to classify ledger signals into structural
# shifts. Kept deliberately narrow — a shift is a durable state change,
# not a news item. The signal_correlator already handles news-tempo.
_SHIFT_KEYWORDS: dict[str, list[str]] = {
    "regime_change": [
        "coup", "regime change", "overthrown", "overthrew", "junta",
        "transition government", "seized power", "power transfer",
        "provisional government", "toppled",
    ],
    "conflict_escalation": [
        "invasion", "invaded", "war declared", "major offensive",
        "airstrike", "air strike", "full-scale war", "ceasefire collapse",
        "escalation", "hostilities", "open warfare",
    ],
    "sanctions_change": [
        "sanctions imposed", "sanctions lifted", "embargo",
        "removed from sanctions", "added to ofac", "ofac designation",
        "eu sanctions", "un sanctions", "delisted", "designated sdn",
        "export ban",
    ],
    "budget_announcement": [
        "defence budget", "defense budget", "military budget",
        "defence spending increase", "defense spending increase",
        "rearmament", "military modernisation", "military modernization",
        "budget approved", "defence allocation", "defense allocation",
        "2% of gdp", "gdp defence target",
    ],
    "alliance_shift": [
        "joined nato", "nato accession", "nato membership",
        "defence pact", "defense pact", "strategic partnership",
        "mutual defence treaty", "mutual defense treaty",
        "military cooperation agreement", "status of forces",
        "sofa signed",
    ],
    "major_election": [
        "elected president", "sworn in as president", "inaugurated as president",
        "new defence minister", "new defense minister",
        "new minister of defence", "new minister of defense",
        "cabinet reshuffle", "new government formed",
        "presidential transition",
    ],
}

# Bonus severity for high-impact keywords — tiered above baseline.
_HIGH_IMPACT = {
    "war", "invasion", "coup", "nato", "ceasefire collapse", "ofac",
    "rearmament", "regime change",
}


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class GeoShift:
    id: str
    iso2: str
    region: Optional[str]
    shift_type: str
    severity: float
    summary: str
    source: str
    source_tier_score: float
    ts: str
    detected_at: str


@dataclass
class ProcurementWindow:
    id: str
    iso2: str
    region: Optional[str]
    trigger_shift_id: str
    trigger_shift_type: str
    shift_ts: str
    window_open: str
    window_close: str
    activation_due: str
    confidence: float
    status: str                 # PROJECTED | ACTIVE | CONFIRMED | EXPIRED
    generated_at: str


@dataclass
class ConfirmedChain:
    id: str
    iso2: str
    region: Optional[str]
    shift_id: str
    shift_ts: str
    proc_event_summary: str
    proc_event_source: str
    proc_event_ts: str
    lag_days: int
    within_window: bool
    activation_active: bool
    chain_score: float
    confirmed_at: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        t = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(t)
    except Exception:
        return None


def _add_months(dt: datetime, months: int) -> datetime:
    # Simple month math — ±1 day drift on edge cases is fine for a 12mo
    # window. Avoids a dateutil dependency.
    return dt + timedelta(days=months * 30)


def _classify(text: str) -> Optional[str]:
    tl = text.lower()
    for shift_type, kws in _SHIFT_KEYWORDS.items():
        if any(kw in tl for kw in kws):
            return shift_type
    return None


def _severity(text: str, source_tier_score: float) -> float:
    tl = text.lower()
    score = 0.4
    for kw in _HIGH_IMPACT:
        if kw in tl:
            score += 0.15
    score += min(source_tier_score, 1.0) * 0.3
    return round(min(score, 1.0), 3)


def _hash_id(*parts: str) -> str:
    h = hashlib.md5("|".join(p or "" for p in parts).encode()).hexdigest()[:12]
    return h


async def _load(key: str) -> dict:
    data = await rs.get_json(key)
    if isinstance(data, dict) and "items" in data:
        return data
    return {"items": [], "version": 1}


async def _save(key: str, data: dict) -> None:
    await rs.set_json(key, data)


# ── scan_geopolitical_shifts ──────────────────────────────────────────────

async def scan_geopolitical_shifts() -> list[dict]:
    """Scan recent intel_ledger signals for structural shifts.

    Reads the last SHIFT_LOOKBACK_DAYS of the ledger, classifies each
    signal into one of the SHIFT_TYPES, normalises country → ISO2, and
    dedups against the stored shifts. Returns the list of NEW shifts
    detected on this call.
    """
    from . import intel_ledger
    cutoff = (_now() - timedelta(days=SHIFT_LOOKBACK_DAYS)).isoformat()

    try:
        ledger = await intel_ledger._load()
    except Exception as e:
        logger.warning("ledger unavailable for shift scan: %s", e)
        return []

    stored = await _load(SHIFTS_KEY)
    stored_ids = {s["id"] for s in stored["items"]}
    new_shifts: list[GeoShift] = []

    for sig in ledger.get("signals", []):
        ts = sig.get("ts", "")
        if not ts or ts < cutoff:
            continue
        text = sig.get("text", "") or ""
        shift_type = _classify(text)
        if not shift_type:
            continue
        # Normalise each country mentioned and emit one shift per country —
        # a coup in one country and sanctions on another in the same signal
        # should not be conflated.
        for country in sig.get("countries", []) or []:
            iso2 = ct.to_iso2(country)
            if not iso2:
                continue
            sev = _severity(text, float(sig.get("source_tier_score", 0.0) or 0.0))
            if sev < MIN_SEVERITY:
                continue
            sid = _hash_id(shift_type, iso2, ts[:10], text[:80])
            if sid in stored_ids:
                continue
            shift = GeoShift(
                id=sid,
                iso2=iso2,
                region=ct.iso2_to_region(iso2),
                shift_type=shift_type,
                severity=sev,
                summary=text[:300],
                source=sig.get("source", ""),
                source_tier_score=float(sig.get("source_tier_score", 0.0) or 0.0),
                ts=ts,
                detected_at=_now().isoformat(),
            )
            new_shifts.append(shift)
            stored_ids.add(sid)

    if not new_shifts:
        logger.debug("[chain] scan_shifts: no new structural shifts")
        return []

    # Prune + persist
    stored["items"].extend(asdict(s) for s in new_shifts)
    retention_cutoff = (_now() - timedelta(days=SHIFT_RETENTION_DAYS)).isoformat()
    stored["items"] = [
        s for s in stored["items"] if s.get("ts", "") >= retention_cutoff
    ]
    if len(stored["items"]) > MAX_SHIFTS:
        # Keep the highest-severity entries
        stored["items"].sort(key=lambda s: s.get("severity", 0), reverse=True)
        stored["items"] = stored["items"][:MAX_SHIFTS]
    await _save(SHIFTS_KEY, stored)

    # Brain hook (fire-and-forget)
    try:
        from . import brain_hook
        regions = sorted({s.region or "?" for s in new_shifts})
        await brain_hook.absorb_silent(
            module="chain_correlator",
            summary=(
                f"Detected {len(new_shifts)} structural shift(s) across "
                f"{len(regions)} region(s): {', '.join(regions)[:120]}"
            ),
            success=True,
            extra_topics=["geopolitics"],
        )
    except Exception:
        pass

    logger.info("[chain] scan_shifts: %d new shift(s)", len(new_shifts))
    return [asdict(s) for s in new_shifts]


# ── project_windows ───────────────────────────────────────────────────────

async def project_windows() -> list[dict]:
    """For each live shift, emit / refresh a ProcurementWindow.

    A shift is "live" if its ts is within the last SHIFT_RETENTION_DAYS
    and the projected window_close hasn't passed. A window transitions:
      PROJECTED  — today < window_open
      ACTIVE     — window_open ≤ today ≤ window_close
      EXPIRED    — today > window_close
    """
    shifts = await _load(SHIFTS_KEY)
    if not shifts["items"]:
        return []

    now = _now()
    windows_out: list[ProcurementWindow] = []

    for s in shifts["items"]:
        shift_ts = _parse_ts(s.get("ts", ""))
        if not shift_ts:
            continue
        window_open = _add_months(shift_ts, WINDOW_OPEN_MONTHS)
        window_close = _add_months(shift_ts, WINDOW_CLOSE_MONTHS)
        activation_due = _add_months(shift_ts, ACTIVATION_LEAD_MONTHS)

        if now > window_close:
            status = "EXPIRED"
        elif now >= window_open:
            status = "ACTIVE"
        else:
            status = "PROJECTED"

        # Confidence: severity × source tier × shift-type prior
        type_priors = {
            "regime_change": 0.75,
            "alliance_shift": 0.80,
            "budget_announcement": 0.70,
            "sanctions_change": 0.55,
            "major_election": 0.50,
            "conflict_escalation": 0.65,
        }
        prior = type_priors.get(s.get("shift_type", ""), 0.5)
        confidence = round(
            min(float(s.get("severity", 0.5)) * prior * 1.1, 1.0), 3
        )

        wid = _hash_id("window", s["id"])
        win = ProcurementWindow(
            id=wid,
            iso2=s["iso2"],
            region=s.get("region"),
            trigger_shift_id=s["id"],
            trigger_shift_type=s.get("shift_type", ""),
            shift_ts=s["ts"],
            window_open=window_open.isoformat(),
            window_close=window_close.isoformat(),
            activation_due=activation_due.isoformat(),
            confidence=confidence,
            status=status,
            generated_at=now.isoformat(),
        )
        windows_out.append(win)

    # Persist the full snapshot (easier to reason about than delta updates)
    snapshot = {"items": [asdict(w) for w in windows_out], "version": 1}
    await _save(WINDOWS_KEY, snapshot)

    # R-F343: dedupe predictor + ground-truth registration so the every-5-min
    # projection doesn't re-spam ~80 brain_hook absorbs / GT records per tick
    # for windows we've already registered. Was ~23k redundant writes/day.
    registered = await _load(FORECAST_REGISTERED_KEY)
    already_registered: set[str] = set(registered.get("items", []))
    # Prune to currently-tracked windows so the set is bounded by MAX_SHIFTS
    # rather than growing forever (shifts that age out free up slots).
    current_ids = {w.id for w in windows_out}
    already_registered &= current_ids
    eligible = [w for w in windows_out
                if w.status != "EXPIRED" and w.id not in already_registered]
    skipped = sum(
        1 for w in windows_out
        if w.status != "EXPIRED" and w.id in already_registered
    )

    newly_registered: set[str] = set()

    # Register predictions with the predictor for later hit-rate scoring
    try:
        from . import predictor
        for w in eligible:
            try:
                await predictor.forecast(
                    task_type="procurement_window",
                    domain=w.iso2,
                    entity_type="country",
                    context={
                        "window_id": w.id,
                        "shift_type": w.trigger_shift_type,
                        "window_open": w.window_open,
                        "window_close": w.window_close,
                        "confidence": w.confidence,
                    },
                )
                newly_registered.add(w.id)
            except Exception:
                # predictor is advisory — never block projection on it
                pass
    except Exception as e:
        logger.debug("predictor unavailable: %s", e)

    # Ground-truth loop: register each projected window as a testable
    # prediction so the team can later record the outcome (did a real
    # tender actually land inside the projected window?). Fire-and-forget.
    try:
        from . import ground_truth_loop as _gt
        for w in eligible:
            try:
                await _gt.record_assessment_async(
                    assessment_type="TENDER_OPPORTUNITY",
                    subject=f"{w.iso2}: procurement window "
                            f"{w.window_open[:10]} to {w.window_close[:10]}",
                    aria_prediction=(
                        f"Geopolitical shift ({w.trigger_shift_type}) on "
                        f"{w.shift_ts[:10]} will open a procurement window "
                        f"{w.window_open[:10]}–{w.window_close[:10]} in {w.iso2}."
                    ),
                    aria_confidence=float(w.confidence),
                    context=f"chain_correlator window {w.id}; region {w.region}",
                    domain=w.iso2 or "general",
                )
                newly_registered.add(w.id)
            except Exception:
                pass
    except Exception as e:
        logger.debug("ground_truth_loop unavailable: %s", e)

    # Persist registrations so the next tick skips them.
    if newly_registered or already_registered != set(registered.get("items", [])):
        already_registered |= newly_registered
        await _save(
            FORECAST_REGISTERED_KEY,
            {"items": sorted(already_registered), "version": 1},
        )

    active = [w for w in windows_out if w.status == "ACTIVE"]
    logger.info(
        "[chain] project_windows: %d projected, %d active "
        "(R-F343 registered %d new, skipped %d already-known)",
        len(windows_out), len(active), len(newly_registered), skipped,
    )
    return [asdict(w) for w in windows_out]


# ── close_chains ──────────────────────────────────────────────────────────

async def close_chains() -> list[dict]:
    """Scan recent procurement events and try to close the chain back
    to a stored shift in the same country.

    Reads:
      - tender_monitor alerts (Redis list crucix:aria:tender_monitor:alerts)
      - deal_pipeline leads (recently created)
    For each proc event in ISO2 X, look back CLOSE_LOOKBACK_MONTHS for a
    shift in ISO2 X. If found, record a ConfirmedChain.
    """
    shifts = await _load(SHIFTS_KEY)
    if not shifts["items"]:
        return []

    now = _now()
    lookback_cutoff = now - timedelta(days=CLOSE_LOOKBACK_MONTHS * 30)
    # Index shifts by iso2
    shifts_by_iso2: dict[str, list[dict]] = {}
    for s in shifts["items"]:
        shifts_by_iso2.setdefault(s["iso2"], []).append(s)

    # Gather procurement events
    proc_events: list[dict] = []

    # Tender alerts
    try:
        raw = await rs.lrange("crucix:aria:tender_monitor:alerts", 0, 199)
        import json as _json
        for item in raw or []:
            try:
                t = _json.loads(item)
                proc_events.append({
                    "iso2": (t.get("country_iso2") or "").upper(),
                    "summary": t.get("title", ""),
                    "source": f"tender:{t.get('portal','?')}",
                    "ts": t.get("publication_date") or t.get("detected_at") or "",
                })
            except Exception:
                continue
    except Exception as e:
        logger.debug("tender alerts unavailable: %s", e)

    # Pipeline leads
    try:
        from . import deal_pipeline
        leads = await deal_pipeline.get_pipeline() or []
        for lead in leads:
            iso2 = ct.to_iso2(lead.get("country", "")) or ""
            proc_events.append({
                "iso2": iso2,
                "summary": f"[{lead.get('stage','?')}] {lead.get('requirement','')[:200]}",
                "source": f"pipeline:{lead.get('id','?')}",
                "ts": lead.get("created_at", ""),
            })
    except Exception as e:
        logger.debug("pipeline unavailable: %s", e)

    # Contacts active at activation_due — lookup table
    active_contacts_by_iso2: dict[str, bool] = {}
    try:
        from . import contact_intelligence
        contacts = await contact_intelligence.get_contacts() or []
        for c in contacts:
            iso2 = ct.to_iso2(c.get("country", "")) or ""
            if not iso2:
                continue
            status = (c.get("_status") or c.get("status") or "").upper()
            if status in ("ACTIVE", "COOLING"):
                active_contacts_by_iso2[iso2] = True
    except Exception as e:
        logger.debug("contacts unavailable: %s", e)

    # Match each proc event to the closest preceding shift in same iso2
    stored = await _load(CONFIRMED_KEY)
    existing_ids = {c["id"] for c in stored["items"]}
    new_chains: list[ConfirmedChain] = []

    for evt in proc_events:
        iso2 = evt.get("iso2", "")
        if not iso2 or iso2 not in shifts_by_iso2:
            continue
        evt_ts = _parse_ts(evt.get("ts", ""))
        if not evt_ts or evt_ts < lookback_cutoff:
            continue
        best: Optional[dict] = None
        best_lag: Optional[int] = None
        for s in shifts_by_iso2[iso2]:
            s_ts = _parse_ts(s.get("ts", ""))
            if not s_ts or s_ts > evt_ts:
                continue
            lag_days = (evt_ts - s_ts).days
            if lag_days > CLOSE_LOOKBACK_MONTHS * 30:
                continue
            if best is None or lag_days < best_lag:  # closest preceding shift
                best = s
                best_lag = lag_days
        if not best:
            continue

        within_window = (
            WINDOW_OPEN_MONTHS * 30 <= best_lag <= WINDOW_CLOSE_MONTHS * 30
        )
        activation_active = active_contacts_by_iso2.get(iso2, False)
        chain_score = round(
            (0.6 if within_window else 0.3)
            + (0.3 if activation_active else 0.0)
            + min(float(best.get("severity", 0.5)), 1.0) * 0.1,
            3,
        )

        cid = _hash_id("chain", best["id"], evt.get("source", ""), evt.get("ts", ""))
        if cid in existing_ids:
            continue
        chain = ConfirmedChain(
            id=cid,
            iso2=iso2,
            region=ct.iso2_to_region(iso2),
            shift_id=best["id"],
            shift_ts=best.get("ts", ""),
            proc_event_summary=evt.get("summary", "")[:300],
            proc_event_source=evt.get("source", ""),
            proc_event_ts=evt.get("ts", ""),
            lag_days=best_lag or 0,
            within_window=within_window,
            activation_active=activation_active,
            chain_score=chain_score,
            confirmed_at=now.isoformat(),
        )
        new_chains.append(chain)
        existing_ids.add(cid)

    if not new_chains:
        logger.debug("[chain] close_chains: no new chains closed")
        return []

    stored["items"].extend(asdict(c) for c in new_chains)
    if len(stored["items"]) > MAX_CONFIRMED:
        stored["items"] = stored["items"][-MAX_CONFIRMED:]
    await _save(CONFIRMED_KEY, stored)

    # Brain hook — confirmed chains are high-value learning signal
    try:
        from . import brain_hook
        in_window = sum(1 for c in new_chains if c.within_window)
        await brain_hook.absorb_silent(
            module="chain_correlator",
            summary=(
                f"Closed {len(new_chains)} causal chain(s); "
                f"{in_window} landed inside the 12–18mo window"
            ),
            success=True,
            extra_topics=["geopolitics", "procurement"],
        )
    except Exception:
        pass

    logger.info("[chain] close_chains: %d chains closed", len(new_chains))
    return [asdict(c) for c in new_chains]


# ── Consumer-facing helpers ───────────────────────────────────────────────

async def relationship_activation_nudges() -> list[dict]:
    """Emit nudges for ACTIVE or soon-to-be-active windows where the
    activation_due date is within ±ACTIVATION_NUDGE_WINDOW_DAYS of today.

    Consumed by contact_intelligence.generate_contact_briefing so the team
    sees "start warming an Angola MoD contact now — procurement window
    opens Dec 2026".
    """
    windows = await _load(WINDOWS_KEY)
    if not windows["items"]:
        return []
    now = _now()
    delta = timedelta(days=ACTIVATION_NUDGE_WINDOW_DAYS)
    nudges: list[dict] = []
    for w in windows["items"]:
        if w.get("status") == "EXPIRED":
            continue
        due = _parse_ts(w.get("activation_due", ""))
        if not due:
            continue
        if abs((due - now).days) > ACTIVATION_NUDGE_WINDOW_DAYS:
            continue
        wc = _parse_ts(w.get("window_close", ""))
        nudges.append({
            "iso2": w.get("iso2"),
            "region": w.get("region"),
            "shift_type": w.get("trigger_shift_type"),
            "activation_due": w.get("activation_due"),
            "window_open": w.get("window_open"),
            "window_close": w.get("window_close"),
            "confidence": w.get("confidence"),
            "_nudge": (
                f"{w.get('iso2')} ({w.get('region') or '?'}) — start activating "
                f"contacts now. Procurement window opens "
                f"{(w.get('window_open') or '')[:10]} "
                f"(trigger: {w.get('trigger_shift_type')})"
            ),
        })
    nudges.sort(key=lambda n: n.get("activation_due") or "")
    return nudges


async def generate_chain_briefing() -> str:
    """Slack/Markdown block for the 05:45 UTC team briefing."""
    windows = await _load(WINDOWS_KEY)
    items = [w for w in windows["items"] if w.get("status") in ("ACTIVE", "PROJECTED")]
    if not items:
        return ""

    # Highlight ACTIVE windows first, then imminent (opens within 60 days)
    now = _now()
    def _open_dt(w: dict) -> datetime:
        return _parse_ts(w.get("window_open", "")) or now

    active = [w for w in items if w.get("status") == "ACTIVE"]
    projected = sorted(
        [w for w in items if w.get("status") == "PROJECTED"],
        key=_open_dt,
    )
    imminent = [
        w for w in projected
        if 0 <= (_open_dt(w) - now).days <= 60
    ]

    lines = [
        "",
        "*🔗 CAUSAL CHAIN — 12–18mo horizon*",
    ]
    if active:
        lines.append(f"_{len(active)} ACTIVE procurement window(s):_")
        for w in active[:5]:
            lines.append(
                f"🟢 *{w.get('iso2')}* ({w.get('region') or '?'}) — "
                f"{w.get('trigger_shift_type')} @ {(w.get('shift_ts') or '')[:10]} → "
                f"window closes {(w.get('window_close') or '')[:10]} "
                f"(conf {float(w.get('confidence') or 0):.0%})"
            )
    if imminent:
        lines.append(f"_{len(imminent)} window(s) opening in the next 60 days:_")
        for w in imminent[:5]:
            lines.append(
                f"🟡 *{w.get('iso2')}* ({w.get('region') or '?'}) — opens "
                f"{(w.get('window_open') or '')[:10]} "
                f"(trigger: {w.get('trigger_shift_type')})"
            )
    if not active and not imminent:
        return ""
    return "\n".join(lines)


async def get_chain_context(query: str, max_items: int = 3) -> str:
    """Chat-surface context block. Returns a short string to inject into
    the prompt context, or "" if nothing is relevant.

    Adds a [CHAIN: ...] marker ARIA can cite in replies so the chain
    becomes visible to the operator, same pattern as [MEMORY]/[WEB]/[CONFLICT].
    """
    windows = await _load(WINDOWS_KEY)
    if not windows["items"]:
        return ""

    # Filter by ISO2 or country name mentioned in the query
    q = (query or "").lower()
    relevant: list[dict] = []
    for w in windows["items"]:
        iso2 = (w.get("iso2") or "").upper()
        if not iso2:
            continue
        iso3 = ct._ISO2_TO_ISO3.get(iso2, "").lower()
        # Find the English name by reverse-lookup via the name table
        name_hit = False
        try:
            name_table = ct._name_to_iso2_table()
            for name, code in name_table.items():
                if code == iso2 and name in q:
                    name_hit = True
                    break
        except Exception:
            pass
        if iso2.lower() in q or (iso3 and iso3 in q) or name_hit:
            if w.get("status") in ("ACTIVE", "PROJECTED"):
                relevant.append(w)

    if not relevant:
        return ""

    relevant.sort(key=lambda w: w.get("window_open") or "")
    parts = ["CAUSAL CHAIN — relevant to this query:"]
    for w in relevant[:max_items]:
        parts.append(
            f"  [CHAIN: {w.get('iso2')} — {w.get('trigger_shift_type')} "
            f"@ {(w.get('shift_ts') or '')[:10]} → proc window "
            f"{(w.get('window_open') or '')[:10]} to "
            f"{(w.get('window_close') or '')[:10]} "
            f"(status {w.get('status')}, conf "
            f"{float(w.get('confidence') or 0):.0%})]"
        )
    return "\n".join(parts)


# ── Stats (for dashboards / health checks) ────────────────────────────────

async def stats() -> dict:
    """Return a small diagnostic snapshot."""
    shifts = await _load(SHIFTS_KEY)
    windows = await _load(WINDOWS_KEY)
    confirmed = await _load(CONFIRMED_KEY)

    active_windows = [w for w in windows["items"] if w.get("status") == "ACTIVE"]
    in_window_confirmed = [c for c in confirmed["items"] if c.get("within_window")]
    regions_covered = sorted({
        w.get("region") for w in windows["items"] if w.get("region")
    })

    return {
        "shifts_total": len(shifts["items"]),
        "shifts_by_type": {
            t: sum(1 for s in shifts["items"] if s.get("shift_type") == t)
            for t in SHIFT_TYPES
        },
        "windows_total": len(windows["items"]),
        "windows_active": len(active_windows),
        "confirmed_chains": len(confirmed["items"]),
        "confirmed_in_window": len(in_window_confirmed),
        "regions_covered": regions_covered,
        "generated_at": _now().isoformat(),
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="chain_correlator",
                     summary="chain_correlator module active",
                     source_id="chain_correlator:init")
    except Exception:
        try:
            wire_failure(module="chain_correlator", detail="module init failed",
                        gap_type="engine_failure", source="chain_correlator:init")
        except Exception:
            pass
