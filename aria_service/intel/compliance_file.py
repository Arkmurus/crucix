"""ARIA Compliance File — end-to-end signed dossier per deal.

Composes the complete compliance record a regulator or internal compliance
officer would actually demand for a deal. Pulls from:

  - deal_pipeline       (the deal itself + stage history + tags + notes)
  - audit_log           (every compliance decision + hash chain)
  - watchlist alerts    (sanctions worsening events that touched this deal)
  - compliance_workflow (open cases linked to the deal)

Returns a structured dossier carrying its own composition hash so a printed
copy can be verified back against the source-of-truth state. NOT a PDF
generator — that's a presentation concern. This is the data layer.

Usage
─────
  from aria_service.intel import compliance_file
  dossier = await compliance_file.compose(deal_id="LEAD-ABC123")
  # dossier["sections"] = ordered list of dossier sections
  # dossier["composition_hash"] = SHA-256 of the dossier body
  # dossier["audit_chain_verified"] = bool — was the underlying chain intact?

Brain wiring
────────────
Every composition is recorded in the audit log under action
'compliance_case_create' if create_case=True, OR feeds brain_hook directly
otherwise. The brain absorbs "compliance file composed for Deal X" so ARIA
can answer questions about prior compositions from her own memory.
"""
from __future__ import annotations
from .engine_wiring import wire_success

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from .engine_wiring import wired, wire_failure

logger = logging.getLogger("aria.compliance_file")


def _hash_dossier_body(body: dict) -> str:
    """Compute a stable SHA-256 over the dossier body (excluding the hash
    field itself). Lets a recipient verify the document matches what was
    originally composed."""
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


async def _get_deal(deal_id: str) -> Optional[dict]:
    try:
        from . import deal_pipeline
        return await deal_pipeline.get_lead(deal_id)
    except Exception as e:
        logger.warning("compliance_file: deal_pipeline lookup failed: %s", e)
        return None


async def _get_audit_entries_for_deal(deal_id: str) -> list[dict]:
    try:
        from . import audit_log
        return await audit_log.get_by_deal(deal_id, limit=500)
    except Exception as e:
        logger.warning("compliance_file: audit lookup failed: %s", e)
        return []


async def _get_audit_entries_for_entity(entity_name: str) -> list[dict]:
    if not entity_name:
        return []
    try:
        from . import audit_log
        return await audit_log.get_by_entity(entity_name, limit=200)
    except Exception as e:
        logger.warning("compliance_file: audit-by-entity lookup failed: %s", e)
        return []


async def _get_watchlist_alerts_for_entity(entity_name: str) -> list[dict]:
    """Pull watchlist alerts whose entity matches the deal's buyer."""
    if not entity_name:
        return []
    try:
        from . import dd_orchestrator
        # 30-day window — we want all alerts in the lifetime of an active deal
        all_alerts = await dd_orchestrator.get_watchlist_alerts(since_hours=30 * 24)
        norm = entity_name.strip().lower()
        return [a for a in all_alerts if (a.get("entity") or "").strip().lower() == norm or norm in (a.get("entity") or "").lower()]
    except Exception as e:
        logger.warning("compliance_file: watchlist alerts lookup failed: %s", e)
        return []


async def _get_compliance_cases_for_entity(entity_name: str) -> list[dict]:
    if not entity_name:
        return []
    try:
        from . import compliance_workflow
        case = await compliance_workflow.find_case(entity_name)
        return [case] if case else []
    except Exception as e:
        logger.warning("compliance_file: compliance_workflow lookup failed: %s", e)
        return []


async def _verify_audit_chain() -> dict:
    try:
        from . import audit_log
        return await audit_log.verify_chain(start=0, count=500)
    except Exception as e:
        return {"verified": False, "error": str(e)}


def _summarise_decisions(audit_entries: list[dict]) -> dict:
    """Roll up audit entries by action type so the summary section answers
    "what compliance work has been done?" at a glance."""
    counts: dict[str, int] = {}
    last_by_action: dict[str, dict] = {}
    decisions: list[dict] = []
    for e in audit_entries:
        action = e.get("action", "unknown")
        counts[action] = counts.get(action, 0) + 1
        # entries are newest-first, so first seen = most recent
        if action not in last_by_action:
            last_by_action[action] = {
                "ts": e.get("ts"),
                "decision": e.get("decision"),
                "entry_hash": e.get("entry_hash"),
            }
        if e.get("decision"):
            decisions.append({
                "ts": e.get("ts"),
                "action": action,
                "decision": e.get("decision"),
                "confidence": e.get("confidence"),
                "entry_hash": e.get("entry_hash"),
            })
    return {
        "action_counts": counts,
        "most_recent_per_action": last_by_action,
        "decisions_chronological": list(reversed(decisions)),  # oldest first for narrative
    }


@wired(module="compliance_file", summary="Compliance file compose")

async def compose(
    deal_id: str,
    *,
    include_chain_verification: bool = True,
    record_in_audit: bool = True,
) -> dict:
    """Compose the full compliance dossier for a deal.

    Returns a structured dict with sections, summary, citations, and a
    composition_hash that uniquely identifies this snapshot.
    """
    if not deal_id or not deal_id.strip():
        raise ValueError("deal_id required")

    composed_at = datetime.now(timezone.utc).isoformat()

    # ── Pull all source data in parallel-friendly order ──
    deal = await _get_deal(deal_id)
    if not deal:
        return {
            "ok": False,
            "deal_id": deal_id,
            "error": "deal not found in pipeline",
            "composed_at": composed_at,
        }

    buyer = deal.get("buyer", "")
    audit_entries = await _get_audit_entries_for_deal(deal_id)
    # Also pull entries about the buyer — sanctions screens etc. happen on
    # the entity, not on the deal_id, so we want both views.
    entity_audit = await _get_audit_entries_for_entity(buyer) if buyer else []
    # Merge + dedupe by entry_hash, newest first
    seen_hashes = {e.get("entry_hash") for e in audit_entries}
    for e in entity_audit:
        if e.get("entry_hash") not in seen_hashes:
            audit_entries.append(e)
            seen_hashes.add(e.get("entry_hash"))
    audit_entries.sort(key=lambda x: x.get("seq", 0), reverse=True)

    watchlist_alerts = await _get_watchlist_alerts_for_entity(buyer)
    cases = await _get_compliance_cases_for_entity(buyer)

    chain_verification = await _verify_audit_chain() if include_chain_verification else None

    decisions_rollup = _summarise_decisions(audit_entries)

    # ── Compose the dossier ──
    sections = [
        {
            "id": "01_deal_summary",
            "title": "Deal summary",
            "content": {
                "deal_id": deal.get("id"),
                "country": deal.get("country"),
                "buyer": deal.get("buyer"),
                "requirement": deal.get("requirement"),
                "estimated_value_usd": deal.get("estimated_value_usd"),
                "stage": deal.get("stage"),
                "owner": deal.get("owner"),
                "deadline": deal.get("deadline"),
                "source": deal.get("source"),
                "tags": deal.get("tags", []),
                "created_at": deal.get("created_at"),
                "updated_at": deal.get("updated_at"),
                "last_activity_at": deal.get("last_activity_at"),
            },
        },
        {
            "id": "02_stage_history",
            "title": "Stage history",
            "content": {
                "transitions": deal.get("stage_history", []),
                "notes": deal.get("notes", []),
            },
        },
        {
            "id": "03_compliance_decisions",
            "title": "Compliance decisions taken",
            "content": {
                "summary": decisions_rollup,
                "total_audit_entries": len(audit_entries),
                "audit_chain_verified": chain_verification.get("verified") if chain_verification else None,
                "audit_chain_check_detail": chain_verification,
            },
        },
        {
            "id": "04_audit_trail",
            "title": "Full audit trail (chronological)",
            "content": {
                "entries": list(reversed(audit_entries)),  # oldest first for reading
                "count": len(audit_entries),
            },
        },
        {
            "id": "05_watchlist_events",
            "title": "Sanctions / watchlist events affecting this deal",
            "content": {
                "alerts": watchlist_alerts,
                "count": len(watchlist_alerts),
                "worst_status": (
                    "HIT" if any(a.get("change_type") == "new_hit" for a in watchlist_alerts)
                    else "PEP" if any(a.get("change_type") == "new_pep" for a in watchlist_alerts)
                    else "CLEAN"
                ),
            },
        },
        {
            "id": "06_compliance_cases",
            "title": "Linked compliance cases",
            "content": {
                "cases": cases,
                "count": len(cases),
            },
        },
    ]

    body = {
        "deal_id": deal_id,
        "composed_at": composed_at,
        "composed_by": "aria",
        "sections": sections,
        "audit_chain_verified": chain_verification.get("verified") if chain_verification else None,
        "schema_version": "1.0",
    }
    composition_hash = _hash_dossier_body(body)

    dossier = {
        **body,
        "composition_hash": composition_hash,
        "verification_note": (
            "Recompute SHA-256 over the dossier body (all keys except "
            "composition_hash) sorted by key, comma+colon separators, no "
            "whitespace, default=str. Result must equal composition_hash."
        ),
    }

    # ── Audit-record the composition + brain-feed ──
    if record_in_audit:
        try:
            from . import audit_log
            await audit_log.record(
                action="export_assessment",  # closest existing action; composition IS an export-readiness assessment
                actor="compliance_file.compose",
                entity_name=buyer,
                deal_id=deal_id,
                inputs={"include_chain_verification": include_chain_verification},
                outputs={
                    "composition_hash": composition_hash,
                    "section_count": len(sections),
                    "audit_entries_included": len(audit_entries),
                    "watchlist_alerts_included": len(watchlist_alerts),
                },
                decision=f"compliance_file_composed",
                confidence="CONFIRMED",
                notes=f"Dossier composition_hash={composition_hash[:16]}...",
            )
        except Exception as e:
            logger.warning("compliance_file: audit record of composition failed (non-fatal): %s", e)

    # Always brain-feed even if audit recording was skipped
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="compliance_file",
            summary=(
                f"Compliance file composed for deal {deal_id} (buyer={buyer[:40]}). "
                f"{len(audit_entries)} audit entries, {len(watchlist_alerts)} alerts, "
                f"{len(cases)} cases. Chain verified: {chain_verification.get('verified') if chain_verification else 'skipped'}."
            ),
            entity_name=buyer or deal_id,
            source_id=composition_hash,
            success=True,
            confidence="CONFIRMED",
        )
    except Exception as e:
        logger.debug("compliance_file brain absorb failed (non-fatal): %s", e)

    return dossier


@wired(module="compliance_file", summary="Compliance file provenance")

async def get_provenance(entry_hash: str) -> dict:
    """Decision provenance: given an audit entry hash, walk back through
    the chain to surface the reasoning context (prior entries on the same
    deal/entity that fed into this decision).

    Returns a structured tree showing the decision + supporting prior
    entries + linked sources. The output is what a compliance officer
    needs to defend the decision to a regulator.
    """
    from . import audit_log

    target = await audit_log.get_entry(entry_hash)
    if not target:
        return {"ok": False, "error": f"audit entry not found: {entry_hash}"}

    deal_id = target.get("deal_id")
    entity_name = target.get("entity_name")

    # Get entries on the same deal AND about the same entity, then filter
    # to those PRIOR to the target (lower seq).
    related_deal = await audit_log.get_by_deal(deal_id) if deal_id else []
    related_entity = await audit_log.get_by_entity(entity_name) if entity_name else []

    target_seq = target.get("seq", 0)
    seen = {target.get("entry_hash")}
    prior: list[dict] = []
    for e in related_deal + related_entity:
        h = e.get("entry_hash")
        if h in seen:
            continue
        if e.get("seq", 0) < target_seq:
            prior.append(e)
            seen.add(h)
    prior.sort(key=lambda x: x.get("seq", 0))

    return {
        "ok": True,
        "decision": {
            "ts": target.get("ts"),
            "action": target.get("action"),
            "actor": target.get("actor"),
            "entity_name": target.get("entity_name"),
            "deal_id": target.get("deal_id"),
            "decision": target.get("decision"),
            "confidence": target.get("confidence"),
            "entry_hash": target.get("entry_hash"),
            "prev_hash": target.get("prev_hash"),
            "inputs": target.get("inputs"),
            "outputs": target.get("outputs"),
            "sources": target.get("sources", []),
        },
        "supporting_prior_entries": [
            {
                "ts": e.get("ts"),
                "action": e.get("action"),
                "decision": e.get("decision"),
                "confidence": e.get("confidence"),
                "entry_hash": e.get("entry_hash"),
                "actor": e.get("actor"),
            }
            for e in prior
        ],
        "supporting_count": len(prior),
        "regulator_note": (
            "Each entry above is hash-chained. To verify the integrity of "
            "the supporting trail, call /api/aria/audit/verify."
        ),
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="compliance_file",
                     summary="compliance_file module active",
                     source_id="compliance_file:init")
    except Exception:
        try:
            wire_failure(module="compliance_file", detail="module init failed",
                        gap_type="engine_failure", source="compliance_file:init")
        except Exception:
            pass
