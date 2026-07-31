"""R-F2560 — OFAC / UN / FCDO designation-diff feed.

Snapshots each official sanctions list (OFAC SDN, UN Security Council, UK OFSI/FCDO)
by its stable per-designation id, diffs against the prior snapshot, and emits an alert
for each GENUINELY NEW designation. Those alerts are promoted by the Golden Intel
bridge as REAL decision-grade tier_1a `sanctions_change` signals (primary official
source — unlike the OpenSanctions heuristic which is capped to the Mining Queue).

Fully public data (no tenant dimension). Honesty guards:
- BASELINE: the first snapshot of a source emits NOTHING (existing designations are
  not "new" — they are the baseline).
- DEAD-FETCH: if a fetch returns empty or a suspicious fraction of the prior size, the
  diff is SKIPPED (snapshot kept) and a gap is recorded — a failed fetch must never be
  read as "everything was de-listed" nor, on recovery, flood "everything is new".
- CAP: at most _MAX_NEW_PER_SOURCE new designations promoted per run (a real list
  update is a handful to dozens); an over-cap run is logged, not silently truncated.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import redis_store as rs
from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.intel.sanctions_designation_diff")

_MODULE = "sanctions_designation_diff"
_SNAPSHOT_KEY = "crucix:sanctions:diff:snapshot:{source}"
_ALERTS_KEY = "crucix:sanctions:diff:alerts"
_MAX_ALERTS = 500
_MAX_NEW_PER_SOURCE = 50          # cap promotable new designations per source per run
_MIN_HEALTHY_LIST = 10            # a real OFAC/UN/FCDO list has hundreds+ of entries
_HEALTHY_FRACTION = 0.5           # a fetch < 50% of the prior snapshot is suspicious


def _clean(v: Any) -> str:
    return str(v or "").strip()


def _record_id(source: str, rec: dict) -> str:
    """Stable per-designation id used for the diff. Falls back to a normalised name so a
    record without its native id still diffs (better than dropping it)."""
    if source == "ofac":
        rid = _clean(rec.get("uid"))
    elif source == "un":
        rid = _clean(rec.get("group_id")) or _clean(rec.get("reference"))
    elif source == "fcdo":
        rid = _clean(rec.get("group_id"))
    elif source.startswith("canon:"):
        # R-F3534 — the canonical store's own upstream id (UNIQUE per source).
        rid = _clean(rec.get("uid"))
    elif source == "worldbank":
        # The debarment feed carries no stable id; name+period is the identity of
        # a debarment, and re-debarring the same firm for a new period IS new.
        rid = ":".join(x for x in (
            _clean(rec.get("name")).lower(),
            _clean(rec.get("ineligibility_from")),
            _clean(rec.get("ineligibility_to")),
        ) if x)
    else:
        rid = ""
    return rid or f"name:{_clean(rec.get('name')).lower()}"


# R-F3534 — per-source display label + the citation that source actually supports.
# Before this the golden-intel bridge fell back to the OFAC search URL for ANY
# source it did not recognise, so an EU or World Bank listing would have cited
# OFAC — a fabricated citation on a compliance signal, which is worse than no
# citation at all.
_SOURCE_LABELS: dict[str, str] = {
    "ofac": "OFAC SDN",
    "un": "UN Security Council",
    "fcdo": "UK FCDO",
    "worldbank": "World Bank Debarment",
    "canon:eu_consolidated": "EU Consolidated",
}
_SOURCE_CITATIONS: dict[str, str] = {
    "ofac": "https://sanctionssearch.ofac.treas.gov/",
    "un": "https://www.un.org/securitycouncil/sanctions/information",
    "fcdo": "https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets",
    "worldbank": "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms",
    "canon:eu_consolidated": "https://www.sanctionsmap.eu/",
}


def source_label(source: str) -> str:
    """Human list name. Never leaks the internal `canon:` prefix to a customer."""
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    bare = source.split(":", 1)[-1]
    return bare.replace("_", " ").upper()


def source_citation(source: str) -> str:
    """The register a reader can check this designation against. Empty when we do
    not have one — an absent citation is honest; a wrong one is not."""
    return _SOURCE_CITATIONS.get(source, "")


async def _loaders() -> list[tuple[str, Callable]]:
    """(source, full-list loader). Imported lazily so a broken source module can't
    break this module's import."""
    out: list[tuple[str, Callable]] = []
    try:
        from .sources import ofac_sdn
        out.append(("ofac", ofac_sdn._load_records))
    except Exception:
        logger.debug("[%s] ofac_sdn unavailable", _MODULE, exc_info=True)
    try:
        from .sources import un_sc_sanctions
        out.append(("un", un_sc_sanctions._load_records))
    except Exception:
        logger.debug("[%s] un_sc_sanctions unavailable", _MODULE, exc_info=True)
    try:
        from .sources import fcdo_sanctions
        out.append(("fcdo", fcdo_sanctions._load_records))
    except Exception:
        logger.debug("[%s] fcdo_sanctions unavailable", _MODULE, exc_info=True)

    # R-F3534 — GLOBAL coverage. The three lists above are US + UN + UK; the
    # canonical store was ALREADY holding the EU consolidated list (5,994 live
    # designations, refreshed daily) and nothing watched it for changes, so an EU
    # designation could never become intel. Enumerating the store means a regime
    # added there is watched automatically rather than needing a second edit here.
    #
    # `ofac_sdn` is deliberately SKIPPED: it is the same regime as the live "ofac"
    # loader above under a different id scheme, and adding it would both duplicate
    # every US alert and, on its first run, baseline 18,959 rows as a separate
    # source. Each NEW source baselines silently on its first run (`prior is None`
    # emits nothing), which is exactly why adding lists here is safe.
    try:
        from .sanctions_canonical import store as _canon
        _already = {"ofac_sdn"}
        for src in _canon.list_sources():
            if src in _already:
                continue
            out.append((f"canon:{src}", _make_canonical_loader(_canon, src)))
    except Exception:
        logger.debug("[%s] canonical store unavailable", _MODULE, exc_info=True)

    # World Bank debarment: not a sanctions regime, but for a defence/procurement
    # customer a newly debarred supplier is the same decision — do not bid, do not
    # contract. It is global by construction (every Bank-financed project).
    try:
        from .sources import worldbank_debarred
        out.append(("worldbank", worldbank_debarred._load_records))
    except Exception:
        logger.debug("[%s] worldbank_debarred unavailable", _MODULE, exc_info=True)
    return out


def _make_canonical_loader(canon_store, source: str) -> Callable:
    """Adapt a canonical-store source to the (async, no-arg) loader contract.

    The store call is synchronous SQLite; it runs in a worker thread so a 24k-row
    read cannot stall the event loop (the R-F3264 lesson — a full scan on this very
    table was caught wedging the loop).
    """
    async def _load() -> list[dict]:
        import asyncio
        return await asyncio.to_thread(canon_store.iter_designations, source)
    _load.__name__ = f"canonical_{source}"
    return _load


def _designation_alert(source: str, rec: dict, rid: str) -> dict:
    programs = rec.get("programs")
    if isinstance(programs, list):
        programs = ", ".join(str(p) for p in programs if p)
    programs = _clean(programs) or _clean(rec.get("regime"))
    # R-F3534 — a designation with no programs still carries its jurisdiction; for
    # the canonical rows that is the most useful context a reader gets.
    countries = rec.get("countries")
    if isinstance(countries, list):
        countries = ", ".join(str(c) for c in countries[:5] if c)
    return {
        "source": source,
        "id": rid,
        "entity": _clean(rec.get("name")),
        "list_type": _clean(rec.get("list_type")) or source_label(source),
        "programs": programs or _clean(rec.get("grounds")),
        "countries": _clean(countries),
        "entity_type": _clean(rec.get("entity_type")),
        "designation_date": _clean(rec.get("designation_date")) or _clean(rec.get("ineligibility_from")),
        # Prefer the record's own citation; otherwise the register this list
        # belongs to. Never another regime's search page.
        "citation_url": _clean(rec.get("citation_url")) or source_citation(source),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def run_designation_diff() -> dict:
    """Snapshot + diff every official sanctions list; emit alerts for new designations."""
    result: dict = {"sources": {}, "new_total": 0}
    for source, loader in await _loaders():
        # Per-source isolation (review #3): a state_store timeout or parse error on ONE
        # source must never starve the others or skip wire_success.
        try:
            records = await loader()
            by_id: dict[str, dict] = {}
            for r in (records or []):
                rid = _record_id(source, r)
                if rid:
                    by_id.setdefault(rid, r)
            snap_key = _SNAPSHOT_KEY.format(source=source)
            prior = await rs.get_json(snap_key)

            # DEAD-FETCH guard: empty or a suspiciously small fetch -> do NOT diff.
            if len(by_id) < _MIN_HEALTHY_LIST or (
                isinstance(prior, list) and prior and len(by_id) < _HEALTHY_FRACTION * len(prior)
            ):
                logger.warning("[%s] %s fetch looks unhealthy (%d entries; prior %s) — skipping diff",
                               _MODULE, source, len(by_id), len(prior) if isinstance(prior, list) else "none")
                wire_failure(_MODULE, f"{source} fetch unhealthy ({len(by_id)} entries) — diff skipped",
                             gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:{source}")
                result["sources"][source] = {"skipped": "unhealthy_fetch", "count": len(by_id)}
                continue

            if prior is None:
                await rs.set_json(snap_key, list(by_id.keys()))      # BASELINE — emit nothing
                result["sources"][source] = {"baseline": len(by_id)}
                continue

            prior_set = set(prior)
            new_ids = [rid for rid in by_id if rid not in prior_set]
            capped = new_ids[:_MAX_NEW_PER_SOURCE]
            if len(new_ids) > _MAX_NEW_PER_SOURCE:
                logger.warning("[%s] %s: %d new designations, capping to %d this run "
                               "(remainder promotes next cycle)", _MODULE, source,
                               len(new_ids), _MAX_NEW_PER_SOURCE)
            emitted = 0
            for rid in capped:
                alert = _designation_alert(source, by_id[rid], rid)
                if not alert["entity"]:
                    continue
                await rs.lpush(_ALERTS_KEY, json.dumps(alert, default=str))
                emitted += 1
            if emitted:
                await rs.ltrim(_ALERTS_KEY, 0, _MAX_ALERTS - 1)      # once, not per-lpush (review #4)
            # ONLY-ADD snapshot (review #1/#2): NEVER shrink — a transiently-partial fetch
            # then cannot re-emit long-standing designations as "new". Add ONLY the promoted
            # ids so any over-cap remainder stays "new" and drains over the next runs.
            new_snapshot = prior_set | set(capped)
            if new_snapshot != prior_set:                            # write only on change (review #4)
                await rs.set_json(snap_key, sorted(new_snapshot))
            result["sources"][source] = {"new": emitted, "new_uncapped": len(new_ids),
                                         "total": len(by_id)}
            result["new_total"] += emitted
        except Exception as exc:
            logger.warning("[%s] %s diff failed: %s", _MODULE, source, exc)
            wire_failure(_MODULE, f"{source} diff failed: {exc}",
                         gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:{source}")
            result["sources"][source] = {"error": str(exc)[:120]}
            continue

    wire_success(_MODULE, summary=f"designation diff: {result['new_total']} new across "
                                  f"{len(result['sources'])} lists")
    logger.info("[%s] %s", _MODULE, json.dumps(result))
    return result


async def get_designation_alerts(since_hours: int = 168) -> list[dict]:
    raw = await rs.lrange(_ALERTS_KEY, 0, _MAX_ALERTS - 1)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    out: list[dict] = []
    for r in raw:
        try:
            a = json.loads(r) if isinstance(r, str) else r
            ts = a.get("timestamp")
            if ts:
                try:
                    if datetime.fromisoformat(str(ts).replace("Z", "+00:00")) < cutoff:
                        continue
                except Exception:
                    pass
            out.append(a)
        except Exception:
            continue
    return out
