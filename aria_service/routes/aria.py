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
from ..intel import knowledge as knowledge_mod
from ..intel import self_improve
from ..intel import ocr as aria_ocr
from ..intel.semantic_search import semantic_search, get_index_stats

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
    llm = get_llm(request)
    result = await aria_ocr.extract_text_from_image(image_data, filename, context, llm)
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

    from datetime import datetime as _dt, timezone as _tz
    return {
        "status": overall_status,
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
        "screened_at": _dt.now(_tz.utc).isoformat() + "Z",
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
