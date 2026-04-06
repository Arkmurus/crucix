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
                    try:
                        hypotheses = await get_hypotheses()
                        open_hyps = [h for h in hypotheses if h.get("status") == "OPEN"]
                        if open_hyps:
                            # Validate up to 3 hypotheses per cycle
                            for h in open_hyps[:3]:
                                vr = await validate_hypothesis(llm, h.get("statement", ""))
                                if vr.get("new_status") != "OPEN":
                                    logger.info("[Research] Hypothesis %s: %s → %s",
                                                h.get("statement", "")[:50],
                                                "OPEN", vr.get("new_status"))
                    except Exception as e:
                        logger.warning("[Research] Hypothesis validation failed: %s", e)
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

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    if research_task:
        research_task.cancel()
    if self_improve_task:
        self_improve_task.cancel()
    logger.info("ARIA Service shutting down")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ARIA — Arkmurus Research Intelligence Agent",
    description="Defence procurement intelligence engine",
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
    try:
        # Learn from OSINT signals
        signals = data.get("signals") or data.get("urgentSignals") or []
        for sig in signals[:20]:
            text = sig.get("text") or sig.get("content") or ""
            if text:
                result = await neural_memory.learn_from_text(text, source="sweep")
                neural_count += result.get("neurons_activated", 0)
        # Learn from news
        for item in (data.get("news") or [])[:10]:
            text = item.get("title", "") + " " + item.get("summary", "")
            if text.strip():
                result = await neural_memory.learn_from_text(text, source="news")
                neural_count += result.get("neurons_activated", 0)
    except Exception as e:
        logger.warning("Neural ingest failed: %s", e)

    return {
        "ok": True,
        "ledger_signals_added": ledger_count,
        "competitor_moves_added": comp_count,
        "neurons_activated": neural_count,
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
