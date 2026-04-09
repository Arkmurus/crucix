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

from fastapi import Depends, FastAPI
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
from .intel import cost_tracker
from .intel.researcher import research_and_learn, get_hypotheses, validate_hypothesis
from .routes.aria import router as aria_router, require_aria_token

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

    # ── RAG store: probe + backfill ALL in background ──────────────────
    # NEITHER the probe nor the backfill can run inline in lifespan.
    # Past incidents (2026-04-07):
    #   1. Backfill was awaited inline → uvicorn never bound → rollback
    #   2. Backfill moved to background, but get_stats() probe was still
    #      inline → chromadb auto-init triggered sentence-transformer
    #      download from HuggingFace (~30-90s) which blocked yield
    # Fix: probe runs in the same background task as the (optional)
    # backfill, after a delay long enough for the server to bind first.
    # Backfill stays opt-in via ARIA_RAG_BACKFILL_ENABLED.
    import os as _os
    rag_backfill_task = None
    backfill_enabled = (_os.getenv("ARIA_RAG_BACKFILL_ENABLED", "") or "").lower() in ("1", "true", "yes")
    backfill_disabled = (_os.getenv("ARIA_RAG_BACKFILL_DISABLED", "") or "").lower() in ("1", "true", "yes")

    async def _rag_init_bg():
        # Wait for the server to bind and answer initial health checks
        # before we touch chromadb. The model download alone can take
        # 30-90s on a cold volume.
        await asyncio.sleep(15)
        try:
            stats = await rag_store.get_stats()
            logger.info("[RAG] probe: %s", stats)
        except Exception as e:
            logger.warning("[RAG] probe failed (non-fatal): %s", e)
            return
        if not backfill_enabled or backfill_disabled:
            logger.info(
                "[RAG] backfill skipped (enabled=%s disabled=%s) — "
                "set ARIA_RAG_BACKFILL_ENABLED=true to opt in",
                backfill_enabled, backfill_disabled,
            )
            return
        if not stats.get("available") or stats.get("total_chunks", 0) > 0:
            logger.info(
                "[RAG] backfill skipped (available=%s chunks=%s)",
                stats.get("available"), stats.get("total_chunks", 0),
            )
            return
        try:
            logger.info("[RAG] empty store — running one-shot backfill (background)")
            result = await rag_store.backfill_from_existing()
            logger.info("[RAG] backfill complete: %s", result)
        except Exception as e:
            logger.warning("[RAG] backfill failed (non-fatal): %s", e)

    rag_backfill_task = asyncio.create_task(_rag_init_bg())

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
    # Wrap the provider with the cost-tracking decorator so every
    # llm.complete() call is metered automatically. Token counts come
    # straight from LLMResult; USD cost from cost_tracker pricing table.
    if llm:
        try:
            from .llm.metered import MeteredProvider
            llm = MeteredProvider(llm)
            logger.info("LLM provider wrapped with cost meter")
        except Exception as e:
            logger.warning("MeteredProvider wrap failed (non-fatal): %s", e)

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

    # ── One-shot reasoning_library cleanup ───────────────────────────────
    # Removes cached cases whose normalised question has < MIN_SALIENT_TOKENS
    # tokens — these are the entries that caused the 2026-04-08 over-cache
    # incident (every "Aria are you online?" returned the same Angola briefing
    # because it had been miscached against the single token "online").
    # Runs in a background task with a short delay so it can never block
    # uvicorn from binding to 0.0.0.0:8000.
    async def _purge_reasoning_library_bg():
        await asyncio.sleep(5)
        try:
            result = await reasoning_library.purge_unsafe_cases()
            logger.info("[Reasoning Library] startup purge: %s", result)
        except Exception as e:
            logger.warning("[Reasoning Library] startup purge failed (non-fatal): %s", e)
    reasoning_purge_task = asyncio.create_task(_purge_reasoning_library_bg())

    # Start autonomous research scheduler (every 30 minutes).
    # Can be disabled entirely with ARIA_AUTONOMOUS_RESEARCH_ENABLED=0 — useful
    # during interactive testing because the research cycle's sync model.encode()
    # calls block the event loop and starve chat replies on a 2GB fly machine.
    research_task = None
    research_enabled = (_os.getenv("ARIA_AUTONOMOUS_RESEARCH_ENABLED", "1") or "1").lower() not in ("0", "false", "no")
    if not research_enabled:
        logger.info("Research scheduler DISABLED via ARIA_AUTONOMOUS_RESEARCH_ENABLED=0")
    if llm and llm.is_configured and research_enabled:
        async def _research_loop():
            # 5-minute startup delay (was 1 minute) so cold-start chat traffic
            # has a clean window before the research loop starts hammering
            # sentence-transformers and saturating CPU.
            await asyncio.sleep(300)
            while True:
                # Attribute every LLM call this loop fires to the
                # "autonomous_research" feature so /cost separates it
                # from interactive chat and on-demand research_tasks.
                _t = cost_tracker.set_feature("autonomous_research")
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
                finally:
                    cost_tracker.reset_feature(_t)
                await asyncio.sleep(30 * 60)  # Every 30 minutes

        research_task = asyncio.create_task(_research_loop())
        logger.info("Research scheduler started (every 30min)")

    # Start autonomous self-improvement loop (every 2 hours)
    self_improve_task = None
    if llm and llm.is_configured:
        async def _self_improve_loop():
            await asyncio.sleep(300)  # Wait 5 min after startup
            while True:
                _t = cost_tracker.set_feature("self_improve")
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
                finally:
                    cost_tracker.reset_feature(_t)
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
            _t = cost_tracker.set_feature("student_quiz")
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
            finally:
                cost_tracker.reset_feature(_t)
            await asyncio.sleep(3 * 3600)  # Every 3 hours

    async def _reading_loop():
        # First reading session 15 min after startup so feeds are warm
        await asyncio.sleep(900)
        while True:
            _t = cost_tracker.set_feature("student_reading")
            try:
                result = await student.reading_session(llm=llm, num_articles=4)
                logger.info(
                    "[Student] Reading session: %d articles studied on %s",
                    result.get("articles_read", 0),
                    result.get("weak_topics_studied", []),
                )
            except Exception as e:
                logger.warning("[Student] Reading session failed: %s", e)
            finally:
                cost_tracker.reset_feature(_t)
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

    # ── ARIA LAYER 3 — AUTONOMOUS RESEARCH ENGINE ───────────────────────
    # Phase 3c-α (2026-04-09): scheduled research tasks defined in
    # aria_service/autonomous/tasks.yaml. Gated behind TWO independent
    # enable flags so a deploy cannot accidentally turn it on:
    #   1. ARIA_AUTONOMOUS_ENABLED env var (default OFF)
    #   2. per-task `enabled: true` in tasks.yaml (default false on every task)
    # Even with both flags on, the engine runs in DRY_RUN mode by default
    # (set ARIA_AUTONOMOUS_DRY_RUN=0 to enable real delivery to WhatsApp /
    # intel ledger). See aria_service/autonomous/AUTONOMOUS_ENGINE.md.
    try:
        from .autonomous import engine as autonomous_engine
        if autonomous_engine.is_enabled():
            started = autonomous_engine.start_engine(llm)
            if started:
                logger.info(
                    "Autonomous engine started (dry_run=%s) — see /api/aria/autonomous/status",
                    autonomous_engine.is_dry_run(),
                )
        else:
            logger.info(
                "Autonomous engine NOT started — set ARIA_AUTONOMOUS_ENABLED=1 to enable"
            )
    except Exception as e:
        logger.warning("Autonomous engine bootstrap failed (non-fatal): %s", e)

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    try:
        from .autonomous import engine as _autonomous_engine
        await _autonomous_engine.stop_engine()
    except Exception as e:
        logger.warning("Autonomous engine shutdown failed (non-fatal): %s", e)
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


@app.post("/api/aria/ingest", dependencies=[Depends(require_aria_token)])
async def ingest_sweep(data: dict):
    """Receive sweep data from Node.js server to update intel layers + neural network.

    Auth-protected: writes to persistent intel/neural state, so this endpoint
    must NOT be reachable without the bearer token. Mounted on `app` directly
    rather than via `aria_router` for historical reasons, so the token check
    is wired in explicitly here instead of inheriting it from the router.
    """
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
