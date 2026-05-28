"""Logging handler that mirrors WARNING+ aria logs into the error ledger.

B1 fix 2026-04-27: self_improve.record_error was only wired into 2 LLM
chat-error sites in aria_engine.py. Every other failure mode (JSON parse,
HTTP fetch, chromadb upsert, sanctions API, brain absorption errors)
was log.warning'd but never recorded. The autonomous self-improvement
cycle therefore reported "0 errors, 0 bugs" every cycle and had nothing
to act on.

This handler intercepts WARNING/ERROR records from any logger matching
the `aria.` prefix and forwards a sanitised payload to
self_improve.record_error. It runs in-process so there's no cross-network
hop. Async writes are scheduled via asyncio.create_task to avoid
blocking the synchronous logging path.

Failure modes:
  - No running event loop → drop the record (we're outside an async
    context, e.g. during initial import). This is fine; the autonomous
    cycle only triggers from the main loop anyway.
  - Redis write fails → swallowed inside record_error; we never raise
    out of a logging handler.

Operator notes:
  - Filtering: only `aria.*` loggers feed the ledger. Third-party
    warnings (httpx, huggingface_hub, sentence_transformers) are not
    forwarded — they aren't actionable for self-improvement.
  - Suppressed prefixes: messages containing "skipped", "trying", or
    "fallback" are demoted (likely transient, not actionable).
  - Cap: the handler is a thin wrapper; record_error itself enforces
    MAX_ERRORS = 200 with a 7-day TTL.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aria.error_log_handler")

# F96 fix 2026-04-30: self_improve.MODIFIABLE_FILES keys are project-relative
# POSIX paths ("aria_service/intel/knowledge.py"), but LogRecord.filename is
# the basename only ("knowledge.py"). Every recorded error therefore failed
# the MODIFIABLE_FILES membership check at self_improve.py:950, leaving
# bugs_detected pinned at 0 across every cycle even when 200 errors had
# been ingested. Resolve from record.pathname to a relative-from-root path
# so the membership check has a fighting chance.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# R-F750 (2026-05-20) — cache of resolved project-relative paths.
# Pre-R-F750 every WARNING+ aria.* log emit called Path().resolve()
# which invokes os.path.realpath — a syscall chain walking the
# filesystem. wedge_675_1779301744.log captured a 6.96s main-loop
# stall with the main thread parked inside
# `pathlib.realpath` → `_project_relative_path` → `error_log_handler.emit`
# triggered by an autonomy_surface timeout warning. Caching is safe:
# the file paths in record.pathname are stable for the process lifetime
# (the Python module set doesn't change post-import), so we compute
# once per unique pathname and reuse. Bounded to MAX_CACHE entries to
# prevent unbounded growth from synthetic / dynamically-generated
# paths (e.g. <string> sources from exec()).
_PATH_CACHE: dict[str, str] = {}
_PATH_CACHE_MAX = 2000


def _project_relative_path(pathname: str) -> str:
    """Return a POSIX-style path relative to the project root, falling back
    to the basename when the source file is outside the project tree
    (third-party libraries, stdlib). Third-party records are already filtered
    by the aria.* logger-name gate above, so this fallback is rarely hit.

    R-F750: cached. Path.resolve() does an os.path.realpath syscall
    chain — called once per emit, this was the dominant cost during
    log bursts. Cache lookup is O(1)."""
    if not pathname:
        return ""
    cached = _PATH_CACHE.get(pathname)
    if cached is not None:
        return cached
    try:
        result = Path(pathname).resolve().relative_to(_PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        result = Path(pathname).name
    # Bounded cache — drop everything if it exceeds the cap rather than
    # implement LRU. Realistic upper bound is ~500 distinct emit paths
    # across all aria.* modules; 2000 is comfortable headroom.
    if len(_PATH_CACHE) >= _PATH_CACHE_MAX:
        _PATH_CACHE.clear()
    _PATH_CACHE[pathname] = result
    return result

# Substrings that mark a log line as transient/operational rather than
# actionable. These get filtered out so the ledger doesn't fill with
# noise like "Skipping searx (cooling down)".
_SKIP_SUBSTRINGS = (
    "trying next",
    "trying archive",
    "soft cooldown",
    "skipping",
    "burst peer",
    "non-fatal",
    # Already-recorded errors — don't duplicate
    "self_improve.record_error",
    # F54 fix 2026-04-28: cascade-killer for asyncio loop-binding errors.
    # If a Redis op fails with one of these, record_error itself touches
    # Redis on the same broken loop and triggers another warning, which
    # the handler turns into another record_error task → recursion. On
    # 04-28 a single cross-loop GET on deal_pipeline:leads cascaded into
    # 21 record_error attempts in 10ms. F51 fixes the root cause; this
    # defends against future cross-loop bugs we haven't found yet.
    "got future",
    "different loop",
    "event loop is closed",
    # R-F891: now that the "ARIA.*" tree feeds the ledger, two per-request
    # operational SECURITY detections in security_protocol.py would otherwise
    # flood the 200-entry error/bug ledger and mislead the coder (they are
    # detections, not code defects — they belong in a security channel).
    "prompt injection detected",
    "output sanitisation total",
)


class ErrorLedgerHandler(logging.Handler):
    """Forward WARNING+ aria logs to self_improve.record_error."""

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Only aria.* loggers — third-party noise isn't actionable.
            # Case-insensitive: ~30 modules use the legacy uppercase "ARIA.*"
            # logger name (R-F891) and their WARNING+ logs must reach the
            # ledger too, not just lowercase "aria.*".
            if not record.name.lower().startswith("aria"):
                return
            # Self-recursion guard
            if record.name == "aria.error_log_handler":
                return
            msg = record.getMessage()
            lower = msg.lower()
            if any(skip in lower for skip in _SKIP_SUBSTRINGS):
                return

            # Async dispatch — must not block the logging thread.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No loop, e.g. during import — drop silently.

            # Lazy import to avoid circular dep at module load.
            from . import self_improve as _si
            loop.create_task(_si.record_error(
                error_type=f"log:{record.levelname.lower()}",
                message=msg[:500],
                file=_project_relative_path(record.pathname),
                function=record.funcName,
            ))
            # R-F994 — increment error count for self_coder post-deploy monitor
            _increment_error_count(loop)
        except Exception:
            # Logging handlers must NEVER raise — would cascade into
            # logger machinery and potentially crash the process.
            pass


_ERROR_COUNT_KEY = "crucix:aria:error_ledger:count"


def _increment_error_count(loop: asyncio.AbstractEventLoop) -> None:
    """Fire-and-forget Redis INCR for the error count key.

    The self_coder post-deploy monitor reads this key to detect
    regressions after an auto-deploy. Pre-R-F994 the key had no
    producer, so the monitor was a permanent no-op.
    """
    try:
        from . import redis_store as _rs
        loop.create_task(_rs.incr(_ERROR_COUNT_KEY))
    except Exception:
        pass


_installed_handler: Optional[ErrorLedgerHandler] = None


def install(level: int = logging.WARNING) -> ErrorLedgerHandler:
    """Attach the handler to the root aria logger. Idempotent — repeated
    calls return the existing handler instead of stacking duplicates."""
    global _installed_handler
    if _installed_handler is not None:
        return _installed_handler
    handler = ErrorLedgerHandler(level=level)
    handler.setLevel(level)
    # Attach to the root 'aria' logger so all sub-loggers (aria.researcher,
    # aria.brain_hook, aria.llm.fallback, etc.) propagate through.
    logging.getLogger("aria").addHandler(handler)
    # R-F891: ~30 modules (dd_orchestrator, security_protocol,
    # global_export_control, regional_compliance, deception_detection,
    # commercial_coherence, verified_intel, ground_truth_loop, …) use the
    # legacy uppercase "ARIA.*" logger name. "ARIA.*" is NOT a child of
    # "aria", so those records never reached this handler — every WARNING+
    # they emit (incl. the R-F886 DD-compliance-layer promotions) was
    # invisible to the brain/coder. Attach to "ARIA" too so the whole tree
    # feeds the ledger. (Filter in emit() is case-insensitive to match.)
    logging.getLogger("ARIA").addHandler(handler)
    _installed_handler = handler
    logger.info("Error-ledger handler installed (level=%s) — WARNING+ aria logs will mirror to self_improve ledger", logging.getLevelName(level))
    return handler


def uninstall() -> None:
    """Remove the handler. Mainly for tests."""
    global _installed_handler
    if _installed_handler is None:
        return
    logging.getLogger("aria").removeHandler(_installed_handler)
    logging.getLogger("ARIA").removeHandler(_installed_handler)  # R-F891
    _installed_handler = None
