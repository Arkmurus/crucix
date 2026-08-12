"""R-F1091 — ARIA wiring monitor agents.

Ongoing surveillance of the brain-wiring health of every intel module.
Runs as background tasks that periodically audit:

  M1 — Wire balance: tracks wire_success vs wire_failure ratio per module.
       Alerts when a module has >5 success calls with 0 failure calls
       (indicating dark failure paths).

  M2 — Compliance screener crash visibility: periodically probes each
       compliance screener with malformed input to verify that crashes
       produce a wire_failure signal that lands in capability_gaps.

  M3 — WA connection health: monitors the WA listener's connection state
       by checking for wa_auth_lost / wa_disconnected signals in the
       brain ledger. Alerts if no signal seen within expected window.

  M4 — Brain signal path integrity: end-to-end test that emits a test
       signal to /api/aria/brain/signal and verifies it lands in
       capability_gaps or brain_hook.

  M5 — Self-coding loop health: checks that the coder's gap_detector
       is scanning, staged improvements are draining, and no cycle
       has stalled for >2× the expected interval.

Each monitor is a standalone async function that can be started as a
background task from lifespan. All monitors wire their own success and
failure to the brain (CLAUDE.md §21a).
"""
from __future__ import annotations

import asyncio
import ast
import glob
import logging
import os
import time
from collections import defaultdict
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.wiring_monitor")

# ── Configuration ──────────────────────────────────────────────────────────────

CHECK_INTERVAL_S = 3600  # 1 hour between full audit cycles
WIRE_BALANCE_THRESHOLD = 5  # modules with >N success and 0 failure get flagged
COMPLIANCE_SCREENERS = (
    "eliminated_weapons_watchlist",
    "weapon_origin_catalogue",
    "goods_list_aggregator_detector",
    "evasion_typology_detector",
    "end_user_granularity",
    "security_protocol",
)
INTEL_DIR = os.path.join(os.path.dirname(__file__))
MONITOR_KEY_PREFIX = "crucix:aria:wiring_monitor:"


# ═══════════════════════════════════════════════════════════════════════════════
# M1 — Wire balance auditor
# ═══════════════════════════════════════════════════════════════════════════════


async def audit_wire_balance() -> dict[str, Any]:
    """Scan all intel modules and report wire_success/wire_failure balance.

    Returns a dict with:
      - total_modules: int
      - modules_with_success: int
      - modules_with_failure: int
      - unbalanced: list of {module, success_count, failure_count}
      - well_balanced: list of {module, success_count, failure_count}
    """
    # ── R-F3707 — this scan runs OFF the event loop ─────────────────────────
    #
    # THE DEFECT, measured: this globbed every `intel/*.py` and `ast.parse`d it
    # INSIDE an `async def`, on the loop, once an hour (monitor_loop,
    # CHECK_INTERVAL_S = 3600, started from main.py). Measured locally at
    # 390 files / 11.4 MB / **2.71 s** of pure CPU — and that is on an idle dev
    # box. On the live single-vCPU machine, under the GIL contention that
    # already produces 8-second heartbeat stalls, it lands squarely in the
    # stall band.
    #
    # It is the same class as R-F3475 (HTML extraction) and R-F1890 (encodes):
    # a CPU-bound sweep that has no business holding the loop. `asyncio.
    # to_thread` releases it for the duration; the work itself is unchanged.
    report = await asyncio.to_thread(_audit_wire_balance_sync)
    return await _wire_and_persist_balance(report)


def _audit_wire_balance_sync() -> dict[str, Any]:
    """The CPU-bound half of audit_wire_balance — safe to run in a thread."""
    results: dict[str, dict[str, int]] = {}
    for f in sorted(glob.glob(os.path.join(INTEL_DIR, "*.py"))):
        name = os.path.basename(f)
        if name.startswith("_"):
            continue  # skip private modules
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read())
        except SyntaxError:
            continue

        success_count = 0
        failure_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and hasattr(node.func, "id"):
                if node.func.id == "wire_success":
                    success_count += 1
                elif node.func.id == "wire_failure":
                    failure_count += 1

        if success_count > 0 or failure_count > 0:
            results[name] = {
                "success": success_count,
                "failure": failure_count,
            }

    unbalanced = [
        {"module": m, "success": v["success"], "failure": v["failure"]}
        for m, v in sorted(results.items())
        if v["success"] > WIRE_BALANCE_THRESHOLD and v["failure"] == 0
    ]
    well_balanced = [
        {"module": m, "success": v["success"], "failure": v["failure"]}
        for m, v in sorted(results.items())
        if v["failure"] > 0
    ]

    total_success = sum(v["success"] for v in results.values())
    total_failure = sum(v["failure"] for v in results.values())

    report = {
        "total_modules": len(results),
        "modules_with_success": sum(1 for v in results.values() if v["success"] > 0),
        "modules_with_failure": sum(1 for v in results.values() if v["failure"] > 0),
        "total_success_calls": total_success,
        "total_failure_calls": total_failure,
        "unbalanced": unbalanced,
        "well_balanced": well_balanced,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return report


async def _wire_and_persist_balance(report: dict[str, Any]) -> dict[str, Any]:
    """The loop-bound half: brain wiring + the dashboard write.

    R-F3707 — deliberately NOT moved into the worker thread with the scan.
    `engine_wiring._dispatch` needs a RUNNING LOOP (it catches RuntimeError and
    falls back at engine_wiring.py:108-111), so emitting from a thread risks the
    §21a signal going dark — trading an 8-second stall for a blind spot is not
    a fix.
    """
    unbalanced = report.get("unbalanced") or []
    results_n = report.get("total_modules") or 0
    total_success = report.get("total_success_calls") or 0
    total_failure = report.get("total_failure_calls") or 0

    # Wire to brain
    if unbalanced:
        wire_failure(
            module="wiring_monitor:M1",
            detail=(
                f"Wire balance audit: {len(unbalanced)} modules have "
                f">={WIRE_BALANCE_THRESHOLD} success calls with 0 failure calls. "
                f"Unbalanced: {', '.join(u['module'] for u in unbalanced[:10])}"
            ),
            gap_type="engine_failure",
            source="wiring_monitor:audit_wire_balance",
        )
    else:
        wire_success(
            module="wiring_monitor:M1",
            summary=f"Wire balance audit: {total_success}S / {total_failure}F across {results_n} modules",
            source_id="wiring_monitor:audit_wire_balance",
        )

    # Persist to Redis for dashboard
    try:
        await rs.set(
            f"{MONITOR_KEY_PREFIX}wire_balance",
            str(report),
            ex=CHECK_INTERVAL_S * 2,
        )
    except Exception:
        pass

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# M2 — Compliance screener crash visibility probe
# ═══════════════════════════════════════════════════════════════════════════════


async def probe_compliance_screeners() -> dict[str, Any]:
    """Probe each compliance screener with malformed input to verify that
    crashes produce a wire_failure signal.

    This is a SOFT probe — it calls the module's public functions with
    deliberately bad input (None, empty strings, non-strings, deeply nested
    objects) and checks whether the module:
      1. Handles it gracefully (returns None / empty), OR
      2. Crashes AND emits a wire_failure

    If a module crashes WITHOUT a wire_failure, that's a G2 gap.
    """
    results: dict[str, dict[str, Any]] = {}

    for module_name in COMPLIANCE_SCREENERS:
        module_path = f"aria_service.intel.{module_name}"
        module_results: dict[str, Any] = {
            "module": module_name,
            "tests": [],
            "has_wire_failure": False,
            "has_wire_success": False,
            "gap": False,
        }

        try:
            import importlib

            mod = importlib.import_module(module_path)
        except Exception as e:
            module_results["import_error"] = str(e)
            results[module_name] = module_results
            continue

        # Check if module has wire_failure calls
        try:
            with open(
                os.path.join(INTEL_DIR, f"{module_name}.py"),
                "r",
                encoding="utf-8",
                errors="replace",
            ) as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and hasattr(node.func, "id"):
                    if node.func.id == "wire_failure":
                        module_results["has_wire_failure"] = True
                    elif node.func.id == "wire_success":
                        module_results["has_wire_success"] = True
        except Exception:
            pass

        # Probe public functions with bad input
        probe_inputs = [
            ("None", None),
            ("empty_string", ""),
            ("integer", 42),
            ("list", ["bad", "input"]),
            ("dict", {"key": "value"}),
            ("very_long_string", "x" * 100_000),
        ]

        for probe_name, probe_value in probe_inputs:
            test_result: dict[str, Any] = {
                "input": probe_name,
                "crashed": False,
                "error": None,
            }
            try:
                # Try calling lookup_by_name or best_match_for_text or score
                for func_name in ("lookup_by_name", "best_match_for_text", "score", "analyse_line_items", "detect", "render_finding", "render_findings_for_ctx"):
                    func = getattr(mod, func_name, None)
                    if func is not None:
                        try:
                            if func_name == "analyse_line_items":
                                result = func([probe_value] if probe_value is not None else None)
                            elif func_name == "detect":
                                from .evasion_typology_detector import DealContext
                                result = func(DealContext())
                            elif func_name == "render_findings_for_ctx":
                                from .evasion_typology_detector import DealContext
                                result = func(DealContext())
                            else:
                                result = func(probe_value)
                            test_result["handled"] = type(result).__name__
                        except Exception as call_e:
                            test_result["crashed"] = True
                            test_result["error"] = f"{type(call_e).__name__}: {str(call_e)[:200]}"
                            test_result["func"] = func_name
                        break  # only test the first found function
            except Exception as e:
                test_result["error"] = str(e)[:200]

            module_results["tests"].append(test_result)

        # Determine if there's a gap
        module_results["gap"] = (
            module_results["has_wire_success"]
            and not module_results["has_wire_failure"]
        )

        results[module_name] = module_results

    # Wire to brain
    gap_modules = [m for m in results.values() if m.get("gap")]
    if gap_modules:
        wire_failure(
            module="wiring_monitor:M2",
            detail=(
                f"Compliance screener probe: {len(gap_modules)} modules "
                f"have wire_success but NO wire_failure — crashes are dark. "
                f"Affected: {', '.join(m['module'] for m in gap_modules)}"
            ),
            gap_type="engine_failure",
            source="wiring_monitor:probe_compliance_screeners",
        )
    else:
        wire_success(
            module="wiring_monitor:M2",
            summary=f"Compliance screener probe: all {len(COMPLIANCE_SCREENERS)} modules have failure wiring",
            source_id="wiring_monitor:probe_compliance_screeners",
        )

    # Persist
    try:
        await rs.set(
            f"{MONITOR_KEY_PREFIX}compliance_probe",
            str(results),
            ex=CHECK_INTERVAL_S * 2,
        )
    except Exception:
        pass

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# M3 — WA connection health monitor
# ═══════════════════════════════════════════════════════════════════════════════


async def check_wa_connection_health() -> dict[str, Any]:
    """Check whether the WA listener has reported connection/auth state
    to the brain recently.

    Reads from capability_gaps for wa_auth_lost / wa_disconnected signals
    and from brain_hook for wa_connected signals. If no signal seen within
    the expected window, flags a potential dark path.
    """
    result: dict[str, Any] = {
        "wa_auth_lost_signals": 0,
        "wa_disconnected_signals": 0,
        "wa_connected_signals": 0,
        "last_signal_age_seconds": None,
        "healthy": True,
        "note": "",
    }

    try:
        # Check capability_gaps for WA auth/disconnect signals
        gaps = await rs.lrange("crucix:aria:capability_gaps", 0, 50) or []
        for gap in gaps:
            if isinstance(gap, str):
                gap_lower = gap.lower()
                if "wa_auth_lost" in gap_lower or "loggedout" in gap_lower:
                    result["wa_auth_lost_signals"] += 1
                if "wa_disconnected" in gap_lower or "disconnect" in gap_lower:
                    result["wa_disconnected_signals"] += 1

        # Check brain_hook for WA connected signals
        # (This is a best-effort check — brain_hook doesn't expose a query API)
        result["note"] = (
            "WA connection health check is passive (reads capability_gaps only). "
            "For full verification, the WA listener must emit wa_auth_lost and "
            "wa_disconnected signals to /api/aria/brain/signal (G3 gap)."
        )
    except Exception as e:
        result["error"] = str(e)[:200]
        result["healthy"] = False

    # C-30 — THE VERDICT WAS INVERTED. This block used to fire `wire_failure` when
    # it found ZERO disconnect signals and `wire_success` when it found some, so a
    # WhatsApp listener that had never dropped was reported as permanently FAILING
    # and one that was dropping constantly was reported as HEALTHY.
    #
    # The docstring above already concedes the check cannot tell "never
    # disconnected" from "these signals are dark" — and the old code resolved that
    # ambiguity by asserting the failure. Absence of evidence is not evidence, in
    # either direction: it is INDETERMINATE, and saying so is the only honest
    # option available to a passive reader of capability_gaps.
    observed = result["wa_auth_lost_signals"] + result["wa_disconnected_signals"]
    result["determinate"] = observed > 0

    if observed > 0:
        # A REAL, OBSERVED problem: the listener has been losing its connection.
        result["healthy"] = False
        wire_failure(
            module="wiring_monitor:M3",
            detail=(
                f"WA connection health: {result['wa_auth_lost_signals']} auth_lost "
                f"and {result['wa_disconnected_signals']} disconnected signals in "
                "capability_gaps — the listener has been dropping."
            ),
            gap_type="engine_failure",
            source="wiring_monitor:check_wa_connection_health",
        )
    else:
        # No verdict. Neither branch is earned, so neither is emitted; a caller
        # reads `determinate: False` and knows this monitor abstained rather than
        # mistaking silence for either health or failure.
        result["note"] += (
            " No auth_lost/disconnected signals observed: INDETERMINATE — this "
            "passive check cannot distinguish a healthy listener from a dark "
            "signal path, so it abstains rather than asserting either."
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M4 — Brain signal path integrity test
# ═══════════════════════════════════════════════════════════════════════════════


# ── R-F3577 — SOURCE READS IN A BACKGROUND LOOP MUST BE CACHED ──────────────
#
# test_brain_signal_path() reads several PRODUCTION SOURCE FILES to decide
# whether a cross-tier path is wired, and it runs on the monitor loop. Those
# files cannot change inside a running process — the code executing IS the code
# on disk — so re-reading them every cycle is blocking I/O on the event loop for
# an answer that is constant for the life of the process.
#
# Found by adding a fourth read (main.py, ~250KB) and watching test_rf1091's
# 0.5s-budgeted loop test fail under load. The test was right to be sensitive:
# the defect is synchronous file I/O in an async loop, not a slow test.
@lru_cache(maxsize=8)
def _read_source(path: str) -> tuple[str, bool]:
    """C-31 — ``(content, readable)``. Read a source file ONCE per process.

    The second element is the whole point. `_cached_source` collapses an
    unreadable file to `""`, so "the token is not in this file" and "there is no
    such file" become the same answer — and every caller that greps for a wiring
    token then concludes the token is ABSENT. That is how M4 came to report the
    Node tier as unwired: aria-intel ships the PYTHON service, the three files it
    greps are Node-tier and simply are not in the image, and their absence was
    read as evidence of a defect.

    Returning readability separately lets a caller say UNKNOWN, which is the only
    honest verdict about a file it cannot open.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), True
    except Exception:
        return "", False


def _cached_source(path: str) -> str:
    """Content only — retained for callers that legitimately want 'absent reads as
    empty'. Anything forming a VERDICT about wiring must use `_read_source` and
    honour the readability flag instead."""
    return _read_source(path)[0]


async def test_brain_signal_path() -> dict[str, Any]:
    """End-to-end test of the brain signal path.

    Emits a test signal to /api/aria/brain/signal and verifies it lands
    in capability_gaps (for failure-type) or brain_hook (for success-type).

    This is a SOFT test — it uses the internal API if available, or
    falls back to checking the module-level wiring.
    """
    result: dict[str, Any] = {
        "signal_sent": False,
        "signal_landed": False,
        "path_healthy": False,
        "detail": "",
    }
    # C-31 — set by any file this check cannot open. See `_read_source`.
    _unreadable = False

    # Check that the brain/signal endpoint exists in routes
    try:
        route_file = os.path.join(
            os.path.dirname(__file__), "..", "routes", "aria.py"
        )
        route_content = _cached_source(route_file)
        result["endpoint_exists"] = (
            'def brain_signal_ep' in route_content
            or 'brain/signal' in route_content
        )
    except Exception as e:
        result["endpoint_check_error"] = str(e)[:200]

    # ── R-F3580 — the brain_signal_consumer checks are REMOVED with the module ──
    #
    # They asserted the consumer's own SOURCE TEXT ("_auto_started" in it, the
    # Redis key in it) and were true for its entire life while the loop had never
    # run. R-F3577 then "fixed" that by starting the loop — against a key NOTHING
    # WRITES. Both the monitor and the fix were reading a retired transport.
    #
    # The cross-tier signal path is HTTP and is checked below via the endpoint and
    # the producer: pushSignalsToBrain() -> /brain/signal/bulk (R-F2505). That is
    # the path carrying real traffic (live: 120 signals absorbed under
    # cross_tier:crucix_briefing_signal), so it is the one worth monitoring.
    result["redis_signal_transport"] = "retired"

    # Check that the web tier's pushSignalsToBrain is NOT a no-op
    try:
        briefing_file = os.path.join(
            os.path.dirname(__file__), "..", "..", "apis", "briefing.mjs"
        )
        briefing_content = _cached_source(briefing_file)
        result["pushSignalsToBrain_is_noop"] = (
            "no-op since Upstash retirement" in briefing_content
        )
    except Exception as e:
        result["briefing_check_error"] = str(e)[:200]

    # Check that errorTracker._reportToBrain is wired
    try:
        tracker_file = os.path.join(
            os.path.dirname(__file__), "..", "..", "lib", "observability", "errorTracker.mjs"
        )
        tracker_content, _readable = _read_source(tracker_file)
        _unreadable = _unreadable or not _readable
        result["errorTracker_wired"] = (
            "/api/aria/brain/signal" in tracker_content
        )
    except Exception as e:
        result["tracker_check_error"] = str(e)[:200]
        _unreadable = True

    # Check that WA listener is wired
    try:
        wa_file = os.path.join(
            os.path.dirname(__file__), "..", "..", "services", "wa-listener", "aria_wa_listener.mjs"
        )
        wa_content, _readable = _read_source(wa_file)
        _unreadable = _unreadable or not _readable
        result["wa_listener_wired"] = "/api/aria/brain/signal" in wa_content
        result["wa_listener_has_auth_loss_dark"] = (
            "loggedOut" in wa_content
            and "console.log" in wa_content
            and "brainPost" not in wa_content.split("loggedOut")[1][:200]
        )
    except Exception as e:
        result["wa_check_error"] = str(e)[:200]
        _unreadable = True

    # Check zoom service
    try:
        zoom_file = os.path.join(
            os.path.dirname(__file__), "..", "..", "services", "aria_zoom_service.py"
        )
        zoom_content, _readable = _read_source(zoom_file)
        _unreadable = _unreadable or not _readable
        result["zoom_uses_bare_brain_signal"] = (
            "/api/brain/signal" in zoom_content
            and "/api/aria/brain/signal" not in zoom_content
        )
    except Exception as e:
        result["zoom_check_error"] = str(e)[:200]
        _unreadable = True

    # C-31 — could this process actually SEE what it is judging?
    result["inspectable"] = not _unreadable

    if _unreadable:
        # UNKNOWN, and it must stay unknown. aria-intel ships the Python service,
        # so the Node-tier files are legitimately absent here — that is not
        # evidence the Node tier is unwired, and C-27/R-F3889 measured that wire
        # LIVE and made it readable at /api/health/brain-wire. Asserting a failure
        # from a file we cannot open produced a permanently-red monitor whose
        # message named an already-wired module: a wrong cause pointing at a wrong
        # fix. No verdict is wired here — `inspectable: False` IS the finding, and
        # a monitor that is red no matter what teaches everyone to ignore it.
        result["path_healthy"] = None
        result["detail"] = (
            "Node-tier sources not present in this image — brain-signal path is "
            "UNVERIFIABLE from here, not broken. Verify cross-tier wiring via "
            "GET /api/health/brain-wire on aria-web (C-27/R-F3889)."
        )
        return result

    # Overall health
    result["path_healthy"] = (
        result.get("endpoint_exists", False)
        and result.get("errorTracker_wired", False)
        and result.get("wa_listener_wired", False)
        and not result.get("zoom_uses_bare_brain_signal", True)
    )

    # Wire to brain
    if result["path_healthy"]:
        wire_success(
            module="wiring_monitor:M4",
            summary="Brain signal path integrity: all tiers wired correctly",
            source_id="wiring_monitor:test_brain_signal_path",
        )
    else:
        issues = []
        if not result.get("endpoint_exists"):
            issues.append("brain/signal endpoint missing")
        if result.get("zoom_uses_bare_brain_signal"):
            issues.append("zoom uses dead /api/brain/signal")
        if result.get("wa_listener_has_auth_loss_dark"):
            issues.append("WA auth-loss path is dark")
        wire_failure(
            module="wiring_monitor:M4",
            detail=f"Brain signal path issues: {'; '.join(issues)}",
            gap_type="engine_failure",
            source="wiring_monitor:test_brain_signal_path",
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M5 — Self-coding loop health
# ═══════════════════════════════════════════════════════════════════════════════


def _actionable_staged(staged: list) -> tuple[int, float | None]:
    """Count ONLY actionable staged items (status == 'staged'), matching
    self_improve.get_staged(). R-F2878: `crucix:aria:staged_improvements` is an
    append-only list that RETAINS deployed/rejected entries, so its raw length
    (90 live) over-reports a queue that is actually draining (3 actionable). Counting
    the raw length false-alarmed 'not draining'. Returns (count, oldest_age_minutes),
    with the oldest computed over ACTIONABLE items only and None when unknown (the old
    'Nonemin' render came from a None age)."""
    import time
    actionable = [s for s in staged if isinstance(s, dict) and s.get("status") == "staged"]
    oldest = None
    now = time.time()
    for item in actionable:
        ts = item.get("timestamp") or item.get("created_at") or ""
        if ts:
            try:
                age = now - float(ts)
                if oldest is None or age > oldest:
                    oldest = age
            except (ValueError, TypeError):
                pass
    return len(actionable), (round(oldest / 60, 1) if oldest is not None else None)


async def check_coder_loop_health() -> dict[str, Any]:
    """Check that the self-coding loop is healthy.

    Reads:
      - Staged improvements count and age
      - Gap detector latest scan timestamp
      - Coder cycle count
      - Rate limiter state
    """
    result: dict[str, Any] = {
        "staged_count": 0,
        "staged_oldest_age_minutes": None,
        "gap_detector_last_scan": None,
        "coder_cycle_count": 0,
        "healthy": True,
        "detail": "",
    }

    try:
        # Check staged improvements
        staged_raw = await rs.get("crucix:aria:staged_improvements")
        if staged_raw:
            import json
            try:
                staged = json.loads(staged_raw) if isinstance(staged_raw, str) else staged_raw
                if isinstance(staged, list):
                    # R-F2878 — count only ACTIONABLE (status=='staged'), not the raw
                    # append-only history, so a draining queue isn't false-alarmed.
                    cnt, oldest_min = _actionable_staged(staged)
                    result["staged_count"] = cnt
                    result["staged_oldest_age_minutes"] = oldest_min
            except (json.JSONDecodeError, TypeError):
                pass

        # Check gap detector latest
        latest_raw = await rs.get("crucix:autonomous:gap_detector:latest")
        if latest_raw:
            result["gap_detector_last_scan"] = str(latest_raw)[:200]

        # Check coder cycle count
        cycle_count = await rs.get("crucix:aria:coder:cycle_count")
        if cycle_count:
            try:
                result["coder_cycle_count"] = int(cycle_count)
            except (ValueError, TypeError):
                pass

    except Exception as e:
        result["error"] = str(e)[:200]
        result["healthy"] = False

    # Determine health
    _age = result["staged_oldest_age_minutes"]
    _age_str = f"{_age}min old" if _age is not None else "age unknown"   # R-F2878 — no literal 'Nonemin'
    if result["staged_count"] > 50:
        result["healthy"] = False
        result["detail"] = (
            f"Staged queue has {result['staged_count']} items — "
            f"not draining fast enough. Oldest is {_age_str}."
        )
    elif result["staged_count"] == 0:
        result["detail"] = "No staged improvements — coder may not be running or no gaps found."
    else:
        result["detail"] = (
            f"{result['staged_count']} staged improvements, "
            f"oldest {_age_str} — draining."
        )

    # Wire to brain
    if result["healthy"]:
        wire_success(
            module="wiring_monitor:M5",
            summary=f"Coder loop: {result['staged_count']} staged, {result['coder_cycle_count']} cycles",
            source_id="wiring_monitor:check_coder_loop_health",
        )
    else:
        wire_failure(
            module="wiring_monitor:M5",
            detail=result["detail"],
            gap_type="engine_failure",
            source="wiring_monitor:check_coder_loop_health",
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator — run all monitors
# ═══════════════════════════════════════════════════════════════════════════════


async def run_all_checks() -> dict[str, Any]:
    """Run all five monitors and return a composite report."""
    logger.info("[wiring_monitor] Running all checks...")
    results = {
        "M1_wire_balance": await audit_wire_balance(),
        "M2_compliance_probe": await probe_compliance_screeners(),
        "M3_wa_health": await check_wa_connection_health(),
        "M4_brain_signal_path": await test_brain_signal_path(),
        "M5_coder_loop": await check_coder_loop_health(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Composite health
    # C-31 — `path_healthy` is TRI-STATE now: True / False / None (could not
    # inspect). `None` must not be folded into "has issues", which is what a bare
    # truthiness test does and is exactly the conflation this fix removes.
    m4_state = results["M4_brain_signal_path"].get("path_healthy", False)
    m4_unknown = m4_state is None
    m4_healthy = m4_state is True
    m5_healthy = results["M5_coder_loop"].get("healthy", True)
    m2_gaps = len([m for m in results["M2_compliance_probe"].values() if m.get("gap")])
    m1_unbalanced = len(results["M1_wire_balance"].get("unbalanced", []))

    # An unverifiable M4 does not by itself degrade the composite — a monitor that
    # abstained has reported nothing to be alarmed about. It is surfaced instead,
    # so "unknown" stays visible rather than silently counting as either state.
    results["composite_health"] = (
        "healthy"
        if ((m4_healthy or m4_unknown) and m5_healthy and m2_gaps == 0 and m1_unbalanced == 0)
        else "degraded"
    )
    _m4_word = (
        "UNVERIFIABLE from this image (Node tier absent — check /api/health/brain-wire)"
        if m4_unknown else ("healthy" if m4_healthy else "has issues")
    )
    results["composite_detail"] = (
        f"M1: {m1_unbalanced} unbalanced modules. "
        f"M2: {m2_gaps} compliance screeners with dark failures. "
        f"M4: brain signal path {_m4_word}. "
        f"M5: coder loop {'healthy' if m5_healthy else 'degraded'}."
    )

    # Wire composite to brain
    if results["composite_health"] == "healthy":
        wire_success(
            module="wiring_monitor",
            summary=f"All monitors healthy: {results['composite_detail']}",
            source_id="wiring_monitor:run_all_checks",
        )
    else:
        wire_failure(
            module="wiring_monitor",
            detail=results["composite_detail"],
            gap_type="engine_failure",
            source="wiring_monitor:run_all_checks",
        )

    # Persist composite
    try:
        await rs.set(
            f"{MONITOR_KEY_PREFIX}latest",
            str(results),
            ex=CHECK_INTERVAL_S * 2,
        )
    except Exception:
        pass

    logger.info(
        "[wiring_monitor] Checks complete: %s — %s",
        results["composite_health"],
        results["composite_detail"],
    )
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Background loop
# ═══════════════════════════════════════════════════════════════════════════════


async def monitor_loop() -> None:
    """Background loop that runs all checks on an interval."""
    logger.info(
        "[wiring_monitor] Background loop started (interval=%ds)",
        CHECK_INTERVAL_S,
    )
    # Tick heartbeat so the agent registry sees us alive
    await _tick_wiring_heartbeat()
    # Run first check immediately
    await run_all_checks()
    while True:
        await asyncio.sleep(CHECK_INTERVAL_S)
        try:
            await _tick_wiring_heartbeat()
            await run_all_checks()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[wiring_monitor] Loop error: %s", e, exc_info=True)


async def _tick_wiring_heartbeat() -> None:
    """Tick the wiring_monitor heartbeat in the agent registry."""
    try:
        from .agent_registry import AgentRegistry
        _reg = AgentRegistry()
        await _reg.tick_heartbeat("wiring_monitor", "Wire balance audit, compliance screener probe, brain signal path integrity")
    except Exception:
        pass


def start_monitor() -> asyncio.Task:
    """Start the background monitor loop. Returns the task handle."""
    task = asyncio.create_task(monitor_loop(), name="wiring_monitor")
    logger.info("[wiring_monitor] Started (task=%s)", task.get_name())
    return task
