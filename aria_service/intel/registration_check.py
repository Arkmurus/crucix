"""Arkmurus portal-registration status checker.

Checks whether Arkmurus Group Ltd is registered as a supplier / vendor /
consultant on the portals ARIA tracks. Writes the results to the knowledge
base as facts so downstream prompts can answer "are we registered on X?"
without re-running the check.

What can be automated (zero credentials, site-restricted search)
────────────────────────────────────────────────────────────────
  - UNGM (ungm.org)     — public vendor listings are indexed
  - SAM.gov             — entity records are partially indexed

What cannot be automated (login-gated supplier directories)
───────────────────────────────────────────────────────────
  - NSPA SRBPS          — supplier portal is behind nato.int login
  - Defence Sourcing Portal (MoD)  — Bravo Solution login required
  - AfDB DACON          — Client Connection portal requires login

For the login-gated set we surface precise manual-check steps instead of
false-negative automated answers.

Not registration-gated (no check needed)
────────────────────────────────────────
  - Contracts Finder    — notice board, no vendor registration
  - TED                 — notice board, no vendor registration

Public API
──────────
  async check_all(company_name=None, *, persist=True) -> dict
  async check_portal(portal_id, company_name=None) -> dict
  def list_portals() -> list[dict]   # metadata only, no network
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("aria.registration_check")


_DEFAULT_LEGAL_NAME = os.getenv("ARKMURUS_LEGAL_NAME", "Arkmurus Group Ltd").strip() or "Arkmurus Group Ltd"
_TRADING_NAME = "Arkmurus"


# Portal registry. Order shapes the output.
PORTALS: list[dict] = [
    {
        "id": "ungm",
        "name": "UNGM — UN Global Marketplace",
        "url": "https://www.ungm.org",
        "method": "site_search",
        "search_domain": "ungm.org",
        "vendor_path_hints": ["/vendor", "/public/companyprofile", "/notice/vendor"],
        "broker_rationale": "UN system procurement — peacekeeping + humanitarian",
        "manual_steps": (
            "1. Open https://www.ungm.org/Public/Account/Login\n"
            "2. If Arkmurus has credentials, log in → Company Profile page\n"
            "   shows registration level (L1/L2) and UNSPSC codes.\n"
            "3. If no credentials exist, the company is not registered — "
            "free 1-2 week Level 1 registration at /Vendor/Account/Register."
        ),
    },
    {
        "id": "sam_gov",
        "name": "SAM.gov — US Federal Procurement",
        "url": "https://sam.gov",
        "method": "site_search",
        "search_domain": "sam.gov",
        "vendor_path_hints": ["/entity", "/workspace/contract"],
        "broker_rationale": "DoD + DSCA/FMS + USASpending awards",
        "manual_steps": (
            "1. Open https://sam.gov/search?index=ei&q=Arkmurus\n"
            "2. If an Entity Information result appears with matching "
            "CAGE code, registration is active. Record UEI.\n"
            "3. If nothing appears, registration has not been completed. "
            "Arkmurus already holds a CAGE (per project memory 2026-04-19) "
            "so the registration path is UEI-only (2-3 weeks)."
        ),
    },
    {
        "id": "nspa_srbps",
        "name": "NSPA SRBPS — NATO Support and Procurement Agency",
        "url": "https://www.nspa.nato.int",
        "method": "manual_required",
        "broker_rationale": "NATO-wide sustainment + multinational consolidation",
        "manual_steps": (
            "1. SRBPS supplier directory is NOT public. Log in at "
            "https://eportal.nspa.nato.int/ as Arkmurus.\n"
            "2. Supplier Profile tab shows registration status, "
            "declared capability codes, and CAGE linkage.\n"
            "3. If no profile exists, start registration via SRBPS "
            "self-service (CAGE prerequisite already satisfied)."
        ),
    },
    {
        "id": "dsp_mod",
        "name": "Defence Contracts Portal — UK MoD (Bravo Solution eSourcing)",
        "url": "https://contracts.mod.uk",
        "method": "manual_required",
        "broker_rationale": "UK MoD above-threshold workflow (ITT, PQQ, clarifications)",
        "manual_steps": (
            "1. Log in at https://contracts.mod.uk/esop/ogc-host/public/mod/web/login.html "
            "as Arkmurus.\n"
            "2. My Organisation → shows registration status + DUNS + categories.\n"
            "3. If no login exists, registration is free via the supplier "
            "self-registration link on the login page — requires DUNS "
            "number and UK company identity."
        ),
    },
    {
        "id": "afdb_dacon",
        "name": "AfDB DACON — African Development Bank Consultants DB",
        "url": "https://clientconnection.afdb.org",
        "method": "manual_required",
        "broker_rationale": "Advisory / consulting role on AfDB-financed programmes",
        "workflow_note": (
            "Primarily DOWNLOAD-based: AfDB publishes consultancy assignment "
            "TORs (Terms of Reference) as downloadable documents; Arkmurus "
            "downloads, prepares proposals offline, uploads response. ARIA "
            "must remind operator on internal proposal deadlines, not rely "
            "on the portal's event log alone."
        ),
        "manual_steps": (
            "1. Log in at https://clientconnection.afdb.org (Client Connection).\n"
            "2. DACON module → Consultant Profile page shows registration "
            "status + declared disciplines.\n"
            "3. If no profile exists, register via DACON self-service. "
            "Separate from the STEP tender portal — both may be needed.\n"
            "4. Expect most active engagement via downloaded TOR packs + "
            "offline proposal preparation; the portal itself is lightweight."
        ),
    },
    {
        "id": "contracts_finder",
        "name": "Contracts Finder — UK notice board",
        "url": "https://www.contractsfinder.service.gov.uk",
        "method": "not_applicable",
        "broker_rationale": "Public-sector notice board — no vendor registration needed",
    },
    {
        "id": "ted",
        "name": "TED — EU Tenders Electronic Daily",
        "url": "https://ted.europa.eu",
        "method": "not_applicable",
        "broker_rationale": "EU notice board — no vendor registration needed, "
                           "saved-search alerts only",
    },
]


def list_portals() -> list[dict]:
    """Return portal metadata (no network). Useful for UI listings."""
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "url": p["url"],
            "method": p["method"],
            "broker_rationale": p["broker_rationale"],
        }
        for p in PORTALS
    ]


def _classify_search_evidence(
    portal: dict,
    results: list[dict],
    company_name: str,
) -> tuple[str, float, list[dict]]:
    """Interpret site-restricted search hits into a registration status.

    Conservative on both sides: only call LIKELY_REGISTERED when at least
    one indexed result (a) mentions the company name in title or snippet
    AND (b) sits on a URL path that matches the portal's vendor-page hints.
    Zero results → LIKELY_NOT_REGISTERED. Anything in between → AUTOMATED_UNKNOWN.
    """
    cn = (company_name or "").lower()
    trading = _TRADING_NAME.lower()
    hints = [h.lower() for h in portal.get("vendor_path_hints") or []]

    evidence: list[dict] = []
    strong_hits = 0

    for r in (results or [])[:10]:
        title = (r.get("title") or "").lower()
        snippet = (r.get("snippet") or "").lower()
        url = (r.get("url") or "").lower()

        name_hit = (cn and cn in (title + " " + snippet)) or \
                   (trading and trading in (title + " " + snippet))
        path_hit = any(h in url for h in hints) if hints else False

        if name_hit:
            evidence.append({
                "title": r.get("title"),
                "url": r.get("url"),
                "snippet": (r.get("snippet") or "")[:200],
                "strong": bool(path_hit),
            })
            if path_hit:
                strong_hits += 1

    if strong_hits >= 1:
        return "LIKELY_REGISTERED", min(0.5 + 0.15 * strong_hits, 0.85), evidence
    if evidence:
        return "AUTOMATED_UNKNOWN", 0.45, evidence
    # Zero hits at all for the company on this domain — not definitive
    # (pages may be noindexed) but suggestive.
    return "LIKELY_NOT_REGISTERED", 0.55, []


async def _check_via_search(portal: dict, company_name: str) -> dict:
    """Site-restricted search on the portal domain, looking for any
    indexed page mentioning Arkmurus."""
    from . import researcher

    domain = portal.get("search_domain") or ""
    if not domain:
        return {"status": "AUTOMATED_UNKNOWN",
                "confidence": 0.0,
                "evidence": [],
                "error": "no search_domain configured"}

    # Two queries: legal name AND trading name. Union the results.
    queries = [
        f'site:{domain} "{company_name}"',
        f'site:{domain} "{_TRADING_NAME}"',
    ]
    all_results: list[dict] = []
    seen_urls: set[str] = set()
    search_error: str | None = None

    for q in queries:
        try:
            r = await researcher.web_search(q, max_results=8, timeout=15.0)
        except Exception as e:
            logger.warning("web_search failed for %r: %s", q, e)
            search_error = str(e)
            continue
        if not r.get("ok"):
            search_error = r.get("error") or "search not ok"
            continue
        for item in (r.get("results") or []):
            url = item.get("url") or ""
            if url and url in seen_urls:
                continue
            seen_urls.add(url)
            all_results.append(item)

    status, conf, evidence = _classify_search_evidence(portal, all_results, company_name)
    result = {
        "status": status,
        "confidence": conf,
        "evidence": evidence,
        "evidence_count": len(evidence),
    }
    if search_error and not all_results:
        # If both queries errored out and nothing returned, don't assert
        # "not registered" — the check itself was unreliable.
        result["status"] = "AUTOMATED_UNKNOWN"
        result["confidence"] = 0.0
        result["error"] = search_error
    return result


async def check_portal(portal_id: str, company_name: str | None = None) -> dict:
    """Run the check for a single portal."""
    portal = next((p for p in PORTALS if p["id"] == portal_id), None)
    if not portal:
        return {"error": f"unknown portal: {portal_id}"}

    company_name = (company_name or _DEFAULT_LEGAL_NAME).strip()
    t0 = time.time()
    base = {
        "portal_id": portal["id"],
        "portal_name": portal["name"],
        "url": portal["url"],
        "method": portal["method"],
        "company_name": company_name,
        "broker_rationale": portal["broker_rationale"],
    }

    if portal["method"] == "not_applicable":
        base["status"] = "NOT_APPLICABLE"
        base["confidence"] = 1.0
        base["note"] = ("Notice board — no vendor registration required. "
                        "Saved-search alerts only.")
    elif portal["method"] == "manual_required":
        base["status"] = "MANUAL_REQUIRED"
        base["confidence"] = 0.0
        base["manual_steps"] = portal["manual_steps"]
    elif portal["method"] == "site_search":
        inner = await _check_via_search(portal, company_name)
        base.update(inner)
        # Manual fallback steps are always useful when the automated check
        # returns UNKNOWN or LIKELY_NOT_REGISTERED — operator can verify.
        if base.get("status") in ("AUTOMATED_UNKNOWN", "LIKELY_NOT_REGISTERED"):
            base["manual_steps"] = portal.get("manual_steps")
    else:
        base["status"] = "UNKNOWN_METHOD"
        base["confidence"] = 0.0

    # Pass through any optional operational notes declared on the portal
    # (e.g. workflow_note for download-driven portals like AfDB DACON).
    if portal.get("workflow_note"):
        base["workflow_note"] = portal["workflow_note"]

    base["duration_ms"] = int((time.time() - t0) * 1000)
    return base


async def check_all(
    company_name: str | None = None,
    *,
    persist: bool = True,
) -> dict:
    """Run registration checks for every portal in the registry."""
    import asyncio

    company_name = (company_name or _DEFAULT_LEGAL_NAME).strip()
    t0 = time.time()

    # Run all portal checks concurrently — each check either makes two
    # cheap search calls or returns a constant.
    portal_ids = [p["id"] for p in PORTALS]
    results = await asyncio.gather(
        *[check_portal(pid, company_name) for pid in portal_ids],
        return_exceptions=True,
    )

    normalised: list[dict] = []
    for pid, r in zip(portal_ids, results):
        if isinstance(r, Exception):
            normalised.append({
                "portal_id": pid,
                "status": "CHECK_ERROR",
                "error": str(r),
            })
        else:
            normalised.append(r)

    summary = {
        "likely_registered": [r for r in normalised if r.get("status") == "LIKELY_REGISTERED"],
        "likely_not_registered": [r for r in normalised if r.get("status") == "LIKELY_NOT_REGISTERED"],
        "manual_required": [r for r in normalised if r.get("status") == "MANUAL_REQUIRED"],
        "automated_unknown": [r for r in normalised if r.get("status") == "AUTOMATED_UNKNOWN"],
        "not_applicable": [r for r in normalised if r.get("status") == "NOT_APPLICABLE"],
        "errors": [r for r in normalised if r.get("status") in ("CHECK_ERROR", "UNKNOWN_METHOD")],
    }

    payload = {
        "ok": True,
        "company_name": company_name,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "portals": normalised,
        "counts": {k: len(v) for k, v in summary.items()},
        "duration_ms": int((time.time() - t0) * 1000),
    }

    # Persist to knowledge + brain_hook so ARIA can answer registration
    # questions from memory between runs.
    if persist:
        try:
            from . import knowledge
            fact_lines = []
            for r in normalised:
                line = (f"{r.get('portal_name')}: {r.get('status')} "
                        f"(confidence={r.get('confidence', 0):.2f})")
                fact_lines.append(line)
            await knowledge.store_fact(
                topic=f"Arkmurus portal-registration status — {payload['checked_at'][:10]}",
                content=(
                    f"Registration status for {company_name}:\n\n"
                    + "\n".join(fact_lines)
                    + "\n\nMANUAL_REQUIRED portals are login-gated and could "
                      "not be verified automatically — operator must check each "
                      "one by hand."
                ),
                source="registration_check",
                confidence="ASSESSED",
            )
        except Exception as e:
            logger.warning("registration_check persist failed: %s", e)
            payload["persist_error"] = str(e)

        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="registration_check",
                summary=(
                    f"Portal registration check for {company_name}: "
                    f"{payload['counts']['likely_registered']} likely registered, "
                    f"{payload['counts']['likely_not_registered']} likely not, "
                    f"{payload['counts']['manual_required']} manual-required"
                ),
                success=True,
                confidence="ASSESSED",
            )
        except Exception:
            pass

    return payload


def render_markdown(payload: dict) -> str:
    """Operator-facing Markdown summary of check_all() result."""
    if not payload.get("ok"):
        return f"**Registration check error:** {payload.get('error') or 'unknown'}"

    counts = payload.get("counts") or {}
    lines: list[str] = []
    lines.append(f"# Portal registration status — {payload.get('company_name')}")
    lines.append(f"*Checked {payload.get('checked_at')}  ·  "
                 f"{payload.get('duration_ms', 0)} ms*")
    lines.append("")
    lines.append(
        f"- **Likely registered:** {counts.get('likely_registered', 0)}\n"
        f"- **Likely NOT registered:** {counts.get('likely_not_registered', 0)}\n"
        f"- **Manual check required:** {counts.get('manual_required', 0)}\n"
        f"- **Automated unknown:** {counts.get('automated_unknown', 0)}\n"
        f"- **Not applicable (notice boards):** {counts.get('not_applicable', 0)}"
    )
    lines.append("")

    for r in payload.get("portals") or []:
        name = r.get("portal_name", r.get("portal_id"))
        status = r.get("status", "?")
        conf = r.get("confidence", 0)
        url = r.get("url", "")
        lines.append(f"## {name}")
        lines.append(f"Status: **{status}**  ·  confidence {conf:.2f}  ·  "
                     f"[{url}]({url})")
        if r.get("evidence"):
            lines.append("")
            lines.append("Evidence:")
            for e in r["evidence"][:3]:
                title = (e.get("title") or "").strip()
                url2 = e.get("url") or ""
                lines.append(f"  - [{title[:120]}]({url2})")
        if r.get("workflow_note"):
            lines.append("")
            lines.append(f"**Workflow:** {r['workflow_note']}")
        if r.get("manual_steps"):
            lines.append("")
            lines.append("Manual verification steps:")
            for step in r["manual_steps"].split("\n"):
                if step.strip():
                    lines.append(f"  > {step.strip()}")
        if r.get("note"):
            lines.append("")
            lines.append(f"_{r['note']}_")
        lines.append("")

    return "\n".join(lines)
