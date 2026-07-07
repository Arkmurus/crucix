"""Pre-call counterparty brief.

Before the operator gets on a call with a counterparty, ARIA should produce a
compact structured brief that blends:

  1. Sanctions / PEP / watchlist screen (OpenSanctions via fuzzy_screen)
  2. Jurisdiction-specific defence-industry context (regional knowledge blocks)
  3. Broker-register portal context (procurement_knowledge) — relevant when
     the counterparty sits in a framework Arkmurus can tap (NSPA, DSP, DACON)

This is distinct from the compliance-brief endpoint (org-wide rolling
compliance digest) and from the DD orchestrator (heavy multi-layer
investigation). Pre-call brief is scoped, fast, and composes primitives
that already exist.

Before this module, sanctions screening was only called from dd_orchestrator,
network_walker, and research_tasks — never from a pre-meeting path. That
was the gap surfaced on the 2026-04-19 review of the TRB Bratunac brief.

Public API
──────────
  async build(name, *, jurisdiction=None, context_query=None,
              sanctions_threshold=0.78) -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import time
from typing import Any

logger = logging.getLogger("aria.precall_brief")


# Jurisdiction hint → regional knowledge fetcher. Keys are lowercased
# ISO2, ISO3, and common country names. Unknown jurisdictions skip the
# regional block — they still get sanctions + procurement context.
_REGIONAL_FETCHERS: dict[str, tuple[str, str]] = {
    # (module_name, function_name)
    "ba":          ("knowledge_balkans", "get_balkans_context"),
    "bih":         ("knowledge_balkans", "get_balkans_context"),
    "bosnia":      ("knowledge_balkans", "get_balkans_context"),
    "bosnia and herzegovina": ("knowledge_balkans", "get_balkans_context"),
    "rs":          ("knowledge_balkans", "get_balkans_context"),
    "serbia":      ("knowledge_balkans", "get_balkans_context"),
    "hr":          ("knowledge_balkans", "get_balkans_context"),
    "croatia":     ("knowledge_balkans", "get_balkans_context"),
    "si":          ("knowledge_balkans", "get_balkans_context"),
    "slovenia":    ("knowledge_balkans", "get_balkans_context"),
    "mk":          ("knowledge_balkans", "get_balkans_context"),
    "al":          ("knowledge_balkans", "get_balkans_context"),
    "me":          ("knowledge_balkans", "get_balkans_context"),
    "xk":          ("knowledge_balkans", "get_balkans_context"),
    "kosovo":      ("knowledge_balkans", "get_balkans_context"),
    # Turkey is deliberately standalone (Turkey ≠ NATO hard rule)
    "tr":          ("knowledge_turkey_standalone", "get_turkey_context"),
    "turkey":      ("knowledge_turkey_standalone", "get_turkey_context"),
    # Gulf
    "ae": ("knowledge_gulf", "get_gulf_context"),
    "sa": ("knowledge_gulf", "get_gulf_context"),
    "qa": ("knowledge_gulf", "get_gulf_context"),
    "kw": ("knowledge_gulf", "get_gulf_context"),
    "bh": ("knowledge_gulf", "get_gulf_context"),
    "om": ("knowledge_gulf", "get_gulf_context"),
    "jo": ("knowledge_gulf", "get_gulf_context"),
    "gulf": ("knowledge_gulf", "get_gulf_context"),
    # West Africa
    "ng": ("knowledge_west_africa", "get_west_africa_context"),
    "gh": ("knowledge_west_africa", "get_west_africa_context"),
    "sn": ("knowledge_west_africa", "get_west_africa_context"),
    "ci": ("knowledge_west_africa", "get_west_africa_context"),
    "west africa": ("knowledge_west_africa", "get_west_africa_context"),
    # Central Africa
    "cm": ("knowledge_central_africa", "get_central_africa_context"),
    "cd": ("knowledge_central_africa", "get_central_africa_context"),
    "cg": ("knowledge_central_africa", "get_central_africa_context"),
    # North Africa
    "ma": ("knowledge_north_africa", "get_north_africa_context"),
    "dz": ("knowledge_north_africa", "get_north_africa_context"),
    "eg": ("knowledge_north_africa", "get_north_africa_context"),
    "tn": ("knowledge_north_africa", "get_north_africa_context"),
    "ly": ("knowledge_north_africa", "get_north_africa_context"),
    # South / SE Asia
    "ph": ("knowledge_south_se_asia", "get_south_se_asia_context"),
    "id": ("knowledge_south_se_asia", "get_south_se_asia_context"),
    "vn": ("knowledge_south_se_asia", "get_south_se_asia_context"),
    "th": ("knowledge_south_se_asia", "get_south_se_asia_context"),
    "bd": ("knowledge_south_se_asia", "get_south_se_asia_context"),
    # LatAm non-Lusophone
    "co": ("knowledge_latam_non_lusophone", "get_latam_context"),
    "pe": ("knowledge_latam_non_lusophone", "get_latam_context"),
    "cl": ("knowledge_latam_non_lusophone", "get_latam_context"),
    "mx": ("knowledge_latam_non_lusophone", "get_latam_context"),
}


def _regional_context(jurisdiction: str | None, query: str) -> tuple[str, str]:
    """Return (region_label, context_text) for the given jurisdiction, or
    ('', '') if no regional fetcher matches."""
    if not jurisdiction:
        return "", ""
    key = jurisdiction.strip().lower()
    target = _REGIONAL_FETCHERS.get(key)
    if not target:
        return "", ""
    module_name, fn_name = target
    try:
        import importlib
        mod = importlib.import_module(f"aria_service.intel.{module_name}")
        fn = getattr(mod, fn_name, None)
        if not callable(fn):
            return "", ""
        text = fn(query) or ""
        return module_name.replace("knowledge_", ""), text
    except Exception as e:
        logger.warning("regional fetcher %s failed: %s", module_name, e)
        return "", ""


async def build(
    name: str,
    *,
    jurisdiction: str | None = None,
    context_query: str | None = None,
    sanctions_threshold: float = 0.78,
) -> dict[str, Any]:
    """Compose a pre-call brief for a counterparty.

    Args:
        name: Counterparty entity name (company or person).
        jurisdiction: ISO2, ISO3, or common country name hint. Used to
            dispatch the right regional knowledge fetcher. Optional.
        context_query: Free-text query for the regional + procurement
            knowledge lookups. Defaults to the counterparty name.
        sanctions_threshold: Match score at which to flag as blocking.

    Returns:
        dict with keys:
          name, jurisdiction, sanctions, regional (label + context text),
          procurement_portals (relevant broker-register context),
          flags (list of 'SANCTIONS_BLOCKING', 'SANCTIONS_SUGGESTIVE',
                  'REGIONAL_SENSITIVITY', 'NO_REGIONAL_CONTEXT'),
          duration_ms.
    """
    t0 = time.time()
    name = (name or "").strip()
    if not name:
        return {"error": "name required", "flags": ["INVALID"]}

    query = (context_query or name).strip()
    flags: list[str] = []

    # 1. Sanctions screen
    from . import sanctions as sanctions_mod
    try:
        sanctions_result = await sanctions_mod.fuzzy_screen(
            name, threshold=sanctions_threshold
        )
    except Exception as e:
        logger.warning("sanctions screen failed for %r: %s", name, e)
        sanctions_result = {"error": str(e), "matches": [], "blocked": False}

    if sanctions_result.get("blocked"):
        flags.append("SANCTIONS_BLOCKING")
    elif sanctions_result.get("match_count", 0) > 0:
        flags.append("SANCTIONS_SUGGESTIVE")

    # 2. Regional knowledge
    region_label, regional_text = _regional_context(jurisdiction, query)
    if not region_label and jurisdiction:
        flags.append("NO_REGIONAL_CONTEXT")
    # The BiH export-control + Bratunac sensitivities block is tagged inside
    # the balkans context. If it fires, surface the flag so the operator
    # prompt can't be flippant.
    if regional_text and any(
        marker in regional_text
        for marker in ("BRATUNAC", "Srebrenica", "TWO-STAGE EXPORT-LICENCE")
    ):
        flags.append("REGIONAL_SENSITIVITY")

    # 3. Procurement / broker-register context (only if query names a portal
    # or registration concept; otherwise it just pollutes the brief).
    procurement_text = ""
    try:
        from . import procurement_knowledge as pk
        # Route on counterparty + jurisdiction + generic broker terms.
        proc_query = f"{query} {jurisdiction or ''} broker registration CAGE NSPA DSP"
        procurement_text = pk.get_procurement_context(proc_query) or ""
    except Exception as e:
        logger.debug("procurement context lookup failed: %s", e)

    duration_ms = int((time.time() - t0) * 1000)

    brief = {
        "name": name,
        "jurisdiction": jurisdiction,
        "query": query,
        "sanctions": {
            "blocked": bool(sanctions_result.get("blocked")),
            "top_score": sanctions_result.get("top_score", 0.0),
            "match_count": sanctions_result.get("match_count", 0),
            "matches": sanctions_result.get("matches", [])[:5],
            "blocking_matches": sanctions_result.get("blocking_matches", []),
            "variants_tried": sanctions_result.get("variants_tried", []),
            "threshold": sanctions_threshold,
            "disclaimer": sanctions_result.get("disclaimer", ""),
            "error": sanctions_result.get("error"),
        },
        "regional": {
            "label": region_label,
            "context": regional_text,
        },
        "procurement_portals": procurement_text,
        "flags": flags,
        "duration_ms": duration_ms,
    }

    # Brain hook — make the pre-call brief visible in the learning stream.
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="precall_brief",
            summary=(
                f"Pre-call brief '{name}' (jx={jurisdiction or '-'}): "
                f"flags={','.join(flags) or 'none'} "
                f"sanctions_top={brief['sanctions']['top_score']:.2f}"
            ),
            entity_name=name,
            success=True,
            confidence="PROBABLE" if flags else "CONFIRMED",
        )
    except Exception as _bh:
        logger.debug("precall_brief brain_hook failed: %s", _bh)

    return brief


def render_markdown(brief: dict[str, Any]) -> str:
    """Render a structured brief as Markdown for direct chat display."""
    if brief.get("error"):
        return f"**Pre-call brief error:** {brief['error']}"

    name = brief.get("name", "unknown")
    jx = brief.get("jurisdiction") or "—"
    flags = brief.get("flags") or []
    flag_line = ", ".join(flags) if flags else "none"

    s = brief.get("sanctions", {})
    blocking = s.get("blocking_matches") or []
    matches = s.get("matches") or []

    lines: list[str] = []
    lines.append(f"# Pre-call brief — {name}")
    lines.append(f"*Jurisdiction: {jx}  ·  Flags: {flag_line}  ·  "
                 f"{brief.get('duration_ms', 0)} ms*")
    lines.append("")

    # Sanctions
    lines.append("## Sanctions / watchlist screen")
    if s.get("error"):
        lines.append(f"_Screen errored: {s['error']}_")
    elif s.get("blocked"):
        lines.append(f"**BLOCKING** — {len(blocking)} match(es) at or above "
                     f"threshold {s.get('threshold')}.")
        for m in blocking[:3]:
            lists = ", ".join(m.get("lists") or [])
            lines.append(f"  - {m.get('name')}  ·  score {m.get('score')}  "
                         f"·  lists: {lists}")
    elif matches:
        lines.append(f"No blocking matches. {len(matches)} suggestive hit(s) "
                     f"for manual review (top score {s.get('top_score')}).")
        for m in matches[:3]:
            lines.append(f"  - {m.get('name')}  ·  score {m.get('score')}")
    else:
        lines.append("Clean — no OpenSanctions hits.")
    if s.get("disclaimer"):
        lines.append("")
        lines.append(f"_{s['disclaimer']}_")
    lines.append("")

    # Regional
    regional = brief.get("regional") or {}
    if regional.get("context"):
        lines.append(f"## Regional context — {regional.get('label') or 'n/a'}")
        lines.append(regional["context"])
        lines.append("")

    # Procurement
    if brief.get("procurement_portals"):
        lines.append("## Broker-register / portal context")
        lines.append(brief["procurement_portals"])
        lines.append("")

    # Operator prompts based on flags
    if "REGIONAL_SENSITIVITY" in flags or "SANCTIONS_BLOCKING" in flags:
        lines.append("## Operator reminders")
        if "SANCTIONS_BLOCKING" in flags:
            lines.append("- Do NOT proceed with commercial discussion until "
                         "the blocking match is manually cleared against "
                         "primary-source sanctions lists.")
        if "REGIONAL_SENSITIVITY" in flags:
            lines.append("- Regional-sensitivity flag present. Keep the call "
                         "commercial and technical; do not volunteer or probe "
                         "historical or political geography.")

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="precall_brief",
                     summary="precall_brief module active",
                     source_id="precall_brief:init")
    except Exception:
        try:
            wire_failure(module="precall_brief", detail="module init failed",
                        gap_type="engine_failure", source="precall_brief:init")
        except Exception:
            pass

    return "\n".join(lines)
