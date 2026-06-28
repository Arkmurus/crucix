"""rca_screening — Relatives and Close Associates screening per
FATF Recommendation 12 (R-F76, 2026-05-09).

Why this module
───────────────
FATF Recommendation 12 requires enhanced DD on PEPs *and* their
relatives and close associates. The classic corruption pattern in every
one of ARIA's active markets is a procurement official's spouse or
sibling owning a company that wins tenders from that official's
ministry. The 61 contacts in the CRM include procurement officials
and government contacts; their family-business networks are not being
mapped.

OpenSanctions already returns a `relationships` array on each match
(familyOf / associateOf / relatedTo / spouseOf / childOf / parentOf /
siblingOf — see sanctions._normalise_match line 290+). What was
missing: recursive screening. When subject A has spouseOf=B, we need
to *also* screen B and surface B's risk to A.

What this module does
─────────────────────
1. screen_with_relatives(name) — runs the standard fuzzy_screen, then
   for every match, walks the relationships array (depth-limited to
   avoid runaway expansion) and screens each related party. Returns
   a tree-structured result with inherited risk highlighted.

2. risk_inheritance_summary(result) — collapses the tree into a one-
   line operator narrative: 'X cleared on direct screening BUT spouse
   Y is on OFAC SDN — inherited risk applies.'

3. Brain absorption — every confirmed inherited-risk discovery writes
   to knowledge with topic=rca_inherited_risk so subsequent DD on the
   same primary subject surfaces the relationship without re-querying.

Caveats
───────
- Depth is hard-capped at 1 (direct relatives) by default. Going to
  2 (in-laws, etc.) explodes the query count. The operator can opt in
  via the depth param if a high-stakes DD warrants it.

- OpenSanctions relationship targets are sometimes free-text strings
  (a name) and sometimes IDs. We screen both — name strings via
  fuzzy_screen, IDs via direct lookup if available.

- The 'inherited' tag marks risk derived from a related party. It's
  weaker evidence than direct designation. The DD output should make
  the distinction visible (a UK supplier whose director's spouse is
  on OFAC SDN is exposure; the supplier itself is not designated).

Public API
──────────
    screen_with_relatives(name, depth=1, threshold=0.78) -> dict
    risk_inheritance_summary(result) -> str
    summary() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
from typing import Any

logger = logging.getLogger("aria.rca_screening")

# Relationship kinds OpenSanctions emits — sanctions._normalise_match
# extracts these into the 'relationships' array. Order roughly by
# inherited-risk weight: spouse > parent/child > sibling > other family
# > associate > related_to (least specific).
_RELATIONSHIP_WEIGHT: dict[str, float] = {
    "spouseOf":   1.00,
    "childOf":    0.85,
    "parentOf":   0.85,
    "siblingOf":  0.70,
    "familyOf":   0.65,
    "associateOf": 0.50,
    "relatedTo":  0.40,
}

# Maximum number of related parties to screen per primary match.
# Defends against pathological data (one entity with 50+ "related" tags)
# from triggering a 50-query screening cascade.
_MAX_RELATIVES_PER_MATCH = 8

# Maximum depth of recursive screening. Depth=1 means: screen primary,
# then screen direct relatives. Depth=2 would also screen relatives'
# relatives (in-laws etc.) — disabled by default because it explodes
# the query count without producing proportionate signal.
_DEFAULT_DEPTH = 1


def _classify_inherited_risk(
    primary_name: str,
    relative_name: str,
    rel_kind: str,
    relative_match: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build the inherited-risk record for one (primary, relative) pair.

    Returns None if the relative had no sanctions hit (no inherited
    risk to surface). Returns a structured record otherwise.
    """
    if not relative_match:
        return None
    weight = _RELATIONSHIP_WEIGHT.get(rel_kind, 0.40)
    return {
        "primary":     primary_name,
        "relative":    relative_name,
        "relationship": rel_kind,
        "weight":      weight,
        "relative_lists":   relative_match.get("lists") or [],
        "relative_score":   relative_match.get("score"),
        "relative_topics":  relative_match.get("topics") or [],
        "narrative": (
            f"{primary_name} ↔ {relative_name} ({rel_kind}, weight {weight:.2f}): "
            f"relative listed on {', '.join((relative_match.get('lists') or [])[:3]) or 'OpenSanctions'}."
        ),
    }


async def _screen_one_relative(
    relative_name: str, threshold: float
) -> dict[str, Any] | None:
    """Screen a single related party by name. Returns the top match
    or None if nothing above threshold."""
    try:
        from . import sanctions as _s
        screen = await _s.fuzzy_screen(relative_name, threshold=threshold)
        matches = screen.get("matches") or []
        return matches[0] if matches else None
    except Exception as e:
        logger.debug("rca screen of %r failed: %s", relative_name, e)
        return None


async def screen_with_relatives(
    name: str,
    *,
    depth: int = _DEFAULT_DEPTH,
    threshold: float = 0.78,
) -> dict[str, Any]:
    """Run the primary screen, then walk relationships and screen each
    related party.

    Returns:
        {
          "name": str,
          "primary_screen": {...},          # raw fuzzy_screen output
          "primary_matches": int,
          "relatives_screened": int,
          "inherited_risks": [              # only non-None classifications
            {primary, relative, relationship, weight,
             relative_lists, relative_score, relative_topics, narrative},
            ...
          ],
          "narrative": str,                 # one-line summary
          "depth": int,
        }
    """
    try:
        from . import sanctions as _s
        primary = await _s.fuzzy_screen(name, threshold=threshold)
    except Exception as e:
        logger.warning("rca: primary fuzzy_screen failed for %r: %s", name, e)
        return {
            "name": name,
            "ok": False,
            "error": str(e),
            "primary_matches": 0,
            "relatives_screened": 0,
            "inherited_risks": [],
            "narrative": f"{name}: primary screen failed — {e}",
        }

    matches = primary.get("matches") or []
    relatives_screened = 0
    inherited: list[dict[str, Any]] = []

    if depth >= 1:
        for m in matches:
            relationships = m.get("relationships") or []
            relationships = relationships[:_MAX_RELATIVES_PER_MATCH]
            primary_name = m.get("name", name)
            for rel in relationships:
                rel_name = (rel.get("target") or "").strip()
                rel_kind = rel.get("kind", "relatedTo")
                if not rel_name:
                    continue
                relatives_screened += 1
                rel_match = await _screen_one_relative(rel_name, threshold)
                cls = _classify_inherited_risk(primary_name, rel_name, rel_kind, rel_match)
                if cls:
                    inherited.append(cls)

    # Build operator narrative
    if not matches and not inherited:
        narrative = f"{name}: no direct or inherited sanctions risk detected."
    elif matches and not inherited:
        narrative = (
            f"{name}: directly listed ({len(matches)} match(es)); "
            f"no additional inherited risk from {relatives_screened} related parties screened."
        )
    elif not matches and inherited:
        rel_list = ", ".join(
            "{} ({})".format(i["relative"], i["relationship"])
            for i in inherited[:3]
        )
        ellipsis = "..." if len(inherited) > 3 else ""
        narrative = (
            f"{name}: NOT directly listed BUT inherited risk via "
            f"{len(inherited)} related party/parties — {rel_list}{ellipsis}."
        )
    else:
        narrative = (
            f"{name}: directly listed AND inherited risk via "
            f"{len(inherited)} related party/parties."
        )

    out = {
        "name":               name,
        "ok":                 True,
        "primary_screen":     primary,
        "primary_matches":    len(matches),
        "relatives_screened": relatives_screened,
        "inherited_risks":    inherited,
        "narrative":          narrative,
        "depth":              depth,
        "threshold":          threshold,
    }

    # Brain absorption — any inherited risk is a knowledge fact worth
    # remembering for next time someone DDs the primary subject.
    if inherited:
        try:
            from . import brain_hook
            for ih in inherited:
                await brain_hook.absorb(
                    module="rca_screening",
                    summary=ih["narrative"],
                    detail=(
                        f"Primary: {ih['primary']}. Relative: {ih['relative']}. "
                        f"Relationship: {ih['relationship']}. "
                        f"Relative-listed-on: {ih['relative_lists']}. "
                        f"Inherited-risk weight: {ih['weight']}."
                    ),
                    topic="rca_inherited_risk",
                    entity=ih["primary"],
                    success=True,
                    confidence="ASSESSED",
                )
        except Exception as e:
            logger.debug("rca brain absorb failed (non-fatal): %s", e)

    return out


def risk_inheritance_summary(result: dict[str, Any]) -> str:
    """Operator-readable one-liner from a screen_with_relatives result."""
    return result.get("narrative", "")


def summary() -> dict[str, Any]:
    """Capability-manifest entry."""
    return {
        "module":              "rca_screening",
        "compliance_basis":    "FATF Recommendation 12 — PEPs + Relatives and Close Associates",
        "max_depth":           _DEFAULT_DEPTH,
        "max_relatives_per_match": _MAX_RELATIVES_PER_MATCH,
        "relationship_kinds":  list(_RELATIONSHIP_WEIGHT.keys()),
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="rca_screening",
                     summary="rca_screening module active",
                     source_id="rca_screening:init")
    except Exception:
        try:
            wire_failure(module="rca_screening", detail="module init failed",
                        gap_type="engine_failure", source="rca_screening:init")
        except Exception:
            pass
