"""ARIA Golden Q&A Auto-Generator — self-growing eval set from verified facts.

Instead of asking Antonio to hand-write 20 Q&As per market, ARIA reads her
own Clause-17 VERIFIED fact store, generates Q&A candidates with full
provenance, and queues them for human approval. The golden set becomes
a self-expanding artifact where every entry is backed by ≥1 tier-classified
source URL.

Flow
────
  verified_intel VERIFIED facts   (aria:verified_facts:*)
            │
            ▼
  golden_autogen.propose_batch()  (this module)
   – pulls recent VERIFIED facts
   – deduplicates against existing candidates + golden set
   – renders Q/A template per fact_type
   – stores candidate in pending queue
            │
            ▼
  GET  /api/aria/golden/candidates            — operator reviews
  POST /api/aria/golden/approve {candidate_id} — promotes to eval_runner
  POST /api/aria/golden/reject  {candidate_id} — archives with reason
            │
            ▼
  eval_runner.add_golden_entry(...)            — enters the golden set
  – next weekly eval run exercises the new Q
  – verification grades pass/fail
  – source_verifier counts inline citations against the stored source_url
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.intel.golden_autogen")

_K_PENDING = "aria:golden_autogen:pending"
_K_HISTORY = "aria:golden_autogen:history"

# Per-fact_type Q/A templates. Each template takes the VerifiedFact dict
# and returns (question, expected_answer_fragment). The expected answer
# is the verified value + its citation; the eval run checks whether
# ARIA's response contains the value AND cites the same source URL.
_QA_TEMPLATES = {
    "APPOINTMENT": (
        "When was {entity_name} appointed to their current role?",
        "{value}",
    ),
    "SANCTIONS_STATUS": (
        "Is {entity_name} currently on any sanctions list?",
        "{value}",
    ),
    "CONTRACT_AWARD": (
        "What contract has {entity_name} been awarded?",
        "{value}",
    ),
    "CORPORATE_REGISTRY": (
        "What is {entity_name}'s corporate registration record?",
        "{value}",
    ),
    "BUDGET_ALLOCATION": (
        "What is the defence budget allocation for {entity_name}?",
        "{value}",
    ),
    "PROGRAMME_STATUS": (
        "What is the current status of the {entity_name} programme?",
        "{value}",
    ),
    "POLITICAL_EVENT": (
        "What happened involving {entity_name}?",
        "{value}",
    ),
    "ARMS_TRANSFER": (
        "What arms transfer involves {entity_name}?",
        "{value}",
    ),
}

_MARKET_KEYWORDS = {
    "angola":      ["angola", "luanda", "faa"],
    "mozambique":  ["mozambique", "maputo", "fadm"],
    "nigeria":     ["nigeria", "abuja", "nigerian"],
    "kenya":       ["kenya", "nairobi"],
    "ghana":       ["ghana", "accra"],
    "turkey":      ["turkey", "turkish", "ankara"],
    "brazil":      ["brazil", "brasil", "defesa"],
    "saudi":       ["saudi", "riyadh"],
    "uae":         ["uae", "emirates"],
    "poland":      ["poland", "polish", "warsaw"],
    "romania":     ["romania", "romanian", "bucharest"],
    "indonesia":   ["indonesia", "jakarta"],
    "philippines": ["philippines", "manila"],
    "vietnam":     ["vietnam", "hanoi"],
    "south_africa": ["south africa", "pretoria", "denel"],
}


def _infer_market(entity_name: str, claim: str) -> str:
    """Crude market classification for the golden set's `market` tag."""
    text = f"{entity_name} {claim}".lower()
    for market, kws in _MARKET_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return market
    return "global"


def _candidate_id(fact_id: str, template_key: str) -> str:
    return hashlib.md5(f"{fact_id}:{template_key}".encode()).hexdigest()[:12]


async def _existing_signatures() -> set[str]:
    """Signatures of all already-proposed or already-approved Qs so we
    don't re-queue the same question."""
    sigs: set[str] = set()
    pending = await rs.get_json(_K_PENDING) or []
    for p in pending:
        sigs.add(p.get("candidate_id", ""))
    history = await rs.get_json(_K_HISTORY) or []
    for h in history:
        sigs.add(h.get("candidate_id", ""))
    return sigs


# Score threshold for auto-promoting a Q to the golden set without
# human review. Clause 17 VERIFIED status already means the fact passed
# multi-source corroboration (Tier 1a single source OR ≥2 independent
# Tier 1b/2 OR ≥3 Tier 3), so re-asking for human approval would be
# duplicate work. Facts below this threshold (edge cases, partial
# verification) still go to the pending queue.
_AUTO_PROMOTE_SCORE = 1.0
_TIER_1A_AUTO_PROMOTE_SCORE = 0.9  # Tier 1a alone verifies at 0.9


def _should_auto_promote(data: dict, sources: list) -> bool:
    """Clause 17 multi-source corroboration already is approval — don't
    re-gate it. Auto-promote when:
      - Single Tier 1a source with score ≥ 0.9, OR
      - Two or more non-Tier-1a sources with combined score ≥ 1.0
    """
    score = float(data.get("verification_score") or 0.0)
    if not sources:
        return False
    has_tier_1a = any(s.get("tier") == "1a" for s in sources)
    if has_tier_1a and score >= _TIER_1A_AUTO_PROMOTE_SCORE:
        return True
    if len(sources) >= 2 and score >= _AUTO_PROMOTE_SCORE:
        return True
    return False


async def propose_batch(max_candidates: int = 20) -> dict:
    """Scan recent VERIFIED facts, generate Q&A candidates, and route:
      - multi-source-verified (Clause 17 threshold met) → AUTO-PROMOTE
        directly to eval_runner golden set. The multi-source verification
        IS the approval; no human re-gate.
      - partial / borderline → pending queue for human review.

    Returns a summary broken down by promotion path."""
    keys = await rs.scan_keys("aria:verified_facts:*", count=200)
    existing = await _existing_signatures()
    auto_promoted: list[dict] = []
    proposed: list[dict] = []

    for key in keys:
        if len(auto_promoted) + len(proposed) >= max_candidates:
            break
        data = await rs.get_json(key)
        if not data:
            continue
        if data.get("verification_status") != "VERIFIED":
            continue
        fact_type = data.get("fact_type", "")
        template = _QA_TEMPLATES.get(fact_type)
        if not template:
            continue

        entity = data.get("entity_name", "").strip()
        value = data.get("value", "").strip()
        claim = data.get("claim", "").strip()
        fact_id = data.get("fact_id", "")
        sources = data.get("sources") or []
        if not entity or not value or not sources:
            continue

        cid = _candidate_id(fact_id, fact_type)
        if cid in existing:
            continue

        question = template[0].format(entity_name=entity)
        expected = template[1].format(value=value)
        market = _infer_market(entity, claim)
        source_urls = [s.get("url", "") for s in sources if s.get("url")]

        candidate = {
            "candidate_id": cid,
            "source_fact_id": fact_id,
            "fact_type": fact_type,
            "entity_name": entity,
            "market": market,
            "category": f"verified_{fact_type.lower()}",
            "question": question,
            "expected_answer": expected,
            "expected_citation_urls": source_urls[:3],
            "verification_score": data.get("verification_score", 0),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "notes": (
                f"Auto-proposed from VERIFIED fact {fact_id}. "
                f"Source tier(s): {[s.get('tier') for s in sources][:3]}. "
                f"Every golden eval should pass if ARIA cites the same source."
            ),
        }

        if _should_auto_promote(data, sources):
            # Multi-source Clause 17 verification IS the approval.
            # Skip the human queue — go straight to the golden set.
            candidate["status"] = "AUTO_APPROVED"
            candidate["approved_by"] = "clause17_multi_source"
            candidate["approved_at"] = datetime.now(timezone.utc).isoformat()
            try:
                from . import eval_runner
                await eval_runner.add_golden_entry(
                    question=candidate["question"],
                    expected_answer=candidate["expected_answer"],
                    category=candidate["category"],
                    notes=(
                        candidate["notes"] +
                        f" | Expected citation URLs: {candidate['expected_citation_urls']} "
                        f"| AUTO_APPROVED: Clause 17 multi-source verified"
                    ),
                    source="auto_from_verified_intel",
                    added_by="clause17_multi_source",
                )
                auto_promoted.append(candidate)
                # Archive to history so stats() reflects it
                history = await rs.get_json(_K_HISTORY) or []
                history.insert(0, candidate)
                await rs.set_json(_K_HISTORY, history[:1000], ex=365 * 86400)
            except Exception as e:
                logger.warning("auto-promote to eval_runner failed: %s", e)
                candidate["status"] = "PENDING"
                candidate["notes"] += f" | auto-promote failed: {e}"
                proposed.append(candidate)
        else:
            candidate["status"] = "PENDING"
            proposed.append(candidate)

        existing.add(cid)

    if proposed:
        pending = await rs.get_json(_K_PENDING) or []
        pending = proposed + pending
        if len(pending) > 500:
            pending = pending[:500]
        await rs.set_json(_K_PENDING, pending, ex=30 * 86400)

    # Brain signal — golden auto-gen is mastery-worthy. Count auto-
    # promoted and pending separately so the daily briefing can say
    # "15 new golden entries this week, 3 pending your review".
    try:
        from . import brain_hook as _bh
        all_markets = sorted(
            {c["market"] for c in auto_promoted + proposed}
        )[:10]
        await _bh.absorb(
            module="golden_autogen",
            summary=(f"Golden auto-gen: {len(auto_promoted)} promoted, "
                     f"{len(proposed)} pending"),
            detail=f"Markets covered: {all_markets}",
            success=True,
        )
    except Exception:
        pass

    by_market_auto: dict[str, int] = {}
    for c in auto_promoted:
        by_market_auto[c["market"]] = by_market_auto.get(c["market"], 0) + 1
    by_market_pending: dict[str, int] = {}
    for c in proposed:
        by_market_pending[c["market"]] = by_market_pending.get(c["market"], 0) + 1
    return {
        "auto_promoted": len(auto_promoted),
        "pending": len(proposed),
        "auto_by_market": by_market_auto,
        "pending_by_market": by_market_pending,
        "queue_depth": len(await rs.get_json(_K_PENDING) or []),
    }


async def list_candidates(status: str = "", limit: int = 50) -> list[dict]:
    pending = await rs.get_json(_K_PENDING) or []
    if status:
        pending = [p for p in pending if p.get("status") == status]
    return pending[:limit]


async def approve_candidate(candidate_id: str, approved_by: str = "human") -> dict:
    pending = await rs.get_json(_K_PENDING) or []
    cand = next((p for p in pending if p.get("candidate_id") == candidate_id), None)
    if not cand:
        return {"ok": False, "error": "candidate not found"}

    cand["status"] = "APPROVED"
    cand["approved_by"] = approved_by
    cand["approved_at"] = datetime.now(timezone.utc).isoformat()
    pending = [p for p in pending if p.get("candidate_id") != candidate_id]
    await rs.set_json(_K_PENDING, pending, ex=30 * 86400)

    history = await rs.get_json(_K_HISTORY) or []
    history.insert(0, cand)
    await rs.set_json(_K_HISTORY, history[:1000], ex=365 * 86400)

    # Promote to the eval_runner golden set — this is what makes the
    # Q&A actually run in weekly evals.
    try:
        from . import eval_runner
        result = await eval_runner.add_golden_entry(
            question=cand["question"],
            expected_answer=cand["expected_answer"],
            category=cand["category"],
            notes=(
                cand.get("notes", "") +
                f" | Expected citation URLs: {cand.get('expected_citation_urls', [])[:3]}"
            ),
            source="auto_from_verified_intel",
            added_by=approved_by,
        )
    except Exception as e:
        logger.warning("eval_runner.add_golden_entry failed: %s", e)
        result = {"error": str(e)}
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "market": cand.get("market"),
        "eval_entry": result,
    }


async def reject_candidate(
    candidate_id: str, reason: str = "", rejected_by: str = "human",
) -> dict:
    pending = await rs.get_json(_K_PENDING) or []
    cand = next((p for p in pending if p.get("candidate_id") == candidate_id), None)
    if not cand:
        return {"ok": False, "error": "candidate not found"}
    cand["status"] = "REJECTED"
    cand["rejected_by"] = rejected_by
    cand["rejected_at"] = datetime.now(timezone.utc).isoformat()
    cand["rejection_reason"] = reason
    pending = [p for p in pending if p.get("candidate_id") != candidate_id]
    await rs.set_json(_K_PENDING, pending, ex=30 * 86400)
    history = await rs.get_json(_K_HISTORY) or []
    history.insert(0, cand)
    await rs.set_json(_K_HISTORY, history[:1000], ex=365 * 86400)
    return {"ok": True, "candidate_id": candidate_id, "reason": reason}


async def stats() -> dict:
    pending = await rs.get_json(_K_PENDING) or []
    history = await rs.get_json(_K_HISTORY) or []
    approved = [h for h in history if h.get("status") == "APPROVED"]
    rejected = [h for h in history if h.get("status") == "REJECTED"]
    by_market: dict[str, int] = {}
    for p in pending:
        by_market[p.get("market", "unknown")] = by_market.get(p.get("market", "unknown"), 0) + 1
    return {
        "pending": len(pending),
        "approved_total": len(approved),
        "rejected_total": len(rejected),
        "pending_by_market": by_market,
    }
