"""ARIA Zoom Integration — meeting intelligence pipeline.

Three capabilities:
  1. TRANSCRIPT PROCESSING — download + analyse Zoom recording transcripts,
     extract facts, decisions, action items, store to knowledge base
  2. WEBHOOK RECEIVER — receive Zoom events (recording.completed, meeting.ended)
     and auto-process new recordings
  3. MEETING MANAGEMENT — schedule/create meetings via Zoom API so ARIA can
     share her own meeting links

Requires Zoom Server-to-Server OAuth app:
  - ZOOM_ACCOUNT_ID
  - ZOOM_CLIENT_ID
  - ZOOM_CLIENT_SECRET

Set up at: https://marketplace.zoom.us/develop/create
Select: Server-to-Server OAuth → Add scopes:
  - cloud_recording:read:list_user_recordings
  - cloud_recording:read:recording
  - meeting:write:meeting
  - meeting:read:meeting
  - user:read:user

Feature-gated: ARIA_ZOOM_ENABLED env var (default ON when credentials set).
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from . import redis_store as rs

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("aria.intel.zoom")

# ── Configuration ──────────────────────────────────────────────────────────

_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID", "").strip()
_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "").strip()
_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "").strip()
_WEBHOOK_SECRET = os.getenv("ZOOM_WEBHOOK_SECRET", "").strip()

_TOKEN_URL = "https://zoom.us/oauth/token"
_API_BASE = "https://api.zoom.us/v2"
_TIMEOUT = 30.0

# Redis keys
_TOKEN_KEY = "crucix:zoom:oauth_token"
_MEETINGS_LIST = "crucix:zoom:meetings_processed"
_TRANSCRIPTS_LIST = "crucix:zoom:transcripts"
_MAX_STORED = 200


def is_enabled() -> bool:
    if os.getenv("ARIA_ZOOM_ENABLED", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(_ACCOUNT_ID and _CLIENT_ID and _CLIENT_SECRET)


def get_status() -> dict:
    return {
        "enabled": is_enabled(),
        "account_id_set": bool(_ACCOUNT_ID),
        "client_id_set": bool(_CLIENT_ID),
        "client_secret_set": bool(_CLIENT_SECRET),
        "webhook_secret_set": bool(_WEBHOOK_SECRET),
        "setup_url": "https://marketplace.zoom.us/develop/create",
        "required_scopes": [
            "cloud_recording:read:list_user_recordings",
            "cloud_recording:read:recording",
            "meeting:write:meeting",
            "meeting:read:meeting",
            "user:read:user",
        ],
    }


# ── OAuth Token Management ─────────────────────────────────────────────────

async def _get_access_token() -> str | None:
    """Get a valid Zoom OAuth access token. Caches in Redis (55 min TTL)."""
    if not is_enabled():
        return None

    # Check cache
    cached = await rs.get(_TOKEN_KEY)
    if cached:
        return cached

    # Request new token (Server-to-Server OAuth)
    try:
        import base64
        credentials = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:  # no-breaker: Zoom integration is best-effort; breaker would block meeting recording
            resp = await client.post(
                _TOKEN_URL,
                params={"grant_type": "account_credentials", "account_id": _ACCOUNT_ID},
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if resp.status_code != 200:
                logger.warning("Zoom OAuth failed: %d %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            token = data.get("access_token", "")
            if token:
                # Cache for 55 minutes (tokens last 60 min)
                await rs.set(_TOKEN_KEY, token, ex=3300)
            return token
    except Exception as e:
        logger.warning("Zoom OAuth request failed: %s", e)
        return None


async def _zoom_api(method: str, path: str, **kwargs) -> dict | None:
    """Authenticated Zoom API call."""
    token = await _get_access_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await getattr(client, method)(
                f"{_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
            if resp.status_code == 401:
                # Token expired — clear cache and retry once
                await rs.delete(_TOKEN_KEY)
                token = await _get_access_token()
                if not token:
                    return None
                resp = await getattr(client, method)(
                    f"{_API_BASE}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
            if resp.status_code not in (200, 201):
                logger.debug("Zoom API %s %s: %d %s", method.upper(), path, resp.status_code, resp.text[:200])
                return None
            return resp.json()
    except Exception as e:
        logger.warning("Zoom API call failed: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITY 1: TRANSCRIPT PROCESSING
# ══════════════════════════════════════════════════════════════════════════════

async def list_recordings(user_id: str = "me", days: int = 7) -> list[dict]:
    """List recent cloud recordings for a Zoom user."""
    from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Go back N days
    import datetime as dt
    from_date = (datetime.now(timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data = await _zoom_api("get", f"/users/{user_id}/recordings", params={
        "from": from_date, "to": to_date, "page_size": 30,
    })
    if not data:
        return []

    meetings = data.get("meetings", [])
    results = []
    for m in meetings:
        files = m.get("recording_files", [])
        transcript_files = [f for f in files if f.get("file_type") == "TRANSCRIPT"]
        audio_files = [f for f in files if f.get("file_type") in ("MP4", "M4A")]

        results.append({
            "meeting_id": m.get("id"),
            "uuid": m.get("uuid"),
            "topic": m.get("topic"),
            "start_time": m.get("start_time"),
            "duration": m.get("duration"),
            "participants": m.get("total_size", 0),
            "has_transcript": len(transcript_files) > 0,
            "has_audio": len(audio_files) > 0,
            "transcript_url": transcript_files[0].get("download_url") if transcript_files else None,
            "recording_count": len(files),
        })
    return results


async def download_transcript(download_url: str) -> str | None:
    """Download a Zoom meeting transcript (VTT format)."""
    token = await _get_access_token()
    if not token or not download_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(
                download_url,
                params={"access_token": token},
            )
            if resp.status_code != 200:
                logger.warning("Transcript download failed: %d", resp.status_code)
                return None
            return resp.text
    except Exception as e:
        logger.warning("Transcript download error: %s", e)
        return None


def parse_vtt_transcript(vtt_text: str) -> list[dict]:
    """Parse Zoom VTT transcript into structured speaker turns."""
    if not vtt_text:
        return []

    lines = vtt_text.strip().split("\n")
    turns: list[dict] = []
    current_speaker = ""
    current_text = ""
    current_time = ""

    for line in lines:
        line = line.strip()
        if not line or line == "WEBVTT" or line.startswith("NOTE"):
            continue

        # Timestamp line: 00:00:01.000 --> 00:00:05.000
        if "-->" in line:
            current_time = line.split("-->")[0].strip()
            continue

        # Speaker: text line
        speaker_match = re.match(r"^(.+?):\s*(.+)$", line)
        if speaker_match:
            if current_speaker and current_text:
                turns.append({
                    "speaker": current_speaker,
                    "text": current_text.strip(),
                    "time": current_time,
                })
            current_speaker = speaker_match.group(1).strip()
            current_text = speaker_match.group(2).strip()
        elif line and current_speaker:
            current_text += " " + line

    # Last turn
    if current_speaker and current_text:
        turns.append({
            "speaker": current_speaker,
            "text": current_text.strip(),
            "time": current_time,
        })

    return turns


async def process_meeting_transcript(
    meeting_id: str,
    transcript_text: str,
    meeting_topic: str,
    llm: "LLMProvider | None" = None,
) -> dict:
    """Process a meeting transcript through ARIA's learning pipeline.

    Extracts:
      1. Key decisions made
      2. Action items with owners
      3. Intelligence facts (entities, deals, markets mentioned)
      4. Follow-up requirements
      5. Compliance flags (if defence/export topics discussed)

    Stores all findings to ARIA's knowledge base, MEM0, and neural memory.
    """
    t0 = time.time()
    result = {
        "ok": False, "meeting_id": meeting_id, "topic": meeting_topic,
        "facts_stored": 0, "actions_found": 0, "duration_ms": 0,
    }

    if not transcript_text or len(transcript_text) < 100:
        result["error"] = "Transcript too short"
        return result

    # Parse into speaker turns
    turns = parse_vtt_transcript(transcript_text)
    if not turns:
        # Treat as raw text if VTT parsing fails
        plain_text = transcript_text
    else:
        plain_text = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)

    # Cap for LLM processing
    plain_capped = plain_text[:12000]

    if not llm or not getattr(llm, "is_configured", False):
        # Store raw transcript as knowledge fact even without LLM
        try:
            from . import knowledge
            await knowledge.store_fact(
                topic=f"Zoom meeting: {meeting_topic}",
                content=plain_capped[:3000],
                source=f"zoom:meeting_{meeting_id}",
                confidence="ASSESSED",
            )
            result["ok"] = True
            result["facts_stored"] = 1
            result["note"] = "Stored raw transcript (no LLM for extraction)"
        except Exception as e:
            result["error"] = f"Knowledge store failed: {e}"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    # LLM-powered extraction
    extraction_prompt = f"""Analyse this meeting transcript and extract structured intelligence.

MEETING: {meeting_topic}
MEETING ID: {meeting_id}

TRANSCRIPT:
{plain_capped}

Extract the following as JSON:
{{
  "summary": "2-3 sentence summary of the meeting",
  "key_decisions": ["decision 1", "decision 2"],
  "action_items": [{{"owner": "person", "action": "what to do", "deadline": "when"}}],
  "entities_mentioned": [{{"name": "entity", "type": "company/person/country/product", "context": "what was said"}}],
  "intelligence_facts": ["fact 1 that ARIA should remember"],
  "compliance_flags": ["any SITCL/sanctions/export control topics discussed"],
  "follow_ups": ["what needs to happen next"],
  "deals_discussed": [{{"deal": "description", "stage": "stage", "value": "if mentioned"}}]
}}"""

    try:
        from . import cost_tracker
        with cost_tracker.feature("zoom"):
            llm_result = await llm.complete(
                "You are ARIA extracting intelligence from a meeting transcript. "
                "Output ONLY valid JSON. No markdown fences.",
                extraction_prompt,
                max_tokens=2000,
                timeout=60.0,
            )
        raw = (getattr(llm_result, "text", "") or "").strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        extraction = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("Meeting extraction JSON parse failed")
        extraction = {"summary": plain_capped[:500], "intelligence_facts": []}
    except Exception as e:
        logger.warning("Meeting extraction LLM call failed: %s", e)
        extraction = {"summary": plain_capped[:500], "intelligence_facts": []}

    # Store extracted intelligence to knowledge base
    facts_stored = 0
    try:
        from . import knowledge

        # Store summary
        await knowledge.store_fact(
            topic=f"Zoom meeting: {meeting_topic}",
            content=extraction.get("summary", "")[:1000],
            source=f"zoom:meeting_{meeting_id}",
            confidence="ASSESSED",
        )
        facts_stored += 1

        # Store each intelligence fact
        for fact in extraction.get("intelligence_facts", [])[:10]:
            if fact and len(fact) > 20:
                await knowledge.store_fact(
                    topic=f"Meeting insight: {fact[:60]}",
                    content=fact[:500],
                    source=f"zoom:meeting_{meeting_id}",
                    confidence="ASSESSED",
                )
                facts_stored += 1

        # Store entity mentions as knowledge
        for entity in extraction.get("entities_mentioned", [])[:10]:
            name = entity.get("name", "")
            context = entity.get("context", "")
            if name and context:
                await knowledge.store_fact(
                    topic=f"Entity from meeting: {name}",
                    content=f"{name} ({entity.get('type', 'unknown')}): {context}",
                    source=f"zoom:meeting_{meeting_id}",
                    confidence="ASSESSED",
                )
                facts_stored += 1

    except Exception as e:
        logger.warning("Meeting knowledge storage failed: %s", e)

    # Store to neural memory for concept graph growth
    try:
        from . import neural_memory
        await neural_memory.learn_from_text(
            plain_capped[:3000], source=f"zoom:{meeting_id}", llm=llm,
        )
    except Exception as e:
        logger.debug("Meeting neural memory failed: %s", e)

    # Persist transcript record
    record = {
        "meeting_id": meeting_id,
        "topic": meeting_topic,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "turns": len(turns),
        "chars": len(plain_text),
        "facts_stored": facts_stored,
        "actions": len(extraction.get("action_items", [])),
        "entities": len(extraction.get("entities_mentioned", [])),
        "compliance_flags": extraction.get("compliance_flags", []),
        "extraction": extraction,
    }
    await rs.lpush(_TRANSCRIPTS_LIST, json.dumps(record))
    await rs.ltrim(_TRANSCRIPTS_LIST, 0, _MAX_STORED - 1)

    result["ok"] = True
    result["facts_stored"] = facts_stored
    result["actions_found"] = len(extraction.get("action_items", []))
    result["entities_found"] = len(extraction.get("entities_mentioned", []))
    result["compliance_flags"] = extraction.get("compliance_flags", [])
    result["extraction"] = extraction
    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITY 2: WEBHOOK RECEIVER
# ══════════════════════════════════════════════════════════════════════════════

async def handle_webhook(event: dict, llm: "LLMProvider | None" = None) -> dict:
    """Process incoming Zoom webhook events.

    Supported events:
      - recording.completed — auto-download + process transcript
      - meeting.ended — log meeting metadata
    """
    event_type = event.get("event", "")
    payload = event.get("payload", {}) or {}

    if event_type == "recording.completed":
        return await _handle_recording_completed(payload, llm)
    elif event_type == "meeting.ended":
        return await _handle_meeting_ended(payload)
    elif event_type == "endpoint.url_validation":
        # Zoom webhook verification challenge
        plain_token = payload.get("plainToken", "")
        if plain_token and _WEBHOOK_SECRET:
            import hashlib, hmac
            hash_obj = hmac.new(
                _WEBHOOK_SECRET.encode(), plain_token.encode(), hashlib.sha256
            )
            return {
                "plainToken": plain_token,
                "encryptedToken": hash_obj.hexdigest(),
            }
        return {"error": "webhook verification failed"}
    else:
        return {"ignored": True, "event": event_type}


async def _handle_recording_completed(payload: dict, llm) -> dict:
    """Auto-process a completed recording."""
    obj = payload.get("object", {})
    meeting_id = str(obj.get("id", ""))
    topic = obj.get("topic", "Untitled Meeting")
    files = obj.get("recording_files", [])

    transcript_files = [f for f in files if f.get("file_type") == "TRANSCRIPT"]
    if not transcript_files:
        return {"processed": False, "reason": "no transcript file", "meeting_id": meeting_id}

    download_url = transcript_files[0].get("download_url", "")
    transcript_text = await download_transcript(download_url)
    if not transcript_text:
        return {"processed": False, "reason": "transcript download failed", "meeting_id": meeting_id}

    result = await process_meeting_transcript(meeting_id, transcript_text, topic, llm)

    # Push proactive alert so team knows ARIA processed the meeting
    try:
        from . import proactive
        await proactive.push_alert({
            "type": "zoom_meeting_processed",
            "title": f"📹 Meeting processed: {topic}",
            "severity": "info",
            "body": (
                f"Meeting: {topic}\n"
                f"Facts stored: {result.get('facts_stored', 0)}\n"
                f"Action items: {result.get('actions_found', 0)}\n"
                f"Entities: {result.get('entities_found', 0)}"
            ),
            "metadata": {"meeting_id": meeting_id},
        })
    except Exception as e:
        logger.debug("Zoom proactive alert failed: %s", e)

    # ── Active Challenge Engine on post-meeting transcript ──────────────────
    # Replaces Slice 6 devils_advocate. Adds DD gap detection against canonical
    # broker/oem/end_user/g2g checklists + structured challenge types.
    if os.getenv("ARIA_CHALLENGE_ENABLED", "0") == "1" and "#ariasilent" not in topic.lower():
        try:
            from .active_challenge_engine import (
                ARIAActiveChallengeEngine,
                DealContext,
                ChallengeType,
                ChallengeUrgency,
            )
            from . import self_metrics as _sm

            engine = ARIAActiveChallengeEngine(deliver_fn=None)
            # Deal context is best-effort — future: pull from pipeline by meeting topic
            ctx = DealContext(
                deal_name=topic[:80],
                deal_type=os.getenv("ARIA_DEFAULT_DEAL_TYPE", "broker"),
                stage="due_diligence",
            )

            # Generate challenges from full transcript
            challenges = engine._generate_challenges(transcript_text, ctx) or []
            challenges += engine.audit_assumptions(transcript_text, ctx)
            challenges += engine.check_dd_gaps(transcript_text, ctx.deal_type, ctx)

            # Filter by relevance
            challenges = [c for c in challenges if c.relevance_score >= engine.RELEVANCE_THRESHOLD]

            if challenges:
                by_type: dict[str, list] = {}
                for c in challenges[:10]:  # hard cap at 10
                    by_type.setdefault(c.challenge_type.value, []).append(c)

                body_lines = ["🎯 ARIA — Active Challenges"]
                emoji = {
                    "DEVIL_ADVOCATE":   "↪",
                    "DD_GAP":           "❓",
                    "ASSUMPTION":       "⚖",
                    "CONTRADICTION":    "⚠",
                    "RISK_BLIND_SPOT":  "👁",
                    "COMPLIANCE_FLAG":  "🛡",
                }
                for ctype, items_ in by_type.items():
                    body_lines.append(f"{ctype.replace('_', ' ').title()}:")
                    for c in items_[:3]:
                        body_lines.append(f"  {emoji.get(ctype,'•')} {c.text[:280]}")
                        if c.recommended_action:
                            body_lines.append(f"    → {c.recommended_action[:200]}")
                body_lines.append(
                    "Push back if any are wrong — feeds the mistake ledger."
                )
                await proactive.push_alert({
                    "type": "zoom_active_challenges",
                    "title": f"🎯 Active challenges: {topic}",
                    "severity": "info",
                    "body": "\n".join(body_lines),
                    "metadata": {
                        "meeting_id": meeting_id,
                        "challenge_count": len(challenges),
                        "deal_type": ctx.deal_type,
                    },
                })

            try:
                await _sm.emit(
                    axis="utility",
                    domain="active_challenge_engine",
                    signal="challenges_fired",
                    value=float(len(challenges)),
                    context={
                        "meeting_id": meeting_id,
                        "topic": topic[:80],
                        "path": "webhook",
                        "deal_type": ctx.deal_type,
                        "by_type": {k: len(v) for k, v in by_type.items()} if challenges else {},
                    },
                )
            except Exception as e:
                logger.debug("self_metrics emit failed: %s", e)
        except Exception as e:
            logger.warning("Active challenge engine (webhook) failed: %s", e)

    return result


async def _handle_meeting_ended(payload: dict) -> dict:
    """Log meeting metadata when a meeting ends."""
    obj = payload.get("object", {})
    record = {
        "meeting_id": str(obj.get("id", "")),
        "topic": obj.get("topic", ""),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration": obj.get("duration", 0),
        "participants": obj.get("participants_count", 0),
    }
    await rs.lpush(_MEETINGS_LIST, json.dumps(record))
    await rs.ltrim(_MEETINGS_LIST, 0, _MAX_STORED - 1)
    return {"logged": True, **record}


def verify_webhook_signature(request_body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Zoom webhook signature for security.

    R-F454 (2026-05-13) — fail-CLOSED when the webhook secret is unset.
    Pre-R-F454 this returned True if `_WEBHOOK_SECRET` was empty, which
    meant ANY signature was accepted in the default deploy config. The
    audit flagged this as a real security hole: if a deploy ever forgot
    to set ZOOM_WEBHOOK_SECRET, attackers could POST forged webhook
    events and ARIA would log them as authenticated. Now we reject by
    default and emit a startup-class warning so the operator sees the
    misconfiguration immediately (warning, not crash, so the rest of
    the service still boots).
    """
    if not _WEBHOOK_SECRET:
        logger.warning(
            "R-F454: zoom webhook called but ZOOM_WEBHOOK_SECRET is empty — "
            "rejecting. Set the env var to enable signed verification."
        )
        return False
    import hashlib, hmac
    message = f"v0:{timestamp}:{request_body.decode('utf-8')}"
    expected = hmac.new(
        _WEBHOOK_SECRET.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="zoom_integration",
        summary="Verify Webhook Signature",
        source_id="zoom_integration:R-F996",
    )

    return hmac.compare_digest(f"v0={expected}", signature)


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITY 3: MEETING MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def create_meeting(
    topic: str,
    duration: int = 60,
    start_time: str = "",
    agenda: str = "",
    user_id: str = "me",
) -> dict | None:
    """Create a Zoom meeting. Returns meeting details with join URL.

    Args:
        topic: Meeting title
        duration: Duration in minutes
        start_time: ISO 8601 format (e.g. "2026-04-15T14:00:00Z"). Empty = instant.
        agenda: Meeting description/agenda
        user_id: Zoom user ID or "me" for the account owner
    """
    body: dict[str, Any] = {
        "topic": topic,
        "type": 2 if start_time else 1,  # 1=instant, 2=scheduled
        "duration": duration,
        "timezone": "UTC",
        "agenda": agenda or f"ARIA-scheduled meeting: {topic}",
        "settings": {
            "host_video": True,
            "participant_video": True,
            "join_before_host": True,
            "auto_recording": "cloud",  # auto-record for transcript
            "waiting_room": False,
        },
    }
    if start_time:
        body["start_time"] = start_time

    data = await _zoom_api("post", f"/users/{user_id}/meetings", json=body)
    if not data:
        return None

    return {
        "meeting_id": data.get("id"),
        "topic": data.get("topic"),
        "join_url": data.get("join_url"),
        "start_url": data.get("start_url"),
        "password": data.get("password"),
        "start_time": data.get("start_time"),
        "duration": data.get("duration"),
        "host_email": data.get("host_email"),
    }


async def get_meeting(meeting_id: str) -> dict | None:
    """Get details of an existing meeting."""
    return await _zoom_api("get", f"/meetings/{meeting_id}")


async def list_upcoming_meetings(user_id: str = "me") -> list[dict]:
    """List upcoming scheduled meetings."""
    data = await _zoom_api("get", f"/users/{user_id}/meetings", params={
        "type": "upcoming", "page_size": 20,
    })
    if not data:
        return []
    return [
        {
            "meeting_id": m.get("id"),
            "topic": m.get("topic"),
            "start_time": m.get("start_time"),
            "duration": m.get("duration"),
            "join_url": m.get("join_url"),
        }
        for m in data.get("meetings", [])
    ]


# ── Retrieval ──────────────────────────────────────────────────────────────

async def get_processed_transcripts(limit: int = 20) -> list[dict]:
    """Return recently processed meeting transcripts."""
    raw = await rs.lrange(_TRANSCRIPTS_LIST, 0, limit - 1)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out


async def get_meeting_log(limit: int = 20) -> list[dict]:
    """Return recent meeting end events."""
    raw = await rs.lrange(_MEETINGS_LIST, 0, limit - 1)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
