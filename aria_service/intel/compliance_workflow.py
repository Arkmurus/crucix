"""
ARIA Compliance Workflow Tracker — Full Lifecycle Management

PURPOSE:
  Defence broking compliance is not a one-time check — it's a lifecycle:
    INTAKE → SCREENING → ASSESSMENT → DECISION → MONITORING → RE-SCREENING

  This module manages the full lifecycle of compliance cases. Every entity
  ARIA screens is tracked through these stages, with automatic re-screening
  on a schedule based on risk tier.

STATE MACHINE:
  PENDING     → entity submitted for screening
  SCREENING   → sanctions/PEP/registry checks in progress
  ASSESSED    → screening complete, awaiting human decision
  APPROVED    → cleared for engagement (re-screen scheduled)
  REJECTED    → engagement refused (reason recorded)
  MONITORING  → approved but under ongoing monitoring
  EXPIRED     → re-screening overdue (auto-escalated)

RE-SCREENING SCHEDULE:
  hard_stop / red → REJECTED (no re-screen)
  amber           → re-screen every 30 days
  info / clear    → re-screen every 90 days
  monitoring      → re-screen every 60 days

STORAGE:
  Redis: crucix:compliance:cases (JSON list, max 5000)
  Each case includes full audit trail of state transitions.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.intel.compliance_workflow")

CASES_KEY = "crucix:compliance:cases"
CASES_TTL = 365 * 86400  # 1 year
MAX_CASES = 5000

# Re-screening intervals in days
RESCREEN_INTERVALS = {
    "hard_stop": None,   # No re-screen — permanently rejected
    "red": None,         # No re-screen — permanently rejected
    "amber": 30,         # Monthly re-screen
    "info": 90,          # Quarterly re-screen
    "clear": 90,         # Quarterly re-screen
    "monitoring": 60,    # Bi-monthly re-screen
}

VALID_STATES = {"pending", "screening", "assessed", "approved", "rejected", "monitoring", "expired"}


@dataclass
class ComplianceCase:
    case_id: str = ""
    entity_name: str = ""
    entity_type: str = "company"         # "company" | "person"
    jurisdiction: str = ""
    registration_number: str = ""
    state: str = "pending"
    risk_level: str = "unknown"          # from DD/screening result
    risk_summary: str = ""
    screened_by: str = "aria"            # "aria" | "manual" | username
    decided_by: str = ""                 # who approved/rejected
    decision_reason: str = ""
    dd_run_id: str = ""                  # link to DD report
    next_rescreen_at: float = 0          # epoch timestamp
    created_at: str = ""
    updated_at: str = ""
    audit_trail: list[dict] = field(default_factory=list)

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.case_id:
            self.case_id = f"comp_{uuid.uuid4().hex[:10]}"
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


# ── Core Functions ───────────────────────────────────────────────────────────

async def create_case(
    entity_name: str,
    entity_type: str = "company",
    jurisdiction: str = "",
    registration_number: str = "",
    screened_by: str = "aria",
) -> dict:
    """Create a new compliance case. Returns the case dict."""
    case = ComplianceCase(
        entity_name=entity_name,
        entity_type=entity_type,
        jurisdiction=jurisdiction,
        registration_number=registration_number,
        screened_by=screened_by,
    )
    case.audit_trail.append({
        "action": "created",
        "state": "pending",
        "by": screened_by,
        "at": case.created_at,
    })

    cases = await _load_cases()
    cases.append(asdict(case))
    await _save_cases(cases)

    logger.info("Compliance case created: %s for %s", case.case_id, entity_name)
    return asdict(case)


async def update_state(
    case_id: str,
    new_state: str,
    by: str = "aria",
    reason: str = "",
    risk_level: str = "",
    risk_summary: str = "",
    dd_run_id: str = "",
) -> dict:
    """Transition a case to a new state. Records audit trail."""
    if new_state not in VALID_STATES:
        return {"error": f"Invalid state: {new_state}. Valid: {', '.join(sorted(VALID_STATES))}"}

    cases = await _load_cases()
    case = next((c for c in cases if c.get("case_id") == case_id), None)
    if not case:
        return {"error": f"Case {case_id} not found"}

    old_state = case["state"]
    now = datetime.now(timezone.utc).isoformat()

    case["state"] = new_state
    case["updated_at"] = now
    if by:
        case["decided_by"] = by
    if reason:
        case["decision_reason"] = reason
    if risk_level:
        case["risk_level"] = risk_level
    if risk_summary:
        case["risk_summary"] = risk_summary
    if dd_run_id:
        case["dd_run_id"] = dd_run_id

    # Set re-screening schedule
    if new_state in ("approved", "monitoring"):
        interval = RESCREEN_INTERVALS.get(risk_level or case.get("risk_level", ""), 90)
        if interval:
            case["next_rescreen_at"] = time.time() + (interval * 86400)
        else:
            case["next_rescreen_at"] = 0
    elif new_state == "rejected":
        case["next_rescreen_at"] = 0

    case.setdefault("audit_trail", []).append({
        "action": f"state_change: {old_state} → {new_state}",
        "state": new_state,
        "by": by,
        "reason": reason,
        "at": now,
    })

    await _save_cases(cases)
    logger.info("Case %s: %s → %s (by %s)", case_id, old_state, new_state, by)
    return case


async def get_case(case_id: str) -> Optional[dict]:
    """Retrieve a single case by ID."""
    cases = await _load_cases()
    return next((c for c in cases if c.get("case_id") == case_id), None)


async def find_case(entity_name: str) -> Optional[dict]:
    """Find a case by entity name (case-insensitive)."""
    cases = await _load_cases()
    name_lower = entity_name.lower().strip()
    return next((c for c in cases if c.get("entity_name", "").lower().strip() == name_lower), None)


async def list_cases(
    state: str = "",
    risk_level: str = "",
    limit: int = 50,
) -> list[dict]:
    """List compliance cases with optional filters."""
    cases = await _load_cases()
    filtered = cases
    if state:
        filtered = [c for c in filtered if c.get("state") == state]
    if risk_level:
        filtered = [c for c in filtered if c.get("risk_level") == risk_level]
    # Sort by updated_at descending
    filtered.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return filtered[:limit]


async def get_overdue_rescreens() -> list[dict]:
    """Return cases where re-screening is overdue."""
    cases = await _load_cases()
    now = time.time()
    overdue = []
    for c in cases:
        rescreen_at = c.get("next_rescreen_at", 0)
        if rescreen_at and rescreen_at < now and c.get("state") in ("approved", "monitoring"):
            overdue.append(c)
    return overdue


async def mark_expired() -> int:
    """Mark overdue cases as EXPIRED. Returns count of newly expired cases."""
    cases = await _load_cases()
    now = time.time()
    count = 0
    for c in cases:
        rescreen_at = c.get("next_rescreen_at", 0)
        if rescreen_at and rescreen_at < now and c.get("state") in ("approved", "monitoring"):
            c["state"] = "expired"
            c["updated_at"] = datetime.now(timezone.utc).isoformat()
            c.setdefault("audit_trail", []).append({
                "action": "auto_expired",
                "state": "expired",
                "by": "system",
                "reason": "Re-screening overdue",
                "at": c["updated_at"],
            })
            count += 1
    if count:
        await _save_cases(cases)
        logger.info("Marked %d cases as expired (re-screening overdue)", count)
    return count


async def get_stats() -> dict:
    """Return compliance workflow statistics."""
    cases = await _load_cases()
    by_state = {}
    by_risk = {}
    for c in cases:
        state = c.get("state", "unknown")
        risk = c.get("risk_level", "unknown")
        by_state[state] = by_state.get(state, 0) + 1
        by_risk[risk] = by_risk.get(risk, 0) + 1

    overdue = await get_overdue_rescreens()
    return {
        "total_cases": len(cases),
        "by_state": by_state,
        "by_risk": by_risk,
        "overdue_rescreens": len(overdue),
        "overdue_entities": [c.get("entity_name") for c in overdue[:10]],
    }


# ── Auto-create from DD ─────────────────────────────────────────────────────

async def auto_create_from_dd(
    entity_name: str,
    entity_type: str,
    jurisdiction: str,
    registration_number: str,
    risk_level: str,
    risk_summary: str,
    dd_run_id: str,
) -> dict:
    """Automatically create and advance a compliance case from a DD result.

    Called by dd_orchestrator after synthesis. Creates the case, moves it to
    ASSESSED state, and if risk is clear/info, auto-approves with re-screen schedule.
    """
    # Check if case already exists
    existing = await find_case(entity_name)
    if existing:
        # Update existing case with new screening
        return await update_state(
            case_id=existing["case_id"],
            new_state="assessed",
            by="aria_dd",
            risk_level=risk_level,
            risk_summary=risk_summary,
            dd_run_id=dd_run_id,
        )

    # Create new case
    case = await create_case(
        entity_name=entity_name,
        entity_type=entity_type,
        jurisdiction=jurisdiction,
        registration_number=registration_number,
    )

    # Move to screening
    await update_state(case["case_id"], "screening", by="aria_dd")

    # Move to assessed
    case = await update_state(
        case["case_id"], "assessed",
        by="aria_dd",
        risk_level=risk_level,
        risk_summary=risk_summary,
        dd_run_id=dd_run_id,
    )

    # Auto-approve clear/info cases
    if risk_level in ("clear", "info"):
        case = await update_state(
            case["case_id"], "approved",
            by="aria_auto",
            reason=f"Auto-approved: risk level {risk_level}",
            risk_level=risk_level,
        )
    # Auto-reject hard_stop
    elif risk_level == "hard_stop":
        case = await update_state(
            case["case_id"], "rejected",
            by="aria_auto",
            reason=f"Auto-rejected: {risk_summary[:200]}",
            risk_level=risk_level,
        )

    return case


# ── Persistence ──────────────────────────────────────────────────────────────

async def _load_cases() -> list[dict]:
    data = await rs.get_json(CASES_KEY)
    return data if isinstance(data, list) else []


async def _save_cases(cases: list[dict]) -> None:
    # Prune to MAX_CASES (keep most recent)
    if len(cases) > MAX_CASES:
        cases.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        cases = cases[:MAX_CASES]
    await rs.set_json(CASES_KEY, cases, ex=CASES_TTL)
