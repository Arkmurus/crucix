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
from .engine_wiring import wire_failure

import asyncio
import logging
from typing import Any

logger = logging.getLogger("aria.network_walker")


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


async def _other_appointments_for_officer(
    name: str, subject_company_number: str | None = None, limit: int = 20
) -> list[dict]:
    """Return the OTHER company appointments genuinely held by this officer,
    resolved by Companies House OFFICER_ID — not a name-string company search.

    R-F2993 — the old implementation called ``search_companies(name)``, which
    returns companies whose NAME matches the officer (eponymous shells), NOT the
    person's board seats. On a common name that inflated the count across many
    DIFFERENT people — the live Silverbrook defect where Justin Howard's real 5
    appointments (by officer_id) were reported as "10+" and drove a false
    "nominee director pattern". We now resolve the SPECIFIC person by matching the
    officer candidate whose appointments include the SUBJECT company; if the name
    cannot be disambiguated to that person, we return [] rather than a
    name-collision count. Never emits an appointment the person does not hold.
    """
    try:
        from . import companies_house
        if not (
            hasattr(companies_house, "search_officers")
            and hasattr(companies_house, "get_officer_appointments")
        ):
            return []
        cands = await companies_house.search_officers(name, limit=5)
        if not cands:
            return []
        subj = (subject_company_number or "").strip().upper()
        chosen: list[dict] | None = None
        for cand in cands:
            oid = cand.get("officer_id") or ""
            if not oid:
                continue
            apts = await companies_house.get_officer_appointments(oid, limit=limit)
            nums = {str(a.get("company_number") or "").upper() for a in apts}
            if subj and subj in nums:
                chosen = apts  # this candidate IS the person on the subject company
                break
        if chosen is None:
            # Cannot disambiguate to the person who sits on the subject company —
            # do NOT emit a name-collision count (the R-F2993 false-positive).
            return []
        out: list[dict] = []
        for a in chosen:
            cnum = str(a.get("company_number") or "").upper()
            if subj and cnum == subj:
                continue  # exclude the subject company itself
            out.append({
                "title": a.get("company_name"),
                "company_name": a.get("company_name"),
                "company_number": a.get("company_number"),
                "number": a.get("company_number"),
                "is_current": a.get("is_current"),
                "officer_role": a.get("officer_role"),
                "matched_via_officer_id": True,
            })
        return out
    except Exception as e:
        logger.debug("other_appointments (officer_id) failed for %s: %s", name, e)
        return []


# =============================================================================
# SANCTIONS NETWORK SCREEN (one-hop)
# =============================================================================

def _officer_surname(name: str) -> str:
    """R-F3030 — the surname of an officer, honouring the Companies House format.

    CH writes officer names as "SURNAME, Forename Middle" (e.g. "ELLARD, Sarah
    Louise"), so taking the LAST whitespace token returns a forename — live, that
    produced "2 officers share surname 'Louise'". When a comma is present the
    surname is what precedes it; otherwise fall back to the last token, which is
    right for the "Forename Surname" shape other sources use.

    Returns "" for a single-token or empty name: one token cannot be split into a
    forename and a surname, and guessing would be the same error in a new place.
    """
    raw = str(name or "").strip()
    if not raw:
        return ""
    if "," in raw:
        return raw.split(",", 1)[0].strip()
    parts = raw.split()
    return parts[-1].strip() if len(parts) >= 2 else ""


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
        # R-F303: previously emitted "Companies House coverage for GB
        # only" data_gap whenever jurisdiction wasn't GB — but ARIA has
        # adapters for 20+ jurisdictions via registry_adapters. Try
        # those FIRST. Only fall back to the manual-action hint if no
        # adapter exists or returns nothing. (Defensive duplicate of
        # what the identity layer should already have done — if the
        # identity layer succeeded, pre_resolved_officers wins above.)
        try:
            from . import registry_adapters as _ra
            seed_name_for_ra = entity_name or ""
            ra_result = await _ra.lookup_entity(
                name=seed_name_for_ra,
                jurisdiction_iso2=jurisdiction_iso2 or "",
                registration_number=registration_number,
            )
            ra_officers = (ra_result or {}).get("officers") or []
            if ra_officers:
                officers = list(ra_officers)
                stats["data_sources_used"].append(
                    f"registry_adapter:{jurisdiction_iso2}"
                )
        except Exception as _ra_err:
            logger.debug("registry_adapter fallback failed: %s", _ra_err)

        if not officers:
            try:
                from .dd_orchestrator import _national_registry_hint
                hint = _national_registry_hint(jurisdiction_iso2, None)
            except Exception:
                hint = "run a manual search of the target country's national corporate registry"
            data_gaps.append(
                f"Directors unavailable for {jurisdiction_iso2 or 'this jurisdiction'} "
                f"— the registry adapter returned no data (a registration/company number is "
                f"often required to query it, e.g. a CNPJ for BR). Manual action: {hint}"
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
        # ── R-F3030 — two bugs made this fabricate a family ──────────────
        #
        # LIVE (Roke Manor Research 00267550, dd_ba494e53f850): the report
        # asserted "Family cluster detected: 2 officers share surname 'Louise'".
        # Companies House formats officer names "SURNAME, Forename Middle", so
        # `parts[-1]` took the last FORENAME — "Louise" is not a surname at all.
        # And the two "officers" were ONE PERSON: Sarah Louise Ellard is both the
        # company secretary (appointed 2010-09-30) and a director (2014-07-24).
        # A single individual in two roles was rendered as a family relationship
        # between two people, off a token that was never a surname.
        #
        # Both halves are fixed: the surname is read from the CH comma form, and
        # officers are de-duplicated by `officer_id` (the same anchor R-F2993 uses
        # — never by name) before any clustering. The wording is also corrected:
        # a shared surname is a PROMPT to check, not proof of a family, and
        # asserting kinship from a name is the same fabrication class as
        # asserting a relationship from a name match.
        _seen_officer_ids: set[str] = set()
        _unique_officers: list[dict] = []
        for o in officers:
            oid = str((o.get("links") or {}).get("officer", {}).get("appointments")
                      if isinstance((o.get("links") or {}).get("officer"), dict) else
                      (o.get("officer_id") or "")).strip()
            if not oid:
                # No anchor → fall back to name + date of birth, NOT name alone.
                _dob = o.get("date_of_birth") or {}
                oid = (f"{(o.get('name') or '').strip().lower()}|"
                       f"{_dob.get('year', '')}-{_dob.get('month', '')}")
            if oid in _seen_officer_ids:
                continue
            _seen_officer_ids.add(oid)
            _unique_officers.append(o)

        _surnames: dict[str, list[str]] = {}  # surname → [full names]
        for o in _unique_officers:
            oname = (o.get("name") or "").strip()
            if not oname:
                continue
            surname = _officer_surname(oname)
            if surname:
                _surnames.setdefault(surname.lower(), []).append(oname)

        # Flag surname clusters (2+ DISTINCT people sharing a surname)
        for surname, names in _surnames.items():
            if len(names) >= 2:
                findings.append({
                    "severity": "info",
                    "title": (f"{len(names)} officers share the surname "
                              f"'{_officer_surname(names[0])}'"),
                    "detail": (f"Officers: {', '.join(names)}. A shared surname is NOT proof "
                               "of a family relationship — it may be coincidence. Family-"
                               "controlled companies are common and legitimate; this is a "
                               "prompt to verify the beneficial-ownership chain, not a finding "
                               "that these people are related."),
                    "source": "network_walker.family_detection",
                    "confidence": "ASSESSED",
                })

        # For PEP-flagged directors, screen close family variants
        for pep in pep_connections:
            pep_name = pep.get("name", "")
            # R-F3030 — the SAME last-token bug lived here, and this branch emits
            # AMBER: a mis-read forename would fabricate a "PEP family link"
            # against a named individual. Use the CH-aware surname on both sides.
            pep_surname = _officer_surname(pep_name)
            if not pep_surname:
                continue
            related_officers = [
                o.get("name") for o in officers
                if _officer_surname(o.get("name", "")).lower() == pep_surname.lower()
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
            other = await _other_appointments_for_officer(
                oname, subject_company_number=registration_number, limit=10)
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
                "detail": ("Possible nominee director pattern — feeds into ghost score "
                           "indicator 4. Count is officer_id-resolved (R-F2993): the same "
                           "person, matched via the subject's own board, not a name search."),
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
    # R-F288 (2026-05-11) — pre-R-F288 this block referenced `target.get(...)`
    # but no `target` variable exists in scope. The function's seed name is
    # `entity_name` (parameter). The bug was silently swallowed by the
    # except clause below, meaning brain_hook NEVER actually absorbed
    # network-walker signals. Brain's _MODULE_TOPICS["network_walker"]
    # stayed stale across the entire fleet. Fixed by referencing the
    # correct seed-entity variable.
    try:
        from . import brain_hook
        _nw_detail = "; ".join(f.get("title", "")[:100] for f in findings[:8])
        await brain_hook.absorb(
            module="network_walker",
            summary=f"Network walk: {len(nodes)} directors, {len(cross_linked)} cross-linked, {len(pep_connections)} PEP, {len(sanctions_network)} sanctions, {len(findings)} findings",
            detail=_nw_detail or "no notable findings",
            entity_name=entity_name,
            success=True,
            confidence="PROBABLE",
            gap_type="knowledge_gap" if data_gaps else None,
            gap_detail=f"Network walker data gaps: {', '.join(data_gaps[:5])}" if data_gaps else None,
        )
    except Exception as _bh:
        logger.debug("network_walker brain_hook failed: %s", _bh)

    return result


# ═══════════════════════════════════════════════════════════════════════════
# R-5001 (2026-05-11) — RECURSIVE UBO CHAIN WALK
# ═══════════════════════════════════════════════════════════════════════════
#
# Operator-visible gap closed: every WhatsApp DD said "UBO: not traced" or
# "single source" because walk_network() above stops after one hop and only
# expands officer → their other UK appointments. Real UBO investigation
# requires walking the OWNERSHIP graph multiple hops deep — entity →
# shareholders → their entities → their shareholders → … until convergence.
#
# Legal-defensibility discipline (operator mandate 2026-05-11 23:??):
# every node carries explicit provenance (discovered_via, evidence_url,
# hop_depth, parent_id) so any claim ARIA makes about an entity in the
# UBO chain can be back-traced to its source. Inferred relationships
# (NOT directly evidenced) get tagged [INFERRED] not [CONFIRMED]. Cycle
# detection prevents infinite loops. Budget caps prevent runaway cost.
#
# This function is ADDITIVE — it does NOT replace walk_network(). The
# DD orchestrator can opt into the deeper walk when the standard
# 1-hop response leaves UBO unresolved.

_UBO_HOP_DEFAULT = 3
_UBO_MAX_NODES_DEFAULT = 50
_UBO_OFFICERS_PER_ENTITY = 10
# ISO timestamps for node-level provenance. Imported lazily to avoid a
# module-load timing cost.


def _now_iso() -> str:
    from datetime import datetime as _dt, timezone as _tz
    return _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _node_key(name: str, jurisdiction: str | None, reg_num: str | None) -> str:
    """Stable identity key for cycle detection. Two nodes with the same
    name + jurisdiction + registration number are the same entity."""
    n = (name or "").strip().lower()
    j = (jurisdiction or "").strip().upper()
    r = (reg_num or "").strip()
    return f"{n}|{j}|{r}"


async def _fetch_officers_with_provenance(
    name: str,
    jurisdiction_iso2: str | None,
    registration_number: str | None,
) -> tuple[list[dict], str, str]:
    """Fetch officers for an entity, returning (officers, source_id, evidence_url).

    Currently supports GB via Companies House. Other jurisdictions
    return ([], "", "") — orchestrator notes the gap, NEVER fabricates.

    R-5001 (2026-05-11) — provenance: returned source_id + evidence_url
    are attached to every officer node so the operator can verify the
    chain back to a real registry response.
    """
    if jurisdiction_iso2 == "GB" and registration_number:
        officers = await _directors_uk(registration_number)
        return (
            officers,
            "companies_house",
            f"https://api.company-information.service.gov.uk/company/{registration_number}/officers",
        )
    # Non-GB: no registry coverage today
    return [], "", ""


async def _other_appointments_with_provenance(name: str, limit: int = 10) -> tuple[list[dict], str, str]:
    """Find an officer's other corporate appointments. Returns
    (entities, source_id, evidence_url). GB-only today."""
    appointments = await _other_appointments_for_officer(name, limit=limit)
    return (
        appointments,
        "companies_house",
        f"https://api.company-information.service.gov.uk/search/officers?q={name.replace(' ', '+')}",
    )


async def walk_ubo_chain(
    entity_name: str,
    *,
    entity_type: str = "company",
    jurisdiction_iso2: str | None = None,
    registration_number: str | None = None,
    max_hops: int = _UBO_HOP_DEFAULT,
    max_total_nodes: int = _UBO_MAX_NODES_DEFAULT,
    officers_per_entity: int = _UBO_OFFICERS_PER_ENTITY,
    sanctions_screen_every_node: bool = True,
) -> dict:
    """R-5001 (2026-05-11) — recursive UBO chain walker with provenance.

    Walks the corporate-ownership graph from `entity_name` outward up to
    `max_hops` deep. At each hop:
      1. Fetch the entity's officers/directors (registry-sourced)
      2. Sanctions-screen each new officer (per-node)
      3. For each officer, fetch their OTHER corporate appointments
      4. Add discovered entities to the graph, recurse into them

    Every node and edge carries provenance fields:
      - node.discovered_via:  the source that surfaced this node
      - node.evidence_url:    the specific URL backing it
      - node.discovered_at:   ISO timestamp
      - node.hop_depth:       0=seed, 1=direct officers, 2=their entities, …
      - node.parent_id:       the node that led to this discovery
      - edge.evidence_url:    URL backing this specific relationship
      - edge.tag:             [CONFIRMED] (registry-sourced) or
                              [INFERRED] (heuristic; e.g., surname cluster)

    Returns:
      {
        "graph":               {"nodes": [...], "edges": [...]},
        "sanctioned_in_chain": [{name, hop, severity, ...}, ...],
        "pep_in_chain":        [...],
        "coverage_gaps":       [{jurisdiction, hop, reason}, ...],
        "stats":               {hops_walked, total_nodes, total_edges,
                                sanctions_screens, registry_lookups, ...},
        "warnings":            ["budget exhausted", "cycle skipped: <name>"],
        "verdict":             "uncovered" | "partial" | "traced",
      }

    Budget controls (legal-defensibility):
      - max_hops: 3 default (operator can raise)
      - max_total_nodes: 50 (prevents runaway fan-out)
      - sanctions_screen_every_node: True default; each new entity
        AND each new person gets screened (already cached in
        OpenSanctions; minimal extra cost)
    """
    from ._sanctions_classify import (
        classify_matches as _cm,
        match_has_secondary_identity as _has_secondary_identity,
    )

    # Graph state — keyed by (name+jurisdiction+reg_num) tuple
    seen_keys: set[str] = set()
    nodes: list[dict] = []
    edges: list[dict] = []
    sanctioned_in_chain: list[dict] = []
    pep_in_chain: list[dict] = []
    coverage_gaps: list[dict] = []
    warnings: list[str] = []
    stats = {
        "hops_walked":      0,
        "total_nodes":      0,
        "total_edges":      0,
        "sanctions_screens":  0,
        "registry_lookups": 0,
        "officers_found":   0,
        "cross_links_found": 0,
        "cycles_skipped":   0,
        "budget_exhausted": False,
    }

    # ── Seed node ──
    seed_key = _node_key(entity_name, jurisdiction_iso2, registration_number)
    seed_node = {
        "id":               "seed",
        "name":             entity_name,
        "type":             entity_type,
        "jurisdiction":     jurisdiction_iso2,
        "registration_number": registration_number,
        "role":             "subject",
        "hop_depth":        0,
        "discovered_via":   "operator_input",
        "evidence_url":     "",
        "discovered_at":    _now_iso(),
        "parent_id":        None,
    }
    nodes.append(seed_node)
    seen_keys.add(seed_key)

    # BFS queue: each entry is (node, hop)
    queue: list[tuple[dict, int]] = [(seed_node, 0)]

    while queue:
        current, hop = queue.pop(0)
        if hop >= max_hops:
            continue
        if len(nodes) >= max_total_nodes:
            warnings.append(f"budget exhausted: {len(nodes)} nodes reached cap {max_total_nodes}")
            stats["budget_exhausted"] = True
            break

        # Only expand company-type nodes (officers' other appointments handled below)
        if current["type"] != "company":
            continue

        cname = current["name"]
        cjur = current.get("jurisdiction")
        cnum = current.get("registration_number")

        # ── Fetch officers for this entity ──
        try:
            officers, src_id, src_url = await _fetch_officers_with_provenance(
                cname, cjur, cnum,
            )
        except Exception as e:
            logger.warning("walk_ubo_chain: fetch officers for %s failed: %s", cname, e)
            officers = []
            src_id = src_url = ""

        stats["registry_lookups"] += 1

        if not officers:
            # R-F2365: the automated UBO OWNERSHIP-GRAPH walk is GB-only today
            # (_fetch_officers_with_provenance only queries Companies House). The
            # old gap text — "ARIA covers GB via Companies House today" — falsely
            # implied ARIA is GB-only OVERALL. It is not: entity-level directors
            # for 24 jurisdictions ARE covered by the identity layer's registry
            # adapter (ID-gated). Be accurate about BOTH: the walk is GB-only,
            # AND non-GB directors are reachable elsewhere (don't imply a CNPJ
            # would make THIS walk succeed — it wouldn't).
            _cj = (cjur or "").upper()
            try:
                from .registry_adapters import _SUPPORTED_JURISDICTIONS as _SJ
            except Exception:
                _SJ = set()
            if _cj == "GB":
                _reason = (
                    f"Companies House returned no officers for '{cname}'"
                    + (f" (reg no. {cnum})" if cnum else " (no registration number supplied)")
                )
            elif _cj in _SJ:
                try:
                    from .dd_orchestrator import _national_registry_hint
                    _hint = _national_registry_hint(_cj, None)
                except Exception:
                    _hint = "supply the national registration number"
                _reason = (
                    f"automated UBO ownership-graph walk is GB-only today (Companies "
                    f"House); {_cj} entity directors ARE covered separately by the "
                    f"registry adapter when an ID is supplied (e.g. a CNPJ for BR — "
                    f"see the Directors data-gap). {_hint}"
                )
            else:
                _reason = (
                    f"automated UBO ownership-graph walk is GB-only today; ARIA has no "
                    f"registry coverage for jurisdiction={cjur or 'unknown'} yet"
                )
            coverage_gaps.append({
                "jurisdiction": cjur or "unknown",
                "hop":          hop,
                "entity_name":  cname,
                "reason":       _reason,
            })
            continue

        stats["officers_found"] += len(officers)
        officers = officers[:officers_per_entity]

        for officer in officers:
            if len(nodes) >= max_total_nodes:
                stats["budget_exhausted"] = True
                warnings.append(f"budget exhausted mid-hop {hop+1}")
                break
            oname = (officer.get("name") or "").strip()
            if not oname:
                continue
            okey = _node_key(oname, None, None)
            if okey in seen_keys:
                stats["cycles_skipped"] += 1
                warnings.append(f"cycle skipped: {oname}")
                continue
            seen_keys.add(okey)

            officer_node = {
                "id":             f"node_{len(nodes)}",
                "name":           oname,
                "type":           "person",
                "role":           officer.get("role") or officer.get("officer_role") or "director",
                "appointed_on":   officer.get("appointed_on"),
                "nationality":    officer.get("nationality"),
                "hop_depth":      hop + 1,
                "discovered_via": src_id,
                "evidence_url":   src_url,
                "discovered_at":  _now_iso(),
                "parent_id":      current["id"],
            }
            nodes.append(officer_node)
            edges.append({
                "from":         current["id"],
                "to":           officer_node["id"],
                "kind":         "has_officer",
                "tag":          "[CONFIRMED]" if src_id else "[INFERRED]",
                "evidence_url": src_url,
            })

            # Sanctions-screen this officer
            if sanctions_screen_every_node:
                try:
                    screen = await _screen_name(oname)
                    stats["sanctions_screens"] += 1
                    matches = screen.get("matches") or []
                    if matches:
                        classified = _cm(matches, query_name=oname)
                        severity = classified["worst_severity"]
                        identified = any(
                            _has_secondary_identity(match) for match in matches
                        )
                        if severity in ("amber", "red", "hard_stop"):
                            entry = {
                                "name":     oname,
                                "hop":      hop + 1,
                                "severity": severity if identified else "info",
                                "summary":  classified["summary"][:300],
                                "parent_entity": current["name"],
                                "identity_confirmed": identified,
                            }
                            if severity == "amber" or not identified:
                                pep_in_chain.append(entry)
                            else:
                                sanctioned_in_chain.append(entry)
                except Exception as e:
                    logger.debug("walk_ubo_chain: screen %s failed: %s", oname, e)

            # ── Expand this officer to their other companies (next hop) ──
            if hop + 1 < max_hops and len(nodes) < max_total_nodes:
                try:
                    other_entities, src_id_e, src_url_e = await _other_appointments_with_provenance(
                        oname, limit=officers_per_entity,
                    )
                except Exception as e:
                    logger.warning("walk_ubo_chain: appointments for %s failed: %s", oname, e)
                    other_entities = []
                    src_id_e = src_url_e = ""

                stats["registry_lookups"] += 1
                stats["cross_links_found"] += len(other_entities)

                for ent in other_entities:
                    if len(nodes) >= max_total_nodes:
                        stats["budget_exhausted"] = True
                        break
                    ename = (ent.get("title") or ent.get("company_name")
                             or ent.get("name") or "").strip()
                    if not ename or ename.lower() == cname.lower():
                        continue
                    enum = ent.get("company_number") or ent.get("number")
                    ekey = _node_key(ename, "GB", enum)  # companies_house is GB
                    if ekey in seen_keys:
                        stats["cycles_skipped"] += 1
                        warnings.append(f"cycle skipped: {ename}")
                        continue
                    seen_keys.add(ekey)

                    company_node = {
                        "id":                "node_" + str(len(nodes)),
                        "name":              ename,
                        "type":              "company",
                        "jurisdiction":      "GB",
                        "registration_number": enum,
                        "role":              "cross_linked",
                        "hop_depth":         hop + 2,
                        "discovered_via":    src_id_e,
                        "evidence_url":      src_url_e,
                        "discovered_at":     _now_iso(),
                        "parent_id":         officer_node["id"],
                    }
                    nodes.append(company_node)
                    edges.append({
                        "from":         officer_node["id"],
                        "to":           company_node["id"],
                        "kind":         "also_officer_of",
                        "tag":          "[CONFIRMED]" if src_id_e else "[INFERRED]",
                        "evidence_url": src_url_e,
                    })
                    # Queue for further expansion if more hops remain
                    if hop + 2 < max_hops:
                        queue.append((company_node, hop + 2))

        stats["hops_walked"] = max(stats["hops_walked"], hop + 1)

    stats["total_nodes"] = len(nodes)
    stats["total_edges"] = len(edges)

    # Verdict:
    # - "uncovered": only seed node remains (no officers fetched)
    # - "partial":   some hops walked, some coverage gaps
    # - "traced":    hop limit reached cleanly with no gaps
    if len(nodes) <= 1:
        verdict = "uncovered"
    elif coverage_gaps:
        verdict = "partial"
    else:
        verdict = "traced"


    # R-F996 — wire to brain
    # R-F1349: wire_success was called with NO import → NameError that the
    # caller caught as "UBO walk failed", bricking EVERY ownership walk (zero
    # ownership nodes / zero sanctioned-in-chain on every company DD).
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="network_walker",
        summary="Network walk",
        source_id="network_walker:R-F996",
    )
    return {
        "graph": {"nodes": nodes, "edges": edges},
        "sanctioned_in_chain": sanctioned_in_chain,
        "pep_in_chain":        pep_in_chain,
        "coverage_gaps":       coverage_gaps,
        "stats":               stats,
        "warnings":            warnings,
        "verdict":             verdict,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
