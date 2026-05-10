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
import re
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

# ── Jurisdiction inference from phone, address, registration number ──────────

_PHONE_PREFIX_TO_ISO2 = {
    "+421": "SK", "+420": "CZ", "+48": "PL", "+40": "RO", "+36": "HU",
    "+43": "AT", "+49": "DE", "+44": "GB", "+33": "FR", "+34": "ES",
    "+39": "IT", "+90": "TR", "+55": "BR", "+234": "NG", "+971": "AE",
    "+91": "IN", "+350": "GI", "+351": "PT", "+966": "SA", "+962": "JO",
    "+20": "EG", "+254": "KE", "+27": "ZA", "+244": "AO", "+258": "MZ",
    "+233": "GH",
    # +1 is US/CA/Caribbean. Default to US for our deal flow; address
    # keywords will override if the entity is clearly Canadian.
    "+1": "US",
}

_ADDRESS_KEYWORDS_TO_ISO2 = {
    "slovak republic": "SK", "slovensko": "SK", "slovakia": "SK",
    "czech republic": "CZ", "česko": "CZ", "czechia": "CZ",
    "poland": "PL", "polska": "PL", "romania": "RO", "românia": "RO",
    "hungary": "HU", "türkiye": "TR", "turkey": "TR", "brazil": "BR",
    "brasil": "BR", "nigeria": "NG", "united arab emirates": "AE",
    "gibraltar": "GI", "united kingdom": "GB", "england": "GB",
    "india": "IN", "angola": "AO", "mozambique": "MZ",
    "south africa": "ZA", "kenya": "KE", "ghana": "GH",
    "saudi arabia": "SA", "jordan": "JO", "egypt": "EG",
    "france": "FR", "germany": "DE", "deutschland": "DE",
    "spain": "ES", "españa": "ES", "italy": "IT", "italia": "IT",
    "portugal": "PT", "austria": "AT", "österreich": "AT",
    "united states": "US", "usa": "US", "u.s.a.": "US",
    # Common US state markers — any of these in the address strongly
    # implies jurisdiction = US. Fine-grained state detection is the
    # responsibility of registry_adapters._detect_us_state.
    "florida": "US", "delaware": "US", "california": "US",
    "new york": "US", "texas": "US", "nevada": "US", "wyoming": "US",
}

_ISO2_TO_COUNTRY = {v: k.title() for k, v in _ADDRESS_KEYWORDS_TO_ISO2.items()}
_ISO2_TO_COUNTRY.update({
    "SK": "Slovak Republic", "CZ": "Czech Republic", "GB": "United Kingdom",
    "AE": "United Arab Emirates", "ZA": "South Africa", "SA": "Saudi Arabia",
})


def _infer_jurisdiction(target: dict, name: str, reg_number: str | None) -> str | None:
    """Infer jurisdiction ISO2 from phone, address, email domain, or reg number format."""
    # 1. Phone prefix (most reliable)
    phone = target.get("phone") or target.get("tel") or ""
    for prefix, iso2 in sorted(_PHONE_PREFIX_TO_ISO2.items(), key=lambda x: -len(x[0])):
        if phone.startswith(prefix):
            return iso2

    # 2. Address keywords
    address = " ".join(filter(None, [
        target.get("address", ""),
        target.get("jurisdiction", ""),
        name,
    ])).lower()
    for keyword, iso2 in _ADDRESS_KEYWORDS_TO_ISO2.items():
        if keyword in address:
            return iso2

    # 3. Email domain → country TLD
    email = target.get("email") or ""
    tld_match = re.search(r'\.([a-z]{2})$', email.lower())
    if tld_match:
        tld = tld_match.group(1)
        _tld_map = {"sk": "SK", "cz": "CZ", "pl": "PL", "ro": "RO", "hu": "HU",
                     "tr": "TR", "br": "BR", "uk": "GB", "de": "DE", "fr": "FR",
                     "es": "ES", "it": "IT", "pt": "PT", "at": "AT", "ae": "AE",
                     "ng": "NG", "in": "IN", "za": "ZA", "ke": "KE"}
        if tld in _tld_map:
            return _tld_map[tld]

    # 4. Registration number format
    if reg_number:
        clean = reg_number.replace(" ", "")
        # German Handelsregister: HRB / HRA / HRG / GnR / PR / VR + digits
        if re.match(r"^HR[ABG]\d", clean, re.IGNORECASE) or re.match(r"^(GnR|PR|VR)\d", clean, re.IGNORECASE):
            return "DE"
        # Slovak IČO: 6-8 pure digits
        if clean.isdigit() and 6 <= len(clean) <= 8:
            # Could be SK or CZ — default SK if address hints
            if any(kw in address for kw in ("sk", "slovak", "bratislava", "košice", "čachtice")):
                return "SK"
            if any(kw in address for kw in ("cz", "czech", "praha", "brno")):
                return "CZ"
            # Default to SK for ambiguous 6-8 digit IDs (most common in our deal flow)
            return "SK"

    return None


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
        # ── Family/associate edge detection ─────────────────────────────
        # 2026-04-12: if any sanctions match has family/associate relationships,
        # the subject inherits elevated risk (e.g., spouse of sanctioned person).
        for pm in classified.get("per_match") or []:
            match_obj = next((m for m in all_matches if isinstance(m, dict) and m.get("name") == pm.get("name")), None)
            if not match_obj:
                continue
            rels = match_obj.get("relationships") or []
            for rel in rels[:3]:
                kind = rel.get("kind", "relatedTo")
                target = rel.get("target", "unknown")
                # Family of a sanctioned entity inherits red risk
                inherited_sev = "red" if pm["severity"] in ("hard_stop", "red") else "amber"
                report.identity.findings.append(Finding(
                    severity=inherited_sev,
                    title=f"{name} — {kind} link to {target}",
                    detail=(
                        f"OpenSanctions reports {pm['name']} has a '{kind}' "
                        f"relationship with '{target}'. If the related party is "
                        f"sanctioned, {name} inherits elevated risk via association."
                    ),
                    source="sanctions.family_edges",
                    confidence="ASSESSED",
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

    # ── Auto-detect jurisdiction from clues when not provided ──
    if not jurisdiction_iso2:
        jurisdiction_iso2 = _infer_jurisdiction(target, name, registration_number)
        if jurisdiction_iso2:
            jurisdiction = jurisdiction or _ISO2_TO_COUNTRY.get(jurisdiction_iso2, "")
            logger.info("Jurisdiction inferred: %s → %s", jurisdiction_iso2, jurisdiction)

    # ── Also try IČO / ico key as registration number for SK/CZ ──
    if not registration_number:
        for _k in ("ico", "ičo", "IČO", "ICO"):
            if target.get(_k):
                registration_number = str(target[_k]).replace(" ", "")
                if not jurisdiction_iso2:
                    jurisdiction_iso2 = "SK"
                    jurisdiction = "Slovak Republic"
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

        # ── Virtual-office / mail-drop detection ──
        # Catches CMRA addresses (e.g. North Miami Beach corridor,
        # Cheyenne WY mass-registration), PMB markers, and registered-
        # agent towers. Added 2026-04-17 after the F3 / SERBAN DD
        # learnings — a US LLC at a virtual-office address should
        # always carry an explicit signal.
        try:
            from . import virtual_office_registry
            _vo = virtual_office_registry.check_address(supplied_address)
            if _vo.get("is_virtual_office"):
                _sev = "red" if _vo.get("risk") == "high" else "amber"
                _provider = _vo.get("provider") or "virtual-office / mail-drop"
                _signals = " ".join(_vo.get("signals") or [])
                _notes = _vo.get("notes") or ""
                report.identity.findings.append(Finding(
                    severity=_sev,
                    title=f"Registered address is a virtual office / mail drop",
                    detail=(
                        f"'{supplied_address}' matches a known virtual-office "
                        f"or CMRA pattern: {_provider}. {_signals} {_notes} "
                        f"Operating business is unlikely to be physically at "
                        f"this address. Require counterparty to disclose the "
                        f"actual principal place of business before proceeding."
                    ).strip(),
                    source="dd_orchestrator.virtual_office_detector",
                    confidence="ASSESSED",
                ))
        except Exception as _vo_err:
            logger.debug("Virtual-office check failed (non-fatal): %s", _vo_err)

    # ── FinCEN BOI caveat for US entities ──
    # MOVED OUT of the `if supplied_address` guard 2026-04-17 PM so the
    # caveat also fires on URL-based / name-only DDs where the address
    # may only surface later from the registry adapter. US state registries
    # disclose registered agent + managers but NOT ultimate beneficial
    # ownership. UBO lives in FinCEN BOI filings (Corporate Transparency
    # Act, 2024) — not public.
    _all_address_hint = " ".join(filter(None, [
        (supplied_address or ""),
        (report.identity.registered_address or ""),
        (target.get("address") or ""),
    ])).lower()
    _jur_hint = (
        (target.get("jurisdiction_iso2") or "")
        or (jurisdiction_iso2 or "")
        or (target.get("jurisdiction") or "")
        or (jurisdiction or "")
    ).upper()
    _is_us = (
        _jur_hint in ("US", "USA", "UNITED STATES")
        or any(kw in _all_address_hint for kw in (
            "united states", "usa", "florida", "delaware", "california",
            "new york", "texas", "nevada", "wyoming",
        ))
        # LLC shape in entity name + no other jurisdiction inferred
        or ((name or "").lower().rstrip(".").endswith((" llc", ",llc", " inc", " inc."))
            and not _jur_hint)
    )
    if _is_us:
        report.identity.data_gaps.append(
            "UBO not visible on US public registry — US state Secretary "
            "of State records disclose registered agent + managers only. "
            "Ultimate beneficial ownership lives in FinCEN BOI filings "
            "(Corporate Transparency Act, effective 2024) which are NOT "
            "public. Require the counterparty to provide a copy of its "
            "FinCEN BOI report during KYC before contracting."
        )

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
            # Build detailed breakdown of each info-level match (datasets + topics)
            _info_matches = classified.get("per_match") or []
            _info_detail_parts = [classified["summary"]]
            for _im in _info_matches[:5]:
                if _im.get("severity") == "info" and not _im.get("noise_filtered"):
                    _ds = ", ".join(_im.get("datasets", [])[:3]) or "unspecified"
                    _tp = ", ".join(_im.get("topics", [])[:3]) or "untagged"
                    _info_detail_parts.append(
                        f"  → {_im.get('name', '?')} (score {_im.get('score', 0):.2f}, datasets: {_ds}, topics: {_tp})"
                    )
            report.identity.findings.append(Finding(
                severity="info",
                title=f"Transparency/state-ownership matches ({len([m for m in _info_matches if m.get('severity') == 'info'])})",
                detail="\n".join(_info_detail_parts) + "\n— informational only, not a refusal ground.",
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

    # ── 1a1. Primary-source parallel screen (SEC, OFAC, OFSI, UN, WB, ACLED) ──
    # Added 2026-04-18 after the DD-depth audit. The OpenSanctions call
    # above is convenient but aggregation-lagged; these six direct primary
    # sources run in parallel and surface findings with canonical
    # citations. Each source is wrapped in an individual try so one
    # failure cannot block the others. ~2-4s added to identity layer
    # worst-case (all six fetched in parallel, cached heavily).
    try:
        from .sources import (
            sec_edgar as _src_sec,
            ofac_sdn as _src_ofac,
            fcdo_sanctions as _src_ofsi,
            un_sc_sanctions as _src_un,
            worldbank_debarred as _src_wb,
            acled as _src_acled,
        )
        _src_results = await asyncio.gather(
            _src_sec.lookup(name),
            _src_ofac.lookup(name),
            _src_ofsi.lookup(name),
            _src_un.lookup(name),
            _src_wb.lookup(name),
            _src_acled.lookup(name, country=(jurisdiction or "")),
            return_exceptions=True,
        )
        _src_labels = ["sec_edgar", "ofac_sdn", "uk_ofsi", "un_sc", "wb_debarred", "acled"]

        for _lbl, _r in zip(_src_labels, _src_results):
            if isinstance(_r, Exception):
                logger.debug("Identity: primary source %s raised: %s", _lbl, _r)
                report.identity.data_gaps.append(f"primary source {_lbl} did not complete")
                continue
            if not isinstance(_r, dict):
                continue
            report.identity.meta.subcalls += 1

            if not _r.get("ok"):
                if _r.get("error"):
                    report.identity.data_gaps.append(
                        f"{_lbl}: {str(_r.get('error'))[:120]}"
                    )
                continue

            _hits = _r.get("hits") or []
            if not _hits:
                continue

            # ── Severity mapping per source (semantics differ) ──
            if _lbl == "ofac_sdn":
                _best = _hits[0]
                report.identity.findings.append(Finding(
                    severity="hard_stop",
                    title=f"OFAC SDN match: {_best.get('name','?')}",
                    detail=(
                        f"Match score {_best.get('_match_score', 0):.2f}. "
                        f"Programme(s): {', '.join(_best.get('programs', []))}. "
                        f"Designated {_best.get('designation_date','?')}. "
                        f"50-percent-rule applies to subsidiaries."
                    ),
                    source="sources.ofac_sdn",
                    confidence="CONFIRMED" if _best.get("_match_score", 0) >= 0.9 else "PROBABLE",
                ))
                hard_stop = True

            elif _lbl == "un_sc":
                _best = _hits[0]
                report.identity.findings.append(Finding(
                    severity="hard_stop",
                    title=f"UN Security Council match: {_best.get('name','?')}",
                    detail=(
                        f"Match score {_best.get('_match_score', 0):.2f}. "
                        f"Regime: {_best.get('regime','?')}. "
                        f"Listed {_best.get('designation_date','?')}."
                    ),
                    source="sources.un_sc_sanctions",
                    confidence="CONFIRMED" if _best.get("_match_score", 0) >= 0.9 else "PROBABLE",
                ))
                hard_stop = True

            elif _lbl == "uk_ofsi":
                _best = _hits[0]
                report.identity.findings.append(Finding(
                    severity="hard_stop",
                    title=f"UK OFSI match: {_best.get('name','?')}",
                    detail=(
                        f"Match score {_best.get('_match_score', 0):.2f}. "
                        f"Regime: {_best.get('regime','?')}. "
                        f"Group ID {_best.get('group_id','?')}. "
                        f"Designated {_best.get('designation_date','?')}."
                    ),
                    source="sources.fcdo_sanctions",
                    confidence="CONFIRMED" if _best.get("_match_score", 0) >= 0.9 else "PROBABLE",
                ))
                hard_stop = True

            elif _lbl == "wb_debarred":
                _active = [h for h in _hits if h.get("status") == "active"]
                if _active:
                    _best = _active[0]
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"World Bank debarment (active): {_best.get('name','?')}",
                        detail=(
                            f"Grounds: {_best.get('grounds','?')}. "
                            f"Ineligible {_best.get('ineligibility_from','?')} → "
                            f"{_best.get('ineligibility_to','?')}. "
                            f"Cross-recognised by AfDB/AsDB/EBRD/IDB under MCEA 2010."
                        ),
                        source="sources.worldbank_debarred",
                        confidence="PROBABLE",
                    ))
                else:
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"World Bank debarment (expired): {_hits[0].get('name','?')}",
                        detail=(
                            f"Historical debarment, ineligibility ended "
                            f"{_hits[0].get('ineligibility_to','?')}. "
                            f"Relevant context, not a current refusal ground."
                        ),
                        source="sources.worldbank_debarred",
                        confidence="ASSESSED",
                    ))

            elif _lbl == "sec_edgar":
                _red_hits = [h for h in _hits if (h.get("severity_hint") or "").startswith("RED")]
                _amber_hits = [h for h in _hits if (h.get("severity_hint") or "").startswith("AMBER")]
                _info_hits = [h for h in _hits if
                              not (h.get("severity_hint") or "").startswith(("RED", "AMBER"))]

                if _red_hits:
                    _b = _red_hits[0]
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"SEC 8-K material event: {_b.get('company_name','?')}",
                        detail=(
                            f"{_b.get('severity_hint','?')}. "
                            f"Filed {_b.get('filing_date','?')}. "
                            f"Items: {_b.get('items','?')}."
                        ),
                        source="sources.sec_edgar",
                        confidence="CONFIRMED",
                    ))
                if _amber_hits:
                    _b = _amber_hits[0]
                    report.identity.findings.append(Finding(
                        severity="amber",
                        title=f"SEC filing flagged: {_b.get('company_name','?')}",
                        detail=f"{_b.get('severity_hint','?')}. Filed {_b.get('filing_date','?')}.",
                        source="sources.sec_edgar",
                        confidence="PROBABLE",
                    ))
                if _info_hits and not (_red_hits or _amber_hits):
                    report.identity.findings.append(Finding(
                        severity="info",
                        title=f"SEC filings found: {len(_info_hits)} recent ({_info_hits[0].get('company_name','?')})",
                        detail=(
                            f"Most recent: {_info_hits[0].get('form','?')} filed "
                            f"{_info_hits[0].get('filing_date','?')}. "
                            f"Full filings available for financial DD review."
                        ),
                        source="sources.sec_edgar",
                        confidence="CONFIRMED",
                    ))

            elif _lbl == "acled":
                _sev = _r.get("severity_hint") or ""
                if _sev.startswith("RED"):
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"ACLED: entity named in political-violence events",
                        detail=(
                            f"{len(_hits)} events in last 180d involve similar actor name. "
                            f"Most recent: {_hits[0].get('event_date','?')} "
                            f"{_hits[0].get('event_type','?')} in {_hits[0].get('country','?')}."
                        ),
                        source="sources.acled",
                        confidence="PROBABLE",
                    ))
                elif _sev.startswith("INFO"):
                    report.identity.findings.append(Finding(
                        severity="info",
                        title="ACLED: operational-environment signal",
                        detail=_sev,
                        source="sources.acled",
                        confidence="ASSESSED",
                    ))
    except Exception as _e:
        # R-F118 (2026-05-09): surface the cause in the data_gap so the
        # operator sees WHY in the DD report (and on the chat output)
        # instead of only in fly logs. Previous message was just
        # "did not complete" — operator had to dig fly logs to learn
        # whether it was an import error, a network issue, or an arg
        # mismatch.
        logger.warning(
            "Identity: primary-source parallel screen failed: %s: %s",
            type(_e).__name__, _e, exc_info=True,
        )
        report.identity.data_gaps.append(
            f"primary-source parallel screen did not complete: "
            f"{type(_e).__name__}: {str(_e)[:160]}"
        )

    # ── 1a2. Extract contact names from email / phone / explicit fields ──
    # When the user provides emails like branislav.takac@btg.sk or
    # explicit contact_name / contact fields, extract person names and
    # add them to the director screening list.
    _directors_in = list(target.get("directors") or [])
    _contact_names_extracted = set()
    for _email_field in ("email", "contact_email"):
        _em = target.get(_email_field) or ""
        if "@" in _em:
            _local = _em.split("@")[0]
            # firstname.lastname or firstname_lastname patterns
            _parts = re.split(r'[._\-]', _local)
            if len(_parts) >= 2:
                _extracted = " ".join(p.capitalize() for p in _parts if len(p) > 1)
                if len(_extracted) > 4 and _extracted not in _contact_names_extracted:
                    _contact_names_extracted.add(_extracted)
                    _directors_in.append({"name": _extracted, "role": "Contact (from email)"})
    for _cn_field in ("contact_name", "contact", "representative"):
        _cn = (target.get(_cn_field) or "").strip()
        if _cn and len(_cn) > 3 and _cn not in _contact_names_extracted:
            _contact_names_extracted.add(_cn)
            _directors_in.append({"name": _cn, "role": "Contact"})

    # ── Named-officeholder sanctions screen ──
    # When the caller supplies directors / beneficial owners / named
    # representatives, each individual is sanctions-screened separately.
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

                    # ── PSC-reverse: screen each beneficial owner against sanctions ──
                    # 2026-04-12: "Which people control this company, and are any of
                    # them sanctioned?" Surfaces hidden risk from beneficial owners.
                    psc_list = ch_result.get("psc") or []
                    if psc_list:
                        from . import sanctions as _san
                        from ._sanctions_classify import classify_matches as _cm_psc, SEVERITY_RANK
                        for psc_member in psc_list[:10]:  # cap at 10 to control cost
                            psc_name = psc_member.get("name") or ""
                            if not psc_name or len(psc_name) < 3:
                                continue
                            if psc_member.get("ceased_on"):
                                continue  # skip former PSCs
                            try:
                                psc_matches = await _san.screen_entity(psc_name)
                                report.identity.meta.subcalls += 1
                                if psc_matches:
                                    psc_classified = _cm_psc(psc_matches, query_name=psc_name)
                                    psc_worst = psc_classified["worst_severity"]
                                    if SEVERITY_RANK.get(psc_worst, 0) >= SEVERITY_RANK.get("amber", 1):
                                        natures = ", ".join(psc_member.get("natures_of_control") or [])[:120]
                                        report.identity.findings.append(Finding(
                                            severity=psc_worst,
                                            title=f"PSC (beneficial owner) {psc_name} flagged: {psc_worst}",
                                            detail=(
                                                f"Person of Significant Control '{psc_name}' "
                                                f"(control: {natures or 'not specified'}) "
                                                f"matched: {psc_classified['summary'][:300]}"
                                            ),
                                            source="sanctions.psc_reverse",
                                            confidence="PROBABLE",
                                        ))
                                        if psc_worst == "hard_stop":
                                            hard_stop = True
                            except Exception as _psc_e:
                                logger.debug("PSC screen failed for %s: %s", psc_name, _psc_e)
        except Exception as e:
            logger.warning("Identity: companies_house lookup failed: %s", e)
            report.identity.data_gaps.append(f"companies_house lookup failed: {str(e)[:120]}")

        # ── 1c. Financial DD — shell company detection (UK) ──────────
        # 2026-04-13: pulls Companies House filing history, detects dormant,
        # micro-entity, overdue, formation agent addresses.
        if registration_number:
            try:
                from . import financial_dd
                fin_profile = await financial_dd.get_financial_profile(registration_number)
                report.identity.meta.subcalls += 1
                if not fin_profile.get("error"):
                    for f in financial_dd.financial_findings(fin_profile):
                        report.identity.findings.append(Finding(**f))
                    report.identity.attributes = report.identity.attributes if hasattr(report.identity, 'attributes') and report.identity.attributes else {}
                    if isinstance(report.identity.attributes, dict):
                        report.identity.attributes["financial"] = {
                            "accounts_type": fin_profile.get("accounts_type"),
                            "shell_risk_score": fin_profile.get("shell_risk_score"),
                            "shell_indicators": fin_profile.get("shell_indicators", []),
                            "financial_summary": fin_profile.get("financial_summary"),
                        }
            except Exception as _fin_err:
                logger.debug("Financial DD failed (non-fatal): %s", _fin_err)
    else:
        # Try multi-jurisdiction registry adapter
        try:
            from . import registry_adapters
            _addr_for_adapter = (
                target.get("registered_address")
                or target.get("address")
                or report.identity.registered_address
                or ""
            )
            reg_result = await registry_adapters.lookup_entity(
                name=name,
                jurisdiction_iso2=jurisdiction_iso2,
                registration_number=registration_number,
                address=_addr_for_adapter,
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
                # ── Virtual-office re-check on registry-returned address ──
                # If the registry returned an address and the supplied_address
                # check earlier did not fire (e.g. no address was supplied
                # initially), now is the time to check. This catches the
                # F3 case where the address only comes from Sunbiz/registry.
                _reg_address = profile.get("registered_office_address") or ""
                # data_gaps may include a US stub — also probe for any FL/DE
                # address pattern there so the detector has material.
                if _reg_address:
                    try:
                        from . import virtual_office_registry
                        _vo = virtual_office_registry.check_address(_reg_address)
                        if _vo.get("is_virtual_office"):
                            _sev = "red" if _vo.get("risk") == "high" else "amber"
                            report.identity.findings.append(Finding(
                                severity=_sev,
                                title=f"Virtual-office match on registry address",
                                detail=(
                                    f"'{_reg_address}' matches known virtual-office "
                                    f"corridor: {_vo.get('provider') or '?'}. "
                                    f"{' '.join(_vo.get('signals') or [])}"
                                ),
                                source="dd_orchestrator.virtual_office_detector.registry",
                                confidence="ASSESSED",
                            ))
                    except Exception as _vo_err:
                        logger.debug("Virtual-office check on registry addr failed: %s", _vo_err)
            else:
                # Adapter returned None — jurisdiction not supported or lookup failed
                jur_hint = _national_registry_hint(jurisdiction_iso2, jurisdiction)
                report.identity.data_gaps.append(
                    f"Registry lookup unavailable for {jurisdiction or jurisdiction_iso2 or 'unspecified jurisdiction'}"
                    f" — ARIA has Companies House coverage for GB only. "
                    f"Manual action: {jur_hint}"
                )
                # Track as capability gap.
                # R-F150 2026-05-10: removed local `import asyncio` here.
                # Python's local-binding rule treats any `import X` inside
                # a function as a marker that X is local FOR THE WHOLE
                # FUNCTION — so this import shadowed the module-level
                # `import asyncio` at line 46, and the earlier reference
                # at line 732 (asyncio.gather in primary-source parallel
                # screen) raised UnboundLocalError. Live evidence
                # 2026-05-10 11:39:28: dd_orchestrator.py:732 traceback.
                # Same pattern as F28 (lifespan_smoke_test_required.md).
                # Module-level import is sufficient — no re-import needed.
                try:
                    from . import capability_gaps
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
            pre_resolved_officers=report.identity.directors or [],
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

        # ── Multi-hop entity graph (2026-04-13) ──────────────────────
        # Build a traversable graph from the walk result and run multi-hop
        # risk search. This finds indirect sanctions exposure through shared
        # directors, PSCs, and family/associate relationships.
        try:
            from . import entity_graph
            graph = entity_graph.build_from_walk_result(
                seed_name=report.identity.entity_name,
                seed_type=report.identity.entity_type,
                seed_jurisdiction=report.identity.jurisdiction_iso2,
                seed_reg_number=report.identity.registration_number,
                walk_result=result,
                sanctions_results=report.identity.sanctions_screen.get("matches") if hasattr(report.identity, "sanctions_screen") and report.identity.sanctions_screen else None,
            )
            # Multi-hop search: find all paths to flagged entities (up to 3 hops)
            hop_results = graph.multi_hop_search(
                start_id=list(graph.nodes.keys())[0] if graph.nodes else "",
                max_depth=3,
            )
            # Store graph summary in report
            report.network.director_graph["multi_hop"] = hop_results.get("risk_summary", {})
            report.network.director_graph["graph_stats"] = hop_results.get("graph_stats", {})

            # Generate findings from multi-hop paths
            for path in hop_results.get("paths", [])[:5]:
                if path.get("terminal_risk") in ("red", "hard_stop"):
                    hops = path.get("length", 0)
                    terminal = path.get("nodes", [{}])[-1] if path.get("nodes") else {}
                    report.network.findings.append(Finding(
                        severity=path["terminal_risk"],
                        title=f"Indirect sanctions exposure ({hops}-hop): {terminal.get('label', 'unknown')}",
                        detail=f"Path: {' → '.join(n.get('label', '?') for n in path.get('nodes', []))}. Reason: {path.get('terminal_reason', '')[:200]}",
                        source="entity_graph.multi_hop",
                        confidence="PROBABLE",
                    ))

            # Persist graph for re-screening
            await graph.save(report.run_id)
            logger.info("Entity graph built: %d nodes, %d edges, %d flagged paths",
                        hop_results.get("graph_stats", {}).get("total_nodes", 0),
                        hop_results.get("graph_stats", {}).get("total_edges", 0),
                        hop_results.get("risk_summary", {}).get("flagged_nodes", 0))
        except Exception as eg_err:
            logger.warning("Entity graph construction failed (non-fatal): %s", eg_err)

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
            # Always "quick" inside DD — deep mode's value-add is the
            # link_investigator tree walk (rule-based, zero LLM cost).
            # "thorough" was firing 24 LLM calls that exhausted provider
            # rate limits before chat synthesis could run (New Akord
            # Security 2026-04-12 — 3 consecutive timeouts).
            dr_depth = "quick"
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

    # ── 5e-bis. Domain ownership verification (RDAP) ──
    # Any DD with a URL / website / domain field runs an RDAP check. This
    # catches the specific failure mode from 2026-04-17: SERBAN cited
    # f3ir.com as F3 International Resources' site but RDAP would have
    # shown the domain belongs to someone else / was recently registered.
    # Independent of _mode_is_deep — cheap (one HTTPS call) and the
    # signal is worth having in quick mode too.
    _dom_candidate = (
        target.get("website")
        or target.get("domain")
        or target.get("url")
        or ""
    )
    # If the DD target name itself looks like a URL (e.g. the operator
    # typed "run dd on https://f3ir.com/"), pick it up here.
    if not _dom_candidate:
        for fld in ("name", "entity", "query"):
            v = target.get(fld) or ""
            if isinstance(v, str) and ("://" in v or v.count(".") >= 1 and " " not in v and len(v) < 120):
                if "://" in v or v.endswith((".com", ".net", ".org", ".io", ".co",
                                               ".ai", ".uk", ".de", ".fr", ".eu",
                                               ".ae", ".sa", ".qa", ".ru", ".cn",
                                               ".tr", ".br", ".ng", ".ma", ".za",
                                               ".ir")):
                    _dom_candidate = v
                    break
    if _dom_candidate:
        try:
            from . import domain_ownership_verifier as dov
            dom_result = await dov.verify_domain(
                _dom_candidate,
                claimed_entity_name=name,
                claimed_jurisdiction=(
                    report.identity.jurisdiction_iso2
                    or report.identity.jurisdiction
                    or target.get("jurisdiction_iso2")
                    or target.get("jurisdiction")
                ),
            )
            if dom_result.get("verified"):
                sev = dov.severity_for(dom_result)
                body = dov.render_finding(dom_result)
                # Emit one finding with the flags summary
                report.digital.findings.append(Finding(
                    severity=sev,
                    title=f"Domain ownership (RDAP): {dom_result['domain']} — flags: {', '.join(dom_result.get('flags') or []) or 'none'}",
                    detail=body + ("\n" + "\n".join(dom_result.get("signals") or []) if dom_result.get("signals") else ""),
                    source="dd_orchestrator.domain_ownership_verifier",
                    confidence="CONFIRMED",
                ))
                # If there's an entity mismatch or a not-registered flag,
                # escalate to the identity section as a hard-stop-worthy signal.
                if any(f in (dom_result.get("flags") or []) for f in (
                    "REGISTRANT_ENTITY_MISMATCH",
                    "DOMAIN_NOT_REGISTERED",
                    "VERY_RECENTLY_REGISTERED",
                )):
                    report.identity.findings.append(Finding(
                        severity="red",
                        title=f"Domain ownership flag: {', '.join(dom_result['flags'])}",
                        detail=body,
                        source="dd_orchestrator.domain_ownership_verifier",
                        confidence="CONFIRMED",
                    ))
            elif dom_result.get("reason"):
                report.digital.data_gaps.append(
                    f"Domain RDAP check inconclusive for {dom_result.get('domain') or _dom_candidate}: {dom_result['reason']}"
                )
        except Exception as e:
            logger.debug("domain_ownership_verifier failed (non-fatal): %s", e)

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
                    if ff.triangulation >= 2:
                        report.digital.findings.append(Finding(
                            severity="info",
                            title=f"link-tree: {ff.kind}={ff.value[:120]} (×{ff.triangulation} sources)",
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

    # Confidence floor: worst tag across all sections.
    # Includes Layer 5c (commercial_coherence) -- it runs BEFORE verification
    # in the orchestrator (lines 2737 vs 2845), so its anomalies/findings
    # are already populated by this point. Excluding it meant a HIGH-tier
    # commercial_coherence anomaly (e.g. licence-chain gap) wouldn't drag
    # the report's confidence floor down, which it should.
    # `report.verification` is included for consistency but contributes
    # nothing today (no findings populated until later in this same call).
    all_confidences = ["ASSESSED"]  # baseline
    for section in (
        report.identity,
        report.network,
        report.verification,
        report.compliance,
        report.digital,
        report.commercial_coherence,
    ):
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
    # Any hard_stop finding anywhere? Includes digital, verification, and
    # commercial_coherence -- the prior list of three layers missed
    # hard-stops surfaced by Layer 5c (e.g. licence-chain gaps that
    # constitute strict-liability offences in some jurisdictions) and
    # by digital-layer OSINT findings (e.g. confirmed sanctions match
    # surfaced by deep_research that didn't propagate up to identity).
    for section in (report.identity, report.network, report.compliance,
                    report.digital, report.verification,
                    report.commercial_coherence):
        for f in getattr(section, "findings", []) or []:
            if getattr(f, "severity", "") == "hard_stop":
                candidates.append("HARD_STOP")
                break

    # Commercial coherence contribution (2026-04-22, Layer 5c)
    # HIGH + sector_mismatch (fronting pattern) → RED.
    # HIGH alone → AMBER-DARK. ELEVATED → AMBER-LIGHT. GREEN → neutral.
    _cc_tier_for_risk = (report.commercial_coherence.tier or "GREEN").upper()
    _cc_has_sector_mismatch = any(
        a.get("kind") == "sector_mismatch"
        for a in (report.commercial_coherence.anomalies or [])
    )
    if _cc_tier_for_risk == "HIGH" and _cc_has_sector_mismatch:
        candidates.append("RED")
    elif _cc_tier_for_risk == "HIGH":
        candidates.append("AMBER-DARK")
    elif _cc_tier_for_risk == "ELEVATED":
        candidates.append("AMBER-LIGHT")

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

    # ── 6b2. Confidence gate — never GREEN when verification is insufficient ──
    # If risk landed on GREEN but key identity signals are missing, bump
    # to AMBER-LIGHT with MANUAL REVIEW flag. This prevents a clean bill
    # when ARIA couldn't actually verify the entity.
    if report.risk_classification == RiskClassification.GREEN.value and not _is_person:
        _needs_manual = False
        _gate_reasons: list[str] = []

        # Registry not verified? Require actual substance — status alone is not enough.
        _has_registry_status = bool(report.identity.registration_status)
        _has_directors = bool(report.identity.directors)
        _has_inc_date = bool(report.identity.incorporation_date)
        _has_substance = _has_directors or (_has_registry_status and _has_inc_date)
        if not _has_substance:
            _needs_manual = True
            _missing = []
            if not _has_registry_status:
                _missing.append("registration status")
            if not _has_directors:
                _missing.append("directors/officers")
            if not _has_inc_date:
                _missing.append("incorporation date")
            _gate_reasons.append(f"registry verification incomplete (missing: {', '.join(_missing)})")

        # Too many data gaps? Fallback iteration includes
        # commercial_coherence so a Layer 5c gap (e.g. unknown payment
        # market, untraceable corporate substance) actually counts toward
        # the manual-review trigger.
        _total_gaps = len(report.data_gaps_summary) if hasattr(report, "data_gaps_summary") else 0
        if not hasattr(report, "data_gaps_summary"):
            _total_gaps = sum(
                len(getattr(s, "data_gaps", []) or [])
                for s in (report.identity, report.network, report.verification,
                          report.compliance, report.digital,
                          report.commercial_coherence)
            )
        if _total_gaps >= 3:
            _needs_manual = True
            _gate_reasons.append(f"{_total_gaps} unresolved data gaps")

        # Ghost score has unresolved indicators?
        _ghost = report.identity.ghost_score or {}
        _indicators = _ghost.get("indicators", [])
        if isinstance(_indicators, list):
            _unresolved = sum(1 for ind in _indicators if isinstance(ind, dict) and ind.get("value") == "?")
        else:
            _unresolved = sum(1 for v in _indicators.values() if v == "?") if isinstance(_indicators, dict) else 0
        if _unresolved >= 4:
            _needs_manual = True
            _gate_reasons.append(f"ghost score has {_unresolved} unresolved indicators")

        if _needs_manual:
            report.risk_classification = RiskClassification.AMBER_LIGHT.value
            report.synthesis.risk_classification = RiskClassification.AMBER_LIGHT.value
            report.identity.findings.append(Finding(
                severity="amber",
                title="Confidence gate: GREEN overridden to AMBER — manual review required",
                detail=f"Reasons: {'; '.join(_gate_reasons)}. ARIA cannot issue a GREEN clearance without sufficient verification.",
                source="dd_orchestrator.confidence_gate",
                confidence="ASSESSED",
            ))
            _entity_name = report.identity.entity_name or target.get("name", "?")
            logger.info("Confidence gate: GREEN → AMBER for %s (%s)", _entity_name, "; ".join(_gate_reasons))

    # ── 6c. SAR trigger — UK POCA / FATF typology ──
    # Triggers (original):
    #   - sanctions hit on subject OR director
    #   - ghost score >= 12 (RED) combined with layered secrecy chain
    #   - transaction value >= 100k with no declared activity
    # Extended triggers (FATF typology / POCA indicators):
    #   - ghost score >= 12 AND no directors (layered secrecy)
    #   - multiple jurisdictions with no apparent business reason
    #   - PEP + opaque ownership (PSC not disclosed)
    #   - entity registered < 12 months with large scope
    #   - director on multiple sanctioned boards
    #   - address cluster with 5+ co-located entities (mail-drop)
    sar_reasons: list[str] = []
    if any("sanctions" in str(f.title).lower() and "hit" in str(f.title).lower()
           for f in report.identity.findings):
        sar_reasons.append("sanctions hit on identity layer")
    if any("hit on sanctions" in str(f.title).lower()
           for f in report.network.findings):
        sar_reasons.append("sanctions hit in network layer (one-hop)")
    if report.synthesis.ghost_score_total >= 12:
        sar_reasons.append(f"ghost score {report.synthesis.ghost_score_total}/20 at RED threshold")

    # SAR-ext-1: ghost score >= 12 AND no directors (layered secrecy)
    _has_directors = bool(report.identity.directors)
    if report.synthesis.ghost_score_total >= 12 and not _has_directors:
        sar_reasons.append("ghost score >= 12 with no directors on file — layered secrecy indicator")

    # SAR-ext-2: multiple jurisdictions with no apparent business reason
    _jurisdictions_seen: set[str] = set()
    if report.identity.jurisdiction_iso2:
        _jurisdictions_seen.add(report.identity.jurisdiction_iso2)
    for _cl in report.network.cross_linked_entities:
        _j = _cl.get("jurisdiction") or _cl.get("jurisdiction_iso2") or ""
        if _j:
            _jurisdictions_seen.add(_j.upper()[:2])
    if len(_jurisdictions_seen) >= 3 and not report.identity.declared_activity:
        sar_reasons.append(
            f"entity spans {len(_jurisdictions_seen)} jurisdictions with no declared activity — complex structuring"
        )

    # SAR-ext-3: PEP connection combined with opaque ownership (no PSC / UBO)
    _has_pep = bool(report.network.pep_connections)
    _has_ubo = bool(report.identity.ubo_chain)
    _has_shareholders = bool(report.identity.shareholders)
    if _has_pep and not _has_ubo and not _has_shareholders:
        sar_reasons.append("PEP connection with no disclosed PSC/UBO — opaque ownership")

    # SAR-ext-4: entity registered < 12 months with large transaction scope
    _inc_date_str = report.identity.incorporation_date
    if _inc_date_str and not _is_person:
        try:
            from datetime import date as _date_type
            _inc_date = _date_type.fromisoformat(str(_inc_date_str)[:10])
            _age_days = (datetime.now(timezone.utc).date() - _inc_date).days
            if _age_days < 365 and report.identity.declared_activity:
                sar_reasons.append(
                    f"entity incorporated < 12 months ago ({_age_days} days) — newco with declared activity"
                )
        except (ValueError, TypeError):
            pass  # unparseable date — skip

    # SAR-ext-5: director appears on multiple sanctioned entities' boards
    _director_sanctions_hits = [
        f for f in report.identity.findings
        if getattr(f, "source", "") == "sanctions.director_screen"
        and getattr(f, "severity", "") in ("hard_stop", "red")
    ]
    if len(_director_sanctions_hits) >= 2:
        sar_reasons.append(
            f"{len(_director_sanctions_hits)} directors flagged on sanctions/debarment lists — cross-board exposure"
        )

    # SAR-ext-6: address cluster — entity shares address with 5+ others (mail-drop)
    _addr_cluster = report.network.address_cluster or {}
    _max_colocated = 0
    for _addr, _info in _addr_cluster.items() if isinstance(_addr_cluster, dict) else []:
        _n = len(_info) if isinstance(_info, list) else int(_info.get("count", 0)) if isinstance(_info, dict) else 0
        _max_colocated = max(_max_colocated, _n)
    if _max_colocated >= 5:
        sar_reasons.append(
            f"address shared with {_max_colocated}+ entities — mail-drop indicator"
        )

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

    # ── ACH-ext-1: Verification quality ──
    _grounded = report.verification.grounded_rate
    _conflicts = report.verification.conflicts
    if _grounded is not None and _grounded < 0.4:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1
    elif _grounded is not None and _grounded >= 0.8:
        hypotheses["H1_legit"]["support"] += 1
    if _conflicts:
        hypotheses["H2_enhanced"]["support"] += 1
        hypotheses["H3_shell"]["support"] += 1
        hypotheses["H1_legit"]["against"] += 1

    # ── ACH-ext-2: Digital footprint ──
    _has_press = bool(report.digital.press_coverage)
    _has_procurement = bool(report.digital.procurement_history)
    if not _has_press and not _has_procurement and not _is_person:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H3_shell"]["support"] += 1
        hypotheses["H1_legit"]["against"] += 1
    elif _has_press and _has_procurement:
        hypotheses["H1_legit"]["support"] += 2

    # ── ACH-ext-3: Network red flags ──
    if report.network.pep_connections:
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1
    if report.network.sanctions_network:
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 2

    # ── ACH-ext-4: Director screening ──
    _dir_sanctions = any(
        getattr(f, "source", "") == "sanctions.director_screen"
        and getattr(f, "severity", "") in ("hard_stop", "red")
        for f in report.identity.findings
    )
    if _dir_sanctions:
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 3

    # ── ACH-ext-5: Registration anomalies — empty company ──
    _no_directors = not report.identity.directors
    _no_inc_date = not report.identity.incorporation_date
    if _no_directors and _no_inc_date and not _is_person:
        hypotheses["H3_shell"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 2

    # ── ACH-ext-6: Export control ──
    _ec = report.compliance.export_control or {}
    _ec_class = (_ec.get("classification") or _ec.get("rating") or "").upper()
    if _ec_class in ("RESTRICTED", "CONTROLLED", "ML", "MTCR", "WA-CAT1", "WA-CAT2"):
        hypotheses["H2_enhanced"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 1

    # ── ACH-ext-7: Commercial coherence (Layer 5c, 2026-04-22) ──
    # A HIGH tier (score < 0.55) means the deal structure itself is
    # incoherent — strong signal for H2 enhanced-DD, and if combined with
    # other red signals pushes H3 (shell/fronting). ELEVATED is a soft
    # signal. GREEN is neutral.
    _cc_section = report.commercial_coherence
    _cc_tier = (_cc_section.tier or "GREEN").upper()
    if _cc_tier == "HIGH":
        hypotheses["H2_enhanced"]["support"] += 3
        hypotheses["H3_shell"]["support"] += 2
        hypotheses["H1_legit"]["against"] += 2
    elif _cc_tier == "ELEVATED":
        hypotheses["H2_enhanced"]["support"] += 1
        hypotheses["H1_legit"]["against"] += 1
    # Sector mismatch (non-defence SIC claiming defence deal) — classic
    # fronting pattern. Treat as a hard H3 signal on its own.
    if any(
        a.get("kind") == "sector_mismatch"
        for a in (_cc_section.anomalies or [])
    ):
        hypotheses["H3_shell"]["support"] += 3
        hypotheses["H1_legit"]["against"] += 3

    # ── Determine winner ──
    report.synthesis.ach_matrix = {
        "hypotheses": hypotheses,
        "method": "balance of support minus against",
        "winner": max(hypotheses.items(), key=lambda kv: kv[1]["support"] - kv[1]["against"])[0],
    }

    # ── 6d2. Competing narratives ──
    _winner = report.synthesis.ach_matrix["winner"]
    _h = hypotheses
    _narratives: list[str] = []
    if _winner == "H1_legit":
        _narratives.append(
            f"Most likely: {report.identity.entity_name or 'entity'} is a legitimate counterparty. "
            f"Supported by ghost score {ghost_total}/20, "
            f"grounded rate {_grounded if _grounded is not None else 'N/A'}, "
            f"and {'presence' if _has_press else 'absence'} in public record."
        )
        _narratives.append(
            "Alternative: entity may still present enhanced-DD risks if verification gaps "
            "remain or counterparty operates in a higher-risk jurisdiction."
        )
    elif _winner == "H2_enhanced":
        _narratives.append(
            f"Most likely: {report.identity.entity_name or 'entity'} is a higher-risk counterparty "
            f"requiring enhanced due diligence before engagement. "
            f"ACH balance: H2 support={_h['H2_enhanced']['support']}, "
            f"H1 net={_h['H1_legit']['support'] - _h['H1_legit']['against']}."
        )
        _narratives.append(
            "Alternative (benign): entity may be legitimate but operating in a complex "
            "regulatory environment or recently incorporated, explaining data sparsity."
        )
        _narratives.append(
            "Alternative (adverse): entity could be a concealment vehicle with "
            "enough surface legitimacy to pass basic checks — deeper investigation warranted."
        )
    else:  # H3_shell
        _narratives.append(
            f"Most likely: {report.identity.entity_name or 'entity'} is a shell or concealment vehicle. "
            f"ACH strongly favours H3 (support={_h['H3_shell']['support']}, "
            f"against={_h['H3_shell']['against']}). "
            f"Ghost score {ghost_total}/20."
        )
        _narratives.append(
            "Alternative: entity may be a dormant but legitimate holding company "
            "with minimal public footprint — verify with the counterparty directly."
        )
    report.synthesis.competing_narratives = _narratives

    # ── 6e. Key findings — pull the highest-severity items across sections ──
    all_findings: list[Finding] = []
    for section in (
        report.identity, report.network, report.verification,
        report.compliance, report.digital, report.commercial_coherence,
    ):
        for f in getattr(section, "findings", []) or []:
            all_findings.append(f)
    severity_order = {"hard_stop": 0, "red": 1, "amber": 2, "info": 3}
    all_findings.sort(key=lambda f: severity_order.get(getattr(f, "severity", "info"), 4))
    report.synthesis.key_findings = all_findings[:10]

    # ── 6f. Residual unknowns = all data_gaps combined ──
    for section in (
        report.identity, report.network, report.verification,
        report.compliance, report.digital, report.commercial_coherence,
    ):
        for g in getattr(section, "data_gaps", []) or []:
            if g not in report.synthesis.residual_unknowns:
                report.synthesis.residual_unknowns.append(g)

    report.synthesis.meta.duration_ms = int((time.time() - t0) * 1000)
    report.synthesis.meta.status = LayerStatus.OK.value


# =============================================================================
# BOTTOM-LINE + RECOMMENDATION (programmatic, pre-LLM)
# =============================================================================

async def _assemble_bluf(report: ARKDDReport) -> None:
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

    # ── Layer 5c tag on the BLUF (2026-04-22) ──
    # When commercial coherence is ELEVATED or HIGH, surface it in the
    # headline so operators see the structural concern next to the
    # sanctions/ghost-score/risk verdict — not buried in the body.
    _cc_section = report.commercial_coherence
    _cc_tier = (_cc_section.tier or "GREEN").upper()
    if _cc_tier in ("ELEVATED", "HIGH") and _cc_section.meta.status not in (
        LayerStatus.SKIPPED.value, LayerStatus.ERROR.value,
    ):
        _icon = "🟠" if _cc_tier == "ELEVATED" else "🔴"
        _n_issues = (
            len(_cc_section.anomalies) + len(_cc_section.licence_chain_gaps)
            + len(_cc_section.jurisdiction_flags)
        )
        _tag = (
            f"\n\n{_icon} Commercial coherence {_cc_tier} "
            f"(score {_cc_section.coherence_score:.2f}) — {_n_issues} issue(s) "
            f"flagged in Layer 5c. See Commercial Coherence section."
        )
        if report.bottom_line and _tag not in report.bottom_line:
            report.bottom_line = report.bottom_line + _tag

    report.confidence_tag = report.verification.confidence_floor or "ASSESSED"
    # Aggregate all data_gaps into the top-level summary so consumers
    # can surface them without walking the whole report tree.
    for section in (
        report.identity, report.network, report.verification,
        report.compliance, report.digital, report.commercial_coherence,
    ):
        for g in getattr(section, "data_gaps", []) or []:
            if g not in report.data_gaps_summary:
                report.data_gaps_summary.append(g)

    # ── Auto-create compliance case from DD result (2026-04-13) ──────
    # Every entity screened gets tracked in the compliance workflow for
    # lifecycle management (re-screening, approval, audit trail).
    try:
        from . import compliance_workflow
        await compliance_workflow.auto_create_from_dd(
            entity_name=report.identity.entity_name,
            entity_type=report.identity.entity_type,
            jurisdiction=report.identity.jurisdiction_iso2 or "",
            registration_number=report.identity.registration_number or "",
            risk_level=report.risk_classification.lower().replace("-", "_").replace("amber_light", "amber").replace("amber_dark", "red"),
            risk_summary=report.bottom_line[:300],
            dd_run_id=report.run_id,
        )
    except Exception as _cw_err:
        logger.debug("Compliance workflow auto-create failed (non-fatal): %s", _cw_err)


# =============================================================================
# PERSISTENCE
# =============================================================================

async def _persist_report(report: ARKDDReport) -> None:
    """Store the finished report in Redis + append a summary signal to
    the intel_ledger + write a notebook entry to mem0 (async, non-blocking)."""
    try:
        from . import redis_store as rs
        # Persist the full serialised report plus a pre-rendered markdown
        # copy. training_export.dd_reports reads `rendered` for the
        # fine-tune capture payload — without it the collector always
        # short-circuits at the word-count guard. Keeping it at write
        # time avoids having to reconstruct DDReport from a plain dict
        # on the read side.
        _body = report.as_dict()
        try:
            _body["rendered"] = report.render_markdown(concise=False)
        except Exception as _rm_err:
            logger.debug("render_markdown failed during persist: %s", _rm_err)
        await rs.set_json(
            REPORT_REDIS_KEY.format(run_id=report.run_id),
            _body,
            ex=REPORT_TTL_SECONDS,
        )
        try:
            index = await rs.get_json(REPORT_INDEX_KEY) or []
            index.insert(0, {
                "run_id": report.run_id,
                "generated_at": report.generated_at,
                # Mirror entity name + jurisdiction into the columns the
                # dashboard table expects so the library renders both.
                "entity_name": report.identity.entity_name,
                "jurisdiction": report.identity.jurisdiction,
                # R-F130 (2026-05-10): write `severity` + `risk` +
                # `risk_classification` together so the library table
                # column populates regardless of which key the renderer
                # asks for.
                "severity": report.risk_classification,
                "risk": report.risk_classification,
                "risk_classification": report.risk_classification,
                "created_at": report.generated_at,
            })
            index = index[:500]
            await rs.set_json(REPORT_INDEX_KEY, index, ex=REPORT_TTL_SECONDS)
        except Exception as e:
            logger.debug("dd_orchestrator: report index write failed: %s", e)
    except Exception as e:
        logger.warning("dd_orchestrator: Redis persist failed: %s", e)

    # ── Self-metrics: declared calibration (final confidence tag as a score)
    # + coverage (subsystems that produced findings). Real calibration arrives
    # later when corrections are logged against this run_id.
    try:
        from . import self_metrics
        _conf_score = {
            "CONFIRMED": 1.0, "PROBABLE": 0.75, "ASSESSED": 0.5, "UNCERTAIN": 0.25,
        }.get((report.confidence_tag or "ASSESSED").upper(), 0.5)
        _domain = (report.identity.jurisdiction or "unknown").upper() or "unknown"
        await self_metrics.emit(
            "calibration", _domain, "dd_declared_confidence",
            _conf_score,
            context={"run_id": report.run_id, "tag": report.confidence_tag,
                     "risk": report.risk_classification},
            source_module="dd_orchestrator",
        )
        # Coverage: fraction of the 6 DD subsystems that produced at least one
        # finding (identity, network, compliance, digital, verification,
        # commercial_coherence). Synthesis uses `key_findings` not
        # `findings` and is the aggregator of the other layers, so it's
        # not counted as a separate signal source. The comment previously
        # said "6 subsystems" but the list had 5 -- commercial_coherence
        # was missing, so coverage was always undercounted by 1/6.
        _subs = [report.identity, report.network, report.compliance,
                 report.digital, report.verification,
                 report.commercial_coherence]
        _produced = sum(1 for s in _subs
                        if s and (getattr(s, "findings", None) or getattr(s, "entity_name", None)))
        await self_metrics.emit(
            "coverage", _domain, "dd_subsystem_yield",
            _produced / max(1, len(_subs)),
            context={"run_id": report.run_id, "subsystems_produced": _produced},
            source_module="dd_orchestrator",
        )
    except Exception as _sm:
        logger.debug("dd_orchestrator self_metrics failed: %s", _sm)


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

# ══════════════════════════════════════════════════════════════════════════
# Entity-name sanity gate
# ══════════════════════════════════════════════════════════════════════════
#
# Rejects obvious garbage names before the 7-layer pipeline runs.
# Garbage names produce noise findings that then poison mem0 /
# claim_ledger for future conversations. Prevention > cleanup.

_URL_SCHEME_FRAGMENTS = {
    "http", "https", "ftp", "ftps", "ws", "wss",
    "file", "about", "data", "mailto",
}


def _validate_entity_name(name: str) -> tuple[bool, str]:
    """Return (is_valid, reject_reason). Reject reason is empty on valid."""
    if not name or not name.strip():
        return False, "empty"
    s = name.strip()

    # Scheme fragments — "https", "http", "ftp" etc. come from URL
    # regex captures that terminated at `/` or `:`.
    if s.lower().strip(":/") in _URL_SCHEME_FRAGMENTS:
        return False, f"looks like a URL scheme fragment, not an entity"

    # Pure punctuation / whitespace
    if not re.search(r"[A-Za-z0-9]", s):
        return False, "no alphanumeric characters"

    # Very short all-lowercase strings with no spaces — likely garbage
    # (single words like 'http', 'com', 'www'). Real entity names are
    # usually capitalised OR longer than 4 chars. Domain names are
    # rescued by the `.` check below.
    if len(s) <= 4 and s.islower() and "." not in s:
        return False, f"too short ({len(s)} chars, lowercase, no dot)"

    # Over-long input
    if len(s) > 500:
        return False, f"too long ({len(s)} chars)"

    # Starts with a scheme separator
    if s.startswith((":", "/", "\\", "?", "#", "&", "=")):
        return False, "starts with a URL delimiter"

    return True, ""


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

    # ── Entity-name sanity gate (2026-04-17 21:40 fix) ──
    # Tonight's F3 incident: DD ran on entity "https" because the intent
    # regex captured "https:" from a URL. The 7-layer pipeline then
    # produced a 5-match "sanctions hit" (noise matches on the string
    # "https") and that defective result was ingested into mem0 as
    # confirmed evidence. Gate it here so garbage names never run.
    _raw_name = (target.get("name") or target.get("entity") or target.get("query", "")).strip()
    _is_valid, _reject_reason = _validate_entity_name(_raw_name)
    if not _is_valid:
        raise ValueError(
            f"Refusing DD on malformed entity name {_raw_name!r}: {_reject_reason}. "
            f"If this came from URL parsing, pass the domain (e.g. 'f3ir.com') "
            f"instead of the scheme."
        )

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

    # ── Predictor: forecast likely-failure axes BEFORE any layer runs.
    # A predicted gap is a closed gap. Surface past mistakes to the brain
    # so the run is informed by prior corrections. Never blocks — if the
    # predictor degrades, we proceed without it.
    try:
        from . import predictor
        _forecast = await predictor.forecast(
            task_type="dd",
            domain=(target.get("jurisdiction_iso2") or "unknown"),
            entity_type=target.get("type"),
            context={"mode": mode, "run_id": report.run_id},
        )
        report.pre_task_forecast = _forecast  # ignored by schema, carried on instance
        if _forecast["likely_failures"]:
            logger.warning(
                "[dd_orchestrator] predictor forecast for %s/%s: confidence=%.2f, "
                "likely_failures=%d, past_mistakes=%d",
                target.get("name", "?")[:40],
                target.get("jurisdiction_iso2", "?"),
                _forecast["overall_confidence"],
                len(_forecast["likely_failures"]),
                len(_forecast["past_mistakes"]),
            )
            try:
                from . import brain_hook
                top = "; ".join(
                    f"{f['axis']}: {f['reason'][:100]}" for f in _forecast["likely_failures"][:3]
                )
                await brain_hook.absorb(
                    module="predictor",
                    summary=f"DD pre-run forecast {target.get('name', '?')[:40]}: "
                            f"conf={_forecast['overall_confidence']:.2f} — {top}",
                    entity_name=target.get("name", ""),
                    success=True,
                    confidence="ASSESSED",
                )
            except Exception:
                pass
    except Exception as _pe:
        logger.debug("predictor forecast failed (non-fatal): %s", _pe)

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

        # ── LAYER 5c: COMMERCIAL COHERENCE ASSESSMENT (2026-04-22) ──
        # Assesses corporate / commercial / legal coherence of the deal
        # structure against jurisdiction norms. Observer, never a gate.
        # Its anomalies feed Layer 5b deception scoring below, and its
        # coherence score feeds Layer 6 synthesis via the findings it
        # emits. Enabled by default; can be disabled with
        # ARIA_LAYER_5C_ENABLED=0 for emergency bypass.
        #
        # See aria_service/intel/commercial_coherence.py + the reference
        # framework doc (Downloads/aria_global_legal_framework.html).
        _coherence_text = ""
        if os.getenv("ARIA_LAYER_5C_ENABLED", "1") != "0" and not hard_stop:
            layer_name = "commercial_coherence"
            report.layers_run.append(layer_name)
            try:
                from . import commercial_coherence as _cc
                _section = await asyncio.wait_for(
                    _cc.assess_commercial_coherence(target, report),
                    timeout=10,  # pure data-driven — no network calls
                )
                _coherence_text = _cc.anomaly_text_for_deception(_section)
                if _section.tier != "GREEN":
                    logger.info(
                        "[dd_orchestrator] Layer 5c %s on %s: score=%.2f, "
                        "%d anomalies, %d licence-chain gaps",
                        _section.tier,
                        report.identity.entity_name or "?",
                        _section.coherence_score,
                        len(_section.anomalies),
                        len(_section.licence_chain_gaps),
                    )
            except asyncio.TimeoutError:
                report.commercial_coherence.meta.status = LayerStatus.ERROR.value
                report.commercial_coherence.meta.error = "timeout after 10s"
            except Exception as _cc_err:
                report.commercial_coherence.meta.status = LayerStatus.ERROR.value
                report.commercial_coherence.meta.error = str(_cc_err)[:200]
                logger.warning("[dd_orchestrator] Layer 5c failed (non-fatal): %s", _cc_err)
        else:
            if "commercial_coherence" not in report.layers_skipped:
                report.layers_skipped.append("commercial_coherence")
            report.commercial_coherence.meta.status = LayerStatus.SKIPPED.value

        # ── LAYER 5b: DECEPTION SCORING (Clause 16 — 2026-04-18 evening) ──
        # Run the validated deception risk analyser over ANY counterparty
        # free-text we have collected. Sources (in priority order):
        #   1. target['counterparty_text'] / target['message_text'] / target['communication']
        #      — the actual message from the counterparty if the caller passed it
        #   2. target['capability_statement'] / target['narrative'] / target['description']
        #      — pitch / proposal text for proposal-context scoring
        #   3. digital.findings narratives — what we discovered in OSINT
        #   4. Layer 5c commercial coherence anomalies (2026-04-22) — so the
        #      deception analyser sees structural incoherence alongside the
        #      counterparty's own words.
        # The score lands on report.deception so synthesis + verification
        # can weight it and the bottom-line renderer can surface ELEVATED/HIGH.
        # Past incident 2026-04-16 — ARIA shipped responses claiming
        # "Deception Detection... protocols running" when the module was
        # NEVER called from the runtime. This wires it for real.
        try:
            from . import deception_detection as _dd
            _texts: list[tuple[str, str]] = []
            for _key in ("counterparty_text", "message_text", "communication", "claim"):
                _v = (target.get(_key) or "").strip()
                if _v and len(_v) >= 50:
                    _texts.append(("entity_claim", _v))
            for _key in ("capability_statement", "narrative", "description", "proposal"):
                _v = (target.get(_key) or "").strip()
                if _v and len(_v) >= 50:
                    _texts.append(("proposal", _v))
            # Also pick up digital-layer findings narratives
            for _f in (report.digital.findings or [])[:3]:
                _narr = getattr(_f, "evidence_text", "") or getattr(_f, "summary", "") or ""
                if _narr and len(_narr) >= 100:
                    _texts.append(("business_communication", _narr))
            # Layer 5c anomalies — treated as business_communication so the
            # deception analyser weights them against business baselines.
            if _coherence_text and len(_coherence_text) >= 50:
                _texts.append(("business_communication", _coherence_text))

            if _texts:
                analyser = _dd.ARIADeceptionAnalyser()
                _max_score = 0.0
                _max_tier = _dd.DeceptionRiskTier.LOW
                _all_signals: list[str] = []
                for _ctx, _text in _texts:
                    score = await analyser.analyse_async(
                        _text[:6000],
                        context_type=_ctx,
                        reference_entity=report.identity.entity_name or "",
                    )
                    if score.raw_score > _max_score:
                        _max_score = score.raw_score
                        _max_tier = score.tier
                    _all_signals.extend(s.category for s in score.signals_detected)
                # Attach to report (schema-tolerant — instance attribute)
                try:
                    report.deception = {
                        "max_score": round(_max_score, 3),
                        "tier": _max_tier.value,
                        "texts_analysed": len(_texts),
                        "signals": sorted(set(_all_signals))[:10],
                        "requires_eDD": _max_tier in (
                            _dd.DeceptionRiskTier.ELEVATED,
                            _dd.DeceptionRiskTier.HIGH,
                        ),
                    }
                except Exception:
                    pass
                if _max_tier in (_dd.DeceptionRiskTier.ELEVATED, _dd.DeceptionRiskTier.HIGH):
                    logger.warning(
                        "[dd_orchestrator] DECEPTION %s on %s: score=%.2f signals=%s",
                        _max_tier.value, report.identity.entity_name or "?",
                        _max_score, sorted(set(_all_signals))[:5],
                    )
        except Exception as _de:
            logger.debug("[dd_orchestrator] deception scoring failed (non-fatal): %s", _de)

        # ── LAYER 8: COUNTER-INTELLIGENCE (R-F121 — wired 2026-05-10) ──
        # Sweeps recent intel_ledger signals about this entity for the
        # patterns that none of the prior layers can see: narrative-shift
        # (positive press timed against negative event), coordinated press
        # (≥3 tier-3 sources publishing in the same window), tier
        # contradiction (tier-1 says listed, tier-3 says clean). Result
        # attached to report.counter_intelligence; brain absorbs material
        # alerts.  Fail-open — never blocks DD on its own errors.
        layer_name = "counter_intelligence"
        report.layers_run.append(layer_name)
        try:
            from . import counter_intelligence as _ci
            _ci_result = await asyncio.wait_for(
                _ci.scan_entity(report.identity.entity_name or "", window_days=30),
                timeout=8,
            )
            try:
                report.counter_intelligence = _ci_result  # type: ignore[attr-defined]
            except Exception:
                pass
            if isinstance(_ci_result, dict) and _ci_result.get("composite_score", 0) >= 0.5:
                logger.warning(
                    "[dd_orchestrator] COUNTER-INTEL alert on %s: score=%.2f patterns=%s",
                    report.identity.entity_name or "?",
                    _ci_result.get("composite_score", 0),
                    list((_ci_result.get("patterns") or {}).keys())[:3],
                )
        except asyncio.TimeoutError:
            logger.warning("[dd_orchestrator] counter-intel timed out (non-fatal)")
        except Exception as _ci_err:
            logger.debug("[dd_orchestrator] counter-intel failed (non-fatal): %s", _ci_err)

        # ── LAYER 9: SANCTIONS DIVERGENCE (R-F122 — wired 2026-05-10) ──
        # Cross-list jurisdictional divergence: entity listed by US OFAC
        # but not UK OFSI? UN SC silent while EU acts? This is the
        # compliance-officer ground-truth question and the prior identity
        # layer's parallel screen reports presence/absence per source —
        # this layer tells the operator the *meaning* of that pattern.
        # Result attached to report.sanctions_divergence.
        layer_name = "sanctions_divergence"
        report.layers_run.append(layer_name)
        try:
            from . import sanctions_divergence as _sdiv
            _sdiv_result = await asyncio.wait_for(
                _sdiv.analyze_divergence(report.identity.entity_name or ""),
                timeout=10,
            )
            try:
                report.sanctions_divergence = _sdiv_result  # type: ignore[attr-defined]
            except Exception:
                pass
            if (
                isinstance(_sdiv_result, dict)
                and _sdiv_result.get("matches", 0) > 0
                and _sdiv_result.get("divergence_count", 0) >= 1
            ):
                logger.warning(
                    "[dd_orchestrator] SANCTIONS DIVERGENCE on %s: listed=%s, silent=%s",
                    report.identity.entity_name or "?",
                    _sdiv_result.get("jurisdictions_listed"),
                    _sdiv_result.get("jurisdictions_not_listed"),
                )
        except asyncio.TimeoutError:
            logger.warning("[dd_orchestrator] sanctions divergence timed out (non-fatal)")
        except Exception as _sdiv_err:
            logger.debug(
                "[dd_orchestrator] sanctions divergence failed (non-fatal): %s",
                _sdiv_err,
            )

        # ── LAYER 10: FORENSIC (Benford + TBML — R-F123, wired 2026-05-10) ──
        # Apply Benford's Law to financial figures collected for this
        # entity (procurement history values + caller-provided figures)
        # and run the TBML transaction classifier over caller-provided
        # transaction line items. Conservative gate: Benford only if
        # ≥50 distinct values; TBML only if transactions list present.
        # When neither gate fires the layer self-skips silently.
        layer_name = "forensic"
        report.layers_run.append(layer_name)
        _forensic_out: dict[str, Any] = {}
        try:
            _values: list[float] = []
            for _key in ("financials", "values", "amounts", "figures", "contract_values"):
                _v = target.get(_key)
                if isinstance(_v, list):
                    for _x in _v:
                        try:
                            _values.append(float(_x))
                        except Exception:
                            pass
            for _ph in (report.network.procurement_history or [])[:200]:
                if not isinstance(_ph, dict):
                    continue
                for _k in ("value", "amount", "contract_value", "award_value"):
                    _vv = _ph.get(_k)
                    if _vv is None:
                        continue
                    try:
                        _values.append(float(_vv))
                    except Exception:
                        pass
            if len(_values) >= 50:
                from . import forensic_benford as _fb
                _benford = _fb.benford_test(_values)
                if isinstance(_benford, dict):
                    _forensic_out["benford"] = {
                        "n":          _benford.get("n"),
                        "chi_square": _benford.get("chi_square"),
                        "p_value":    _benford.get("p_value"),
                        "tier":       _benford.get("tier"),
                        "narrative":  _fb.benford_narrative(_benford),
                    }
                    if str(_benford.get("tier") or "").upper() == "HIGH":
                        logger.warning(
                            "[dd_orchestrator] BENFORD anomaly on %s: chi2=%s, p=%s, n=%s",
                            report.identity.entity_name or "?",
                            _benford.get("chi_square"),
                            _benford.get("p_value"),
                            _benford.get("n"),
                        )
            _txns = target.get("transactions")
            if isinstance(_txns, list) and _txns:
                from . import tbml_detection as _tbml
                _tbml_results: list[dict[str, Any]] = []
                for _t in _txns[:25]:
                    if not isinstance(_t, dict):
                        continue
                    try:
                        _r = await asyncio.wait_for(_tbml.analyze_transaction(_t), timeout=6)
                        if isinstance(_r, dict):
                            _tbml_results.append(_r)
                    except Exception:
                        continue
                if _tbml_results:
                    _forensic_out["tbml"] = {
                        "transactions_analysed": len(_tbml_results),
                        "high_anomalies": sum(
                            1 for _r in _tbml_results
                            if str(_r.get("anomaly_tier") or "").upper() == "HIGH"
                        ),
                        "results": _tbml_results,
                    }
            if _forensic_out:
                try:
                    report.forensic = _forensic_out  # type: ignore[attr-defined]
                except Exception:
                    pass
                logger.info(
                    "[dd_orchestrator] forensic layer ran: keys=%s",
                    sorted(_forensic_out.keys()),
                )
            else:
                if "forensic" not in report.layers_skipped:
                    report.layers_skipped.append("forensic")
        except Exception as _fx_err:
            logger.debug("[dd_orchestrator] forensic layer failed (non-fatal): %s", _fx_err)

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
    await _assemble_bluf(report)

    # ── Verification gate (2026-04-18) ──
    # On RED verdicts, run a second-opinion pass through a different
    # provider and compare the structured decision. If they disagree
    # we stamp CRITICAL_UNVERIFIED and the downstream handler knows
    # to block auto-send. If they agree we stamp VERIFIED BY
    # DISAGREEMENT so the confidence is earned, not claimed.
    # Adds ~20-40s to a RED DD only; GREEN/AMBER passes through
    # untouched so routine DD latency is unchanged.
    try:
        _risk = (report.risk_classification or "").upper()
        if _risk in ("RED", "HARD_STOP", "NO-GO") and llm is not None:
            from ..learning import verification_gate as _vg
            # Build a concise secondary-opinion prompt from what we
            # already have. The secondary doesn't re-run tools — it
            # reasons over the same evidence to see if it reaches
            # the same verdict independently.
            evidence_brief = (
                f"Entity: {report.identity.entity_name}\n"
                f"Jurisdiction: {report.identity.jurisdiction_iso2 or 'unknown'}\n"
                f"Sanctions screen: {report.identity.sanctions_screen or 'CLEAN'}\n"
                f"Data gaps: {', '.join(report.data_gaps_summary[:10]) if report.data_gaps_summary else 'none'}\n"
                f"Findings (identity): {'; '.join(getattr(f, 'title', '')[:120] for f in report.identity.findings[:6])}\n"
                f"Findings (network):  {'; '.join(getattr(f, 'title', '')[:120] for f in report.network.findings[:4])}\n"
                f"Findings (digital):  {'; '.join(getattr(f, 'title', '')[:120] for f in report.digital.findings[:4])}\n"
            )
            sys_prompt = (
                "You are a defence-compliance auditor reviewing evidence "
                "on one specific entity. Return a concise verdict:\n"
                "  Risk: RED / AMBER / GREEN\n"
                "  Sanctions: HIT / CLEAN\n"
                "  Recommendation: HALT / PROCEED\n"
                "  Confidence: [CONFIRMED / PROBABLE / ASSESSED / UNCERTAIN]\n"
                "Be brief — reasoning in 6-10 lines max. Do NOT invent "
                "new facts. Only reason over the evidence given."
            )
            primary_narrative = report.bottom_line or (report.synthesis.rationale or "")
            if primary_narrative and len(primary_narrative) > 50:
                # Run ONLY the secondary via a different provider — use
                # whatever narrative is already in the report as the primary
                sec_provider = _vg.pick_secondary_provider(
                    llm, exclude_name=getattr(llm, "_last_used_name", "") or ""
                )
                secondary_narrative = ""
                if sec_provider is None:
                    # CRITICAL-grade DD output but no independent secondary
                    # — count it so /verification/stats surfaces the gap
                    # instead of silently reading 0/0/0.
                    try:
                        await _vg.record_skipped("no_secondary_provider")
                    except Exception:
                        pass
                if sec_provider is not None:
                    try:
                        _t_sec_start = time.time()
                        _r = await asyncio.wait_for(
                            sec_provider.complete(
                                sys_prompt, evidence_brief,
                                max_tokens=400, timeout=45.0,
                            ),
                            timeout=50.0,
                        )
                        secondary_narrative = getattr(_r, "text", "") or ""
                        logger.info(
                            "[dd_orchestrator] verification secondary pass on %s — %.1fs",
                            sec_provider.name, time.time() - _t_sec_start,
                        )
                        if not secondary_narrative:
                            try:
                                await _vg.record_skipped("secondary_empty")
                            except Exception:
                                pass
                    except Exception as _sec_err:
                        try:
                            await _vg.record_skipped("secondary_call_failed")
                        except Exception:
                            pass
                        logger.warning(
                            "[dd_orchestrator] verification secondary pass failed: %s",
                            _sec_err,
                        )
                if secondary_narrative:
                    vres = await _vg.verify(
                        primary_narrative, secondary_narrative,
                        metadata={"risk_classification": _risk},
                    )
                    # Attach to report — schema-tolerant (the field may
                    # not exist in all dataclass variants, so use setattr).
                    try:
                        report.verification_gate = {
                            "verdict": vres["verdict"],
                            "severity": vres["disagreement"]["severity"] if vres.get("disagreement") else "NONE",
                            "disagreements": vres["disagreement"]["disagreements"] if vres.get("disagreement") else [],
                            "recommendation": vres.get("recommendation"),
                            "primary_provider": getattr(llm, "_last_used_name", "") or "fallback",
                            "secondary_provider": getattr(sec_provider, "name", "") if sec_provider else "",
                        }
                    except Exception:
                        pass
                    # Stamp the BLUF so any downstream renderer surfaces it
                    tag = (
                        "\n\n🛡 [VERIFIED BY DISAGREEMENT — both providers agree]"
                        if vres["verdict"] == "CRITICAL_VERIFIED"
                        else f"\n\n⚠ [CRITICAL — PROVIDERS DISAGREE — {vres['disagreement']['severity']}] "
                             f"Block auto-send; human adjudication required."
                    )
                    if report.bottom_line and tag not in report.bottom_line:
                        report.bottom_line = report.bottom_line + tag
    except Exception as _vg_err:
        logger.warning(
            "[dd_orchestrator] verification gate failed (non-fatal): %s", _vg_err
        )

    report.total_duration_ms = int((time.time() - t_run_start) * 1000)
    report.layer_costs_usd = {
        "identity":             report.identity.meta.cost_usd,
        "network":              report.network.meta.cost_usd,
        "verification":         report.verification.meta.cost_usd,
        "compliance":           report.compliance.meta.cost_usd,
        "digital":              report.digital.meta.cost_usd,
        "commercial_coherence": report.commercial_coherence.meta.cost_usd,
        "synthesis":            report.synthesis.meta.cost_usd,
    }
    report.total_cost_usd = sum(report.layer_costs_usd.values())

    # R-F119 (2026-05-09): the per-layer meta.cost_usd fields above are
    # rarely populated by individual layers (DD layers don't track cost
    # internally — they call the wrapped LLM and MeteredProvider records
    # to cost_tracker out-of-band). Sum from cost_tracker by feature
    # window so report.total_cost_usd reflects what was actually spent.
    # Operator-visible fix: 'Cost: $0.0000' on every DD report (TARA
    # AEROSPACE on 2026-05-09 was the trigger).
    if report.total_cost_usd == 0:
        try:
            from . import cost_tracker as _ct
            recent = await _ct.list_recent_calls(limit=200)
            window_total = 0.0
            window_calls = 0
            for c in (recent or []):
                if not isinstance(c, dict):
                    continue
                ts = c.get("ts") or 0
                if ts < t_run_start:
                    continue
                feat = c.get("feature") or ""
                if feat == "dd_orchestrator" or not feat or feat == "uncategorized":
                    window_total += float(c.get("cost_usd") or 0.0)
                    window_calls += 1
            if window_total > 0:
                report.total_cost_usd = round(window_total, 6)
                # Stamp synthesis layer as the carrier for the aggregate
                # since per-layer attribution isn't available
                report.synthesis.meta.cost_usd = round(window_total, 6)
                report.layer_costs_usd["synthesis"] = round(window_total, 6)
                logger.info(
                    "[dd_orchestrator] cost backfill from cost_tracker: "
                    "$%.4f over %d calls in window",
                    window_total, window_calls,
                )
        except Exception as _ce:
            logger.debug("[dd_orchestrator] cost backfill failed: %s", _ce)

    # ── Persist + deliver ──
    await _persist_report(report)

    # ── Brain hook: feed all learning tiers ──
    try:
        from . import brain_hook
        _dd_summary = (
            f"DD report on {report.identity.entity_name} "
            f"({report.identity.entity_type}, {report.identity.jurisdiction_iso2 or 'unknown jurisdiction'}): "
            f"risk={report.risk_classification}. {report.bottom_line[:300]}"
        )
        _dd_detail_parts = [_dd_summary]
        _sanctions = report.identity.sanctions_screen or {}
        if _sanctions.get("matches"):
            _dd_detail_parts.append(f"Sanctions: {len(_sanctions['matches'])} matches")
        if report.network.findings:
            _dd_detail_parts.append(f"Network: {'; '.join(getattr(f, 'title', '')[:100] for f in report.network.findings[:5])}")
        if report.digital.findings:
            _dd_detail_parts.append(f"Digital: {'; '.join(getattr(f, 'title', '')[:100] for f in report.digital.findings[:5])}")
        _cc_for_brain = report.commercial_coherence
        if (_cc_for_brain.tier or "GREEN").upper() != "GREEN":
            _dd_detail_parts.append(
                f"Coherence: {_cc_for_brain.tier} "
                f"(score {_cc_for_brain.coherence_score:.2f}, "
                f"{len(_cc_for_brain.anomalies)} anomalies, "
                f"{len(_cc_for_brain.licence_chain_gaps)} licence-chain gaps)"
            )
        if report.data_gaps_summary:
            _dd_detail_parts.append(f"Data gaps: {', '.join(report.data_gaps_summary[:5])}")
        await brain_hook.absorb(
            module="dd_orchestrator",
            summary=_dd_summary,
            detail=" | ".join(_dd_detail_parts),
            entity_name=report.identity.entity_name or "",
            success=report.risk_classification != "ERROR",
            source_id=report.run_id,
            confidence="PROBABLE",
            gap_type="knowledge_gap" if report.data_gaps_summary else None,
            gap_detail=f"DD data gaps for {report.identity.entity_name}: {', '.join(report.data_gaps_summary[:5])}" if report.data_gaps_summary else None,
        )
    except Exception as e:
        logger.warning("dd_orchestrator: brain_hook failed (non-fatal): %s", e)

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


def _buyer_matches_entity(buyer: str, entity_name: str) -> bool:
    """Loose match: deals store buyer as a free-text string. We match on
    case-insensitive substring in either direction, with a min token length
    of 4 to avoid matching on short common words."""
    if not buyer or not entity_name:
        return False
    b = buyer.strip().lower()
    e = entity_name.strip().lower()
    if len(e) < 4 or len(b) < 4:
        return False
    return e in b or b in e


async def _fan_out_alert_to_deals(alert: dict) -> list[dict]:
    """When a watchlist entity worsens, find every open deal whose buyer
    matches and tag it. Returns list of {id, buyer, country, stage} impacted.
    Silent on errors — never block the parent rescreen cycle."""
    try:
        from . import deal_pipeline
    except Exception as e:
        logger.debug("deal_pipeline unavailable for fan-out: %s", e)
        return []

    entity = alert.get("entity", "")
    change_type = alert.get("change_type", "")
    try:
        open_deals = await deal_pipeline.get_pipeline(include_closed=False)
    except Exception as e:
        logger.warning("[watchlist fan-out] pipeline read failed: %s", e)
        return []

    impacted: list[dict] = []
    tag = f"sanctions_alert_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    note = f"Counterparty '{entity}' flagged ({change_type}) by watchlist re-screen."

    for deal in open_deals:
        if not _buyer_matches_entity(deal.get("buyer", ""), entity):
            continue
        deal_id = deal.get("id")
        if not deal_id:
            continue
        try:
            await deal_pipeline.update_lead(
                deal_id,
                tags=[tag, "FLAGGED_FOR_RESCREEN"],
                notes=note,
            )
            impacted.append({
                "id": deal_id,
                "buyer": deal.get("buyer", ""),
                "country": deal.get("country", ""),
                "stage": deal.get("stage", ""),
            })
        except Exception as e:
            logger.warning("[watchlist fan-out] update %s failed: %s", deal_id, e)

    if impacted:
        logger.warning(
            "[watchlist fan-out] %s (%s) impacts %d open deal(s)",
            entity, change_type, len(impacted),
        )
        # ── Brain hook: ARIA needs to know that a worsening sanctions event
        # has just rippled into the active pipeline. Without this she'd see
        # the rescreen alert and the deal tag separately and never connect
        # them. Rule Zero: she sees everything.
        try:
            from . import brain_hook
            deal_summary = ", ".join(f"{d['id']} ({d.get('buyer','')[:30]})" for d in impacted[:5])
            await brain_hook.absorb(
                module="dd_orchestrator",
                summary=(
                    f"Sanctions worsening on '{entity}' ({change_type}) impacted "
                    f"{len(impacted)} open deal(s): {deal_summary}"
                ),
                entity_name=entity,
                success=False,  # worsening sanctions = adverse event for the pipeline
                confidence="CONFIRMED",
                gap_type="counterparty_risk_materialised",
                gap_detail=f"Deals now flagged FOR_RESCREEN: {[d['id'] for d in impacted]}",
            )
        except Exception as e:
            logger.debug("fan-out brain_hook absorb failed (non-fatal): %s", e)

        # ── Audit log: a watchlist worsening that touches a deal is a
        # compliance-grade event. Record per deal so each deal's compliance
        # file picks it up cleanly. ──
        try:
            from . import audit_log
            for d in impacted:
                await audit_log.record(
                    action="watchlist_alert",
                    actor="dd_orchestrator._fan_out_alert_to_deals",
                    entity_name=entity,
                    deal_id=d["id"],
                    inputs={"change_type": change_type, "alert_ts": alert.get("timestamp")},
                    outputs={
                        "deal_tagged": True,
                        "deal_buyer": d.get("buyer"),
                        "deal_stage": d.get("stage"),
                    },
                    decision=f"DEAL_FLAGGED_FOR_RESCREEN ({change_type})",
                    confidence="CONFIRMED",
                    notes=f"Sanctions worsening on '{entity}' triggered rescreen flag on deal {d['id']}",
                )
        except Exception as e:
            logger.debug("fan-out audit record failed (non-fatal): %s", e)
    return impacted


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

    # F57 fix 2026-04-28: opportunistic purge of polluted entries before
    # spending cycle budget on them. Past auto-escalations from before
    # the validator was wired added search-query strings to the watchlist
    # (e.g. "SAM.gov defence military security procurement global last 7
    # days 2026"). Each daily re-screen wasted budget logging "rejecting
    # non-entity input" for them. Strip those entries here so the
    # watchlist self-cleans over the next few cycles.
    try:
        from . import sanctions as _sanc
        if hasattr(_sanc, "_looks_like_entity_name"):
            before = len(watchlist)
            watchlist = [
                w for w in watchlist
                if _sanc._looks_like_entity_name((w.get("name") or w.get("entity") or "").strip())
            ]
            removed = before - len(watchlist)
            if removed:
                await rs.set_json(WATCHLIST_KEY, watchlist)
                logger.info(
                    "[watchlist purge] removed %d polluted entries (search "
                    "queries / sentence fragments) before re-screen",
                    removed,
                )
    except Exception as e:
        logger.debug("[watchlist purge] non-fatal: %s", e)

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
                    "old_score": round(old_score, 3),
                    "new_score": round(new_score, 3),
                    "detail": detail,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                # Fan out to linked deals on worsening changes only — don't
                # tag deals when a counterparty is removed from a list or
                # a match score drops. "score_change" with a drop is
                # informational; with a rise it's a risk signal.
                worsening = (
                    change_type in ("new_hit", "new_pep")
                    or (change_type == "score_change" and new_score > old_score)
                )
                if worsening:
                    impacted = await _fan_out_alert_to_deals(alert)
                    if impacted:
                        alert["impacted_deals"] = impacted

                changes.append(alert)

                # Persist alert in Redis
                await rs.lpush(WATCHLIST_ALERTS_KEY, _json.dumps(alert, default=str))
                await rs.ltrim(WATCHLIST_ALERTS_KEY, 0, 499)  # cap at 500
                await rs.expire(WATCHLIST_ALERTS_KEY, _RESCREEN_ALERT_TTL_SECONDS)

        except Exception as e:
            errors.append({"entity": name, "error": str(e)})

    duration_ms = int((time.monotonic() - t0) * 1000)

    # ── Brain hook: feed every cycle into learning so ARIA tracks the rate
    # of change across her watchlist over time. She should know how often
    # her counterparties churn on/off sanctions lists, not just react to
    # individual alerts. Rule Zero: she sees the whole pattern.
    try:
        from . import brain_hook
        change_breakdown = {}
        for c in changes:
            ct = c.get("change_type", "unknown")
            change_breakdown[ct] = change_breakdown.get(ct, 0) + 1
        worsening_count = sum(
            1 for c in changes
            if c.get("change_type") in ("new_hit", "new_pep")
            or (c.get("change_type") == "score_change" and c.get("new_score", 0) > c.get("old_score", 0))
        )
        await brain_hook.absorb(
            module="dd_orchestrator",
            summary=(
                f"Watchlist re-screen: {len(entities)} entities scanned, "
                f"{len(changes)} changes detected ({worsening_count} worsening), "
                f"{len(errors)} errors. Breakdown: {change_breakdown or 'no changes'}"
            ),
            success=(len(errors) == 0),
            confidence="CONFIRMED",
            gap_type="rescreen_errors" if errors else None,
            gap_detail=f"Errors on entities: {[e.get('entity') for e in errors[:5]]}" if errors else None,
        )
    except Exception as e:
        logger.debug("rescreen brain_hook absorb failed (non-fatal): %s", e)

    return {
        "entities_screened": len(entities),
        "changes_detected": changes,
        "errors": errors,
        "duration_ms": duration_ms,
    }


async def get_watchlist_alerts(since_hours: int = 24, user_id: str = "") -> list[dict]:
    """Retrieve recent watchlist re-screen alerts from Redis.

    R-F51: when user_id is supplied, every alert carries an additional
    `read` boolean derived from the per-user "read-until" timestamp
    persisted at crucix:aria:watchlist:read_until:<userId>. Alerts older
    than the read_until timestamp are marked read=True; newer ones
    read=False. This lets the FE compute an unread-count badge without
    a separate roundtrip.
    """
    import json as _json
    from datetime import datetime as _dt
    from . import redis_store as rs

    raw_list = await rs.lrange(WATCHLIST_ALERTS_KEY, 0, 499)
    if not raw_list:
        return []

    # Read-until timestamp per user (best-effort; no error if Redis
    # is unavailable — alerts come back marked unread, which is the
    # conservative default).
    read_until_ts = 0.0
    if user_id:
        try:
            ru = await rs.get(f"crucix:aria:watchlist:read_until:{user_id}")
            if ru:
                read_until_ts = float(ru)
        except Exception:
            pass

    cutoff = _dt.now(timezone.utc).timestamp() - (since_hours * 3600)
    alerts: list[dict] = []
    for raw in raw_list:
        try:
            alert = _json.loads(raw) if isinstance(raw, str) else raw
            ts_str = alert.get("timestamp", "")
            ts = 0.0
            if ts_str:
                ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    continue
            if user_id:
                alert = {**alert, "read": (ts > 0 and ts <= read_until_ts)}
            alerts.append(alert)
        except Exception:
            continue
    return alerts


async def mark_watchlist_alerts_read(user_id: str) -> dict:
    """R-F51: mark all currently visible alerts as read for this user
    by stamping the per-user read-until timestamp. Idempotent — calling
    twice in a row is harmless.
    """
    if not user_id:
        return {"ok": False, "reason": "user_id required"}
    from . import redis_store as rs
    ts = datetime.now(timezone.utc).timestamp()
    try:
        await rs.set(f"crucix:aria:watchlist:read_until:{user_id}", str(ts))
        # 90 days TTL — old read-until stamps are pruned naturally.
        await rs.expire(f"crucix:aria:watchlist:read_until:{user_id}", 90 * 86400)
    except Exception as e:
        return {"ok": False, "reason": f"redis error: {e}"}
    return {"ok": True, "read_until": ts}


async def get_watchlist_unread_count(user_id: str, since_hours: int = 168) -> int:
    """R-F51: light-weight unread badge probe. Counts alerts in the last
    `since_hours` (default 7d) that arrived after the per-user read-until
    timestamp. Skips the JSON re-shape get_watchlist_alerts performs.
    """
    if not user_id:
        return 0
    alerts = await get_watchlist_alerts(since_hours=since_hours, user_id=user_id)
    return sum(1 for a in alerts if not a.get("read", False))
