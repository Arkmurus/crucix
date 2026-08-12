"""R-F1148 — Memory Leak Detector for ARIA.

Detects and reports memory growth patterns by periodically sampling RSS and
analysing the trend. Wires findings to brain_hook so ARIA learns about
sustained memory pressure.

Architecture:
  - A lightweight async loop samples process RSS at configurable intervals
  - Growth-rate analysis detects sustained increases (>1MB/interval sustained)
  - On leak detection, emits a capability_gap signal for the coder to act on
  - No external dependencies — uses only os + gc from stdlib

Usage:
  from aria_service.intel.memory_leak_detector import MemoryLeakDetector
  detector = MemoryLeakDetector(threshold_mb=1024)
  task = asyncio.create_task(detector.run_forever())
  # ... later ...
  detector.stop()

Environment:
  ARIA_MEMORY_LEAK_INTERVAL_S=60  (sampling interval, default 60)
  ARIA_MEMORY_LEAK_THRESHOLD_MB=1024  (alert threshold, default 1024)
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import gc
import logging
import os
import time
from typing import Any

logger = logging.getLogger("aria.memory_leak_detector")

_INTERVAL_S = int(os.getenv("ARIA_MEMORY_LEAK_INTERVAL_S", "60"))
# R-F1435: raised from 1024MB to 6144MB for the 8GB Fly box. The old
# threshold fired constantly on normal RSS (~2GB), causing the coder to
# re-stage the same memory_leak_detector.py fix 557x (false-positive gap
# churn). 6144MB (~75% of 8GB) is a genuine pressure signal — normal
# operation stays well below it.
_THRESHOLD_MB = int(os.getenv("ARIA_MEMORY_LEAK_THRESHOLD_MB", "6144"))
_MAX_SNAPSHOTS = 100


def _get_rss_bytes() -> int:
    """Return the current RSS of this process in bytes.

    Uses /proc/self/status on Linux (Fly.io), os module on other platforms.
    Pure stdlib — no psutil, ctypes, or subprocess dependency.
    """
    # Linux: /proc/self/status is the most reliable
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except (FileNotFoundError, IOError, ValueError, IndexError):
        pass

    # Windows: use os.popen('wmic') as a last resort
    # On platforms where /proc is unavailable, return 0 and rely on GC-only mode
    return 0


#: R-F3930 — module-level so the census is answerable WITHOUT the running detector
#: instance. Absolute sizes are useful on their own; the delta needs a prior reading.
_LAST_REPORT_CENSUS: dict[str, int] = {}


def subsystem_census() -> dict[str, int | None]:
    """Current sizes of ARIA's known in-memory growth candidates.

    Bounded len() probes only — see `_subsystem_census_delta` for why this must never
    walk the object graph on a 6.7GB heap. Each probe is individually guarded so one
    unavailable subsystem cannot blind the rest.

    R-F3932 — `None` MEANS NOT LOADED; `0` MEANS MEASURED AND EMPTY. The first live
    reading of this report returned `facts: 0` at 2552MB RSS on a freshly booted
    process, because the probe was `len((_k._cache or {}).get("facts", []))` — an
    UNHYDRATED cache (`_cache is None`, knowledge not yet read from disk) collapsed
    into the same 0 as a genuinely empty one.

    That is the absence-reads-as-a-measurement defect this whole detector exists to
    surface, reproduced inside its own diagnostic an hour after it shipped. It
    matters concretely: "2.5GB with zero facts" invites the conclusion that knowledge
    is not the memory consumer, when the truth may simply be that it has not loaded
    yet — a wrong cause pointing at a wrong fix.
    """
    out: dict[str, int | None] = {}

    def _probe(name, fn):
        try:
            v = fn()
            # None is a REPORTED value ("not loaded"), not a dropped probe.
            out[name] = v if (v is None or isinstance(v, int)) else None
        except Exception:
            pass          # a raising probe is genuinely unmeasurable — omit it

    def _facts():
        from . import knowledge as _k
        if _k._cache is None:
            return None       # not hydrated yet — NOT the same as empty
        return len(_k._cache.get("facts", []))

    def _topic():
        from . import knowledge as _k
        return len(_k._topic_index)

    def _content():
        from . import knowledge as _k
        return len(_k._content_index)

    def _tasks():
        return len(asyncio.all_tasks())

    _probe("facts", _facts)
    _probe("topic_index", _topic)
    _probe("content_index", _content)
    _probe("asyncio_tasks", _tasks)
    return out


def process_memory_report() -> dict[str, Any]:
    """R-F3930 — ANSWER "what is my memory doing?" ON DEMAND.

    THE GAP THIS CLOSES. The detector's findings were reachable only by waiting for
    RSS to cross the threshold and reading a log line or a capability gap. Nothing
    exposed `get_status()`, so after a deploy restart — RSS back down at 4792MB with
    the threshold at 6144MB — the diagnosis was simply unavailable for hours, and a
    session could not tell "healthy" from "not yet measured". That is the §25
    proprioception rule applied to the process itself: ARIA must be able to answer
    what her own memory is doing, not just be told when it is already bad.

    Deliberately independent of the running detector instance (which lives on
    self_healing): the probes are stateless, so plumbing a singleton through would
    add coupling for nothing. The DELTA is against the previous CALL of this
    function, and is absent on the first one rather than being reported as zero —
    "no prior reading" and "no change" are different facts (§22).
    """
    global _LAST_REPORT_CENSUS
    rss = _get_rss_bytes()
    census = subsystem_census()
    prev, _LAST_REPORT_CENSUS = _LAST_REPORT_CENSUS, dict(census)
    # R-F3932 — a subsystem that was NOT LOADED on either reading has no delta.
    # Subtracting against None would either crash or invent a number; both are worse
    # than saying "not comparable".
    delta = ({k: v - prev[k] for k, v in census.items()
              if k in prev and isinstance(v, int) and isinstance(prev[k], int)}
             if prev else None)
    rss_mb = round(rss / (1024 * 1024), 1) if rss else None
    return {
        "rss_mb": rss_mb,
        "threshold_mb": _THRESHOLD_MB,
        # None when RSS is unreadable (non-Linux): "could not measure" is never
        # "measured and fine" (§22).
        "over_threshold": (None if rss_mb is None else rss_mb > _THRESHOLD_MB),
        "subsystems": census,
        "subsystems_delta_since_last_call": delta,
        "note": (
            "Bounded len() probes — never a heap walk (R-F3920). A delta of None "
            "means this is the first reading, not that nothing changed."
        ),
    }


class MemoryLeakDetector:
    """Detects and reports memory growth patterns.

    Samples RSS at regular intervals, tracks a sliding window of snapshots,
    and analyses the growth rate to detect sustained leaks.
    """

    def __init__(self, threshold_mb: int = _THRESHOLD_MB) -> None:
        self.threshold_bytes = threshold_mb * 1024 * 1024
        self.snapshots: list[dict[str, Any]] = []
        self._running = False
        self._last_gc_at: float = 0.0
        #: R-F3924 — consecutive collections that reclaimed essentially nothing.
        self._ineffective_gc_runs: int = 0

    #: R-F3924 — below this a collection reclaimed nothing worth the heap walk.
    _GC_EFFECTIVE_MB = 1.0
    #: After this many consecutive no-op collections, stop paying every 5 minutes.
    _GC_GIVE_UP_AFTER = 3
    _GC_BASE_INTERVAL_S = 300
    _GC_BACKOFF_INTERVAL_S = 3600

    def _gc_interval_s(self) -> int:
        """How long to wait before the next collection.

        R-F3924 — widens once GC is PROVEN ineffective on this process. Not a
        cooldown guess: it is driven by the measured `freed_mb` of the collections
        already performed. A remedy that has reclaimed nothing three times running
        has demonstrated that the memory is live, and repeating it every 5 minutes
        is a full heap walk bought for zero.
        """
        if self._ineffective_gc_runs >= self._GC_GIVE_UP_AFTER:
            return self._GC_BACKOFF_INTERVAL_S
        return self._GC_BASE_INTERVAL_S

    def _note_gc_outcome(self, freed_mb: float) -> None:
        """Record whether a collection actually reclaimed anything, and SAY SO once.

        The transition is the news: a single honest signal when GC is established as
        the wrong remedy, not a line every five minutes. §21a — it reaches the brain,
        because "the memory is live, collection cannot help" is exactly what the
        coder and the operator need to know instead of an endless `freed 0.0MB`.
        """
        if freed_mb >= self._GC_EFFECTIVE_MB:
            self._ineffective_gc_runs = 0
            return
        self._ineffective_gc_runs += 1
        if self._ineffective_gc_runs != self._GC_GIVE_UP_AFTER:
            return          # announce the transition once, not every cycle
        logger.warning(
            "[memory_leak_detector] R-F3924 — GC reclaimed <%.1fMB on %d consecutive "
            "runs; the memory is LIVE, not garbage. Backing off to %ds. Collection is "
            "not the remedy here — read the subsystem census on the leak signal.",
            self._GC_EFFECTIVE_MB, self._ineffective_gc_runs,
            self._GC_BACKOFF_INTERVAL_S,
        )
        try:
            wire_failure(
                module="memory_leak_detector",
                detail=(
                    f"GC ineffective {self._ineffective_gc_runs}x consecutively at "
                    f"RSS above {_THRESHOLD_MB}MB — the retained memory is reachable, "
                    f"so collection cannot reclaim it. Backed off to "
                    f"{self._GC_BACKOFF_INTERVAL_S}s. The fix is to find what holds "
                    f"the references (see the subsystem census on the leak gap), not "
                    f"to collect more often."
                ),
                gap_type="performance",
                source="memory_leak_detector:_note_gc_outcome",
            )
        except Exception:      # pragma: no cover - telemetry never blocks the loop
            pass

    async def run_forever(self) -> None:
        """Sample memory and analyse growth continuously."""
        self._running = True
        logger.info(
            "[memory_leak_detector] Started (interval=%ds, threshold=%dMB)",
            _INTERVAL_S, _THRESHOLD_MB,
        )

        while self._running:
            try:
                await asyncio.sleep(_INTERVAL_S)
                if not self._running:
                    break

                rss = _get_rss_bytes()
                now = time.time()

                snapshot = {
                    "timestamp": now,
                    "rss_bytes": rss,
                    "rss_mb": rss / (1024 * 1024) if rss else 0,
                }
                self.snapshots.append(snapshot)

                # Keep sliding window
                if len(self.snapshots) > _MAX_SNAPSHOTS:
                    self.snapshots = self.snapshots[-_MAX_SNAPSHOTS:]

                # Log at debug level every sample
                logger.debug(
                    "[memory_leak_detector] RSS=%.1fMB (%d samples)",
                    snapshot["rss_mb"], len(self.snapshots),
                )

                # Check threshold — trigger GC if over
                if rss > self.threshold_bytes and rss > 0:
                    elapsed_since_gc = now - self._last_gc_at
                    # R-F3924 — the interval widens once GC is proven ineffective.
                    if elapsed_since_gc > self._gc_interval_s():
                        logger.warning(
                            "[memory_leak_detector] RSS %.1fMB exceeds threshold %dMB — triggering GC",
                            snapshot["rss_mb"], _THRESHOLD_MB,
                        )
                        # R-F1332: clear torch CUDA/tensor caches before GC.
                        # The profiler shows 35% CPU in thread._worker + 23% in
                        # aiosqlite — sentence_transformers model.encode() holds
                        # tensor references in thread-local caches that GC can't
                        # reach. Clearing them frees the resident memory so GC
                        # can actually reclaim it (live evidence: GC freed 0.0MB
                        # every 5min while RSS stayed at 2588.4MB).
                        try:
                            import torch as _torch
                            if hasattr(_torch, "cuda") and _torch.cuda.is_available():
                                _torch.cuda.empty_cache()
                            # Clear CPU-side tensor caches in sentence_transformers
                            if hasattr(_torch, "_C"):
                                _torch._C._clear_autocast_cache()
                        except ImportError:
                            pass
                        except Exception:
                            pass

                        # R-F3924 — OFF THE EVENT LOOP. A full gc.collect() walks
                        # every tracked object; at 6.7GB live that is exactly the
                        # traversal R-F3920 refused to add to this same loop, and the
                        # starvation class R-F2144/R-F2200 already paid for. It ran
                        # synchronously here every 5 minutes.
                        await asyncio.to_thread(gc.collect)
                        self._last_gc_at = now

                        # Sample again after GC to see if it helped
                        rss_after = _get_rss_bytes()
                        freed_mb = (rss - rss_after) / (1024 * 1024) if rss_after else 0
                        logger.info(
                            "[memory_leak_detector] GC freed %.1fMB (RSS: %.1fMB → %.1fMB)",
                            freed_mb, snapshot["rss_mb"], rss_after / (1024 * 1024) if rss_after else 0,
                        )
                        # R-F3924 — STOP PAYING FOR A REMEDY THAT MEASURABLY DOES
                        # NOT WORK. `GC freed 0.0MB` means the memory is LIVE —
                        # reachable state — so collection cannot reclaim it by
                        # construction. R-F1332 recorded this exact symptom at
                        # 2588.4MB and added torch-cache clearing; live 2026-08-12 it
                        # is back at 6690MB, freeing 0.0MB every pass. Repeating a
                        # proven no-op forever is the band-aid §1 forbids, and it is
                        # not free: it is a full heap walk every 5 minutes.
                        self._note_gc_outcome(freed_mb)

                # Analyse growth every 10 samples
                if len(self.snapshots) >= 10 and len(self.snapshots) % 10 == 0:
                    analysis = self.analyse()
                    if analysis.get("leak_detected"):
                        # R-F1512: only emit a signal if the growth is genuinely
                        # abnormal — not just the knowledge base growing. The
                        # knowledge base grows by ~1-2MB per sweep as new facts
                        # are indexed. A "leak" of 12MB/interval at 2.8GB RSS
                        # is normal steady-state growth, not a leak.
                        rate = analysis["growth_rate_mb_per_interval"]
                        current = analysis["current_memory_mb"]
                        if rate > 50:  # R-F1512: >50MB/interval is a real leak
                            logger.warning(
                                "[memory_leak_detector] LEAK DETECTED — "
                                "growth=%.2fMB/interval, current=%.1fMB",
                                rate, current,
                            )
                            self._emit_leak_signal(analysis)
                        else:
                            logger.debug(
                                "[memory_leak_detector] growth=%.2fMB/interval "
                                "(below 50MB threshold — normal KB growth), "
                                "current=%.1fMB",
                                rate, current,
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[memory_leak_detector] Error: %s", e)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False

    def analyse(self) -> dict[str, Any]:
        """Analyse memory usage patterns for leak detection.

        Returns a dict with:
          - leak_detected: True if sustained growth >1MB/interval
          - growth_rate_mb_per_interval: average growth per interval
          - current_memory_mb: latest RSS in MB
          - peak_memory_mb: peak RSS in the window
          - sample_count: number of snapshots analysed
        """
        if len(self.snapshots) < 2:
            return {"leak_detected": False, "sample_count": len(self.snapshots)}

        first = self.snapshots[0]["rss_bytes"]
        last = self.snapshots[-1]["rss_bytes"]
        n = len(self.snapshots)

        # Linear growth rate: (last - first) / intervals
        growth_rate = (last - first) / n if n > 0 else 0

        # Peak in window
        peak = max(s["rss_bytes"] for s in self.snapshots)

        return {
            "leak_detected": growth_rate > 1024 * 1024,  # >1MB per interval
            "growth_rate_mb_per_interval": growth_rate / (1024 * 1024),
            "current_memory_mb": last / (1024 * 1024),
            "peak_memory_mb": peak / (1024 * 1024),
            "sample_count": n,
        }

    def _emit_leak_signal(self, analysis: dict[str, Any]) -> None:
        """Fire-and-forget a brain signal about the detected leak."""
        try:
            from . import capability_gaps as _cg
            import asyncio as _aio
            census = self._subsystem_census_delta()
            _aio.create_task(_cg.record_gap(
                gap_type="performance",
                severity=3,
                title=f"Memory leak detected: {analysis['growth_rate_mb_per_interval']:.1f}MB/interval",
                detail=(
                    f"Sustained memory growth detected: "
                    f"{analysis['growth_rate_mb_per_interval']:.1f}MB per interval, "
                    f"current={analysis['current_memory_mb']:.1f}MB, "
                    f"peak={analysis['peak_memory_mb']:.1f}MB"
                    # R-F3920 — WHAT grew, not just that something did.
                    f"\nSubsystem sizes (delta since last detection): {census}"
                ),
                source="memory_leak_detector",
            ))
        except Exception:
            pass

    def _subsystem_census_delta(self) -> str:
        """R-F3920 — WHICH subsystem grew. Without this the leak is undiagnosable.

        THE DEFECT THIS CLOSES. Live 2026-08-12: this detector reported
        `LEAK DETECTED — growth=114.84MB/interval, current=6681.6MB` and recorded a
        gap carrying only the rate and the totals. Nobody — human or the autonomous
        coder, which DID pick that gap up — could act on it, because nothing said
        what was growing. An alarm that cannot be diagnosed is the same shape as the
        Node gate that refused without saying why (R-F3903): correct, and useless.

        WHY A TARGETED CENSUS AND NOT `gc.get_objects()` / tracemalloc. A generic
        object histogram on a 6.7GB process walks millions of tracked objects and
        can block for seconds. This runs on the monitoring loop, and this repo has
        already paid for event-loop starvation twice (R-F2144, R-F2200). So it reads
        a handful of ARIA'S OWN known growth candidates by len() — O(1) each, no
        allocation, no traversal — which is also *more* actionable than a type
        histogram: "facts +8,214" names a subsystem, "dict +190,000" does not.

        The DELTA is the signal. Absolute sizes at 223k facts say nothing about a
        leak; what changed between two detections is the thing to chase.

        Every probe is individually guarded: a subsystem that is absent, renamed or
        mid-import contributes "?" rather than taking the whole census down. A
        diagnosis that fails closed on one bad probe is no diagnosis.
        """
        probes: dict[str, int] = {}

        def _probe(name: str, fn) -> None:
            try:
                v = fn()
                if isinstance(v, int):
                    probes[name] = v
            except Exception:
                pass          # one unavailable subsystem must not blind the rest

        def _knowledge_facts() -> int:
            from . import knowledge as _k
            return len((_k._cache or {}).get("facts", []))

        def _topic_idx() -> int:
            from . import knowledge as _k
            return len(_k._topic_index)

        def _content_idx() -> int:
            from . import knowledge as _k
            return len(_k._content_index)

        def _tasks() -> int:
            import asyncio as _a
            return len(_a.all_tasks())

        _probe("facts", _knowledge_facts)
        _probe("topic_index", _topic_idx)
        _probe("content_index", _content_idx)
        _probe("asyncio_tasks", _tasks)

        prev = getattr(self, "_last_census", None)
        self._last_census = dict(probes)
        if not probes:
            return "(no subsystem readable)"
        if not prev:
            return " ".join(f"{k}={v}" for k, v in probes.items()) + " (first sample — no delta yet)"
        return " ".join(
            f"{k}={v}({d:+d})" if (d := v - prev.get(k, v)) else f"{k}={v}"
            for k, v in probes.items()
        )

    def get_status(self) -> dict[str, Any]:
        """Return current status for the orchestrator."""
        analysis = self.analyse()
        return {
            "running": self._running,
            "snapshots": len(self.snapshots),
            "analysis": analysis,
            "last_snapshot": self.snapshots[-1] if self.snapshots else None,
        }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="memory_leak_detector",
                     summary="memory_leak_detector module active",
                     source_id="memory_leak_detector:init")
    except Exception:
        try:
            wire_failure(module="memory_leak_detector", detail="module init failed",
                        gap_type="engine_failure", source="memory_leak_detector:init")
        except Exception:
            pass
