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
from .intel import redis_store as rs
from .intel import knowledge, intel_ledger, contacts, competitors, training_data
from .intel.researcher import research_and_learn
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

    # Create LLM provider
    api_key = settings.llm_api_key or settings.deepseek_api_key
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
                except Exception as e:
                    logger.warning(f"[Research] Cycle failed: {e}")
                await asyncio.sleep(30 * 60)  # Every 30 minutes

        research_task = asyncio.create_task(_research_loop())
        logger.info("Research scheduler started (every 30min)")

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    if research_task:
        research_task.cancel()
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
    return {
        "status": "operational",
        "service": "aria",
        "llm_provider": llm.name if llm else "none",
        "llm_configured": bool(llm and llm.is_configured),
    }


@app.post("/api/aria/ingest")
async def ingest_sweep(data: dict):
    """Receive sweep data from Node.js server to update intel layers."""
    app.state.current_data = data
    ledger_count = await intel_ledger.ingest_sweep_signals(data)
    comp_count = await competitors.scan_for_moves(data)
    return {
        "ok": True,
        "ledger_signals_added": ledger_count,
        "competitor_moves_added": comp_count,
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
