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

router = APIRouter(prefix="/api/aria", tags=["aria"])


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
    return await intel_ledger.get_country_situation(country)


# 8. GET /api/aria/contacts
@router.get("/contacts")
async def contacts_ep():
    all_c = await contacts.get_all()
    return {"contacts": all_c}


# 9. GET /api/aria/contacts/country/{country}
@router.get("/contacts/country/{country}")
async def contacts_country_ep(country: str):
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
