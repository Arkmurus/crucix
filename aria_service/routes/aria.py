"""
ARIA API Routes — all 18 endpoints matching the Node.js API surface.
"""
from __future__ import annotations

import json
import re
import uuid
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
)
from ..intel import neural_memory
from ..intel import self_improve

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


# 18. POST /api/aria/chat
@router.post("/chat")
async def chat_ep(req: ChatRequest, request: Request):
    if not req.message:
        raise HTTPException(status_code=400, detail="message required")
    session_id = req.session_id or str(uuid.uuid4())[:12]
    llm = get_llm(request)
    intel = get_intel_data(request)
    result = await aria_chat(req.message, session_id, llm, intel)
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


# 25. POST /api/aria/read-document — Read a document (text content from any format)
@router.post("/read-document")
async def read_document_ep(request: Request):
    body = await request.json()
    content = body.get("content", "")
    filename = body.get("filename", "unknown")
    source = body.get("source", "document")
    context = body.get("context", "")
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
