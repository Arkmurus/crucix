"""Meeting-notes intake → structured actions → pending_actions queue.

The operator pastes prose meeting notes into chat or emails them to the
intelligence address. ARIA extracts WHO / WHAT / WHEN / ACTION and creates
a pending_actions entry per agreed action, keyed for idempotency so the
same notes pasted twice do not double-create tasks.

Design goals
────────────
  1. Idempotent — sha256(notes) gates re-processing. Re-pasted notes
     return the original extraction and the already-created action IDs.
  2. Pending-queue first — actions land in pending_actions (Clause 18
     approval queue), not directly in an external CRM or Airtable base.
     Airtable sync, if/when configured, should watch pending_actions with
     source='meeting_notes' and mirror approved entries.
  3. Provenance — every created pending_actions entry carries notes_hash
     and meeting_label in metadata so origin is auditable.
  4. Compliance signals surface as flags — SITCL, sanctions, export
     control mentions are extracted into a dedicated bucket for the
     operator, independent of the action list.

Companion to
────────────
  - zoom_integration: transcript-based extraction with the same JSON shape.
    Meeting_notes works on free-form pasted prose (chat / email body).
  - pending_actions.record(): authoritative destination for every action.
  - brain_hook: each processing run is absorbed as an event.

Public API
──────────
  async process(notes, *, meeting_label, llm, source='chat', user_id='',
                chat_id='') -> dict
  async extract(notes, *, meeting_label, llm) -> dict
  def render_markdown(result) -> str
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.meeting_notes")

# Redis keys
_KEY_PROCESSED = "crucix:meeting_notes:processed:{hash}"  # stores full result
_KEY_RECENT = "crucix:meeting_notes:recent"               # list of hashes
_IDEMPOTENCY_TTL_SEC = 60 * 60 * 24 * 30  # 30 days

# Minimum realistic note size — below this, refuse to process (saves the
# LLM bill on typos and accidental empties).
_MIN_NOTES_CHARS = 60

# Cap sent to the LLM. Beyond this the extractor gets worse signal and
# the prompt size balloons. Tune if meetings routinely run longer.
_MAX_NOTES_CHARS = 12000


def _notes_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _severity_for_action(action: str, deadline: str) -> str:
    """Pick a pending_actions severity from action text + deadline hint.

    Deadline language like 'today', 'tomorrow', 'this week', 'urgent',
    or a compliance keyword elevates severity. Default is MEDIUM.
    """
    a = (action or "").lower()
    d = (deadline or "").lower()
    if any(kw in a for kw in ("sanction", "embargo", "sitcl", "export control",
                              "eu consolidated", "ofac", "urgent")):
        return "HIGH"
    if any(kw in d for kw in ("today", "tomorrow", "urgent", "asap", "eod")):
        return "HIGH"
    if any(kw in d for kw in ("this week", "by friday", "by monday")):
        return "MEDIUM"
    if not d or d in ("tbc", "tbd", "n/a", "none"):
        return "LOW"
    return "MEDIUM"


async def extract(
    notes: str,
    *,
    meeting_label: str,
    llm: Any,
) -> dict[str, Any]:
    """LLM-extract structured fields from free-form meeting notes.

    Returns a dict shaped like:
      {
        "summary": str,
        "counterparties": [str, ...],
        "action_items": [{"owner": str, "action": str, "deadline": str,
                          "related_entity": str}],
        "key_decisions": [str, ...],
        "entities_mentioned": [{"name": str, "type": str, "context": str}],
        "compliance_flags": [str, ...],
        "follow_ups": [str, ...],
        "intelligence_facts": [str, ...],
        "deal_stage": str | None,
        "next_meeting": str | None,
      }
    On extraction failure, returns a degraded result with fields present
    but empty — callers should treat empty action_items as "nothing to
    queue" rather than as an error.
    """
    if not llm or not getattr(llm, "is_configured", False):
        logger.warning("[meeting_notes] LLM not configured — cannot extract")
        return {
            "summary": notes[:400],
            "counterparties": [],
            "action_items": [],
            "key_decisions": [],
            "entities_mentioned": [],
            "compliance_flags": [],
            "follow_ups": [],
            "intelligence_facts": [],
            "deal_stage": None,
            "next_meeting": None,
            "_llm": "not_configured",
        }

    capped = notes[:_MAX_NOTES_CHARS]

    prompt = f"""Analyse these meeting notes and extract structured intelligence.

MEETING: {meeting_label}

NOTES:
{capped}

Extract the following as strict JSON (no markdown fences, no commentary):
{{
  "summary": "2-3 sentence summary of what happened",
  "counterparties": ["list of counterparty companies/people named"],
  "action_items": [
    {{"owner": "person or team who owns this",
      "action": "what they will do",
      "deadline": "when (exact date if stated, else language like 'this week')",
      "related_entity": "counterparty this action relates to, if any"}}
  ],
  "key_decisions": ["decisions agreed in the meeting"],
  "entities_mentioned": [
    {{"name": "entity", "type": "company|person|country|product|programme",
      "context": "what was said about it"}}
  ],
  "compliance_flags": [
    "any SITCL, sanctions, export-control, end-user, EUC, OFAC, OFSI,
     embargo, CFSP, ITAR, EAR, dual-use topics discussed — one line each"
  ],
  "follow_ups": ["what needs to happen next, not yet owned"],
  "intelligence_facts": ["surprising or non-obvious facts ARIA should remember"],
  "deal_stage": "stage of the deal if mentioned (e.g. 'Teaming agreement signed') or null",
  "next_meeting": "next meeting date/time if stated, else null"
}}

Rules:
  - If a field has no content, return it as an empty list or null.
  - Do NOT invent owners, deadlines, or decisions. If the notes do not
    state who owns an action, set owner to "unassigned".
  - If the notes do not state a deadline, set deadline to "TBC".
  - Keep each action under 180 characters.
"""

    try:
        from . import cost_tracker
        with cost_tracker.feature("meeting_notes"):
            llm_result = await llm.complete(
                "You are ARIA extracting structured intelligence from meeting "
                "notes. Output ONLY valid JSON — no markdown, no prose.",
                prompt,
                max_tokens=2000,
                timeout=60.0,
            )
        raw = (getattr(llm_result, "text", "") or "").strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        extraction = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("[meeting_notes] JSON parse failed; raw output: %r",
                       raw[:300] if 'raw' in dir() else "<no output>")
        extraction = {}
    except Exception as e:
        logger.warning("[meeting_notes] LLM extraction failed: %s", e)
        extraction = {}

    # Fill missing keys so callers can index safely.
    defaults = {
        "summary": notes[:400] if not extraction else "",
        "counterparties": [],
        "action_items": [],
        "key_decisions": [],
        "entities_mentioned": [],
        "compliance_flags": [],
        "follow_ups": [],
        "intelligence_facts": [],
        "deal_stage": None,
        "next_meeting": None,
    }
    for k, default in defaults.items():
        extraction.setdefault(k, default)
    return extraction


async def _persist_actions(
    extraction: dict,
    *,
    notes_hash: str,
    meeting_label: str,
    source: str,
    user_id: str,
    chat_id: str,
) -> list[str]:
    """Create one pending_actions entry per extracted action. Returns IDs."""
    from . import pending_actions

    action_ids: list[str] = []
    for idx, item in enumerate(extraction.get("action_items") or []):
        owner = (item.get("owner") or "unassigned").strip()[:80]
        action = (item.get("action") or "").strip()
        if not action:
            continue
        deadline = (item.get("deadline") or "TBC").strip()[:80]
        related = (item.get("related_entity") or "").strip()[:120]

        severity = _severity_for_action(action, deadline)

        # Operator prompt — imperative, includes owner + deadline so the
        # daily briefing line is self-contained.
        op_prompt = (f"[{owner}] {action}"
                     + (f"  · due {deadline}" if deadline and deadline != "TBC" else "")
                     + (f"  · re {related}" if related else ""))

        try:
            entry = await pending_actions.record(
                promise=action[:600],
                reason=f"meeting_notes:{meeting_label}",
                resolver_kind="operator_action",
                resolver_ref=f"notes:{notes_hash[:12]}:act{idx}",
                severity=severity,
                source=source[:40],
                user_id=user_id[:80],
                chat_id=chat_id[:80],
                operator_prompt=op_prompt[:400],
                metadata={
                    "kind": "meeting_action",
                    "meeting_label": meeting_label,
                    "notes_hash": notes_hash,
                    "owner": owner,
                    "deadline": deadline,
                    "related_entity": related,
                    "action_index": idx,
                },
            )
            action_ids.append(entry["action_id"])
        except Exception as e:
            logger.warning("[meeting_notes] record action %d failed: %s", idx, e)

    return action_ids


async def process(
    notes: str,
    *,
    meeting_label: str = "Meeting notes",
    llm: Any = None,
    source: str = "chat",
    user_id: str = "",
    chat_id: str = "",
) -> dict[str, Any]:
    """Ingest pasted meeting notes end-to-end.

    Steps:
      1. Validate length + idempotency. If already processed, return the
         stored result with fresh 'reprocessed=True' flag.
      2. Extract structured fields via LLM.
      3. Persist each action_item into pending_actions.
      4. Store the combined result under the notes_hash for 30 days.
      5. Absorb event into brain_hook.

    Returns:
      {
        "ok": bool,
        "notes_hash": str,
        "reprocessed": bool,
        "meeting_label": str,
        "extraction": { ... extract() shape ... },
        "action_ids": [str, ...],     # pending_actions IDs created
        "action_count": int,
        "compliance_flag_count": int,
        "duration_ms": int,
        "error": str | None,
      }
    """
    t0 = time.time()
    notes = (notes or "").strip()
    if len(notes) < _MIN_NOTES_CHARS:
        return {
            "ok": False,
            "error": f"notes too short (min {_MIN_NOTES_CHARS} chars)",
            "notes_hash": None,
            "reprocessed": False,
            "meeting_label": meeting_label,
            "extraction": None,
            "action_ids": [],
            "action_count": 0,
            "compliance_flag_count": 0,
            "duration_ms": int((time.time() - t0) * 1000),
        }

    nh = _notes_hash(notes)
    from . import redis_store as rs

    # Idempotency check
    try:
        existing = await rs.get_json(_KEY_PROCESSED.format(hash=nh))
    except Exception as e:
        logger.debug("idempotency check failed: %s", e)
        existing = None

    if existing:
        existing = dict(existing)
        existing["reprocessed"] = True
        existing["duration_ms"] = int((time.time() - t0) * 1000)
        logger.info("[meeting_notes] idempotent hit for %s (%d actions)",
                    nh[:12], existing.get("action_count", 0))
        return existing

    # Fresh extraction
    extraction = await extract(notes, meeting_label=meeting_label, llm=llm)
    action_ids = await _persist_actions(
        extraction,
        notes_hash=nh,
        meeting_label=meeting_label,
        source=source,
        user_id=user_id,
        chat_id=chat_id,
    )

    result = {
        "ok": True,
        "notes_hash": nh,
        "reprocessed": False,
        "meeting_label": meeting_label,
        "extraction": extraction,
        "action_ids": action_ids,
        "action_count": len(action_ids),
        "compliance_flag_count": len(extraction.get("compliance_flags") or []),
        "ts": datetime.now(timezone.utc).isoformat(),
        "duration_ms": int((time.time() - t0) * 1000),
        "error": None,
    }

    # Store for idempotency + let operators review past processings
    try:
        await rs.set_json(
            _KEY_PROCESSED.format(hash=nh),
            result,
            ex=_IDEMPOTENCY_TTL_SEC,
        )
        await rs.lpush(_KEY_RECENT, nh)
        # Bound the list so it doesn't grow forever -- nothing reads it
        # today (intent was "let operators review past processings", but
        # no endpoint surfaces it yet), so without ltrim every meeting
        # stays in Redis indefinitely.
        await rs.ltrim(_KEY_RECENT, 0, 199)
    except Exception as e:
        logger.warning("[meeting_notes] persist result failed: %s", e)

    # Brain hook
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="meeting_notes",
            summary=(
                f"Processed '{meeting_label}': "
                f"{result['action_count']} actions, "
                f"{result['compliance_flag_count']} compliance flags, "
                f"{len(extraction.get('counterparties') or [])} counterparties"
            ),
            success=True,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("meeting_notes brain_hook failed: %s", _bh)

    return result


def render_markdown(result: dict[str, Any]) -> str:
    """Render the process() result as operator-facing Markdown."""
    if not result.get("ok"):
        return f"**Meeting notes error:** {result.get('error') or 'unknown'}"

    ext = result.get("extraction") or {}
    lines: list[str] = []
    label = result.get("meeting_label") or "Meeting notes"
    lines.append(f"# {label}")
    tag = " · _already processed — returning cached_" if result.get("reprocessed") else ""
    lines.append(f"*{result['action_count']} actions  ·  "
                 f"{result['compliance_flag_count']} compliance flags  ·  "
                 f"{result['duration_ms']} ms{tag}*")
    lines.append("")

    if ext.get("summary"):
        lines.append("## Summary")
        lines.append(ext["summary"])
        lines.append("")

    cps = ext.get("counterparties") or []
    if cps:
        lines.append("## Counterparties")
        for c in cps:
            lines.append(f"  - {c}")
        lines.append("")

    acts = ext.get("action_items") or []
    if acts:
        lines.append(f"## Action items ({len(acts)})")
        lines.append("_Each action is queued as a pending_actions entry — "
                     "the daily 05:45 briefing surfaces open items._")
        lines.append("")
        for a in acts:
            owner = a.get("owner") or "unassigned"
            deadline = a.get("deadline") or "TBC"
            related = a.get("related_entity") or ""
            rel = f"  _(re {related})_" if related else ""
            lines.append(f"  - **[{owner}]** {a.get('action', '')} — "
                         f"_due {deadline}_{rel}")
        lines.append("")

    decisions = ext.get("key_decisions") or []
    if decisions:
        lines.append("## Decisions")
        for d in decisions:
            lines.append(f"  - {d}")
        lines.append("")

    flags = ext.get("compliance_flags") or []
    if flags:
        lines.append("## Compliance flags")
        for f in flags:
            lines.append(f"  - {f}")
        lines.append("")

    follow_ups = ext.get("follow_ups") or []
    if follow_ups:
        lines.append("## Follow-ups (unowned)")
        for f in follow_ups:
            lines.append(f"  - {f}")
        lines.append("")

    facts = ext.get("intelligence_facts") or []
    if facts:
        lines.append("## Facts to remember")
        for f in facts:
            lines.append(f"  - {f}")
        lines.append("")

    nm = ext.get("next_meeting")
    if nm:
        lines.append(f"**Next meeting:** {nm}")

    return "\n".join(lines)
