"""
ARIA API Routes — all 18 endpoints matching the Node.js API surface.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..aria_engine import aria_chat, aria_think, get_identity
from ..intel import (
    knowledge,
    intel_ledger,
    contacts,
    competitors,
    approach,
    gtm_strategy,
    training_data,
    redis_store as rs,
)
from ..intel.researcher import (
    research_and_learn,
    read_article,
    read_document,
    extract_url_text,
    extract_url_deep,
    web_search,
    deep_research,
    validate_hypothesis,
    get_hypotheses,
    get_research_summary,
)
from ..intel.deep_researcher import (
    crawl_website,
    investigate,
    analyse_scenarios,
    build_profile,
    investigate_person,
    investigate_company,
    map_network,
    get_crawl_progress,
)
from ..intel import neural_memory
from ..intel import knowledge as knowledge_mod
from ..intel import self_improve
from ..intel import ocr as aria_ocr
from ..intel.semantic_search import semantic_search, get_index_stats
from ..intel import sanctions as aria_sanctions
from ..intel import conflict_tracker
from ..intel import tech_classifier
from ..intel import local_brain
from ..intel import reasoning_router
from ..intel import reasoning_library
from ..intel import symbolic_reasoner
from ..intel import student
from ..intel import proactive
from ..intel import rag_store
from ..intel import research_tasks
from ..intel import feedback as feedback_store
from ..intel import eval_runner
from ..intel import source_verifier
from ..intel import cost_tracker
from ..intel import trace_stream
from ..intel import honesty_judge

import logging
_log = logging.getLogger("aria.routes")

# Router-wide bearer-token enforcement. The require_aria_token dependency
# is defined below; the forward reference works because FastAPI resolves
# dependencies at request time, not import time. Soft-rollout: no-op when
# ARIA_API_TOKEN is unset, enforces when set.
def _router_auth_dep(request: Request) -> None:
    require_aria_token(request)

router = APIRouter(prefix="/api/aria", tags=["aria"], dependencies=[Depends(_router_auth_dep)])


# ── Bearer-token auth ───────────────────────────────────────────────────────
# 2026-04-08 round 7: protect the fly.io endpoints with a shared-secret
# bearer token. Set ARIA_API_TOKEN as a fly secret + on the seenode side
# (server.mjs uses INT_TOKEN-equivalent header) to enable enforcement.
#
# Soft-rollout design: when ARIA_API_TOKEN is UNSET, this dependency is a
# no-op (logs a warning once on startup so we know the service is open).
# When SET, every request to a protected route must carry
#   Authorization: Bearer <token>
# matching the secret, otherwise 401. This lets us deploy auth code, then
# set the secret in a separate step, then update server.mjs and the CLIs
# to send the token, all without a coordinated big-bang.
import os as _os

_AUTH_WARNING_LOGGED = False


def _aria_token() -> str:
    """Return the configured shared-secret token, or empty string if unset."""
    return (_os.getenv("ARIA_API_TOKEN") or "").strip()


def require_aria_token(request: Request) -> None:
    """FastAPI dependency that enforces a bearer-token check when
    ARIA_API_TOKEN is set. No-op when unset (soft rollout)."""
    global _AUTH_WARNING_LOGGED
    expected = _aria_token()
    if not expected:
        if not _AUTH_WARNING_LOGGED:
            _log.warning(
                "[auth] ARIA_API_TOKEN not set — fly.io endpoints are OPEN to the public internet. "
                "Set the secret to enable enforcement."
            )
            _AUTH_WARNING_LOGGED = True
        return
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected 'Bearer <token>'.",
        )
    presented = auth_header[7:].strip()
    # Constant-time-ish comparison — Python's == is technically not constant
    # time but for a short shared secret on a low-volume API the timing leak
    # is negligible. If we ever go higher-stakes, swap for hmac.compare_digest.
    if presented != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


# ── Input sanitisation ──────────────────────────────────────────────────────
_COUNTRY_RE = re.compile(r"^[a-zA-Z\s\-']{2,60}$")

def _validate_country(country: str) -> str:
    """Sanitise country parameter — alphanumeric + spaces only."""
    c = country.strip()
    if not _COUNTRY_RE.match(c):
        raise HTTPException(status_code=400, detail="Invalid country parameter")
    return c


# ── Request/Response Models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    auto_tools: bool = True   # auto-detect intent and call investigate/crawl/read tools
    # Group context as a SEPARATE field — populated by the WhatsApp listener
    # with the last 5 group messages so ARIA has multi-participant context.
    # The chat handler treats this as an ADDITIONAL context layer (not as
    # part of the user's message body) to avoid the prior-turn topic bleed
    # that caused the DUMA / Iraq incident on 2026-04-09. Empty string when
    # the caller is a single-user channel (curl, frontend, /ask command).
    group_context: str = ""

class ThinkRequest(BaseModel):
    question: str
    context: dict | None = None
    fast: bool = False

class FactRequest(BaseModel):
    topic: str
    content: str
    confidence: str = "CONFIRMED"
    source: str = "user"

class ContactRequest(BaseModel):
    name: str
    country: str
    role: str = ""
    title: str = ""
    organisation: str = ""
    influence: str = "MEDIUM"
    notes: str = ""

class ApproachRequest(BaseModel):
    market: str
    product: str = ""
    context: str = ""

class CorrectionRequest(BaseModel):
    originalQuery: str = ""
    originalResponse: str = ""
    correction: str
    correctAnswer: str = ""

class LearningRequest(BaseModel):
    correction: str
    context: str = ""


# ── Dependency: get app state ────────────────────────────────────────────────

def get_llm(request: Request):
    return request.app.state.llm_provider

def get_intel_data(request: Request):
    return getattr(request.app.state, "current_data", None)


# ── Routes ───────────────────────────────────────────────────────────────────

# 1. GET /api/aria/identity
@router.get("/identity")
async def identity_ep():
    idn = await get_identity()
    return {
        "name": idn.get("name", "ARIA"),
        "full_name": idn.get("full_name", "Arkmurus Research Intelligence Agent"),
        "status": "online",
        "mode": "python",
        "age_days": idn.get("age_days", 0),
        "total_sweeps": idn.get("total_sweeps", 0),
        "total_leads": idn.get("total_leads", 0),
        "domain": "Defence procurement intelligence",
    }


# 2. GET /api/aria/thoughts
@router.get("/thoughts")
async def thoughts_ep():
    thought_ids = await rs.lrange("crucix:brain:aria:thoughts", 0, 9)
    thoughts = []
    for tid in thought_ids:
        raw = await rs.get_json(f"crucix:brain:aria:thought:{tid}")
        if raw:
            thoughts.append(raw)
    return thoughts


# 3. GET /api/aria/curiosity
@router.get("/curiosity")
async def curiosity_ep():
    idn = await get_identity()
    threads = [t for t in idn.get("curiosity_threads", []) if not t.get("resolved")]
    return {"open_threads": threads}


# 4. GET /api/aria/knowledge
@router.get("/knowledge")
async def knowledge_ep():
    return await knowledge.get_stats()


# 5. POST /api/aria/knowledge/fact
@router.post("/knowledge/fact")
async def store_fact_ep(req: FactRequest):
    result = await knowledge.store_fact(req.topic, req.content, req.source, req.confidence)
    return {"ok": True, "message": "Fact stored", "action": result.get("action", "unknown")}


# 6. GET /api/aria/ledger
@router.get("/ledger")
async def ledger_ep():
    return await intel_ledger.get_stats()


# 7. GET /api/aria/ledger/country/{country}
@router.get("/ledger/country/{country}")
async def ledger_country_ep(country: str):
    country = _validate_country(country)
    return await intel_ledger.get_country_situation(country)


# 8. GET /api/aria/contacts
@router.get("/contacts")
async def contacts_ep():
    all_c = await contacts.get_all()
    return {"contacts": all_c}


# 9. GET /api/aria/contacts/country/{country}
@router.get("/contacts/country/{country}")
async def contacts_country_ep(country: str):
    country = _validate_country(country)
    cs = await contacts.get_by_country(country)
    return {"contacts": cs}


# 10. POST /api/aria/contacts
@router.post("/contacts")
async def add_contact_ep(req: ContactRequest):
    await contacts.add_contact(req.model_dump())
    return {"ok": True, "message": "Contact added"}


# 11. POST /api/aria/approach
@router.post("/approach")
async def approach_ep(req: ApproachRequest):
    result = approach.generate_approach(req.market, req.product, req.context)
    return result


# 12. POST /api/aria/correct
@router.post("/correct")
async def correct_ep(req: CorrectionRequest):
    await training_data.record_correction(
        req.originalQuery, req.originalResponse, req.correction, req.correctAnswer,
    )
    await knowledge.store_learning(req.correction, req.originalQuery)
    return {"ok": True, "message": "Correction recorded — ARIA will learn from this"}


# 13. GET /api/aria/training-data/stats
# ── Student mode: active learning ──────────────────────────────────────────
@router.get("/student/stats")
async def student_stats_ep():
    """Full student dashboard — mastery + curriculum + quizzes + reading."""
    return await student.get_student_stats()


@router.get("/student/health")
async def student_health_ep():
    """Verify the student loops are actually running and producing output.

    Returns a per-loop "are you alive?" check based on Redis state. If a
    loop hasn't fired in N hours when it should, this surfaces the silence.
    """
    import time as _t
    now = _t.time()
    quiz_history = await student.rs.get_json(student.QUIZ_HISTORY_KEY) or []
    reading_log = await student.rs.get_json(student.READING_LOG_KEY) or []
    library_stats = await reasoning_library.get_stats()
    mastery = await student.get_mastery_report()

    last_quiz = quiz_history[-1] if quiz_history else None
    last_reading = reading_log[-1] if reading_log else None
    last_quiz_age_h = (now - last_quiz.get("ts", 0)) / 3600 if last_quiz else None
    last_reading_age_h = (now - last_reading.get("ts", 0)) / 3600 if last_reading else None

    # Health checks (each loop has an expected cadence)
    issues: list[str] = []
    if last_quiz_age_h is None:
        issues.append("Self-quiz loop has NEVER fired (or library was empty when it last tried).")
    elif last_quiz_age_h > 4:
        issues.append(f"Self-quiz hasn't fired in {last_quiz_age_h:.1f}h (expected every 3h).")

    if last_reading_age_h is None:
        issues.append("Reading session loop has NEVER fired.")
    elif last_reading_age_h > 8:
        issues.append(f"Reading session hasn't fired in {last_reading_age_h:.1f}h (expected every 6h).")

    if library_stats.get("total_cases", 0) == 0:
        issues.append("Reasoning library is empty — no cloud answers have been distilled yet.")

    if mastery.get("total_samples", 0) == 0:
        issues.append("Mastery tracker has zero samples — no conversations have updated topic scores.")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "loops": {
            "self_quiz": {
                "last_fired_hours_ago": round(last_quiz_age_h, 2) if last_quiz_age_h is not None else None,
                "total_quizzes": len(quiz_history),
                "last_score": last_quiz.get("score") if last_quiz else None,
            },
            "reading_session": {
                "last_fired_hours_ago": round(last_reading_age_h, 2) if last_reading_age_h is not None else None,
                "total_sessions": len(reading_log),
                "last_articles_read": last_reading.get("articles_read") if last_reading else None,
            },
        },
        "library": {
            "total_cases": library_stats.get("total_cases", 0),
            "hit_rate": library_stats.get("hit_rate", 0),
            "embedder_available": library_stats.get("embedder_available", False),
        },
        "mastery": {
            "overall": mastery.get("overall_mastery", 0),
            "total_samples": mastery.get("total_samples", 0),
            "weak_count": len(mastery.get("weak_topics", [])),
            "strong_count": len(mastery.get("strong_topics", [])),
        },
    }


# ── Research tasks: long-running background research operations ──────────
class SpawnTaskRequest(BaseModel):
    type: str
    params: dict = {}
    title: str = ""
    requested_by: str = "api"
    chat_id: str = ""


@router.post("/research/spawn")
async def research_spawn_ep(req: SpawnTaskRequest, request: Request):
    """Spawn a long-running research task. Returns immediately with the
    task_id so the caller can acknowledge the user. Actual work runs in
    the background and pushes results via the proactive alert queue when done.
    """
    if not req.type:
        raise HTTPException(status_code=400, detail="task type required")
    llm = get_llm(request)
    return await research_tasks.spawn_research_task(
        task_type=req.type,
        params=req.params or {},
        title=req.title,
        requested_by=req.requested_by,
        chat_id=req.chat_id,
        llm=llm,
    )


@router.get("/research/task/{task_id}")
async def research_task_ep(task_id: str):
    """Get the current state of a research task — status, progress, result."""
    task = await research_tasks.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/research/list")
async def research_list_ep(limit: int = 30, status: str | None = None):
    """List recent research tasks, optionally filtered by status."""
    limit = max(1, min(limit, 200))
    return {
        "tasks": await research_tasks.list_tasks(limit=limit, status_filter=status),
    }


@router.get("/research/stats")
async def research_stats_ep():
    """Overview of the research task system."""
    return await research_tasks.get_stats()


# ── Feedback: WhatsApp reaction → ground-truth signal ────────────────────
class FeedbackSnapshotRequest(BaseModel):
    chat_id: str
    msg_id: str
    question: str = ""
    answer: str = ""
    user: str = ""
    group_name: str = ""
    metadata: dict = {}
    trace_id: str = ""


class FeedbackReactionRequest(BaseModel):
    chat_id: str
    msg_id: str
    emoji: str
    reactor: str = ""
    reactor_jid: str = ""


@router.post("/feedback/snapshot")
async def feedback_snapshot_ep(req: FeedbackSnapshotRequest):
    """Persist the Q→A pair for an ARIA reply so a later WhatsApp reaction
    can retrieve the context. Called by the WA listener after sending.
    Accepts trace_id (from /chat response) so reactions can attach
    themselves to the trace_stream record for joined inspection."""
    return await feedback_store.snapshot_reply(
        chat_id=req.chat_id,
        msg_id=req.msg_id,
        question=req.question,
        answer=req.answer,
        user=req.user,
        group_name=req.group_name,
        metadata=req.metadata,
        trace_id=req.trace_id,
    )


@router.post("/feedback")
async def feedback_record_ep(req: FeedbackReactionRequest):
    """Record a WhatsApp reaction emoji as feedback on an ARIA reply.
    Looks up the snapshot if present, classifies sentiment from the emoji,
    and (on negative) fires the self-improve diagnose loop."""
    if not req.chat_id or not req.msg_id:
        raise HTTPException(status_code=400, detail="chat_id and msg_id required")
    return await feedback_store.record_feedback(
        chat_id=req.chat_id,
        msg_id=req.msg_id,
        emoji=req.emoji,
        reactor=req.reactor,
        reactor_jid=req.reactor_jid,
    )


@router.get("/feedback/list")
async def feedback_list_ep(limit: int = 30, sentiment: str | None = None):
    """List recent feedback. sentiment ∈ {positive, negative, uncertain, neutral}."""
    return {
        "feedback": await feedback_store.list_feedback(
            limit=limit, sentiment_filter=sentiment,
        ),
    }


@router.get("/feedback/stats")
async def feedback_stats_ep():
    """Aggregate feedback counts + rough quality score."""
    return await feedback_store.get_feedback_stats()


@router.get("/feedback/{feedback_id}")
async def feedback_get_ep(feedback_id: str):
    """Full record for one feedback item, including original Q&A snapshot."""
    rec = await feedback_store.get_feedback(feedback_id)
    if not rec:
        raise HTTPException(status_code=404, detail="feedback not found")
    return rec


# ── Eval: golden Q&A regression framework ────────────────────────────────
async def _aria_chat_session(question: str, llm) -> str:
    """One-shot chat call used by the eval runner. Mirrors chat_ep() so the
    eval exercises the SAME path the user hits (NLU tool detection + chat),
    just without the FastAPI Request object. Each call uses a fresh session
    id so eval entries don't pollute each other's history."""
    if not question or not llm:
        return ""
    session_id = f"eval_{uuid.uuid4().hex[:10]}"
    tool_context = ""
    try:
        intent = _detect_tool_intent(question)
        if intent and llm.is_configured:
            tool_context = await _execute_tool(intent, llm)
    except Exception as e:
        _log.debug("eval tool detection failed: %s", e)

    message_for_llm = question
    if tool_context:
        message_for_llm = f"{question}\n\n{tool_context}"
    result = await aria_chat(message_for_llm, session_id, llm, None)
    return (result or {}).get("response") or (result or {}).get("answer") or ""


class GoldenAddRequest(BaseModel):
    question: str
    expected_answer: str
    category: str = "general"
    notes: str = ""
    added_by: str = ""


class PromoteFeedbackRequest(BaseModel):
    feedback_id: str
    category: str = "feedback"
    notes: str = ""
    added_by: str = ""


class RunEvalRequest(BaseModel):
    ids: list[str] = []
    label: str = ""


@router.get("/eval/golden")
async def eval_golden_list_ep():
    """List all golden Q&A entries."""
    items = await eval_runner.get_golden_set()
    return {"total": len(items), "entries": items}


@router.post("/eval/golden")
async def eval_golden_add_ep(req: GoldenAddRequest):
    """Manually add a golden Q&A entry."""
    return await eval_runner.add_golden_entry(
        question=req.question,
        expected_answer=req.expected_answer,
        category=req.category,
        notes=req.notes,
        source="manual",
        added_by=req.added_by,
    )


@router.post("/eval/golden/promote")
async def eval_golden_promote_ep(req: PromoteFeedbackRequest):
    """Promote a feedback record into the golden set. Use this for 👍-rated
    answers — the answer ARIA gave becomes the expected answer. For 👎
    feedback, edit the expected_answer manually via POST /eval/golden first."""
    return await eval_runner.promote_feedback_to_golden(
        feedback_id=req.feedback_id,
        category=req.category,
        notes=req.notes,
        added_by=req.added_by,
    )


@router.delete("/eval/golden/{entry_id}")
async def eval_golden_remove_ep(entry_id: str):
    return await eval_runner.remove_golden_entry(entry_id)


@router.post("/eval/run")
async def eval_run_ep(req: RunEvalRequest, request: Request):
    """Execute the golden set against the current ARIA chat path. Returns
    a run record with per-entry scores, summary, and delta vs the previous run."""
    llm = get_llm(request)
    if not llm:
        raise HTTPException(status_code=503, detail="LLM not configured")
    return await eval_runner.run_eval(llm, ids=req.ids or None, label=req.label)


@router.get("/eval/runs")
async def eval_runs_ep(limit: int = 10):
    """Recent eval run summaries (lightweight — no per-entry detail)."""
    return {"runs": await eval_runner.get_recent_runs(limit=limit)}


@router.get("/eval/runs/{run_id}")
async def eval_run_get_ep(run_id: str):
    """Full eval run record including per-entry scores."""
    run = await eval_runner.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run


# ── Source verification: deterministic citation grounding check ──────────
@router.get("/verify/list")
async def verify_list_ep(limit: int = 30, verdict: str | None = None):
    """List recent verification records.
    verdict ∈ {grounded, partial, ungrounded, no_citations, no_tool}"""
    return {
        "verifications": await source_verifier.list_verifications(
            limit=limit, verdict_filter=verdict,
        ),
    }


@router.get("/verify/stats")
async def verify_stats_ep():
    """Aggregate verification counts + rolling grounded rate."""
    return await source_verifier.get_verification_stats()


@router.get("/verify/{verification_id}")
async def verify_get_ep(verification_id: str):
    """Full verification record including the actual cited and fetched URL lists."""
    rec = await source_verifier.get_verification(verification_id)
    if not rec:
        raise HTTPException(status_code=404, detail="verification not found")
    return rec


# ── Cost tracking: tokens + USD per LLM call, per feature ────────────────
@router.get("/cost/summary")
async def cost_summary_ep(window_hours: int = 24):
    """Rolling cost over the last N hours, broken down by feature + model."""
    return await cost_tracker.get_cost_summary(window_hours=window_hours)


@router.get("/cost/cumulative")
async def cost_cumulative_ep():
    """All-time per-feature totals (survives index rotation)."""
    return await cost_tracker.get_cumulative_aggregate()


@router.get("/cost/recent")
async def cost_recent_ep(
    limit: int = 30,
    feature: str | None = None,
    model: str | None = None,
):
    """Recent LLM call summaries (lightweight — no full prompts)."""
    return {
        "calls": await cost_tracker.list_recent_calls(
            limit=limit, feature_filter=feature, model_filter=model,
        ),
    }


@router.get("/cost/call/{call_id}")
async def cost_call_get_ep(call_id: str):
    """Full record for one LLM call including latency + error details."""
    rec = await cost_tracker.get_call_record(call_id)
    if not rec:
        raise HTTPException(status_code=404, detail="call not found")
    return rec


# ── Trace stream: joined view across cost / verification / feedback ──────
@router.get("/trace/recent")
async def trace_recent_ep(
    limit: int = 30,
    status: str | None = None,
    source: str | None = None,
    bad_only: bool = False,
):
    """List recent trace summaries. bad_only=true returns traces that
    either errored, scored ungrounded, or got 👎 feedback — i.e. the set
    the team should investigate first."""
    return {
        "traces": await trace_stream.list_traces(
            limit=limit,
            status_filter=status,
            source_filter=source,
            bad_only=bad_only,
        ),
    }


@router.get("/trace/stats")
async def trace_stats_ep():
    """Aggregate trace counts + averages."""
    return await trace_stream.get_trace_stats()


@router.get("/trace/{trace_id}")
async def trace_get_ep(trace_id: str):
    """Full trace record — question, response, every LLM call with cost,
    verification verdict, feedback (if any). The complete lifecycle of
    one ARIA reply in one record."""
    rec = await trace_stream.get_trace(trace_id)
    if not rec:
        raise HTTPException(status_code=404, detail="trace not found")
    return rec


# ── Honesty judge: confidence-tag verification ───────────────────────────
@router.get("/honesty/list")
async def honesty_list_ep(limit: int = 30, status: str | None = None, bad_only: bool = False):
    """List recent honesty judgments. bad_only=true returns only judgments
    where the score fell below the suspicious threshold (0.7)."""
    return {
        "judgments": await honesty_judge.list_judgments(
            limit=limit, status_filter=status, bad_only=bad_only,
        ),
    }


@router.get("/honesty/stats")
async def honesty_stats_ep():
    """Aggregate counts + rolling honesty score across all judgments."""
    return await honesty_judge.get_honesty_stats()


@router.get("/honesty/{judgment_id}")
async def honesty_get_ep(judgment_id: str):
    """Full judgment record including each [CONFIRMED] claim, the per-claim
    supported/unsupported verdict, and the judge's reason for each."""
    rec = await honesty_judge.get_judgment(judgment_id)
    if not rec:
        raise HTTPException(status_code=404, detail="judgment not found")
    return rec


# ── RAG store: persistent retrieval-augmented generation ──────────────────
@router.get("/rag/stats")
async def rag_stats_ep():
    """Report on the persistent RAG store: documents indexed, path, model."""
    return await rag_store.get_stats()


@router.get("/rag/sources")
async def rag_sources_ep(limit: int = 50):
    """List all unique sources in the RAG store grouped by type."""
    limit = max(1, min(limit, 500))
    return await rag_store.list_sources(limit=limit)


class RagSearchRequest(BaseModel):
    query: str
    top_k: int = 8
    source_type: str | None = None
    market: str | None = None


@router.post("/rag/search")
async def rag_search_ep(req: RagSearchRequest):
    """Hybrid retrieval over the RAG store. Returns ranked chunks with metadata."""
    if not req.query or len(req.query.strip()) < 3:
        raise HTTPException(status_code=400, detail="query required (min 3 chars)")
    results = await rag_store.search(
        req.query,
        top_k=max(1, min(req.top_k, 30)),
        source_type=req.source_type,
        market=req.market,
    )
    return {"query": req.query, "results": results, "count": len(results)}


class RagIngestRequest(BaseModel):
    text: str
    source: str
    source_type: str = "manual"
    title: str = ""
    url: str = ""
    market: str = ""


@router.post("/rag/ingest")
async def rag_ingest_ep(req: RagIngestRequest):
    """Manually ingest a document into the RAG store. Used for backfill,
    customer document drops, and any text the team wants ARIA to remember.
    """
    if not req.text or len(req.text.strip()) < 50:
        raise HTTPException(status_code=400, detail="text required (min 50 chars)")
    return await rag_store.ingest_document(
        text=req.text,
        source=req.source,
        source_type=req.source_type,
        title=req.title,
        url=req.url,
        market=req.market,
    )


@router.post("/rag/backfill")
async def rag_backfill_ep():
    """One-shot backfill: index every existing fact + ledger signal into RAG.
    Idempotent — chromadb upserts so re-running is safe.
    """
    return await rag_store.backfill_from_existing()


# ── Proactive watch ────────────────────────────────────────────────────────
@router.get("/proactive/stats")
async def proactive_stats_ep():
    """Stats for the proactive watch system — how many alerts queued, etc."""
    return await proactive.get_proactive_stats()


@router.get("/proactive/alerts")
async def proactive_alerts_ep(mark_seen: bool = False):
    """Drain the proactive alert queue. Used by the WhatsApp listener to
    poll for new alerts to push to the team.
    """
    alerts = await proactive.get_unseen_alerts(mark_seen=mark_seen)
    return {"alerts": alerts, "count": len(alerts)}


@router.get("/student/mastery")
async def student_mastery_ep():
    """Per-topic competence scores."""
    return await student.get_mastery_report()


@router.get("/student/curriculum")
async def student_curriculum_ep():
    """What ARIA should study next, prioritised by weakness + staleness."""
    return await student.get_curriculum()


@router.post("/student/quiz")
async def student_quiz_ep(request: Request):
    """Trigger an immediate self-quiz. Picks N stale library cases, attempts
    them locally, scores divergence vs the original cloud answer, updates mastery.
    """
    body = {}
    try:
        body = await request.json()
    except Exception as e:
        _log.warning("student_quiz_ep: failed to parse request body, using defaults: %s", e)
    n = max(1, min(int(body.get("num_questions", 5)), 20))
    return await student.self_quiz(num_questions=n)


@router.post("/student/study")
async def student_study_ep(request: Request):
    """Trigger an immediate reading session — focus on weak topics, deep-read
    authoritative sources, extract facts + index into knowledge + neural memory.
    """
    body = {}
    try:
        body = await request.json()
    except Exception as e:
        _log.warning("student_study_ep: failed to parse request body, using defaults: %s", e)
    n = max(1, min(int(body.get("num_articles", 4)), 10))
    llm = get_llm(request)
    return await student.reading_session(llm=llm, num_articles=n)


# ── Independence + reasoning ratio ─────────────────────────────────────────
@router.get("/independence")
async def independence_ep():
    """Report ARIA's reasoning independence ratio.

    Tracks: how many of her recent answers came from local reasoning sources
    (symbolic_reasoner, reasoning_library, local_brain, local_ollama) vs the
    cloud LLM. The trajectory shows how she is detaching from DeepSeek over
    time as the library warms up and local models come online.

    This is THE metric for the "ARIA-LLM" product roadmap.
    """
    return await reasoning_router.get_independence_report()


@router.post("/reasoning-library/find")
async def reasoning_library_find_ep(request: Request):
    """Look up a question in ARIA's reasoning library. Pure local — no LLM call.

    Body: {question: "..."}
    Returns the best matching prior case if any.
    """
    body = await request.json()
    q = (body.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question required")
    return await reasoning_library.find_match(q)


@router.get("/reasoning-library/stats")
async def reasoning_library_stats_ep():
    return await reasoning_library.get_stats()


@router.post("/reasoning-library/consolidate")
async def reasoning_library_consolidate_ep():
    """Trigger maintenance — prune stale cases, promote high-quality."""
    return await reasoning_library.consolidate()


@router.post("/reasoning-library/feedback")
async def reasoning_library_feedback_ep(request: Request):
    """Record a positive/negative outcome for a library case.

    Body: {case_id: "...", positive: true/false}
    Used by the learning loop to upweight good answers and downweight bad.
    """
    body = await request.json()
    case_id = (body.get("case_id") or "").strip()
    positive = bool(body.get("positive", True))
    if not case_id:
        raise HTTPException(status_code=400, detail="case_id required")
    return await reasoning_library.record_outcome(case_id, positive)


@router.post("/reasoning/test")
async def reasoning_test_ep(request: Request):
    """Run a question through the FULL local reasoning pipeline without
    falling through to a cloud LLM. Useful for testing what ARIA can answer
    on her own RIGHT NOW. Returns the trace of which stages were tried.
    """
    body = await request.json()
    q = (body.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question required")
    return await reasoning_router.try_local_reasoning(q)


@router.get("/training-data/library-export")
async def training_data_library_export_ep():
    """Export the reasoning library as JSONL training data.

    This is the path to ARIA-LLM. The library captures (question, response,
    confidence, intent) tuples — when there are enough of them (~10k+ high-
    quality cases) you can fine-tune a 7B base model (Qwen2.5/Llama3.1) to
    serve as ARIA's INDEPENDENT reasoning brain, replacing DeepSeek entirely.

    Output is OpenAI / Anthropic / Llama format compatible.
    """
    library_stats = await reasoning_library.get_stats()
    index = await reasoning_library._load_index()
    samples = []
    for entry in index[:5000]:  # cap export to 5000 most recent for memory
        case = await reasoning_library.rs.get_json(reasoning_library._case_key(entry["id"]))
        if not case:
            continue
        samples.append({
            "messages": [
                {"role": "system", "content": "You are ARIA — defence procurement and security intelligence analyst."},
                {"role": "user", "content": case.get("question", "")},
                {"role": "assistant", "content": case.get("response", "")},
            ],
            "metadata": {
                "intent": case.get("intent"),
                "confidence_tag": case.get("confidence_tag"),
                "confidence_score": case.get("confidence_score"),
                "source_brain": case.get("source_brain"),
                "access_count": case.get("access_count"),
                "positive_outcomes": case.get("positive_outcomes"),
                "negative_outcomes": case.get("negative_outcomes"),
            },
        })
    return {
        "format": "messages_jsonl",
        "samples": samples,
        "total": len(samples),
        "library_stats": library_stats,
        "fine_tune_targets": [
            "Qwen2.5-7B-Instruct (recommended — strong reasoning, Apache-2.0)",
            "Llama-3.1-8B-Instruct (alternative, Meta licence)",
            "Mistral-7B-Instruct-v0.3 (alternative, Apache-2.0)",
            "DeepSeek-R1-Distill-Qwen-7B (chain-of-thought specialist)",
        ],
        "instructions": (
            "1. Save this as aria_training.jsonl. "
            "2. Use Axolotl/Unsloth for LoRA fine-tuning (~£15 on a single A100 hour). "
            "3. Deploy via Ollama: `ollama create aria-llm -f Modelfile` then "
            "set LLM_PROVIDER=ollama LLM_MODEL=aria-llm. "
            "4. ARIA's reasoning becomes 100% local at that point."
        ),
    }


@router.get("/training-data/calibration")
async def training_calibration_ep():
    """ARIA's confidence calibration report.

    Compares self-tagged confidence ([CONFIRMED]/[PROBABLE]/etc) against actual
    outcomes (corrections + recorded losses). Surfaces overconfident or
    underconfident tiers and recommends threshold adjustments.
    """
    return await training_data.get_calibration()


@router.get("/training-data/stats")
async def training_stats_ep():
    return await training_data.get_stats()


# 14. GET /api/aria/training-data/export
@router.get("/training-data/export")
async def training_export_ep(request: Request):
    data = await training_data.export_training_data()
    fmt = request.query_params.get("format", "json")
    if fmt == "jsonl":
        lines = "\n".join(json.dumps(d, default=str) for d in data.get("data", []))
        return Response(
            content=lines,
            media_type="application/jsonl",
            headers={"Content-Disposition": "attachment; filename=aria_training_data.jsonl"},
        )
    return data


# 15. GET /api/aria/gtm/{market}
@router.get("/gtm/{market}")
async def gtm_ep(market: str):
    result = gtm_strategy.generate_gtm_strategy(market)
    if not result:
        raise HTTPException(status_code=404, detail="Market not found")
    return result


# 16. POST /api/aria/research
@router.post("/research")
async def research_ep(request: Request):
    body = await request.json()
    topic = body.get("topic", "")
    market = body.get("market", "")
    if not topic:
        raise HTTPException(status_code=400, detail="topic required")

    # Sanitize
    topic = re.sub(r"[^a-zA-Z0-9\s\-]", "", topic)[:100]
    market = re.sub(r"[^a-zA-Z0-9\s\-]", "", market)[:50]

    # Store query
    await knowledge.record_query(topic, f"Research request: {topic} {market}", market)
    return {"ok": True, "message": f"Research query recorded: {topic} {market}"}


# 17. POST /api/aria/knowledge/learn
@router.post("/knowledge/learn")
async def learn_ep(req: LearningRequest):
    await knowledge.store_learning(req.correction, req.context)
    return {"ok": True, "message": "Learning stored"}


# ── NLU: detect investigative intent in free-form text ──────────────────────
_URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|co\.uk|org|net|gov|edu|io|ai|de|fr|pt|es|br|mz|ao|cv|ke|ng|za|cn|ru)(?:/\S*)?", re.IGNORECASE)

# Imperative verbs (and noun equivalents) that signal "go do something"
# rather than "answer me from training data". The noun forms were added
# 2026-04-09 19:38 after the DUMA Engineering incident: the user said
# "Aria, investigation https://duma-engineering.com?" (noun, not verb)
# and the regex missed it, so the chain fell through to route 3
# (extract_url, single page) instead of route 2 (deep_research, 5-angle
# + extracts). The resulting brief only saw the homepage and called
# DUMA a "potential shell entity" because off-site OSINT (Jane's,
# LinkedIn, Crunchbase) was never queried.
_INVESTIGATE_KW = re.compile(
    r"\b("
    r"investigate|investigation|investigations|investigating|"
    r"research|researching|researches|"
    r"look\s+into|looking\s+into|"
    r"dig\s+into|digging\s+into|"
    r"find\s+out\s+about|"
    r"deep[\-\s]?dive|do\s+a\s+deep\s+dive|"
    r"tell\s+me\s+everything\s+about|"
    r"explore|exploring|"
    r"due\s+diligence|"
    r"DD\s+(?:on|of)|"
    r"background\s+check"
    r")\b",
    re.IGNORECASE,
)
_CRAWL_KW       = re.compile(r"\b(crawl|spider|scrape|harvest)\b", re.IGNORECASE)
_READ_KW        = re.compile(r"\b(read|fetch|grab|pull\s+in|ingest|summari[sz]e\s+(?:this|the)\s+(?:url|page|article|link))\b", re.IGNORECASE)
_PROFILE_KW     = re.compile(r"\b(profile|build\s+a\s+profile\s+on|background\s+check|due\s+diligence\s+on)\b", re.IGNORECASE)
_SCREEN_KW      = re.compile(r"\b(screen|sanction|sanctions\s+check|compliance\s+check|run\s+(?:a\s+)?compliance|fuzzy\s+screen|fuzzy\s+match)\b", re.IGNORECASE)
_PERSON_KW      = re.compile(r"\b(person|individual|director|minister|general|colonel|owner|ceo|cfo)\b", re.IGNORECASE)
_COMPANY_KW     = re.compile(r"\b(company|corporation|firm|business|ltd|limited|inc|gmbh|sa|sarl|ltd\.|plc)\b", re.IGNORECASE)
# New tool keywords introduced with the brain/neuron upgrade
_CONFLICT_KW    = re.compile(r"\b(conflict|kinetic|escalation|violence|attacks?|battles?|insurgency|"
                              r"what(?:'s|\s+is)\s+happening\s+in|situation\s+in|security\s+(?:in|situation)|"
                              r"acled|gdelt)\b", re.IGNORECASE)
_TECH_KW        = re.compile(r"\b(what\s+is\s+(?:a|the)\s+|specs?\s+(?:of|for)\s+|tell\s+me\s+about\s+(?:the\s+)?|"
                              r"classify\s+(?:this|these)|extract\s+items|what\s+ml\s+category)\b", re.IGNORECASE)
_FUZZY_KW       = re.compile(r"\b(fuzzy|alias|alternate\s+spelling|transliteration|name\s+variant)\b", re.IGNORECASE)

# Phase 3 cherry-pick from aria_research_architecture.py 2026-04-09:
# Procurement / tender intent — fires deep_research with a procurement-
# scoped query. Conservative regex: requires both a procurement noun
# AND a market or year so we don't trigger on every "I had a contract".
_PROCUREMENT_KW = re.compile(
    r"\b(tender|RFP|RFQ|RFI|procurement|concurso|"
    r"public\s+(?:contract|notice)|contract\s+award|"
    r"bid\s+(?:submission|opportunit)|opportunit(?:y|ies)\s+for|"
    r"request\s+for\s+(?:proposal|quote|information))\b",
    re.IGNORECASE,
)

# Time-sensitive trigger — when the user uses a temporal word the answer
# is almost always going to be wrong from training data alone, so we
# should fire deep_research instead of trusting the LLM's stale memory.
# Past incidents driving this: officeholder hallucinations (Nitiwul/Ghana),
# stale conflict casualty figures, expired sanctions designations.
_TIME_SENSITIVE_KW = re.compile(
    r"\b(current(?:ly)?|latest|recent(?:ly)?|today|this\s+week|"
    r"this\s+month|right\s+now|these\s+days|as\s+of|2025|2026|2027|"
    r"in\s+the\s+last\s+\d+\s+(?:days?|weeks?|months?))\b",
    re.IGNORECASE,
)
# Known weapon systems - matched against tech_classifier's database
_WEAPON_DESIGNATION_RE = re.compile(
    r"\b(F[\-/]?\d{1,3}|Su[\-/]?\d{1,3}|MiG[\-/]?\d{1,3}|MQ[\-/]?\d|TB\d|"
    r"K9|HIMARS|Caesar|PzH\s*2000|M777|Patriot|S[\-]?[34]00|Iron\s+Dome|NASAMS|"
    r"IRIS[\-/]?T|THAAD|Javelin|Spike|NLAW|Stinger|BrahMos|Storm\s+Shadow|SCALP|"
    r"Harpoon|Exocet|NSM|Leopard\s*2|Abrams|Challenger|Bayraktar)\b",
    re.IGNORECASE,
)
# Country detection (re-uses ACLED ISO map)
_COUNTRY_RE_TOOL = re.compile(
    r"\b(angola|mozambique|guinea[\-\s]bissau|cape\s+verde|nigeria|kenya|mali|"
    r"burkina\s+faso|niger|chad|sudan|ethiopia|somalia|cameroon|senegal|drc|"
    r"ghana|south\s+africa|libya|egypt|iraq|syria|yemen|afghanistan|ukraine|russia|"
    r"saudi\s+arabia|uae|jordan|lebanon|colombia|venezuela|haiti|myanmar)\b",
    re.IGNORECASE,
)

# Officeholder question detection — auto-triggers investigate so the LLM
# has fresh web data to ground against. Without this, "who is the current
# defence minister of Ghana" goes straight to the LLM with stale training
# data and the LLM hallucinates a current officeholder. Round-4 incident
# 2026-04-08: ARIA confidently named Dominic Nitiwul as Ghana's current
# defence minister; Mahama's December 2024 election had replaced the entire
# cabinet. The LLM had no way to know — no tool ran, no fresh source.
_OFFICEHOLDER_INTENT_RE = re.compile(
    r"\b(?:who(?:'?s| is)|name\s+of|tell\s+me\s+who|current)\s+"
    r"(?:the\s+)?"
    r"(?:current\s+)?"
    r"(?:president|prime\s+minister|defen[cs]e\s+minister|"
    r"minister\s+(?:of|for)\s+defen[cs]e|"
    r"minister\s+(?:of|for)\s+foreign\s+affairs|foreign\s+minister|"
    r"interior\s+minister|finance\s+minister|"
    r"chief\s+of\s+(?:army|navy|air\s+force|defen[cs]e\s+staff)|"
    r"head\s+of\s+(?:state|government)|"
    r"ambassador\s+to|"
    r"director\s+of\s+procurement)\b",
    re.IGNORECASE,
)


# Past incident 2026-04-09 19:18 — DUMA Engineering investigation:
# the WhatsApp listener prepends `[WhatsApp group context]\n[<sender>]: ...`
# blocks containing recent message history into the chat envelope. The
# previous turns leak into intent detection: a duma-engineering.com URL
# investigation got 5 web_search angles all containing "Iraq tenders" from
# the prior turn, returned 5 Iraq RFP extracts and ZERO duma data, and
# the LLM responded with a fabricated "self-improvement plan" instead of
# an honest brief. Root cause: entity extraction took the first 200 chars
# of the message blob (which started with `[WhatsApp group context]`),
# not the actual current question.
#
# This regex strips the listener-side context blocks so that intent
# detection only sees the user's current message text. Strips:
#   - leading "[WhatsApp group context]\n[Sender]: ...\n[Sender]: ...\n"
#   - "[Question from <sender>]\n" markers
# Returns the trailing message body. Idempotent: messages without these
# markers pass through unchanged.
_LISTENER_CONTEXT_PREFIX_RE = re.compile(
    r"^\s*\[WhatsApp group context\][\s\S]*?\[Question from [^\]]+\]\s*",
    re.IGNORECASE,
)
_LISTENER_QUESTION_MARKER_RE = re.compile(
    r"\[Question from [^\]]+\]\s*",
    re.IGNORECASE,
)


def _strip_listener_context(message: str) -> str:
    """Strip the WhatsApp listener context-block prefix from a chat message.

    The listener prepends recent conversation history as a `[WhatsApp group
    context]` block followed by `[Question from <sender>]\\n<actual message>`.
    For intent detection we ONLY want the actual current message — the
    history is already persisted server-side via session_id-based history
    storage and does not need to be re-injected through the message body.
    """
    if not message:
        return message
    # Fast path: no context block present
    if "[WhatsApp group context]" not in message and "[Question from" not in message:
        return message
    stripped = _LISTENER_CONTEXT_PREFIX_RE.sub("", message, count=1)
    if stripped == message:
        # Prefix didn't match (maybe partial pattern) — just strip the
        # `[Question from <sender>]` marker if it appears anywhere
        stripped = _LISTENER_QUESTION_MARKER_RE.sub("", message)
    return stripped.strip()


def _detect_tool_intent(message: str) -> dict | None:
    """Parse free-form text into a structured tool call. Returns None if no tool intent."""
    # Strip the WhatsApp listener context prefix BEFORE any pattern matching.
    # This prevents prior-turn topics (e.g. "Iraq tenders") from contaminating
    # the entity extraction for the current turn (e.g. "duma-engineering.com").
    # Past incident 2026-04-09 19:18 — DUMA Engineering investigation.
    msg = _strip_listener_context(message).strip()
    if not msg:
        return None

    # Find URLs / domains
    url_match = _URL_RE.search(msg)
    url = url_match.group(0) if url_match else None
    if not url:
        dom = _DOMAIN_RE.search(msg)
        if dom:
            url = "https://" + dom.group(0).lstrip("/")

    has_investigate = bool(_INVESTIGATE_KW.search(msg))
    has_crawl       = bool(_CRAWL_KW.search(msg))
    has_read        = bool(_READ_KW.search(msg))
    has_profile     = bool(_PROFILE_KW.search(msg))
    has_screen      = bool(_SCREEN_KW.search(msg))
    has_conflict    = bool(_CONFLICT_KW.search(msg))
    has_fuzzy       = bool(_FUZZY_KW.search(msg))
    weapon_match    = _WEAPON_DESIGNATION_RE.search(msg)
    country_match   = _COUNTRY_RE_TOOL.search(msg)

    # ── Officeholder questions auto-trigger investigate ──
    # "Who is the current defence minister of Ghana" → fire investigate
    # immediately so the LLM has fresh web data. Without this, the LLM
    # would hallucinate from stale training data (round-4 Nitiwul incident).
    #
    # Round 6: pass depth='quick' for the officeholder auto-trigger.
    # The full thorough path runs 8 search queries × 3 articles = 24
    # sequential LLM calls and routinely exceeds the 240s budget. Quick
    # mode does 3 × 2 = 6 article fetches, well within budget. The full
    # depth is still available via explicit /investigate <name>.
    officeholder_match = _OFFICEHOLDER_INTENT_RE.search(msg)
    if officeholder_match and country_match:
        # Build the investigation topic from the matched fragment + country
        topic = f"current {officeholder_match.group(0)} {country_match.group(0)}".strip()
        return {
            "tool": "investigate",
            "topic": topic[:200],
            "depth": "quick",  # round 6 — fast path for officeholder lookups
            "context": msg,
            "_reason": "officeholder_question",
        }

    # ── 0. Multi-entity batch detection — spawn a background research task ──
    # Triggered by patterns like "research each of these suppliers", "compare
    # these companies", "investigate all of them", "find out about each one".
    # These cannot run inline (would take 5+ minutes and timeout the chat).
    # Instead we extract the entity list and spawn a research_each task.
    _BATCH_RE = re.compile(
        r"\b(?:research|investigate|compare|analy[sz]e|profile|look\s+into|"
        r"find\s+out\s+about|background\s+check)\b.*?\b(?:each|all|these|"
        r"every|both)\b",
        re.IGNORECASE,
    )
    if _BATCH_RE.search(msg) and len(msg) < 1500:
        # Try to pull entity names out of the message — bullets, numbers,
        # comma-separated, or capitalised noun phrases
        entity_candidates: list[str] = []
        # 1. Bulleted / numbered lists
        for line in msg.splitlines():
            line = line.strip()
            m = re.match(r"^[\-\*\u2022\d\.\)\(]+\s*(.+)$", line)
            if m and 3 <= len(m.group(1)) <= 100:
                entity_candidates.append(m.group(1).strip().rstrip(".,;:"))
        # 2. Comma-separated names if no list found
        if not entity_candidates:
            colon_idx = msg.find(":")
            if colon_idx > 0:
                tail = msg[colon_idx + 1:]
                parts = [p.strip().rstrip(".,;:") for p in re.split(r"[,;\n]", tail)]
                entity_candidates = [p for p in parts if 3 <= len(p) <= 100]
        # Cap to a sensible batch size
        entity_candidates = entity_candidates[:10]
        if len(entity_candidates) >= 2:
            return {
                "tool": "spawn_research_task",
                "task_type": "research_each",
                "entities": entity_candidates,
                "context": msg,
            }
        # If we couldn't extract entities but the prompt clearly asks for
        # multi-step research on something specific, still spawn an
        # investigate task in the background instead of running inline
        if has_investigate or has_crawl:
            topic_match = re.search(r"\b(?:research|investigate|crawl|look\s+into|find\s+out\s+about|profile)\s+(?:each\s+(?:of\s+)?(?:these|the)\s+)?(.+?)(?:\?|\.|$)", msg, re.IGNORECASE)
            topic = (topic_match.group(1) if topic_match else msg)[:300].strip(" .,:;-?!\"'")
            if topic and len(topic) >= 3:
                return {
                    "tool": "spawn_research_task",
                    "task_type": "investigate" if has_investigate else "crawl",
                    "params": {"topic": topic, "url": url} if not has_crawl else {"url": url},
                    "context": msg,
                }

    # 1. Explicit crawl request — needs a URL. Only fires when the user
    # explicitly asks to crawl/spider a site (full multi-page LLM analysis,
    # 75-150s). For inline chat use this is too slow; users should type
    # "crawl <url>" specifically when they want it.
    if has_crawl and url:
        return {"tool": "crawl", "url": url, "context": msg}

    # 2. Investigate / research / look-into a URL → DEEP RESEARCH the entity
    # AND the URL together. Phase 2 evolution (2026-04-09 evening):
    # extract_url alone (even multi-page) only sees what the company
    # publishes on its own website. For real DD, ARIA needs the OFF-site
    # OSINT surface too — registry filings, news articles, LinkedIn,
    # think tank coverage. deep_research orchestrates 5 parallel web
    # searches (different angles) + extracts the top 5 URLs in parallel
    # + extract_url_deep on the user-provided URL — one tool call,
    # everything aggregated.
    #
    # Past incident 2026-04-09 evening: ARIA called Modirum Gespi a
    # "Portuguese OEM" because it could only see the homepage marketing
    # copy (which had hreflang="pt-pt") and inferred nationality from
    # the language variant. The actual jurisdiction (Finnish HQ +
    # Brazilian defence operations) was nowhere on the company website
    # but is presumably documented in news articles, registry filings,
    # and LinkedIn profiles — i.e. exactly the kind of OFF-site sources
    # web search surfaces.
    if has_investigate and url:
        # Try to derive a clean entity name for the search query
        ctx = msg
        ctx_no_url = re.sub(r"https?://\S+", "", ctx).strip()
        ctx_clean = re.sub(
            r"^\s*(aria[,\s]*|please\s+|kindly\s+|can\s+you\s+)*",
            "", ctx_no_url, flags=re.IGNORECASE,
        )
        ctx_clean = re.sub(
            r"\b("
            r"investigate|investigation|investigations|investigating|"
            r"research|researching|researches|"
            r"crawl|check|screen|"
            r"look\s+into|looking\s+into|"
            r"dig\s+into|digging\s+into|"
            r"find\s+out\s+about|"
            r"tell\s+me\s+about|tell\s+me\s+everything\s+about|"
            r"profile|profiling|"
            r"due\s+diligence|background\s+check|"
            r"explore|exploring"
            r")\b",
            "", ctx_clean, flags=re.IGNORECASE,
        )
        # Strip common framing prefixes — accept "this/that/the" before
        # company/firm/etc, plus the trailing "and (it is|its) people/
        # directors/team" framing. Past incident 2026-04-09 19:18 — DUMA
        # Engineering: the regex previously only accepted "the" before
        # the noun, so "this company and it is people" (referring to
        # the URL) was NOT stripped, the entity ended up as the literal
        # string "this company and it is people", and Brave Search
        # returned People Magazine articles instead of duma data.
        ctx_clean = re.sub(
            r"^\s*(this|that|the|a|an)?\s*(company|companies|firm|broker|entity|organisation|organization|business|corporation|corp|ltd|limited|gmbh)\s*(and\s+(it\s+is|its|they\s+are|their)\s+(people|directors|team|leadership|owners|founders|management|staff|employees))?\s*[:\-]?\s*",
            "", ctx_clean, flags=re.IGNORECASE,
        )
        entity = ctx_clean.strip(" ,.:;-?!\"'\n")[:200]

        # Past incident 2026-04-09 19:18 — DUMA Engineering: detect generic
        # placeholder phrases that survived the regex strip and fall back
        # to the URL hostname. This is the safety net when the user phrases
        # the request as "investigate this/it/them <url>" — the placeholder
        # is meaningless to a search engine and the URL is the real entity.
        _GENERIC_PLACEHOLDERS = {
            "", "this", "that", "it", "them", "they",
            "this one", "that one", "this company", "that company",
            "the company", "this firm", "the firm", "this entity",
            "the entity", "this people", "the people", "people",
            "this business", "the business",
        }
        if entity.lower().strip() in _GENERIC_PLACEHOLDERS or len(entity.strip()) < 3:
            try:
                from urllib.parse import urlparse as _up
                host = _up(url).netloc.lower().replace("www.", "")
                # Use the second-level domain as the entity name
                # (e.g. duma-engineering.com → duma-engineering)
                entity = host.split(".")[0] if host else url
                # Replace hyphens with spaces for nicer search queries
                entity = entity.replace("-", " ").replace("_", " ").strip()
            except Exception:
                entity = url

        if not entity:
            # Final fallback to the raw domain
            try:
                from urllib.parse import urlparse as _up
                host = _up(url).netloc.lower().replace("www.", "")
                entity = host.split(".")[0] if host else url
            except Exception:
                entity = url
        return {"tool": "deep_research", "entity": entity, "url": url, "context": msg}

    # 3. Read / summarise URL — bare URL with no verb, or explicit "read this"
    if (has_read and url) or (url and not (has_investigate or has_crawl or has_profile)):
        return {"tool": "read", "url": url, "context": msg}

    # 4. Investigate topic (no URL) — fire deep_research for thorough OSINT.
    # Phase 2 evolution: this used to route to web_search (snippet-only)
    # and even earlier to the slow deep_researcher.investigate() (which
    # took >2 min and timed out). deep_research is the right primitive
    # — multi-query web search + parallel extract of top results in one
    # tool call, ~10-25s wall time.
    if has_investigate:
        verb_match = _INVESTIGATE_KW.search(msg)
        topic = msg[verb_match.end():].strip(" .,:;-?!\"'")
        # Strip leading determiners and prepositions including "this/that"
        topic = re.sub(
            r"^(this|that|the|a|an|on|about|into|of|for|regarding|re)\s+",
            "", topic, flags=re.IGNORECASE,
        )
        # Strip common framing noise like "(this/the) company and it is people:"
        # 2026-04-09 19:38: expanded determiner to accept this/that/a/an in
        # addition to the/optional, same fix as route 2 for the DUMA-pattern.
        topic = re.sub(
            r"^(?:this|that|the|a|an)?\s*(company|companies|firm|broker|entity|organisation|organization|person|individual|business|corporation|corp|ltd|limited|gmbh)\s*(?:and\s+(?:it\s+is|its|they\s+are|their)\s+(?:people|directors|team|leadership|owners|founders|management|staff|employees)\s*)?[:\-]?\s*",
            "", topic, flags=re.IGNORECASE,
        )
        topic = topic.strip(" .,:;-?!\"'\n")
        if topic and len(topic) >= 3:
            return {"tool": "deep_research", "entity": topic[:200], "context": msg}

    # 5. Profile / background check on entity
    if has_profile:
        verb_match = _PROFILE_KW.search(msg)
        entity = msg[verb_match.end():].strip(" .,:;-?!\"'")
        entity = re.sub(r"^(on|of|the|a|an|this|that)\s+", "", entity, flags=re.IGNORECASE)
        if entity and len(entity) >= 2:
            ptype = "person" if _PERSON_KW.search(msg) else "company" if _COMPANY_KW.search(msg) else "auto"
            return {"tool": "profile", "entity": entity[:200], "ptype": ptype, "context": msg}

    # 6. Compliance / fuzzy sanctions screen
    if has_screen or has_fuzzy:
        verb_match = (_FUZZY_KW.search(msg) if has_fuzzy else _SCREEN_KW.search(msg))
        entity = msg[verb_match.end():].strip(" .,:;-?!\"'")
        entity = re.sub(r"^(on|of|the|a|an|this|that|for)\s+", "", entity, flags=re.IGNORECASE)
        if entity and len(entity) >= 2:
            tool_name = "fuzzy_sanctions" if has_fuzzy else "screen"
            return {"tool": tool_name, "entity": entity[:200], "context": msg}

    # 7. Conflict / kinetic event lookup — needs a country
    if has_conflict and country_match:
        return {"tool": "conflict", "country": country_match.group(0), "context": msg}

    # 8. Weapon system / technical explainer — designation present
    if weapon_match:
        return {"tool": "tech_explain", "designation": weapon_match.group(0), "context": msg}

    # ── Phase 3 cherry-pick triggers (added 2026-04-09 from architecture
    # proposal). Each one is conservative — requires the corresponding
    # keyword PLUS a discriminator (market/country/entity) so we don't
    # fire deep_research on every chat with a stray temporal word.

    # 9. Procurement / tender / RFQ — fires deep_research scoped to the
    # procurement question. Requires the procurement keyword AND either a
    # country OR a year-like time word so a generic "what's an RFP" stays
    # in pure-LLM mode.
    proc_match = _PROCUREMENT_KW.search(msg)
    if proc_match and (country_match or _TIME_SENSITIVE_KW.search(msg)):
        # Build a deep_research entity that captures the procurement subject
        ctx_clean = re.sub(
            r"^\s*(aria[,\s]*|please\s+|kindly\s+|can\s+you\s+)*",
            "", msg, flags=re.IGNORECASE,
        )[:200]
        return {
            "tool": "deep_research",
            "entity": ctx_clean.strip(" .,:;-?!\"'\n"),
            "context": msg,
            "_reason": "procurement_query",
        }

    # 10. Time-sensitive question with a country — fires deep_research so
    # the LLM has fresh OSINT instead of stale training data. Conservative:
    # requires both the time word AND the country, so a chit-chat
    # "what's the latest with you" doesn't trigger. Past incidents driving
    # this: officeholder hallucinations on Ghana / Modirum, stale figures.
    if _TIME_SENSITIVE_KW.search(msg) and country_match and len(msg) > 25:
        # Avoid colliding with officeholder route (already handled at top)
        if not officeholder_match:
            ctx_clean = re.sub(
                r"^\s*(aria[,\s]*|please\s+|kindly\s+|can\s+you\s+)*",
                "", msg, flags=re.IGNORECASE,
            )[:200]
            return {
                "tool": "deep_research",
                "entity": ctx_clean.strip(" .,:;-?!\"'\n"),
                "context": msg,
                "_reason": "time_sensitive_country_query",
            }

    return None


# Domains that block scrapers as a matter of policy. When the user asks ARIA
# to investigate one of these, an empty tool result is EXPECTED — not a sign
# the user is wrong or that ARIA should "try harder by guessing". Used by the
# empty-result framing below to give the LLM accurate context.
_KNOWN_BLOCKING_DOMAINS = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "youtube.com", "reddit.com", "medium.com",
)


def _is_blocking_domain(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(d in u for d in _KNOWN_BLOCKING_DOMAINS)


def _no_data_warning(tool_name: str, target: str, *, blocking: bool = False) -> str:
    """Return an explicit DO-NOT-EXTRAPOLATE warning to be appended to the
    tool result block when the tool returned no usable data.

    Why this exists
    ───────────────
    Past incident: a /investigate on a LinkedIn URL returned 0 facts because
    LinkedIn blocks scrapers. ARIA then invented a 2000-word "MANUAL OSINT
    PROTOCOL" reply that fabricated the person's family lineage, employer,
    and commercial relevance. The CONSTITUTION clause 9 forbids this — but
    the LLM needs an explicit, in-prompt reminder when staring at an empty
    tool result, otherwise INTELLECTUAL COURAGE wins and it confabulates.
    """
    blocking_note = ""
    if blocking:
        blocking_note = (
            f"\n\nNOTE: The target URL is on a domain that blocks crawlers as a matter of policy "
            f"({target}). An empty result is EXPECTED. Do NOT 'try harder by guessing'. The user "
            f"already knows scraping it doesn't work — they expect you to ask them for context."
        )
    return (
        f"\n\n⛔ NO USABLE DATA RETURNED — CONSTITUTION CLAUSE 9 ENFORCEMENT ⛔\n"
        f"The {tool_name} tool ran but returned no usable facts about: {target!r}\n"
        f"\n"
        f"YOU MUST reply approximately as follows (rephrase naturally, but keep the meaning):\n"
        f'  "I could not access {target!r}. I have no information about this entity beyond '
        f'what you have just shared with me. To build a useful profile, please tell me: '
        f'(1) who they work for, (2) their role or function, (3) any context about why '
        f"you're asking — a deal, a meeting, a referral. With that I can run targeted research.\"\n"
        f"\n"
        f"YOU MUST NOT:\n"
        f"  - Invent a profile from the URL slug, username, or name pattern\n"
        f"  - Guess at family lineage from suffixes like 'IV', 'Jr', 'III'\n"
        f"  - Fabricate employer, role, network, sanctions exposure, or commercial relevance\n"
        f"  - Connect the entity to current events (conflicts, deals, escalations) without evidence\n"
        f"  - Produce a 'MANUAL OSINT' or 'PRELIMINARY ASSESSMENT' that is actually fiction\n"
        f"\n"
        f"The user can correct ARIA if you say 'I don't know'. They cannot un-see a fabricated profile."
        f"{blocking_note}"
    )


async def _execute_tool(intent: dict, llm) -> str:
    """Run the detected tool and return a compact context string for the LLM."""
    tool = intent.get("tool")
    try:
        # ── Background research task — spawn instead of running inline ──
        if tool == "spawn_research_task":
            task_type = intent.get("task_type", "investigate")
            entities = intent.get("entities") or []
            params = intent.get("params") or {}
            if task_type == "research_each" and entities:
                params = {"entities": entities, "context": intent.get("context", "")}
                title = f"Research each: {', '.join(entities[:3])}"
                eta = max(60, 60 * len(entities))
            else:
                title = f"{task_type}: {(params.get('topic') or params.get('url') or '')[:60]}"
                eta = 120 if task_type == "investigate" else 90

            spawn = await research_tasks.spawn_research_task(
                task_type=task_type,
                params=params,
                title=title,
                requested_by="chat_nlu",
                llm=llm,
            )
            if spawn.get("status") == "rejected":
                return (
                    f"\n\n[TOOL: spawn_research_task — REJECTED]\n"
                    f"Reason: {spawn.get('reason')}\n"
                    f"Retry after: {spawn.get('retry_after_s', 60)}s"
                )
            return (
                f"\n\n[TOOL: spawn_research_task]\n"
                f"Task ID: {spawn['id']}\n"
                f"Type: {task_type}\n"
                f"Title: {title}\n"
                f"ETA: ~{eta}s\n"
                f"Status: queued — running in background\n"
                f"Entities: {entities if entities else 'N/A'}\n"
                f"\n"
                f"IMPORTANT: This task is now RUNNING IN THE BACKGROUND.\n"
                f"Tell the user: 'I have spawned background task {spawn['id']} "
                f"for this research. ETA ~{eta // 60} minutes. The result will "
                f"be pushed to the group automatically when complete. You can "
                f"check status anytime with /task {spawn['id']}.'\n"
                f"Do NOT try to give the answer yourself — it does not exist yet."
            )

        if tool == "deep_research":
            # THE THOROUGH RESEARCH PATH (Phase 2, 2026-04-09 evening).
            # Orchestrates multiple parallel web searches across different
            # angles (entity / company / headquarters / directors / news),
            # deduplicates + ranks results by source-tier authority, then
            # extracts verbatim content from the top 5 URLs in parallel.
            # Optionally also runs extract_url_deep on a primary_url if
            # the user provided one.
            #
            # This is the "go through every single bit of information"
            # primitive Antonio asked for. NO LLM call, NO RAG ingest —
            # pure HTTP fetching + structured extraction. Returns the
            # entire research dossier verbatim to the LLM in one tool
            # block.
            entity = (intent.get("entity") or intent.get("query") or "").strip()
            primary_url = (intent.get("url") or "").strip()
            r = await deep_research(
                entity, primary_url=primary_url, max_queries=5, max_extracts=5,
            )
            if not r.get("ok"):
                return (
                    f"\n\n[TOOL: deep_research — FAILED]\n"
                    f"Entity: {entity}\n"
                    f"Error: {r.get('error', 'unknown')}\n"
                    f"Duration: {r.get('duration_ms', 0)}ms\n"
                    + _no_data_warning("deep_research", entity, blocking=False)
                )
            # Format the snippets section
            snippets = r.get("snippets_top") or []
            if snippets:
                snippets_block = "\n".join(
                    f"  [{i+1}] [tier={s['tier_score']} angles={len(s['angles'])}] {s['title']}\n"
                    f"      URL: {s['url']}\n"
                    f"      Snippet: {s['snippet']}"
                    for i, s in enumerate(snippets[:12])
                )
            else:
                snippets_block = "  (no snippets returned across any angle)"

            # Format the extracted pages section
            extracted = r.get("extracted_pages") or []
            if extracted:
                extracted_block = "\n\n".join(
                    f"--- EXTRACT {i+1}: {p['url']} {'(deep multi-page)' if p.get('is_deep') else '(single page)'} ---\n"
                    f"Title: {p.get('title','')}\n"
                    f"Description: {p.get('description','')}\n"
                    f"Social: {', '.join((p.get('social') or [])[:5]) or '(none)'}\n"
                    f"Emails: {', '.join((p.get('emails') or [])[:3]) or '(none)'}\n"
                    f"Text:\n{p.get('text','')}"
                    for i, p in enumerate(extracted[:6])
                )
            else:
                extracted_block = "(no pages successfully extracted)"

            queries_run = ", ".join(repr(q) for q in (r.get("queries_run") or []))
            return (
                f"\n\n[TOOL: deep_research — multi-query OSINT + verbatim extracts]\n"
                f"Entity: {entity}\n"
                f"Primary URL: {primary_url or '(none)'}\n"
                f"Queries run: {queries_run}\n"
                f"Snippets per provider: {r.get('snippet_count_per_provider', {})}\n"
                f"Snippets per angle: {r.get('snippet_count_per_angle', {})}\n"
                f"Total unique URLs surfaced: {r.get('snippets_total', 0)}\n"
                f"Pages extracted verbatim: {r.get('extracted_count', 0)}\n"
                f"Duration: {r.get('duration_ms', 0)}ms\n"
                f"\n--- TOP-RANKED SEARCH SNIPPETS (sorted by source-tier score + cross-angle bonus) ---\n"
                f"{snippets_block}\n"
                f"--- End snippets ---\n"
                f"\n--- VERBATIM EXTRACTS FROM TOP URLS ---\n"
                f"{extracted_block}\n"
                f"--- End extracts ---\n"
                f"\nIMPORTANT — clauses 9, 13, 14 + researcher principles:\n"
                f"  (a) You now have BOTH snippet-level OSINT pointers AND verbatim "
                f"text from the highest-ranked URLs. Cite the source URL inline "
                f"for every fact. The format is '[from <url>]' or '[snippet #N]'.\n"
                f"  (b) Apply the source-tier hierarchy: registry / official "
                f"records (tier 1, score 95-100) override think tanks (tier 2, "
                f"score 80) which override defence trade press (tier 3, score "
                f"65) which override quality journalism (tier 4, score 50) "
                f"which override generic web / Wikipedia (tier 5, score "
                f"10-45). Tag each fact with its tier source.\n"
                f"  (c) Apply the triangulation rule: single-source = "
                f"`[ASSESSED — single source]`, two independent sources = "
                f"`[PROBABLE]`, three+ independent sources = `[CONFIRMED]`.\n"
                f"  (d) Do NOT invent any verifiable fact (company number, "
                f"address, director name, jurisdiction, NACE code, financial "
                f"figure) that is not present in the materials above. If "
                f"absent, say so explicitly under GAPS.\n"
                f"  (e) Do NOT infer nationality / jurisdiction from URL TLDs "
                f"or hreflang language variants — only from explicit text in "
                f"the snippets or extracts.\n"
                f"  (f) Required output sections: BOTTOM LINE, ENTITY IDENTITY, "
                f"BENEFICIAL OWNERSHIP, GHOST DETECTION CHECKLIST (if "
                f"counterparty DD), SANCTIONS SCREEN, DIGITAL FOOTPRINT, "
                f"GAPS, RECOMMENDED ACTION, NEXT STEP."
            )

        if tool == "web_search":
            # Standalone web search for entity-only investigations (no URL).
            # Past incident 2026-04-09: ARIA could only investigate
            # entities for which the user provided a URL — there was no
            # way for her to discover related URLs (registry filings,
            # LinkedIn, news articles) on her own. This tool gives her
            # snippet-level OSINT discovery via Brave Search API
            # (preferred) or DuckDuckGo HTML scraping (free fallback).
            query = intent.get("query") or intent.get("topic") or ""
            r = await web_search(query, max_results=8)
            if not r.get("ok"):
                return (
                    f"\n\n[TOOL: web_search — FAILED]\n"
                    f"Query: {query}\n"
                    f"Provider: {r.get('provider', 'none')}\n"
                    f"Error: {r.get('error', 'unknown')}\n"
                    f"Duration: {r.get('duration_ms', 0)}ms\n"
                    + _no_data_warning(
                        "web_search", query,
                        blocking=False,
                    )
                )
            results = r.get("results") or []
            if not results:
                return (
                    f"\n\n[TOOL: web_search — NO RESULTS]\n"
                    f"Query: {query}\n"
                    f"Provider: {r.get('provider')}\n"
                    f"Duration: {r.get('duration_ms', 0)}ms\n"
                    f"\nThe search returned zero results. Treat this as "
                    f"INSUFFICIENT DATA per clause 9 — do not extrapolate "
                    f"or invent information about the entity."
                )
            results_block = "\n".join(
                f"  [{i+1}] {r['title']}\n      URL: {r['url']}\n      Snippet: {r['snippet']}"
                for i, r in enumerate(results)
            )
            return (
                f"\n\n[TOOL: web_search — verbatim search results below]\n"
                f"Query: {query}\n"
                f"Provider: {r.get('provider')}\n"
                f"Results: {len(results)}\n"
                f"Duration: {r.get('duration_ms', 0)}ms\n"
                f"\n--- Search results (verbatim, in rank order) ---\n"
                f"{results_block}\n"
                f"--- End search results ---\n"
                f"\nIMPORTANT — clauses 9, 13, 14: these snippets are "
                f"EXCERPTS from third-party pages. They are POINTERS to "
                f"sources, not verified facts. To produce a confident "
                f"finding about the entity, you must (a) cite the source "
                f"URL inline, (b) tag the claim with at most "
                f"[ASSESSED — single search snippet] unless multiple "
                f"snippets corroborate, and (c) recommend a follow-up "
                f"extract_url call on the most relevant result for "
                f"verbatim verification. Do NOT fabricate facts that go "
                f"beyond the snippets."
            )

        if tool == "extract_url":
            # Multi-page DD extraction (Phase 2 fix, 2026-04-09 evening).
            # Fetches the URL plus 4 high-value internal links (about / team /
            # contact / leadership / products / locations / etc.) and
            # aggregates the content into ONE result for the LLM.
            #
            # Past incident sequence:
            #   1. Original chat-path crawl was crawl_website(15 pages, 15
            #      LLM calls per page) — 90+ seconds, frequently timed out.
            #   2. Replaced with extract_url_text() (single page, no LLM)
            #      — fast but only saw the homepage.
            #   3. The single-page extraction gave the LLM only marketing
            #      copy + meta tags. ARIA confidently misclassified
            #      Modirum Gespi as "Portuguese OEM" because the homepage
            #      had hreflang="pt-pt" but no jurisdiction info — the
            #      actual Finnish HQ + Brazilian defence operations live
            #      on /about / /contact / /locations pages that the
            #      single-page extractor never touched.
            #
            # extract_url_deep follows 3-5 high-value internal links in
            # parallel and aggregates the result. Same defensive pattern
            # (no LLM call, no RAG ingest, returns verbatim text), just
            # broader coverage.
            # Run extract_url_deep AND a parallel web_search on the entity
            # name, so the LLM has BOTH the verbatim site content AND the
            # broader OSINT surface (registry filings, news, LinkedIn, etc.).
            # The web search gives the LLM a way to discover information
            # the company doesn't publish on its own website — past
            # incident 2026-04-09 evening: Modirum Gespi's Finnish HQ +
            # Brazilian operations were not on their own homepage at all,
            # so single-domain extraction missed the actual jurisdiction.
            from urllib.parse import urlparse as _urlparse
            entity_hint = (intent.get("context") or "").strip()
            # Try to extract a clean entity name for the search query
            search_query = ""
            if entity_hint:
                # Strip the URL out of the context
                search_query = re.sub(r"https?://\S+", "", entity_hint).strip()
                # Strip ARIA mention prefixes / common verbs
                search_query = re.sub(
                    r"^\s*(aria[,\s]*|please\s+|kindly\s+|can\s+you\s+)*",
                    "", search_query, flags=re.IGNORECASE,
                )
                search_query = re.sub(
                    r"\b(investigate|research|crawl|check|screen|look\s+into|find\s+out\s+about|tell\s+me\s+about|profile)\b",
                    "", search_query, flags=re.IGNORECASE,
                )
                search_query = re.sub(
                    r"^\s*(the\s+)?(company|firm|broker|entity|organisation|organization)\s+(and\s+(it\s+is|its)\s+(people|directors|team))?\s*[:\-]?\s*",
                    "", search_query, flags=re.IGNORECASE,
                )
                search_query = search_query.strip(" ,.:;-?!\"'\n")[:200]
            if not search_query:
                # Fallback: derive from the domain
                try:
                    host = _urlparse(intent["url"]).netloc.lower()
                    search_query = host.replace("www.", "").split(".")[0]
                except Exception:
                    search_query = intent["url"]

            extract_task = extract_url_deep(intent["url"], max_pages=5)
            search_task = web_search(search_query, max_results=8)
            r, search_r = await asyncio.gather(
                extract_task, search_task, return_exceptions=True,
            )
            # Defensive: handle either task throwing
            if isinstance(r, Exception):
                logger.warning("extract_url_deep failed: %s", r)
                r = {"extraction_ok": False, "error": str(r)[:200], "url": intent["url"]}
            if isinstance(search_r, Exception):
                logger.warning("web_search parallel call failed: %s", search_r)
                search_r = {"ok": False, "results": [], "error": str(search_r)[:200], "provider": "none"}
            if not r.get("extraction_ok"):
                return (
                    f"\n\n[TOOL: extract_url — FETCH/EXTRACTION FAILED]\n"
                    f"URL: {intent['url']}\n"
                    f"Error: {r.get('error', 'unknown')}\n"
                    f"Duration: {r.get('duration_ms', 0)}ms\n"
                    + _no_data_warning(
                        "extract_url",
                        intent["url"],
                        blocking=_is_blocking_domain(intent["url"]),
                    )
                )
            # Successful extraction — surface the verbatim content. The
            # chat LLM will read this and quote from it under clauses 9
            # (no profiling without data) and 14 (no fabricated verifiable
            # facts). Cap to keep the prompt budget sane.
            pages_fetched = r.get("pages_fetched") or [intent["url"]]
            pages_count = r.get("pages_count") or len(pages_fetched)
            # Build the parallel web search results section
            search_results = (search_r or {}).get("results") or []
            if search_results:
                search_block = "\n".join(
                    f"  [{i+1}] {sr.get('title','(no title)')}\n      URL: {sr.get('url','')}\n      Snippet: {sr.get('snippet','')}"
                    for i, sr in enumerate(search_results)
                )
                search_section = (
                    f"\n--- Parallel web search results (provider: {search_r.get('provider','none')}, "
                    f"query: {search_query!r}) ---\n"
                    f"{search_block}\n"
                    f"--- End web search results ---\n"
                )
            elif search_r and search_r.get("ok") is False:
                search_section = (
                    f"\n--- Parallel web search FAILED ({search_r.get('provider','none')}: "
                    f"{search_r.get('error','unknown')}) — fall back on extracted text only ---\n"
                )
            else:
                search_section = "\n--- Parallel web search returned 0 results ---\n"

            return (
                f"\n\n[TOOL: extract_url_deep + web_search — verbatim content + OSINT pointers below]\n"
                f"Root URL: {intent['url']}\n"
                f"Pages fetched ({pages_count}):\n"
                + "\n".join(f"  - {p}" for p in pages_fetched) + "\n"
                f"Extracted in: {r.get('duration_ms', 0)}ms\n"
                f"Title: {r.get('title','')}\n"
                f"Description: {r.get('description','')}\n"
                f"Social profiles: {', '.join(r.get('social', [])[:8]) or '(none across fetched pages)'}\n"
                f"Emails: {', '.join(r.get('emails', [])[:5]) or '(none across fetched pages)'}\n"
                f"Phones: {', '.join(r.get('phones', [])[:5]) or '(none across fetched pages)'}\n"
                f"\n--- Full extracted text (verbatim from the fetched pages, in order) ---\n"
                f"{(r.get('text','') or '')[:12000]}\n"
                f"--- End extracted text ---\n"
                f"{search_section}"
                f"\nIMPORTANT — clauses 9, 13, 14:\n"
                f"  (a) You may ONLY state facts about this entity that are "
                f"verifiably present in EITHER the extracted page text OR a "
                f"specific web search snippet above. Cite the source inline "
                f"for every fact: '[from <url>]' or '[from search snippet #N]'.\n"
                f"  (b) Do NOT invent company numbers, NACE codes, registered "
                f"addresses, executive names, jurisdictions, or any other "
                f"verifiable identifiers. If a fact is not in the materials "
                f"above, say so explicitly: 'I cannot verify <fact> from the "
                f"available data.'\n"
                f"  (c) DO NOT infer nationality from language variants in "
                f"the URL or HTML (e.g. /en, hreflang='pt-pt') — language "
                f"variants reflect target audience, not company origin. State "
                f"the actual jurisdiction ONLY if it appears in the extracted "
                f"text or a search snippet. If multiple jurisdictions appear "
                f"(HQ in one country, subsidiaries in another), report each "
                f"one with its specific source.\n"
                f"  (d) Web search snippets are POINTERS, not verified facts. "
                f"Tag findings derived from a single snippet at most as "
                f"[ASSESSED — single search snippet]. Tag findings present "
                f"in BOTH the extracted text AND a snippet as [PROBABLE]. "
                f"Tag findings only present in 3+ independent snippets as "
                f"[CONFIRMED]."
            )

        if tool == "crawl":
            max_pages = intent.get("max_pages", 15)
            r = await crawl_website(llm, intent["url"], max_pages=max_pages, context=intent.get("context", ""))
            facts = r.get("facts_learned") or r.get("facts") or 0
            pages = r.get("pages_crawled") or r.get("pages") or 0
            facts_list = r.get("facts") or []
            # Surface the actual extracted facts to the LLM, not just a count
            facts_preview = "\n".join(
                f"  - [{f.get('confidence', '?')}] {f.get('topic', '?')}: {(f.get('content') or '')[:200]}"
                for f in facts_list[:15] if isinstance(f, dict)
            ) or "  (no facts extracted)"
            base = (
                f"\n\n[TOOL: crawl_website]\n"
                f"URL: {intent['url']}\n"
                f"Pages crawled: {pages}\n"
                f"Facts learned: {facts}\n"
                f"Top extracted facts:\n{facts_preview}"
            )
            # Append the no-data warning when the crawl produced nothing.
            # This is what stops the LLM from inventing a "manual OSINT
            # protocol" reply when given an empty tool result.
            if pages == 0 or facts == 0 or not facts_list:
                base += _no_data_warning(
                    "crawl_website",
                    intent["url"],
                    blocking=_is_blocking_domain(intent["url"]),
                )
            return base

        if tool == "read":
            r = await read_article(llm, intent["url"], intent.get("context", ""))
            facts = r.get("facts_learned", 0) or 0
            summary = (r.get("summary") or r.get("analysis") or "")[:1500]
            base = (
                f"\n\n[TOOL: read_article]\nURL: {intent['url']}\n"
                f"Facts learned: {facts}\nSummary: {summary}"
            )
            if facts == 0 and not summary.strip():
                base += _no_data_warning(
                    "read_article",
                    intent["url"],
                    blocking=_is_blocking_domain(intent["url"]),
                )
            return base

        if tool in ("investigate", "investigate_url"):
            topic = intent.get("topic") or intent.get("url", "")
            # Round 6: honour explicit depth override from intent (e.g.
            # officeholder questions auto-trigger with depth='quick' for speed).
            # Default stays 'thorough' for explicit /investigate calls.
            depth = intent.get("depth", "thorough")
            r = await investigate(llm, topic, depth=depth)
            synth = r.get("synthesis") or {}
            findings = synth.get("key_findings") or []
            actions = synth.get("recommended_actions") or []
            articles_read = r.get("articles_read", 0) or 0
            facts_learned = r.get("facts_learned", 0) or 0
            base = (
                f"\n\n[TOOL: investigate]\nTopic: {topic}\n"
                f"Articles read: {articles_read} | Facts: {facts_learned}\n"
                f"Key findings: {json.dumps(findings)[:1200]}\n"
                f"Recommended actions: {json.dumps(actions)[:800]}"
            )
            if articles_read == 0 and facts_learned == 0 and not findings:
                base += _no_data_warning(
                    "investigate",
                    topic,
                    blocking=_is_blocking_domain(topic),
                )
            return base

        if tool == "profile":
            r = await build_profile(llm, intent["entity"], intent.get("ptype", "auto"))
            payload = json.dumps(r, default=str)[:2000] if r else ""
            base = (
                f"\n\n[TOOL: build_profile]\nEntity: {intent['entity']}\n"
                f"Result: {payload or '(empty)'}"
            )
            # Treat as empty if the profile result is missing, errored, or
            # just an empty dict / null fields.
            empty = (
                not r
                or (isinstance(r, dict) and (
                    r.get("error")
                    or not any(v for k, v in r.items() if k not in ("entity", "ptype"))
                ))
            )
            if empty:
                base += _no_data_warning("build_profile", intent["entity"])
            return base

        if tool == "screen":
            # Quick screen — uses fuzzy sanctions module + KB hits for context
            r = await aria_sanctions.fuzzy_screen(intent["entity"])
            kb_hits = knowledge_mod.search_knowledge(intent["entity"]) or ""
            top = (r.get("matches") or [])[:3]
            top_str = "\n".join(
                f"  - {m.get('name')} [{m.get('list')}] score={m.get('score')}"
                for m in top
            ) or "  - no matches"
            return (
                f"\n\n[TOOL: compliance_screen]\nEntity: {intent['entity']}\n"
                f"Top matches:\n{top_str}\n"
                f"Blocked: {r.get('blocked')}\n"
                f"Knowledge base hits: {kb_hits[:1000] or 'None'}"
            )

        if tool == "fuzzy_sanctions":
            r = await aria_sanctions.fuzzy_screen(intent["entity"])
            top = (r.get("matches") or [])[:5]
            top_str = "\n".join(
                f"  - {m.get('name')} [{m.get('list')}] score={m.get('score')} via='{m.get('matched_via_variant')}'"
                for m in top
            ) or "  - no matches"
            return (
                f"\n\n[TOOL: fuzzy_sanctions_screen]\n"
                f"Entity: {intent['entity']}\n"
                f"Variants tried: {r.get('variants_tried')}\n"
                f"Top matches:\n{top_str}\n"
                f"Top score: {r.get('top_score')}\n"
                f"Blocking: {len(r.get('blocking_matches', []))} match(es)"
            )

        if tool == "conflict":
            r = await conflict_tracker.correlate_with_procurement(intent["country"], days=60)
            return (
                f"\n\n[TOOL: conflict_tracker]\n"
                f"Country: {intent['country']}\n"
                f"Escalation: {r.get('escalation_score')} ({r.get('escalation_label')})\n"
                f"Events (60d): {r.get('events_last_period')}, fatalities: {r.get('fatalities_last_period')}\n"
                f"Event mix: {r.get('event_mix')}\n"
                f"Projected demand: {r.get('projected_capability_demand')}\n"
                f"Procurement window: {r.get('procurement_window')}"
            )

        if tool == "tech_explain":
            r = tech_classifier.explain_item(intent["designation"])
            extracted = tech_classifier.classify_text(intent.get("context", ""))
            return (
                f"\n\n[TOOL: tech_classifier]\n"
                f"Designation: {intent['designation']}\n"
                f"Lookup result: {json.dumps(r, default=str)[:1000]}\n"
                f"Other items extracted from message: {extracted.get('raw_summary')}"
            )

    except Exception as e:
        _log.warning("Tool execution failed (%s): %s", tool, e)
        return f"\n\n[TOOL: {tool} — failed: {str(e)[:200]}]"
    return ""


# 18. POST /api/aria/chat
@router.post("/chat")
async def chat_ep(req: ChatRequest, request: Request):
    if not req.message:
        raise HTTPException(status_code=400, detail="message required")
    session_id = req.session_id or str(uuid.uuid4())[:12]

    # Past incident 2026-04-09 19:18 — DUMA Engineering investigation:
    # the WhatsApp listener prepends `[WhatsApp group context]\n[Sender]: ...
    # \n[Question from <sender>]\n` blocks containing recent message history.
    # That history was bleeding into intent detection (the entity for a
    # duma-engineering.com URL became the first 200 chars of conversation
    # history starting with "Iraq tenders 2026"), into tool query construction
    # (5 web_search angles all containing Iraq), into the LLM prompt (the
    # LLM saw the polluted message and confabulated a self-improvement reply
    # instead of an honest brief), and into the verifier (which then flagged
    # NO_CITATIONS because no real duma data was extracted).
    #
    # Strip the listener context block at the very top of the chat handler
    # so EVERY downstream consumer (intent detection, context layer build,
    # LLM prompt construction, session history persistence, verifier,
    # honesty judge, mem0 summariser) sees ONLY the actual current user
    # message. Idempotent on messages without the prefix.
    req.message = _strip_listener_context(req.message)
    if not req.message.strip():
        # If stripping leaves nothing, the listener sent a context-only
        # blob with no actual question — return a clarification rather
        # than firing tools on garbage.
        return {
            "response": "I see context from the conversation but no specific question for me. What would you like me to look at?",
            "session_id": session_id,
            "trivial": True,
        }

    # ── Trivial-question short-circuit (highest priority, runs before
    # tool detection / tracing / verification / cost-tracking).
    # Greetings, liveness probes ('are you online?'), identity questions,
    # 'test'/'ping', 'thanks' get a fixed reply with zero LLM cost and zero
    # tool execution. Past incident 2026-04-08: 'Aria, are you online?'
    # was flowing into _detect_tool_intent which spotted a URL from earlier
    # OCR'd group context, fetched the jewellery shop website, then a
    # follow-up LLM call timed out → self-diagnose fired → user got a
    # generic error message instead of a reply.
    from ..intel import reasoning_library as _rl
    _trivial = _rl.trivial_reply(req.message)
    if _trivial is not None:
        _log.info("[chat] trivial short-circuit: %r → fixed reply", req.message[:80])
        return {
            "response": _trivial,
            "session_id": session_id,
            "trivial": True,
        }

    llm = get_llm(request)
    intel = get_intel_data(request)

    # Start a trace for this chat request — joins cost, verification,
    # and (later) feedback under one id so /trace shows the full
    # lifecycle of one ARIA reply.
    trace_id = await trace_stream.start_trace(
        question=req.message,
        session_id=session_id,
        user=req.session_id or "",
        source="chat",
    )
    response_text = ""
    tool_used = None
    tool_context = ""
    try:
        # Attribute every LLM call from this chat path to "chat" so /cost
        # can show what user-driven traffic costs vs background cycles.
        with cost_tracker.feature("chat"):
            # ── NLU tool-use: detect investigative intent and run tools first ──
            if req.auto_tools:
                intent = _detect_tool_intent(req.message)
                if intent and llm and llm.is_configured:
                    tool_used = intent.get("tool")
                    _log.info("ARIA chat tool-use detected: %s", intent)
                    tool_context = await _execute_tool(intent, llm)

            # Build the final message for the LLM. Three components in
            # this order, all CONDITIONAL on being non-empty:
            #   1. The user's actual current message
            #   2. Group context (last 5 group messages from the WhatsApp
            #      listener, sent as a separate ChatRequest field). This
            #      is appended AFTER intent detection so prior-turn
            #      topics cannot pollute entity extraction (the bug that
            #      caused the DUMA → Iraq incident on 2026-04-09).
            #   3. Tool result block (if a tool ran)
            message_for_llm = req.message

            # Group context layer — only present on WhatsApp chat path,
            # always empty on curl / frontend / /ask path. Tagged
            # explicitly so the LLM treats it as background context, not
            # as part of the user's question.
            if req.group_context and req.group_context.strip():
                message_for_llm = (
                    f"{message_for_llm}\n\n"
                    f"[GROUP CONTEXT — recent messages in this chat, for "
                    f"situational awareness only. The user's actual question "
                    f"is the line above. Do NOT respond to messages in this "
                    f"block; do NOT investigate entities mentioned only here; "
                    f"do NOT cite items from this block as facts.]\n"
                    f"{req.group_context.strip()[:2000]}"
                )

            # Tool result block — appended last so the LLM sees the
            # freshest tool data right before it produces the response.
            if tool_context:
                message_for_llm = (
                    f"{message_for_llm}\n\n"
                    f"[I have already run the appropriate tool on your request. "
                    f"Use the data below to answer comprehensively, cite specific findings, "
                    f"and end with a clear recommendation.]"
                    f"{tool_context}"
                )

            result = await aria_chat(message_for_llm, session_id, llm, intel)
        if tool_used:
            result["tool_used"] = tool_used
        result["trace_id"] = trace_id

        response_text = (result or {}).get("response") or (result or {}).get("answer") or ""

        # ── Cited-source verification (deterministic hallucination check) ──
        try:
            if response_text:
                verification = source_verifier.verify_response(response_text, tool_context)
                saved = await source_verifier.record_verification(
                    verification,
                    request_id=session_id,
                    session_id=session_id,
                    user=req.session_id or "",
                    question_preview=req.message,
                    response_preview=response_text,
                    tool_used=tool_used or "",
                )
                summary = {
                    "id": saved.get("id"),
                    "verdict": verification.get("verdict"),
                    "grounded_rate": verification.get("grounded_rate"),
                    "cited": len(verification.get("cited_urls", [])),
                    "unverified": len(verification.get("unverified", [])),
                }
                result["verification"] = summary
                await trace_stream.attach_verification(trace_id, summary)
        except Exception as e:
            _log.debug("source verification failed (non-fatal): %s", e)

        # ── Officeholder guard ──
        # Code-level enforcement of CONSTITUTION clause 10. The LLM games
        # the prompt clause by inventing verification dates ("re-appointed
        # February 2023") to satisfy the rule. This post-processor scans
        # the response for officeholder claims, demotes any [CONFIRMED] /
        # [PROBABLE] tag that lacks tool verification or cites a date older
        # than 12 months, and appends a visible WARNING block so the user
        # sees the demotion explicitly.
        # Behind ARIA_OFFICEHOLDER_GUARD env var (default ON).
        try:
            from ..intel import officeholder_guard
            _v = result.get("verification")
            _log.warning(
                "[officeholder_guard] running on trace=%s verification=%r response_len=%d",
                trace_id,
                _v,
                len(response_text or ""),
            )
            rewritten, demotions = officeholder_guard.review_response(
                response_text,
                _v,
            )
            _log.warning(
                "[officeholder_guard] result trace=%s demotions=%d",
                trace_id,
                len(demotions),
            )
            if demotions:
                response_text = rewritten
                result["response"] = rewritten
                result["officeholder_demotions"] = demotions
                _log.warning(
                    "[officeholder_guard] DEMOTED %d claim(s) in trace %s",
                    len(demotions), trace_id,
                )
        except Exception as e:
            # Bumped from debug to warning so silent failures show up in fly logs.
            _log.warning(
                "[officeholder_guard] failed: %r — trace=%s",
                e, trace_id,
                exc_info=True,
            )

        # ── Confidence-tagged reply footer ──
        # Wires existing observability signals (confidence tags +
        # source_verifier verdict + grounded/unverified counts) into a
        # structured footer block appended to the user-facing reply. This
        # is the single biggest "professional intelligence vs chatbot"
        # signal — visible epistemic state instead of bare prose.
        # Behind ARIA_CONFIDENCE_FOOTER (default ON). Disabled → no-op.
        try:
            from ..intel import confidence_footer
            footer = confidence_footer.build_footer(
                response_text=response_text,
                verification=result.get("verification"),
                rag_sources_count=0,  # RAG count not currently surfaced from aria_chat
            )
            if footer:
                result["response"] = (response_text or "") + footer
        except Exception as e:
            _log.debug("confidence footer build failed (non-fatal): %s", e)

        # ── Correction learner — extract facts from user corrections ──
        # When the user message looks like a correction containing factual
        # claims (e.g. "you said Nitiwul but actually it's Boamah"), spawn a
        # background task that runs an LLM extractor on the user message and
        # stores any extracted facts in knowledge.py with high trust. The
        # facts then surface in subsequent replies via the recent_corrections
        # addendum (read side, wired in _build_calibrated_system_prompt).
        # Behind ARIA_CORRECTION_LEARN env var.
        try:
            from ..intel import correction_learner as _cl
            if _cl.looks_like_correction(req.message):
                async def _learn_correction_bg(
                    _msg=req.message,
                    _sender=req.session_id or "user",
                    _llm=llm,
                ):
                    try:
                        result_summary = await _cl.extract_and_persist(_msg, _sender, _llm)
                        if result_summary.get("stored", 0):
                            _log.info(
                                "[correction_learner] background: stored %d fact(s) from correction",
                                result_summary["stored"],
                            )
                    except Exception as e:
                        _log.warning("correction_learner bg failed: %s: %s", type(e).__name__, e)
                import asyncio as _aio
                _aio.create_task(_learn_correction_bg())
        except Exception as e:
            _log.warning("correction_learner dispatch failed: %s: %s", type(e).__name__, e)

        # ── Honesty judge — fire in background if response has [CONFIRMED] tags ──
        # This is another LLM round-trip and would add 2-5s of latency to the
        # chat reply if run inline. Instead we spawn it as a task that
        # self-attaches to the trace via attach_judgment when it completes.
        # Skipped silently when the response has no confidence tags or no
        # tool ran (nothing to judge against).
        try:
            if (
                response_text
                and tool_context
                and honesty_judge.has_confidence_tags(response_text)
            ):
                async def _judge_bg(
                    _llm=llm,
                    _resp=response_text,
                    _ctx=tool_context,
                    _trace_id=trace_id,
                    _session=session_id,
                    _q=req.message,
                ):
                    try:
                        judgment = await honesty_judge.judge_response(_llm, _resp, _ctx)
                        await honesty_judge.record_judgment(
                            judgment,
                            trace_id=_trace_id,
                            session_id=_session,
                            question_preview=_q,
                            response_preview=_resp,
                        )
                    except Exception as e:
                        _log.warning("honesty judge bg failed: %s: %s", type(e).__name__, e)
                import asyncio as _aio
                _aio.create_task(_judge_bg())
        except Exception as e:
            _log.warning("honesty judge dispatch failed: %s: %s", type(e).__name__, e)

        return result
    finally:
        # Always finalise the trace so /trace doesn't show stuck
        # in_progress entries even if the request errored out.
        try:
            await trace_stream.finish_trace(
                trace_id,
                response=response_text,
                tool_used=tool_used or "",
                tool_context_size=len(tool_context or ""),
                status="ok" if response_text else "empty_response",
            )
        except Exception as e:
            _log.debug("finish_trace failed: %s", e)


# 19. POST /api/aria/think
@router.post("/think")
async def think_ep(req: ThinkRequest, request: Request):
    if not req.question:
        raise HTTPException(status_code=400, detail="question required")
    llm = get_llm(request)
    intel = get_intel_data(request)
    result = await aria_think(req.question, req.context, llm, intel)
    return result


# ── Research & Learning Endpoints ────────────────────────────────────────────

# 20. POST /api/aria/research/auto — Run autonomous research cycle
@router.post("/research/auto")
async def research_auto_ep(request: Request):
    llm = get_llm(request)
    result = await research_and_learn(llm)
    return result


# 24. POST /api/aria/read — Read a specific article URL
@router.post("/read")
async def read_article_ep(request: Request):
    body = await request.json()
    url = body.get("url", "")
    context = body.get("context", "")
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    llm = get_llm(request)
    result = await read_article(llm, url, context)
    return result


# 25. POST /api/aria/read-document — Read a document (text content or base64 binary)
@router.post("/read-document")
async def read_document_ep(request: Request):
    body = await request.json()
    content = body.get("content", "")
    filename = body.get("filename", "unknown")
    source = body.get("source", "document")
    context = body.get("context", "")
    encoding = body.get("encoding", "utf-8")
    mimetype = body.get("mimetype", "")

    # Handle base64-encoded binary documents (PDF, DOCX, Excel)
    if encoding == "base64" and content:
        import base64 as _b64
        try:
            raw_bytes = _b64.b64decode(content)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 content")

        extracted = ""
        fname_lower = filename.lower()
        mime_lower = mimetype.lower()

        # PDF extraction
        if "pdf" in mime_lower or fname_lower.endswith(".pdf"):
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(stream=raw_bytes, filetype="pdf")
                extracted = "\n".join(page.get_text() for page in doc)[:15000]
                doc.close()
            except ImportError:
                llm = get_llm(request)
                ocr_result = await aria_ocr.extract_text_from_image(raw_bytes, filename, context, llm)
                extracted = ocr_result.get("text", "")
            except Exception as e:
                _log.warning("PDF extraction failed: %s", e)

        # DOCX extraction
        elif "word" in mime_lower or "officedocument" in mime_lower or fname_lower.endswith(".docx"):
            try:
                import io, zipfile, re as _re
                zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
                if "word/document.xml" in zf.namelist():
                    xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
                    extracted = _re.sub(r"<[^>]+>", " ", xml)
                    extracted = " ".join(extracted.split())[:15000]
                zf.close()
            except Exception as e:
                _log.warning("DOCX extraction failed: %s", e)

        # Excel extraction
        elif "spreadsheet" in mime_lower or fname_lower.endswith((".xlsx", ".xls")):
            try:
                import io
                from openpyxl import load_workbook
                wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
                rows = []
                for ws in wb.worksheets[:3]:
                    rows.append(f"--- Sheet: {ws.title} ---")
                    for row in ws.iter_rows(max_row=200, values_only=True):
                        rows.append(",".join(str(c or "") for c in row))
                wb.close()
                extracted = "\n".join(rows)[:15000]
            except Exception as e:
                _log.warning("Excel extraction failed: %s", e)

        if not extracted or len(extracted) < 30:
            llm = get_llm(request)
            ocr_result = await aria_ocr.extract_text_from_image(raw_bytes, filename, context, llm)
            extracted = ocr_result.get("text", "")

        if not extracted or len(extracted) < 30:
            raise HTTPException(status_code=400, detail="Could not extract text from binary document")
        content = extracted

    if not content or len(content) < 30:
        raise HTTPException(status_code=400, detail="content required (min 30 chars)")
    llm = get_llm(request)
    result = await read_document(llm, content, filename, source, context)
    return result


# 21. GET /api/aria/hypotheses — ARIA's current hypotheses
@router.get("/hypotheses")
async def hypotheses_ep():
    return {"hypotheses": await get_hypotheses()}


# 22. POST /api/aria/hypotheses/validate — Validate a specific hypothesis
@router.post("/hypotheses/validate")
async def validate_hypothesis_ep(request: Request):
    body = await request.json()
    hypothesis = body.get("hypothesis", "")
    if not hypothesis:
        raise HTTPException(status_code=400, detail="hypothesis required")
    llm = get_llm(request)
    result = await validate_hypothesis(llm, hypothesis)
    return result


# 23. GET /api/aria/research/summary — What ARIA has learned
@router.get("/research/summary")
async def research_summary_ep(request: Request):
    llm = get_llm(request)
    return await get_research_summary(llm)


# ── Cache hygiene admin endpoints ───────────────────────────────────────────
# Two surgical operations for the WhatsApp operator:
#   /api/aria/admin/purge-cases  — drop polluted entries from the case library
#   /api/aria/session/forget     — wipe a single session's history
# Both are safe-by-default: purge supports dry_run, forget only touches the
# specified session.
@router.post("/admin/purge-cases")
async def purge_cases_ep(request: Request):
    """One-shot purge of correction-acknowledgement / feedback / investigation
    entries from the reasoning_library case index. Use after deploying the
    round-3 anti-replay fix to clean up entries written before the fix shipped.

    Body (all optional):
        {"dry_run": true}  — count what WOULD be removed without deleting
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    dry_run = bool(body.get("dry_run", False))
    from ..intel import reasoning_library as _rl
    return await _rl.purge_polluted_cases(dry_run=dry_run)


@router.post("/admin/purge-signals")
async def purge_signals_ep(request: Request):
    """Surgical purge of intel-ledger signals matching one or more keywords.

    Designed for cleanup after a polluted current-event signal has bled
    across multiple chat replies (past incident 2026-04-09: a single
    intelslava Telegram post about Lebanon airstrikes propagated into
    unrelated commercial conversations because the signal sat in the
    30-day rolling intel ledger and got pulled by every chat reply).

    Body:
        {
            "keywords": ["lebanon", "hms dragon", "112 killed"],
            "dry_run": true  // optional, default false
        }

    Returns:
        {
            "matched": <count>,
            "removed": <count>,        // 0 if dry_run
            "remaining": <count>,
            "sample": [{"text": ..., "source": ..., "ts": ...}, ...],
            "dry_run": <bool>,
            "keywords": [...]
        }
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    keywords = body.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, list) or not keywords:
        raise HTTPException(status_code=400, detail="Body must include non-empty 'keywords' list")
    dry_run = bool(body.get("dry_run", False))
    from ..intel import intel_ledger as _il
    return await _il.purge_signals_by_keyword(keywords, dry_run=dry_run)


@router.post("/session/forget")
async def session_forget_ep(request: Request):
    """Wipe the conversation history for one session_id.

    Used by the WhatsApp /forget command when a thread has gone off the rails
    (cached pollution, hallucinated context, sensitive content the user wants
    out of memory). Only affects the specified session — other senders are
    untouched.

    Body:
        {"session_id": "wa_group_xxx"}
    """
    body = await request.json()
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    from ..intel import redis_store as _rs
    key = f"crucix:aria:session:{session_id}"
    existed = await _rs.delete(key)
    return {
        "ok": True,
        "session_id": session_id,
        "existed": bool(existed),
        "message": "session wiped — next message will start fresh" if existed else "no session existed under that id",
    }


# ── Tiered corpus ingest ────────────────────────────────────────────────────
# Curated documents (Tier A primary sources, Tier B secondary intel,
# Tier C live feeds, Tier D Arkmurus proprietary) get pushed in here with
# explicit provenance metadata so retrieval can prefer trusted tiers.
# Independent of /read-document which is the opportunistic-ingest path
# for whatever gets shared in chat.
@router.post("/corpus/ingest")
async def corpus_ingest_ep(request: Request):
    """Ingest a single corpus document with tier metadata.

    Body shape:
    {
        "filename":          "SIPRI-arms-transfers-2024.pdf",
        "content_b64":       "<base64 of file bytes>",   # OR plain "text"
        "text":              "<plain text content>",     # if no base64
        "mimetype":          "application/pdf",
        "tier":              "A",                         # A/B/C/D
        "source_class":      "SIPRI",
        "region":            "Africa",
        "cplp_relevant":     true,
        "confidence":        "CONFIRMED",                 # optional default
        "publication_date":  "2024-03",                   # optional ISO
        "notes":             "Annual arms transfers report"
    }
    """
    body = await request.json()
    filename = (body.get("filename") or "unknown").strip()
    tier = (body.get("tier") or "").strip().upper()
    source_class = (body.get("source_class") or "").strip()
    if not tier:
        raise HTTPException(status_code=400, detail="tier required (A/B/C/D)")
    if not source_class:
        raise HTTPException(status_code=400, detail="source_class required")

    text = body.get("text") or ""
    content_b64 = body.get("content_b64") or ""
    mimetype = body.get("mimetype") or ""

    # If binary, extract text first
    if not text and content_b64:
        import base64 as _b64
        try:
            raw_bytes = _b64.b64decode(content_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid base64 content")
        from ..intel import corpus_ingest as _ci
        try:
            text = _ci.extract_text_from_bytes(raw_bytes, filename, mimetype)
        except _ci.ExtractError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if not text or len(text.strip()) < 30:
        raise HTTPException(status_code=400, detail="no usable text (min 30 chars)")

    from ..intel import corpus_ingest as _ci
    try:
        result = await _ci.ingest_corpus_document(
            text,
            filename=filename,
            tier=tier,
            source_class=source_class,
            region=body.get("region", ""),
            cplp_relevant=bool(body.get("cplp_relevant", False)),
            confidence=body.get("confidence", ""),
            publication_date=body.get("publication_date", ""),
            notes=body.get("notes", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Branded report builder ──────────────────────────────────────────────────
# Generates Arkmurus-formatted DD / compliance / market reports by combining
# investigate() + Tier D template retrieval + LLM template fill. Independent
# of the chat path — has its own endpoint and its own slash command.
@router.post("/report")
async def report_ep(request: Request):
    """Build a branded Arkmurus report.

    Body shape:
        {
            "report_type":   "dd" | "compliance" | "market",
            "subject":       "Acme Trading Corp",
            "extra_context": "(optional free-form context the user supplied)"
        }
    """
    body = await request.json()
    report_type = (body.get("report_type") or "").strip()
    subject = (body.get("subject") or "").strip()
    if not report_type or not subject:
        raise HTTPException(status_code=400, detail="report_type and subject required")
    llm = get_llm(request)
    from ..intel import report_builder
    result = await report_builder.build_report(
        llm,
        report_type=report_type,
        subject=subject,
        extra_context=body.get("extra_context", ""),
    )
    if result.get("error"):
        # Return 200 with error in body — the WhatsApp side renders these
        # consistently rather than choking on a 4xx.
        return result
    return result


# ── Deep Research Endpoints ──────────────────────────────────────────────────

# 26. POST /api/aria/crawl — Crawl a website, follow links, read everything
@router.post("/crawl")
async def crawl_ep(request: Request):
    body = await request.json()
    url = body.get("url", "")
    max_pages = body.get("max_pages", 20)
    context = body.get("context", "")
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    llm = get_llm(request)
    return await crawl_website(llm, url, max_pages, context)


# GET /api/aria/crawl/progress/{domain} — Live crawl status
@router.get("/crawl/progress/{domain}")
async def crawl_progress_ep(domain: str):
    """Query live crawl progress for a domain currently being crawled."""
    return await get_crawl_progress(domain)


# 27. POST /api/aria/investigate — Deep multi-source investigation
@router.post("/investigate")
async def investigate_ep(request: Request):
    body = await request.json()
    topic = body.get("topic", "")
    depth = body.get("depth", "thorough")
    if not topic:
        raise HTTPException(status_code=400, detail="topic required")
    llm = get_llm(request)
    return await investigate(llm, topic, depth)


# 28. POST /api/aria/scenarios — Strategic scenario analysis
@router.post("/scenarios")
async def scenarios_ep(request: Request):
    body = await request.json()
    situation = body.get("situation", "")
    num_scenarios = body.get("num_scenarios", 4)
    if not situation:
        raise HTTPException(status_code=400, detail="situation required")
    llm = get_llm(request)
    return await analyse_scenarios(llm, situation, num_scenarios)


# 29. POST /api/aria/profile — Build intelligence profile on entity
@router.post("/profile")
async def profile_ep(request: Request):
    body = await request.json()
    entity = body.get("entity", "")
    profile_type = body.get("type", "auto")
    if not entity:
        raise HTTPException(status_code=400, detail="entity required")
    llm = get_llm(request)
    return await build_profile(llm, entity, profile_type)


# 29b. POST /api/aria/investigate/person — Deep person investigation
@router.post("/investigate/person")
async def investigate_person_ep(request: Request):
    body = await request.json()
    name = body.get("name", "")
    context = body.get("context", "")
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    llm = get_llm(request)
    return await investigate_person(llm, name, context)


# 29c. POST /api/aria/investigate/company — Deep company investigation
@router.post("/investigate/company")
async def investigate_company_ep(request: Request):
    body = await request.json()
    company = body.get("company", "")
    country = body.get("country", "")
    if not company:
        raise HTTPException(status_code=400, detail="company required")
    llm = get_llm(request)
    return await investigate_company(llm, company, country)


# 29d. POST /api/aria/network — Map relationships between entities
@router.post("/network")
async def network_ep(request: Request):
    body = await request.json()
    entities = body.get("entities", [])
    context = body.get("context", "")
    if not entities or len(entities) < 2:
        raise HTTPException(status_code=400, detail="At least 2 entities required")
    llm = get_llm(request)
    return await map_network(llm, entities, context)


# ── Neural Memory ────────────────────────────────────────────────────────────

# 30. GET /api/aria/neural/stats — Neural network statistics
@router.get("/neural/stats")
async def neural_stats_ep():
    return await neural_memory.get_stats()


# 31. POST /api/aria/neural/recall — Associative recall
@router.post("/neural/recall")
async def neural_recall_ep(request: Request):
    body = await request.json()
    query = body.get("query", "")
    depth = min(body.get("depth", 2), 3)
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    return await neural_memory.recall(query, depth=depth)


# 32. POST /api/aria/neural/learn — Teach ARIA a concept
@router.post("/neural/learn")
async def neural_learn_ep(request: Request):
    body = await request.json()
    concept = body.get("concept", "")
    category = body.get("category", "general")
    related_to = body.get("related_to", [])
    confidence = body.get("confidence", "CONFIRMED")
    metadata = body.get("metadata", {})
    if not concept:
        raise HTTPException(status_code=400, detail="concept required")
    return await neural_memory.learn_explicit(
        concept, category, related_to, source="user", confidence=confidence, metadata=metadata
    )


# 33. GET /api/aria/neural/cluster/{concept} — Get concept neighborhood
@router.get("/neural/cluster/{concept}")
async def neural_cluster_ep(concept: str):
    return await neural_memory.get_cluster(concept)


# 33a. GET /api/aria/neural/graph — Knowledge graph visualization
@router.get("/neural/graph")
async def neural_graph_ep(limit: int = 200):
    """Returns ARIA's neural network as a graph structure for D3/Cytoscape visualization.
    Format: {nodes: [{id, label, category, activation, size}], edges: [{source, target, weight}]}
    """
    from collections import defaultdict as _defaultdict

    # Get neurons sorted by activation (top N)
    all_neurons = list(neural_memory._neurons.values())
    all_neurons.sort(key=lambda n: -n.get("activation", 0))
    top_neurons = all_neurons[:limit]
    top_ids = {n["id"] for n in top_neurons}

    # Build nodes
    nodes = []
    cat_counts = _defaultdict(int)
    for n in top_neurons:
        cat = n.get("category", "general")
        cat_counts[cat] += 1
        nodes.append({
            "id": n["id"],
            "label": n.get("label", n.get("concept", "")),
            "category": cat,
            "activation": round(n.get("activation", 0), 3),
            "size": max(4, round(n.get("activation", 0) * 20, 1)),
        })

    # Build edges (only between neurons in the result set)
    edges = []
    for from_id, targets in neural_memory._edges.items():
        if from_id not in top_ids:
            continue
        for to_id, weight in targets.items():
            if to_id in top_ids and from_id < to_id:  # deduplicate bidirectional
                edges.append({
                    "source": from_id,
                    "target": to_id,
                    "weight": round(weight, 3),
                })

    total_neurons = len(neural_memory._neurons)
    total_edges = sum(len(v) for v in neural_memory._edges.values())

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "total_neurons": total_neurons,
            "total_edges": total_edges,
            "returned_neurons": len(nodes),
            "returned_edges": len(edges),
            "categories": dict(cat_counts),
        },
    }


# 33a2. GET /api/aria/conversations/search — Full-text conversation search
@router.get("/conversations/search")
async def search_conversations_ep(q: str = "", limit: int = 20):
    """Search across all ARIA conversation sessions for matching messages."""
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="q parameter required (min 2 chars)")

    query_lower = q.strip().lower()
    results = []

    # Use SCAN to iterate session keys (never KEYS)
    client = rs._client
    if client:
        cursor = 0
        session_keys = []
        while True:
            cursor, keys = await client.scan(cursor, match="crucix:aria:session:*", count=100)
            session_keys.extend(keys)
            if cursor == 0:
                break
            if len(session_keys) > 500:  # safety cap
                break

        for key in session_keys:
            if len(results) >= limit:
                break
            try:
                raw = await client.get(key)
                if not raw:
                    continue
                import json as _json
                session = _json.loads(raw) if isinstance(raw, str) else raw
                messages = session.get("messages") or session.get("history") or []
                if isinstance(session, list):
                    messages = session

                matching_msgs = []
                for msg in messages:
                    content = ""
                    if isinstance(msg, dict):
                        content = msg.get("content", "") or msg.get("text", "") or msg.get("message", "")
                    elif isinstance(msg, str):
                        content = msg
                    if query_lower in content.lower():
                        matching_msgs.append({
                            "role": msg.get("role", "unknown") if isinstance(msg, dict) else "unknown",
                            "content": content[:500],
                            "match": True,
                        })

                if matching_msgs:
                    session_id = key.replace("crucix:aria:session:", "")
                    results.append({
                        "session_id": session_id,
                        "matched_messages": matching_msgs[:5],
                        "total_matches": len(matching_msgs),
                    })
            except Exception:
                continue
    else:
        # Fallback: search in-memory store
        for key, raw in rs._mem_store.items():
            if not key.startswith("crucix:aria:session:"):
                continue
            if len(results) >= limit:
                break
            try:
                import json as _json
                session = _json.loads(raw) if isinstance(raw, str) else raw
                messages = session.get("messages") or session.get("history") or []
                if isinstance(session, list):
                    messages = session

                matching_msgs = []
                for msg in messages:
                    content = ""
                    if isinstance(msg, dict):
                        content = msg.get("content", "") or msg.get("text", "") or msg.get("message", "")
                    elif isinstance(msg, str):
                        content = msg
                    if query_lower in content.lower():
                        matching_msgs.append({
                            "role": msg.get("role", "unknown") if isinstance(msg, dict) else "unknown",
                            "content": content[:500],
                            "match": True,
                        })

                if matching_msgs:
                    session_id = key.replace("crucix:aria:session:", "")
                    results.append({
                        "session_id": session_id,
                        "matched_messages": matching_msgs[:5],
                        "total_matches": len(matching_msgs),
                    })
            except Exception:
                continue

    return {
        "query": q,
        "results": results,
        "total_sessions_matched": len(results),
        "limit": limit,
    }


# 33b. POST /api/aria/neural/consolidate — Nightly memory consolidation cycle
@router.post("/neural/consolidate")
async def neural_consolidate_ep(request: Request):
    """Memory consolidation — prune weak neurons, strengthen strong, merge facts, validate hypotheses."""
    import logging as _logging
    _clog = _logging.getLogger("aria.consolidation")

    report: dict = {"ok": True}

    # 1. Neural memory consolidation
    try:
        report["neural"] = await neural_memory.consolidate()
    except Exception as e:
        _clog.warning("Neural consolidation failed: %s", e)
        report["neural"] = {"error": str(e)}

    # 2. Knowledge base consolidation
    try:
        report["knowledge"] = await knowledge_mod.consolidate_facts()
    except Exception as e:
        _clog.warning("Knowledge consolidation failed: %s", e)
        report["knowledge"] = {"error": str(e)}

    # 3. Trigger pending hypothesis validation
    try:
        llm = get_llm(request)
        hypotheses = await get_hypotheses()
        open_hyps = [h for h in hypotheses if h.get("status") == "OPEN"][:5]
        hyp_results = []
        for h in open_hyps:
            try:
                r = await validate_hypothesis(llm, h["hypothesis"])
                hyp_results.append({"hypothesis": h["hypothesis"][:80], "result": r.get("verdict", r.get("status", "?"))})
            except Exception:
                hyp_results.append({"hypothesis": h["hypothesis"][:80], "result": "ERROR"})
        report["hypotheses"] = {"validated": len(hyp_results), "results": hyp_results}
    except Exception as e:
        _clog.warning("Hypothesis validation failed: %s", e)
        report["hypotheses"] = {"error": str(e)}

    _clog.info("Consolidation complete: %s", report)
    return report


# ── Self-Improvement ─────────────────────────────────────────────────────────

# 34. GET /api/aria/self/files — List ARIA's own source files
@router.get("/self/files")
async def self_files_ep():
    return {"files": await self_improve.list_own_files()}


# 35. POST /api/aria/self/read — Read ARIA's own source code
@router.post("/self/read")
async def self_read_ep(request: Request):
    body = await request.json()
    file_path = body.get("file", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="file required")
    return await self_improve.read_own_code(file_path)


# 36. POST /api/aria/self/improve — Stage a code improvement
@router.post("/self/improve")
async def self_improve_ep(request: Request):
    body = await request.json()
    file_path = body.get("file", "")
    new_content = body.get("content", "")
    change_type = body.get("change_type", "enhancement")
    description = body.get("description", "")
    reasoning = body.get("reasoning", "")
    if not file_path or not new_content or not description:
        raise HTTPException(status_code=400, detail="file, content, and description required")
    return await self_improve.stage_improvement(
        file_path, new_content, change_type, description, reasoning
    )


# Self-diagnostic: receive a failure report from any downstream component
@router.post("/self/diagnose")
async def self_diagnose_ep(request: Request):
    """Receive a failure report and have ARIA classify + decide an action.

    This is the AUTO self-fix entry point. Called by:
      - WhatsApp listener when askARIA() fails
      - Sweep ingest when it can't reach the brain
      - OCR pipeline when all backends fail
      - Any future component that wants ARIA to learn from its errors

    Returns the diagnosis + suggested action so the caller knows what
    happened and can decide whether to retry / alert / escalate.
    """
    body = await request.json()
    failure_type = (body.get("failure_type") or "unknown").strip()
    error_message = (body.get("error_message") or "").strip()
    context = body.get("context") or {}
    if not error_message:
        raise HTTPException(status_code=400, detail="error_message required")
    return await self_improve.diagnose_failure(failure_type, error_message, context)


# Self-coding: scaffold a brand-new module from a free-text request
@router.post("/self/code")
async def self_code_ep(request: Request):
    """ARIA writes a new intel module from a natural-language request.

    Body: {request: "track Saudi MoD procurement", name: "saudi_mod_tracker"?}
    Returns: {ok, file, module_name, lines, staged_id, preview} or {ok: false, error}

    The generated module is staged under aria_service/intel/auto/<name>.py and
    must be deployed via /api/aria/self/deploy/{staged_id}. NEVER auto-deploys
    new files — they always require human review.
    """
    body = await request.json()
    user_request = (body.get("request") or "").strip()
    suggested_name = (body.get("name") or "").strip()
    if not user_request or len(user_request) < 10:
        raise HTTPException(status_code=400, detail="request required (min 10 chars describing what the module should do)")
    llm = get_llm(request)
    return await self_improve.propose_new_module(user_request, llm, suggested_name=suggested_name)


# 37. GET /api/aria/self/staged — List staged improvements
@router.get("/self/staged")
async def self_staged_ep():
    return {"staged": await self_improve.get_staged()}


# 38. GET /api/aria/self/staged/{id} — Get diff for a staged improvement
@router.get("/self/staged/{improvement_id}")
async def self_staged_diff_ep(improvement_id: str):
    return await self_improve.get_staged_diff(improvement_id)


# 39. POST /api/aria/self/deploy/{id} — Deploy a staged improvement
@router.post("/self/deploy/{improvement_id}")
async def self_deploy_ep(improvement_id: str):
    return await self_improve.deploy_improvement(improvement_id)


# 40. POST /api/aria/self/rollback/{id} — Rollback a deployed improvement
@router.post("/self/rollback/{improvement_id}")
async def self_rollback_ep(improvement_id: str):
    return await self_improve.rollback_improvement(improvement_id)


# 41. POST /api/aria/self/evolve-prompt — Evolve system prompt
@router.post("/self/evolve-prompt")
async def self_evolve_prompt_ep(request: Request):
    body = await request.json()
    feedback = body.get("feedback", "")
    current_prompt = body.get("current_prompt", "")
    performance_data = body.get("performance_data", None)
    if not feedback:
        raise HTTPException(status_code=400, detail="feedback required")
    llm = get_llm(request)
    return await self_improve.evolve_prompt(llm, current_prompt, feedback, performance_data)


# 42. GET /api/aria/self/log — Improvement history
@router.get("/self/log")
async def self_log_ep():
    return {"log": await self_improve.get_improvement_log()}


# 43. GET /api/aria/self/code-knowledge — ARIA's learned coding patterns
@router.get("/self/code-knowledge")
async def self_code_knowledge_ep():
    return await self_improve.get_code_knowledge()


# ── Semantic Search ──────────────────────────────────────────────────────────

# 44. POST /api/aria/semantic/search — Search knowledge by meaning
@router.post("/semantic/search")
async def semantic_search_ep(request: Request):
    body = await request.json()
    query = body.get("query", "")
    top_k = min(body.get("top_k", 10), 50)
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    return {"results": semantic_search(query, top_k)}


# 45. GET /api/aria/semantic/stats — Semantic index statistics
@router.get("/semantic/stats")
async def semantic_stats_ep():
    return get_index_stats()


# Pre-Phase-3 brain observability endpoint 2026-04-09 — single-call view
# of every persistent brain layer for a given session. Used to debug
# "ARIA keeps saying X" / "she should remember Y" reports without having
# to grep across 6 different stats endpoints.
@router.get("/admin/brain/{session_id}")
async def admin_brain_ep(session_id: str, query: str = ""):
    """Returns a unified snapshot of what each brain layer holds for
    a given session. Optional `query` param scopes RAG / neural recall
    to a specific topic for relevance-style debugging.

    Each layer is wrapped in its own try/except so a failure in one
    layer doesn't kill the rest of the snapshot — partial visibility
    beats no visibility.
    """
    import inspect as _inspect
    out: dict = {"session_id": session_id, "query": query or None}

    async def _maybe_await(value):
        """Await value if it's a coroutine, otherwise return as-is.
        Several brain modules have a mix of sync and async stats functions
        and we don't want to know which is which at call site."""
        if _inspect.iscoroutine(value):
            return await value
        return value

    # 1. Session history (best-effort — there is no top-level history
    #    module; session state lives in Redis under crucix:aria:sessions)
    try:
        sess_data = await rs.get_json(f"crucix:aria:sessions:{session_id}")
        if sess_data and isinstance(sess_data, dict):
            history = sess_data.get("history", [])
            out["session"] = {
                "exists": True,
                "turn_count": len(history) // 2,
                "history_length": len(history),
            }
        else:
            out["session"] = {"exists": False}
    except Exception as e:
        out["session"] = {"error": f"{type(e).__name__}: {e}"}

    # 2. RAG store stats
    try:
        from ..intel import rag_store as _rs
        rag_stats = await _maybe_await(_rs.get_stats()) if hasattr(_rs, "get_stats") else {}
        if not isinstance(rag_stats, dict):
            rag_stats = {}
        out["rag"] = {
            "documents_indexed": rag_stats.get("documents_indexed", 0),
            "facts_indexed": rag_stats.get("facts_indexed", 0),
            "total_chunks": rag_stats.get("total_chunks", 0),
            "embedding_model": rag_stats.get("embedding_model"),
        }
        if query and hasattr(_rs, "get_rag_context"):
            try:
                ctx = await _maybe_await(_rs.get_rag_context(query, max_chars=600))
                if isinstance(ctx, str):
                    out["rag"]["preview_for_query"] = ctx[:600]
            except Exception as e:
                out["rag"]["preview_error"] = str(e)[:200]
    except Exception as e:
        out["rag"] = {"error": f"{type(e).__name__}: {e}"}

    # 3. Knowledge facts (Redis)
    try:
        kb = await knowledge_mod._load()
        facts = (kb or {}).get("facts", []) if isinstance(kb, dict) else []
        out["knowledge"] = {
            "total_facts": len(facts),
            "recent_facts": [
                {"topic": (f.get("topic") or "")[:80], "content": (f.get("content") or "")[:120]}
                for f in facts[-5:]
            ],
        }
    except Exception as e:
        out["knowledge"] = {"error": f"{type(e).__name__}: {e}"}

    # 4. Semantic search index
    try:
        out["semantic_search"] = get_index_stats()
    except Exception as e:
        out["semantic_search"] = {"error": f"{type(e).__name__}: {e}"}

    # 5. Neural memory stats
    try:
        ns = await _maybe_await(neural_memory.get_stats()) if hasattr(neural_memory, "get_stats") else {}
        if not isinstance(ns, dict):
            ns = {}
        out["neural_memory"] = {
            "total_neurons": ns.get("total_neurons", 0),
            "total_edges": ns.get("total_edges", 0),
            "total_activations": ns.get("total_activations", 0),
        }
    except Exception as e:
        out["neural_memory"] = {"error": f"{type(e).__name__}: {e}"}

    # 6. Reasoning library
    try:
        rl_stats = await _maybe_await(reasoning_library.get_stats()) if hasattr(reasoning_library, "get_stats") else {}
        if not isinstance(rl_stats, dict):
            rl_stats = {}
        out["reasoning_library"] = {
            "total_cases": rl_stats.get("total_cases", 0),
            "hit_rate": rl_stats.get("hit_rate", 0),
            "total_lookups": rl_stats.get("total_lookups", 0),
            "total_hits": rl_stats.get("total_hits", 0),
        }
    except Exception as e:
        out["reasoning_library"] = {"error": f"{type(e).__name__}: {e}"}

    # 7. Intel ledger
    try:
        led = await _maybe_await(intel_ledger.get_stats())
        if not isinstance(led, dict):
            led = {}
        out["intel_ledger"] = {
            "total_signals": led.get("totalSignals", 0),
            "by_type": led.get("byType", {}),
        }
    except Exception as e:
        out["intel_ledger"] = {"error": f"{type(e).__name__}: {e}"}

    # 8. Recent corrections (best-effort — module API varies)
    try:
        from ..intel import correction_learner as _cl
        for fn_name in ("get_recent_corrections", "recent", "list_recent"):
            if hasattr(_cl, fn_name):
                fn = getattr(_cl, fn_name)
                try:
                    corrections = await _maybe_await(fn(limit=5))
                    out["recent_corrections"] = corrections
                    break
                except TypeError:
                    corrections = await _maybe_await(fn())
                    out["recent_corrections"] = corrections[:5] if corrections else []
                    break
        else:
            out["recent_corrections"] = {"note": "no recent-corrections accessor found in module"}
    except Exception as e:
        out["recent_corrections"] = {"error": f"{type(e).__name__}: {e}"}

    return out


# Pre-Phase-3 admin endpoint 2026-04-09 — manually rebuild the in-memory
# semantic_search index from the persistent knowledge.facts store.
#
# The index is in-memory and rebuilds on every machine restart. The
# startup-time bulk build is disabled via ARIA_SEMANTIC_INDEX_BUILD=0
# (set after a past GIL-contention incident on 2026-04-08 where the
# encode loop blocked uvicorn binding and broke fly health checks).
# Result: index sits at ~2 documents while knowledge.py has ~900 facts.
#
# Original implementation awaited the rebuild inline in the request,
# which times out on any HTTP client (~900 facts × 0.5-1s/fact encode
# = 8-15 minutes wall-clock). Now: spawns the rebuild as a background
# task and returns immediately with a job marker. Poll /semantic/stats
# to track progress.
_semantic_rebuild_state = {"running": False, "started_at": None, "facts_to_index": 0, "last_result": None}


@router.post("/admin/rebuild-semantic-index")
async def rebuild_semantic_index_ep():
    """Spawn a background rebuild of the semantic_search in-memory index
    from knowledge.facts. Returns immediately with a job marker. Safe
    to call multiple times — idempotent. Skips if a rebuild is already
    in progress (returns the existing state)."""
    import asyncio as _aio
    import time as _time
    from ..intel.semantic_search import rebuild_index_from_knowledge

    if _semantic_rebuild_state["running"]:
        return {
            "ok": True,
            "status": "already_running",
            "started_at": _semantic_rebuild_state["started_at"],
            "facts_to_index": _semantic_rebuild_state["facts_to_index"],
            "current_index": get_index_stats(),
        }

    kb = await knowledge_mod._load()
    facts = (kb or {}).get("facts", []) if isinstance(kb, dict) else []

    before_stats = get_index_stats()
    if not facts:
        return {
            "ok": True, "facts_to_index": 0,
            "before": before_stats, "after": before_stats,
            "note": "knowledge.facts is empty — nothing to rebuild",
        }

    _semantic_rebuild_state["running"] = True
    _semantic_rebuild_state["started_at"] = _time.time()
    _semantic_rebuild_state["facts_to_index"] = len(facts)

    async def _rebuild_bg():
        try:
            loop = _aio.get_running_loop()
            count = await loop.run_in_executor(None, rebuild_index_from_knowledge, facts)
            elapsed = _time.time() - _semantic_rebuild_state["started_at"]
            _log.info(
                "[admin/rebuild-semantic-index] background rebuild complete: "
                "%d facts indexed in %.1fs", count, elapsed,
            )
            _semantic_rebuild_state["last_result"] = {
                "ok": True, "facts_indexed": count, "elapsed_s": round(elapsed, 1),
            }
        except Exception as e:
            _log.error("[admin/rebuild-semantic-index] background rebuild raised: %s", e)
            _semantic_rebuild_state["last_result"] = {
                "ok": False, "error": f"{type(e).__name__}: {e}",
            }
        finally:
            _semantic_rebuild_state["running"] = False

    _aio.create_task(_rebuild_bg())

    return {
        "ok": True,
        "status": "started_background",
        "facts_to_index": len(facts),
        "before": before_stats,
        "note": (
            "Rebuild is running in the background. Poll "
            "/api/aria/semantic/stats to watch indexed_documents grow, "
            "or GET /api/aria/admin/rebuild-semantic-index/status for the job state. "
            "Expected duration: ~%d-%d seconds for %d facts."
        ) % (len(facts) // 2, len(facts), len(facts)),
    }


@router.get("/admin/rebuild-semantic-index/status")
async def rebuild_semantic_index_status_ep():
    """Returns the state of the most recent rebuild job (running, last
    result, current index size)."""
    return {
        "running": _semantic_rebuild_state["running"],
        "started_at": _semantic_rebuild_state["started_at"],
        "facts_to_index": _semantic_rebuild_state["facts_to_index"],
        "last_result": _semantic_rebuild_state["last_result"],
        "current_index": get_index_stats(),
    }


# ════════════════════════════════════════════════════════════════════════
# AUTONOMOUS ENGINE ADMIN ENDPOINTS (Phase 3c-α — added 2026-04-09)
# ════════════════════════════════════════════════════════════════════════
#
# Five endpoints to manage the autonomous research engine from outside
# the process. These give the operator (Antonio) an emergency stop, a
# manual fire button, a status view, and a tasks-yaml reload trigger
# without needing SSH access. Critical for incident response.
#
# All five are router-protected by the existing bearer-token dependency.
# See aria_service/autonomous/AUTONOMOUS_ENGINE.md for the full design.

@router.get("/autonomous/status")
async def autonomous_status_ep():
    """One-shot view of the autonomous engine state: env-var flags,
    in-process loop state, safety counters (rate limit + cost cap),
    loaded tasks summary, and the last 20 task run records."""
    try:
        from ..autonomous import engine as _eng, safety as _safety, tasks as _tsk
        engine_state = _eng.get_engine_status()
        safety_state = await _safety.get_safety_state()
        loaded = _tsk.get_loaded_tasks()
        tasks_summary = [
            {
                "id": t.id,
                "name": t.name,
                "cron": t.cron,
                "enabled": t.enabled,
                "priority": t.priority,
                "delivery_channels": t.delivery_channels,
                "paused": await _safety.is_task_paused(t.id),
            }
            for t in loaded.values()
        ]
        recent_runs = await _tsk.get_recent_runs(limit=20)
        return {
            "ok": True,
            "engine": engine_state,
            "safety": safety_state,
            "tasks_loaded": len(loaded),
            "tasks": tasks_summary,
            "recent_runs": recent_runs,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/autonomous/pause")
async def autonomous_pause_ep(request: Request):
    """Global pause switch — stops ALL tasks immediately. Resume with
    /autonomous/resume. Used as the emergency stop button when a task
    starts misbehaving and the operator needs to halt the engine
    without redeploying.
    """
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        reason = (body.get("reason") if isinstance(body, dict) else "") or "(no reason)"
        from ..autonomous import safety as _safety
        await _safety.pause_engine(reason=reason)
        return {"ok": True, "paused": True, "reason": reason}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/autonomous/resume")
async def autonomous_resume_ep():
    """Lift the global pause and let the engine fire scheduled tasks again.
    Per-task pauses are unaffected — use /autonomous/resume-task/<id>
    to lift those individually."""
    try:
        from ..autonomous import safety as _safety
        await _safety.resume_engine()
        return {"ok": True, "paused": False}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/autonomous/pause-task/{task_id}")
async def autonomous_pause_task_ep(task_id: str):
    """Pause a single task without stopping the engine. Useful when
    one task is failing but the others are healthy."""
    try:
        from ..autonomous import safety as _safety
        await _safety.pause_task(task_id)
        return {"ok": True, "task_id": task_id, "paused": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/autonomous/resume-task/{task_id}")
async def autonomous_resume_task_ep(task_id: str):
    """Lift the per-task pause."""
    try:
        from ..autonomous import safety as _safety
        await _safety.resume_task(task_id)
        return {"ok": True, "task_id": task_id, "paused": False}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/autonomous/run-now/{task_id}")
async def autonomous_run_now_ep(task_id: str, request: Request):
    """Manually fire a single task immediately, bypassing the cron and
    the per-task enabled flag. Safety guardrails (rate limit, cost cap,
    engine pause) STILL apply.

    The DRY_RUN env var still applies — to actually deliver to WhatsApp
    you must set ARIA_AUTONOMOUS_DRY_RUN=0 first. There is intentionally
    no per-call dry_run override on this endpoint — the override has to
    be a deliberate env var change so a curl typo cannot trigger a
    real WhatsApp post.
    """
    try:
        from ..autonomous import engine as _eng
        llm = get_llm(request)
        if llm is None or not getattr(llm, "is_configured", False):
            return {"ok": False, "error": "LLM provider not configured"}
        result = await _eng.run_task_now(task_id, llm)
        return result
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/autonomous/reload-tasks")
async def autonomous_reload_tasks_ep():
    """Re-read tasks.yaml from disk and replace the in-process task
    cache. Use this after editing tasks.yaml to apply changes without
    restarting the service."""
    try:
        from ..autonomous import tasks as _tsk
        loaded = _tsk.load_tasks()
        return {
            "ok": True,
            "tasks_loaded": len(loaded),
            "task_ids": sorted(loaded.keys()),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── OCR ──────────────────────────────────────────────────────────────────────

# GET /api/aria/vision-status — Diagnostic for image OCR configuration
@router.get("/vision-status")
async def vision_status_ep(request: Request):
    """Report whether ARIA can do image OCR right now and which backend will run.

    ARIA's image OCR is LOCAL-FIRST. She tries (in order):
        1. EasyOCR  (pure Python, no system binary, recommended for independence)
        2. Tesseract (needs Tesseract binary + pytesseract)
        3. Ollama vision model (auto-detected if running locally with llava/minicpm-v/etc)
        4. Cloud LLM vision (only if ARIA_VISION_PROVIDER + key are explicitly set)

    She is independent of any cloud LLM for image reading by default.
    """
    # ── Probe local backends ─────────────────────────────────────────────
    easyocr_available = False
    try:
        import easyocr  # noqa: F401
        easyocr_available = True
    except ImportError:
        pass

    tesseract_available = False
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        tesseract_available = True
    except ImportError:
        pass

    ollama_vision_model = await aria_ocr._detect_ollama_vision_model()

    # ── Probe optional cloud backend ─────────────────────────────────────
    cfg = aria_ocr.get_vision_config()
    llm = get_llm(request)
    fallback_inner = aria_ocr._unwrap_provider(llm) if llm else None
    fallback_provider_name = (getattr(fallback_inner, "name", "") or "").lower() if fallback_inner else None
    fallback_has_key = bool(getattr(fallback_inner, "_api_key", "")) if fallback_inner else False

    # Auto-install state
    install_status = aria_ocr.get_auto_install_status()

    # Determine which backend will actually run for the next image. OCR.space
    # is the always-on free public fallback so the chain ALWAYS has at least
    # one usable backend — there's no "none" state any more by default.
    if easyocr_available:
        active = "easyocr"
    elif tesseract_available:
        active = "tesseract"
    elif ollama_vision_model:
        active = f"ollama:{ollama_vision_model}"
    elif cfg["dedicated_provider_configured"]:
        active = f"cloud:{cfg['dedicated_provider']}"
    elif fallback_inner is not None and fallback_has_key:
        active = f"cloud:{fallback_provider_name}"
    else:
        # Free public fallback — always available, no setup
        active = "ocrspace_free"

    is_working = True  # OCR.space is always reachable as last resort

    # Setup hints — lead with the local options to nudge toward independence
    setup_instructions = []
    if active in ("ocrspace_free",):
        if install_status.get("started"):
            setup_instructions.append(
                "Auto-install of easyocr is RUNNING in the background — "
                "the next image will be served fully locally."
            )
        else:
            setup_instructions.append(
                "Currently using free public OCR fallback. For full independence, "
                "install local OCR: `pip install easyocr Pillow pytesseract` "
                "(or set ARIA_OCR_AUTO_INSTALL=1 to auto-install on next image)."
            )
    elif active == "tesseract" and not easyocr_available:
        setup_instructions.append(
            "Tip: `pip install easyocr` for higher-quality local OCR"
        )

    return {
        "ok": is_working,
        "independent": active in ("easyocr", "tesseract") or active.startswith("ollama:"),
        "active_backend": active,
        "auto_install": install_status,
        "local_backends": {
            "easyocr": easyocr_available,
            "tesseract": tesseract_available,
            "ollama_vision_model": ollama_vision_model,
        },
        "free_public_fallback": {
            "name": "OCR.space",
            "always_available": True,
            "max_image_size_mb": 1,
            "monthly_quota": "25,000 per IP",
        },
        "cloud_backend": {
            "dedicated_provider": cfg["dedicated_provider"],
            "dedicated_configured": cfg["dedicated_provider_configured"],
            "fallback_provider": fallback_provider_name,
            "fallback_available": fallback_inner is not None and fallback_has_key,
        },
        "main_llm": getattr(llm, "name", "?") if llm else None,
        "setup_instructions": setup_instructions,
        "philosophy": (
            "ARIA reads images out of the box via free public fallback, "
            "and auto-installs local OCR in the background so subsequent "
            "images become fully offline + independent."
        ),
    }


# 46. POST /api/aria/ocr — Extract text from image
@router.post("/ocr")
async def ocr_ep(request: Request):
    body = await request.json()
    image_b64 = body.get("image", "")
    filename = body.get("filename", "image.jpg")
    context = body.get("context", "")
    if not image_b64:
        raise HTTPException(status_code=400, detail="image (base64) required")
    import base64
    try:
        image_data = base64.b64decode(image_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")

    _log.info("OCR request: filename=%s size=%d bytes context=%s",
              filename, len(image_data), context[:120])

    llm = get_llm(request)
    result = await aria_ocr.extract_text_from_image(image_data, filename, context, llm)

    # Always log the outcome so we can debug "silent failures" from the logs
    if result.get("text"):
        _log.info("OCR success: method=%s chars=%d filename=%s",
                  result.get("method"), len(result["text"]), filename)
    else:
        _log.warning("OCR returned empty result for %s (size=%d). Method last tried: %s. "
                     "Backends tried: %s. Note: %s",
                     filename, len(image_data), result.get("method"),
                     result.get("tried"), result.get("note"))
    return result


# ── Report Generation ───────────────────────────────────────────────────────

# 48. POST /api/aria/reports/compliance-brief — Generate compliance intelligence brief
@router.post("/reports/compliance-brief")
async def compliance_brief_ep(request: Request):
    """Generate a compliance intelligence brief covering:
    - New sanctions updates (last 7 days)
    - Active export control changes
    - Country risk changes
    - Flagged entities
    - Open compliance hypotheses
    - Recommendations
    """
    llm = get_llm(request)
    if not llm or not llm.is_configured:
        raise HTTPException(status_code=503, detail="LLM not configured")

    # 1. Gather compliance-tagged facts from knowledge base
    kb_compliance = knowledge.search_knowledge(
        "sanctions export control compliance embargo ITAR EAR OFAC ECJU licence"
    )

    # 2. Get recent intel ledger signals tagged as compliance/sanctions/export
    ledger_compliance = intel_ledger.query_ledger(
        "sanctions compliance export control embargo arms licence regulation"
    )

    # 3. Get hypothesis status for compliance-related hypotheses
    all_hypotheses = await get_hypotheses()
    compliance_hyps = [
        h for h in all_hypotheses
        if any(kw in h.get("hypothesis", "").lower()
               for kw in ["sanction", "compliance", "export control", "embargo",
                           "licence", "itar", "ear", "ofac", "ecju", "regulation"])
    ]
    hyp_block = ""
    if compliance_hyps:
        hyp_block = "\n".join(
            f"- [{h['status']}] {h['hypothesis']}" for h in compliance_hyps[:10]
        )

    # 4. Ask LLM to synthesise into structured brief
    from datetime import datetime as _dt, timezone as _tz
    today = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    synth_prompt = f"""Generate a structured Arkmurus Compliance Intelligence Brief for {today}.

KNOWLEDGE BASE (compliance-related facts):
{kb_compliance or 'No compliance facts currently stored.'}

INTEL LEDGER (recent compliance/sanctions/export signals):
{ledger_compliance or 'No recent compliance signals.'}

COMPLIANCE HYPOTHESES:
{hyp_block or 'No active compliance hypotheses.'}

Produce the brief with EXACTLY these sections in markdown:

## Executive Summary
- (3 concise bullet points covering the most important compliance developments)

## New Sanctions & Export Control Updates
(Any new sanctions designations, export control changes, licence updates in the last 7 days. If none, state that the environment is stable.)

## Country Risk Updates
(Changes to country risk profiles relevant to Arkmurus target markets — Lusophone Africa, Nigeria, Kenya, Gulf, SE Asia, etc.)

## Entity Watchlist Changes
(New flagged entities, beneficial ownership changes, debarments, or PEP updates.)

## Open Issues & Recommendations
(Open compliance questions, recommended actions, upcoming deadlines, licence renewal reminders.)

Be specific with names, dates, and regulation references where available. Tag confidence levels.
If no data exists for a section, note it as "No updates — monitoring continues." Do NOT fabricate data."""

    try:
        result = await llm.complete(
            "You are ARIA — Arkmurus Research Intelligence Agent. Generate a compliance intelligence brief. Be precise, factual, and action-oriented.",
            synth_prompt,
            max_tokens=2000,
            timeout=60.0,
        )
        brief_md = result.text if result else "Brief generation failed."
    except Exception as e:
        _log.warning("Compliance brief generation failed: %s", e)
        brief_md = f"Brief generation failed: {e}"

    return {
        "ok": True,
        "date": today,
        "report_type": "compliance-brief",
        "brief": brief_md,
        "sources": {
            "kb_facts_matched": bool(kb_compliance),
            "ledger_signals_matched": bool(ledger_compliance),
            "compliance_hypotheses": len(compliance_hyps),
        },
    }


# 49. POST /api/aria/reports/entity-investigation — Deep investigation report on an entity
@router.post("/reports/entity-investigation")
async def entity_investigation_ep(request: Request):
    """Deep investigation report on an entity.
    Input: {entity_name, entity_type: "company"|"person"|"country"}
    Uses: deep_researcher.build_profile + sanctions screening + knowledge base
    Returns structured report.
    """
    body = await request.json()
    entity_name = body.get("entity_name", "")
    entity_type = body.get("entity_type", "company")  # company, person, country
    if not entity_name:
        raise HTTPException(status_code=400, detail="entity_name required")

    # Sanitise
    entity_name = re.sub(r"[^a-zA-Z0-9\s\-'.&]", "", entity_name)[:120]
    if entity_type not in ("company", "person", "country"):
        entity_type = "company"

    llm = get_llm(request)
    if not llm or not llm.is_configured:
        raise HTTPException(status_code=503, detail="LLM not configured")

    from datetime import datetime as _dt, timezone as _tz
    today = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    # 1. Build profile via deep researcher
    profile_result = await build_profile(llm, entity_name, entity_type)
    profile_text = profile_result.get("profile", profile_result.get("error", "No profile data."))

    # 2. Run sanctions screening via knowledge base
    sanctions_kb = knowledge.search_knowledge(
        f"{entity_name} sanctions embargo designated blocked SDN OFAC EU"
    )

    # 3. Check existing intel in knowledge base
    entity_kb = knowledge.search_knowledge(entity_name)

    # 4. Check intel ledger for signals mentioning this entity
    entity_ledger = intel_ledger.query_ledger(entity_name)

    # 5. Ask LLM to compile investigation report
    compile_prompt = f"""Generate an Arkmurus Entity Investigation Report for: {entity_name} (type: {entity_type}).

DEEP RESEARCH PROFILE:
{str(profile_text)[:4000]}

SANCTIONS SCREENING RESULTS:
{sanctions_kb or 'No sanctions matches found in knowledge base.'}

EXISTING INTELLIGENCE:
{entity_kb or 'No prior intelligence on this entity.'}

INTEL LEDGER SIGNALS:
{entity_ledger or 'No recent signals for this entity.'}

Produce the report with EXACTLY these sections in markdown:

## Entity Overview
(Who/what is this entity, location, key details, organisational structure.)

## Compliance Risk Assessment
(Overall risk rating: HIGH / MEDIUM / LOW with justification. Key risk factors.)

## Sanctions & Embargo Status
(Current sanctions status across OFAC, EU, UK, UN. Any secondary sanctions exposure. If clean, state so.)

## Known Associates / Beneficial Owners
(Key personnel, parent companies, subsidiaries, beneficial ownership chains. PEP connections.)

## Procurement Activity
(Known defence procurement contracts, tenders, awards, or commercial dealings. Historic pattern.)

## Recommended Actions
(Specific next steps for Arkmurus — due diligence actions, screening recommendations, engagement approach.)

Be specific. Use confidence tags: [CONFIRMED], [PROBABLE], [ASSESSED], [UNCERTAIN].
If no data for a section, state "No data available — further investigation recommended." Do NOT fabricate data."""

    try:
        result = await llm.complete(
            "You are ARIA — Arkmurus Research Intelligence Agent conducting an entity investigation. Be thorough, precise, and flag all compliance risks.",
            compile_prompt,
            max_tokens=2500,
            timeout=90.0,
        )
        report_md = result.text if result else "Report generation failed."
    except Exception as e:
        _log.warning("Entity investigation report failed: %s", e)
        report_md = f"Report generation failed: {e}"

    return {
        "ok": True,
        "date": today,
        "report_type": "entity-investigation",
        "entity_name": entity_name,
        "entity_type": entity_type,
        "report": report_md,
        "sources": {
            "profile_built": "error" not in profile_result,
            "profile_facts": profile_result.get("facts_learned", 0),
            "sanctions_kb_matched": bool(sanctions_kb),
            "existing_intel_matched": bool(entity_kb),
            "ledger_signals_matched": bool(entity_ledger),
        },
    }


# ── Compliance Screening ────────────────────────────────────────────────────

class ComplianceScreenRequest(BaseModel):
    entity_name: str
    product_description: str = ""
    destination_country: str = ""

# 50. POST /api/aria/compliance/screen — Combined compliance screening
@router.post("/compliance/screen")
async def compliance_screen_ep(req: ComplianceScreenRequest, request: Request):
    """
    Runs combined compliance assessment:
      1. Fuzzy sanctions match (via Node.js entityMatcher)
      2. Country risk assessment
      3. Product classification against ML categories
      4. Returns combined compliance assessment
    """
    entity = req.entity_name.strip()
    if not entity or len(entity) < 2:
        raise HTTPException(status_code=400, detail="entity_name required (min 2 chars)")

    product = req.product_description.strip()
    country = req.destination_country.strip().upper()

    # ── 1. Sanctions screening via Node.js brain endpoint ────────────────
    sanctions_result: dict[str, Any] = {"matched": False, "matches": [], "risk_level": "clear"}
    try:
        app_url = getattr(request.app.state, "app_url", "http://localhost:3117")
        token = getattr(request.app.state, "internal_token", "aria-internal")
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{app_url}/api/brain/compliance/screen-entity",
                json={"entity_name": entity},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                sanctions_result = resp.json()
    except Exception as e:
        _log.warning("Sanctions screening call failed: %s", e)
        sanctions_result["error"] = str(e)

    # ── 2. Country risk assessment ──────────────────────────────────────────
    EMBARGOED = {
        "RU", "BY", "IR", "KP", "SY", "CU", "VE",
        "CF", "CD", "ER", "IQ", "LY", "ML", "SO", "SS", "SD", "YE", "AF", "HT",
        "MM", "NI", "ZW", "CN",
    }
    HIGH_RISK = {"GW", "CM", "NE", "BF", "TD", "PK", "EG", "TR", "IN", "ET"}
    MEDIUM_RISK = {"AO", "MZ", "NG", "SN", "CI", "UG", "ID", "VN", "CO", "PE", "SA", "AE", "JO"}

    country_risk = "unknown"
    country_notes: list[str] = []
    if country:
        if country in EMBARGOED:
            country_risk = "embargoed"
            country_notes.append(f"{country} is under international arms embargo")
        elif country in HIGH_RISK:
            country_risk = "high"
            country_notes.append(f"{country} is a high-risk destination — enhanced due diligence required")
        elif country in MEDIUM_RISK:
            country_risk = "medium"
            country_notes.append(f"{country} requires standard due diligence")
        else:
            country_risk = "low"

    # ── 3. Product classification against ML categories ─────────────────────
    ML_KEYWORDS: dict[str, list[str]] = {
        "ML1":  ["small arms", "rifle", "pistol", "machine gun", "firearm"],
        "ML3":  ["ammunition", "cartridge", "round", "bullet", "fuze"],
        "ML4":  ["missile", "rocket", "torpedo", "bomb", "mine"],
        "ML6":  ["armoured vehicle", "armored vehicle", "apc", "ifv", "tank", "mrap"],
        "ML9":  ["vessel", "ship", "boat", "patrol", "naval", "submarine"],
        "ML10": ["aircraft", "helicopter", "uav", "drone", "fighter", "bomber"],
        "ML11": ["communication", "radio", "crypto", "c4isr", "command"],
        "ML13": ["armour", "armor", "body", "helmet", "protective", "vest"],
        "ML15": ["radar", "sensor", "electro-optical", "infrared", "lidar"],
        "ML22": ["training", "simulation", "advisory"],
    }
    product_lower = product.lower()
    matched_categories: list[str] = []
    for ml_code, keywords in ML_KEYWORDS.items():
        for kw in keywords:
            if kw in product_lower:
                matched_categories.append(ml_code)
                break

    product_classification = {
        "description": product or "(not provided)",
        "ml_categories": sorted(set(matched_categories)),
        "controlled": len(matched_categories) > 0,
        "note": "Product appears on UK Military List" if matched_categories else "No obvious ML classification detected — verify manually",
    }

    # ── 4. Combined assessment ──────────────────────────────────────────────
    blocked = False
    risk_factors: list[str] = []

    sanc_risk = sanctions_result.get("risk_level", "clear")
    if sanc_risk in ("critical", "high"):
        blocked = True
        risk_factors.append(f"Entity sanctions match: {sanc_risk}")
    elif sanc_risk == "medium":
        risk_factors.append("Entity has potential sanctions match — manual review required")

    if country_risk == "embargoed":
        blocked = True
        risk_factors.append(f"Destination country {country} is embargoed")
    elif country_risk == "high":
        risk_factors.append(f"Destination country {country} is high-risk")

    if matched_categories:
        risk_factors.append(f"Product matches ML categories: {', '.join(sorted(set(matched_categories)))}")

    if blocked:
        overall_status = "BLOCKED"
    elif risk_factors:
        overall_status = "REVIEW_REQUIRED"
    else:
        overall_status = "CLEAR"

    # Backward-compat mapping for WhatsApp/Telegram clients
    result_label = {"CLEAR": "PERMITTED", "REVIEW_REQUIRED": "REVIEW", "BLOCKED": "BLOCKED"}.get(overall_status, overall_status)
    screened_against = {
        "Sanctions (entity)": sanctions_result.get("risk_level", "checked"),
        "Country risk": country_risk,
        "Product classification": ", ".join(product_classification.get("ml_categories", [])) or "no ML match",
    }

    return {
        "status": overall_status,
        "result": result_label,
        "screened_against": screened_against,
        "entity": entity,
        "sanctions": sanctions_result,
        "country_risk": {
            "country": country or "(not provided)",
            "risk_level": country_risk,
            "notes": country_notes,
        },
        "product_classification": product_classification,
        "risk_factors": risk_factors,
        "blocked": blocked,
        "disclaimer": "Automated pre-screen only. Formal export control and sanctions advice required before proceeding.",
        "screened_at": datetime.now(timezone.utc).isoformat() + "Z",
    }


# ── Hypothesis Validation (batch) ──────────────────────────────────────────

@router.post("/research/validate-hypotheses")
async def validate_hypotheses_batch_ep(request: Request):
    llm = get_llm(request)
    hypotheses = await get_hypotheses()
    if not hypotheses:
        return {"validated": 0, "results": [], "message": "No hypotheses to validate"}
    # Pick top 3 oldest OPEN hypotheses
    open_h = [h for h in hypotheses if h.get("status") == "OPEN"]
    # Sort oldest first (by created_at ascending)
    open_h.sort(key=lambda h: h.get("created_at", ""))
    targets = open_h[:3]
    if not targets:
        return {"validated": 0, "results": [], "message": "No OPEN hypotheses to validate"}
    results = []
    for h in targets:
        try:
            r = await validate_hypothesis(llm, h["hypothesis"])
            results.append(r)
        except Exception as e:
            results.append({"hypothesis": h.get("hypothesis", ""), "error": str(e)})
    return {"validated": len(results), "results": results}


# ── Conversation Search & Export ─────────────────────────────────────────────

@router.get("/conversations/search")
async def search_conversations(q: str = "", limit: int = 50):
    """Full-text search across ARIA conversation history."""
    keys = await rs.scan_keys("crucix:aria:session:*", count=500)
    if not keys:
        return {"results": [], "total": 0, "query": q}

    results = []
    query_lower = q.lower().strip()

    for key in keys:
        try:
            session = await rs.get_json(key)
            if not session or not isinstance(session, dict):
                continue
            messages = session.get("messages", [])
            if not messages:
                continue

            session_id = key.replace("crucix:aria:session:", "")

            # If no query, return all sessions with metadata
            if not query_lower:
                results.append({
                    "session_id": session_id,
                    "message_count": len(messages),
                    "created_at": session.get("createdAt"),
                    "preview": (messages[0].get("content", "") or "")[:120] if messages else "",
                })
                if len(results) >= limit:
                    break
                continue

            # Search messages for query match
            matching_messages = []
            for i, msg in enumerate(messages):
                content = msg.get("content", "") or ""
                if query_lower in content.lower():
                    matching_messages.append({
                        "index": i,
                        "role": msg.get("role", "unknown"),
                        "content": content[:300],
                    })

            if matching_messages:
                results.append({
                    "session_id": session_id,
                    "message_count": len(messages),
                    "created_at": session.get("createdAt"),
                    "matches": len(matching_messages),
                    "matching_messages": matching_messages[:5],
                })
                if len(results) >= limit:
                    break
        except Exception as e:
            _log.warning("Session search error for %s: %s", key, e)
            continue

    return {"results": results, "total": len(results), "query": q}


@router.get("/conversations/export")
async def export_conversation(session_id: str, format: str = "json"):
    """Export a conversation transcript."""
    key = f"crucix:aria:session:{session_id}"
    session = await rs.get_json(key)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = session.get("messages", [])

    if format == "text":
        lines = [
            f"ARIA Conversation Transcript — Session: {session_id}",
            f"Created: {session.get('createdAt', 'unknown')}",
            f"Messages: {len(messages)}",
            "=" * 72,
            "",
        ]
        for msg in messages:
            role = (msg.get("role", "unknown")).upper()
            content = msg.get("content", "")
            lines.append(f"[{role}]")
            lines.append(content)
            lines.append("")
        text_output = "\n".join(lines)
        return Response(
            content=text_output,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="conversation_{session_id}.txt"'},
        )

    # Default: JSON
    return {
        "session_id": session_id,
        "created_at": session.get("createdAt"),
        "message_count": len(messages),
        "messages": messages,
    }


# ── Compliance sub-endpoints (used by WhatsApp / Telegram interfaces) ───────

class ClassifyRequest(BaseModel):
    description: str

@router.post("/compliance/classify")
async def compliance_classify_ep(req: ClassifyRequest):
    """Classify a product description against UK Military List categories."""
    desc = (req.description or "").strip()
    if not desc or len(desc) < 3:
        raise HTTPException(status_code=400, detail="description required (min 3 chars)")

    ML_KEYWORDS: dict[str, tuple[str, list[str]]] = {
        "ML1":  ("Smooth-bore weapons (small arms)",        ["small arms", "rifle", "pistol", "machine gun", "firearm", "carbine", "shotgun"]),
        "ML2":  ("Smooth-bore weapons of calibre 20mm+",    ["mortar", "cannon", "howitzer", "artillery", "field gun"]),
        "ML3":  ("Ammunition and fuze setting",             ["ammunition", "cartridge", "round", "bullet", "fuze", "shell"]),
        "ML4":  ("Bombs, torpedoes, rockets, missiles",     ["missile", "rocket", "torpedo", "bomb", "mine", "warhead", "atgm", "manpads"]),
        "ML5":  ("Fire control, surveillance",              ["fire control", "targeting", "laser designator", "rangefinder"]),
        "ML6":  ("Ground vehicles & components",            ["armoured vehicle", "armored vehicle", "apc", "ifv", "tank", "mrap", "humvee"]),
        "ML7":  ("CBRN agents",                             ["chemical", "biological", "toxin", "nerve agent", "riot control"]),
        "ML8":  ("Energetic materials",                     ["explosive", "propellant", "detonator", "rdx", "tnt", "c4"]),
        "ML9":  ("Vessels of war",                          ["vessel", "warship", "patrol boat", "naval", "submarine", "frigate", "corvette"]),
        "ML10": ("Aircraft & components",                   ["aircraft", "helicopter", "uav", "drone", "fighter", "bomber", "rotorcraft"]),
        "ML11": ("Electronic equipment",                    ["communication", "radio", "crypto", "c4isr", "command and control", "jammer"]),
        "ML13": ("Body armour & protective gear",           ["armour", "armor", "body armour", "helmet", "ballistic vest", "protective"]),
        "ML15": ("Imaging & countermeasure equipment",      ["radar", "sensor", "electro-optical", "infrared", "thermal", "lidar", "night vision"]),
        "ML22": ("Training & advisory",                     ["training", "simulation", "advisory", "instructor"]),
    }

    lower = desc.lower()
    classifications = []
    for code, (label, kws) in ML_KEYWORDS.items():
        for kw in kws:
            if kw in lower:
                classifications.append({
                    "code": code,
                    "category": label,
                    "description": label,
                    "controlled": True,
                    "matched_keyword": kw,
                    "confidence": 0.85 if len(kw) > 6 else 0.70,
                })
                break

    if not classifications:
        classifications.append({
            "code": "UNCLASSIFIED",
            "category": "No obvious ML category",
            "description": "Item does not match any known UK Military List keywords. Manual review required.",
            "controlled": False,
            "confidence": 0.30,
        })

    return {
        "input": desc,
        "classifications": classifications,
        "result": classifications[0]["code"],
        "category": classifications[0]["category"],
        "disclaimer": "Keyword classification only — formal export control assessment required.",
    }


class SanctionsRequest(BaseModel):
    name: str

@router.post("/compliance/sanctions")
async def compliance_sanctions_ep(req: SanctionsRequest, request: Request):
    """Sanctions check — proxies to Node entityMatcher and falls back to knowledge base."""
    name = (req.name or "").strip()
    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="name required (min 2 chars)")

    matches: list[dict] = []
    error = None
    try:
        app_url = getattr(request.app.state, "app_url", "http://localhost:3117")
        token = getattr(request.app.state, "internal_token", "aria-internal")
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{app_url}/api/brain/compliance/screen-entity",
                json={"entity_name": name},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for m in (data.get("matches") or []):
                    matches.append({
                        "name": m.get("name") or m.get("entity") or name,
                        "list": m.get("list") or m.get("source") or "Sanctions list",
                        "score": m.get("score") or m.get("confidence"),
                        "reason": m.get("reason") or m.get("notes") or "",
                    })
    except Exception as e:
        error = str(e)
        _log.warning("Sanctions check upstream failed: %s", e)

    # Knowledge-base fallback — search for the entity in stored intel
    kb_hits = knowledge.search_knowledge(name) or ""
    kb_flagged = bool(kb_hits and re.search(r"sanction|embargo|ofac|ofsi|debarred", kb_hits, re.IGNORECASE))
    if kb_flagged:
        matches.append({
            "name": name,
            "list": "ARIA knowledge base",
            "score": 0.6,
            "reason": "Mentioned alongside sanctions/embargo terms in stored intelligence",
        })

    return {
        "name": name,
        "matches": matches,
        "results": matches,
        "match_count": len(matches),
        "clear": len(matches) == 0,
        "error": error,
        "disclaimer": "Pre-screen only. Verify against authoritative lists (OFAC, OFSI, EU, UN) before any commercial action.",
    }


class RiskRequest(BaseModel):
    country: str

@router.post("/compliance/risk")
async def compliance_risk_ep(req: RiskRequest):
    """Country risk assessment — sanctions regimes, embargoes, ML risk tier."""
    country_input = (req.country or "").strip()
    if not country_input or len(country_input) < 2:
        raise HTTPException(status_code=400, detail="country required (min 2 chars)")

    # Country name → ISO2 (best-effort)
    NAME_TO_ISO = {
        "russia": "RU", "belarus": "BY", "iran": "IR", "north korea": "KP", "syria": "SY",
        "cuba": "CU", "venezuela": "VE", "central african republic": "CF", "car": "CF",
        "drc": "CD", "congo": "CD", "eritrea": "ER", "iraq": "IQ", "libya": "LY",
        "mali": "ML", "somalia": "SO", "south sudan": "SS", "sudan": "SD", "yemen": "YE",
        "afghanistan": "AF", "haiti": "HT", "myanmar": "MM", "burma": "MM", "nicaragua": "NI",
        "zimbabwe": "ZW", "china": "CN",
        "guinea-bissau": "GW", "guinea bissau": "GW", "cameroon": "CM", "niger": "NE",
        "burkina faso": "BF", "chad": "TD", "pakistan": "PK", "egypt": "EG", "turkey": "TR",
        "india": "IN", "ethiopia": "ET",
        "angola": "AO", "mozambique": "MZ", "nigeria": "NG", "senegal": "SN", "ivory coast": "CI",
        "côte d'ivoire": "CI", "uganda": "UG", "indonesia": "ID", "vietnam": "VN", "colombia": "CO",
        "peru": "PE", "saudi arabia": "SA", "uae": "AE", "united arab emirates": "AE", "jordan": "JO",
    }
    iso = country_input.upper() if len(country_input) == 2 else NAME_TO_ISO.get(country_input.lower(), country_input.upper()[:2])

    EMBARGOED = {"RU","BY","IR","KP","SY","CU","VE","CF","CD","ER","IQ","LY","ML","SO","SS","SD","YE","AF","HT","MM","NI","ZW","CN"}
    HIGH_RISK = {"GW","CM","NE","BF","TD","PK","EG","TR","IN","ET"}
    MEDIUM_RISK = {"AO","MZ","NG","SN","CI","UG","ID","VN","CO","PE","SA","AE","JO"}

    if iso in EMBARGOED:
        level, score = "HIGH", 90
        regimes = ["UN SC", "EU restrictive measures", "UK OFSI"]
        notes = f"{country_input} is subject to international arms embargo. Most defence exports prohibited or require explicit Government licence."
    elif iso in HIGH_RISK:
        level, score = "HIGH", 70
        regimes = ["Enhanced due diligence required"]
        notes = f"{country_input} is high-risk. End-user verification, diversion risk assessment, and SITCL likely required."
    elif iso in MEDIUM_RISK:
        level, score = "MEDIUM", 45
        regimes = ["Standard licensing"]
        notes = f"{country_input} permits standard defence exports but requires SITCL with end-use certificate."
    else:
        level, score = "LOW", 20
        regimes = []
        notes = f"{country_input} is a low-risk destination. Standard export controls apply."

    return {
        "country": country_input,
        "iso": iso,
        "risk_level": level,
        "level": level,
        "score": score,
        "sanctions_regimes": regimes,
        "embargoes": ["UN/EU/UK arms embargo"] if iso in EMBARGOED else [],
        "export_controls": "SITCL + end-user certificate" if level != "LOW" else "Standard SITCL",
        "notes": notes,
        "disclaimer": "Advisory only. Confirm with current Foreign Office guidance and ECJU rating before action.",
    }


# ── Proactive endpoints (strategic ideas, lead hunting) ──────────────────────

@router.post("/proactive/strategic-ideas")
async def proactive_strategic_ideas_ep(request: Request):
    """Generate strategic ideas for Arkmurus based on current intel."""
    llm = get_llm(request)
    if not llm or not llm.is_configured:
        return {"ideas": "⚠️ ARIA LLM not configured.", "error": True}

    intel = get_intel_data(request)
    intel_summary = ""
    if intel:
        opps = (intel.get("opportunities") or [])[:5]
        if opps:
            intel_summary = "Current top opportunities:\n" + "\n".join(
                f"- {o.get('market')}: score {o.get('score')}/100, tier {o.get('tier')}"
                for o in opps
            )

    prompt = f"""You are ARIA. Generate 5 distinct, actionable strategic ideas for Arkmurus this week.

{intel_summary}

For each idea provide:
1. The idea (one sentence)
2. Why now (specific trigger)
3. First concrete action (within 48 hours)
4. Expected outcome
5. Risk / compliance flags

Be bold, specific, and commercially realistic. Reference Arkmurus's relationship tiers (Incumbent in Lusophone Africa; Established in SA/Kenya/Nigeria; Developing/Cold-entry elsewhere)."""

    try:
        result = await llm.complete(
            "ARIA — strategic ideation for defence procurement broker.",
            prompt,
            max_tokens=2000,
            timeout=90.0,
        )
        return {"ideas": result.text, "generated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"ideas": f"⚠️ Generation failed: {e}", "error": True}


# ── Fuzzy sanctions screening (OpenSanctions + Levenshtein + Metaphone) ─────

class FuzzyScreenRequest(BaseModel):
    name: str
    aliases: list[str] | None = None
    threshold: float = 0.78

@router.post("/sanctions/fuzzy")
async def sanctions_fuzzy_ep(req: FuzzyScreenRequest):
    """Fuzzy entity sanctions screening with name-variant generation.

    Catches transliteration, acronym, and obfuscation attempts that exact-match
    screens miss. Backed by the OpenSanctions consolidated dataset.
    """
    if not req.name or len(req.name.strip()) < 2:
        raise HTTPException(status_code=400, detail="name required (min 2 chars)")
    if req.aliases:
        return await aria_sanctions.screen_with_aliases(req.name, req.aliases)
    return await aria_sanctions.fuzzy_screen(req.name, threshold=req.threshold)


# ── Conflict / kinetic event tracking (ACLED + GDELT fallback) ──────────────

@router.get("/conflict/events/{country}")
async def conflict_events_ep(country: str, days: int = 30, limit: int = 50):
    """Recent conflict / political-violence events for a country.

    Pulls from ACLED if ACLED_API_KEY is set, otherwise falls back to GDELT.
    Returns total events, fatalities, escalation score, and recent event list.
    """
    days = max(1, min(days, 180))
    limit = max(1, min(limit, 200))
    return await conflict_tracker.get_recent_events(country, days=days, limit=limit)


@router.get("/conflict/correlate/{country}")
async def conflict_correlate_ep(country: str, days: int = 60):
    """Cross-reference conflict escalation with projected procurement demand.

    Returns capability projections and a procurement-window assessment ARIA can
    use to inform BD timing recommendations.
    """
    days = max(7, min(days, 180))
    return await conflict_tracker.correlate_with_procurement(country, days=days)


# ── Technical classifier (calibres, weapon systems, ML/ECCN/HS) ─────────────

class TechClassifyRequest(BaseModel):
    text: str

@router.post("/tech/classify")
async def tech_classify_ep(req: TechClassifyRequest):
    """Extract structured defence items from free text — calibres, systems,
    ML categories, quantities, and embargo risks."""
    if not req.text or len(req.text.strip()) < 5:
        raise HTTPException(status_code=400, detail="text required (min 5 chars)")
    return tech_classifier.classify_text(req.text)


@router.get("/tech/explain/{designation}")
async def tech_explain_ep(designation: str):
    """Look up a single weapon system designation in the technical database."""
    return tech_classifier.explain_item(designation)


# ── Knowledge contradictions (metacognitive self-correction) ────────────────

@router.get("/knowledge/contradictions")
async def knowledge_contradictions_ep(limit: int = 50):
    """Return facts that have detected contradictions or version history.

    This is what powers ARIA's "I used to think X, now Y" reasoning.
    """
    limit = max(1, min(limit, 200))
    contradictions = await knowledge_mod.get_contradictions(limit=limit)
    return {"count": len(contradictions), "contradictions": contradictions}


@router.post("/proactive/lead-hunt")
async def proactive_lead_hunt_ep(request: Request):
    """Run a lead-hunting cycle — identify fresh procurement opportunities."""
    llm = get_llm(request)
    if not llm or not llm.is_configured:
        return {"leads": "⚠️ ARIA LLM not configured.", "error": True}

    intel = get_intel_data(request)
    tenders = ((intel or {}).get("procurementTenders") or {}).get("items") or []
    tender_block = ""
    if tenders:
        tender_block = "Recent tenders detected:\n" + "\n".join(
            f"- {t.get('title') or t.get('text', '')[:100]} [{t.get('source', '')}]"
            for t in tenders[:8]
        )

    prompt = f"""You are ARIA on a lead-hunting cycle. Identify the 5 strongest defence procurement leads Arkmurus should pursue right now.

{tender_block}

For each lead:
- Market (country) and buyer (specific ministry/directorate)
- Requirement (what they need)
- Window (procurement cycle stage, decision timeline)
- Arkmurus angle (relationship tier + which OEM partner)
- Win probability (0-100%)
- Compliance flags (sanctions, end-use, export control)
- First action (specific, within 48 hours)

Prioritise: Lusophone Africa (incumbent advantage), then established markets where Arkmurus has contacts, then high-value cold-entry where there's a clear angle."""

    try:
        result = await llm.complete(
            "ARIA — defence procurement lead generation specialist.",
            prompt,
            max_tokens=2500,
            timeout=120.0,
        )
        return {"leads": result.text, "generated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"leads": f"⚠️ Lead hunt failed: {e}", "error": True}


# ── METACOGNITIVE ADMIN ENDPOINTS ──────────────────────────────────────────
# Phase 3 metacognitive engine — self-assessment, gap detection, calibration,
# consciousness mapping. All behind the same bearer token auth as the rest
# of the router.

@router.get("/metacognitive/status")
async def metacognitive_status():
    """GET /api/aria/metacognitive/status — full metacognitive engine state."""
    try:
        from ..metacognitive import consciousness, calibration, engine as metacog_engine, cycle
        state = await consciousness.get_consciousness_state()
        cycle_status = await cycle.get_cycle_status()
        return {
            "enabled": metacog_engine.is_enabled(),
            "consciousness_state": state,
            "cycle_status": cycle_status,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/metacognitive/calibration")
async def metacognitive_calibration():
    """GET /api/aria/metacognitive/calibration — full Brier scoring report."""
    try:
        from ..metacognitive import calibration
        report = await calibration.get_full_calibration_report()
        recent = await calibration.get_recent_assessments(limit=10)
        return {"report": report, "recent_assessments": recent}
    except Exception as e:
        return {"error": str(e)}


@router.get("/metacognitive/assessments")
async def metacognitive_assessments(limit: int = 20):
    """GET /api/aria/metacognitive/assessments — recent self-assessment records."""
    try:
        from ..metacognitive import engine as metacog_engine
        records = await metacog_engine.get_recent_assessments(limit=limit)
        stats = await metacog_engine.get_assessment_stats()
        return {"assessments": records, "stats": stats}
    except Exception as e:
        return {"error": str(e)}


@router.get("/metacognitive/gaps")
async def metacognitive_gaps(limit: int = 30):
    """GET /api/aria/metacognitive/gaps — detected knowledge + methodology gaps."""
    try:
        from ..metacognitive import gaps
        knowledge_gaps = await gaps.get_recent_gaps(limit=limit)
        corrections = await gaps.get_recent_corrections(limit=20)
        methodology = await gaps.get_recent_methodology_updates(limit=20)
        docs_read = await gaps.get_documents_read(limit=20)
        return {
            "knowledge_gaps": knowledge_gaps,
            "corrections": corrections,
            "methodology_updates": methodology,
            "documents_read": docs_read,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/metacognitive/consciousness")
async def metacognitive_consciousness(limit: int = 3):
    """GET /api/aria/metacognitive/consciousness — recent consciousness reports."""
    try:
        from ..metacognitive import consciousness
        reports = await consciousness.get_recent_reports(limit=limit)
        profiles = await consciousness.get_all_profiles()
        return {"reports": reports, "capability_profiles": profiles}
    except Exception as e:
        return {"error": str(e)}


class ReadAndLearnRequest(BaseModel):
    document: str
    doc_type: str = "general"
    domain: str = "general"


@router.post("/metacognitive/read-and-learn")
async def metacognitive_read_and_learn(
    req: ReadAndLearnRequest,
    llm=Depends(get_llm),
):
    """POST /api/aria/metacognitive/read-and-learn — ARIA reads a document
    and performs gap detection against her capability profile."""
    try:
        from ..metacognitive import gaps
        result = await gaps.detect_gaps_from_document(
            document_content=req.document,
            doc_type=req.doc_type,
            domain=req.domain,
            llm=llm,
        )
        return result
    except Exception as e:
        return {"error": str(e)}


class RecordAssessmentRequest(BaseModel):
    assessment_id: str
    domain: str
    claim: str
    stated_confidence: float
    outcome: Optional[bool] = None


@router.post("/metacognitive/record-assessment")
async def metacognitive_record_assessment(req: RecordAssessmentRequest):
    """POST /api/aria/metacognitive/record-assessment — record a prediction
    for Brier scoring."""
    try:
        from ..metacognitive import calibration
        record = await calibration.record_assessment(
            assessment_id=req.assessment_id,
            domain=req.domain,
            claim=req.claim,
            stated_confidence=req.stated_confidence,
            outcome=req.outcome,
        )
        return {"ok": True, "record": record}
    except Exception as e:
        return {"error": str(e)}


class ResolveAssessmentRequest(BaseModel):
    assessment_id: str
    outcome: bool


@router.post("/metacognitive/resolve-assessment")
async def metacognitive_resolve_assessment(req: ResolveAssessmentRequest):
    """POST /api/aria/metacognitive/resolve-assessment — resolve a prediction
    with its actual outcome for Brier scoring."""
    try:
        from ..metacognitive import calibration
        record = await calibration.resolve_assessment(
            assessment_id=req.assessment_id,
            outcome=req.outcome,
        )
        if record:
            return {"ok": True, "record": record}
        return {"ok": False, "error": "Assessment not found"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/metacognitive/run-daily")
async def metacognitive_run_daily(llm=Depends(get_llm)):
    """POST /api/aria/metacognitive/run-daily — manually trigger the daily
    self-check cycle."""
    try:
        from ..metacognitive import cycle
        result = await cycle.daily_self_check(llm)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"error": str(e)}


@router.post("/metacognitive/run-weekly")
async def metacognitive_run_weekly(llm=Depends(get_llm)):
    """POST /api/aria/metacognitive/run-weekly — manually trigger the weekly
    consciousness review."""
    try:
        from ..metacognitive import cycle
        result = await cycle.weekly_consciousness_review(llm)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"error": str(e)}


@router.post("/metacognitive/run-monthly")
async def metacognitive_run_monthly(llm=Depends(get_llm)):
    """POST /api/aria/metacognitive/run-monthly — manually trigger the monthly
    gap-closure sprint."""
    try:
        from ..metacognitive import cycle
        result = await cycle.monthly_gap_closure_sprint(llm, top_n_gaps=3)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/metacognitive/codegen/pending")
async def metacognitive_codegen_pending(limit: int = 10):
    """GET /api/aria/metacognitive/codegen/pending — pending self-improvement
    code proposals."""
    try:
        from ..metacognitive import self_improvement_codegen as codegen
        proposals = await codegen.get_pending_proposals(limit=limit)
        return {"proposals": proposals}
    except Exception as e:
        return {"error": str(e)}
