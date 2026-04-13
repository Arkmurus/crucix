# =============================================================================
# ARIA — Network Layer: director, address, and PEP graph walker
# aria_service/intel/network_walker.py
#
# Layer 2 of the 7-layer DD orchestrator. Given a seed entity, walks
# the connected graph out to one hop by default (configurable), looking
# for:
#
#   - Cross-linked entities via shared directors
#   - Address clusters (entities at the same registered address)
#   - PEP connections (directors matching the sanctions / PEP database)
#   - Sanctions-network contamination (any 1-hop entity flagged)
#
# COMPOSITIONAL — this module CALLS existing functions, never modifies
# them. If a helper isn't available (e.g. companies_house is a stub for
# non-UK entities), the walker records a data_gap and continues.
#
# Current data sources:
#   - companies_house.py (UK-only, full registry)
#   - sanctions.py (OpenSanctions-backed fuzzy screen)
# Future sources (pluggable via extract_cross_links stub):
#   - OpenCorporates REST API (global corporate graph)
#   - Sayari Graph (commercial, needs API key)
#   - ICIJ Offshore Leaks (Panama/Paradise/Pandora Papers)
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("ARIA.NetworkWalker")


# =============================================================================
# DIRECTOR GRAPH
# =============================================================================

async def _directors_uk(company_number: str) -> list[dict]:
    """Fetch directors from Companies House. Returns [] on error / no data."""
    try:
        from . import companies_house
        if not hasattr(companies_house, "get_officers"):
            return []
        officers = await companies_house.get_officers(company_number)
        return officers or []
    except Exception as e:
        logger.debug("directors_uk failed for %s: %s", company_number, e)
        return []


async def _other_appointments_for_officer(name: str, limit: int = 20) -> list[dict]:
    """Find other entities the officer sits on. UK-only via companies_house
    search; returns [] for non-UK or on error.

    When an OpenCorporates / Sayari wrapper is added, swap this function
    to return global results.
    """
    try:
        from . import companies_house
        if not hasattr(companies_house, "search_companies"):
            return []
        # companies_house doesn't expose an officer-search endpoint by
        # name in the current stub, but search_companies at least lets us
        # look for entities whose name matches the officer — useful when
        # the officer has an eponymous company.
        hits = await companies_house.search_companies(name, limit=limit)
        return hits or []
    except Exception as e:
        logger.debug("other_appointments failed for %s: %s", name, e)
        return []


# =============================================================================
# SANCTIONS NETWORK SCREEN (one-hop)
# =============================================================================

async def _screen_name(name: str) -> dict:
    """Screen a single name against sanctions + PEP lists. Returns a
    normalised dict: {name, hit: bool, matches: [...], score: float}."""
    try:
        from . import sanctions
        if hasattr(sanctions, "screen_with_aliases"):
            result = await sanctions.screen_with_aliases(name)
        elif hasattr(sanctions, "fuzzy_screen"):
            result = await sanctions.fuzzy_screen(name)
        else:
            return {"name": name, "hit": False, "error": "no sanctions module entrypoint"}
        matches = result.get("matches") or []
        return {
            "name": name,
            "hit": bool(matches) or bool(result.get("hit")),
            "matches": matches,
            "score": float(result.get("score") or 0.0),
            "raw": result,
        }
    except Exception as e:
        logger.debug("sanctions screen failed for %s: %s", name, e)
        return {"name": name, "hit": False, "error": str(e)}


# =============================================================================
# PUBLIC API — walk_network
# =============================================================================

async def walk_network(
    entity_name: str,
    *,
    entity_type: str = "company",
    jurisdiction_iso2: str | None = None,
    registration_number: str | None = None,
    max_officers: int = 20,
    max_hops: int = 1,
    pre_resolved_officers: list[dict] | None = None,
) -> dict:
    """Walk the network around a seed entity one hop out.

    Returns a dict shaped for NetworkSection consumption:
      {
        "director_graph":        {"nodes": [...], "edges": [...]},
        "cross_linked_entities": [{name, via_director, …}],
        "address_cluster":       {address, cohabitants},
        "pep_connections":       [{name, role, source}],
        "sanctions_network":     [{entity, hit_details}],
        "findings":              [{severity, title, detail}],
        "data_gaps":             [...],
        "stats":                 {officers_checked, entities_walked, …}
      }

    The orchestrator converts this into a NetworkSection + findings list.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    cross_linked: list[dict] = []
    pep_connections: list[dict] = []
    sanctions_network: list[dict] = []
    findings: list[dict] = []
    data_gaps: list[str] = []
    stats = {
        "officers_checked": 0,
        "entities_walked": 0,
        "sanctions_screens": 0,
        "data_sources_used": [],
    }

    # Seed node
    nodes.append({
        "id": "seed",
        "name": entity_name,
        "type": entity_type,
        "jurisdiction": jurisdiction_iso2,
        "registration_number": registration_number,
        "role": "subject",
    })

    # ── Step 1: Get directors ──
    officers: list[dict] = []
    if pre_resolved_officers:
        # Directors already found by the identity layer (registry adapter)
        officers = list(pre_resolved_officers)
        stats["data_sources_used"].append("identity_layer")
    elif jurisdiction_iso2 == "GB" and registration_number:
        officers = await _directors_uk(registration_number)
        stats["data_sources_used"].append("companies_house")
    else:
        try:
            from .dd_orchestrator import _national_registry_hint
            hint = _national_registry_hint(jurisdiction_iso2, None)
        except Exception:
            hint = "run a manual search of the target country's national corporate registry"
        data_gaps.append(
            f"Directors unavailable — ARIA has Companies House coverage for GB only. "
            f"Manual action for {jurisdiction_iso2 or 'this jurisdiction'}: {hint}"
        )
    if not officers and jurisdiction_iso2 == "GB":
        data_gaps.append(f"Companies House returned no officers for {registration_number}")

    officers = officers[:max_officers]
    stats["officers_checked"] = len(officers)
    for i, officer in enumerate(officers):
        oname = (officer.get("name") or "").strip()
        if not oname:
            continue
        officer_id = f"officer:{i}"
        nodes.append({
            "id": officer_id,
            "name": oname,
            "type": "person",
            "role": officer.get("role") or officer.get("officer_role") or "director",
            "appointed_on": officer.get("appointed_on"),
            "nationality": officer.get("nationality"),
        })
        edges.append({"from": "seed", "to": officer_id, "kind": "has_officer"})

    # ── Step 2: Sanctions / PEP screen every officer ──
    # Use the shared topic-based classifier from _sanctions_classify.
    # Topic + score band decides severity:
    #   sanction / asset.frozen / export.control   → hard_stop
    #   crime / debarment / wanted / reg.action     → red
    #   role.pep / role.pol / reg.warn / corp.disqual → amber
    #   corp.state / corp.public / gov.* / mil      → info (noise)
    # A score below 0.75 demotes to info regardless of topic.
    from ._sanctions_classify import classify_matches
    _sev_to_conf = {
        "hard_stop": "CONFIRMED",
        "red":       "PROBABLE",
        "amber":     "ASSESSED",
        "info":      "UNCERTAIN",
    }

    if officers:
        screen_tasks = [_screen_name(officer.get("name", "")) for officer in officers if officer.get("name")]
        screens = await asyncio.gather(*screen_tasks, return_exceptions=True)
        for officer, screen in zip(officers, screens):
            stats["sanctions_screens"] += 1
            if isinstance(screen, Exception):
                continue
            matches = screen.get("matches") or []
            if not matches:
                continue
            classified = classify_matches(matches, query_name=officer.get("name", ""))
            severity = classified["worst_severity"]
            if severity in ("info", "none"):
                continue  # noise; don't escalate
            pep_connections.append({
                "name": officer.get("name"),
                "role": officer.get("role") or "director",
                "source": "sanctions/PEP screen",
                "matches": matches,
                "severity": severity,
                "summary": classified["summary"],
            })
            findings.append({
                "severity": severity,
                "title": f"Director {officer.get('name')} — {severity.upper()} on sanctions/PEP screen",
                "detail": classified["summary"][:300],
                "source": "sanctions.screen_with_aliases",
                "confidence": _sev_to_conf.get(severity, "ASSESSED"),
            })

    # ── Step 2b: Family/associate detection ──────────────────────────────
    # Detect surname clusters among directors (family companies) and
    # screen any PEP-flagged director's relatives via surname variants.
    if len(officers) >= 2:
        # Build surname map
        _surnames: dict[str, list[str]] = {}  # surname → [full names]
        for o in officers:
            oname = (o.get("name") or "").strip()
            if not oname:
                continue
            parts = oname.split()
            if len(parts) >= 2:
                surname = parts[-1]
                _surnames.setdefault(surname.lower(), []).append(oname)

        # Flag surname clusters (3+ people with same surname = family company)
        for surname, names in _surnames.items():
            if len(names) >= 2:
                findings.append({
                    "severity": "info",
                    "title": f"Family cluster detected: {len(names)} officers share surname '{names[0].split()[-1]}'",
                    "detail": f"Officers: {', '.join(names)}. Family-controlled companies are common but require UBO verification to confirm beneficial ownership chain.",
                    "source": "network_walker.family_detection",
                    "confidence": "ASSESSED",
                })

        # For PEP-flagged directors, screen close family variants
        for pep in pep_connections:
            pep_name = pep.get("name", "")
            pep_parts = pep_name.split()
            if len(pep_parts) < 2:
                continue
            pep_surname = pep_parts[-1]
            # Check if other directors share this PEP's surname
            related_officers = [
                o.get("name") for o in officers
                if o.get("name", "").split()[-1:] == [pep_surname]
                and o.get("name") != pep_name
            ]
            for rel_name in related_officers[:3]:
                findings.append({
                    "severity": "amber",
                    "title": f"PEP family link: {rel_name} shares surname with PEP-flagged {pep_name}",
                    "detail": f"{rel_name} is a director/officer and shares the surname '{pep_surname}' with {pep_name} who was flagged as {pep.get('severity', '?')}. Verify whether they are related.",
                    "source": "network_walker.family_association",
                    "confidence": "ASSESSED",
                })

    # ── Step 3: Seed entity itself screened (if not already done upstream) ──
    seed_screen = await _screen_name(entity_name)
    stats["sanctions_screens"] += 1
    seed_matches = seed_screen.get("matches") or []
    if seed_matches:
        seed_classified = classify_matches(seed_matches, query_name=entity_name)
        severity = seed_classified["worst_severity"]
        if severity not in ("info", "none"):
            sanctions_network.append({
                "entity": entity_name,
                "role": "subject",
                "matches": seed_matches,
                "severity": severity,
                "summary": seed_classified["summary"],
            })
            findings.append({
                "severity": severity,
                "title": f"Subject entity {entity_name} — {severity.upper()} on sanctions screen",
                "detail": seed_classified["summary"][:300],
                "source": "sanctions.screen_with_aliases",
                "confidence": _sev_to_conf.get(severity, "ASSESSED"),
            })

    # ── Step 4: One-hop expansion — officer → other appointments ──
    # Disabled when max_hops == 0 for quick runs. Default 1 hop.
    if max_hops >= 1 and officers:
        hop_budget = min(5, len(officers))  # don't fan-out uncontrollably
        for officer in officers[:hop_budget]:
            oname = (officer.get("name") or "").strip()
            if not oname:
                continue
            other = await _other_appointments_for_officer(oname, limit=10)
            stats["entities_walked"] += len(other)
            for entity in other:
                ename = (entity.get("title") or entity.get("company_name") or entity.get("name") or "").strip()
                if not ename or ename.lower() == entity_name.lower():
                    continue
                entity_num = entity.get("company_number") or entity.get("number")
                cross_linked.append({
                    "name": ename,
                    "via_director": oname,
                    "jurisdiction": "GB",  # companies_house only
                    "registration_number": entity_num,
                    "link_type": "shared_director",
                })
                node_id = f"entity:{len(nodes)}"
                nodes.append({
                    "id": node_id,
                    "name": ename,
                    "type": "company",
                    "jurisdiction": "GB",
                    "registration_number": entity_num,
                    "role": "cross_linked",
                })
                officer_edge_source = next(
                    (n["id"] for n in nodes if n.get("name") == oname and n.get("type") == "person"),
                    None,
                )
                if officer_edge_source:
                    edges.append({
                        "from": officer_edge_source,
                        "to": node_id,
                        "kind": "also_director_of",
                    })

    # ── Step 5: Cluster density flag ──
    # If a single director holds 10+ other appointments, that is a
    # well-known nominee-director pattern (indicator 4 in
    # due_diligence_playbooks ghost scoring). Pre-flag here.
    appointment_counts: dict[str, int] = {}
    for c in cross_linked:
        appointment_counts[c["via_director"]] = appointment_counts.get(c["via_director"], 0) + 1
    for director, count in appointment_counts.items():
        if count >= 10:
            findings.append({
                "severity": "amber",
                "title": f"Director {director} has {count}+ cross-linked appointments",
                "detail": "Possible nominee director pattern — feeds into ghost score indicator 4.",
                "source": "network_walker",
                "confidence": "PROBABLE",
            })

    # ── Step 6: Summary counts into stats ──
    stats["cross_linked_count"] = len(cross_linked)
    stats["pep_hits"] = len(pep_connections)
    stats["flagged_in_network"] = len(sanctions_network) + len(pep_connections)

    result = {
        "director_graph": {"nodes": nodes, "edges": edges},
        "cross_linked_entities": cross_linked,
        "address_cluster": {},   # populated once address-cluster API added
        "pep_connections": pep_connections,
        "sanctions_network": sanctions_network,
        "findings": findings,
        "data_gaps": data_gaps,
        "stats": stats,
    }

    # ── Brain hook: feed network analysis to learning ──
    try:
        from . import brain_hook
        _nw_detail = "; ".join(f.get("title", "")[:100] for f in findings[:8])
        await brain_hook.absorb(
            module="network_walker",
            summary=f"Network walk: {len(nodes)} directors, {len(cross_linked)} cross-linked, {len(pep_connections)} PEP, {len(sanctions_network)} sanctions, {len(findings)} findings",
            detail=_nw_detail or "no notable findings",
            entity_name=target.get("name", ""),
            success=True,
            confidence="PROBABLE",
            gap_type="knowledge_gap" if data_gaps else None,
            gap_detail=f"Network walker data gaps: {', '.join(data_gaps[:5])}" if data_gaps else None,
        )
    except Exception as _bh:
        logger.debug("network_walker brain_hook failed: %s", _bh)

    return result
