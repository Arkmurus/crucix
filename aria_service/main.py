"""
ARIA Service — FastAPI entrypoint.

Runs the complete ARIA intelligence engine as a standalone Python service.
Replaces both the Node.js lib/aria/ and the Flask brain/ service.

Usage:
    python -m aria_service.main
    # or
    uvicorn aria_service.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .llm.factory import create_llm_provider
from .llm.fallback import create_fallback_chain
from .intel import redis_store as rs
from .intel import knowledge, intel_ledger, contacts, competitors, training_data, neural_memory
from .intel import self_improve
from .intel import student
from .intel import reasoning_library
from .intel import proactive
from .intel import rag_store
from .intel import ocr as ocr_module
from .intel.researcher import research_and_learn, get_hypotheses, validate_hypothesis
from .routes.aria import router as aria_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("aria.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("ARIA Service starting...")

    # Connect Redis
    await rs.connect(settings.redis_url)

    # Initialize all intel modules
    await knowledge.init()
    await intel_ledger.init()
    await contacts.init()
    await competitors.init()
    await training_data.init()
    await neural_memory.init()

    # ── RAG store: lazy init + one-shot backfill in BACKGROUND ──────────
    # The RAG store auto-initialises on first call. We probe it here so
    # any chromadb errors surface during startup, but the backfill itself
    # MUST NOT block lifespan startup — it embeds every ledger item via
    # sentence-transformers which takes many minutes on a fresh volume,
    # and uvicorn doesn't bind to 0.0.0.0:8000 until lifespan yields.
    # Past incident (2026-04-07): blocking backfill caused fly health
    # checks to fail with "[PC01] instance refused connection" and the
    # deploy was rolled back even though the app was healthy and busy.
    try:
        rag_stats_initial = await rag_store.get_stats()
        logger.info("RAG store: %s", rag_stats_initial)
    except Exception as e:
        logger.warning("RAG store probe failed (non-fatal): %s", e)
        rag_stats_initial = {}

    rag_backfill_task = None
    if rag_stats_initial.get("available") and rag_stats_initial.get("total_chunks", 0) == 0:
        async def _rag_backfill_bg():
            # Small delay so the server is bound + serving health checks first
            await asyncio.sleep(5)
            try:
                logger.info("RAG store empty — running one-shot backfill from existing knowledge + ledger (background)")
                result = await rag_store.backfill_from_existing()
                logger.info("RAG backfill complete: %s", result)
            except Exception as e:
                logger.warning("RAG backfill failed (non-fatal): %s", e)
        rag_backfill_task = asyncio.create_task(_rag_backfill_bg())

    # Create LLM provider with automatic fallback chain
    api_key = settings.llm_api_key or settings.deepseek_api_key
    llm = create_fallback_chain(
        primary_provider=settings.llm_provider,
        primary_key=api_key,
        primary_model=settings.llm_model,
        primary_base_url=settings.llm_base_url,
    )
    if not llm:
        # No fallback providers either — use single provider
        llm = create_llm_provider(
            provider=settings.llm_provider,
            api_key=api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            ollama_url=settings.ollama_url,
            ollama_model=settings.ollama_model,
        )
    app.state.llm_provider = llm
    app.state.current_data = None  # Will be set by sweep integration

    if llm and llm.is_configured:
        logger.info(f"LLM provider: {llm.name} ✓")
    else:
        logger.warning(f"LLM provider not configured — set LLM_PROVIDER + LLM_API_KEY")

    # ── OCR pre-warm ────────────────────────────────────────────────────
    # Load OCR backends in a background task so the first user image
    # doesn't pay the cold-start cost mid-request. Tesseract is cheap to
    # probe; EasyOCR is opt-in via ARIA_PREWARM_EASYOCR. Past incident
    # (2026-04-07): EasyOCR cold-loaded its 200MB model on the first OCR
    # call and OOM-killed the worker.
    async def _prewarm_ocr_bg():
        # Wait until sentence-transformers + chromadb have settled so we
        # don't pile model loads on top of each other and trigger an OOM
        # before traffic even arrives.
        await asyncio.sleep(20)
        try:
            status = await ocr_module.prewarm_ocr()
            logger.info("[OCR Pre-warm] %s", status)
        except Exception as e:
            logger.warning("[OCR Pre-warm] failed: %s", e)
    ocr_prewarm_task = asyncio.create_task(_prewarm_ocr_bg())

    # Start autonomous research scheduler (every 30 minutes)
    research_task = None
    if llm and llm.is_configured:
        async def _research_loop():
            await asyncio.sleep(60)  # Wait 1 min after startup before first research
            while True:
                try:
                    logger.info("[Research] Starting autonomous research cycle...")
                    result = await research_and_learn(llm)
                    logger.info(
                        f"[Research] Complete: {result.get('facts_learned', 0)} facts, "
                        f"{result.get('hypotheses_generated', 0)} hypotheses"
                    )
                    # Auto-validate open hypotheses (every other cycle)
                    # Auto-validate open hypotheses
                    validated = 0
                    try:
                        hypotheses = await get_hypotheses()
                        open_hyps = [h for h in hypotheses if h.get("status") == "OPEN"]
                        for h in open_hyps[:3]:
                            vr = await validate_hypothesis(llm, h.get("statement", ""))
                            validated += 1
                            if vr.get("new_status") != "OPEN":
                                logger.info("[Research] Hypothesis %s: %s → %s",
                                            h.get("statement", "")[:50],
                                            "OPEN", vr.get("new_status"))
                        if open_hyps:
                            logger.info("[Research] Validated %d/%d hypotheses",
                                        validated, len(open_hyps))
                    except Exception as e:
                        logger.warning("[Research] Hypothesis validation failed (%d validated before error): %s",
                                       validated, e)
                except Exception as e:
                    logger.warning(f"[Research] Cycle failed: {e}")
                await asyncio.sleep(30 * 60)  # Every 30 minutes

        research_task = asyncio.create_task(_research_loop())
        logger.info("Research scheduler started (every 30min)")

    # Start autonomous self-improvement loop (every 2 hours)
    self_improve_task = None
    if llm and llm.is_configured:
        async def _self_improve_loop():
            await asyncio.sleep(300)  # Wait 5 min after startup
            while True:
                try:
                    logger.info("[Self-Improve] Starting autonomous improvement cycle...")
                    result = await self_improve.autonomous_improvement_cycle(llm)
                    logger.info(
                        "[Self-Improve] Cycle complete: %d errors, %d bugs, %d auto-deployed",
                        result.get("errors_analysed", 0),
                        result.get("bugs_detected", 0),
                        result.get("auto_deployed", 0),
                    )
                except Exception as e:
                    logger.warning("[Self-Improve] Cycle failed: %s", e)
                await asyncio.sleep(2 * 3600)  # Every 2 hours

        self_improve_task = asyncio.create_task(_self_improve_loop())
        logger.info("Self-improvement scheduler started (every 2h)")

    # ── ARIA STUDENT LOOPS ──────────────────────────────────────────────
    # Active learning behaviours: self-quiz, reading sessions, library
    # consolidation. These run independently of conversation traffic so
    # ARIA studies during idle time — like a real student. Each loop is
    # safe to run with or without an LLM (the student doesn't depend on
    # the cloud teacher; she just learns faster when one is available).

    quiz_task = None
    reading_task = None
    library_consolidate_task = None

    async def _quiz_loop():
        # First quiz happens 10 min after startup so the library has time
        # to receive at least one cloud answer worth quizzing on.
        await asyncio.sleep(600)
        while True:
            try:
                result = await student.self_quiz(num_questions=5)
                logger.info(
                    "[Student] Quiz complete: %d/%d passed (score %.2f)",
                    result.get("passed", 0),
                    result.get("quizzed", 0),
                    result.get("score", 0),
                )
            except Exception as e:
                logger.warning("[Student] Quiz failed: %s", e)
            await asyncio.sleep(3 * 3600)  # Every 3 hours

    async def _reading_loop():
        # First reading session 15 min after startup so feeds are warm
        await asyncio.sleep(900)
        while True:
            try:
                result = await student.reading_session(llm=llm, num_articles=4)
                logger.info(
                    "[Student] Reading session: %d articles studied on %s",
                    result.get("articles_read", 0),
                    result.get("weak_topics_studied", []),
                )
            except Exception as e:
                logger.warning("[Student] Reading session failed: %s", e)
            await asyncio.sleep(6 * 3600)  # Every 6 hours

    async def _library_consolidate_loop():
        # Daily housekeeping — prune stale low-quality cases
        await asyncio.sleep(3600)
        while True:
            try:
                result = await reasoning_library.consolidate()
                logger.info(
                    "[Student] Library consolidated: pruned %d, remaining %d",
                    result.get("pruned", 0),
                    result.get("remaining", 0),
                )
            except Exception as e:
                logger.warning("[Student] Library consolidate failed: %s", e)
            await asyncio.sleep(24 * 3600)  # Daily

    quiz_task = asyncio.create_task(_quiz_loop())
    reading_task = asyncio.create_task(_reading_loop())
    library_consolidate_task = asyncio.create_task(_library_consolidate_loop())
    logger.info("Student loops started: self-quiz (3h), reading (6h), library consolidate (24h)")

    # ── ARIA PROACTIVE WATCH ────────────────────────────────────────────
    # Hourly background loop that:
    #   - Checks if a daily morning briefing should fire
    #   - Triggers mastery-driven prep on weak topics
    # The anomaly watch runs inside /ingest after every sweep so it fires
    # the moment new data arrives (not on a fixed schedule).
    proactive_task = None

    async def _proactive_loop():
        await asyncio.sleep(120)  # 2 min after startup
        while True:
            try:
                # Daily briefing check
                fired = await proactive.daily_briefing_check(getattr(app.state, "current_data", None))
                if fired:
                    logger.info("[Proactive] Daily briefing fired")

                # Mastery prep
                weak_count = await proactive.prepare_weak_topics()
                if weak_count:
                    logger.info("[Proactive] Mastery prep: %d weak topic(s) flagged", weak_count)
            except Exception as e:
                logger.warning("[Proactive] Loop iteration failed: %s", e)
            await asyncio.sleep(3600)  # Every hour

    proactive_task = asyncio.create_task(_proactive_loop())
    logger.info("Proactive watch started: daily briefing + mastery prep (hourly)")

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    if research_task:
        research_task.cancel()
    if self_improve_task:
        self_improve_task.cancel()
    if quiz_task:
        quiz_task.cancel()
    if reading_task:
        reading_task.cancel()
    if library_consolidate_task:
        library_consolidate_task.cancel()
    if proactive_task:
        proactive_task.cancel()
    if ocr_prewarm_task:
        ocr_prewarm_task.cancel()
    if rag_backfill_task:
        rag_backfill_task.cancel()
    logger.info("ARIA Service shutting down")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ARIA Intelligence API",
    description="Arkmurus Research Intelligence Agent — defence procurement, compliance, and geopolitical intelligence",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(aria_router)


@app.get("/health")
async def health():
    llm = app.state.llm_provider
    llm_stats = {}
    if hasattr(llm, "get_stats"):
        llm_stats = llm.get_stats()
    return {
        "status": "operational",
        "service": "aria",
        "llm_provider": llm.name if llm else "none",
        "llm_configured": bool(llm and llm.is_configured),
        "llm_fallback_stats": llm_stats,
    }


@app.post("/api/aria/ingest")
async def ingest_sweep(data: dict):
    """Receive sweep data from Node.js server to update intel layers + neural network."""
    app.state.current_data = data
    ledger_count = await intel_ledger.ingest_sweep_signals(data)
    comp_count = await competitors.scan_for_moves(data)

    # Grow neural network from sweep signals
    neural_count = 0
    llm = getattr(app.state, "llm_provider", None)
    try:
        # Learn from OSINT signals
        signals = data.get("signals") or data.get("urgentSignals") or []
        for sig in signals[:20]:
            text = sig.get("text") or sig.get("content") or ""
            if text:
                result = await neural_memory.learn_from_text(text, source="sweep", llm=llm)
                neural_count += result.get("neurons_activated", 0)
        # Learn from news
        for item in (data.get("news") or [])[:10]:
            text = item.get("title", "") + " " + item.get("summary", "")
            if text.strip():
                result = await neural_memory.learn_from_text(text, source="news", llm=llm)
                neural_count += result.get("neurons_activated", 0)
    except Exception as e:
        logger.warning("Neural ingest failed: %s", e)

    # ── PROACTIVE: anomaly watch fires on every sweep ──────────────────
    # Looks at the fresh sweep data for spikes vs the rolling baseline
    # and pushes alerts to the proactive queue if anything stands out.
    anomaly_alerts = 0
    try:
        anomaly_alerts = await proactive.anomaly_watch(data)
    except Exception as e:
        logger.warning("Proactive anomaly watch failed: %s", e)

    return {
        "ok": True,
        "ledger_signals_added": ledger_count,
        "competitor_moves_added": comp_count,
        "neurons_activated": neural_count,
        "anomaly_alerts_pushed": anomaly_alerts,
    }


# ── CLI entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "aria_service.main:app",
        host=settings.host,
        port=settings.effective_port,
        reload=False,
    )
