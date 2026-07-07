"""ARIA Peer Scan — compliance / DD / entity-intel systems ARIA competes with.

NOT to be confused with `competitors.py`, which tracks defence-industry OEM
competitors of Arkmurus (Baykar, Hanwha, Rheinmetall, etc.). That file is
about the broking firm's deal landscape.

THIS file is about ARIA's own peer landscape: the platforms a client could
use instead of ARIA to do sanctions screening, due diligence, beneficial-
ownership research, tender monitoring, or compliance workflow. ARIA needs
to know what they can do so she can (a) surface capability gaps versus
them, (b) propose adopt / counter-position / ignore for each gap, and
(c) deliver that to the team in the weekly briefing.

Seed list ─ the 9 agreed with the operator 2026-04-14:
  Dow Jones RiskCenter, LexisNexis WorldCompliance, Refinitiv World-Check,
  Sayari, Kharon, Castellum.AI, ComplyAdvantage, Moody's Orbis, Quantexon.

Each peer carries a capability vector across the same axes as ARIA's own
capability manifest — so `diff_against_manifest()` can surface a clean
"they have X, we don't" table. Scoring uses a simple coverage dict keyed
by axis (modules, jurisdictions, sanctions_sources, autonomous_tasks).

How it grows
────────────
The seed below is deliberately conservative — claims ARIA can defend.
`record_observation()` lets the weekly research flow ADD to a peer's
capability vector as new evidence is found in public sources (press
releases, product pages, analyst reports). ARIA never invents features
for a peer — if it's not observed, it's not claimed.

Public API
──────────
  await scan() -> dict                       # run the full scan + diff + emit
  await latest() -> dict | None              # last persisted scan
  get_peers() -> list[dict]                  # seed + observations merged
  await record_observation(peer, axis, items, source_url, note="") -> dict
  diff_against_manifest(manifest, peers) -> dict  # pure diff

Governed by the autonomy doctrine: scan results go to the briefing and
become gap tickets; ARIA proposes verdicts; nothing auto-sends, nothing
auto-spends.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.aria_peers")

_KEY_LATEST = "crucix:aria_peers:latest"
_KEY_HISTORY = "crucix:aria_peers:history"
_KEY_OBSERVATIONS = "crucix:aria_peers:observations"

# ── Seed capability matrix (conservative, publicly-documented claims) ──────
# Each peer is scored on the same axes as capability_manifest. Items are
# string identifiers; counts drive rough coverage comparison. "strengths"
# and "gaps_vs_peer" are free-text, used by the briefing to explain WHY
# a diff is material (not just count-wise).

_SEED_PEERS: list[dict[str, Any]] = [
    {
        "name": "Dow Jones RiskCenter",
        "category": "sanctions + adverse media",
        "strengths": "global sanctions + PEP + adverse media; very strong news licensing",
        "jurisdictions": ["global"],
        "sanctions_sources": ["OFAC", "EU", "UN", "HMT", "OFSI", "DFAT", "SECO"],
        "modules": ["sanctions", "adverse_media", "pep", "watchlist"],
        "autonomous": ["continuous_screening"],
    },
    {
        "name": "LexisNexis WorldCompliance",
        "category": "sanctions + DD + litigation",
        "strengths": "litigation records, state enforcement actions, long history",
        "jurisdictions": ["global"],
        "sanctions_sources": ["OFAC", "EU", "UN", "HMT", "consolidated"],
        "modules": ["sanctions", "pep", "litigation", "enforcement_actions"],
        "autonomous": [],
    },
    {
        "name": "Refinitiv World-Check",
        "category": "sanctions + KYC",
        "strengths": "bank incumbent; heavy PEP coverage; structured watchlists",
        "jurisdictions": ["global"],
        "sanctions_sources": ["OFAC", "EU", "UN", "HMT", "OFSI", "consolidated"],
        "modules": ["sanctions", "pep", "kyc", "watchlist"],
        "autonomous": ["continuous_screening"],
    },
    {
        "name": "Sayari",
        "category": "entity graph + UBO",
        "strengths": "deep UBO graphs, shell-network detection, commodity trace",
        "jurisdictions": ["CN", "RU", "HK", "AE", "global"],
        "sanctions_sources": ["OFAC", "EU", "UK"],
        "modules": ["ubo", "entity_graph", "commodity_trace", "shell_detection"],
        "autonomous": ["graph_expansion"],
    },
    {
        "name": "Kharon",
        "category": "sanctions + defence nexus",
        "strengths": "sanctions-network analysis; defence industrial base focus",
        "jurisdictions": ["global"],
        "sanctions_sources": ["OFAC", "EU", "UN", "BIS"],
        "modules": ["sanctions", "entity_graph", "defence_industrial_base"],
        "autonomous": ["network_alerts"],
    },
    {
        "name": "Castellum.AI",
        "category": "sanctions + API-first",
        "strengths": "near-real-time sanctions updates; developer-friendly API; free tier",
        "jurisdictions": ["global"],
        "sanctions_sources": ["OFAC", "EU", "UN", "HMT", "OFSI", "DFAT", "SECO", "consolidated"],
        "modules": ["sanctions", "pep", "adverse_media"],
        "autonomous": ["continuous_updates"],
    },
    {
        "name": "ComplyAdvantage",
        "category": "AML + screening",
        "strengths": "AI-driven adverse media; transaction monitoring; SMB pricing",
        "jurisdictions": ["global"],
        "sanctions_sources": ["OFAC", "EU", "UN", "HMT"],
        "modules": ["sanctions", "pep", "adverse_media", "transaction_monitoring"],
        "autonomous": ["continuous_screening", "tx_alerts"],
    },
    {
        "name": "Moody's Orbis",
        "category": "company database + UBO",
        "strengths": "widest company coverage (~500M entities), financials, UBO tree",
        "jurisdictions": ["global"],
        "sanctions_sources": [],
        "modules": ["company_registry", "financials", "ubo", "industry_classification"],
        "autonomous": [],
    },
    {
        "name": "Quantexa",
        "category": "entity resolution + network",
        "strengths": "entity resolution at scale; bank + gov clients; network analytics",
        "jurisdictions": ["global"],
        "sanctions_sources": [],
        "modules": ["entity_resolution", "entity_graph", "network_analytics"],
        "autonomous": ["graph_inference"],
    },
]


def get_peers() -> list[dict[str, Any]]:
    """Seed list merged with any runtime observations."""
    # Observations are additive — they extend a peer's lists but never
    # overwrite the seed. Read happens inside scan() which has the
    # redis_store import; this helper returns the seed alone for callers
    # that want the static baseline.
    return [dict(p) for p in _SEED_PEERS]


async def _load_observations() -> list[dict[str, Any]]:
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_OBSERVATIONS, 0, 499)
    out: list[dict[str, Any]] = []
    for r in raw:
        try:
            out.append(json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    return out


def _merge_observations(peers: list[dict], observations: list[dict]) -> list[dict]:
    """Fold observations into peer records — observations add to list-fields,
    never overwrite, and always carry a source URL."""
    by_name: dict[str, dict] = {p["name"]: dict(p) for p in peers}
    for obs in observations:
        name = obs.get("peer")
        axis = obs.get("axis")
        items = obs.get("items") or []
        if not name or not axis or not items:
            continue
        peer = by_name.get(name)
        if not peer:
            # Unknown peer observed — add it as a discovered entry
            peer = {
                "name": name, "category": obs.get("category", "discovered"),
                "strengths": obs.get("note", ""),
                "jurisdictions": [], "sanctions_sources": [], "modules": [], "autonomous": [],
                "discovered": True,
            }
            by_name[name] = peer
        existing = set(peer.get(axis, []) or [])
        existing.update(items)
        peer[axis] = sorted(existing)
    return list(by_name.values())


async def record_observation(
    peer: str,
    axis: str,
    items: list[str],
    source_url: str,
    note: str = "",
    category: str = "",
) -> dict:
    """Record a new observation about a peer — used by the weekly research
    flow when it finds new evidence in a press release, product page, or
    analyst report. Observations are append-only and always carry a source
    URL so the briefing can cite the claim."""
    if axis not in ("jurisdictions", "sanctions_sources", "modules", "autonomous"):
        raise ValueError(
            f"aria_peers.record_observation: unknown axis '{axis}'. "
            "Must be jurisdictions|sanctions_sources|modules|autonomous."
        )
    if not source_url:
        raise ValueError("aria_peers.record_observation: source_url is required")

    from . import redis_store as rs
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "peer": peer.strip(),
        "axis": axis,
        "items": [str(i).strip() for i in items if i],
        "source_url": source_url,
        "note": note[:500],
        "category": category,
    }
    try:
        await rs.lpush(_KEY_OBSERVATIONS, json.dumps(entry, default=str))
        await rs.ltrim(_KEY_OBSERVATIONS, 0, 999)
    except Exception as e:
        logger.warning("aria_peers: observation persist failed: %s", e)
        entry["_persisted"] = False
    return entry


def _verdict(axis: str, missing_item: str, count_aria: int, count_peer: int) -> str:
    """Cheap heuristic for adopt / counter-position / ignore. The briefing
    shows the verdict + reasoning; humans decide what to actually do.

    Rule of thumb:
      - sanctions_sources missing → ADOPT (compliance table stakes)
      - jurisdictions missing and peer covers it → ADOPT
      - modules: ADOPT if most peers have it, else COUNTER (differentiate)
      - autonomous: ADOPT if it's a retention feature (continuous_screening)
    """
    if axis == "sanctions_sources":
        return "ADOPT"
    if axis == "jurisdictions":
        return "ADOPT"
    if axis == "autonomous" and missing_item in {"continuous_screening", "continuous_updates"}:
        return "ADOPT"
    if count_aria >= count_peer:
        return "COUNTER_POSITION"
    return "REVIEW"


def diff_against_manifest(manifest: dict, peers: list[dict]) -> dict:
    """Pure: compute gaps between ARIA's capability_manifest and each peer.

    Returns:
      {
        per_peer: [{peer, gaps: [{axis, missing: [...], verdict}], overlap: int}],
        aggregate_gaps: {axis: [{item, peers_with_it: [...], verdict}]},
        generated_at: iso
      }

    aggregate_gaps is the useful view for the briefing — items that MORE
    THAN ONE peer has but ARIA doesn't are the strongest adopt candidates.
    """
    aria_axes = {
        "jurisdictions": set(manifest.get("jurisdictions", {}).get("items", []) or []),
        "sanctions_sources": set(manifest.get("sanctions_sources", {}).get("items", []) or []),
        "modules": set(manifest.get("modules", {}).get("items", []) or []),
        "autonomous": set(manifest.get("autonomous_tasks", {}).get("ids", []) or []),
    }

    per_peer: list[dict] = []
    # item → set(peers that have it)
    peer_coverage: dict[tuple[str, str], set[str]] = {}

    for p in peers:
        peer_name = p.get("name", "?")
        gaps: list[dict] = []
        overlap = 0
        for axis, aria_set in aria_axes.items():
            peer_items = set(p.get(axis, []) or [])
            missing = sorted(peer_items - aria_set)
            overlap += len(peer_items & aria_set)
            if missing:
                for m in missing:
                    peer_coverage.setdefault((axis, m), set()).add(peer_name)
                gaps.append({
                    "axis": axis,
                    "missing": missing,
                    "peer_count": len(peer_items),
                    "aria_count": len(aria_set),
                })
        per_peer.append({
            "peer": peer_name,
            "category": p.get("category", ""),
            "strengths": p.get("strengths", ""),
            "overlap": overlap,
            "gaps": gaps,
        })

    aggregate_gaps: dict[str, list[dict]] = {}
    for (axis, item), peer_names in peer_coverage.items():
        verdict = _verdict(axis, item,
                           count_aria=len(aria_axes[axis]),
                           count_peer=len(peer_names))
        aggregate_gaps.setdefault(axis, []).append({
            "item": item,
            "peers_with_it": sorted(peer_names),
            "peer_count": len(peer_names),
            "verdict": verdict,
        })
    # Sort aggregate gaps by how many peers have it (strongest signal first)
    for axis in aggregate_gaps:
        aggregate_gaps[axis].sort(key=lambda x: -x["peer_count"])

    return {
        "per_peer": per_peer,
        "aggregate_gaps": aggregate_gaps,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def scan() -> dict:
    """Full weekly scan: derive latest manifest, merge peer seed +
    observations, diff, persist, emit self_metrics signals, feed brain.

    Returns the full report. Intended to be called once a week via the
    autonomous engine; also exposed via `POST /api/aria/self/peers/scan`
    for manual runs after a new peer observation is recorded.
    """
    from . import redis_store as rs
    from . import capability_manifest as cm

    manifest = await cm.latest() or cm.derive()
    observations = await _load_observations()
    peers = _merge_observations(get_peers(), observations)
    report = diff_against_manifest(manifest, peers)
    report["manifest_hash"] = manifest.get("content_hash")
    report["peer_count"] = len(peers)
    report["observations_applied"] = len(observations)

    try:
        await rs.set_json(_KEY_LATEST, report)
        await rs.lpush(_KEY_HISTORY, json.dumps(report, default=str))
        await rs.ltrim(_KEY_HISTORY, 0, 51)
    except Exception as e:
        logger.warning("aria_peers.scan: persist failed: %s", e)

    # Emit one self_metrics signal per aggregate gap — coverage=0.0 so the
    # rollup surfaces them alongside other capability regressions. The
    # domain encodes axis so self_assess can group them.
    try:
        from . import self_metrics
        for axis, gaps in report["aggregate_gaps"].items():
            for g in gaps:
                await self_metrics.emit(
                    "coverage",
                    f"peer_gap_{axis}",
                    "peer_has_we_dont",
                    0.0,
                    context={
                        "item": g["item"],
                        "peer_count": g["peer_count"],
                        "verdict": g["verdict"],
                    },
                    source_module="aria_peers",
                )
    except Exception as e:
        logger.debug("aria_peers: self_metrics emit failed: %s", e)

    # Feed brain so ARIA can answer "what do our peers have that we don't?"
    try:
        from . import brain_hook
        top_gaps = []
        for axis, gaps in report["aggregate_gaps"].items():
            for g in gaps[:3]:  # top 3 per axis
                top_gaps.append(f"{axis}:{g['item']} ({g['peer_count']} peers, {g['verdict']})")
        if top_gaps:
            await brain_hook.absorb(
                module="aria_peers",
                summary=f"Peer scan: {len(peers)} peers, top gaps — " + "; ".join(top_gaps[:8]),
                entity_name="aria_peer_landscape",
                success=True,
                confidence="ASSESSED",
            )
    except Exception as e:
        logger.debug("aria_peers: brain_hook failed: %s", e)

    return report


async def latest() -> dict | None:
    from . import redis_store as rs
    return await rs.get_json(_KEY_LATEST)


async def history(limit: int = 10) -> list[dict]:
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_HISTORY, 0, max(0, limit - 1))
    out: list[dict] = []
    for r in raw:
        try:
            out.append(json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="aria_peers",
                     summary="aria_peers module active",
                     source_id="aria_peers:init")
    except Exception:
        try:
            wire_failure(module="aria_peers", detail="module init failed",
                        gap_type="engine_failure", source="aria_peers:init")
        except Exception:
            pass

    return out
