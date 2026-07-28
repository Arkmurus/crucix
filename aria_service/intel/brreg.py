"""ARIA Brønnøysundregistrene integration — Norwegian (NO) registry lookups.

WHY THIS EXISTS
───────────────
R-F2862. Before this, a Norwegian counterparty produced no registry evidence —
`dd_orchestrator` emitted only a manual-action hint. Norway matters for defence
DD (Kongsberg, Nammo, the state's direct industrial holdings) and NO is a common
counterparty jurisdiction for maritime and energy trade.

SOURCE — OFFICIAL AND FULLY OPEN
────────────────────────────────
    https://data.brreg.no/enhetsregisteret/api      (Enhetsregisteret)

Norwegian government, no API key, no registration (verified live 2026-07-22).
Same §6 reasoning as R-F2861/zefix: a free PRIMARY source beats any paid
aggregator, so this adds no operator dependency.

WHY IT IS RICHER THAN ZEFIX
───────────────────────────
Two fields make it decision-grade rather than merely identifying:

  * REAL STATUS. `konkurs`, `underAvvikling` and
    `underTvangsavviklingEllerTvangsopplosning` are PUBLISHED booleans, so an
    all-false reading is positive EVIDENCE the entity is active. Zefix has no
    such field, so there status must stay unknown. That difference is why this
    module will assert "active" and zefix.py deliberately will not.
    ** If a flag is MISSING we make NO claim — absent is not false. **

  * OFFICERS WITH DATE OF BIRTH, from the open /roller endpoint. A DOB is what
    collapses the false-positive rate when those names are pushed through
    sanctions/PEP screening.

`institusjonellSektorkode` beginning "1" identifies PUBLIC-SECTOR / state-owned
entities — directly load-bearing for RCA screening and defence end-use review.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger(__name__)

_BASE = os.getenv("ARIA_BRREG_BASE", "https://data.brreg.no/enhetsregisteret/api")
_TIMEOUT_S = float(os.getenv("ARIA_BRREG_TIMEOUT_S", "15") or 15)

# Status flags, checked in severity order — the worst true flag wins so a
# bankrupt entity is never reported merely as "in liquidation".
_STATUS_FLAGS: tuple[tuple[str, str], ...] = (
    ("konkurs", "bankrupt"),
    ("underTvangsavviklingEllerTvangsopplosning", "compulsory_liquidation"),
    ("underAvvikling", "in_liquidation"),
)


def _derive_status(unit: dict) -> str:
    """Map the published distress booleans to a status string.

    Returns "" when the flags are ABSENT. brreg publishing `false` is evidence;
    brreg not publishing the field at all is not, and inferring "active" from a
    missing key would manufacture a clean status out of missing data.
    """
    present = [k for k, _ in _STATUS_FLAGS if isinstance(unit.get(k), bool)]
    if not present:
        return ""
    for key, label in _STATUS_FLAGS:
        if unit.get(key) is True:
            return label
    # Every flag we saw is explicitly False.
    return "active"


def _address(unit: dict) -> str:
    addr = unit.get("forretningsadresse") or unit.get("postadresse") or {}
    if not isinstance(addr, dict):
        return ""
    parts: list[str] = []
    lines = addr.get("adresse")
    if isinstance(lines, list):
        parts.extend([str(x) for x in lines if x])
    for key in ("postnummer", "poststed", "land"):
        if addr.get(key):
            parts.append(str(addr[key]))
    return ", ".join(parts)


def _sic_codes(unit: dict) -> list[str]:
    out: list[str] = []
    for key in ("naeringskode1", "naeringskode2", "naeringskode3"):
        node = unit.get(key)
        if isinstance(node, dict) and node.get("kode"):
            out.append(str(node["kode"]))
    return out


def _normalise(unit: dict) -> dict[str, Any]:
    form = unit.get("organisasjonsform") or {}
    sector = unit.get("institusjonellSektorkode") or {}
    sector_code = str(sector.get("kode") or "") or None
    return {
        "organisation_number": str(unit.get("organisasjonsnummer") or "") or None,
        "name": unit.get("navn") or None,
        "legal_form_code": (form.get("kode") if isinstance(form, dict) else None) or None,
        "legal_form": (form.get("beskrivelse") if isinstance(form, dict) else None) or None,
        "registration_date": unit.get("registreringsdatoEnhetsregisteret") or None,
        "founded_date": unit.get("stiftelsesdato") or None,
        "status": _derive_status(unit),
        "address": _address(unit) or None,
        "sic_codes": _sic_codes(unit),
        "employees": unit.get("antallAnsatte"),
        "sector_code": sector_code,
        # Sector codes starting "1" are the public-sector / state-owned block.
        "state_owned": bool(sector_code and sector_code.startswith("1")),
        "former_names": [
            n.get("navn") for n in (unit.get("historiskeNavn") or [])
            if isinstance(n, dict) and n.get("navn")
        ],
        "website": unit.get("hjemmeside") or None,
        "registry": "brreg",
        "jurisdiction": "NO",
    }


async def _get(path: str, params: dict | None = None) -> Any | None:
    """GET against brreg. Returns None on ANY failure, wiring it to the brain."""
    url = f"{_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params,
                                    headers={"Accept": "application/json"})
            if getattr(resp, "status_code", 500) >= 400:
                wire_failure(
                    module="brreg",
                    detail=f"brreg returned HTTP {resp.status_code} for {path}",
                    gap_type="source_failure",
                    source="brreg:_get",
                )
                return None
            return resp.json()
    except Exception as exc:                      # noqa: BLE001 — degrade, never raise
        # §21a — the failure branch must reach the brain. fail_wire cannot help
        # here because we deliberately swallow so a registry outage cannot crash
        # a DD run.
        wire_failure(
            module="brreg",
            detail=f"NO registry call {path} failed: {type(exc).__name__}: {exc}"[:400],
            gap_type="source_failure",
            source="brreg:_get",
        )
        logger.warning("[brreg] %s failed: %s", path, exc)
        return None


async def search_company(name: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Search Norwegian entities by name. Returns [] on any failure."""
    if not (name or "").strip():
        return []
    payload = await _get("/enheter", {"navn": name.strip(), "size": int(limit)})
    if not isinstance(payload, dict):
        return []
    units = ((payload.get("_embedded") or {}).get("enheter")) or []
    out = [_normalise(u) for u in units if isinstance(u, dict) and u.get("navn")]
    logger.info("[brreg] NO registry search '%s' -> %d record(s)", name[:60], len(out))
    # R-F3386 — see ariregister: the success branch carries the record count so a
    # registry that answers but has gone empty is distinguishable from one that
    # errors, and from one nobody queried.
    wire_success(
        module="brreg",
        summary=f"NO registry search returned {len(out)} record(s)",
        source_id="brreg:search_company",
    )
    return out


async def get_company(org_number: str) -> dict[str, Any] | None:
    """Fetch a single entity by its 9-digit organisation number."""
    digits = "".join(ch for ch in str(org_number or "") if ch.isdigit())
    if len(digits) != 9:
        return None
    payload = await _get(f"/enheter/{digits}")
    if not isinstance(payload, dict) or not payload.get("navn"):
        return None
    return _normalise(payload)


async def get_officers(org_number: str) -> list[dict[str, Any]]:
    """Board, CEO and auditor roles, with date of birth where published.

    A role whose person block is empty (commonly a FIRM acting as auditor) is
    DROPPED rather than emitted with a blank name — a nameless officer in a DD
    report is a phantom, and downstream screening would treat it as a real one.
    """
    digits = "".join(ch for ch in str(org_number or "") if ch.isdigit())
    if len(digits) != 9:
        return []
    payload = await _get(f"/enheter/{digits}/roller")
    if not isinstance(payload, dict):
        return []
    officers: list[dict[str, Any]] = []
    for group in payload.get("rollegrupper") or []:
        if not isinstance(group, dict):
            continue
        for role in group.get("roller") or []:
            if not isinstance(role, dict):
                continue
            person = role.get("person") or {}
            names = person.get("navn") or {} if isinstance(person, dict) else {}
            full = " ".join(
                str(names.get(part)) for part in ("fornavn", "mellomnavn", "etternavn")
                if names.get(part)
            ).strip()
            if not full:
                continue                          # firm/blank role — never a phantom officer
            role_type = role.get("type") or {}
            officers.append({
                "name": full,
                "role": (role_type.get("beskrivelse") if isinstance(role_type, dict) else None) or "",
                "date_of_birth": person.get("fodselsdato") or None,
                "source": "brreg:roller",
            })
    return officers
