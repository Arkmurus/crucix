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

import logging
_log = logging.getLogger("aria.routes")

router = APIRouter(prefix="/api/aria", tags=["aria"])

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

class ThinkRequest(BaseModel):
    question: str
    context: dict | None = None
    fast: bool = False

class FactRequest(BaseModel):
    topic: str
    content: str
    confidence: str = "CONFIRMED"

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
    await knowledge.store_fact(req.topic, req.content, "user", req.confidence)
    return {"ok": True, "message": "Fact stored"}


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


class FeedbackReactionRequest(BaseModel):
    chat_id: str
    msg_id: str
    emoji: str
    reactor: str = ""
    reactor_jid: str = ""


@router.post("/feedback/snapshot")
async def feedback_snapshot_ep(req: FeedbackSnapshotRequest):
    """Persist the Q→A pair for an ARIA reply so a later WhatsApp reaction
    can retrieve the context. Called by the WA listener after sending."""
    return await feedback_store.snapshot_reply(
        chat_id=req.chat_id,
        msg_id=req.msg_id,
        question=req.question,
        answer=req.answer,
        user=req.user,
        group_name=req.group_name,
        metadata=req.metadata,
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
    except Exception:
        pass
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
    except Exception:
        pass
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

# Imperative verbs that signal "go do something" rather than "answer me"
_INVESTIGATE_KW = re.compile(r"\b(investigate|research|look\s+into|dig\s+into|find\s+out\s+about|deep[\-\s]?dive|do\s+a\s+deep\s+dive|tell\s+me\s+everything\s+about|explore)\b", re.IGNORECASE)
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
    r"south\s+africa|libya|egypt|iraq|syria|yemen|afghanistan|ukraine|russia|"
    r"saudi\s+arabia|uae|jordan|lebanon|colombia|venezuela|haiti|myanmar)\b",
    re.IGNORECASE,
)


def _detect_tool_intent(message: str) -> dict | None:
    """Parse free-form text into a structured tool call. Returns None if no tool intent."""
    msg = message.strip()
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

    # 1. Explicit crawl request — needs a URL
    if has_crawl and url:
        return {"tool": "crawl", "url": url, "context": msg}

    # 2. Investigate / research / look-into a URL → CRAWL the actual website
    # This routes "research wearwiser.com" / "tell me about acme.io" / "look
    # into example.com" to crawl_website() which spiders the real site, NOT
    # to investigate() which only does Google News searches around the URL.
    # The previous version sent these to investigate() and got 0-2 facts
    # because the URL itself wasn't a news article — it was a company root.
    if has_investigate and url:
        return {"tool": "crawl", "url": url, "context": msg, "max_pages": 15}

    # 3. Read / summarise URL — bare URL with no verb, or explicit "read this"
    if (has_read and url) or (url and not (has_investigate or has_crawl or has_profile)):
        return {"tool": "read", "url": url, "context": msg}

    # 4. Investigate topic (no URL) — extract topic after the verb
    if has_investigate:
        verb_match = _INVESTIGATE_KW.search(msg)
        topic = msg[verb_match.end():].strip(" .,:;-?!\"'")
        topic = re.sub(r"^(this|that|the|a|an|on|about|into|of)\s+", "", topic, flags=re.IGNORECASE)
        if topic and len(topic) >= 3:
            return {"tool": "investigate", "topic": topic[:200], "context": msg}

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

    return None


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
            return (
                f"\n\n[TOOL: crawl_website]\n"
                f"URL: {intent['url']}\n"
                f"Pages crawled: {pages}\n"
                f"Facts learned: {facts}\n"
                f"Top extracted facts:\n{facts_preview}"
            )

        if tool == "read":
            r = await read_article(llm, intent["url"], intent.get("context", ""))
            return f"\n\n[TOOL: read_article]\nURL: {intent['url']}\nFacts learned: {r.get('facts_learned', 0)}\nSummary: {(r.get('summary') or r.get('analysis') or '')[:1500]}"

        if tool in ("investigate", "investigate_url"):
            topic = intent.get("topic") or intent.get("url", "")
            r = await investigate(llm, topic, depth="thorough")
            synth = r.get("synthesis") or {}
            findings = synth.get("key_findings") or []
            actions = synth.get("recommended_actions") or []
            return (
                f"\n\n[TOOL: investigate]\nTopic: {topic}\n"
                f"Articles read: {r.get('articles_read', 0)} | Facts: {r.get('facts_learned', 0)}\n"
                f"Key findings: {json.dumps(findings)[:1200]}\n"
                f"Recommended actions: {json.dumps(actions)[:800]}"
            )

        if tool == "profile":
            r = await build_profile(llm, intent["entity"], intent.get("ptype", "auto"))
            return f"\n\n[TOOL: build_profile]\nEntity: {intent['entity']}\nResult: {json.dumps(r, default=str)[:2000]}"

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
    llm = get_llm(request)
    intel = get_intel_data(request)

    # ── NLU tool-use: detect investigative intent and run tools first ────────
    tool_context = ""
    tool_used = None
    if req.auto_tools:
        intent = _detect_tool_intent(req.message)
        if intent and llm and llm.is_configured:
            tool_used = intent.get("tool")
            _log.info("ARIA chat tool-use detected: %s", intent)
            tool_context = await _execute_tool(intent, llm)

    # If a tool ran, prepend the result to the message so the LLM sees the data
    message_for_llm = req.message
    if tool_context:
        message_for_llm = (
            f"{req.message}\n\n"
            f"[I have already run the appropriate tool on your request. "
            f"Use the data below to answer comprehensively, cite specific findings, "
            f"and end with a clear recommendation.]"
            f"{tool_context}"
        )

    result = await aria_chat(message_for_llm, session_id, llm, intel)
    if tool_used:
        result["tool_used"] = tool_used
    return result


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
