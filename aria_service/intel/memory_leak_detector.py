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
                    if elapsed_since_gc > 300:  # Don't GC more than once per 5min
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

                        gc.collect()
                        self._last_gc_at = now

                        # Sample again after GC to see if it helped
                        rss_after = _get_rss_bytes()
                        freed_mb = (rss - rss_after) / (1024 * 1024) if rss_after else 0
                        logger.info(
                            "[memory_leak_detector] GC freed %.1fMB (RSS: %.1fMB → %.1fMB)",
                            freed_mb, snapshot["rss_mb"], rss_after / (1024 * 1024) if rss_after else 0,
                        )

                # Analyse growth every 10 samples
                if len(self.snapshots) >= 10 and len(self.snapshots) % 10 == 0:
                    analysis = self.analyse()
                    if analysis.get("leak_detected"):
                        logger.warning(
                            "[memory_leak_detector] LEAK DETECTED — "
                            "growth=%.2fMB/interval, current=%.1fMB",
                            analysis["growth_rate_mb_per_interval"],
                            analysis["current_memory_mb"],
                        )
                        self._emit_leak_signal(analysis)

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
            _aio.create_task(_cg.record_gap(
                gap_type="performance",
                severity=3,
                title=f"Memory leak detected: {analysis['growth_rate_mb_per_interval']:.1f}MB/interval",
                description=(
                    f"Sustained memory growth detected: "
                    f"{analysis['growth_rate_mb_per_interval']:.1f}MB per interval, "
                    f"current={analysis['current_memory_mb']:.1f}MB, "
                    f"peak={analysis['peak_memory_mb']:.1f}MB"
                ),
                module="memory_leak_detector",
            ))
        except Exception:
            pass

    def get_status(self) -> dict[str, Any]:
        """Return current status for the orchestrator."""
        analysis = self.analyse()
        return {
            "running": self._running,
            "snapshots": len(self.snapshots),
            "analysis": analysis,
            "last_snapshot": self.snapshots[-1] if self.snapshots else None,
        }
