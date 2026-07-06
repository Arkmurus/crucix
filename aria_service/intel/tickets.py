"""ARIA ticket system — durable bug / feature / infra reports.

Purpose
───────
ARIA routinely spots problems during operation — tripped circuit breakers,
failed sources, fabricated answers from old data, etc. Before this module
she mentioned them in chat and even fabricated ticket IDs (incident
2026-04-21: "ARK-DEV-001", never filed). Now she can raise real tickets:

  - GitHub Issues (label `aria-raised`). Visible to Claude Code sessions
    via `gh issue list --label aria-raised`, which is how the developer
    receives them across sessions without a secret exchange.

GitHub is env-gated and silently no-ops when unconfigured, so local dev /
tests don't blow up.

R-F1863 (2026-06-24): the optional Airtable "Dev Tickets" mirror was
removed. Ticketing is GitHub-only.

Configuration
─────────────
GitHub (this is what Claude Code sees):
  GITHUB_TOKEN          Personal access token. Fine-grained token needs
                        `Issues: Read and write` on the target repo.
                        Classic PAT needs `repo` scope.
  GITHUB_REPO           `owner/repo`. Default: `Arkmurus/crucix`.
  GITHUB_ARIA_LABEL     Label applied to every ARIA ticket. Default
                        `aria-raised`. Must exist in the repo; if it
                        doesn't, the issue still gets created — GitHub
                        ignores unknown labels silently.

Killswitch (set to "0" to disable without removing env):
  ARIA_TICKETS_GITHUB_ENABLED   default "1"

Anti-fabrication
────────────────
Constitution clause 22 (aria_engine.py) forbids ARIA from citing ticket
IDs she didn't obtain from raise_ticket(). The returned payload includes
the authoritative `ticket_id` string (`GH-42` when GitHub succeeded)
which is the ONLY form ARIA is permitted to quote in a reply.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("aria.intel.tickets")

_DEFAULT_REPO = "Arkmurus/crucix"  # override with GITHUB_REPO env

_DEFAULT_LABEL = "aria-raised"
_GITHUB_API = "https://api.github.com"
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
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:  # no-breaker: ticket system is best-effort; breaker would block ticket creation
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
      ticket_id   Canonical ID ARIA is permitted to quote — `GH-<number>`
                  when GitHub succeeded. If GitHub is disabled or failed,
                  returns None and ok=False.
      github      {ok, number, url} — present iff GitHub attempted.
      ok          True iff GitHub succeeded.

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

    # R-F1863 (2026-06-24): the Airtable "Dev Tickets" mirror was removed —
    # ticketing is GitHub-only.

    if result["ok"]:
        logger.info(
            "Ticket raised: %s (severity=%s category=%s)",
            result["ticket_id"], severity, category,
        )
    else:
        logger.warning(
            "Ticket raise failed (github=%s)",
            result.get("github", {}).get("reason"),
        )
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="tickets",
        summary="Raise Ticket",
        source_id="tickets:R-F1001",
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

# R-F2119 §21a — wire failure handler for tickets
try:
    wire_failure(module="tickets", detail="module shutdown",
                gap_type="engine_failure", source="tickets:shutdown")
except Exception:
    pass
