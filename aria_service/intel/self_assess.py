"""ARIA Self-Assessment — the State-of-ARIA briefing composer.

Runs once a day (piggy-backed on DAILY-TEAM-BRIEFING) and answers the
Rule Zero question: *what does ARIA see about herself today?*

Pulls together the four self-awareness sources already built:
  - self_metrics.strengths_and_weaknesses — how she's performing
  - capability_manifest.snapshot — whether her surface area drifted
  - aria_peers.latest — what peers have that she doesn't (weekly fresh)
  - mistake_ledger.stats + recent unprevented HIGH/CRITICAL mistakes

Produces a compact markdown block for the briefing. Also persists the
structured report for /self/assess so the team can inspect yesterday's
state on demand.

Governed by the autonomy doctrine:
  - Briefing delivery is an internal channel — auto-allowed.
  - Severity escalation (email to operator) fires only when something is
    flagged client_impact_possible — implemented as a simple predicate
    below so the rule is visible in code, not hidden in prompt text.
  - Nothing external auto-sends; nothing auto-spends.

Public API
──────────
  await assess() -> dict                           # structured report
  await generate_state_of_aria_briefing() -> str   # markdown block for briefing
  await latest() -> dict | None
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.self_assess")

_KEY_LATEST = "crucix:self_assess:latest"
_KEY_HISTORY = "crucix:self_assess:history"

# Thresholds — kept here and visible, not buried in prompt strings.
_WEAKNESS_SCORE = 0.7
_CONF_LOW_FLAG = 0.5     # overall self-confidence below this → operator alert
_UNPREVENTED_CRITICAL_FLAG = 1  # any unprevented CRITICAL mistake → alert


def _client_impact_possible(report: dict) -> tuple[bool, list[str]]:
    """Single predicate that decides whether this report warrants an
    operator email. Reasons listed for transparency — the briefing shows
    them so the team understands why the alert fired (or didn't).
    """
    reasons: list[str] = []
    if report.get("overall_confidence", 1.0) < _CONF_LOW_FLAG:
        reasons.append(
            f"self-confidence {report['overall_confidence']:.2f} below alert threshold"
        )
    unprevented_hi = [
        m for m in report.get("open_mistakes", [])
        if m.get("severity") in ("HIGH", "CRITICAL") and not m.get("prevented_count")
    ]
    if len(unprevented_hi) >= _UNPREVENTED_CRITICAL_FLAG:
        reasons.append(
            f"{len(unprevented_hi)} unprevented HIGH/CRITICAL mistake(s) in ledger"
        )
    if report.get("capability_drift", {}).get("removed"):
        reasons.append("capability removed since last snapshot")
    return bool(reasons), reasons


async def assess() -> dict:
    """Run the full self-assessment. Snapshots the manifest (so drift is
    captured daily), pulls strengths/weaknesses from self_metrics, reads
    the latest peer scan, and surfaces unprevented high-severity mistakes.

    Returns a structured report and persists it. Never raises — any
    source that degrades is reported as `degraded_sources: [...]` and
    the briefing still ships.
    """
    from . import redis_store as rs
    from . import self_metrics, capability_manifest as cm
    from . import aria_peers, mistake_ledger

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strengths": [],
        "weaknesses": [],
        "capability_drift": {},
        "peer_gap_highlight": None,
        "open_mistakes": [],
        "prevented_total": 0,
        "total_mistakes": 0,
        "overall_confidence": 0.5,
        "degraded_sources": [],
    }

    # ── 1. Self-metrics: strengths & weaknesses (7d) ──
    try:
        sw = await self_metrics.strengths_and_weaknesses(window_days=7, top_n=5)
        report["strengths"] = sw.get("strengths", [])
        report["weaknesses"] = sw.get("weaknesses", [])
        report["metrics_cells_total"] = sw.get("total_cells", 0)
    except Exception as e:
        logger.debug("self_assess: self_metrics failed: %s", e)
        report["degraded_sources"].append("self_metrics")

    # ── 2. Capability manifest snapshot (captures drift daily) ──
    try:
        snap = await cm.snapshot()
        d = snap.get("diff_vs_prior", {})
        report["capability_drift"] = {
            "baseline": d.get("baseline", False),
            "added": d.get("added", {}),
            "removed": d.get("removed", {}),
            "corpus_tier_drift": d.get("corpus_tier_drift", {}),
        }
        report["manifest_hash"] = snap.get("content_hash")
    except Exception as e:
        logger.debug("self_assess: capability_manifest failed: %s", e)
        report["degraded_sources"].append("capability_manifest")

    # ── 3. Peer gaps (use latest scan, don't re-run — that's weekly) ──
    try:
        peer = await aria_peers.latest()
        if peer:
            # Surface the single highest-signal aggregate gap (peer_count DESC)
            agg = peer.get("aggregate_gaps") or {}
            top: list[dict] = []
            for axis, items in agg.items():
                for item in items[:2]:
                    top.append({"axis": axis, **item})
            top.sort(key=lambda x: -x.get("peer_count", 0))
            if top:
                report["peer_gap_highlight"] = top[0]
            report["peer_scan_at"] = peer.get("generated_at")
    except Exception as e:
        logger.debug("self_assess: aria_peers failed: %s", e)
        report["degraded_sources"].append("aria_peers")

    # ── 4. Mistake ledger: stats + top unprevented HIGH/CRITICAL ──
    try:
        stats = await mistake_ledger.stats()
        report["total_mistakes"] = stats.get("total_entries", 0)
        report["prevented_total"] = stats.get("prevented_total", 0)
        recent = await mistake_ledger.recent(limit=50)
        open_hi = [
            m for m in recent
            if m.get("severity") in ("HIGH", "CRITICAL")
            and not m.get("prevented_count")
        ][:5]
        report["open_mistakes"] = [
            {
                "mistake_id": m.get("mistake_id"),
                "task_type": m.get("task_type"),
                "domain": m.get("domain"),
                "what_class": m.get("what_class"),
                "severity": m.get("severity"),
                "what": (m.get("what") or "")[:200],
            }
            for m in open_hi
        ]
    except Exception as e:
        logger.debug("self_assess: mistake_ledger failed: %s", e)
        report["degraded_sources"].append("mistake_ledger")

    # ── 5. Composite overall_confidence ──
    # Weighted across the sources that produced data: weaknesses count,
    # open mistakes, capability drift, peer gaps. Start 0.75, subtract
    # for each signal, clamp to [0.1, 0.95]. Reported in the briefing.
    conf = 0.75
    conf -= 0.05 * min(5, len(report["weaknesses"]))
    conf -= 0.1 * min(3, len(report["open_mistakes"]))
    if report["capability_drift"].get("removed"):
        conf -= 0.15
    if report["peer_gap_highlight"] and report["peer_gap_highlight"].get("peer_count", 0) >= 5:
        conf -= 0.05
    # Reward closed-loop learning
    if report["prevented_total"] > 0:
        conf += min(0.1, 0.02 * report["prevented_total"])
    report["overall_confidence"] = round(max(0.1, min(0.95, conf)), 3)

    # ── 6. Client-impact predicate + reasons ──
    alert, reasons = _client_impact_possible(report)
    report["client_impact_possible"] = alert
    report["alert_reasons"] = reasons

    # Persist
    try:
        await rs.set_json(_KEY_LATEST, report)
        await rs.lpush(_KEY_HISTORY, json.dumps(report, default=str))
        await rs.ltrim(_KEY_HISTORY, 0, 60)  # ~2 months of dailies
    except Exception as e:
        logger.warning("self_assess: persist failed: %s", e)

    # Feed brain so ARIA can answer "how am I doing?" from her own memory
    try:
        from . import brain_hook
        top_weak = ", ".join(
            f"{w['axis']}/{w['domain']}" for w in report["weaknesses"][:3]
        ) or "none"
        await brain_hook.absorb(
            module="self_assess",
            summary=(
                f"State-of-ARIA: conf={report['overall_confidence']:.2f}, "
                f"weaknesses=[{top_weak}], open_mistakes={len(report['open_mistakes'])}, "
                f"prevented_total={report['prevented_total']}, "
                f"alert={alert}"
            ),
            entity_name="aria_self",
            success=not alert,
            confidence="ASSESSED",
        )
    except Exception as e:
        logger.debug("self_assess brain_hook failed: %s", e)

    return report


async def latest() -> dict | None:
    from . import redis_store as rs
    return await rs.get_json(_KEY_LATEST)


def _fmt_cell(cell: dict) -> str:
    """Pretty-print one axis×domain cell for the briefing."""
    axis = cell.get("axis", "?")
    domain = cell.get("domain", "?")
    cur = cell.get("current", {})
    score = cur.get("score")
    n = cur.get("n", 0)
    trend = cell.get("trend", "flat")
    arrow = {"up": "↑", "down": "↓", "flat": "→", "new": "new", "stale": "—"}.get(trend, "·")
    score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
    return f"{axis}/{domain} {arrow} {score_str} (n={n})"


async def generate_state_of_aria_briefing() -> str:
    """Return the markdown block to append to the daily team briefing.

    Empty string if self-assessment has no meaningful data yet (freshly
    deployed, metrics chain empty) — don't clutter the briefing with a
    blank section. Once 24-48h of signals have accumulated this fills in.
    """
    report = await assess()

    # Gate: if nothing has emitted yet and there are no mistakes, skip.
    if (not report["strengths"] and not report["weaknesses"]
            and not report["open_mistakes"]
            and report["total_mistakes"] == 0):
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append("*🧠 STATE OF ARIA*")
    conf = report["overall_confidence"]
    emoji = "🟢" if conf >= 0.7 else ("🟡" if conf >= 0.5 else "🔴")
    lines.append(f"{emoji} Self-confidence: *{conf:.0%}* "
                 f"· {report['prevented_total']} mistake(s) prevented · "
                 f"{report['total_mistakes']} in ledger")

    if report["strengths"]:
        top = report["strengths"][:3]
        lines.append("*Strengths:* " + ", ".join(_fmt_cell(c) for c in top))
    if report["weaknesses"]:
        top = report["weaknesses"][:3]
        lines.append("*Weaknesses:* " + ", ".join(_fmt_cell(c) for c in top))

    if report["open_mistakes"]:
        n = len(report["open_mistakes"])
        top = report["open_mistakes"][0]
        lines.append(
            f"⚠️ *{n} unprevented {top['severity']} mistake(s)* — top: "
            f"{top['task_type']}/{top['domain']} · {top['what_class']}"
        )

    drift = report["capability_drift"]
    if drift.get("removed"):
        removed_count = sum(len(v) for v in drift["removed"].values())
        lines.append(f"📉 *Capability drift:* {removed_count} item(s) removed since last snapshot")
    if drift.get("added"):
        added_count = sum(len(v) for v in drift["added"].values())
        if added_count:
            lines.append(f"📈 *New capabilities:* {added_count} item(s) added")

    peer = report.get("peer_gap_highlight")
    if peer and peer.get("peer_count", 0) >= 2:
        lines.append(
            f"🎯 *Peer gap:* {peer['peer_count']} peers have "
            f"`{peer['item']}` ({peer['axis']}) — verdict *{peer['verdict']}*"
        )

    if report["client_impact_possible"]:
        lines.append(
            "🚨 *Flag for operator:* " + "; ".join(report["alert_reasons"])
        )

    return "\n".join(lines)
