"""R-F2230 — grounded competitive SWOT / landscape frame.

Structures ARIA's REAL competitor data into a recognized CI framework (SWOT)
WITHOUT fabrication. Every cell is backed by a stored source; cells with no
grounded source are marked INSUFFICIENT_DATA rather than LLM-invented.

This is the honest half of the competitive-intelligence-analyst template: the
framework structure, minus the "ask an LLM to fill a SWOT" step that produced
the R-F2002 fabrication (BD headline numbers invented) — mirrors the R-F2003
signal-backed-only discipline. §6-clean: reads curated + correlated stored data,
no paid source, no LLM call.
"""
from __future__ import annotations

from typing import Any, Optional

from .engine_wiring import wire_success, wire_failure  # §21a — brain wiring

_THREAT_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
# Strategy-field markers that denote a grounded OPPORTUNITY in the curated data
# (e.g. "Sanctioned — replacement opportunity", "acquisition opportunity",
# "potential partner"). Kept explicit so opportunities are never inferred loosely.
_OPP_MARKERS = ("opportunity", "acquisition", "replacement", "potential partner", "sanctioned")


def _insufficient(what: str) -> dict:
    return {"status": "insufficient_data", "note": f"no grounded source for {what}"}


def grounded_swot(
    market: Optional[str] = None,
    *,
    competitors: Optional[list[dict]] = None,
    signals: Optional[list[dict]] = None,
) -> dict:
    """Build a SWOT frame grounded ONLY in stored data. Never invents a cell.

    - Threats: competitors rated HIGH/MEDIUM (evidence = their curated strategy).
    - Opportunities: competitors whose strategy flags a replacement/acquisition/
      partner/sanctioned opening, PLUS correlated opportunity signals.
    - Strengths/Weaknesses describe the CLIENT's OWN position — absent from
      competitor data — so they are grounded only from signals explicitly tagged
      strength/weakness; INSUFFICIENT_DATA otherwise. Never fabricated.

    `competitors` defaults to the curated competitors.COMPETITORS list; `signals`
    is the output of signal_correlator.correlate_signals() (or None). Pure over
    its inputs so it is fully unit-testable and never touches the event loop.
    """
    if competitors is None:
        from .competitors import COMPETITORS as competitors  # curated, grounded
    _n_source = len(competitors or [])

    m = (market or "").strip().lower()

    def _in_market(c: dict) -> bool:
        if not m:
            return True
        hay = " ".join(str(c.get(k, "")) for k in ("country", "products", "strategy", "name")).lower()
        return m in hay

    comps = [c for c in (competitors or []) if _in_market(c)]

    threats = [
        {
            "item": f"{c.get('name', '?')} ({c.get('country', '?')})",
            "level": str(c.get("threat", "UNKNOWN")).upper(),
            "evidence": c.get("strategy", ""),
            "products": c.get("products", ""),
            "source": "competitors.COMPETITORS",
        }
        for c in comps
        if str(c.get("threat", "")).upper() in ("HIGH", "MEDIUM")
    ]
    threats.sort(key=lambda t: _THREAT_RANK.get(t["level"], 0), reverse=True)

    opportunities = [
        {
            "item": c.get("name", "?"),
            "evidence": c.get("strategy", ""),
            "source": "competitors.COMPETITORS",
        }
        for c in comps
        if any(k in str(c.get("strategy", "")).lower() for k in _OPP_MARKERS)
    ]
    for s in signals or []:
        if "opportunit" in str(s.get("insight_type", "")).lower():
            opportunities.append({
                "item": s.get("country") or s.get("insight_type") or "signal",
                "evidence": s.get("recommendation") or "",
                "source": "signal_correlator",
            })

    def _own_position(tag: str) -> list[dict]:
        return [
            {"item": s.get("country") or "signal", "evidence": s.get("recommendation", ""),
             "source": "signal_correlator"}
            for s in (signals or [])
            if str(s.get("insight_type", "")).lower() == tag
        ]

    strengths = _own_position("strength")
    weaknesses = _own_position("weakness")

    frame = {
        "framework": "SWOT",
        "market": market,
        "grounded": True,
        "competitors_considered": len(comps),
        "threats": threats or _insufficient("threats"),
        "opportunities": opportunities or _insufficient("opportunities"),
        "strengths": strengths or _insufficient("strengths (client position)"),
        "weaknesses": weaknesses or _insufficient("weaknesses (client position)"),
        "note": (
            "grounded in stored data only; INSUFFICIENT_DATA cells are not "
            "fabricated (R-F2002 lesson / R-F2230)"
        ),
    }
    # §21a — wire to the brain. Success telemetry when there is curated data to
    # frame; wire_failure (a real capability gap) ONLY when the curated competitor
    # source has no entries. A market filter matching nothing is INSUFFICIENT_DATA
    # — honest output, still a success, not a failure. Best-effort; never raises.
    try:
        if _n_source == 0:
            wire_failure(
                module="competitive_frame",
                detail="grounded_swot: curated competitor source has no entries",
                gap_type="missing_capability",
                source="competitive_frame.grounded_swot",
            )
        else:
            wire_success(
                module="competitive_frame",
                summary=f"SWOT frame: {len(threats)} threats, {len(opportunities)} opps, market={market or 'all'}",
                source_id="competitive_frame:R-F2230",
            )
    except Exception:
        pass
    return frame
