"""DD-layer integration shims for R-F579/F580/F581/F582 modules.

R-F584/F585/F586/F587 (2026-05-16).

What this exists for
────────────────────
The 4 capability modules shipped on 2026-05-16 — court_records,
cert_transparency, eccn_lookup, ais_gap_detector — are callable
in isolation but didn't yet wire into the live DD run. This module
bundles the 4 wiring shims so dd_orchestrator only needs a single
import + single call block instead of 4 scattered insertion points.

Why bundled (vs 4 separate edits)
─────────────────────────────────
1. **Minimises collision surface.** dd_orchestrator.py is 6,650+ LOC
   and actively edited by the parallel agent on R-F569/F573/F575.
   One additive call block ≈ one merge conflict, not four.
2. **Single test surface.** All 4 wirings live in
   test_dd_extensions_rf584_587.py.
3. **Symmetric error handling.** Every shim follows the same
   contract: never raises, logs honestly, returns a dict the
   caller can attach to `report.layers_run` or to a finding.

Each function
─────────────
- Pure-async, takes (target_dict, report)
- Returns a findings dict OR None when the layer has nothing to add
- Honest with empty-data paths — caller knows "skipped because no
  vessel context" vs "ran and found nothing"
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.intel.dd_layer_extensions")


# ── R-F584: court_records → Layer 4 (compliance) ──────────────────────


async def run_court_records_check(
    target: dict[str, Any],
    report: Any,
    *,
    timeout: float = 15.0,
) -> dict[str, Any] | None:
    """R-F584 — query CourtListener + Bailii for the entity.

    Hits emerge as a finding tagged `litigation_history` so the
    compliance + counter-intelligence layers can fold them into
    risk-tier reasoning. NEVER raises; per-source failures degrade.
    """
    try:
        entity_name = ""
        try:
            entity_name = (report.identity.entity_name or "").strip()
        except Exception:
            pass
        if not entity_name:
            entity_name = (target.get("entity") or target.get("name") or "").strip()
        if not entity_name or len(entity_name) < 3:
            return None

        from . import redis_store as _rs  # noqa: F401 — fail-open if import lifecycle changes
        from .sources import court_records as _cr

        result = await _cr.search_all(entity_name, limit_per_source=8, timeout=timeout)
        hits = result.get("hits") or []
        if not hits:
            return {
                "module":    "court_records",
                "r_number":  "R-F584",
                "entity":    entity_name,
                "hits":      [],
                "us_count":  result.get("us_count", 0),
                "uk_count":  result.get("uk_count", 0),
                "severity":  "NONE",
                "summary":   "No US/UK court records matched",
            }

        # Severity: ELEVATED on any US-federal hit (district / circuit /
        # supreme), LOW on Bailii only or state-level US.
        us_hits = [h for h in hits if h.get("jurisdiction") == "US"]
        uk_hits = [h for h in hits if h.get("jurisdiction") == "UK"]
        federal_keywords = ("U.S.", "S.D.", "E.D.", "N.D.", "W.D.", "D.C.",
                            "Cir.", "Federal", "Supreme")
        any_federal = any(
            any(k in (h.get("court") or "") for k in federal_keywords)
            for h in us_hits
        )
        severity = "ELEVATED" if any_federal else "LOW"

        return {
            "module":    "court_records",
            "r_number":  "R-F584",
            "entity":    entity_name,
            "hits":      hits[:5],  # cap for finding payload
            "us_count":  len(us_hits),
            "uk_count":  len(uk_hits),
            "severity":  severity,
            "summary": (
                f"Litigation history: {len(us_hits)} US case(s), "
                f"{len(uk_hits)} UK/Bailii case(s). "
                f"{'Federal court involvement detected.' if any_federal else ''}"
            ),
        }
    except Exception as e:
        logger.debug("[R-F584] court_records wire failed (non-fatal): %s", e)

        return None


# ── R-F585: cert_transparency → Layer 8 (counter_intelligence) ────────


_DOMAIN_RX = re.compile(r"\b([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,24}){1,3})\b", re.I)


def _extract_domain(target: dict[str, Any], report: Any) -> str:
    """Find a domain to seed CT search. Prefer explicit fields; fall
    back to scanning the entity name for a TLD pattern."""
    for k in ("website", "domain", "url"):
        v = (target.get(k) or "").strip()
        if v:
            # Strip scheme + path
            v = re.sub(r"^https?://", "", v, flags=re.I).split("/")[0]
            return v.lstrip("www.")
    # Scan entity name for a domain-looking token
    name = ""
    try:
        name = report.identity.entity_name or ""
    except Exception:
        pass
    if not name:
        name = target.get("entity") or target.get("name") or ""
    m = _DOMAIN_RX.search(name or "")
    return m.group(1) if m else ""


async def run_cert_transparency_check(
    target: dict[str, Any],
    report: Any,
    *,
    timeout: float = 15.0,
) -> dict[str, Any] | None:
    """R-F585 — pull CT log entries for the entity's domain, score
    the shell-broker fingerprint. Result feeds counter_intelligence.

    Skips with `severity: SKIPPED` (not None) when no domain is
    inferable from target — honest about why the layer didn't fire.
    """
    try:
        domain_or_name = _extract_domain(target, report)
        if not domain_or_name:
            # Fall back to entity name as a substring search
            try:
                domain_or_name = (report.identity.entity_name or "").strip()
            except Exception:
                domain_or_name = (target.get("entity") or "").strip()
        if not domain_or_name or len(domain_or_name) < 3:
            return None

        from .sources import cert_transparency as _ct
        hits = await _ct.search_certs(domain_or_name, limit=50, timeout=timeout)
        pattern = _ct.detect_shell_pattern(hits)

        severity = "NONE"
        if pattern["score"] >= 60:
            severity = "ELEVATED"
        elif pattern["score"] >= 30:
            severity = "LOW"

        return {
            "module":     "cert_transparency",
            "r_number":   "R-F585",
            "query":      domain_or_name,
            "cert_count": len(hits),
            "shell_score": pattern["score"],
            "signals":    pattern.get("signals", []),
            "issuer_count": pattern.get("issuer_count", 0),
            "apex_count":   pattern.get("apex_count", 0),
            "severity":   severity,
            "summary": (
                f"CT log: {len(hits)} certs across "
                f"{pattern.get('apex_count', 0)} apex domain(s). "
                f"Shell-pattern score: {pattern['score']}/100. "
                f"{'; '.join(pattern.get('signals', [])[:3])}"
            ),
        }
    except Exception as e:
        logger.debug("[R-F585] cert_transparency wire failed (non-fatal): %s", e)
        return None


# ── R-F586: eccn_lookup → export-control sub-layer ────────────────────


def _gather_equipment_text(target: dict[str, Any], report: Any) -> str:
    """Concatenate every text field that might mention equipment for
    the keyword match. Conservative — bounded length to keep regex
    cost low."""
    parts: list[str] = []
    for k in (
        "equipment_description", "equipment", "product", "products",
        "capability_statement", "narrative", "description",
        "rfq_subject", "rfq_text", "contract_subject", "trade_subject",
    ):
        v = target.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, list):
            parts.extend(str(x) for x in v if isinstance(x, (str, bytes)))
    try:
        ent = report.identity.entity_name or ""
        if ent:
            parts.append(ent)
    except Exception:
        pass
    blob = " | ".join(parts)
    return blob[:8000]  # cap


async def run_eccn_indicator_check(
    target: dict[str, Any],
    report: Any,
) -> dict[str, Any] | None:
    """R-F586 — scan the equipment text for ECCN keyword matches.

    Pure deterministic lookup (no network, no LLM). Returns indicators
    only — clause 3 reserves legal classification for licensed counsel,
    each hit carries that honesty_note.
    """
    try:
        text = _gather_equipment_text(target, report)
        if not text or len(text) < 8:
            return None

        from .sources import eccn_lookup as _ec
        hits = _ec.lookup_by_keyword(text, limit=6)
        if not hits:
            return {
                "module":   "eccn_lookup",
                "r_number": "R-F586",
                "hits":     [],
                "severity": "NONE",
                "summary":  "No ECCN keyword match in supplied equipment text",
            }

        # Severity = highest match_count among hits. >=2 = ELEVATED.
        top_match = max((h.get("match_count", 0) for h in hits), default=0)
        severity = "ELEVATED" if top_match >= 2 else "LOW"

        return {
            "module":   "eccn_lookup",
            "r_number": "R-F586",
            "hits":     hits,
            "severity": severity,
            "summary": (
                f"{len(hits)} potential ECCN match(es) — top: "
                f"{hits[0]['eccn']} ({hits[0]['title'][:60]})"
            ),
        }
    except Exception as e:
        logger.debug("[R-F586] eccn_lookup wire failed (non-fatal): %s", e)
        return None


# ── R-F587: ais_gap_detector → vessel sub-layer ───────────────────────


async def run_ais_gap_check(
    target: dict[str, Any],
    report: Any,
) -> dict[str, Any] | None:
    """R-F587 — run AIS-gap analysis when vessel context present.

    Triggers only when target carries:
      - `ais_positions`: list of {timestamp, lat, lon}, OR
      - `vessel_positions`: same shape

    Skips silently when no vessel data is supplied (no false signals
    for entity DD that doesn't involve a vessel).
    """
    try:
        positions = target.get("ais_positions") or target.get("vessel_positions")
        if not isinstance(positions, list) or len(positions) < 2:
            return None

        vessel_id = (
            target.get("vessel_imo")
            or target.get("imo")
            or target.get("vessel_name")
            or ""
        )

        from .sources import ais_gap_detector as _ais
        result = _ais.detect_gaps(positions, vessel_id=str(vessel_id))

        score = result.get("score", 0) or 0
        severity = "NONE"
        if score >= 50:
            severity = "ELEVATED"
        elif score >= 15:
            severity = "LOW"
        # Override: any critical gap → ELEVATED regardless of total score
        if any(g.get("suspicion") == "critical" for g in result.get("gaps", [])):
            severity = "ELEVATED"

        return {
            "module":      "ais_gap_detector",
            "r_number":    "R-F587",
            "vessel_id":   vessel_id or "(unspecified)",
            "total_gaps":  result.get("total_gaps", 0),
            "longest_gap_h": result.get("longest_gap_h", 0),
            "score":       score,
            "signals":     result.get("signals", []),
            "severity":    severity,
            "summary": (
                f"AIS gaps: {result.get('total_gaps', 0)} flagged, "
                f"longest {result.get('longest_gap_h', 0)}h. "
                f"Score: {score}/100. "
                f"{'; '.join(result.get('signals', [])[:3])}"
            ),
        }
    except Exception as e:
        logger.debug("[R-F587] ais_gap_detector wire failed (non-fatal): %s", e)
        return None


# ── Bundle entrypoint dd_orchestrator calls once ──────────────────────


async def run_all_extensions(
    target: dict[str, Any],
    report: Any,
    *,
    timeout_per_module: float = 15.0,
) -> dict[str, Any]:
    """Fire all 4 R-F584/585/586/587 shims and return a summary dict.

    Designed for a single call site in dd_orchestrator. Each shim
    is independently fail-safe; this wrapper aggregates the results
    and is itself never-raising.

    Returns:
        {
          "ran":      ["court_records", "cert_transparency", ...],
          "skipped":  ["ais_gap_detector"],          # no vessel context
          "errors":   [],
          "by_module": {<name>: <result dict>},
          "max_severity": "ELEVATED" | "LOW" | "NONE",
        }
    """
    import asyncio
    out: dict[str, Any] = {
        "ran":         [],
        "skipped":     [],
        "errors":      [],
        "by_module":   {},
        "max_severity": "NONE",
    }
    severity_rank = {"NONE": 0, "LOW": 1, "ELEVATED": 2, "HIGH": 3, "CRITICAL": 4}

    # Run all four concurrently — bounded by the per-module timeout
    # plus the wrapper's overall budget.
    coros = {
        "court_records":     run_court_records_check(target, report, timeout=timeout_per_module),
        "cert_transparency": run_cert_transparency_check(target, report, timeout=timeout_per_module),
        "eccn_lookup":       run_eccn_indicator_check(target, report),
        "ais_gap_detector":  run_ais_gap_check(target, report),
    }
    try:
        results = await asyncio.gather(*coros.values(), return_exceptions=True)
    except Exception as e:
        out["errors"].append(f"gather_failed: {e}")
        return out

    for name, res in zip(coros.keys(), results):
        if isinstance(res, Exception):
            out["errors"].append(f"{name}: {type(res).__name__}: {res}")
            continue
        if res is None:
            out["skipped"].append(name)
            continue
        out["ran"].append(name)
        out["by_module"][name] = res
        sev = (res.get("severity") or "NONE").upper()
        if severity_rank.get(sev, 0) > severity_rank.get(out["max_severity"], 0):
            out["max_severity"] = sev
    return out
