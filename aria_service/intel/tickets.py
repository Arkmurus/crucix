"""ARIA ticket system — durable bug / feature / infra reports.

Purpose
───────
ARIA routinely spots problems during operation — tripped circuit breakers,
failed sources, fabricated answers from old data, etc. Before this module
she mentioned them in chat and even fabricated ticket IDs (incident
2026-04-21: "ARK-DEV-001", never filed). Now she can raise real tickets:

  - Primary surface: GitHub Issues (label `aria-raised`). Visible to
    Claude Code sessions via `gh issue list --label aria-raised`, which
    is how the developer receives them across sessions without a secret
    exchange.
  - Mirror surface: Airtable "Dev Tickets" table (optional). Gives the
    operator a filterable queue inside the Arkmurus base.

Either surface is env-gated and silently no-ops when unconfigured, so
local dev / tests don't blow up.

Configuration
─────────────
GitHub (primary — this is what Claude Code sees):
  GITHUB_TOKEN          Personal access token. Fine-grained token needs
                        `Issues: Read and write` on the target repo.
                        Classic PAT needs `repo` scope.
  GITHUB_REPO           `owner/repo`. Default: `Arkmurus/crucix`.
  GITHUB_ARIA_LABEL     Label applied to every ARIA ticket. Default
                        `aria-raised`. Must exist in the repo; if it
                        doesn't, the issue still gets created — GitHub
                        ignores unknown labels silently.

Airtable (mirror — optional, reuses airtable_sync env vars):
  AIRTABLE_PAT          Already set for Task Register sync.
  AIRTABLE_BASE_ID      Already set.
  AIRTABLE_TICKETS_TABLE  Default `Dev Tickets`. If the table doesn't
                          exist the mirror write fails gracefully;
                          GitHub still gets the issue.

Killswitches (set to "0" to disable that surface without removing env):
  ARIA_TICKETS_GITHUB_ENABLED   default "1"
  ARIA_TICKETS_AIRTABLE_ENABLED default "1"

Anti-fabrication
────────────────
Constitution clause 22 (aria_engine.py) forbids ARIA from citing ticket
IDs she didn't obtain from raise_ticket(). The returned payload includes
the authoritative `ticket_id` string (`GH-42` when GitHub succeeded,
`AT-recXXXX` when only Airtable succeeded) which is the ONLY form ARIA
is permitted to quote in a reply.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("aria.intel.tickets")

_DEFAULT_REPO = "Arkmurus/crucix"  # override with GITHUB_REPO env

_DEFAULT_LABEL = "aria-raised"
_DEFAULT_AIRTABLE_TABLE = "Dev Tickets"
_GITHUB_API = "https://api.github.com"
_AIRTABLE_API = "https://api.airtable.com/v0"
_HTTP_TIMEOUT = 12.0

# Severity → GitHub label mapping. Every ticket gets `aria-raised` plus one
# severity label so the operator can filter.
_SEVERITY_LABELS = {
    "CRITICAL": "severity-critical",
    "HIGH": "severity-high",
    "MEDIUM": "severity-medium",
    "LOW": "severity-low",
}

# Category → GitHub label for downstream triage.
_CATEGORY_LABELS = {
    "infra": "category-infra",
    "pipeline": "category-pipeline",
    "code": "category-code",
    "data": "category-data",
    "llm": "category-llm",
    "prompt": "category-prompt",
    "ux": "category-ux",
    "other": "category-other",
}


def _github_enabled() -> tuple[bool, str]:
    if os.getenv("ARIA_TICKETS_GITHUB_ENABLED", "1").strip() == "0":
        return False, "killswitch"
    if not (os.getenv("GITHUB_TOKEN") or "").strip():
        return False, "no_github_token"
    return True, ""


def _airtable_enabled() -> tuple[bool, str]:
    if os.getenv("ARIA_TICKETS_AIRTABLE_ENABLED", "1").strip() == "0":
        return False, "killswitch"
    if not (os.getenv("AIRTABLE_PAT") or "").strip():
        return False, "no_airtable_pat"
    return True, ""


def _normalise_severity(s: str | None) -> str:
    s = (s or "MEDIUM").strip().upper()
    return s if s in _SEVERITY_LABELS else "MEDIUM"


def _normalise_category(c: str | None) -> str:
    c = (c or "other").strip().lower()
    return c if c in _CATEGORY_LABELS else "other"


def _build_issue_body(
    *,
    symptom: str,
    context: str,
    severity: str,
    category: str,
    suggested_fix: str | None,
    source: str | None,
) -> str:
    """Markdown body for a GitHub issue. Designed to be skimmable + actionable."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"**Severity:** {severity}  ",
        f"**Category:** {category}  ",
        f"**Raised:** {now}  ",
    ]
    if source:
        lines.append(f"**Observed in:** {source}  ")
    lines.append("")
    lines.append("## Symptom")
    lines.append(symptom.strip() or "_(no symptom provided)_")
    lines.append("")
    lines.append("## Context")
    lines.append(context.strip() or "_(no additional context)_")
    if suggested_fix:
        lines.append("")
        lines.append("## Suggested fix")
        lines.append(suggested_fix.strip())
    lines.append("")
    lines.append("---")
    lines.append(
        "_Raised automatically by ARIA via `raise_ticket()`. "
        "See `aria_service/intel/tickets.py` for the module, "
        "constitution clause 22 for the anti-fabrication rule._"
    )
    return "\n".join(lines)


async def _create_github_issue(
    *, title: str, body: str, labels: list[str]
) -> dict[str, Any]:
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    repo = (os.getenv("GITHUB_REPO") or _DEFAULT_REPO).strip()
    url = f"{_GITHUB_API}/repos/{repo}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "aria-raise-ticket",
    }
    payload = {"title": title[:250], "body": body, "labels": labels}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=headers)
        if r.status_code in (200, 201):
            data = r.json()
            return {
                "ok": True,
                "number": data.get("number"),
                "url": data.get("html_url"),
                "ticket_id": f"GH-{data.get('number')}",
            }
        logger.warning(
            "GitHub issue create failed: %s %s", r.status_code, r.text[:200]
        )
        return {"ok": False, "reason": f"http_{r.status_code}", "detail": r.text[:200]}
    except httpx.TimeoutException:
        return {"ok": False, "reason": "timeout"}
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("GitHub issue create exception: %s", e)
        return {"ok": False, "reason": "exception", "detail": str(e)[:200]}


async def _create_airtable_ticket(
    *,
    title: str,
    symptom: str,
    context: str,
    severity: str,
    category: str,
    suggested_fix: str | None,
    source: str | None,
    github_url: str | None,
) -> dict[str, Any]:
    pat = (os.getenv("AIRTABLE_PAT") or "").strip()
    base = (os.getenv("AIRTABLE_BASE_ID") or "appq2TB9F6NRxAB8f").strip()
    table = (os.getenv("AIRTABLE_TICKETS_TABLE") or _DEFAULT_AIRTABLE_TABLE).strip()
    from urllib.parse import quote

    url = f"{_AIRTABLE_API}/{base}/{quote(table)}"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
    }
    # Field names chosen to match a sensible default schema. If the user's
    # table has different field names, the write fails with 422 and we log
    # it once. Operator then either renames fields or sets the env var to
    # a different table.
    fields = {
        "Title": title[:250],
        "Severity": severity,
        "Category": category,
        "Symptom": symptom,
        "Context": context,
        "Status": "open",
        "Raised": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if suggested_fix:
        fields["Suggested Fix"] = suggested_fix
    if source:
        fields["Source"] = source
    if github_url:
        fields["GitHub URL"] = github_url

    payload = {"records": [{"fields": fields}], "typecast": True}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=headers)
        if r.status_code in (200, 201):
            data = r.json()
            rec = (data.get("records") or [{}])[0]
            rec_id = rec.get("id")
            return {"ok": True, "record_id": rec_id, "ticket_id": f"AT-{rec_id}"}
        logger.warning(
            "Airtable ticket create failed: %s %s", r.status_code, r.text[:200]
        )
        return {"ok": False, "reason": f"http_{r.status_code}", "detail": r.text[:200]}
    except httpx.TimeoutException:
        return {"ok": False, "reason": "timeout"}
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("Airtable ticket create exception: %s", e)
        return {"ok": False, "reason": "exception", "detail": str(e)[:200]}


async def raise_ticket(
    *,
    title: str,
    symptom: str,
    context: str = "",
    severity: str = "MEDIUM",
    category: str = "other",
    suggested_fix: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Raise a durable ticket for a problem ARIA has observed.

    Returns a dict with:
      ticket_id   Canonical ID ARIA is permitted to quote. Preferred form
                  is `GH-<number>` (GitHub succeeded). If GitHub is disabled
                  or failed but Airtable succeeded, returns `AT-<recId>`.
                  If both failed, returns None and ok=False.
      github      {ok, number, url} — present iff GitHub attempted.
      airtable    {ok, record_id}   — present iff Airtable attempted.
      ok          True iff at least one surface succeeded.

    Fire-and-forget callers can ignore the return; the authoritative ID
    is only important when ARIA needs to cite the ticket back to the user.
    """
    title = (title or "").strip() or "ARIA raised an untitled ticket"
    symptom = (symptom or "").strip()
    context = (context or "").strip()
    severity = _normalise_severity(severity)
    category = _normalise_category(category)
    suggested_fix = (suggested_fix or "").strip() or None
    source = (source or "").strip() or None

    result: dict[str, Any] = {
        "ok": False,
        "ticket_id": None,
        "title": title,
        "severity": severity,
        "category": category,
    }

    # ── GitHub (primary) ──────────────────────────────────────────────────
    gh_enabled, gh_reason = _github_enabled()
    if gh_enabled:
        labels = [
            (os.getenv("GITHUB_ARIA_LABEL") or _DEFAULT_LABEL).strip(),
            _SEVERITY_LABELS[severity],
            _CATEGORY_LABELS[category],
        ]
        body = _build_issue_body(
            symptom=symptom,
            context=context,
            severity=severity,
            category=category,
            suggested_fix=suggested_fix,
            source=source,
        )
        gh = await _create_github_issue(title=title, body=body, labels=labels)
        result["github"] = gh
        if gh.get("ok"):
            result["ticket_id"] = gh["ticket_id"]
            result["ok"] = True
    else:
        result["github"] = {"ok": False, "reason": gh_reason, "skipped": True}

    # ── Airtable (mirror) ──────────────────────────────────────────────────
    at_enabled, at_reason = _airtable_enabled()
    if at_enabled:
        github_url = (result.get("github") or {}).get("url") if result["ok"] else None
        at = await _create_airtable_ticket(
            title=title,
            symptom=symptom,
            context=context,
            severity=severity,
            category=category,
            suggested_fix=suggested_fix,
            source=source,
            github_url=github_url,
        )
        result["airtable"] = at
        if at.get("ok") and not result["ticket_id"]:
            # GitHub was unavailable or failed — fall back to the Airtable ID
            # so ARIA can still cite an authoritative reference.
            result["ticket_id"] = at["ticket_id"]
            result["ok"] = True
    else:
        result["airtable"] = {"ok": False, "reason": at_reason, "skipped": True}

    if result["ok"]:
        logger.info(
            "Ticket raised: %s (severity=%s category=%s)",
            result["ticket_id"], severity, category,
        )
    else:
        logger.warning(
            "Ticket raise failed on all surfaces (github=%s, airtable=%s)",
            result.get("github", {}).get("reason"),
            result.get("airtable", {}).get("reason"),
        )
    return result


async def list_open_tickets(limit: int = 20) -> dict[str, Any]:
    """Fetch open tickets from GitHub so ARIA can recall what she's already
    filed (avoids duplicate filings for the same symptom).

    Returns {ok, tickets: [{number, title, url, labels, created_at}]}.
    Fails soft: returns {ok: False, reason} when GitHub is unavailable.
    """
    gh_enabled, gh_reason = _github_enabled()
    if not gh_enabled:
        return {"ok": False, "reason": gh_reason, "tickets": []}

    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    repo = (os.getenv("GITHUB_REPO") or _DEFAULT_REPO).strip()
    label = (os.getenv("GITHUB_ARIA_LABEL") or _DEFAULT_LABEL).strip()
    url = f"{_GITHUB_API}/repos/{repo}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "aria-list-tickets",
    }
    params = {"labels": label, "state": "open", "per_page": min(max(1, limit), 100)}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            return {
                "ok": False,
                "reason": f"http_{r.status_code}",
                "detail": r.text[:200],
                "tickets": [],
            }
        items = r.json()
        tickets = [
            {
                "number": i.get("number"),
                "ticket_id": f"GH-{i.get('number')}",
                "title": i.get("title"),
                "url": i.get("html_url"),
                "labels": [l.get("name") for l in (i.get("labels") or []) if l.get("name")],
                "created_at": i.get("created_at"),
            }
            for i in items
            # GitHub's `issues` endpoint returns pull requests too — filter them out.
            if not i.get("pull_request")
        ]
        return {"ok": True, "tickets": tickets, "count": len(tickets)}
    except httpx.TimeoutException:
        return {"ok": False, "reason": "timeout", "tickets": []}
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("list_open_tickets exception: %s", e)
        return {"ok": False, "reason": "exception", "detail": str(e)[:200], "tickets": []}
