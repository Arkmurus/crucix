"""
ARIA Service — FastAPI entrypoint. R-F1191: fully autonomous.

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
from typing import Any, Optional

import json

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

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
# R-F786 (2026-05-21) — eager-import document_reader so the first
# /api/aria/document/extract call doesn't pay the import cost on the
# response loop. Wedge stack /data/wedge_stacks/wedge_677_1779379566.log
# captured `extract_document_ep` blocked on `<module>` of
# document_reader.py during a 17.91s stall — same pattern as R-F772
# closed for counterparty_claim_ledger. document_reader pulls
# PyMuPDF/fitz + OCR backends, which open shared libs and take
# multi-second on cold import. Eager-loading at boot moves that cost
# into the startup window where the event loop isn't serving traffic.
from .intel import document_reader as _document_reader_module  # noqa: F401
from .intel import cost_tracker
from .intel.researcher import research_and_learn, get_hypotheses, validate_hypothesis
from .routes.aria import router as aria_router, require_aria_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("aria.main")

# R-F513 (2026-05-14): auto-derive build_rev from Dockerfile ARGs that
# are passed by deploy-fly.yml at build time. Pre-R-F513 ARIA_BUILD_REV
# was a hand-edited string that drifted — 27 commits on 2026-05-14
# shipped without a bump, so /health/live reported R-F422 while
# R-F509/F510 were live. Verify-after-fix was unreliable as a result.
#
# Env vars (set in aria_service/Dockerfile via --build-arg):
#   ARIA_BUILD_GIT_SHA — git SHA at build (e.g. "a698a4f...")
#   ARIA_BUILD_R_TAG   — most recent R-number(s) (e.g. "R-F511+F512+F513")
#
# Pass these on the CLI:
#   flyctl deploy --remote-only --build-arg ARIA_BUILD_GIT_SHA=$(git rev-parse HEAD) \
#                 --build-arg ARIA_BUILD_R_TAG="R-F513"
# CI already does this in .github/workflows/deploy-fly.yml.
#
# If neither env var is present (local dev, image rebuilt without
# args), fall back to a clear sentinel that lights up obviously in
# logs and /health so we know to redeploy with the args.
_BUILD_GIT_SHA = _os.environ.get("ARIA_BUILD_GIT_SHA", "").strip()
_BUILD_R_TAG = _os.environ.get("ARIA_BUILD_R_TAG", "").strip()


def _resolve_git_head_from_image(git_dir: str = "/app/.git") -> str:
    """R-F589 (2026-05-16) — runtime build_rev fallback.

    Pre-R-F589 the only path to build_rev was the --build-arg passed at
    docker build time. Manual `flyctl deploy` invocations (no wrapper)
    skipped the flag, so the Dockerfile ARG defaulted to "unknown" and
    /api/aria/health.build_rev reported UNKNOWN-BUILD even though the
    code was live.

    R-F589 bakes .git/HEAD + .git/refs/ into the image (via
    .dockerignore exceptions + COPY in the Dockerfile) so we can
    resolve HEAD at runtime regardless of how the deploy was invoked.
    Returns the resolved 40-char SHA, or "" if anything is missing.

    Pure-Python git ref resolution — no `git` binary needed in the
    image. Handles three HEAD shapes:
      - "ref: refs/heads/main"  → reads refs/heads/main
      - "ref: refs/heads/feature/foo" → reads refs/heads/feature/foo
      - "<40-char-sha>"         → detached HEAD; return SHA directly
    Also handles packed-refs fallback (long-lived clones with many tags).
    """
    try:
        head_path = _os.path.join(git_dir, "HEAD")
        if not _os.path.isfile(head_path):
            return ""
        with open(head_path, "r", encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            # Detached HEAD — first 40 chars are the SHA
            return head[:40] if len(head) >= 40 else head
        ref_path = head[len("ref:"):].strip()
        ref_file = _os.path.join(git_dir, ref_path)
        if _os.path.isfile(ref_file):
            with open(ref_file, "r", encoding="utf-8") as f:
                return f.read().strip()[:40]
        # Fallback to packed-refs (long-lived clones often pack refs)
        packed = _os.path.join(git_dir, "packed-refs")
        if _os.path.isfile(packed):
            with open(packed, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.strip().split(" ", 1)
                    if len(parts) == 2 and parts[1] == ref_path:
                        return parts[0][:40]
        return ""
    except Exception:
        return ""


if _BUILD_GIT_SHA and _BUILD_GIT_SHA != "unknown":
    _sha_short = _BUILD_GIT_SHA[:8]
    _source = "build-arg"
elif (_runtime_sha := _resolve_git_head_from_image()):
    # R-F589 fallback — operator skipped --build-arg, but .git/HEAD is
    # in the image so we still know which commit is running.
    _BUILD_GIT_SHA = _runtime_sha
    _sha_short = _runtime_sha[:8]
    _source = "git-head-runtime"
else:
    _sha_short = ""
    _source = "unknown"

# R-F920 (2026-05-26) — build-source is tracked SEPARATELY from the
# user-facing build_rev string. Per CLAUDE.md §14 (fallback transparency): a
# runtime-resolved SHA is the CORRECT commit and IS working, so the user-facing
# footer must read cleanly ("sha 3a9139f") — not "· R-F589 runtime fallback
# (build-arg missing)", which leaked an internal deploy detail into every
# WhatsApp answer (live 2026-05-26) and read as if ARIA were broken. The
# build-arg-skipped signal still reaches operators via the startup log and the
# ARIA_BUILD_SOURCE field on /api/aria/health — just not the customer footer.
ARIA_BUILD_SOURCE = _source
if _sha_short:
    if _BUILD_R_TAG:
        ARIA_BUILD_REV = f"{_BUILD_R_TAG} · sha {_sha_short}"
    else:
        ARIA_BUILD_REV = f"sha {_sha_short}"
else:
    # Build-arg AND .git/HEAD both missing — final fallback string.
    # Operator should run `scripts/fly_deploy.sh` or pass --build-arg
    # explicitly so the metadata reflects the running commit.
    ARIA_BUILD_REV = "UNKNOWN-BUILD · ARIA_BUILD_GIT_SHA not set at image build (pass --build-arg)"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("ARIA Service starting...")
    logger.info("ARIA Build: %s", ARIA_BUILD_REV)
    # R-F920 — operator-facing signal that the deploy skipped --build-arg and we
    # resolved the SHA from the in-image .git/HEAD. The user-facing footer stays
    # clean (§14); this WARNING tells operators to use scripts/fly_deploy.sh / CI.
    if ARIA_BUILD_SOURCE == "git-head-runtime":
        logger.warning(
            "ARIA Build SHA resolved at runtime from .git/HEAD (deploy skipped "
            "--build-arg ARIA_BUILD_GIT_SHA). SHA is correct; use scripts/fly_deploy.sh "
            "or CI so build metadata is passed at build time."
        )

    # Connect Redis / SQLite / memory backend per ARIA_STATE_BACKEND.
    # R-F762 (2026-05-20): capture the result so /health can flag the
    # backend as RED when connect fails. Pre-R-F762 a Redis-unreachable
    # boot would silently fall back to in-process _mem_store
    # (knowledge cache grows in RAM, lost on restart) and the operator
    # only noticed by manually inspecting fly logs or seeing /health
    # report degraded for unrelated reasons. Now the state-backend
    # health rolls up into /health's top-level status and the
    # /health.state_backend block shows backend (sqlite/upstash/memory)
    # + reachable bool + a status string for observers.
    try:
        _state_connect_ok = await rs.connect(settings.redis_url)
    except Exception as _state_e:
        logger.error(
            "[R-F762] state-backend connect raised — falling back to "
            "in-memory dict (data lost on restart): %s",
            _state_e, exc_info=True,
        )
        _state_connect_ok = False
    app.state.state_backend = (rs._BACKEND if hasattr(rs, "_BACKEND") else "unknown")
    app.state.state_backend_reachable = bool(_state_connect_ok)
    if not _state_connect_ok:
        logger.error(
            "[R-F762] state-backend connect FAILED (backend=%s). "
            "Knowledge cache will grow in RAM and be lost on restart. "
            "Check ARIA_STATE_BACKEND env var + /data volume mount + "
            "Upstash subscription state. /health will show backend=red.",
            app.state.state_backend,
        )

    # R-F1178: install the error ledger handler so WARNING+ log entries
    # are persisted to Redis and the self_coder's _monitor_post_deploy
    # can detect regressions after auto-deploy. Pre-R-F1178 the handler
    # was only installed in tests, so the error count key had no producer
    # in production and the post-deploy monitor was a permanent no-op.
    try:
        from .intel import error_log_handler
        error_log_handler.install()
        logger.info("[R-F1178] Error ledger handler installed")
    except Exception as _elh_err:
        logger.warning("[R-F1178] Error ledger handler install failed: %s", _elh_err)

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

    # ── R-F504 (2026-05-14) — ARIA's own search index ───────────────────
    # Opens a separate SQLite file at /data/aria_search.db (configurable
    # via ARIA_SEARCH_DB_PATH) for the curated FTS5 corpus that powers
    # the independence path away from third-party search APIs. Registers
    # the seed_list domains at boot; the actual crawl runs out-of-band
    # (admin endpoint or scheduled job in a follow-up R-number).
    # Non-fatal: a failure here just means chat falls back to the
    # legacy web_search.search() path until the index is reachable.
    try:
        from .search_index import db as _search_db
        _ok = await _search_db.connect()
        if _ok:
            from .crawler import seed_list as _seeds
            _n = await _seeds.seed_all()
            logger.info(
                "[R-F504] search index ready (%d seed domains registered)", _n,
            )
        else:
            logger.warning(
                "[R-F504] search index connect() returned False — "
                "chat will fall back to web_search only",
            )
    except Exception as _exc:
        logger.warning(
            "[R-F504] search index init failed (non-fatal): %s", _exc,
        )

    # ── R-F507 (2026-05-14) — light the crawler ─────────────────────────
    # Game-changer move: the engine fills itself. Background task
    # rotates through the seed domains every CRAWL_INTERVAL_S (default
    # 6 h). Gated by ARIA_CRAWLER_DISABLED — set to "1" to keep the
    # engine dark (useful for first-deploy verification or volume cap
    # exposure investigation).
    _crawler_task = None
    _crawler_stop_event = None
    # R-F508 (2026-05-14): use _f28_os (already imported at line 78), NOT
    # _os — the inner `import os as _os` on line 168 makes _os a LOCAL
    # variable for the whole function, so referencing it BEFORE that line
    # raises UnboundLocalError at startup. R-F507 author missed the F28
    # warning at lines 72-77 and took prod down at 07:28 UTC.
    if _f28_os.getenv("ARIA_CRAWLER_DISABLED", "").lower() not in ("1", "true", "yes"):
        try:
            from .crawler import runner as _crunner
            _crawler_stop_event = asyncio.Event()
            _crawl_interval = int(
                _f28_os.getenv("ARIA_CRAWLER_INTERVAL_SEC", "21600"))  # 6h
            _crawler_task = asyncio.create_task(
                _crunner.crawl_loop(
                    interval_sec=_crawl_interval,
                    stop_event=_crawler_stop_event,
                ),
            )
            logger.info(
                "[R-F507] crawler attached (interval=%ds, set "
                "ARIA_CRAWLER_DISABLED=1 to disable)", _crawl_interval,
            )
        except Exception as _exc:
            logger.warning(
                "[R-F507] crawler attach failed (non-fatal): %s", _exc,
            )
    else:
        logger.info("[R-F507] crawler DISABLED via ARIA_CRAWLER_DISABLED env")

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

    # ── R-F459 (2026-05-14) — sentence-transformer prewarm ──────────────
    # Fire the prewarm IMMEDIATELY (no sleep) as its own background task
    # so the ~32s cold model load happens DURING boot, in a worker
    # thread, off the event loop. By the time fly's healthcheck grace
    # period (30s) elapses and real HTTP traffic arrives, the model is
    # warm and the embed-load race no longer fires.
    #
    # Pre-R-F459 history:
    #   - R-F379 (queued 2026-05-12, never shipped) called for this
    #   - R-F458 (2026-05-13 22:18) attempted but used a 15s sleep
    #     before prewarming AND added a threading.Lock — combined effect
    #     was that the first request beat the prewarm to _get_embedder,
    #     held the lock for 32s, and the lock serialised other waiting
    #     threads → ThreadPoolExecutor saturated → GIL starvation →
    #     /health/live timed out → fly PR04 cascade → reverted 22:25.
    #   - R-F459 (this commit) — prewarm fires at T+0 (not T+15) AND
    #     the lock-pattern was validated locally on Python 3.14 first.
    async def _embedder_prewarm_bg():
        try:
            from .intel.semantic_search import prewarm_embedder
            await prewarm_embedder()
            logger.info("[R-F459] sentence-transformer prewarm complete")
        except Exception as exc:
            logger.warning(
                "[R-F459] sentence-transformer prewarm failed "
                "(non-fatal, lazy load will retry): %s", exc,
            )
    asyncio.create_task(_embedder_prewarm_bg())

    # ── R-F703 (2026-05-18) — event-loop stall detector ────────────────
    # The fly /health/live timeout pattern is consistent: the event loop
    # gets blocked by sync CPU work (most commonly sentence_transformers
    # encode under torch's GIL, occasionally large JSON load/save) for
    # 8-30s; during the stall /health/live can't respond → fly LB marks
    # the machine unhealthy → PR04 cascade. The 19:52:34 wedge showed
    # this exactly — autonomy_surface's 4 parallel asyncio.wait_for(...,
    # timeout=8.0) calls all expired at the same wall-clock instant,
    # which is only possible if the loop was wall-clock-stuck for ≥8s.
    #
    # Pre-this-detector we had no on-line signal of *what* was blocking
    # the loop; gate #3 (0 fly ERRORs/7d) has been blocked by recurring
    # ~4-min outage cycles with the actual blocker invisible. This
    # background task wakes every 1s; whenever the real elapsed wall-
    # clock between wakeups exceeds 5s, it logs WARNING with the
    # measured stall duration. The next wedge will be timestamped so
    # we can correlate against what was running at that instant.
    #
    # Implementation is deliberately tiny:
    #   - one asyncio.sleep(1.0) per iteration (zero CPU when idle)
    #   - monotonic-clock measurement (immune to wall-clock jumps)
    #   - logs at WARNING so it joins the error-ledger (R-F381) and
    #     surfaces on the operator dashboard's recent-errors panel.
    #   - 5s threshold chosen so spurious GC pauses (typically <1s)
    #     don't spam; an 8s autonomy_surface timeout = real wedge.
    #
    # R-F704 (2026-05-18) — stack-capture extension. Pre-R-F704 the
    # detector logged "stall happened" but not "what was running". The
    # wedge had already ended by the time the detector iteration runs
    # (it can only wake AFTER the blocking sync work returned and the
    # loop became schedulable again). So we add a sibling daemon
    # *thread* (not coroutine) that updates from a wall-clock heartbeat
    # that the loop posts; when the heartbeat goes stale the thread
    # captures live stack frames via faulthandler.dump_traceback. The
    # daemon thread is OS-scheduled — torch / numpy / sentence_transformers
    # release the GIL during their actual compute, so a 1-second sleep
    # in the daemon can wake and grab the GIL even mid-stall.
    import faulthandler as _fh
    import threading as _threading
    import time as _time

    # R-F710 (2026-05-19) — prefer fly's persistent /data volume so the
    # wedge log survives reboots. Pre-R-F710 the path resolved to
    # /app/data/wedge_stacks/ inside the container, which is wiped on
    # every fly machine restart — including every deploy. The 07:18:58
    # wedge captured immediately after R-F704's deploy would have been
    # gone the moment the next deploy landed. /data is the fly volume
    # mount (same root as aria_state.db / aria_knowledge.json / aria_rag).
    # Local dev (no /data dir) falls back to the repo-local path.
    if _os.path.isdir("/data") and _os.access("/data", _os.W_OK):
        _wedge_dir = "/data/wedge_stacks"
    else:
        _wedge_dir = _os.path.join(_os.path.dirname(__file__), "..", "data", "wedge_stacks")
    try:
        _os.makedirs(_wedge_dir, exist_ok=True)
    except Exception:
        pass
    _wedge_log_path = _os.path.join(
        _wedge_dir, f"wedge_{_os.getpid()}_{int(_time.time())}.log"
    )
    try:
        _wedge_log_fh = open(_wedge_log_path, "a", buffering=1, encoding="utf-8")
        logger.info("[R-F704] wedge stack log → %s", _wedge_log_path)
    except Exception as _exc:
        logger.warning("[R-F704] could not open wedge log (non-fatal): %s", _exc)
        _wedge_log_fh = None

    # Shared monotonic heartbeat the async detector bumps every 1s.
    # Initialised in the future — the daemon won't begin until after
    # the 120s settle window passes (matching the async detector).
    _wedge_state = {
        "heartbeat": _time.monotonic(),
        "armed": False,
        "last_dump": 0.0,
    }
    _STALL_WARN_THRESHOLD_S = 5.0
    _WEDGE_DUMP_DEBOUNCE_S = 30.0  # don't dump more than once per 30s

    # R-F814 (2026-05-22) — local sys import for is_finalizing() guard
    # below. Module-level `import sys` isn't otherwise needed in main.py;
    # keeping the import local to the watchdog avoids polluting the rest
    # of the file.
    import sys as _sys

    def _wedge_watchdog():
        # Daemon thread. Runs forever; cleanly exits when fh closed.
        while True:
            try:
                _time.sleep(1.0)
                # R-F814 (2026-05-22) — skip during interpreter shutdown.
                # Pre-R-F814 every graceful machine restart (deploy or
                # health-check restart) produced a false-positive "event-
                # loop stalled" wedge dump:
                #   1. uvicorn catches SIGTERM, lifespan unwinds, the
                #      _event_loop_stall_detector task is cancelled.
                #   2. The asyncio loop closes; nothing updates
                #      _wedge_state["heartbeat"] anymore.
                #   3. This daemon watchdog keeps running (daemon=True),
                #      sees the stale heartbeat, dumps stacks.
                #   4. Captured stack shows the main thread at
                #      threading.py:1543 _shutdown — the actual interpreter
                #      shutdown sequence, NOT a runtime wedge.
                # Live evidence (2026-05-22 21:06:41 + 21:18:27 UTC):
                # both captured wedges showed the _shutdown frame —
                # confirmed false positives produced once per release
                # cycle (the app saw 5+ deploys in 4h on 2026-05-22).
                # `sys.is_finalizing()` is True from the moment the
                # interpreter starts shutting down. Bail cleanly rather
                # than emit operator-confusing noise.
                if _sys.is_finalizing():
                    return
                if not _wedge_state.get("armed"):
                    continue
                now = _time.monotonic()
                stale = now - _wedge_state["heartbeat"]
                if (
                    stale > _STALL_WARN_THRESHOLD_S
                    and (now - _wedge_state["last_dump"]) > _WEDGE_DUMP_DEBOUNCE_S
                    and _wedge_log_fh is not None
                ):
                    _wedge_state["last_dump"] = now
                    try:
                        _wedge_log_fh.write(
                            f"\n=== [R-F704] event-loop heartbeat stale "
                            f"by {stale:.2f}s at wall-clock "
                            f"{_time.strftime('%Y-%m-%d %H:%M:%S UTC', _time.gmtime())} "
                            f"(epoch {_time.time():.3f}) ===\n"
                        )
                        _fh.dump_traceback(file=_wedge_log_fh, all_threads=True)
                        _wedge_log_fh.write("=== end stack dump ===\n")
                        _wedge_log_fh.flush()
                    except Exception:
                        # Daemon must never crash — swallow and continue.
                        pass
            except Exception:
                # Defensive: keep watchdog alive across any failure.
                continue

    _threading.Thread(
        target=_wedge_watchdog,
        daemon=True,
        name="rf704-wedge-watchdog",
    ).start()

    async def _event_loop_stall_detector():
        import asyncio as _aio
        # Wait until the lifespan settle window has fully passed before
        # starting to measure. Cold-boot hydration legitimately stalls
        # the loop for tens of seconds (RAG, knowledge load, OCR
        # prewarm); we only care about post-warm stalls.
        await _aio.sleep(120)
        _wedge_state["heartbeat"] = _time.monotonic()
        _wedge_state["armed"] = True
        logger.info(
            "[R-F703] event-loop stall detector armed (threshold=%.1fs); "
            "[R-F704] watchdog will dump live stacks to %s on stall",
            _STALL_WARN_THRESHOLD_S,
            _wedge_log_path,
        )
        last = _time.monotonic()
        while True:
            try:
                await _aio.sleep(1.0)
            except _aio.CancelledError:
                return
            now = _time.monotonic()
            elapsed = now - last
            last = now
            _wedge_state["heartbeat"] = now
            if elapsed > _STALL_WARN_THRESHOLD_S:
                logger.warning(
                    "[R-F703] event loop stalled for %.2fs (threshold=%.1fs) — "
                    "synchronous CPU work blocked the loop. Likely culprits: "
                    "sync sentence_transformers.encode(), large JSON load/save, "
                    "or unwrapped CPU-bound work. Correlate with concurrent "
                    "log lines around this timestamp. [R-F704] check %s for "
                    "live-stack capture from the wedge watchdog.",
                    elapsed, _STALL_WARN_THRESHOLD_S, _wedge_log_path,
                )
    asyncio.create_task(_event_loop_stall_detector())

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
        "groq": _os.environ.get("GROQ_API_KEY", ""),
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

    # ── R-F673 (2026-05-17) — explicit dialogue_state DB init ──────────
    # dialogue_state.py lazily creates its aiosqlite connection + schema
    # on first call. That's fine for normal traffic, but it means a
    # boot-time misconfiguration (volume not mounted, schema mismatch)
    # only surfaces when the FIRST chat turn lands — sometimes minutes
    # after deploy. Calling _ensure_conn here forces the failure into
    # the boot log so /api/aria/health/live can't go green over a
    # broken dialogue store.
    try:
        from .intel import dialogue_state as _ds_boot
        await _ds_boot._ensure_conn()
        logger.info("[R-F673] dialogue_state DB init ✓")
    except Exception as _ds_e:
        logger.warning(
            "[R-F673] dialogue_state init failed at boot — open-question "
            "tracking will be degraded until DB is reachable: %s",
            _ds_e,
        )

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
        except Exception as _snap_e:
            # R-F672 (2026-05-17): promoted from silent pass per audit
            # — boot-snapshot persistence failure means R-F248 boot-state
            # diff loses the previous baseline, silently masking the next
            # regression. Log so the operator sees it in fly logs.
            logger.warning(
                "R-F672: boot snapshot persistence failed (next boot-diff "
                "will be against an older baseline): %s",
                _snap_e,
            )

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
                        except Exception as _absorb_e:
                            # R-F672 (2026-05-17): promoted from silent
                            # pass — if brain_hook fails to record the
                            # regression, we still want the failure
                            # itself logged so the operator knows the
                            # alert was generated but didn't land.
                            logger.warning(
                                "R-F672: brain_hook.absorb for boot_state "
                                "regression failed (alert may not surface "
                                "on dashboard): %s",
                                _absorb_e,
                            )
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
                    await _tick_heartbeat("research_engine", "RSS feeds → fact extraction → hypothesis validation")
                    logger.info("[Research] Starting autonomous research cycle...")
                    result = await research_and_learn(llm)
                    await _wire_agent_success(
                        "research_engine",
                        f"Research cycle: {result.get('facts_learned', 0)} facts, "
                        f"{result.get('hypotheses_generated', 0)} hypotheses",
                    )
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
                    await _wire_agent_failure("research_engine", f"Cycle failed: {e}")
                    logger.warning(f"[Research] Cycle failed: {e}")
                finally:
                    cost_tracker.reset_feature(_t)
                    reset_priority(_p)
                await asyncio.sleep(30 * 60)  # Every 30 minutes

        research_task = asyncio.create_task(_research_loop())
        logger.info("Research scheduler started (every 30min)")

    # ── R-F1207/R-F1209: Register all background loops in the agent registry ─────
    # Every autonomous loop registers itself so the multi-agent awareness
    # protocol (R-F1160) can see who's running, what they're doing, and
    # detect stale/dead agents. Registration is best-effort (non-fatal).
    # R-F1209: each loop also ticks its heartbeat every iteration so the
    # registry knows the agent is alive and working.
    async def _register_agent(agent_id: str, agent_type: str, task: str) -> None:
        try:
            from .intel.agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.register(agent_id, agent_type, current_task=task)
        except Exception:
            pass

    async def _tick_heartbeat(agent_id: str, current_task: str = "") -> None:
        """Tick an agent's heartbeat in the registry. Best-effort, non-fatal."""
        try:
            from .intel.agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.tick_heartbeat(agent_id, current_task=current_task or None)
        except Exception:
            pass

    async def _wire_agent_success(agent_id: str, summary: str) -> None:
        """Wire an agent's successful cycle to the brain. Best-effort."""
        try:
            from .intel.engine_wiring import wire_success
            wire_success(
                module=agent_id,
                summary=summary[:300],
                source_id=f"agent:{agent_id}",
            )
        except Exception:
            pass

    async def _wire_agent_failure(agent_id: str, detail: str) -> None:
        """Wire an agent's failed cycle to the brain. Best-effort."""
        try:
            from .intel.engine_wiring import wire_failure
            wire_failure(
                module=agent_id,
                detail=detail[:600],
                gap_type="agent_cycle_failure",
                source=f"agent:{agent_id}",
            )
        except Exception:
            pass

    # Register research engine
    if research_enabled:
        asyncio.create_task(_register_agent(
            "research_engine", "autonomous_research",
            "RSS feeds → fact extraction → hypothesis validation (every 30min)",
        ))

    # Register self-improvement engine
    if llm and llm.is_configured:
        asyncio.create_task(_register_agent(
            "self_improve", "autonomous_self_improve",
            "Error-ledger analysis → bug detection → auto-fix → auto-deploy (every 2h)",
        ))

    # Register student loops
    asyncio.create_task(_register_agent(
        "student_quiz", "student_brain",
        "Self-quiz on weak topics, mastery tracking (every 3h)",
    ))
    asyncio.create_task(_register_agent(
        "student_reading", "student_brain",
        "Study articles on weak topics (every 6h)",
    ))
    asyncio.create_task(_register_agent(
        "library_consolidation", "student_brain",
        "Archive stale reasoning cases (daily)",
    ))

    # Register proactive watch
    asyncio.create_task(_register_agent(
        "proactive_watch", "proactive_engine",
        "Daily briefing trigger + mastery prep (hourly)",
    ))

    # Register weekly report
    asyncio.create_task(_register_agent(
        "weekly_report", "reporting_engine",
        "Weekly learning report (Monday 06-08 UTC)",
    ))

    # Register watchlist re-screen
    asyncio.create_task(_register_agent(
        "watchlist_rescreen", "dd_engine",
        "Re-screen DD watchlist entities against sanctions/PEP (daily)",
    ))

    # Register tender monitor
    asyncio.create_task(_register_agent(
        "tender_monitor", "procurement_engine",
        "Crawl defence procurement portals (every 6h)",
    ))

    # Register crawler
    if _f28_os.getenv("ARIA_CRAWLER_DISABLED", "").lower() not in ("1", "true", "yes"):
        asyncio.create_task(_register_agent(
            "web_crawler", "search_index",
            "Web crawl seed domains for search index (every 6h)",
        ))

    # Register Web Integrity Agent (started below)
    asyncio.create_task(_register_agent(
        "web_integrity", "monitoring",
        "24/7 endpoint monitoring, input/output validation, error pattern detection",
    ))

    # Register self-healing (already done in start_self_healing, but ensure it's registered)
    asyncio.create_task(_register_agent(
        "self_healing", "infrastructure",
        "Health checks, circuit breakers, auto-recovery, ecosystem repair",
    ))

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
                    await _tick_heartbeat("self_improve", "Error-ledger analysis → bug detection → auto-fix")
                    logger.info("[Self-Improve] Starting autonomous improvement cycle...")
                    result = await self_improve.autonomous_improvement_cycle(llm)
                    await _wire_agent_success(
                        "self_improve",
                        f"Improvement cycle: {result.get('bugs_detected', 0)} bugs, "
                        f"{result.get('auto_deployed', 0)} deployed",
                    )
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
                    await _wire_agent_failure("self_improve", f"Cycle failed: {e}")
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
                await _tick_heartbeat("student_quiz", "Self-quiz on weak topics")
                result = await student.self_quiz(num_questions=5)
                await _wire_agent_success(
                    "student_quiz",
                    f"Quiz: {result.get('quizzed', 0)} questions, "
                    f"score {result.get('score', 0):.2f}",
                )
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
                await _wire_agent_failure("student_quiz", f"Quiz failed: {e}")
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
                await _tick_heartbeat("student_reading", "Study articles on weak topics")
                result = await student.reading_session(llm=llm, num_articles=4)
                await _wire_agent_success(
                    "student_reading",
                    f"Reading: {result.get('articles_read', 0)} articles on "
                    f"{result.get('weak_topics_studied', [])}",
                )
                logger.info(
                    "[Student] Reading session: %d articles studied on %s",
                    result.get("articles_read", 0),
                    result.get("weak_topics_studied", []),
                )
            except Exception as e:
                await _wire_agent_failure("student_reading", f"Reading session failed: {e}")
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
                await _tick_heartbeat("library_consolidation", "Archive stale reasoning cases")
                result = await reasoning_library.consolidate()
                await _wire_agent_success(
                    "library_consolidation",
                    f"Consolidated: archived {result.get('archived', 0)}, "
                    f"{result.get('remaining', 0)} remaining",
                )
                # R-F242 (2026-05-13): log archived + missing distinctly.
                # Pre-R-F242 the log said "pruned N" but consolidate now
                # archives (preserves) cases instead of deleting. Surface
                # the honest counts so the daily log doesn't imply data
                # was lost.
                logger.info(
                    "[Student] Library consolidated: archived %d (preserved), "
                    "missing %d (Redis data lost), remaining %d in active index",
                    result.get("archived", 0),
                    result.get("missing", 0),
                    result.get("remaining", 0),
                )
            except Exception as e:
                await _wire_agent_failure("library_consolidation", f"Consolidate failed: {e}")
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
                await _tick_heartbeat("proactive_watch", "Daily briefing trigger + mastery prep")
                # Daily briefing check
                fired = await proactive.daily_briefing_check(getattr(app.state, "current_data", None))
                await _wire_agent_success(
                    "proactive_watch",
                    f"Briefing fired: {fired}, weak topics flagged",
                )
                if fired:
                    logger.info("[Proactive] Daily briefing fired")

                # Mastery prep
                weak_count = await proactive.prepare_weak_topics()
                if weak_count:
                    logger.info("[Proactive] Mastery prep: %d weak topic(s) flagged", weak_count)
            except Exception as e:
                await _wire_agent_failure("proactive_watch", f"Loop failed: {e}")
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
                await _tick_heartbeat("weekly_report", "Weekly learning report generation")
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                await _wire_agent_success("weekly_report", "Weekly report check cycle")
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
                await _wire_agent_failure("weekly_report", f"Loop failed: {e}")
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
                await _tick_heartbeat("watchlist_rescreen", "Re-screen DD watchlist entities against sanctions/PEP")
                from .intel import dd_orchestrator
                result = await dd_orchestrator.rescreen_watchlist(
                    llm=getattr(app.state, "llm_provider", None),
                )
                await _wire_agent_success(
                    "watchlist_rescreen",
                    f"Re-screen: {result.get('entities_screened', 0)} entities, "
                    f"{len(result.get('changes_detected', []))} changes",
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
                    except Exception as _wa_e:
                        # R-F672 (2026-05-17): keep at debug not warning
                        # — most fires here ARE "WA not configured"
                        # which is operationally fine. But promote out
                        # of silent-pass so a real WA outage isn't
                        # invisible.
                        logger.debug(
                            "R-F672: watchlist WA notification skipped: %s",
                            _wa_e,
                        )
            except Exception as e:
                await _wire_agent_failure("watchlist_rescreen", f"Re-screen failed: {e}")
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
                await _tick_heartbeat("tender_monitor", "Crawl defence procurement portals")
                from .intel import tender_monitor
                result = await tender_monitor.run_monitoring_cycle()
                await _wire_agent_success(
                    "tender_monitor",
                    f"Tender cycle: {result.get('new_tenders', 0)} new tenders "
                    f"across {result.get('portals_crawled', 0)} portals",
                )
                if result.get("new_tenders", 0) > 0:
                    logger.info(
                        "[Tender Monitor] %d new tenders detected across %d portals",
                        result["new_tenders"], result["portals_crawled"],
                    )
                else:
                    logger.info("[Tender Monitor] Cycle complete — no new tenders")
            except Exception as e:
                await _wire_agent_failure("tender_monitor", f"Cycle failed: {e}")
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

    # ── R-F803 (2026-05-22): autonomous self-coder boot ───────────────────
    # ARIACoder + GapDetector. Dormant unless ALL of these hold:
    #   ARIA_AUTONOMOUS_ENABLED=1  (existing master switch)
    #   ARIA_CODER_ENABLED=1       (this engine specifically)
    #   ARIA_INTERNAL_TOKEN set    (auth for /api/aria/coder/llm)
    #   app.state.redis available
    # See aria_service/autonomous/coder_entrypoint.py for the gates.
    # Returns a list[Task] (or None if any gate refused).
    aria_coder_tasks: list[asyncio.Task] = []
    try:
        from .autonomous.coder_entrypoint import start_aria_coder
        _coder_tasks = await start_aria_coder(app.state)
        if _coder_tasks:
            aria_coder_tasks = _coder_tasks
            logger.info(
                "[R-F803] ARIA-Coder started with %d background tasks",
                len(aria_coder_tasks),
            )
    except Exception as _coder_e:
        # Never let a coder-init exception block the lifespan — the engine
        # is non-essential for chat / DD traffic.
        logger.warning(
            "[R-F803] ARIA-Coder init failed (non-fatal): %s", _coder_e,
        )

    # R-F1207 — start Web Integrity Agent (24/7 endpoint monitoring)
    # Monitors all 14 web endpoints every 60s, validates inputs/outputs,
    # detects error patterns, and stages fixes for recurring issues.
    # Implements all 7 binding directives from the operator:
    #   1. Verify every input   2. Verify every output   3. Monitor 24/7
    #   4. Cross-agent comms    5. Zero tolerance         6. Self-healing
    #   7. Never silent
    web_integrity_agent: Optional[Any] = None
    try:
        from .intel.web_integrity_agent import WebIntegrityAgent
        web_integrity_agent = WebIntegrityAgent(
            aria_service_url=f"http://localhost:{settings.effective_port}",
            redis_store=rs if _state_connect_ok else None,
        )
        await web_integrity_agent.start()
        logger.info(
            "[R-F1207] Web Integrity Agent started — monitoring %d endpoints every 60s",
            len(getattr(WebIntegrityAgent, 'WEB_ENDPOINTS', [])),
        )
    except Exception as _wia_e:
        logger.warning("[R-F1207] Web Integrity Agent start failed (non-fatal): %s", _wia_e)

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")

    # R-F1051 -- start self-healing infrastructure
    try:
        from .intel.self_healing import start_self_healing
        await start_self_healing()
        logger.info("[R-F1051] Self-healing infrastructure started")
    except Exception as _heal_e:
        logger.warning("[R-F1051] Self-healing start failed (non-fatal): %s", _heal_e)

    # R-F1146 -- start self-restart blackout detector
    try:
        from .intel.self_restart import start_blackout_detector, tick_heartbeat
        start_blackout_detector()
        tick_heartbeat("aria_main")
        logger.info("[R-F1146] Self-restart blackout detector started")
    except Exception:
        logger.warning("[R-F1146] Self-restart start failed (non-fatal)")

    # R-F1225 -- start PowerShell Master
    try:
        from .utils.powershell_master import PowerShellMaster, add_powershell_endpoints
        ps_master = PowerShellMaster()
        # Test if PowerShell is available (non-fatal if not)
        ps_available = await ps_master.test_powershell()
        if ps_available:
            add_powershell_endpoints(router, ps_master)
            logger.info("[R-F1225] PowerShell Master started — endpoints registered")
        else:
            logger.info("[R-F1225] PowerShell not available on this platform — skipping")
        app.state.ps_master = ps_master
    except Exception as _ps_e:
        logger.debug("[R-F1225] PowerShell Master init failed (non-fatal): %s", _ps_e)

    yield


    # ── Shutdown ─────────────────────────────────────────────────────────
    # R-F1051 -- stop self-healing infrastructure
    try:
        from .intel.self_healing import stop_self_healing
        await stop_self_healing()
    except Exception as _heal_e:
        logger.warning("[R-F1051] Self-healing shutdown failed (non-fatal): %s", _heal_e)

    # R-F1146 -- stop self-restart blackout detector
    try:
        from .intel.self_restart import stop_blackout_detector
        stop_blackout_detector()
        logger.info("[R-F1146] Self-restart blackout detector stopped")
    except Exception:
        logger.warning("[R-F1146] Self-restart shutdown failed (non-fatal)")

    # R-F1207 -- stop Web Integrity Agent
    if web_integrity_agent is not None:
        try:
            await web_integrity_agent.stop()
            logger.info("[R-F1207] Web Integrity Agent stopped")
        except Exception as _wia_e:
            logger.warning("[R-F1207] Web Integrity Agent stop failed: %s", _wia_e)

    try:
        from .autonomous import engine as _autonomous_engine
        await _autonomous_engine.stop_engine()
    except Exception as e:
        logger.warning("Autonomous engine shutdown failed (non-fatal): %s", e)
    # R-F803: cancel ARIA-Coder background tasks. The tasks own httpx
    # clients (SovereignLLM + FlyDeployer); cancel propagates aclose.
    for _t in aria_coder_tasks:
        _t.cancel()
    if aria_coder_tasks:
        logger.info(
            "[R-F803] cancelled %d ARIA-Coder tasks on shutdown",
            len(aria_coder_tasks),
        )
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
    # R-F656 (2026-05-17): full-system audit found these 3 background
    # tasks were started in lifespan but never cancelled on shutdown.
    # Without cancel, the task survives the lifespan return, fly's
    # graceful-stop window expires before SIGKILL, and the loop
    # accumulates wakeups across deploys. Net effect on fly: deploy
    # latency creeps + intermittent worker-zombie warnings in logs.
    if reasoning_purge_task:
        reasoning_purge_task.cancel()
    if weekly_report_task:
        weekly_report_task.cancel()
    if watchlist_rescreen_task:
        watchlist_rescreen_task.cancel()
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
    # R-F507: stop the crawler loop cleanly so we don't leak a fetch
    # across deploys. The loop checks the stop_event before sleeping;
    # if it's mid-fetch, the task.cancel() interrupt is also caught.
    try:
        if _crawler_stop_event is not None:
            _crawler_stop_event.set()
        if _crawler_task is not None:
            _crawler_task.cancel()
    except Exception as e:
        logger.warning("crawler shutdown failed (non-fatal): %s", e)
    # R-F504: close ARIA's own search index cleanly so WAL is flushed.
    try:
        from .search_index import db as _search_db_shut
        await _search_db_shut.close()
    except Exception as e:
        logger.warning("search_index.close failed (non-fatal): %s", e)
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

# R-F1241: Serve static files (ARIA demo page) + public demo endpoint
import os as _static_os
_static_dir = _static_os.path.join(_static_os.path.dirname(__file__), "static")
if _static_os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_demo_page():
        """Serve the ARIA demo page at the root URL."""
        index_path = _static_os.path.join(_static_dir, "index.html")
        try:
            with open(index_path, encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>ARIA Demo</h1><p>Error loading page: {e}</p></body></html>",
                status_code=500,
            )

    @app.get("/download", response_class=HTMLResponse, include_in_schema=False)
    async def download_aria_launcher():
        """Download the ARIA one-click launcher (.bat file)."""
        bat_path = _static_os.path.join(_static_dir, "download_aria.bat")
        try:
            with open(bat_path, encoding="utf-8") as f:
                content = f.read()
            from fastapi.responses import Response
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename=ARIA_Launcher.bat",
                    "Content-Type": "application/octet-stream",
                },
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>Download Error</h1><p>{e}</p></body></html>",
                status_code=500,
            )

    @app.get("/download/aria", response_class=HTMLResponse, include_in_schema=False)
    async def download_aria_zip():
        """Download ARIA as a ZIP folder. Unzip, open cmd, type 'aria'."""
        import zipfile, io
        aria_folder = _static_os.path.join(_static_dir, "aria_folder")
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in _static_os.walk(aria_folder):
                    for f in files:
                        file_path = _static_os.path.join(root, f)
                        arcname = _static_os.path.relpath(file_path, aria_folder)
                        zf.write(file_path, arcname)
            buf.seek(0)
            from fastapi.responses import Response
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=ARIA.zip",
                },
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>Download Error</h1><p>{e}</p></body></html>",
                status_code=500,
            )

    @app.get("/download/client", response_class=HTMLResponse, include_in_schema=False)
    async def download_aria_client():
        """Download ARIA Client — tiny ZIP, type 'aria' in cmd, connected to main server."""
        import zipfile, io
        client_folder = _static_os.path.join(_static_dir, "aria_client")
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in _static_os.walk(client_folder):
                    for f in files:
                        file_path = _static_os.path.join(root, f)
                        arcname = _static_os.path.relpath(file_path, client_folder)
                        zf.write(file_path, arcname)
            buf.seek(0)
            from fastapi.responses import Response
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=ARIA_Client.zip",
                },
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>Download Error</h1><p>{e}</p></body></html>",
                status_code=500,
            )

    @app.post("/api/aria/client/chat")
    async def aria_client_chat(request: Request):
        """Chat endpoint for the ARIA terminal client.

        Proxies to the real ARIA chat engine (routes.aria.chat_ep) so the
        terminal client gets full intelligence — intent detection, web
        research, tool execution, LLM reasoning, and verification.
        """
        body = await request.json()
        message = (body.get("message") or "").strip()
        user = (body.get("user") or "user").strip()

        if not message:
            raise HTTPException(status_code=400, detail="message required")

        # Build a ChatRequest and delegate to the real chat endpoint
        from .routes.aria import ChatRequest, chat_ep

        chat_req = ChatRequest(
            message=message,
            session_id=f"client_{user}",
            user_id=user,
            auto_tools=True,
        )
        result = await chat_ep(chat_req, request)
        return result

    @app.post("/api/aria/client/analyse")
    async def aria_client_analyse(request: Request):
        """Analyse code endpoint for the ARIA terminal client.

        Proxies to the real ARIA chat engine with a code-analysis prompt,
        so the terminal client gets full ARIA intelligence on the code.
        """
        body = await request.json()
        code = (body.get("code") or "").strip()

        if not code:
            raise HTTPException(status_code=400, detail="code required")

        # Delegate to the real chat endpoint with a code-analysis prompt
        from .routes.aria import ChatRequest, chat_ep

        analysis_prompt = (
            f"Analyse the following code and provide:\n"
            f"1. A summary of what it does\n"
            f"2. Any bugs, issues, or anti-patterns\n"
            f"3. Specific fixes for each issue\n\n"
            f"```python\n{code}\n```"
        )
        chat_req = ChatRequest(
            message=analysis_prompt,
            session_id="client_analyse",
            user_id="client",
            auto_tools=False,
        )
        result = await chat_ep(chat_req, request)
        return {"analysis": result.get("response", ""), "fixes": result.get("response", "")}

    @app.post("/api/aria/coder/demo")
    async def aria_coder_demo_ep(request: Request):
        """Public demo endpoint — no auth required.

        Takes a description and existing code, runs ARIA's autonomous coding
        engine (no LLM), and returns the analysis + generated fix.
        """
        body = await request.json()
        description = (body.get("description") or "").strip()
        code = (body.get("code") or "").strip()

        if not description:
            raise HTTPException(status_code=400, detail="description required")

        if not code:
            code = 'def process_item(data):\n    result = data["value"] * 2\n    return result\n'

        try:
            from .intel.autonomous_coder import AutonomousCoder
            coder = AutonomousCoder()

            from .autonomous.gap_detector import Gap, GapType, GapSeverity
            gap = Gap(
                gap_id="demo_gap",
                gap_type=GapType.MODULE_BUG,
                severity=GapSeverity.MEDIUM,
                title=description[:80],
                description=description,
                module="demo_module",
            )
            plan = await coder.generate_fix_plan(gap, code)
            code_result = await coder.write_code(plan, code, "demo_module.py")

            return {
                "plan": {
                    "title": plan.get("title"),
                    "approach": plan.get("approach"),
                    "risk_level": plan.get("risk_level"),
                    "target_files": plan.get("target_files"),
                },
                "code": code_result.get("code", ""),
                "source": code_result.get("source", "unknown"),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:500])


@app.get("/health/live")
async def health_live_top_level():
    """R-F690 (2026-05-18) — top-level /health/live alias.

    Live fly logs 2026-05-18 10:43:28 / 10:47:15 / 10:52:53 showed
    monitoring callers hitting `/health/live` without the
    `/api/aria/` prefix and getting 404. The canonical
    `/api/aria/health/live` (R-F372) is the fly.io load-balancer
    probe path; this alias keeps Kubernetes-style monitoring tools
    (which default to `/health/live`) happy without forcing them to
    re-config.

    Same payload shape as the canonical handler — no Redis, no
    stats, in-process only."""
    try:
        _build_rev = ARIA_BUILD_REV
    except Exception:
        _build_rev = "unknown"
    return {"status": "alive", "build_rev": _build_rev}


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

    # R-F762 (2026-05-20) — state-backend health surfaced. Pre-R-F762
    # a Redis-unreachable boot silently fell back to in-memory dict
    # and the operator had no signal from /health that knowledge was
    # not being persisted. Now: reachable=False rolls up into degraded
    # status so any monitor watching /health sees the regression
    # without log scraping.
    state_backend_ind = {
        "backend": getattr(app.state, "state_backend", "unknown"),
        "reachable": bool(getattr(app.state, "state_backend_reachable", True)),
    }
    state_backend_ind["status"] = "green" if state_backend_ind["reachable"] else "red"

    return {
        "status": "operational" if (
            chain_resilient
            and autonomous_healthy
            and state_backend_ind["reachable"]
        ) else "degraded",
        "service": "aria",
        "llm_provider": llm.name if llm else "none",
        "llm_configured": bool(llm and llm.is_configured),
        "llm_chain": llm_chain,
        "llm_fallback_stats": llm_stats,
        "autonomous": autonomous_ind,
        "diagnostic": diagnostic_ind,
        "state_backend": state_backend_ind,
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
    async def _record_sweep_failure(reason: str, detail: str) -> None:
        # R-F973 (§21a): ingest_sweep is the largest Node→brain data path and
        # its parse-failure branches were logger.warning-only (DARK) — ARIA had
        # no coder-visible signal that sweeps were failing to ingest. Record a
        # capability gap so the failure reaches the brain on the failure branch.
        try:
            from .intel import capability_gaps as _cg
            await _cg.record_gap(
                gap_type="file_parse",
                detail=f"ingest_sweep {reason}: {detail[:300]}",
                source="ingest_sweep",
            )
        except Exception as _cg_e:
            logger.debug("ingest_sweep gap-record failed: %s", _cg_e)

    try:
        raw = await request.body()
    except Exception as e:
        logger.warning("ingest: body read failed: %s", e)
        await _record_sweep_failure("body_read_failed", str(e))
        raise HTTPException(status_code=400, detail="body_read_failed")

    try:
        data = json.loads(raw) if raw else {}
    except Exception as e:
        preview = (raw[:200] if raw else b"").decode("utf-8", errors="replace")
        logger.warning("ingest: JSON parse failed (%s). Body first 200b: %r", e, preview)
        await _record_sweep_failure("invalid_json", f"{e} | {preview}")
        raise HTTPException(status_code=400, detail="invalid_json")

    if not isinstance(data, dict):
        logger.warning(
            "ingest: expected dict body, got %s. Preview: %r",
            type(data).__name__, str(data)[:200],
        )
        await _record_sweep_failure("expected_dict_body", f"got {type(data).__name__}")
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
    anomaly_failed = False
    try:
        anomaly_alerts = await proactive.anomaly_watch(data)
    except Exception as e:
        anomaly_failed = True
        logger.warning("Proactive anomaly watch failed: %s", e)

    # R-F973 (§21a): wire the success path to the brain. Pre-R-F973 this
    # returned counts to the Node tier but emitted NO brain signal, so the
    # largest cross-tier data path was invisible to ARIA's self-introspection
    # (health_perf_ep could cite knowledge_facts but not "what the sweep
    # ingested"). Use the lightweight per-module signal recorder (a §21a
    # metric, NOT absorb): it surfaces the ingest in brain_hook.get_stats() and
    # health_perf cross_tier WITHOUT re-running neural learning on a meta-summary
    # (the sweep already learns its own signals/news above via _learn_one) or
    # adding latency to the ingest connection. anomaly-watch failure flips the
    # signal to unsuccessful so a degrading sweep stays visible.
    try:
        from .intel import brain_hook as _bh_sweep
        await _bh_sweep._record_signal("ingest_sweep", success=not anomaly_failed)
    except Exception as _bh_e:
        logger.debug("ingest_sweep brain signal-record failed: %s", _bh_e)

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

    _host = settings.host
    _port = settings.effective_port

    if _host == "::":
        # R-F842 (2026-05-23): manual dual-stack socket.
        #
        # R-F838 set ARIA_HOST=:: so .internal (IPv6/6PN) calls reach
        # aria-intel — but uvicorn's host="::" is IPv6-only on Linux
        # (IPV6_V6ONLY defaults ON for Python sockets). That broke
        # Fly's healthcheck + public proxy (both IPv4-only): aria-intel
        # showed "1 critical · connect: no route to host" until this
        # patch.
        #
        # Fix: create the listening socket ourselves, set
        # IPV6_V6ONLY=0, and pass the fd to uvicorn. The socket then
        # accepts BOTH IPv4 (mapped as ::ffff:x.x.x.x) and IPv6
        # connections. One process, one port, both stacks.
        import socket as _socket
        sock = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.IPPROTO_IPV6, _socket.IPV6_V6ONLY, 0)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("::", _port))
        sock.listen(2048)
        sock.set_inheritable(True)
        uvicorn.run(
            "aria_service.main:app",
            fd=sock.fileno(),
            reload=False,
        )
    else:
        uvicorn.run(
            "aria_service.main:app",
            host=_host,
            port=_port,
            reload=False,
        )
