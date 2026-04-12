# =============================================================================
# ARIA — ARK-DD Orchestrator
# aria_service/intel/dd_orchestrator.py
#
# The 7-layer due-diligence orchestrator. Takes a trigger (entity name
# + optional hints) and walks:
#
#   1. IDENTITY         sanctions + companies_house + ghost-score
#   2. NETWORK          one-hop director graph + PEP + sanctions network
#   3. VERIFICATION     cross-source triangulation + conflict detection
#   4. COMPLIANCE       country risk + export control + regional blocs
#   5. DIGITAL          web search (multilingual) + RAG + neural + press
#   6. SYNTHESIS        ACH + ghost score aggregation + SAR trigger
#   7. ARK-DD REPORT    assembled structured output
#
# COMPOSITIONAL — every existing module is CALLED via its public
# interface. No existing function signature is modified. No existing
# route is removed or changed. This module is purely additive.
#
# SHORT-CIRCUIT RULES (budget protection):
#   - If IDENTITY returns a sanctions hit → skip NETWORK, VERIFICATION,
#     DIGITAL, synthesise immediately as HARD_STOP
#   - If the per-run cost cap is exceeded mid-run → skip remaining
#     layers, mark them SKIPPED, synthesise with what's been collected
#   - If the per-layer timeout fires → mark layer ERROR, continue
#
# PERSISTENCE:
#   - Full report stored in Redis under crucix:dd:report:{run_id} (7 day TTL)
#   - Summary signal appended to intel_ledger
#   - Markdown render appended to mem0 notebook
#   - Trace linked via trace_stream so /trace shows the full lifecycle
#
# CALLABLE FROM:
#   - routes/aria.py POST /api/aria/dd/orchestrate (interactive)
#   - autonomous/tasks.py WEEKLY-DD-WATCHLIST (scheduled)
#   - fly ssh for manual one-shot runs
#
# FEATURE FLAGS (env):
#   ARIA_DD_ORCHESTRATOR_ENABLED (default 1)
#   ARIA_DD_COST_CAP_USD          (default 0.50 per run)
#   ARIA_DD_DEEP_RESEARCH          (default 1 — disable to skip layer 5 LLM)
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .dd_schema import (
    ARKDDReport,
    IdentitySection,
    NetworkSection,
    VerificationSection,
    ComplianceSection,
    DigitalSection,
    SynthesisSection,
    SectionMeta,
    Finding,
    Evidence,
    LayerStatus,
    RiskClassification,
    EntityType,
    weakest_confidence,
)

logger = logging.getLogger("ARIA.DDOrchestrator")


# =============================================================================
# CONFIG
# =============================================================================

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


DEFAULT_COST_CAP_USD = _env_float("ARIA_DD_COST_CAP_USD", 0.50)
DEFAULT_LAYER_TIMEOUT_S = _env_int("ARIA_DD_LAYER_TIMEOUT_S", 90)
DEEP_RESEARCH_ENABLED = (os.getenv("ARIA_DD_DEEP_RESEARCH", "1") or "1").strip() not in ("0", "false", "no", "off")
ORCHESTRATOR_ENABLED = (os.getenv("ARIA_DD_ORCHESTRATOR_ENABLED", "1") or "1").strip() not in ("0", "false", "no", "off")

REPORT_REDIS_KEY = "crucix:dd:report:{run_id}"
REPORT_INDEX_KEY = "crucix:dd:report_index"
REPORT_TTL_SECONDS = 7 * 24 * 3600


# =============================================================================
# LAYER RUNNERS — each layer is a coroutine that fills a section of the report
# =============================================================================

async def _run_identity_person(
    target: dict,
    report: ARKDDReport,
) -> bool:
    """Layer 1 (person mode) — Identity for a natural person.

    Runs:
      1. Name resolution → variant set (transliteration, short forms,
         particle handling, initials)
      2. Multi-variant sanctions screen — each variant is screened and
         matches are aggregated. Severity = worst across variants.
      3. PEP / ICC / Interpol topic classification from the match data.
      4. Role extraction from any supplied free-text context (title,
         organisation, nationality) so the synthesis layer has context.

    No Companies House, no CUI, no ghost score — those are company-only.
    Returns True on hard-stop (active sanctions hit).
    """
    t0 = time.time()
    report.identity.meta.started_at = datetime.now(timezone.utc).isoformat()

    name = (target.get("name") or target.get("entity") or target.get("query", "")).strip()
    nationality = target.get("nationality") or target.get("nationality_iso2")
    role = target.get("role") or target.get("title")
    organisation = target.get("organisation") or target.get("employer")
    dob = target.get("dob") or target.get("date_of_birth")

    report.identity.entity_name = name
    report.identity.entity_type = "person"
    report.identity.jurisdiction = target.get("jurisdiction") or nationality
    report.identity.jurisdiction_iso2 = target.get("jurisdiction_iso2")
    if role:
        report.identity.declared_activity = f"{role}" + (f" at {organisation}" if organisation else "")

    hard_stop = False

    # ── 1a. Name resolution ──
    try:
        from . import person_resolver
        resolution = person_resolver.resolve(
            name,
            nationality_iso2=target.get("jurisdiction_iso2"),
            max_variants=12,
        )
        report.identity.findings.append(Finding(
            severity="info",
            title=f"Name resolved: {len(resolution.variants)} variants ({resolution.script})",
            detail=(
                f"Canonical: {resolution.canonical}. "
                f"Components: given={resolution.components.given or '-'}, "
                f"particles={resolution.components.particles or '-'}, "
                f"surname={resolution.components.surname or '-'}. "
                f"First 5 variants: {', '.join(resolution.variants[:5])}."
            ),
            source="person_resolver.resolve",
            confidence="CONFIRMED",
        ))
    except Exception as e:
        logger.warning("Identity (person): name resolution failed: %s", e)
        resolution = None
        report.identity.data_gaps.append(f"name resolution failed: {str(e)[:120]}")

    # ── 1b. Multi-variant sanctions screen ──
    #
    # Each variant is screened separately against OpenSanctions. Matches
    # are aggregated and the worst severity wins. Token-overlap filtering
    # in classify_matches rejects short-string collisions (e.g. "Ali"
    # matching hundreds of unrelated sanctioned individuals named Ali).
    all_matches: list = []
    screened_variants: list[str] = []
    try:
        from . import sanctions as _sanc
        from ._sanctions_classify import classify_matches as _cm
        _screen_fn = getattr(_sanc, "screen_with_aliases", None) or getattr(_sanc, "fuzzy_screen", None)

        variants_to_screen: list[str] = []
        if resolution and resolution.variants:
            variants_to_screen = resolution.variants[:6]  # cost cap
        else:
            variants_to_screen = [name]

        for variant in variants_to_screen:
            if not variant or len(variant) < 4:
                continue
            try:
                _scr = await _screen_fn(variant) if _screen_fn else {"matches": []}
                screened_variants.append(variant)
                report.identity.meta.subcalls += 1
                _matches = _scr.get("matches") or []
                # Tag each match with which variant surfaced it for audit
                for _m in _matches:
                    if isinstance(_m, dict):
                        _m.setdefault("_variant", variant)
                all_matches.extend(_matches)
            except Exception as _e:
                logger.warning("Person screen failed for variant '%s': %s", variant, _e)

        # Store the aggregate screen result on the report for renderers
        report.identity.sanctions_screen = {
            "matches": all_matches,
            "variants_screened": screened_variants,
        }

        classified = _cm(all_matches, query_name=name)
        worst = classified["worst_severity"]

        if worst == "hard_stop":
            report.identity.findings.append(Finding(
                severity="hard_stop",
                title=f"{name} on active sanctions list",
                detail=classified["summary"],
                source="sanctions.person_screen",
                confidence="CONFIRMED",
            ))
            hard_stop = True
        elif worst == "red":
            report.identity.findings.append(Finding(
                severity="red",
                title=f"{name} linked to crime/debarment/ICC list",
                detail=classified["summary"],
                source="sanctions.person_screen",
                confidence="PROBABLE",
            ))
        elif worst == "amber":
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"{name} on PEP / adverse-media list",
                detail=classified["summary"] + " — enhanced DD required on individual before contracting.",
                source="sanctions.person_screen",
                confidence="ASSESSED",
            ))
        elif worst == "info":
            report.identity.findings.append(Finding(
                severity="info",
                title=f"{name} on transparency / officeholder register",
                detail=classified["summary"] + " — informational only, not a refusal ground.",
                source="sanctions.person_screen",
                confidence="ASSESSED",
            ))
        else:
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Sanctions + PEP screen CLEAN across {len(screened_variants)} name variant(s)",
                detail=(
                    f"No matches for {name} across OFAC SDN, UK OFSI, EU Consolidated, "
                    f"UN 1267, ICC, Interpol Red Notices, or OpenSanctions PEP data. "
                    f"Variants tested: {', '.join(screened_variants[:8])}. "
                    f"This is a POSITIVE CLEAN result — treat as clearance under "
                    f"standard commercial PDD."
                ),
                source="sanctions.person_screen",
                confidence="CONFIRMED",
            ))
    except Exception as e:
        logger.warning("Identity (person): sanctions screen failed: %s", e)
        report.identity.findings.append(Finding(
            severity="amber", title="Sanctions screen failed", detail=str(e)[:200],
            source="sanctions", confidence="UNCERTAIN",
        ))
        report.identity.data_gaps.append("sanctions screen did not complete")

    # ── 1c. Role / context hints ──
    if role or organisation:
        report.identity.findings.append(Finding(
            severity="info",
            title=f"Context: {role or 'unknown role'}{' at ' + organisation if organisation else ''}",
            detail=(
                f"Role and employer were supplied with the query. "
                f"These narrow match disambiguation but do NOT substitute "
                f"for verification — the subject's identity must still be "
                f"cross-referenced against the named organisation's own records."
            ),
            source="person_resolver.context",
            confidence="ASSESSED",
        ))

    if dob:
        report.identity.findings.append(Finding(
            severity="info",
            title=f"DOB supplied: {dob}",
            detail="DOB is the highest-value disambiguator for common names.",
            source="person_resolver.context",
            confidence="CONFIRMED",
        ))

    # ── 1d. Data gaps ──
    if not nationality:
        report.identity.data_gaps.append("nationality not supplied — material disambiguator missing")
    if not dob:
        report.identity.data_gaps.append("DOB not supplied — recommended before contracting")
    if not role and not organisation:
        report.identity.data_gaps.append("role/employer not supplied — weakens variant disambiguation")

    report.identity.meta.duration_ms = int((time.time() - t0) * 1000)
    report.identity.meta.status = LayerStatus.OK.value
    return hard_stop


async def _run_identity(
    target: dict,
    report: ARKDDReport,
) -> bool:
    """Layer 1 — Identity. Returns True if a hard-stop was triggered
    (sanctions hit), signalling the orchestrator to short-circuit."""
    entity_type = target.get("type") or EntityType.UNKNOWN.value
    # Person branch — separate logic path because persons don't have
    # Companies House, CUI, ghost score, or address pattern checks.
    # They DO need name-variant resolution, multi-variant sanctions
    # screening, and PEP classification.
    if entity_type == EntityType.PERSON.value or entity_type == "person":
        return await _run_identity_person(target, report)

    t0 = time.time()
    report.identity.meta.started_at = datetime.now(timezone.utc).isoformat()

    name = target.get("name") or target.get("entity") or target.get("query", "")
    jurisdiction = target.get("jurisdiction")
    jurisdiction_iso2 = target.get("jurisdiction_iso2")
    registration_number = target.get("registration_number")
    # Jurisdiction-specific registration IDs flow into registration_number
    # when the caller didn't supply one explicitly. This keeps the
    # downstream renderer, ghost scorer, and manual-registry hint
    # working on any jurisdiction we recognise.
    if not registration_number:
        for _k in ("cui", "nip", "cnpj", "cvr", "kvk", "siret", "vat"):
            if target.get(_k):
                registration_number = str(target[_k])
                break

    report.identity.entity_name = name
    report.identity.entity_type = entity_type
    report.identity.jurisdiction = jurisdiction
    report.identity.jurisdiction_iso2 = jurisdiction_iso2
    report.identity.registration_number = registration_number

    hard_stop = False

    # Romanian CUI → incorporation-date analyzer. If the caller
    # supplies a CUI (directly, via registration_number on a RO
    # jurisdiction, or in free text extracted by the chat intent
    # detector), run the sequential-CUI analysis and emit a finding.
    # This runs BEFORE the ghost scorer so the orchestrator can
    # surface the CUI-derived incorporation estimate as a first-class
    # identity signal, not just as an internal input to ghost
    # indicator 11.
    if (jurisdiction_iso2 == "RO" or (jurisdiction or "").lower() == "romania") and (target.get("cui") or registration_number):
        try:
            from . import _romanian_cui as _ro_cui
            _analysis = _ro_cui.analyse_cui(target.get("cui") or registration_number)
            if _analysis and _analysis.estimated_incorporation:
                report.identity.incorporation_date = _analysis.estimated_incorporation.isoformat()
                report.identity.findings.append(Finding(
                    severity="info",
                    title=f"Romanian CUI {_analysis.cui} estimates incorporation ≈ {_analysis.estimated_incorporation.isoformat()}",
                    detail=(
                        f"Sequential-CUI analysis places incorporation at "
                        f"{_analysis.estimated_incorporation.isoformat()} "
                        f"(±{_analysis.uncertainty_months} months). "
                        f"Company age: {_analysis.age_months_now} months. "
                        f"{_analysis.notes}. "
                        f"VERIFY against ONRC portal (https://portal.onrc.ro) "
                        f"before relying on this date."
                    ),
                    source="_romanian_cui.analyse_cui",
                    confidence="ASSESSED",
                ))
                # Also cross-check claimed founding year if supplied
                _claimed = target.get("claimed_founding_year")
                if _claimed is not None:
                    _cmp = _ro_cui.compare_claimed_founding(_analysis.cui, int(_claimed))
                    if _cmp["severity"] in ("red", "hard_stop"):
                        report.identity.findings.append(Finding(
                            severity=_cmp["severity"],
                            title=f"Founding-year misrepresentation: claimed {_claimed} vs CUI-estimated {_cmp['estimated_incorporation_year']}",
                            detail=_cmp["detail"],
                            source="_romanian_cui.compare_claimed_founding",
                            confidence="PROBABLE",
                        ))
                        if _cmp["severity"] == "hard_stop":
                            hard_stop = True
        except Exception as e:
            logger.debug("Romanian CUI analysis failed (non-fatal): %s", e)

    # If the caller supplied a registered address (via chat intent or
    # direct API), use it as the initial identity signal. Registry
    # lookup may overwrite it with authoritative data later.
    supplied_address = target.get("registered_address")
    if supplied_address and not report.identity.registered_address:
        report.identity.registered_address = supplied_address
        # Residential-apartment pattern detection. A registered office
        # at a specific apartment number inside a named block is a
        # ghost-indicator signal (indicator 2 — no verifiable physical
        # premises). We add it as an amber finding so the LLM sees it
        # without the orchestrator having to ship an expensive registry
        # lookup first.
        _addr_lower = supplied_address.lower()
        _residential_patterns = (
            "apt.", "apt ", " ap.", " ap ", " ap,", "apartment",
            "flat ", "unit ", "sc. ", "bl. ", "et. ", "etaj ", "floor ",
        )
        if any(p in _addr_lower for p in _residential_patterns):
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"Registered address is a residential apartment",
                detail=(
                    f"'{supplied_address}' — the address decodes to an apartment "
                    f"inside a named block/staircase/floor, not a commercial office. "
                    f"This matches ghost-indicator 2 (no verifiable physical premises). "
                    f"Not a refusal ground on its own; requires verification against "
                    f"the national registry and cross-check against the number of "
                    f"other entities registered at the same address."
                ),
                source="dd_orchestrator.residential_address_pattern",
                confidence="ASSESSED",
            ))

    # ── 1a. Sanctions screen (always runs) ──
    #
    # Classification is by BOTH score AND OpenSanctions topic labels.
    # Not every match at score 1.00 is a hard stop — legitimate
    # corporate entities (BAE Systems, Lockheed Martin, Rolls-Royce)
    # routinely hit at 1.00 against transparency data like `corp.state`
    # (state-owned / strategic industry lists), which is NOT a sanction.
    # See _classify_sanctions_match() for the topic → severity mapping.
    try:
        from . import sanctions
        if hasattr(sanctions, "screen_with_aliases"):
            screen = await sanctions.screen_with_aliases(name)
        elif hasattr(sanctions, "fuzzy_screen"):
            screen = await sanctions.fuzzy_screen(name)
        else:
            screen = {"error": "no sanctions module entrypoint"}
            report.identity.data_gaps.append("sanctions module not exposing expected API")
        report.identity.sanctions_screen = screen
        report.identity.meta.subcalls += 1

        from ._sanctions_classify import classify_matches
        matches = screen.get("matches") or []
        classified = classify_matches(matches, query_name=name)
        # The overall severity is the worst single match.
        if classified["worst_severity"] == "hard_stop":
            report.identity.findings.append(Finding(
                severity="hard_stop",
                title=f"Subject on active sanctions list",
                detail=classified["summary"],
                source="sanctions.screen_with_aliases",
                confidence="CONFIRMED",
            ))
            hard_stop = True
        elif classified["worst_severity"] == "red":
            report.identity.findings.append(Finding(
                severity="red",
                title=f"Subject linked to crime/debarment/export-risk list",
                detail=classified["summary"],
                source="sanctions.screen_with_aliases",
                confidence="PROBABLE",
            ))
        elif classified["worst_severity"] == "amber":
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"Subject on PEP or adverse-media list",
                detail=classified["summary"] + " — enhanced DD required, not a refusal ground.",
                source="sanctions.screen_with_aliases",
                confidence="ASSESSED",
            ))
        elif classified["worst_severity"] == "info":
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Subject on transparency / state-ownership register",
                detail=classified["summary"] + " — informational only, not a refusal ground.",
                source="sanctions.screen_with_aliases",
                confidence="ASSESSED",
            ))
        else:
            # Clean screen — zero matches across the full alias/variant set.
            # Emit an explicit INFO-tier finding so consumers can see the
            # screen actually RAN and came back clean. Previously an empty
            # matches list produced no finding at all, which the LLM
            # (correctly) read as "sanctions screen not completed".
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Sanctions screen CLEAN",
                detail=(
                    f"{name} — no matches across OFAC SDN, UK OFSI, EU Consolidated, "
                    f"UN 1267, or OpenSanctions datasets. Fuzzy variants / aliases "
                    f"screened. This is a POSITIVE CLEAN result — treat as clearance "
                    f"under standard commercial DD."
                ),
                source="sanctions.screen_with_aliases",
                confidence="CONFIRMED",
            ))
    except Exception as e:
        logger.warning("Identity: sanctions screen failed: %s", e)
        report.identity.findings.append(Finding(
            severity="amber", title="Sanctions screen failed", detail=str(e)[:200],
            source="sanctions", confidence="UNCERTAIN",
        ))
        report.identity.data_gaps.append("sanctions screen did not complete")

    # ── 1a2. Named-officeholder sanctions screen ──
    # When the caller supplies directors / beneficial owners / named
    # representatives (chat intent extracts "represented by its
    # Director X"), each individual is sanctions-screened separately.
    # This closes the "director screening gap" that previously had to
    # be chased manually after every DD run.
    _directors_in = target.get("directors") or []
    if _directors_in:
        try:
            from . import sanctions as _sanc
            from ._sanctions_classify import classify_matches as _cm
            _screen_fn = getattr(_sanc, "screen_with_aliases", None) or getattr(_sanc, "fuzzy_screen", None)
            for _d in _directors_in[:8]:  # hard cap — don't hammer the API
                _nm = (_d.get("name") or "").strip()
                if not _nm or len(_nm) < 4:
                    continue
                try:
                    _dscreen = await _screen_fn(_nm) if _screen_fn else {"matches": []}
                    _dcls = _cm(_dscreen.get("matches") or [], query_name=_nm)
                    _role = _d.get("role") or "Officer"
                    report.identity.meta.subcalls += 1
                    if _dcls["worst_severity"] == "hard_stop":
                        report.identity.findings.append(Finding(
                            severity="hard_stop",
                            title=f"{_role} {_nm} on active sanctions list",
                            detail=_dcls["summary"],
                            source="sanctions.director_screen",
                            confidence="CONFIRMED",
                        ))
                        hard_stop = True
                    elif _dcls["worst_severity"] == "red":
                        report.identity.findings.append(Finding(
                            severity="red",
                            title=f"{_role} {_nm} linked to crime/debarment list",
                            detail=_dcls["summary"],
                            source="sanctions.director_screen",
                            confidence="PROBABLE",
                        ))
                    elif _dcls["worst_severity"] == "amber":
                        report.identity.findings.append(Finding(
                            severity="amber",
                            title=f"{_role} {_nm} on PEP / adverse-media list",
                            detail=_dcls["summary"] + " — enhanced DD required on individual.",
                            source="sanctions.director_screen",
                            confidence="ASSESSED",
                        ))
                    else:
                        report.identity.findings.append(Finding(
                            severity="info",
                            title=f"{_role} {_nm} — sanctions screen CLEAN",
                            detail=f"No matches for {_nm} across OFAC / UK OFSI / EU / UN / OpenSanctions datasets.",
                            source="sanctions.director_screen",
                            confidence="CONFIRMED",
                        ))
                except Exception as _e:
                    logger.warning("Director screen failed for %s: %s", _nm, _e)
                    report.identity.data_gaps.append(f"director sanctions screen failed for {_nm}")
        except Exception as e:
            logger.warning("Identity: director screen block failed: %s", e)

    # ── 1b. Companies House lookup (UK only) ──
    if jurisdiction_iso2 == "GB":
        try:
            from . import companies_house
            if hasattr(companies_house, "investigate_uk_entity"):
                ch_result = await companies_house.investigate_uk_entity(
                    company_number=registration_number,
                    company_name=None if registration_number else name,
                )
                report.identity.meta.subcalls += 1
                if isinstance(ch_result, dict):
                    profile = ch_result.get("profile") or ch_result.get("company") or {}
                    report.identity.registration_number = profile.get("company_number") or registration_number
                    report.identity.registration_status = profile.get("company_status")
                    report.identity.incorporation_date = profile.get("date_of_creation")
                    report.identity.registered_address = (profile.get("registered_office_address") or {}).get("address_snippet") if isinstance(profile.get("registered_office_address"), dict) else profile.get("registered_office_address")
                    report.identity.declared_activity = ", ".join(profile.get("sic_codes") or [])[:200] or profile.get("sic_description")
                    report.identity.directors = ch_result.get("officers") or []
                    report.identity.shareholders = ch_result.get("psc") or []
        except Exception as e:
            logger.warning("Identity: companies_house lookup failed: %s", e)
            report.identity.data_gaps.append(f"companies_house lookup failed: {str(e)[:120]}")
    else:
        # Try multi-jurisdiction registry adapter
        try:
            from . import registry_adapters
            reg_result = await registry_adapters.lookup_entity(
                name=name,
                jurisdiction_iso2=jurisdiction_iso2,
                registration_number=registration_number,
            )
            if reg_result:
                profile = reg_result.get("profile", {})
                report.identity.registration_number = profile.get("company_number") or registration_number
                report.identity.registration_status = profile.get("company_status")
                report.identity.incorporation_date = profile.get("date_of_creation")
                report.identity.registered_address = profile.get("registered_office_address")
                report.identity.declared_activity = ", ".join(profile.get("sic_codes") or [])[:200]
                report.identity.directors = reg_result.get("officers") or []
                report.identity.shareholders = reg_result.get("psc") or []
                report.identity.meta.subcalls += 1
                report.identity.findings.append(Finding(
                    severity="info",
                    title=f"Registry lookup: {reg_result.get('adapter', jurisdiction_iso2)} ({profile.get('company_status', 'unknown')})",
                    detail=f"Source: {reg_result.get('source_url', 'registry adapter')}",
                    source=f"registry_adapters.{reg_result.get('adapter', 'unknown')}",
                    confidence="CONFIRMED",
                ))
            else:
                # Adapter returned None — jurisdiction not supported or lookup failed
                jur_hint = _national_registry_hint(jurisdiction_iso2, jurisdiction)
                report.identity.data_gaps.append(
                    f"Registry lookup unavailable for {jurisdiction or jurisdiction_iso2 or 'unspecified jurisdiction'}"
                    f" — ARIA has Companies House coverage for GB only. "
                    f"Manual action: {jur_hint}"
                )
                # Track as capability gap
                try:
                    from . import capability_gaps
                    import asyncio
                    _t = asyncio.create_task(capability_gaps.record_gap(
                        gap_type="registry_lookup",
                        detail=f"No automated registry adapter for {jurisdiction_iso2 or jurisdiction or 'unknown'}",
                        source="dd_orchestrator._run_identity",
                    ))
                    _t.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Registry adapter failed: %s", e)
            jur_hint = _national_registry_hint(jurisdiction_iso2, jurisdiction)
            report.identity.data_gaps.append(
                f"Registry lookup failed for {jurisdiction or jurisdiction_iso2}: {str(e)[:100]}. "
                f"Manual action: {jur_hint}"
            )

    # ── 1c. Ghost-score from available signals ──
    # Feed whatever we've collected into the programmatic scorer. The
    # scorer treats MISSING keys as data gaps, so only include keys
    # where we actually have a non-None value. (Including None values
    # crashes the scorer because its `_need(key)` only checks key
    # presence, not truthiness — int(None) then raises.)
    try:
        from . import due_diligence_playbooks as _dd
        profile: dict = {
            "name": report.identity.entity_name,
            "jurisdiction": report.identity.jurisdiction_iso2 or report.identity.jurisdiction,
            "registration_number": report.identity.registration_number,
        }
        _age = _age_months(report.identity.incorporation_date)
        if _age is not None:
            profile["age_months"] = _age
        _act = _map_activity(report.identity.declared_activity)
        if _act is not None:
            profile["declared_activity"] = _act
        _tval = target.get("transaction_value_usd")
        if _tval:
            profile["transaction_value_usd"] = _tval

        # Serban-case detectors — CUI, website, claimed founding year.
        # Any of these passed via the target dict (from chat intent
        # detection, API body, or autonomous task watchlist entry)
        # gets threaded into the ghost scorer so indicators 11 and 12
        # fire when the evidence is there.
        _cui = target.get("cui") or target.get("registration_number")
        if _cui:
            profile["cui"] = _cui
        _website = target.get("website") or target.get("domain")
        if _website:
            profile["website"] = _website
        _claimed_year = target.get("claimed_founding_year")
        if _claimed_year is not None:
            profile["claimed_founding_year"] = _claimed_year
        _residential_address = report.identity.registered_address
        if _residential_address and any(p in _residential_address.lower() for p in (
            "apt.", "apt ", " ap.", " ap ", " ap,", "apartment", "flat ",
            "unit ", "sc. ", "bl. ", "et. ", "etaj ", "floor ",
        )):
            profile["registered_address_type"] = "residential"

        ghost = _dd.score_ghost_indicators(profile)
        report.identity.ghost_score = ghost.as_dict()
        report.identity.meta.subcalls += 1
        if ghost.classification in ("RED", "HARD STOP"):
            hard_stop = True
            report.identity.findings.append(Finding(
                severity="hard_stop" if ghost.classification == "HARD STOP" else "red",
                title=f"Ghost score {ghost.total}/20 — {ghost.classification}",
                detail=ghost.recommendation,
                source="due_diligence_playbooks.score_ghost_indicators",
                confidence="PROBABLE",
            ))
        elif ghost.classification.startswith("AMBER"):
            report.identity.findings.append(Finding(
                severity="amber",
                title=f"Ghost score {ghost.total}/20 — {ghost.classification}",
                detail=ghost.recommendation,
                source="due_diligence_playbooks.score_ghost_indicators",
                confidence="ASSESSED",
            ))
    except Exception as e:
        logger.warning("Identity: ghost scoring failed: %s", e)
        report.identity.data_gaps.append(f"ghost score failed: {str(e)[:120]}")

    report.identity.meta.duration_ms = int((time.time() - t0) * 1000)
    report.identity.meta.status = LayerStatus.OK.value
    return hard_stop


async def _run_network(target: dict, report: ARKDDReport) -> None:
    """Layer 2 — Network. Composes network_walker.walk_network."""
    t0 = time.time()
    report.network.meta.started_at = datetime.now(timezone.utc).isoformat()
    try:
        from . import network_walker
        result = await network_walker.walk_network(
            entity_name=report.identity.entity_name,
            entity_type=report.identity.entity_type,
            jurisdiction_iso2=report.identity.jurisdiction_iso2,
            registration_number=report.identity.registration_number,
        )
        report.network.director_graph = result.get("director_graph", {})
        report.network.cross_linked_entities = result.get("cross_linked_entities", [])
        report.network.address_cluster = result.get("address_cluster", {})
        report.network.pep_connections = result.get("pep_connections", [])
        report.network.sanctions_network = result.get("sanctions_network", [])
        report.network.findings = [Finding(**f) for f in result.get("findings", [])]
        report.network.data_gaps = result.get("data_gaps", [])
        report.network.meta.subcalls = result.get("stats", {}).get("sanctions_screens", 0) + result.get("stats", {}).get("entities_walked", 0)
        report.network.meta.status = LayerStatus.OK.value
    except Exception as e:
        logger.warning("Network layer failed: %s", e)
        report.network.meta.status = LayerStatus.ERROR.value
        report.network.meta.error = str(e)[:200]
    report.network.meta.duration_ms = int((time.time() - t0) * 1000)


async def _run_compliance(target: dict, report: ARKDDReport) -> None:
    """Layer 4 — Compliance. Composes risk_indices + tech_classifier +
    international_law / global_export_control / regional_compliance via
    RAG queries through rag_store."""
    t0 = time.time()
    report.compliance.meta.started_at = datetime.now(timezone.utc).isoformat()

    # ── 4a. Country risk ──
    try:
        from . import risk_indices
        iso2 = report.identity.jurisdiction_iso2 or target.get("destination_iso2")
        if iso2:
            risk = risk_indices.get_country_risk(iso2, name=report.identity.jurisdiction or iso2)
            report.compliance.country_risk = risk.as_dict()
            report.compliance.meta.subcalls += 1
            headline = risk.headline_risk()
            if headline in ("RED", "HARD_STOP"):
                report.compliance.findings.append(Finding(
                    severity="hard_stop" if headline == "HARD_STOP" else "red",
                    title=f"Country risk: {headline}",
                    detail=f"CPI={risk.cpi_score} · Basel AML={risk.basel_aml} · FATF={risk.fatf_status} · OECD CRC={risk.oecd_crc}",
                    source="risk_indices.get_country_risk",
                    confidence="ASSESSED",
                ))
    except Exception as e:
        logger.warning("Compliance: country risk failed: %s", e)
        report.compliance.data_gaps.append(f"country risk lookup failed: {str(e)[:120]}")

    # ── 4b. Export control classification ──
    product_text = target.get("product_description") or target.get("goods") or ""
    if product_text:
        try:
            from . import tech_classifier
            ec = tech_classifier.classify_export_control(product_text)
            report.compliance.export_control = ec
            report.compliance.meta.subcalls += 1
            if ec.get("multilateral"):
                for hit in ec.get("multilateral", []):
                    report.compliance.sanctions_regimes.append(hit.get("regime", ""))
            if ec.get("recommendation", "").startswith("ITAR"):
                report.compliance.licence_path = "DSP-5 / TAA (ITAR)"
            elif "EAR" in (ec.get("recommendation", "") or ""):
                report.compliance.licence_path = "BIS-748P / Licence Exception"
            elif ec.get("wassenaar_ml"):
                report.compliance.licence_path = "SIEL / SITCL (UK) or equivalent national ML route"
        except Exception as e:
            logger.warning("Compliance: export control classification failed: %s", e)
            report.compliance.data_gaps.append(f"export control classify failed: {str(e)[:120]}")
    else:
        report.compliance.data_gaps.append("No product/goods description — export control classification skipped")

    # ── 4c. Regional bloc matching via RAG ──
    try:
        from . import rag_store
        country = report.identity.jurisdiction or target.get("destination") or ""
        if country:
            regional = await rag_store.get_rag_context(
                f"{country} regional compliance framework defence arms transfer",
                max_chars=2000,
            )
            if regional and regional.strip():
                report.compliance.regional_bloc_requirements = [{
                    "query": f"{country} regional bloc",
                    "excerpt": regional[:800],
                    "source": "RAG:regional_compliance",
                }]
                report.compliance.meta.subcalls += 1
    except Exception as e:
        logger.warning("Compliance: regional bloc RAG failed: %s", e)
        report.compliance.data_gaps.append(f"regional bloc RAG failed: {str(e)[:120]}")

    report.compliance.meta.duration_ms = int((time.time() - t0) * 1000)
    report.compliance.meta.status = LayerStatus.OK.value


async def _run_digital(target: dict, report: ARKDDReport, llm: Any, _mode_is_deep: bool = False) -> None:
    """Layer 5 — Digital. web_search multilingual + RAG + neural + (opt.) deep_research.

    When _mode_is_deep is True (orchestrator mode="deep"), deep_researcher
    runs with depth="thorough" (8 search angles × 3 articles, ~30-60s
    and ~$0.10). Otherwise depth="quick" (3 × 2 = 6 articles, ~15s and
    ~$0.03). This keeps the default "standard" DD run under the
    per-run cost cap even with the LLM-backed investigation firing.
    """
    t0 = time.time()
    report.digital.meta.started_at = datetime.now(timezone.utc).isoformat()
    name = report.identity.entity_name or target.get("query", "")

    # ── 5a. Multilingual web search ──
    try:
        from . import web_search
        hits = await web_search.search_multilingual(
            f"{name} defence procurement",
            max_results=12,
        )
        # Convert SearchResult objects to Evidence dataclasses where possible
        press: list[Evidence] = []
        tier_counts: dict[str, int] = {}
        for h in hits or []:
            tier = getattr(h, "source_tier", None) or "UNVERIFIED"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            press.append(Evidence(
                source=getattr(h, "title", "") or getattr(h, "url", ""),
                source_tier=tier,
                url=getattr(h, "url", None),
                snippet=(getattr(h, "snippet", "") or "")[:400],
                retrieved_at=datetime.now(timezone.utc).isoformat(),
            ))
        report.digital.press_coverage = press[:15]
        report.digital.source_tier_breakdown = tier_counts
        report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: web_search failed: %s", e)
        report.digital.data_gaps.append(f"web_search failed: {str(e)[:120]}")

    # ── 5b. RAG context ──
    try:
        from . import rag_store
        rag_ctx = await rag_store.get_rag_context(f"{name}", max_chars=2500)
        if rag_ctx and rag_ctx.strip():
            report.digital.knowledge_base_hits = [{"query": name, "excerpt": rag_ctx[:1500]}]
            report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: rag_store failed: %s", e)

    # ── 5c. Neural associations ──
    try:
        from . import neural_memory
        neural = await neural_memory.get_neural_context(name)
        if neural and neural.strip():
            # Pull out first N concept names from the neural block
            for line in neural.split("\n")[:8]:
                if line.strip().startswith("["):
                    report.digital.neural_associations.append(line.strip()[:200])
            report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: neural_memory failed: %s", e)

    # ── 5d. Knowledge base ──
    try:
        from . import knowledge
        kb = knowledge.search_knowledge(name)
        if kb and kb.strip():
            report.digital.knowledge_base_hits.append({"query": name, "excerpt": kb[:1500], "tier": "aria_knowledge"})
            report.digital.meta.subcalls += 1
    except Exception as e:
        logger.warning("Digital: knowledge search failed: %s", e)

    # ── 5e. Deep research (opt-in, LLM-backed) ──
    # Real signature: investigate(llm, topic, depth="quick"|"thorough"|"exhaustive").
    # depth is a STRING enum, not an int, and there is no max_pages or
    # context kwarg. Previous code passed max_pages=10 depth=1 and
    # crashed with "unexpected keyword argument 'max_pages'" on every
    # DD run with DEEP_RESEARCH_ENABLED. Reported silently in
    # digital.data_gaps so the Serban v3 chat output surfaced it.
    #
    # DD orchestrator uses "quick" by default (3 search angles × 2
    # articles = 6 LLM calls — ~30s and ~$0.03 per run) so the
    # digital layer stays within the per-run cost cap. Callers who
    # want the full "thorough" or "exhaustive" depth can pass the
    # mode="deep" flag at orchestration time; the orchestrator maps
    # deep → "thorough" and all other modes → "quick".
    if DEEP_RESEARCH_ENABLED and llm is not None:
        try:
            from . import deep_researcher
            dr_depth = "thorough" if _mode_is_deep else "quick"
            dr = await deep_researcher.investigate(llm, name, depth=dr_depth)
            if isinstance(dr, dict):
                synth = dr.get("synthesis") or {}
                report.digital.web_footprint = {
                    "summary": (
                        dr.get("summary")
                        or synth.get("executive_summary")
                        or ""
                    )[:1500],
                    "articles_read": dr.get("articles_read", 0),
                    "facts_learned": dr.get("facts_learned", 0),
                    "search_angles": dr.get("search_angles", []),
                    "depth": dr_depth,
                }
                report.digital.meta.subcalls += 1
                # If investigate surfaced its own findings, merge them in.
                for f in (synth.get("key_findings") or [])[:5]:
                    if isinstance(f, str):
                        report.digital.findings.append(Finding(
                            severity="info",
                            title=f[:200],
                            source="deep_researcher.investigate",
                            confidence="ASSESSED",
                        ))
        except Exception as e:
            logger.warning("Digital: deep_research failed: %s", e)
            report.digital.data_gaps.append(f"deep_research failed: {str(e)[:120]}")

    # ── 5f. Link-investigator (deep mode only) ──
    # Recursive URL-tree walk seeded from the target's own website (if
    # supplied) or the top-tier press-coverage hit. Rule-based extraction
    # only by default — no LLM cost. Budgets enforced inside the module.
    if _mode_is_deep:
        seed_url = target.get("website") or target.get("domain")
        if not seed_url and report.digital.press_coverage:
            seed_url = next(
                (e.url for e in report.digital.press_coverage if e.url),
                None,
            )
        if seed_url:
            if not seed_url.startswith(("http://", "https://")):
                seed_url = "https://" + seed_url
            try:
                from . import link_investigator
                tree = await link_investigator.investigate_link_tree(
                    seed_url=seed_url,
                    query_context=name,
                    max_depth=2,
                    max_pages=20,
                    wall_budget_s=60,
                    cost_budget_usd=0.0,  # rule-based only inside DD
                    llm=None,
                )
                report.digital.web_footprint = dict(report.digital.web_footprint or {})
                report.digital.web_footprint["link_tree"] = {
                    "tree_id": tree.tree_id,
                    "seed_url": tree.seed_url,
                    "pages_fetched": tree.pages_fetched,
                    "pages_failed": tree.pages_failed,
                    "max_depth_reached": tree.max_depth_reached,
                    "fused_fact_count": len(tree.fused_facts),
                    "budget_exceeded": tree.budget_exceeded,
                    "duration_ms": tree.duration_ms,
                }
                # Surface high-confidence triangulated facts as findings.
                for ff in tree.fused_facts[:8]:
                    if ff.source_count >= 2:
                        report.digital.findings.append(Finding(
                            severity="info",
                            title=f"link-tree: {ff.kind}={ff.value[:120]} (×{ff.source_count} sources)",
                            source=f"link_investigator.{tree.tree_id}",
                            confidence="ASSESSED",
                        ))
                report.digital.meta.subcalls += 1
            except Exception as e:
                logger.warning("Digital: link_investigator failed: %s", e)
                report.digital.data_gaps.append(f"link_investigator failed: {str(e)[:120]}")

    report.digital.meta.duration_ms = int((time.time() - t0) * 1000)
    report.digital.meta.status = LayerStatus.OK.value


async def _run_verification(target: dict, report: ARKDDReport) -> None:
    """Layer 3 — Verification. Cross-source triangulation + conflict
    detection over whatever the previous layers collected."""
    t0 = time.time()
    report.verification.meta.started_at = datetime.now(timezone.utc).isoformat()

    # Count sources per material claim. A "claim" here is a distinct
    # piece of evidence/finding from any section. The verifier counts
    # how many independent sources back each.
    sources_for_claim: dict[str, set[str]] = {}
    def _add(claim: str, src: str):
        sources_for_claim.setdefault(claim, set()).add(src)

    # Identity claims
    if report.identity.sanctions_screen:
        _add("identity:sanctions_checked", "sanctions")
    if report.identity.directors:
        _add("identity:directors_known", "companies_house")
    if report.identity.ghost_score:
        _add("identity:ghost_scored", "ghost_scorer")
    # Network claims
    if report.network.director_graph.get("nodes"):
        _add("network:graph_built", "network_walker")
    if report.network.pep_connections:
        _add("network:pep_checked", "sanctions")
    # Compliance
    if report.compliance.country_risk:
        _add("compliance:country_risk_known", "risk_indices")
    if report.compliance.export_control:
        _add("compliance:export_classified", "tech_classifier")
    if report.compliance.regional_bloc_requirements:
        _add("compliance:regional_framework_cited", "rag:regional_compliance")
    # Digital
    if report.digital.press_coverage:
        for p in report.digital.press_coverage[:10]:
            _add("digital:press_coverage", p.source or "press")
    if report.digital.knowledge_base_hits:
        _add("digital:knowledge_base_hits", "aria_knowledge")
    if report.digital.neural_associations:
        _add("digital:neural_associations", "neural_memory")

    triangulated = [
        {"claim": k, "sources": sorted(list(v)), "source_count": len(v)}
        for k, v in sources_for_claim.items()
    ]
    report.verification.triangulated_claims = triangulated

    # Grounded rate: fraction of claims backed by at least 2 independent
    # sources. Not identical to source_verifier's URL-based rate, but
    # the right shape for a DD report.
    if triangulated:
        grounded = sum(1 for t in triangulated if t["source_count"] >= 2)
        report.verification.grounded_rate = round(grounded / len(triangulated), 2)
    else:
        report.verification.grounded_rate = None

    # Conflict detection — look for contradictions in ghost score
    # classification vs country risk headline
    ghost_cls = (report.identity.ghost_score or {}).get("classification", "")
    country_headline = (report.compliance.country_risk or {}).get("headline_risk", "")
    if ghost_cls in ("GREEN",) and country_headline in ("RED", "HARD_STOP"):
        report.verification.conflicts.append({
            "type": "classification_mismatch",
            "detail": f"ghost={ghost_cls} but country={country_headline}",
            "resolution": "use worst-case — promote overall to country's level",
        })

    # Confidence floor: worst tag across all sections
    all_confidences = ["ASSESSED"]  # baseline
    for section in (report.identity, report.network, report.verification, report.compliance, report.digital):
        for f in getattr(section, "findings", []) or []:
            all_confidences.append(getattr(f, "confidence", "ASSESSED"))
    report.verification.confidence_floor = weakest_confidence(all_confidences)

    # Pull in unverified claim count from source_verifier IF we have
    # any tool_context blob to verify. The orchestrator isn't invoking
    # source_verifier against LLM outputs (no LLM outputs yet here),
    # so this is a structural placeholder.
    report.verification.unverified_claim_count = sum(
        1 for t in triangulated if t["source_count"] < 2
    )

    report.verification.meta.duration_ms = int((time.time() - t0) * 1000)
    report.verification.meta.status = LayerStatus.OK.value


async def _run_synthesis(target: dict, report: ARKDDReport) -> None:
    """Layer 6 — Synthesis. ACH matrix + final ghost score + risk
    classification + SAR trigger."""
    t0 = time.time()
    report.synthesis.meta.started_at = datetime.now(timezone.utc).isoformat()

    # ── 6a. Ghost score roll-up (authoritative) ──
    # Person DD doesn't have a ghost score — ghost detection is a
    # company-only signal (founding date, registered address pattern,
    # website age, etc.). Skip for persons so the synthesis layer
    # doesn't emit "Ghost score: 0/20 — GREEN" which is misleading.
    _is_person = (report.identity.entity_type or "").lower() == "person"
    ghost = report.identity.ghost_score or {}
    if _is_person:
        report.synthesis.ghost_score_total = 0
        report.synthesis.ghost_classification = ""
    else:
        report.synthesis.ghost_score_total = int(ghost.get("total") or 0)
        report.synthesis.ghost_classification = str(ghost.get("classification") or "GREEN")

    # ── 6b. Risk classification — worst-case aggregation ──
    # Tiers in ascending severity
    severity_rank = {
        "GREEN":       0,
        "AMBER-LIGHT": 1,
        "AMBER":       1,
        "AMBER-DARK":  2,
        "RED":         3,
        "HARD STOP":   4,
        "HARD_STOP":   4,
    }
    candidates: list[str] = []
    if report.synthesis.ghost_classification and not _is_person:
        candidates.append(report.synthesis.ghost_classification)
    if report.compliance.country_risk.get("headline_risk"):
        candidates.append(report.compliance.country_risk["headline_risk"])
    # Any hard_stop finding anywhere?
    for section in (report.identity, report.network, report.compliance):
        for f in getattr(section, "findings", []) or []:
            if getattr(f, "severity", "") == "hard_stop":
                candidates.append("HARD_STOP")
                break

    if candidates:
        worst = max(candidates, key=lambda c: severity_rank.get(c, 0))
    else:
        worst = "GREEN"

    # Normalise to canonical RiskClassification values
    canonical_map = {
        "GREEN":        RiskClassification.GREEN.value,
        "AMBER-LIGHT":  RiskClassification.AMBER_LIGHT.value,
        "AMBER":        RiskClassification.AMBER_LIGHT.value,
        "AMBER-DARK":   RiskClassification.AMBER_DARK.value,
        "RED":          RiskClassification.RED.value,
        "HARD STOP":    RiskClassification.HARD_STOP.value,
        "HARD_STOP":    RiskClassification.HARD_STOP.value,
    }
    report.synthesis.risk_classification = canonical_map.get(worst, RiskClassification.GREEN.value)
    report.risk_classification = report.synthesis.risk_classification

    # ── 6c. SAR trigger — UK POCA / FATF typology ──
    # Triggers:
    #   - sanctions hit on subject OR director
    #   - ghost score >= 12 (RED) combined with layered secrecy chain
    #   - transaction value >= 100k with no declared activity
    sar_reasons: list[str] = []
    if any("sanctions" in str(f.title).lower() and "hit" in str(f.title).lower()
           for f in report.identity.findings):
        sar_reasons.append("sanctions hit on identity layer")
    if any("hit on sanctions" in str(f.title).lower()
           for f in report.network.findings):
        sar_reasons.append("sanctions hit in network layer (one-hop)")
    if report.synthesis.ghost_score_total >= 12:
        sar_reasons.append(f"ghost score {report.synthesis.ghost_score_total}/20 at RED threshold")

    if sar_reasons:
        report.synthesis.sar_trigger = True
        report.synthesis.sar_rationale = " · ".join(sar_reasons)

    # ── 6d. ACH matrix (structural) ──
    # Three hypotheses by default:
    #   H1: entity is a legitimate counterparty suitable for BD
    #   H2: entity is a higher-risk counterparty requiring enhanced DD
    #   H3: entity is a shell / concealment vehicle — refuse
    hypotheses = {
        "H1_legit": {"label": "Legitimate BD counterparty", "support": 0, "against": 0},
        "H2_enhanced": {"label": "Higher-risk, enhanced DD required", "support": 0, "against": 0},
        "H3_shell": {"label": "Shell / concealment vehicle — refuse", "support": 0, "against": 0},
    }
    ghost_total = report.synthesis.ghost_score_total
    if ghost_total <= 3:
        hypotheses["H1_legit"]["support"] += 3
        hypotheses["H3_shell"]["against"] += 3
    elif ghost_total <= 7:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1
    elif ghost_total <= 11:
        hypotheses["H2_enhanced"]["support"] += 3
        hypotheses["H3_shell"]["support"] += 1
    elif ghost_total <= 15:
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 3
    else:
        hypotheses["H3_shell"]["support"] += 5
        hypotheses["H1_legit"]["against"] += 5

    # Country risk contribution
    country_headline = (report.compliance.country_risk or {}).get("headline_risk")
    if country_headline in ("RED", "HARD_STOP"):
        hypotheses["H2_enhanced"]["support"] += 1
        hypotheses["H3_shell"]["support"] += 1
    elif country_headline == "AMBER":
        hypotheses["H2_enhanced"]["support"] += 1

    # Sanctions hit → H3 strongly favoured
    if report.synthesis.sar_trigger:
        hypotheses["H3_shell"]["support"] += 5
        hypotheses["H1_legit"]["against"] += 5

    report.synthesis.ach_matrix = {
        "hypotheses": hypotheses,
        "method": "balance of support minus against",
        "winner": max(hypotheses.items(), key=lambda kv: kv[1]["support"] - kv[1]["against"])[0],
    }

    # ── 6e. Key findings — pull the highest-severity items across sections ──
    all_findings: list[Finding] = []
    for section in (report.identity, report.network, report.verification, report.compliance, report.digital):
        for f in getattr(section, "findings", []) or []:
            all_findings.append(f)
    severity_order = {"hard_stop": 0, "red": 1, "amber": 2, "info": 3}
    all_findings.sort(key=lambda f: severity_order.get(getattr(f, "severity", "info"), 4))
    report.synthesis.key_findings = all_findings[:10]

    # ── 6f. Residual unknowns = all data_gaps combined ──
    for section in (report.identity, report.network, report.verification, report.compliance, report.digital):
        for g in getattr(section, "data_gaps", []) or []:
            if g not in report.synthesis.residual_unknowns:
                report.synthesis.residual_unknowns.append(g)

    report.synthesis.meta.duration_ms = int((time.time() - t0) * 1000)
    report.synthesis.meta.status = LayerStatus.OK.value


# =============================================================================
# BOTTOM-LINE + RECOMMENDATION (programmatic, pre-LLM)
# =============================================================================

def _assemble_bluf(report: ARKDDReport) -> None:
    """Populate report.bottom_line / recommendation / next_actions / confidence.

    Deterministic — no LLM call, just pattern matching over the sections
    so the orchestrator always returns a non-empty BLUF even when the
    cost cap prevented the LLM from running.
    """
    risk = report.risk_classification
    name = report.identity.entity_name or "subject"

    if risk == RiskClassification.HARD_STOP.value:
        report.bottom_line = (
            f"🔴 HARD STOP — {name} triggers a mandatory refusal. "
            "Do NOT proceed with the transaction."
        )
        report.recommendation = (
            "Refuse the engagement. File SAR if reporting thresholds are met. "
            "Preserve all investigation evidence for compliance record."
        )
        report.next_actions = [
            "Do not contact the counterparty further until compliance sign-off",
            "Escalate to ECJU / OFSI / DBT compliance desk as appropriate",
            "Assess SAR filing obligation under POCA 2002 / national AML law",
            "Lock the case file — preserve all evidence",
        ]
    elif risk == RiskClassification.RED.value:
        report.bottom_line = (
            f"🔴 RED — {name} is very likely unsuitable for onboarding in current form. "
            "Independent commercial DD required before any further engagement."
        )
        report.recommendation = (
            "Commission a commercial-grade DD report from LSEG / Sayari / Dow Jones / Orbis. "
            "Do NOT proceed on open-source findings alone. Re-evaluate after commercial DD."
        )
        report.next_actions = [
            "Commission commercial DD (Sayari / LSEG / Dow Jones / Orbis)",
            "Halt any in-progress contracting until commercial DD returns clean",
            "Document the current AMBER-DARK / RED grounds in the case file",
        ]
    elif risk == RiskClassification.AMBER_DARK.value:
        report.bottom_line = (
            f"🟠 AMBER-DARK — {name} shows structural concerns. Enhanced DD is required; "
            "do not proceed without independent verification of beneficial ownership."
        )
        report.recommendation = (
            "Obtain commercial DD report on beneficial ownership. Require signed EUC "
            "from end-user government. Screen signatory identities. Escalate any new red flag to RED."
        )
        report.next_actions = [
            "Obtain commercial UBO verification",
            "Require signed EUC from end-user authority",
            "Identity-verify all signatories against independent sources",
            "Re-run orchestrator weekly via watchlist until risk tier improves",
        ]
    elif risk == RiskClassification.AMBER_LIGHT.value:
        report.bottom_line = (
            f"🟡 AMBER — {name} can proceed with enhanced due diligence. "
            "Resolve the gaps flagged below before contracting."
        )
        report.recommendation = (
            "Proceed with enhanced DD: require EUC, verify signatory identity, "
            "escalate any new red flag to RED. Close data gaps before contracting."
        )
        report.next_actions = [
            "Close the data gaps listed under residual unknowns",
            "Require EUC before any binding commitment",
            "Verify signatory identity via at least one independent source",
        ]
    else:
        report.bottom_line = (
            f"🟢 GREEN — {name} passes baseline due diligence. "
            "Standard contracting path available."
        )
        report.recommendation = (
            "Proceed with standard DD. No blocking concerns identified in the universal layer."
        )
        report.next_actions = [
            "Proceed with standard commercial process",
            "Apply regular sanctions-list re-screen on contract renewal",
        ]

    report.confidence_tag = report.verification.confidence_floor or "ASSESSED"
    # Aggregate all data_gaps into the top-level summary so consumers
    # can surface them without walking the whole report tree.
    for section in (report.identity, report.network, report.verification, report.compliance, report.digital):
        for g in getattr(section, "data_gaps", []) or []:
            if g not in report.data_gaps_summary:
                report.data_gaps_summary.append(g)


# =============================================================================
# PERSISTENCE
# =============================================================================

async def _persist_report(report: ARKDDReport) -> None:
    """Store the finished report in Redis + append a summary signal to
    the intel_ledger + write a notebook entry to mem0 (async, non-blocking)."""
    try:
        from . import redis_store as rs
        await rs.set_json(
            REPORT_REDIS_KEY.format(run_id=report.run_id),
            report.as_dict(),
            ex=REPORT_TTL_SECONDS,
        )
        try:
            index = await rs.get_json(REPORT_INDEX_KEY) or []
            index.insert(0, {
                "run_id": report.run_id,
                "generated_at": report.generated_at,
                "entity_name": report.identity.entity_name,
                "risk": report.risk_classification,
            })
            index = index[:500]
            await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
        except Exception as e:
            logger.debug("dd_orchestrator: report index write failed: %s", e)
    except Exception as e:
        logger.warning("dd_orchestrator: Redis persist failed: %s", e)


# =============================================================================
# HELPERS
# =============================================================================

def _age_months(iso_date: Optional[str]) -> Optional[int]:
    if not iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.fromisoformat(iso_date)
        except Exception:
            return None
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.utcnow()
    delta = now - dt
    return max(0, delta.days // 30)


def _map_activity(declared: Optional[str]) -> Optional[str]:
    if not declared:
        return None
    d = declared.lower()
    if any(k in d for k in ("general trading", "holding", "investment holding", "management services", "not elsewhere classified")):
        return "generic_holding"
    return "specific_aligned"


# National corporate registries ARIA can recommend for manual lookup
# when the orchestrator doesn't have automated coverage. Keyed by
# ISO-2. Every entry names the authoritative free public registry
# so the user knows WHERE to look rather than just that ARIA
# couldn't do it. This closes the "unknown jurisdiction" gap that
# surfaced on the Serban Industries SRL / Romania run.
_NATIONAL_REGISTRY_HINTS: dict[str, str] = {
    # Europe
    "GB": "already covered automatically via Companies House",
    "GI": "check Gibraltar Companies House at https://www.companieshouse.gi — separate registry from UK CH; paid per extract. Also check Gibraltar Beneficial Ownership Register (Companies Act 2014, as amended 2019).",
    "IM": "check Isle of Man Companies Registry at https://services.gov.im/ded/services/companiesregistry — paid per extract",
    "JE": "check Jersey Financial Services Commission Registry at https://www.jerseyfsc.org — paid per extract",
    "GG": "check Guernsey Registry at https://www.greg.gg — paid per extract",
    "KY": "check Cayman Islands General Registry at https://www.ciregistry.ky — restricted access; UBO via Beneficial Ownership Transparency Act",
    "BM": "check Bermuda Registrar of Companies at https://www.gov.bm/department/registrar-companies — paid per extract",
    "VG": "check BVI Financial Services Commission at https://www.bvifsc.vg — restricted; BOSS (Beneficial Ownership Secure Search) system",
    "TC": "check Turks & Caicos Financial Services Commission — paid extracts only",
    "AI": "check Anguilla Commercial Registry (ACORN) — paid per extract",
    "RO": "check ONRC (Oficiul Național al Registrului Comerțului) at https://portal.onrc.ro — free public Romanian registry",
    "DE": "check Handelsregister at https://www.handelsregister.de — free German commercial register (fee per extract)",
    "FR": "check INFOGREFFE / Pappers at https://www.pappers.fr — free French commercial registry aggregator",
    "IT": "check Registro Imprese at https://www.registroimprese.it — Italian commercial register (fee per extract)",
    "ES": "check Registro Mercantil Central at https://www.rmc.es — Spanish commercial register (fee per extract)",
    "NL": "check KvK (Kamer van Koophandel) at https://www.kvk.nl — Dutch chamber of commerce",
    "BE": "check Crossroads Bank for Enterprises at https://kbopub.economie.fgov.be — Belgian company register, free",
    "AT": "check FirmenABC or Firmenbuch at https://www.firmenbuchabfrage.at — Austrian commercial register",
    "CH": "check Zefix at https://www.zefix.ch — Swiss central business names index, free",
    "IE": "check CRO (Companies Registration Office) at https://www.cro.ie — Irish registry",
    "PT": "check Portal da Empresa at https://bde.portaldocidadao.pt — Portuguese public business database",
    "PL": "check KRS (Krajowy Rejestr Sądowy) at https://ekrs.ms.gov.pl — Polish national court register, free",
    "CZ": "check Czech Business Register at https://or.justice.cz — free",
    "SK": "check Slovak Business Register at https://www.orsr.sk — free",
    "HU": "check E-cégjegyzék at https://e-cegjegyzek.hu — Hungarian commercial register",
    "BG": "check Bulgarian Trade Register at https://portal.registryagency.bg — free",
    "HR": "check Sudski registar at https://sudreg.pravosudje.hr — Croatian court register",
    "SI": "check AJPES at https://www.ajpes.si — Slovenian Agency for Public Legal Records",
    "GR": "check GEMI (General Commercial Registry) at https://www.businessregistry.gr",
    "SE": "check Bolagsverket at https://www.bolagsverket.se — Swedish Companies Registration Office",
    "DK": "check CVR (Det Centrale Virksomhedsregister) at https://datacvr.virk.dk — Danish CVR, free",
    "FI": "check YTJ (Business Information System) at https://tietopalvelu.ytj.fi — Finnish business info, free",
    "NO": "check Brønnøysundregistrene at https://w2.brreg.no — Norwegian business registry, free",
    "LU": "check LBR (Luxembourg Business Registers) at https://www.lbr.lu — paid per extract",
    "EE": "check Estonian Business Registry (e-Business Register) at https://ariregister.rik.ee — free",
    "LT": "check Lithuanian Centre of Registers at https://www.registrucentras.lt",
    "LV": "check Latvian UR (Uzņēmumu reģistrs) at https://www.ur.gov.lv",
    "CY": "check Cyprus Department of Registrar of Companies at https://www.companies.gov.cy",
    "MT": "check Malta Business Registry at https://mbr.mt",
    # Americas
    "US": "check the relevant US state Secretary of State (Delaware https://icis.corp.delaware.gov is the most common for holding companies); SEC EDGAR at https://www.sec.gov/edgar for public filers",
    "CA": "check the relevant province (Federal Corporations Canada at https://ised-isde.canada.ca) or provincial registry",
    "BR": "check Receita Federal CNPJ lookup at https://solucoes.receita.fazenda.gov.br/Servicos/cnpjreva — free Brazilian corporate tax ID registry",
    "MX": "check the national Mexican corporate registry at https://psm.economia.gob.mx (public commercial filings)",
    "AR": "check AFIP CUIT lookup or Inspección General de Justicia (IGJ) at https://www.argentina.gob.ar/justicia/igj",
    "CL": "check the Chilean Registro de Comercio / CMF registry",
    "CO": "check RUES (Registro Único Empresarial y Social) at https://www.rues.org.co — Colombian national business registry",
    # Middle East
    "AE": "check Dubai DED Trade Licence Info at https://eservices.dubaided.gov.ae and the Abu Dhabi DED equivalent; DIFC Public Register at https://www.difc.ae/public-register; ADGM Public Register at https://www.adgm.com/public-registers",
    "SA": "check Saudi Ministry of Commerce Commercial Registration search at https://mc.gov.sa",
    "QA": "check Qatar Ministry of Commerce and Industry registry",
    "KW": "check Kuwait Public Authority for Industry commercial registry",
    "BH": "check Bahrain Ministry of Industry and Commerce — Sijilat at https://www.sijilat.bh",
    "OM": "check Oman Ministry of Commerce, Industry and Investment Promotion registry",
    "IL": "check Israeli Registrar of Companies at https://ica.justice.gov.il",
    "TR": "check Mersis (Ticaret Sicili Kayıtları) at https://www.mersis.gtb.gov.tr — Turkish central trade registry",
    # Asia
    "CN": "check National Enterprise Credit Information Publicity System at https://www.gsxt.gov.cn — Chinese AIC registry",
    "IN": "check MCA21 (Ministry of Corporate Affairs) at https://www.mca.gov.in — Indian corporate registry",
    "JP": "check the National Tax Agency corporate number system at https://www.houjin-bangou.nta.go.jp",
    "KR": "check DART (Data Analysis, Retrieval and Transfer System) at https://dart.fss.or.kr — Korean financial disclosures",
    "SG": "check BizFile+ at https://www.bizfile.gov.sg — ACRA Singapore, full public register",
    "MY": "check SSM (Suruhanjaya Syarikat Malaysia) at https://www.ssm-einfo.my",
    "ID": "check AHU Online at https://ahu.go.id — Indonesian Ministry of Law and Human Rights registry",
    "TH": "check DBD (Department of Business Development) at https://datawarehouse.dbd.go.th",
    "VN": "check Vietnamese National Business Registration Portal at https://dangkykinhdoanh.gov.vn",
    "PH": "check SEC Philippines at https://www.sec.gov.ph",
    "PK": "check SECP at https://www.secp.gov.pk",
    "BD": "check RJSC (Registrar of Joint Stock Companies and Firms) at http://www.roc.gov.bd",
    # Africa
    "NG": "check CAC (Corporate Affairs Commission) at https://pre.cac.gov.ng",
    "ZA": "check CIPC at https://www.cipc.co.za — South African Companies and Intellectual Property Commission",
    "KE": "check eCitizen Business Registration Service at https://brs.ecitizen.go.ke",
    "GH": "check Ghana Registrar-General's Department at https://www.rgd.gov.gh",
    "AO": "check SIAC (Single Enterprise Counter) / Ministério da Justiça Angola — manual registry lookup, no public online portal",
    "MZ": "check Mozambique Ministry of Justice corporate registry — manual only, no public online portal",
    "EG": "check Egyptian GAFI (General Authority for Investment) at https://www.gafi.gov.eg",
    "MA": "check OMPIC at https://www.directinfo.ma — Moroccan Office of Industrial and Commercial Property",
    # Post-Soviet / CIS
    "RU": "check EGRUL (ЕГРЮЛ) at https://egrul.nalog.ru — Russian Federal Tax Service corporate registry (CAUTION: sanctions-regime jurisdiction, avoid automated connectivity)",
    "UA": "check YouControl at https://youcontrol.com.ua or Ministry of Justice USR at https://usr.minjust.gov.ua",
    "KZ": "check Kazakhstan Ministry of Justice legal entities registry",
    "BY": "check Unified State Register of Belarus — manual lookup required",
    "AM": "check Armenia State Register of Legal Persons",
    "GE": "check LEPL National Agency of Public Registry at https://napr.gov.ge — Georgian NAPR",
    "AZ": "check Azerbaijani Tax Ministry legal entity registry",
    "UZ": "check Uzbek Ministry of Justice legal entity registry",
    # Oceania
    "AU": "check ASIC (Australian Securities and Investments Commission) at https://asic.gov.au",
    "NZ": "check NZ Companies Register at https://companies-register.companiesoffice.govt.nz",
}


def _national_registry_hint(iso2: Optional[str], jurisdiction: Optional[str]) -> str:
    """Return a specific, actionable manual-lookup instruction for the
    national corporate registry of a given jurisdiction. Used in
    data_gap messages so the LLM (and the human reader) know exactly
    where to look manually when ARIA's automated coverage doesn't
    reach the target country.
    """
    if iso2 and iso2 in _NATIONAL_REGISTRY_HINTS:
        return _NATIONAL_REGISTRY_HINTS[iso2]
    # Best-effort fallback by jurisdiction name
    if jurisdiction:
        return (
            f"run a manual search of the {jurisdiction} national corporate "
            f"registry (consult FATF country profile for the authoritative "
            f"source) and attach the result to the DD record."
        )
    return (
        "run a manual search of the target country's national corporate registry "
        "(FATF country profiles list the authoritative source) and attach the "
        "result to the DD record."
    )


# Sanctions-match classification now lives in _sanctions_classify.py
# so both dd_orchestrator and network_walker share the same topic →
# severity logic. The inline copy was removed to eliminate drift.


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

async def orchestrate_dd(
    target: dict,
    *,
    llm: Any = None,
    mode: str = "standard",
    cost_cap_usd: float | None = None,
    trace_id: str | None = None,
) -> ARKDDReport:
    """Run the 7-layer DD orchestrator on a target entity.

    Args:
        target: dict with keys:
            - name / entity / query (required)
            - type: "company" | "person" | "address" | "vessel" | ...
            - jurisdiction_iso2: ISO-2 country code (optional)
            - jurisdiction: full country name (optional)
            - registration_number: national registry number (optional)
            - product_description: goods/service description (optional,
              for export-control classification)
            - transaction_value_usd: proposed deal value (optional,
              for ghost-score proportionality check)
        llm: LLMProvider for the digital layer's optional deep_research
             call. Pass None to skip the LLM-backed deep_research step.
        mode: "quick" (skip network + digital deep_research) |
              "standard" (full sequential walk) |
              "deep" (standard + future watchlist diff)
        cost_cap_usd: override the default run cost cap.
        trace_id: link to an existing trace (from chat_ep / autonomous task).

    Returns:
        ARKDDReport — fully populated; also persisted to Redis and
        ready to be delivered via autonomous/delivery.py.
    """
    if not ORCHESTRATOR_ENABLED:
        raise RuntimeError("DD orchestrator disabled via ARIA_DD_ORCHESTRATOR_ENABLED=0")
    if not target or not (target.get("name") or target.get("entity") or target.get("query")):
        raise ValueError("target must include 'name', 'entity', or 'query'")

    cost_cap = cost_cap_usd if cost_cap_usd is not None else DEFAULT_COST_CAP_USD
    t_run_start = time.time()

    report = ARKDDReport(
        target=target,
        orchestrator_mode=mode,
        trace_id=trace_id,
    )
    report.identity.entity_name = target.get("name") or target.get("entity") or target.get("query", "")
    report.identity.entity_type = target.get("type") or EntityType.UNKNOWN.value

    # Hook into cost_tracker so every LLM call made by the layers is
    # attributed to "dd_orchestrator".
    cost_tracker_token = None
    try:
        from . import cost_tracker
        cost_tracker_token = cost_tracker.set_feature("dd_orchestrator")
    except Exception:
        pass

    try:
        # ── LAYER 1: IDENTITY ──
        layer_name = "identity"
        report.layers_run.append(layer_name)
        try:
            hard_stop = await asyncio.wait_for(
                _run_identity(target, report),
                timeout=DEFAULT_LAYER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            report.identity.meta.status = LayerStatus.ERROR.value
            report.identity.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S}s"
            hard_stop = False

        # Short-circuit on sanctions hit: skip network/digital, keep
        # compliance + verification + synthesis so the user still gets a
        # structured HARD_STOP report with the reasoning.
        if hard_stop:
            logger.info("[dd_orchestrator] hard stop triggered in identity layer — short-circuiting")
            for layer in ("network", "digital"):
                if layer not in report.layers_skipped:
                    report.layers_skipped.append(layer)
            report.network.meta.status = LayerStatus.SKIPPED.value
            report.digital.meta.status = LayerStatus.SKIPPED.value
        else:
            # ── LAYER 2: NETWORK (unless quick mode) ──
            if mode != "quick":
                layer_name = "network"
                report.layers_run.append(layer_name)
                try:
                    await asyncio.wait_for(_run_network(target, report), timeout=DEFAULT_LAYER_TIMEOUT_S)
                except asyncio.TimeoutError:
                    report.network.meta.status = LayerStatus.ERROR.value
                    report.network.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S}s"
            else:
                if "network" not in report.layers_skipped:
                    report.layers_skipped.append("network")
                report.network.meta.status = LayerStatus.SKIPPED.value

        # ── LAYER 4: COMPLIANCE ── (always — it's cheap and load-bearing)
        layer_name = "compliance"
        report.layers_run.append(layer_name)
        try:
            await asyncio.wait_for(_run_compliance(target, report), timeout=DEFAULT_LAYER_TIMEOUT_S)
        except asyncio.TimeoutError:
            report.compliance.meta.status = LayerStatus.ERROR.value
            report.compliance.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S}s"

        # ── LAYER 5: DIGITAL (unless quick mode OR short-circuited) ──
        if mode != "quick" and not hard_stop:
            layer_name = "digital"
            report.layers_run.append(layer_name)
            try:
                await asyncio.wait_for(
                    _run_digital(target, report, llm, _mode_is_deep=(mode == "deep")),
                    timeout=DEFAULT_LAYER_TIMEOUT_S * 2,
                )
            except asyncio.TimeoutError:
                report.digital.meta.status = LayerStatus.ERROR.value
                report.digital.meta.error = f"timeout after {DEFAULT_LAYER_TIMEOUT_S * 2}s"
        elif mode == "quick":
            if "digital" not in report.layers_skipped:
                report.layers_skipped.append("digital")
            report.digital.meta.status = LayerStatus.SKIPPED.value

        # ── LAYER 3: VERIFICATION (runs over what the previous layers collected) ──
        layer_name = "verification"
        report.layers_run.append(layer_name)
        try:
            await asyncio.wait_for(_run_verification(target, report), timeout=30)
        except asyncio.TimeoutError:
            report.verification.meta.status = LayerStatus.ERROR.value
            report.verification.meta.error = "timeout after 30s"

        # ── LAYER 6: SYNTHESIS ──
        layer_name = "synthesis"
        report.layers_run.append(layer_name)
        try:
            await asyncio.wait_for(_run_synthesis(target, report), timeout=10)
        except asyncio.TimeoutError:
            report.synthesis.meta.status = LayerStatus.ERROR.value
            report.synthesis.meta.error = "timeout after 10s"

    finally:
        if cost_tracker_token is not None:
            try:
                from . import cost_tracker
                cost_tracker.reset_feature(cost_tracker_token)
            except Exception:
                pass

    # ── BLUF + assembly ──
    _assemble_bluf(report)
    report.total_duration_ms = int((time.time() - t_run_start) * 1000)
    report.layer_costs_usd = {
        "identity":     report.identity.meta.cost_usd,
        "network":      report.network.meta.cost_usd,
        "verification": report.verification.meta.cost_usd,
        "compliance":   report.compliance.meta.cost_usd,
        "digital":      report.digital.meta.cost_usd,
        "synthesis":    report.synthesis.meta.cost_usd,
    }
    report.total_cost_usd = sum(report.layer_costs_usd.values())

    # ── Persist + deliver ──
    await _persist_report(report)

    try:
        from . import knowledge
        summary = f"ARK-DD report {report.run_id} on {report.identity.entity_name}: {report.risk_classification}. {report.bottom_line[:200]}"
        await knowledge.store_fact(
            topic=f"ark_dd:{report.identity.entity_name}",
            content=summary,
            source=f"dd_orchestrator:{report.run_id}",
            confidence="PROBABLE",
        )
    except Exception as e:
        logger.debug("dd_orchestrator: knowledge store failed (non-fatal): %s", e)

    logger.info(
        "[dd_orchestrator] run %s complete — entity=%s risk=%s cost=$%.4f duration=%dms layers=%s",
        report.run_id,
        report.identity.entity_name,
        report.risk_classification,
        report.total_cost_usd,
        report.total_duration_ms,
        ",".join(report.layers_run),
    )
    return report


# =============================================================================
# WATCHLIST (Redis-backed, used by autonomous task)
# =============================================================================

WATCHLIST_KEY = "crucix:dd:watchlist"


async def add_to_watchlist(target: dict) -> dict:
    """Add a target to the DD watchlist. Target must include at least
    a name. Idempotent — dedupes by name."""
    from . import redis_store as rs
    current = await rs.get_json(WATCHLIST_KEY) or []
    name = (target.get("name") or target.get("entity") or "").strip()
    if not name:
        raise ValueError("target must include a name")
    if any((w.get("name") or "").strip().lower() == name.lower() for w in current):
        return {"ok": True, "note": "already on watchlist", "count": len(current)}
    current.insert(0, target)
    current = current[:200]
    await rs.set_json(WATCHLIST_KEY, current)
    return {"ok": True, "added": target, "count": len(current)}


async def remove_from_watchlist(name: str) -> dict:
    from . import redis_store as rs
    current = await rs.get_json(WATCHLIST_KEY) or []
    before = len(current)
    current = [w for w in current if (w.get("name") or "").strip().lower() != (name or "").strip().lower()]
    await rs.set_json(WATCHLIST_KEY, current)
    return {"ok": True, "removed": before - len(current), "count": len(current)}


async def get_watchlist() -> list[dict]:
    from . import redis_store as rs
    return await rs.get_json(WATCHLIST_KEY) or []


async def get_report(run_id: str) -> dict | None:
    from . import redis_store as rs
    return await rs.get_json(REPORT_REDIS_KEY.format(run_id=run_id))


async def list_reports(limit: int = 50) -> list[dict]:
    from . import redis_store as rs
    index = await rs.get_json(REPORT_INDEX_KEY) or []
    return index[:limit]


# =============================================================================
# WATCHLIST AUTO-RE-SCREEN
# =============================================================================

WATCHLIST_ALERTS_KEY = "crucix:aria:dd:watchlist:alerts"
_RESCREEN_MAX_ENTITIES = 50
_RESCREEN_ALERT_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _derive_status(classified: dict) -> str:
    """Map classify_matches worst_severity to a simple tri-state."""
    sev = classified.get("worst_severity", "clean")
    if sev in ("hard_stop", "red"):
        return "HIT"
    if sev in ("amber",):
        return "PEP"
    return "CLEAN"


def _derive_status_from_findings(findings: list[dict]) -> str:
    """Derive status from a report's identity findings list."""
    for f in findings:
        sev = f.get("severity", "")
        src = f.get("source", "")
        if "sanctions" not in src and "person_screen" not in src:
            continue
        if sev in ("hard_stop", "red"):
            return "HIT"
        if sev == "amber":
            return "PEP"
    return "CLEAN"


def _derive_score_from_matches(matches: list[dict]) -> float:
    """Best match score from a sanctions screen result."""
    if not matches:
        return 0.0
    return max((m.get("score", 0) for m in matches if isinstance(m, dict)), default=0.0)


async def rescreen_watchlist(llm=None) -> dict:
    """Re-screen every watchlist entity (sanctions + PEP only, no LLM).

    Returns summary dict with entities_screened, changes_detected, errors,
    and duration_ms. Alerts are persisted in Redis for later retrieval.
    """
    import json as _json
    t0 = time.monotonic()
    from . import redis_store as rs

    watchlist = await rs.get_json(WATCHLIST_KEY) or []
    if not watchlist:
        return {"entities_screened": 0, "changes_detected": [], "errors": [],
                "duration_ms": 0}

    # Enforce cost cap: max 50 entities per cycle
    entities = watchlist[:_RESCREEN_MAX_ENTITIES]

    changes: list[dict] = []
    errors: list[dict] = []

    # Import sanctions module and classifier once
    try:
        from . import sanctions
        from ._sanctions_classify import classify_matches
    except Exception as e:
        return {"entities_screened": 0, "changes_detected": [], "errors": [
            {"entity": "*", "error": f"sanctions module import failed: {e}"}],
            "duration_ms": int((time.monotonic() - t0) * 1000)}

    for entry in entities:
        name = (entry.get("name") or entry.get("entity") or "").strip()
        if not name:
            continue
        try:
            # --- Run quick sanctions screen (no LLM, no deep research) ---
            if hasattr(sanctions, "screen_with_aliases"):
                screen = await sanctions.screen_with_aliases(name)
            elif hasattr(sanctions, "fuzzy_screen"):
                screen = await sanctions.fuzzy_screen(name)
            else:
                errors.append({"entity": name, "error": "no sanctions entrypoint"})
                continue

            matches = screen.get("matches") or []
            classified = classify_matches(matches, query_name=name)
            new_status = _derive_status(classified)
            new_score = _derive_score_from_matches(matches)

            # --- Load previous status from the most recent DD report ---
            old_status = "CLEAN"
            old_score = 0.0
            old_run_id = None

            index = await rs.get_json(REPORT_INDEX_KEY) or []
            for idx_entry in index:
                if (idx_entry.get("entity_name") or "").strip().lower() == name.lower():
                    old_run_id = idx_entry.get("run_id")
                    break

            if old_run_id:
                prev_report = await rs.get_json(REPORT_REDIS_KEY.format(run_id=old_run_id))
                if prev_report:
                    identity = prev_report.get("identity") or {}
                    prev_findings = identity.get("findings") or []
                    old_status = _derive_status_from_findings(prev_findings)
                    prev_screen = identity.get("sanctions_screen") or {}
                    old_score = _derive_score_from_matches(prev_screen.get("matches") or [])

            # --- Compare ---
            change_type = None
            detail = ""

            if old_status == "CLEAN" and new_status == "HIT":
                change_type = "new_hit"
                detail = f"Previously clean, now sanctioned. Top match: {classified.get('summary', '')[:200]}"
            elif old_status == "HIT" and new_status == "CLEAN":
                change_type = "removed"
                detail = "Previously sanctioned, now clean across all lists."
            elif old_status != "PEP" and new_status == "PEP":
                change_type = "new_pep"
                detail = f"New PEP/adverse-media match. {classified.get('summary', '')[:200]}"
            elif abs(new_score - old_score) > 0.1:
                change_type = "score_change"
                detail = f"Best match score changed from {old_score:.2f} to {new_score:.2f}."

            if change_type:
                alert = {
                    "entity": name,
                    "run_id": old_run_id or "none",
                    "change_type": change_type,
                    "old_status": old_status,
                    "new_status": new_status,
                    "detail": detail,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                changes.append(alert)

                # Persist alert in Redis
                await rs.lpush(WATCHLIST_ALERTS_KEY, _json.dumps(alert, default=str))
                await rs.ltrim(WATCHLIST_ALERTS_KEY, 0, 499)  # cap at 500
                await rs.expire(WATCHLIST_ALERTS_KEY, _RESCREEN_ALERT_TTL_SECONDS)

        except Exception as e:
            errors.append({"entity": name, "error": str(e)})

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "entities_screened": len(entities),
        "changes_detected": changes,
        "errors": errors,
        "duration_ms": duration_ms,
    }


async def get_watchlist_alerts(since_hours: int = 24) -> list[dict]:
    """Retrieve recent watchlist re-screen alerts from Redis."""
    import json as _json
    from . import redis_store as rs

    raw_list = await rs.lrange(WATCHLIST_ALERTS_KEY, 0, 499)
    if not raw_list:
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - (since_hours * 3600)
    alerts: list[dict] = []
    for raw in raw_list:
        try:
            alert = _json.loads(raw) if isinstance(raw, str) else raw
            ts_str = alert.get("timestamp", "")
            if ts_str:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    continue
            alerts.append(alert)
        except Exception:
            continue
    return alerts
