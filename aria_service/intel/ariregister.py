"""ARIA ariregister integration — Estonian (EE) business register lookups.

R-F2865. Third jurisdiction recovered from the manual-action-only list, after
CH (R-F2861) and NO (R-F2862).

SOURCE — OFFICIAL AND OPEN
──────────────────────────
    https://ariregister.rik.ee/est/api/autocomplete

Estonian Centre of Registers and Information Systems (RIK). No credentials
(verified live 2026-07-22), so this adds no operator dependency (§6).

CAVEAT, stated plainly: this is the register's own lookup endpoint rather than a
published, versioned API contract. It is the same endpoint the public register
UI uses, so it will not silently return WRONG data — but it could change shape.
Every field is read defensively and any parse failure degrades to "no result",
never to a partial record that could read as complete.

THE STATUS RULE — and why it differs from Norway and Switzerland
────────────────────────────────────────────────────────────────
Estonia publishes a single-letter status. "R" (registrisse kantud — entered in
the register) is well established and maps to active. Any OTHER code is NOT
guessed: status is left empty, the raw code is preserved as evidence, and the
caller raises a data gap.

    NO — asserts active, because brreg publishes explicit distress booleans
    CH — never asserts, because Zefix's open projection has no status field
    EE — asserts active ONLY for the one code we can actually evidence

Only "R" was observed while building this. Inventing a mapping for unseen codes
would put a fabricated registration status on a counterparty — a material error,
not a cosmetic one.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger(__name__)

_ENDPOINT = os.getenv(
    "ARIA_ARIREGISTER_URL", "https://ariregister.rik.ee/est/api/autocomplete"
)
_TIMEOUT_S = float(os.getenv("ARIA_ARIREGISTER_TIMEOUT_S", "15") or 15)

# Only codes we can actually evidence. Deliberately NOT exhaustive — an unmapped
# code yields no status claim rather than a guess.
_STATUS_MAP = {"R": "active"}


def _clean(value: Any) -> str | None:
    """Trim to a real value. Absent/blank stays None, never ""."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise(row: dict) -> dict[str, Any] | None:
    name = _clean(row.get("name"))
    if not name:
        return None                       # a nameless record is unusable
    raw_status = _clean(row.get("status")) or ""
    reg_code = _clean(row.get("reg_code"))
    return {
        "registration_code": reg_code,
        "name": name,
        "status": _STATUS_MAP.get(raw_status, ""),
        "status_code_raw": raw_status,    # preserved as evidence for the gap text
        "address": _clean(row.get("legal_address")),
        "postal_code": _clean(row.get("zip_code")),
        "legal_form_code": _clean(row.get("legal_form")),
        "former_names": [
            n for n in (row.get("historical_names") or [])
            if isinstance(n, str) and n.strip()
        ],
        "source_url": _clean(row.get("url")) or (
            f"https://ariregister.rik.ee/est/company/{reg_code}" if reg_code
            else "https://ariregister.rik.ee"
        ),
        "registry": "ariregister",
        "jurisdiction": "EE",
    }


async def search_company(name: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Search Estonian entities by name. Returns [] on ANY failure."""
    if not (name or "").strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _ENDPOINT,
                params={"q": name.strip()},
                headers={"Accept": "application/json"},
            )
            if getattr(resp, "status_code", 500) >= 400:
                wire_failure(
                    module="ariregister",
                    detail=f"ariregister returned HTTP {resp.status_code}",
                    gap_type="source_failure",
                    source="ariregister:search_company",
                )
                return []
            payload = resp.json()
    except Exception as exc:                      # noqa: BLE001 — degrade, never raise
        # §21a — the failure branch must reach the brain; fail_wire cannot,
        # because we deliberately swallow so a registry outage cannot crash a DD.
        wire_failure(
            module="ariregister",
            detail=f"EE registry lookup failed: {type(exc).__name__}: {exc}"[:400],
            gap_type="source_failure",
            source="ariregister:search_company",
        )
        logger.warning("[ariregister] EE registry lookup failed: %s", exc)
        return []

    if not isinstance(payload, dict) or payload.get("status") != "OK":
        # The endpoint reports its own status — a non-OK body is not data.
        return []
    rows = payload.get("data") or []
    out = [r for r in (_normalise(x) for x in rows if isinstance(x, dict)) if r]
    logger.info("[ariregister] EE registry search '%s' -> %d record(s)", name[:60], len(out))
    # R-F3386 — §21a: the SUCCESS branch must reach the brain too. Failure-only
    # wiring cannot distinguish "the EE registry errored" from "the EE registry
    # answered and returned nothing", and the second is what a source going dark
    # actually looks like. The record count is carried so a collapse to zero is
    # visible rather than inferred.
    wire_success(
        module="ariregister",
        summary=f"EE registry search returned {len(out)} record(s)",
        source_id="ariregister:search_company",
    )
    return out[:limit]
