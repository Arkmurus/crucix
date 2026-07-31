"""ARIA Zefix integration — Swiss (CH) commercial-register lookups.

WHY THIS EXISTS
───────────────
Before R-F2861 a Swiss counterparty produced no registry evidence at all:
`dd_orchestrator` only emitted a manual-action hint ("check Zefix at zefix.ch"),
so "verified legal identity" could never be satisfied from a primary source for
CH entities. Switzerland is over-represented in exactly ARIA's market —
commodity traders, holding structures and defence intermediaries — so this was a
material coverage hole, visible in the SOCAR (Geneva) run.

WHICH SOURCE, AND WHY NOT THE OBVIOUS ONE
─────────────────────────────────────────
The advertised REST API (`www.zefix.admin.ch/ZefixPublicREST`) is NOT open.
Verified live 2026-07-22 — every endpoint returns HTTP 401 without registered
credentials (`/company/search`, `/firm/search`, `/firm/{uid}`, `/legalForm`,
`/community`).

The SAME federal dataset (Federal Office of Justice, `foj-zefix`) is published as
OPEN linked data on LINDAS and needs no credentials at all:

    POST https://lindas.admin.ch/query        (SPARQL 1.1, verified HTTP 200)

So this module needs no API key and adds no operator dependency — deliberately,
per CLAUDE.md §6 (mirror Claude Code: no paid third-party where a free primary
source exists).

WHAT IT RETURNS
───────────────
Per company: the registered name, the UID (the Swiss registration number a DD
report must cite, normalised to `CHE-123.456.789`), the eCH-97 legal-form code,
the municipality id, and the registered purpose.

An ABSENT field is returned as None, never as "" or a guess — an unsourced value
in a compliance report is precisely the failure ARIA's USP forbids.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger(__name__)

# Overridable for tests / a future self-hosted mirror.
_ENDPOINT = os.getenv("ARIA_ZEFIX_SPARQL_URL", "https://lindas.admin.ch/query")
# A name CONTAINS scan over the national dataset measured ~9s live, so the
# bound is generous but hard — a DD phase budget must never hang on a registry.
_TIMEOUT_S = float(os.getenv("ARIA_ZEFIX_TIMEOUT_S", "25") or 25)

_ZEFIX_CLASS = "https://schema.ld.admin.ch/ZefixOrganisation"

# .../company/225002/UID/CHE102145963  ->  CHE-102.145.963
_UID_RE = re.compile(r"/UID/CHE(\d{9})\b")


def _escape_literal(value: str) -> str:
    """Escape a user string for inclusion in a SPARQL string literal.

    Backslash FIRST, then the quote — the other order would double-escape the
    backslashes introduced by the quote replacement. Without this a `"` in the
    needle closes the literal and the remainder is parsed as query syntax.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _uid_from_uri(uri: str | None) -> str | None:
    """Normalise the UID identifier URI to the canonical CHE-xxx.xxx.xxx form."""
    m = _UID_RE.search(uri or "")
    if not m:
        return None
    d = m.group(1)
    return f"CHE-{d[0:3]}.{d[3:6]}.{d[6:9]}"


def _tail(uri: str | None) -> str | None:
    """Last path segment of a LINDAS URI (legal-form / municipality code)."""
    if not uri:
        return None
    tail = uri.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _binding(row: dict, key: str) -> str | None:
    """Read one SPARQL binding. Absent stays None — never coerced to ''."""
    cell = row.get(key)
    if not isinstance(cell, dict):
        return None
    value = cell.get("value")
    return value if value else None


def build_query(name: str, limit: int) -> str:
    """The SPARQL sent to LINDAS. Separate so tests can assert its shape."""
    needle = _escape_literal((name or "").strip().lower())
    return (
        'PREFIX schema: <http://schema.org/>\n'
        'SELECT ?company ?name ?uid ?legalForm ?municipality ?description WHERE {\n'
        f'  ?company a <{_ZEFIX_CLASS}> ; schema:name ?name .\n'
        '  OPTIONAL { ?company schema:identifier ?uid . '
        'FILTER(CONTAINS(STR(?uid), "/UID/")) }\n'
        '  OPTIONAL { ?company schema:additionalType ?legalForm }\n'
        # NOT schema:municipality — that expands to schema.org, which Zefix does
        # NOT use for this. The real predicate is in the admin.ch schema
        # namespace; verified live 2026-07-22. The fixture test passed with the
        # wrong namespace because the fixture supplies the binding either way —
        # only the LIVE smoke (§23) caught it, silently returning null.
        '  OPTIONAL { ?company <https://schema.ld.admin.ch/municipality> ?municipality }\n'
        '  OPTIONAL { ?company schema:description ?description }\n'
        f'  FILTER(CONTAINS(LCASE(STR(?name)), "{needle}"))\n'
        f'}} LIMIT {int(limit)}'
    )


async def search_company(name: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Look up Swiss companies by (partial) registered name.

    Returns [] on ANY failure — transport error, non-200, or unparseable body.
    A registry outage must degrade a DD run to an honest data gap, never crash
    it and never yield a partial record that could read as complete.
    """
    if not (name or "").strip():
        return []
    query = build_query(name, limit)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                _ENDPOINT,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )
            if getattr(resp, "status_code", 500) >= 400:
                wire_failure(
                    module="zefix",
                    detail=f"LINDAS returned HTTP {resp.status_code} for a CH registry search",
                    gap_type="source_failure",
                    source="zefix:search_company",
                )
                return []
            payload = resp.json()
    except Exception as exc:                      # noqa: BLE001 — degrade, never raise
        # §21a — the FAILURE branch must reach the brain, not just a log line.
        # fail_wire cannot do it here because we deliberately swallow the error.
        wire_failure(
            module="zefix",
            detail=f"CH registry lookup failed: {type(exc).__name__}: {exc}"[:400],
            gap_type="source_failure",
            source="zefix:search_company",
        )
        logger.warning("[zefix] CH registry lookup failed: %s", exc)
        return []

    try:
        rows = (payload or {}).get("results", {}).get("bindings", []) or []
    except Exception as exc:                      # noqa: BLE001 — degrade, never raise
        # R-F3567 — this was a bare `return []`. An unparseable 200 from the
        # registry is indistinguishable from "no such company" to every caller,
        # which is the false-clean shape: a DD run would record a clean CH
        # registry check that never actually parsed.
        wire_failure(
            module="zefix",
            detail=f"LINDAS returned an unparseable result body: {type(exc).__name__}"[:400],
            gap_type="source_failure",
            source="zefix:search_company",
        )
        logger.warning("[zefix] unparseable LINDAS payload", exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        company_uri = _binding(row, "company")
        name_value = _binding(row, "name")
        if not name_value:
            continue                              # a record with no name is unusable
        out.append({
            "name": name_value,
            "uid": _uid_from_uri(_binding(row, "uid")),
            "legal_form_code": _tail(_binding(row, "legalForm")),
            "municipality_id": _tail(_binding(row, "municipality")),
            "purpose": _binding(row, "description"),
            "source_url": company_uri,
            "registry": "zefix",
            "jurisdiction": "CH",
        })
    logger.info("[zefix] CH registry search '%s' -> %d record(s)", name[:60], len(out))
    # §21a SUCCESS branch — the registry answered and the body parsed. Zero rows
    # is still a successful check (the company is genuinely not on the CH
    # register); it is the failure branches above that must never look like this.
    wire_success(
        module="zefix",
        summary=f"CH registry search returned {len(out)} record(s)",
        detail=f"query='{name[:60]}' limit={limit}",
        entity_name=name[:120],
        confidence="CONFIRMED",
        source_id="zefix:search_company",
    )
    return out
