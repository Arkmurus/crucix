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
import os as _os
import time
from contextlib import asynccontextmanager

import json

from fastapi import Depends, FastAPI, HTTPException, Request
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

# R-F360 (2026-05-12): deploy marker. Bumped manually each commit that
# touches fly.io-deployed surfaces (aria_service/*). Logged at lifespan
# startup + exposed in /api/aria/health so we can confirm the deployed
# commit matches what's in git. Diagnostic added after R-F353 was committed
# and pushed but seenode kept emitting the pre-R-F353 log shape — uptime
# alone couldn't tell us whether the deploy had picked up. Same pattern
# now also installed on seenode (server.mjs CRUCIX_BUILD_REV).
ARIA_BUILD_REV = "R-F394..F398 · 2026-05-13 · five-fix batch from ARIA's self-assessment: R-F394 brave_answer anchor extraction + R-F395 GCC opt-out (no auto-Arabic for Saudi/UAE/Qatar/Kuwait/Bahrain/Oman) + R-F396 /health/perf self-introspection endpoint + R-F397 RAG similarity floor (0.50 default, kills 0.43 phonetic bleed) + R-F398 DD INSUFFICIENT_EVIDENCE fallback web-search; prior on this rev: R-F392 deep_research anchor + R-F393 verification honest-scope"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("ARIA Service starting...")
    logger.info("ARIA Build: %s", ARIA_BUILD_REV)

    # Connect Redis
    await rs.connect(settings.redis_url)

    # F28 fix 2026-04-27: every Lightpanda / Playwright render emits
    # `(node:NNN) [DEP0169] DeprecationWarning: url.parse() behavior is
    # not standardized` from internal Node helpers. The warning is
    # cosmetic — Playwright still works — but adds 3-4 noise lines per
    # render. Set NODE_OPTIONS=--no-deprecation BEFORE any Node child
    # is spawned to silence the lot.
    #
    # IMPORTANT: cannot reference module-level `_os` here. Python sees
    # the `import os as _os` later in this function (rag_init_bg block)
    # and treats `_os` as LOCAL for the whole function scope —
    # referencing it before that assignment raises UnboundLocalError.
    # That bug took prod down for 30s of restart-loop on commit
    # 6c26e17 → fixed in this commit by using a fresh local alias.
    import os as _f28_os
    _f28_os.environ.setdefault("NODE_OPTIONS", "--no-deprecation")

    # B1 fix 2026-04-27: install the error-ledger logging handler so
    # WARNING+ aria.* logs auto-record into self_improve's error ledger.
    # Previously self_improve.record_error was only wired to 2 sites in
    # aria_engine.py, so the autonomous self-improvement cycle reported
    # "0 errors" every cycle and had nothing to act on.
    try:
        from .intel import error_log_handler as _elh
        _elh.install()
    except Exception as e:
        logger.warning("error-ledger handler install failed (non-fatal): %s", e)

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

    # Create LLM provider with automatic fallback chain.
    # Auto-detect the right API key based on the provider name so that
    # setting ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic works without
    # also needing to duplicate the key into LLM_API_KEY.
    _provider_key_map = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "deepseek": settings.deepseek_api_key,
    }
    api_key = (
        settings.llm_api_key
        or _provider_key_map.get(settings.llm_provider.lower().strip(), "")
        or settings.deepseek_api_key
    )
    llm = create_fallback_chain(
        primary_provider=settings.llm_provider,
        primary_key=api_key,
        primary_model=settings.llm_model,
        primary_base_url=settings.llm_base_url,
    )
    # F68 fix 2026-04-28: rehydrate any HARD (auth/billing) cooldowns
    # that were mirrored to Redis before the previous process exited.
    # Without this, every restart re-probes the failed backend and burns
    # ~5 calls before the in-process cooldown re-engages.
    if llm and hasattr(llm, "hydrate_from_redis"):
        try:
            n = await llm.hydrate_from_redis()
            if n:
                logger.info(
                    "LLM fallback chain: rehydrated %d HARD cooldown(s) from Redis",
                    n,
                )
        except Exception as e:
            logger.warning("LLM cooldown hydrate failed (non-fatal): %s", e)
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

    # Wrap with priority-aware rate limiter so background loops don't
    # starve interactive chat of Anthropic quota. Interactive requests
    # always go through; background tasks yield when near the limit.
    # ARIA_LLM_RPM env var sets the requests-per-minute cap (default 50).
    if llm:
        try:
            from .llm.rate_limiter import RateLimitedProvider
            llm = RateLimitedProvider(llm)
            logger.info("LLM provider wrapped with rate limiter (rpm=%s)",
                        _os.getenv("ARIA_LLM_RPM", "50"))
        except Exception as e:
            logger.warning("RateLimitedProvider wrap failed (non-fatal): %s", e)

    app.state.llm_provider = llm
    app.state.current_data = None  # Will be set by sweep integration

    if llm and llm.is_configured:
        logger.info(f"LLM provider: {llm.name} ✓")
    else:
        logger.warning(f"LLM provider not configured — set LLM_PROVIDER + LLM_API_KEY")

    # ── R-F248 (2026-05-11) — startup state snapshot ──────────────────────
    # Log a single "ARIA state at boot" line with the size of every
    # persistent store. This is the FIRST line operators should see if
    # any data was lost on the deploy (knowledge / RAG / mem0 / neural /
    # ledger should all match the previous boot ± natural growth).
    # If a count drops by more than ~5% across restarts, something
    # truncated or corrupted state and the operator needs to investigate
    # before traffic resumes.
    async def _log_boot_state():
        # Defer a few seconds so all stores have finished their lazy
        # init (chromadb + knowledge + ledger + neural all warm up
        # asynchronously after lifespan starts).
        await asyncio.sleep(10)
        snapshot = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            from .intel import knowledge as _kb
            snapshot["knowledge_facts"] = len(_kb.all_facts())
        except Exception as e:
            snapshot["knowledge_facts"] = f"err:{str(e)[:40]}"
        try:
            from .intel import intel_ledger as _il
            sigs = await _il.recent_signals(limit=10**9)
            snapshot["ledger_signals"] = len(sigs) if isinstance(sigs, list) else "err"
        except Exception as e:
            snapshot["ledger_signals"] = f"err:{str(e)[:40]}"
        try:
            from .intel import rag_store as _rs
            rs_stats = await _rs.get_stats()
            snapshot["rag_chunks"] = rs_stats.get("documents_indexed", "err")
            snapshot["rag_facts"] = rs_stats.get("facts_indexed", "err")
        except Exception as e:
            snapshot["rag_chunks"] = f"err:{str(e)[:40]}"
        try:
            from .intel import chat_audit_log as _cal
            cal_stats = await _cal.get_stats()
            snapshot["chat_audit_total"] = cal_stats.get("total_entries", 0)
        except Exception as e:
            snapshot["chat_audit_total"] = f"err:{str(e)[:40]}"
        try:
            from .intel import neural_memory as _nm
            if hasattr(_nm, "get_stats"):
                nm_stats = await _nm.get_stats()
                snapshot["neural_neurons"] = nm_stats.get("total_neurons", "n/a")
                snapshot["neural_edges"] = nm_stats.get("total_edges", "n/a")
            else:
                snapshot["neural_neurons"] = "n/a"
        except Exception as e:
            snapshot["neural_neurons"] = f"err:{str(e)[:40]}"
        try:
            from .intel import state_store as _ss
            ss = await _ss.stats()
            snapshot["state_backend"] = ss.get("backend", "unknown")
            snapshot["state_keys"] = ss.get("key_count", "n/a")
        except Exception:
            snapshot["state_backend"] = "upstash-or-memory"

        logger.warning(
            "[R-F248] ARIA STATE AT BOOT — %s",
            " · ".join(f"{k}={v}" for k, v in snapshot.items()),
        )
        # Also persist the snapshot for diff-on-next-boot
        try:
            from .intel import redis_store as _rs_b
            await _rs_b.lpush("crucix:aria:boot_snapshots",
                              __import__("json").dumps(snapshot, default=str))
            await _rs_b.ltrim("crucix:aria:boot_snapshots", 0, 49)
        except Exception:
            pass

        # R-F251 (2026-05-11) — regression detection. Diff this boot's
        # snapshot against the PREVIOUS one (index 1 in the list). If any
        # numeric counter dropped by >5%, that's silent state loss — log
        # a LOUD warning AND absorb to brain_hook so the operator
        # dashboard surfaces it. Per the infinite-memory rule a counter
        # NEVER drops on a healthy deploy; if it does, the operator
        # needs to know BEFORE traffic resumes.
        try:
            from .intel import redis_store as _rs_diff
            import json as _json_diff
            prior_raw = await _rs_diff.lrange("crucix:aria:boot_snapshots", 1, 1)
            if prior_raw:
                try:
                    prior = _json_diff.loads(prior_raw[0]) if isinstance(prior_raw[0], str) else prior_raw[0]
                except Exception:
                    prior = None
                if isinstance(prior, dict):
                    drops: list[str] = []
                    for k in ("knowledge_facts", "ledger_signals", "rag_chunks",
                              "rag_facts", "chat_audit_total", "neural_neurons",
                              "neural_edges", "state_keys"):
                        cur_val = snapshot.get(k)
                        prv_val = prior.get(k)
                        if isinstance(cur_val, (int, float)) and isinstance(prv_val, (int, float)):
                            if prv_val > 0 and cur_val < prv_val * 0.95:
                                drop_pct = round((1 - cur_val / prv_val) * 100, 1)
                                drops.append(f"{k}: {prv_val} → {cur_val} (-{drop_pct}%)")
                    if drops:
                        logger.error(
                            "[R-F251] STATE REGRESSION DETECTED — counters dropped >5%% "
                            "since previous boot: %s",
                            "; ".join(drops),
                        )
                        try:
                            from .intel import brain_hook as _bh_reg
                            await _bh_reg.absorb(
                                module="boot_diagnostic",
                                summary="R-F251: state regression detected at boot",
                                detail=(
                                    "Per the infinite-memory rule, NO counter should "
                                    "drop across restarts. The following counters fell "
                                    f"by >5% since the previous boot: {'; '.join(drops)}. "
                                    "Investigate disk volume mount, Redis fallback "
                                    "behaviour, or recent code changes BEFORE traffic "
                                    "resumes."
                                ),
                                success=False,
                                gap_type="boot_state_regression",
                                gap_detail="; ".join(drops),
                            )
                        except Exception:
                            pass
        except Exception as _diff_err:
            logger.debug("R-F251 boot-diff failed: %s", _diff_err)
    asyncio.create_task(_log_boot_state())

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
        # R-F333 (2026-05-11): boot-time reasoning_library size diagnostic.
        # Live evidence 21:19:37 — Student Quiz fired with library_size=0,
        # meaning the chat-recorded cases weren't accumulating. Without
        # this log line we had to wait for the 3-hourly quiz to learn the
        # library was empty. Now: emit the count at boot + on every
        # consolidate cycle, AND brain_hook a gap when library is empty
        # so the dashboard surfaces it as an operator-action item.
        try:
            _bo_index = await reasoning_library._load_index()
            _bo_count = len(_bo_index or [])
            logger.info(
                "[R-F333] reasoning_library boot diagnostic: %d cases loaded from INDEX_KEY",
                _bo_count,
            )
            if _bo_count == 0:
                logger.warning(
                    "[R-F333] reasoning_library EMPTY at boot — chat-recorded "
                    "cases aren't accumulating. Check Upstash INDEX_KEY "
                    "(crucix:aria:reasoning_library:index) AND record_response "
                    "filter rejections."
                )
                try:
                    from .intel import brain_hook as _bh_rf333
                    await _bh_rf333.absorb(
                        module="reasoning_library",
                        summary="R-F333: reasoning_library empty at boot",
                        detail=(
                            "INDEX_KEY returned 0 cases on startup. Either "
                            "Upstash key was wiped, record_response is "
                            "rejecting every chat answer, or the chat path "
                            "isn't reaching record_cloud_llm_response. "
                            "Investigate: (1) GET crucix:aria:reasoning_library:index "
                            "from Upstash REST API, (2) grep fly logs for "
                            "record_response rejection reasons, (3) verify "
                            "chat handler wiring."
                        ),
                        success=False,
                        gap_type="reasoning_library_empty_at_boot",
                        gap_detail="0 cases in INDEX_KEY at startup",
                    )
                except Exception as _bh_e:
                    logger.debug("R-F333 brain_hook absorb failed: %s", _bh_e)
        except Exception as _bd_e:
            logger.warning("[R-F333] reasoning_library boot diagnostic failed: %s", _bd_e)

        try:
            result = await reasoning_library.purge_unsafe_cases()
            logger.info("[Reasoning Library] startup purge (unsafe): %s", result)
        except Exception as e:
            logger.warning("[Reasoning Library] startup purge (unsafe) failed: %s", e)
        try:
            # Second pass: remove fresh-input-tied and turn-failure responses.
            # Catches the detonator_suppliers.xlsx replay cluster (2026-04-11).
            polluted = await reasoning_library.purge_polluted_cases()
            logger.info("[Reasoning Library] startup purge (polluted): %s", polluted)
        except Exception as e:
            logger.warning("[Reasoning Library] startup purge (polluted) failed: %s", e)
    reasoning_purge_task = asyncio.create_task(_purge_reasoning_library_bg())

    # Start autonomous research scheduler (every 30 minutes).
    # Can be disabled entirely with ARIA_AUTONOMOUS_RESEARCH_ENABLED=0 — useful
    # during interactive testing because the research cycle's sync model.encode()
    # calls block the event loop and starve chat replies on a 2GB fly machine.
    research_task = None
    research_enabled = (_os.getenv("ARIA_AUTONOMOUS_RESEARCH_ENABLED", "1") or "1").lower() not in ("0", "false", "no")
    if not research_enabled:
        logger.info("Research scheduler DISABLED via ARIA_AUTONOMOUS_RESEARCH_ENABLED=0")
    # R-F195 (2026-05-11): start research loop even when LLM is
    # unavailable. The degraded path in researcher.research_and_learn
    # still fetches RSS + ingests into RAG; only the LLM-driven fact
    # extraction is skipped. Air-gap independence depends on this.
    if research_enabled:
        async def _research_loop():
            # 15-minute startup delay (was 5 min). Staggered far from
            # self-improve (10min) and student (20/25min) to prevent
            # thundering herd on Anthropic tier-1 rate limits.
            await asyncio.sleep(900)
            while True:
                # Tag as BACKGROUND priority so the rate limiter yields
                # to interactive chat when Anthropic quota is tight.
                from .llm.rate_limiter import set_priority, reset_priority, Priority
                _p = set_priority(Priority.BACKGROUND)
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
                    # Auto-validate open hypotheses (every other cycle).
                    # F27 fix 2026-04-27: was reading the wrong dict key
                    # but hypotheses are stored under key "hypothesis" (see
                    # researcher._process_analysis). Empty-string lookup
                    # then triggered the substring-match-anything fallback
                    # in validate_hypothesis, so we re-validated the same
                    # hypothesis #0 three times per cycle for months.
                    # Also sort by created_at so we work the oldest-OPEN
                    # backlog first — those have had the most time for new
                    # evidence to land.
                    # F78d 2026-04-29: log was "Validated 3/175" which
                    # read like a 1.7% success rate. Reality: 175 = OPEN
                    # backlog total, 3 = per-cycle quota; flipped (verdict
                    # reached) is a separate axis. Split the three so the
                    # log doesn't mislead future log-readers.
                    # R-F32 2026-05-03: bumped quota 3→8. With 5-attempt
                    # drain cap, picks=3 gave 0.6 drained/cycle vs ~1.0
                    # generated/cycle — backlog grew +20/day (live
                    # observation 2026-05-03 09:00:56: 109 OPEN, 0/3
                    # verdicts). picks=8 gives 1.6/cycle drain, net
                    # -0.6/cycle so the backlog actually clears.
                    processed = 0
                    flipped = 0
                    # R-F205 (2026-05-11) — guard hypothesis validation against
                    # the R-F195 no-LLM degraded path. Without this, every
                    # validate_hypothesis call returns {"error": "..."} (no
                    # new_status), the `!= "OPEN"` check evaluates True (None
                    # != "OPEN"), `flipped` increments for all 8 picks, and
                    # the operator log shows phantom verdicts. Skip the whole
                    # validation pass when LLM is absent — hypotheses stay
                    # OPEN until the next cycle with a working LLM.
                    _llm_ok = bool(llm and getattr(llm, "is_configured", False))
                    if not _llm_ok:
                        logger.info(
                            "[Research] LLM unavailable — skipping hypothesis "
                            "validation pass (R-F205)"
                        )
                    try:
                        if _llm_ok:
                            hypotheses = await get_hypotheses()
                            open_hyps = [h for h in hypotheses if h.get("status") == "OPEN"]
                            open_hyps.sort(key=lambda h: h.get("created_at") or "")
                            for h in open_hyps[:8]:
                                hyp_text = h.get("hypothesis", "")
                                if not hyp_text:
                                    continue
                                vr = await validate_hypothesis(llm, hyp_text)
                                processed += 1
                                if vr.get("new_status") != "OPEN":
                                    flipped += 1
                                    logger.info("[Research] Hypothesis %s: %s → %s",
                                                hyp_text[:50],
                                                "OPEN", vr.get("new_status"))
                            if open_hyps:
                                logger.info(
                                    "[Research] Hypothesis validation: %d processed this cycle, "
                                    "%d reached a verdict, %d still OPEN in backlog",
                                    processed, flipped,
                                    max(0, len(open_hyps) - flipped),
                                )
                    except Exception as e:
                        logger.warning("[Research] Hypothesis validation failed (%d processed before error): %s",
                                       processed, e)
                except Exception as e:
                    logger.warning(f"[Research] Cycle failed: {e}")
                finally:
                    cost_tracker.reset_feature(_t)
                    reset_priority(_p)
                await asyncio.sleep(30 * 60)  # Every 30 minutes

        research_task = asyncio.create_task(_research_loop())
        logger.info("Research scheduler started (every 30min)")

    # Start autonomous self-improvement loop (every 2 hours)
    self_improve_task = None
    if llm and llm.is_configured:
        async def _self_improve_loop():
            await asyncio.sleep(600)  # Wait 10 min after startup (staggered from research at 15min)
            while True:
                from .llm.rate_limiter import set_priority, reset_priority, Priority
                _p = set_priority(Priority.BACKGROUND)
                _t = cost_tracker.set_feature("self_improve")
                try:
                    logger.info("[Self-Improve] Starting autonomous improvement cycle...")
                    result = await self_improve.autonomous_improvement_cycle(llm)
                    # R-F272 (2026-05-11) — honest cycle log. Operator was
                    # alarmed by "160 errors, 0 bugs" and couldn't tell whether
                    # the 0 meant no real bugs OR that every error was in a
                    # non-MODIFIABLE_FILES path being silently skipped. The
                    # cycle now reports both populations so the operator sees
                    # the actual landscape.
                    modifiable = result.get("errors_in_modifiable_files", {}) or {}
                    external = result.get("errors_in_external_files", {}) or {}
                    mod_sum = sum(modifiable.values())
                    ext_sum = sum(external.values())
                    below_sum = result.get("errors_below_threshold", 0)
                    # R-F361 (2026-05-12): renamed "external" → "out-of-scope"
                    # in the log because every file under the prior label is
                    # in our codebase, just outside the MODIFIABLE_FILES
                    # auto-fix allowlist. Surfaced the third bucket (errors
                    # in below-threshold files) so total = sum-of-three.
                    # Underlying dict keys preserved for backward compat.
                    top_external = sorted(external.items(), key=lambda kv: -kv[1])[:3]
                    top_external_str = ", ".join(f"{p}={n}" for p, n in top_external) or "none"
                    logger.info(
                        "[Self-Improve] Cycle complete: %d errors total "
                        "(%d auto-fixable · %d out-of-scope · %d below-threshold), "
                        "%d bugs detected, %d auto-deployed. "
                        "Top out-of-scope offenders: %s",
                        result.get("errors_analysed", 0),
                        mod_sum,
                        ext_sum,
                        below_sum,
                        result.get("bugs_detected", 0),
                        result.get("auto_deployed", 0),
                        top_external_str,
                    )
                except Exception as e:
                    logger.warning("[Self-Improve] Cycle failed: %s", e)
                finally:
                    cost_tracker.reset_feature(_t)
                    reset_priority(_p)
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
        # First quiz happens 20 min after startup (staggered from research
        # at 15min and self-improve at 10min to prevent rate limit storms).
        await asyncio.sleep(1200)
        while True:
            from .llm.rate_limiter import set_priority, reset_priority, Priority
            _p = set_priority(Priority.BACKGROUND)
            _t = cost_tracker.set_feature("student_quiz")
            try:
                result = await student.self_quiz(num_questions=5)
                # R-F291: when quizzed==0 the previous log was diagnostically
                # blind. Surface library_size + orphan + skip counts so the
                # silent-skip root cause is visible on the next sweep.
                if result.get("quizzed", 0) == 0:
                    logger.info(
                        "[Student] Quiz complete: 0/0 passed (score 0.00) — "
                        "note=%s library_size=%d sample=%d orphans=%d healed=%d "
                        "no_question=%d no_response=%d",
                        result.get("note", "all_sample_fell_through"),
                        result.get("library_size", 0),
                        result.get("sample_size", 0),
                        result.get("orphans", 0),
                        result.get("orphans_healed", 0),
                        result.get("skipped_no_question", 0),
                        result.get("skipped_no_response", 0),
                    )
                else:
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
                reset_priority(_p)
            await asyncio.sleep(3 * 3600)  # Every 3 hours

    async def _reading_loop():
        # First reading session 25 min after startup (last in the stagger
        # sequence: self-improve 10m → research 15m → quiz 20m → reading 25m).
        await asyncio.sleep(1500)
        while True:
            from .llm.rate_limiter import set_priority, reset_priority, Priority
            _p = set_priority(Priority.BACKGROUND)
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
                reset_priority(_p)
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

    # ── WEEKLY LEARNING REPORT ──────────────────────────────────────────
    # Every Monday at ~07:00 UTC, generate a learning report aggregating
    # new facts, mastery changes, capability gaps, standards ingested,
    # reasoning library health, and correction learning activity. The
    # report is persisted in Redis and can be delivered via WhatsApp.
    async def _weekly_report_loop():
        await asyncio.sleep(300)  # 5 min after startup
        while True:
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                if now.weekday() == 0 and 6 <= now.hour <= 8:
                    from .intel import weekly_report
                    result = await weekly_report.generate_weekly_report(
                        llm=getattr(app.state, "llm_provider", None),
                    )
                    # weekly_report.generate_weekly_report returns nested
                    # dicts (`new_facts.total`, `capability_gaps.unresolved`,
                    # `mastery_changes.current_scores`), not flat keys.
                    # Previous logging always printed "0 new facts, 0 gaps,
                    # mastery 0%" because the keys it read didn't exist.
                    _new_facts = (result.get("new_facts") or {}).get("total", 0)
                    _gaps = (result.get("capability_gaps") or {}).get("unresolved", 0)
                    _scores = (
                        (result.get("mastery_changes") or {}).get("current_scores") or {}
                    )
                    _overall_now = (
                        sum(_scores.values()) / len(_scores) if _scores else 0
                    )
                    logger.info(
                        "[Weekly Report] Generated: %d new facts, %d gaps, mastery %.0f%%",
                        _new_facts, _gaps, _overall_now * 100,
                    )
            except Exception as e:
                logger.warning("[Weekly Report] Loop iteration failed: %s", e)
            await asyncio.sleep(3600)  # Check every hour (only fires on Monday 06-08 UTC)

    weekly_report_task = asyncio.create_task(_weekly_report_loop())
    logger.info("Weekly report loop started (fires Monday 06-08 UTC)")

    # ── WATCHLIST AUTO-RE-SCREEN ──────────────────────────────────────────
    # Daily background loop: re-screens every entity on the DD watchlist
    # against sanctions + PEP lists (no LLM, no deep research). Detects
    # status changes and pushes alerts to Redis for API retrieval.
    watchlist_rescreen_task = None

    async def _watchlist_rescreen_loop():
        await asyncio.sleep(600)  # 10 min after startup
        while True:
            try:
                from .intel import dd_orchestrator
                result = await dd_orchestrator.rescreen_watchlist(
                    llm=getattr(app.state, "llm_provider", None),
                )
                logger.info(
                    "[Watchlist] Re-screen: %d entities, %d changes, %d errors, %dms",
                    result.get("entities_screened", 0),
                    len(result.get("changes_detected", [])),
                    len(result.get("errors", [])),
                    result.get("duration_ms", 0),
                )
                # If changes detected, fire-and-forget WhatsApp notification
                if result.get("changes_detected"):
                    try:
                        from .intel import whatsapp
                        summary_lines = []
                        for ch in result["changes_detected"][:10]:
                            summary_lines.append(
                                f"  - {ch['entity']}: {ch['old_status']} -> {ch['new_status']} ({ch['change_type']})"
                            )
                        msg = (
                            f"[ARIA Watchlist Alert] {len(result['changes_detected'])} change(s) detected:\n"
                            + "\n".join(summary_lines)
                        )
                        asyncio.create_task(whatsapp.send_message(msg))
                    except Exception:
                        pass  # WhatsApp not configured — no-op
            except Exception as e:
                logger.warning("[Watchlist] Re-screen failed: %s", e)
            await asyncio.sleep(86400)  # Every 24 hours

    watchlist_rescreen_task = asyncio.create_task(_watchlist_rescreen_loop())
    logger.info("Watchlist re-screen loop started (daily, 10 min after startup)")

    # ── TENDER MONITOR ────────────────────────────────────────────────────
    # Every 6 hours, crawl public defence procurement portals (TED, SAM.gov,
    # Contracts Finder, UNGM, AfDB) for relevant tenders. Equivalent to
    # Janes/IHS Markit tender monitoring. No LLM required — pure HTTP
    # crawl + keyword/CPV scoring.
    tender_monitor_task = None

    async def _tender_monitor_loop():
        await asyncio.sleep(900)  # 15 min after startup
        while True:
            try:
                from .intel import tender_monitor
                result = await tender_monitor.run_monitoring_cycle()
                if result.get("new_tenders", 0) > 0:
                    logger.info(
                        "[Tender Monitor] %d new tenders detected across %d portals",
                        result["new_tenders"], result["portals_crawled"],
                    )
                else:
                    logger.info("[Tender Monitor] Cycle complete — no new tenders")
            except Exception as e:
                logger.warning("[Tender Monitor] Cycle failed: %s", e)
            await asyncio.sleep(21600)  # Every 6 hours

    tender_monitor_task = asyncio.create_task(_tender_monitor_loop())
    logger.info("Tender monitor started (every 6h)")

    # ── METACOGNITIVE ENGINE STATUS ───────────────────────────────────────
    # Phase 3 metacognitive stack: self-assessment, gap detection, Brier
    # scoring, consciousness mapping, self-improvement code generation.
    # The engine hooks into the chat pipeline (post-output self-assessment)
    # and the autonomous engine (daily/weekly/monthly cycles). No background
    # loop needed — just log readiness status at startup.
    try:
        from .metacognitive.identity import is_enabled as metacog_enabled
        if metacog_enabled():
            logger.info(
                "Metacognitive engine ENABLED — self-assessment on chat pipeline, "
                "identity+calibration injected into system prompt. "
                "Admin: /api/aria/metacognitive/status"
            )
        else:
            logger.info("Metacognitive engine DISABLED — set ARIA_METACOGNITIVE_ENABLED=1 to enable")
    except Exception as e:
        logger.warning("Metacognitive engine status check failed (non-fatal): %s", e)

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
        # Hydrate the in-process runtime-override cache BEFORE checking
        # is_enabled(). This lets /autonomous/enable keep the engine on
        # after a redeploy when the env var is missing — the Redis flag
        # survives restarts and gets picked up here on the next boot.
        await autonomous_engine.refresh_runtime_override()
        if autonomous_engine.is_enabled():
            started = autonomous_engine.start_engine(llm)
            if started:
                logger.info(
                    "Autonomous engine started (dry_run=%s) — see /api/aria/autonomous/status",
                    autonomous_engine.is_dry_run(),
                )
        else:
            logger.info(
                "Autonomous engine NOT started — set ARIA_AUTONOMOUS_ENABLED=1 "
                "or POST /api/aria/autonomous/enable to flip at runtime"
            )
            # Log a pending-action so the operator sees this in the next
            # daily briefing. CRITICAL severity so it gets nudged now.
            try:
                from .intel import pending_actions as _pa
                await _pa.record(
                    promise=(
                        "Autonomous learning loop should be running 24/7 — "
                        "spider, metacog, research, style_learner, plus 65 "
                        "scheduled tasks."
                    ),
                    reason=(
                        "ARIA_AUTONOMOUS_ENABLED env var is not set on the "
                        "Python backend (fly.io app aria-intel). The engine "
                        "cannot run until the master switch is on."
                    ),
                    resolver_kind="operator_action",
                    resolver_ref="ARIA_AUTONOMOUS_ENABLED",
                    severity="CRITICAL",
                    source="lifespan_bootstrap",
                    operator_prompt=(
                        "POST /api/aria/autonomous/enable to turn on the "
                        "autonomous engine right now (survives redeploy via "
                        "Redis). For a permanent fix, also run: "
                        "flyctl secrets set ARIA_AUTONOMOUS_ENABLED=1 "
                        "-a aria-intel"
                    ),
                )
            except Exception as _pa_err:
                logger.debug(
                    "pending_actions record at bootstrap failed (non-fatal): %s",
                    _pa_err,
                )
    except Exception as e:
        logger.warning("Autonomous engine bootstrap failed (non-fatal): %s", e)

    # ── Defence source seed → web_atlas (2026-04-18) ────────────────
    # Bootstrap the curated Tier-1/1b/2 defence source catalogue into
    # web_atlas if it hasn't been populated yet. Idempotent — safe to
    # run on every startup. Seeding happens in background so it doesn't
    # block the lifespan startup gate.
    try:
        from .intel import defence_source_seed
        async def _seed_bg():
            try:
                result = await defence_source_seed.seed_web_atlas(
                    skip_if_populated=True,
                )
                logger.info("Defence source seed: %s", result)
            except Exception as _e:
                logger.debug("Defence source seed bg failed: %s", _e)
        import asyncio as _aio
        _aio.create_task(_seed_bg())
    except Exception as e:
        logger.debug("Defence source seed dispatch failed (non-fatal): %s", e)

    # ── Knowledge seeding (background) ─────────────────────────────────
    # Seed the full knowledge corpus on startup. Runs after RAG store is
    # warm (25s delay). Idempotent — rag_store.ingest_document()
    # deduplicates by source URL. Five modules get ingested in order:
    #   1. international_law            (LOAC/IHL, ATT, sanctions, AML, …)
    #   2. global_export_control        (UK/US/EU/Wassenaar/MTCR/NSG/AG/CWC
    #                                    + national regimes TR/IL/KR/JP/BR/
    #                                    IN/RU/CN/AE)
    #   3. regional_compliance          (NATO, EU, AU/ECOWAS/SADC/EAC, GCC,
    #                                    ASEAN/Quad/AUKUS, OAS/MERCOSUR,
    #                                    CIS/CSTO/SCO, OSCE, UNROCA)
    #   4. due_diligence_playbooks      (UBO extraction + ghost scoring)
    #   5. risk_indices                 (CPI, Basel AML, FATF, WGI, EITI,
    #                                    GPI, GTI, OECD CRC)
    #   6. international_law sources    (crawl registration for refresh)
    #   7. contract_intelligence.ingest_clause_library (clause library)
    # Seed-completion marker in Redis. If the seed finished within the
    # last SEED_CACHE_TTL seconds on a previous boot, skip re-running to
    # avoid pinning CPU/memory on rolling restarts. Force re-ingest via
    # POST /api/aria/knowledge/reseed or by setting ARIA_FORCE_RESEED=1.
    _SEED_MARKER_KEY = "crucix:knowledge_seed:last_completed"
    _SEED_CACHE_TTL = 6 * 3600  # 6 hours

    async def run_knowledge_seed(force: bool = False) -> dict:
        """Idempotent knowledge-corpus seeding.

        Runs every module sequentially. Each ingest_all_sections call is
        internally deduped by rag_store via source URL, so re-running is
        cheap. Returns a summary dict. Safe to call from startup, from
        /api/aria/knowledge/reseed, or manually via fly ssh.
        """
        from .intel import redis_store as _rs
        summary: dict = {}
        if not force:
            try:
                last = await _rs.get(_SEED_MARKER_KEY)
                if last:
                    age = time.time() - float(last)
                    if age < _SEED_CACHE_TTL:
                        logger.info(
                            "[Knowledge Seed] skipping — completed %.0fs ago (within %ds cache window). "
                            "Set ARIA_FORCE_RESEED=1 or POST /api/aria/knowledge/reseed to override.",
                            age, _SEED_CACHE_TTL,
                        )
                        return {"skipped": True, "last_completed_age_s": int(age)}
            except Exception as e:
                logger.debug("seed marker read failed (non-fatal): %s", e)

        modules = [
            ("international_law",       "Law",                   "ingest_all_sections"),
            ("global_export_control",   "Global export control", "ingest_all_sections"),
            ("regional_compliance",     "Regional compliance",   "ingest_all_sections"),
            ("due_diligence_playbooks", "DD playbooks",          "ingest_all_sections"),
            ("risk_indices",            "Risk indices",          "ingest_all_sections"),
            ("dd_case_library",         "DD case library",       "ingest_all_cases"),
            ("nato_standards",          "NATO standards",        "ingest_to_knowledge"),
            ("procurement_knowledge",   "Procurement intel",     "ingest_to_knowledge"),
            ("market_competitor_knowledge", "Market & competitor",  "ingest_to_knowledge"),
            ("osint_knowledge",          "OSINT methodology",    "ingest_to_knowledge"),
            ("security_protocol",        "Security protocol",    "ingest_to_knowledge"),
            ("sipri_knowledge",          "SIPRI + equipment",    "ingest_all_sections"),
            ("global_defence_knowledge", "Global defence intel", "ingest_all_sections"),
        ]

        # F50 fix 2026-04-27: chromadb dedupes upserts by ID, but the
        # sentence-transformer ENCODE still runs on every chunk every
        # time. With ~660 chunks across 13 modules, that's ~5 minutes of
        # CPU per cold boot — and it tripped the brain_hook circuit
        # breaker at 21:35:05 (p95=2800ms). Skip per-module if the
        # module's source file hash hasn't changed since the last
        # successful seed.
        import hashlib as _hashlib
        from pathlib import Path as _Path
        async def _module_hash(modname: str) -> str:
            """Return md5 of the module's .py file, or '' if not found."""
            try:
                mod_path = _Path(__file__).parent / "intel" / f"{modname}.py"
                if not mod_path.exists():
                    return ""
                h = _hashlib.md5()
                h.update(mod_path.read_bytes())
                return h.hexdigest()
            except Exception:
                return ""

        for modname, label, fn in modules:
            try:
                # Hash-guard: skip the whole module if its source file
                # hasn't changed since the last successful seed.
                if not force:
                    cur_hash = await _module_hash(modname)
                    if cur_hash:
                        seed_hash_key = f"crucix:knowledge_seed:hash:{modname}"
                        try:
                            stored = await _rs.get(seed_hash_key)
                            if stored and str(stored) == cur_hash:
                                summary[modname] = {"skipped": True, "reason": "hash_unchanged"}
                                logger.info(
                                    "[Knowledge Seed] %s: skipped (file unchanged since last seed)",
                                    label,
                                )
                                continue
                        except Exception as e:
                            logger.debug("seed hash read failed for %s: %s", modname, e)

                mod = __import__(f"aria_service.intel.{modname}", fromlist=[fn])
                result = await getattr(mod, fn)()
                summary[modname] = result
                logger.info(
                    "[Knowledge Seed] %s: %d/%d sections, %d chunks",
                    label,
                    result.get("sections_ingested", 0),
                    result.get("total_sections", 0),
                    result.get("total_chunks", 0),
                )
                # Stamp the hash on success so subsequent boots skip
                # this module until the file changes (e.g. via a deploy
                # that updates the law/procurement/etc. text content).
                cur_hash = await _module_hash(modname)
                if cur_hash:
                    try:
                        await _rs.set(
                            f"crucix:knowledge_seed:hash:{modname}",
                            cur_hash,
                            ex=30 * 86400,  # 30 days
                        )
                    except Exception as e:
                        logger.debug("seed hash write failed for %s: %s", modname, e)
            except Exception as e:
                summary[modname] = {"error": str(e)}
                logger.warning("[Knowledge Seed] %s ingestion failed (non-fatal): %s", label, e)

        try:
            from .intel import international_law
            reg = await international_law.register_law_sources()
            summary["law_sources"] = reg
            logger.info("[Knowledge Seed] Law sources registered: %d", reg.get("registered", 0))
        except Exception as e:
            summary["law_sources"] = {"error": str(e)}
            logger.warning("[Knowledge Seed] Law source registration failed (non-fatal): %s", e)
        try:
            from .intel import contract_intelligence
            clause_result = await contract_intelligence.ingest_clause_library()
            summary["clause_library"] = clause_result
            logger.info(
                "[Knowledge Seed] Clause library: %d clauses, %d chunks",
                clause_result.get("clauses_ingested", 0),
                clause_result.get("total_chunks", 0),
            )
        except Exception as e:
            summary["clause_library"] = {"error": str(e)}
            logger.warning("[Knowledge Seed] Clause library ingestion failed (non-fatal): %s", e)

        # Mark seed completion. Even a partial run counts — the URL-dedup
        # layer makes the next run cheap, and the marker prevents
        # thundering-herd retries on rolling restarts.
        try:
            await _rs.set(_SEED_MARKER_KEY, str(time.time()), ex=_SEED_CACHE_TTL * 4)
        except Exception as e:
            logger.debug("seed marker write failed (non-fatal): %s", e)
        summary["completed_at"] = time.time()
        return summary

    # Expose for the /api/aria/knowledge/reseed route.
    app.state.run_knowledge_seed = run_knowledge_seed

    async def _seed_knowledge_bg():
        await asyncio.sleep(25)  # Wait for RAG + sentence-transformers
        force = (_os.getenv("ARIA_FORCE_RESEED", "") or "").strip().lower() in ("1", "true", "yes", "on")
        try:
            await run_knowledge_seed(force=force)
        except Exception as e:
            logger.warning("[Knowledge Seed] unhandled error (non-fatal): %s", e)

    knowledge_seed_task = asyncio.create_task(_seed_knowledge_bg())

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    try:
        from .autonomous import engine as _autonomous_engine
        await _autonomous_engine.stop_engine()
    except Exception as e:
        logger.warning("Autonomous engine shutdown failed (non-fatal): %s", e)
    if knowledge_seed_task:
        knowledge_seed_task.cancel()
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
    if tender_monitor_task:
        tender_monitor_task.cancel()
    # F94: flush any pending knowledge writes to disk before exit so the
    # last <FLUSH_DEBOUNCE_S of in-memory mutations aren't lost on a
    # clean shutdown / deploy.
    try:
        await knowledge.shutdown()
    except Exception as e:
        logger.warning("knowledge.shutdown failed (non-fatal): %s", e)
    # F110: same protection for the intel ledger — without this, the last
    # ~2s of channel/ingest signals (and any sweep-burst mid-flush) are
    # lost on every deploy.
    try:
        await intel_ledger.shutdown()
    except Exception as e:
        logger.warning("intel_ledger.shutdown failed (non-fatal): %s", e)
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
    """Public liveness + minimal autonomy state.

    Deliberately exposes only boolean indicators — no task IDs, no run
    history, no vendor credentials. The authed /api/aria/autonomous/
    status endpoint is the rich view; this one is safe to publish on a
    status page.
    """
    llm = app.state.llm_provider
    llm_stats = {}
    llm_chain: dict = {}
    if hasattr(llm, "get_stats"):
        llm_stats = llm.get_stats()
    if hasattr(llm, "get_health"):
        # Chain-level summary — "resilient" is the load-bearing signal;
        # raw per-provider stats stay in llm_fallback_stats for operators.
        # A cooling provider is the fallback chain WORKING — status should
        # only flip to degraded when no provider can serve.
        llm_chain = llm.get_health()

    # Autonomy indicator — is the 24/7 loop actually running right
    # now? Boolean only, plus the last-tick age so an observer can
    # tell "enabled but stuck" from "enabled and ticking".
    autonomous_ind = {
        "enabled": False,
        "running": False,
        "dry_run": True,
        "autonomy_level": 0,
        "seconds_since_last_tick": None,
        "tasks_loaded": 0,
    }
    try:
        from .autonomous import engine as _eng, tasks as _tsk
        status = _eng.get_engine_status()
        autonomous_ind["enabled"] = bool(status.get("enabled"))
        autonomous_ind["running"] = bool(status.get("running"))
        autonomous_ind["dry_run"] = bool(status.get("dry_run"))
        autonomous_ind["autonomy_level"] = int(status.get("autonomy_level", 0))
        last_tick = status.get("last_tick_at")
        if last_tick:
            import time
            autonomous_ind["seconds_since_last_tick"] = int(time.time() - last_tick)
        try:
            autonomous_ind["tasks_loaded"] = len(_tsk.get_loaded_tasks())
        except Exception:
            pass
    except Exception:
        pass

    # Health rollup — service is operational only if LLM is configured
    # AND (autonomous is off OR autonomous is running healthily). A
    # stuck autonomous loop is worse than off.
    autonomous_healthy = (
        not autonomous_ind["enabled"]  # off is fine for liveness purposes
        or (
            autonomous_ind["running"]
            and (autonomous_ind["seconds_since_last_tick"] is None
                 or autonomous_ind["seconds_since_last_tick"] < 180)
        )
    )

    # Self-diagnostic rollup (2026-04-18) — safe-to-publish summary of
    # module wiring health. Detailed report at /api/aria/diagnostic/details
    # behind auth. Read the cached result (refreshed every 15min by the
    # autonomous task) so /health stays fast.
    diagnostic_ind: dict = {"overall": "UNKNOWN"}
    try:
        from .intel import redis_store as rs
        latest = await rs.get_json("crucix:self_diagnostic:latest")
        if latest:
            diagnostic_ind = {
                "overall": latest.get("overall"),
                "counts": latest.get("counts"),
                "critical_failures": latest.get("critical_failures", []),
                "generated_at": latest.get("generated_at"),
            }
    except Exception:
        pass

    # Top-level status: "operational" iff the chain can serve a request
    # (≥1 non-cooling provider) AND the autonomous loop isn't stuck. A
    # cooling primary with a live fallback is NOT degraded — the chain
    # is doing its job.
    chain_resilient = llm_chain.get("resilient") if llm_chain else bool(llm and llm.is_configured)
    return {
        "status": "operational" if (chain_resilient and autonomous_healthy) else "degraded",
        "service": "aria",
        "llm_provider": llm.name if llm else "none",
        "llm_configured": bool(llm and llm.is_configured),
        "llm_chain": llm_chain,
        "llm_fallback_stats": llm_stats,
        "autonomous": autonomous_ind,
        "diagnostic": diagnostic_ind,
    }


@app.get("/diagnostic")
async def public_diagnostic():
    """Public diagnostic summary — binary PASS/FAIL per module cluster.
    No per-check notes, no infra details. Safe to publish on status
    page. Rich details at /api/aria/diagnostic/details (auth required)."""
    try:
        from .intel import self_diagnostic as _sd
        return await _sd.run_diagnostic_summary()
    except Exception as e:
        return {"ok": False, "overall": "UNKNOWN", "error": str(e)[:200]}


@app.post("/api/aria/zoom/webhook")
async def zoom_webhook_ep(request: Request):
    """Zoom webhook receiver — NOT auth-protected (Zoom sends its own signature).

    Handles:
      - recording.completed → auto-download + process transcript
      - meeting.ended → log metadata
      - endpoint.url_validation → Zoom verification challenge
    """
    try:
        from .intel import zoom_integration as zoom
        body = await request.json()

        # Verify Zoom signature if webhook secret is set
        signature = request.headers.get("x-zm-signature", "")
        timestamp = request.headers.get("x-zm-request-timestamp", "")
        if zoom._WEBHOOK_SECRET and signature:
            raw_body = await request.body()
            if not zoom.verify_webhook_signature(raw_body, signature, timestamp):
                from fastapi import HTTPException
                raise HTTPException(status_code=401, detail="Invalid Zoom webhook signature")

        llm = getattr(app.state, "llm_provider", None)
        result = await zoom.handle_webhook(body, llm=llm)
        return result
    except Exception as e:
        logger.warning("Zoom webhook error: %s", e)
        return {"error": str(e)}


@app.post("/api/aria/ingest", dependencies=[Depends(require_aria_token)])
async def ingest_sweep(request: Request):
    """Receive sweep data from Node.js server to update intel layers + neural network.

    Auth-protected: writes to persistent intel/neural state, so this endpoint
    must NOT be reachable without the bearer token. Mounted on `app` directly
    rather than via `aria_router` for historical reasons, so the token check
    is wired in explicitly here instead of inheriting it from the router.

    Body parse is manual rather than `data: dict` so validation failures log
    the offending payload (first 200 bytes) instead of returning an opaque
    FastAPI 422. Past symptom: a single 422 appeared in the log with no way
    to tell whether it was malformed JSON, a non-dict top-level, or a shape
    mismatch from the WhatsApp mirror (which posts WA-shaped payloads here).
    """
    try:
        raw = await request.body()
    except Exception as e:
        logger.warning("ingest: body read failed: %s", e)
        raise HTTPException(status_code=400, detail="body_read_failed")

    try:
        data = json.loads(raw) if raw else {}
    except Exception as e:
        preview = (raw[:200] if raw else b"").decode("utf-8", errors="replace")
        logger.warning("ingest: JSON parse failed (%s). Body first 200b: %r", e, preview)
        raise HTTPException(status_code=400, detail="invalid_json")

    if not isinstance(data, dict):
        logger.warning(
            "ingest: expected dict body, got %s. Preview: %r",
            type(data).__name__, str(data)[:200],
        )
        raise HTTPException(status_code=400, detail="expected_dict_body")

    app.state.current_data = data
    ledger_count = await intel_ledger.ingest_sweep_signals(data)
    comp_count = await competitors.scan_for_moves(data)

    # Grow neural network from sweep signals.
    # Live observation 2026-04-27 17:35:23-17:35:34: a single sweep with 5
    # signals + 4 news items fired 9 sequential DeepSeek calls and held the
    # ingest connection open for 12 seconds. Parallelize with concurrency
    # cap so the rate limiter (RPM-bounded) still gates spend, and so one
    # slow item doesn't head-of-line-block the rest.
    #
    # Safety: learn_from_text mutates module-level _neurons / _edges via
    # SYNC helpers between awaits. Two parallel tasks cannot corrupt the
    # store -- each task runs its sync mutation block atomically per
    # async-scheduling-window. _persist() races are benign last-writer-wins.
    #
    # Cost lever: ARIA_NEURAL_SAMPLE_RATE (0.0-1.0, default 1.0) skips
    # the LLM-supplement on a fraction of items. Regex extract_concepts
    # still runs on all items (free), so neuron/edge creation is
    # preserved -- only the LLM-driven novel-entity catch is sampled.
    # 0.25 ≈ 75% reduction in DeepSeek/Anthropic spend on neural ingest.
    import random as _random
    raw_rate = _os.getenv("ARIA_NEURAL_SAMPLE_RATE", "1.0") or "1.0"
    try:
        sample_rate = max(0.0, min(1.0, float(raw_rate)))
    except ValueError:
        sample_rate = 1.0

    neural_count = 0
    llm = getattr(app.state, "llm_provider", None)
    sem = asyncio.Semaphore(5)

    async def _learn_one(text: str, source: str) -> int:
        async with sem:
            item_llm = llm if (sample_rate >= 1.0 or _random.random() < sample_rate) else None
            try:
                result = await neural_memory.learn_from_text(text, source=source, llm=item_llm)
                return result.get("neurons_activated", 0)
            except Exception as e:
                logger.warning("Neural ingest item failed (%s): %s", source, e)
                return 0

    learn_tasks: list = []
    signals = data.get("signals") or data.get("urgentSignals") or []
    for sig in signals[:20]:
        text = sig.get("text") or sig.get("content") or ""
        if text:
            learn_tasks.append(_learn_one(text, "sweep"))
    for item in (data.get("news") or [])[:10]:
        text = (item.get("title", "") + " " + item.get("summary", "")).strip()
        if text:
            learn_tasks.append(_learn_one(text, "news"))
    if learn_tasks:
        results = await asyncio.gather(*learn_tasks)
        neural_count = sum(results)

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
