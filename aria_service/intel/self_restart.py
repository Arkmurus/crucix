"""R-F1146 — ARIA Self-Restart & Blackout Recovery System.

ARIA's brain can stall, wedge, or "black out" — the event loop freezes,
the autonomous coder gets stuck in a dedupe loop, or the agent loses
context mid-session. When this happens, the operator has to re-explain
everything from scratch.

This module closes that loop with three mechanisms:

  1. Session Checkpointing — saves the agent's last N actions, current
     task, error context, and a "what was I doing" summary to a JSON
     checkpoint file. Written on every significant state transition.

  2. Blackout Detection — a background task that monitors the agent's
     heartbeat. If the heartbeat goes stale for too long (configurable
     threshold), it logs a blackout event, captures a wedge stack, and
     triggers recovery.

  3. Self-Restart Entry Point — a recovery endpoint that the agent can
     call to restart from the latest checkpoint. Returns the saved
     context so the agent can pick up where it left off.

  4. Recovery Replay — replays the last N actions after a restart so
     the agent can re-establish context without the operator repeating
     instructions.

All four mechanisms wire to the brain via brain_hook so ARIA learns
from every blackout and can self-improve the recovery logic.

Usage
─────
  from .self_restart import (
      save_checkpoint,
      load_latest_checkpoint,
      get_blackout_status,
      trigger_self_restart,
  )

  # After every significant action:
  await save_checkpoint(
      agent_id="aria_coder",
      current_task="Fixing neural_memory.py",
      last_actions=[...],
      error_context="...",
  )

  # On session start, check if we're recovering:
  checkpoint = await load_latest_checkpoint("aria_coder")
  if checkpoint and checkpoint.get("blackout_detected"):
      # We're recovering from a blackout — replay last actions
      await replay_actions(checkpoint["last_actions"])
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.intel.self_restart")

# ── Configuration ─────────────────────────────────────────────────────────────

# How often the blackout detector checks the heartbeat (seconds)
_BLACKOUT_CHECK_INTERVAL_S = int(os.getenv("ARIA_BLACKOUT_CHECK_INTERVAL", "30"))

# How long the heartbeat can be stale before we declare a blackout (seconds)
_BLACKOUT_THRESHOLD_S = int(os.getenv("ARIA_BLACKOUT_THRESHOLD", "300"))  # 5 min

# Max checkpoint files to keep per agent
_MAX_CHECKPOINTS_PER_AGENT = int(os.getenv("ARIA_MAX_CHECKPOINTS", "10"))

# Max actions to keep in a checkpoint's replay buffer
_MAX_REPLAY_ACTIONS = int(os.getenv("ARIA_MAX_REPLAY_ACTIONS", "50"))

# Where checkpoints live — prefer fly's persistent /data volume
_CHECKPOINT_DIR = os.getenv(
    "ARIA_CHECKPOINT_DIR",
    "/data/self_restart_checkpoints",
)

# R-F1435: blackout dump size cap + retention — prevent runaway 128MB dumps
# from filling /data (live incident 2026-06-07: 1.4GB of dumps filled 5GB volume).
# Each dump is truncated at _MAX_DUMP_BYTES; the wedge_stacks dir is kept under
# _MAX_WEDGE_DIR_BYTES by pruning oldest files on each write.
_MAX_DUMP_BYTES = int(os.getenv("ARIA_MAX_WEDGE_DUMP_BYTES", str(5 * 1024 * 1024)))  # 5MB
_MAX_WEDGE_DIR_BYTES = int(os.getenv("ARIA_MAX_WEDGE_DIR_BYTES", str(200 * 1024 * 1024)))  # 200MB
_MAX_WEDGE_FILES = int(os.getenv("ARIA_MAX_WEDGE_FILES", "50"))  # keep at most 50 files

# Fall back to repo-local data/ dir in dev / CI
_REPO_CHECKPOINT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "self_restart_checkpoints",
)


def _resolve_checkpoint_dir() -> str:
    """Resolve the checkpoint directory — prefer fly volume, fall back to local."""
    d = _CHECKPOINT_DIR
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        d = "/data/self_restart_checkpoints"
    else:
        d = _REPO_CHECKPOINT_DIR
    os.makedirs(d, exist_ok=True)
    return d


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class AgentAction:
    """A single action the agent took — used for replay after restart."""
    timestamp: float
    action_type: str  # e.g. "fix_gap", "deploy", "research", "chat"
    summary: str
    result: str  # "success", "failure", "skipped"
    detail: str = ""


@dataclass
class SessionCheckpoint:
    """A snapshot of the agent's state at a point in time."""
    agent_id: str
    timestamp: float
    timestamp_iso: str
    current_task: str
    last_actions: list[dict]  # serialised AgentAction list
    error_context: str
    blackout_detected: bool
    recovery_count: int  # how many times this agent has recovered
    version: int = 1  # schema version for forward compat


# ── In-memory state ──────────────────────────────────────────────────────────

# Per-agent heartbeat: {agent_id: last_tick_timestamp}
_heartbeats: dict[str, float] = {}

# Per-agent blackout count: {agent_id: count}
_blackout_counts: dict[str, int] = {}

# Per-agent recovery count: {agent_id: count}
_recovery_counts: dict[str, int] = {}

# Whether the blackout detector background task is running
_detector_running: bool = False
_detector_task: Optional[asyncio.Task] = None


# ── Checkpoint I/O ────────────────────────────────────────────────────────────


def _checkpoint_path(agent_id: str, timestamp: float) -> str:
    """Build the filesystem path for a checkpoint."""
    d = _resolve_checkpoint_dir()
    # Sanitise agent_id — no path separators
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    return os.path.join(d, f"checkpoint_{safe_id}_{int(timestamp)}.json")


def _latest_checkpoint_path(agent_id: str) -> str:
    """Build the path for the 'latest' symlink/copy for an agent."""
    d = _resolve_checkpoint_dir()
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    return os.path.join(d, f"checkpoint_{safe_id}_latest.json")


async def save_checkpoint(
    agent_id: str,
    current_task: str = "",
    last_actions: list[AgentAction] | None = None,
    error_context: str = "",
    blackout_detected: bool = False,
) -> dict:
    """Save a session checkpoint for the given agent.

    This is the main entry point for checkpointing. Call it after every
    significant state transition (task completion, error, deploy, etc.).

    Returns the saved checkpoint dict (for brain wiring).
    """
    now = time.time()
    iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    # Trim replay buffer
    actions = last_actions or []
    if len(actions) > _MAX_REPLAY_ACTIONS:
        actions = actions[-_MAX_REPLAY_ACTIONS:]

    recovery_count = _recovery_counts.get(agent_id, 0)

    checkpoint = SessionCheckpoint(
        agent_id=agent_id,
        timestamp=now,
        timestamp_iso=iso,
        current_task=current_task,
        last_actions=[asdict(a) if isinstance(a, AgentAction) else a for a in actions],
        error_context=error_context[:2000],  # cap at 2KB
        blackout_detected=blackout_detected,
        recovery_count=recovery_count,
    )

    cp_dict = asdict(checkpoint)

    # Write to timestamped file
    path = _checkpoint_path(agent_id, now)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cp_dict, f, indent=2, default=str)
    except OSError as e:
        logger.warning("[self_restart] Failed to write checkpoint %s: %s", path, e)
        return {"success": False, "error": str(e)}

    # Write to latest file (overwrite)
    latest_path = _latest_checkpoint_path(agent_id)
    try:
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(cp_dict, f, indent=2, default=str)
    except OSError as e:
        logger.warning("[self_restart] Failed to write latest checkpoint %s: %s", latest_path, e)
        # Non-fatal — the timestamped file exists

    # Prune old checkpoints
    _prune_old_checkpoints(agent_id)

    # Wire to brain
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="self_restart",
            summary=f"Checkpoint saved for {agent_id}: {current_task[:100]}",
            source_id=f"self_restart:checkpoint:{agent_id}",
        )
    except ImportError:
        pass

    logger.info(
        "[self_restart] Checkpoint saved for %s: %s (%d actions, %s)",
        agent_id, current_task[:80], len(actions),
        "blackout" if blackout_detected else "normal",
    )

    return {"success": True, "path": path, "checkpoint": cp_dict}


def _prune_old_checkpoints(agent_id: str) -> None:
    """Remove old checkpoint files beyond the retention limit."""
    d = _resolve_checkpoint_dir()
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    prefix = f"checkpoint_{safe_id}_"
    try:
        files = [
            os.path.join(d, f)
            for f in os.listdir(d)
            if f.startswith(prefix) and f.endswith(".json") and "_latest" not in f
        ]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[_MAX_CHECKPOINTS_PER_AGENT:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


async def load_latest_checkpoint(agent_id: str) -> dict | None:
    """Load the most recent checkpoint for an agent.

    Returns the checkpoint dict, or None if no checkpoint exists.
    This is the entry point for recovery — call it at session start
    to check if we're recovering from a blackout.
    """
    latest_path = _latest_checkpoint_path(agent_id)
    if not os.path.isfile(latest_path):
        return None

    try:
        with open(latest_path, "r", encoding="utf-8") as f:
            cp = json.load(f)
        return cp
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[self_restart] Failed to load latest checkpoint: %s", e)
        return None


async def list_checkpoints(agent_id: str, limit: int = 10) -> list[dict]:
    """List recent checkpoints for an agent (metadata only, no actions)."""
    d = _resolve_checkpoint_dir()
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    prefix = f"checkpoint_{safe_id}_"
    results: list[dict] = []
    try:
        for fname in os.listdir(d):
            if not fname.startswith(prefix) or not fname.endswith(".json"):
                continue
            if "_latest" in fname:
                continue
            full = os.path.join(d, fname)
            try:
                st = os.stat(full)
                with open(full, "r", encoding="utf-8") as f:
                    cp = json.load(f)
                results.append({
                    "path": fname,
                    "timestamp": cp.get("timestamp", st.st_mtime),
                    "timestamp_iso": cp.get("timestamp_iso", ""),
                    "current_task": cp.get("current_task", ""),
                    "blackout_detected": cp.get("blackout_detected", False),
                    "recovery_count": cp.get("recovery_count", 0),
                    "action_count": len(cp.get("last_actions", [])),
                })
            except (OSError, json.JSONDecodeError):
                continue
    except OSError:
        pass

    results.sort(key=lambda r: r["timestamp"], reverse=True)
    return results[:limit]


# ── Heartbeat ─────────────────────────────────────────────────────────────────


def tick_heartbeat(agent_id: str = "aria_main") -> None:
    """Mark the agent as alive. Call this periodically from the main loop.

    The blackout detector checks this timestamp. If it goes stale beyond
    _BLACKOUT_THRESHOLD_S, a blackout is declared.
    """
    _heartbeats[agent_id] = time.time()


def get_heartbeat_age(agent_id: str = "aria_main") -> float:
    """How many seconds since the last heartbeat tick for this agent.

    Returns float('inf') if no heartbeat has ever been recorded.
    """
    last = _heartbeats.get(agent_id)
    if last is None:
        return float("inf")
    return time.time() - last


# ── Blackout Detection ────────────────────────────────────────────────────────


def _prune_wedge_dir(wedge_dir: str) -> None:
    """R-F1435: enforce retention + size budget on the wedge dump directory.

    Keeps at most _MAX_WEDGE_FILES files and total size under
    _MAX_WEDGE_DIR_BYTES by deleting oldest files first. Best-effort —
    never raises.
    """
    try:
        if not os.path.isdir(wedge_dir):
            return
        entries: list[tuple[str, float, int]] = []
        for name in os.listdir(wedge_dir):
            full = os.path.join(wedge_dir, name)
            try:
                st = os.stat(full)
                if st.st_size > 0:
                    entries.append((full, st.st_mtime, st.st_size))
            except OSError:
                continue
        # Sort oldest first
        entries.sort(key=lambda e: e[1])
        total = sum(e[2] for e in entries)
        # Prune by count
        while len(entries) > _MAX_WEDGE_FILES:
            old = entries.pop(0)
            try:
                os.remove(old[0])
                total -= old[2]
            except OSError:
                pass
        # Prune by size
        while total > _MAX_WEDGE_DIR_BYTES and entries:
            old = entries.pop(0)
            try:
                os.remove(old[0])
                total -= old[2]
            except OSError:
                pass
    except Exception:
        pass  # Best-effort


# R-F3360 — public alias. The retention budget above was reachable from exactly
# one place (save_blackout_wedge, i.e. only the self-restart blackout path), but
# the writer that actually FILLS the directory is the R-F704 stall detector in
# main.py, which opens a new `wedge_<pid>_<epoch>.log` on every boot and never
# pruned. Live 2026-07-28: 513 files, oldest seven weeks old, against a cap of
# 50 that had existed the whole time — the guard written after /data filled on
# 2026-06-07 was not on the path that fills /data. Exposed publicly so the boot
# path can enforce the budget without reaching into a private.
prune_wedge_dir = _prune_wedge_dir


def _write_wedge_dump(agent_id: str, stale: float, wedge_dir: str | None = None) -> str | None:
    """R-F1334: write a blackout wedge dump and return its path (None on failure).

    Extracted from the inline block in _blackout_detector_loop so a capability
    test can drive the REAL dump path directly (the R-F1333 test depended on
    import-time env constants and could silently assert nothing). Contents:
      1. faulthandler thread stacks (R-F1146)
      2. asyncio task stacks — every alive Task + get_stack() (R-F1333)
      3. state_store RMW-lock diagnostics incl. HOLDER task + acquire stack
         (R-F1334 via state_store.get_lock_diagnostics — verified to exist)

    R-F1435: each dump is capped at _MAX_DUMP_BYTES (5MB default) and the
    wedge directory is pruned to stay under _MAX_WEDGE_DIR_BYTES (200MB) and
    _MAX_WEDGE_FILES (50 files). This prevents the 128MB-dump runaway that
    filled /data on 2026-06-07.

    Must be called from inside the running event loop (uses all_tasks()).
    """
    try:
        import faulthandler as _fh
        if wedge_dir is None:
            wedge_dir = "/data/wedge_stacks"
            if not os.path.isdir(wedge_dir):
                wedge_dir = os.path.join(
                    os.path.dirname(__file__), "..", "..", "data", "wedge_stacks",
                )
        os.makedirs(wedge_dir, exist_ok=True)
        wedge_path = os.path.join(
            wedge_dir,
            f"blackout_{agent_id}_{os.getpid()}_{int(time.time())}.log",
        )
        # R-F1435: write with a size cap — truncate the dump at _MAX_DUMP_BYTES
        # so a single blackout storm can't fill the volume. 5MB is plenty for
        # thread stacks + asyncio tasks + lock diagnostics.
        with open(wedge_path, "a", buffering=1, encoding="utf-8") as fh:
            _remaining = _MAX_DUMP_BYTES

            def _write_capped(text: str) -> None:
                nonlocal _remaining
                if _remaining <= 0:
                    return
                chunk = text.encode("utf-8")
                if len(chunk) > _remaining:
                    chunk = chunk[:_remaining]
                    fh.write(chunk.decode("utf-8", errors="replace"))
                    fh.write("\n... (dump truncated at size cap) ...\n")
                    _remaining = 0
                else:
                    fh.write(text)
                    _remaining -= len(chunk)

            _write_capped(
                f"\n=== [R-F1146] Blackout detected for {agent_id}: "
                f"heartbeat stale {stale:.1f}s ===\n"
            )
            _fh.dump_traceback(file=fh, all_threads=True)
            # R-F1333: dump asyncio task stacks. faulthandler only
            # shows thread stacks — stuck coroutines (awaiting a lock,
            # semaphore, or queue that never resolves) are invisible.
            _write_capped("\n=== asyncio task stacks ===\n")
            try:
                _loop = asyncio.get_running_loop()
                for _t in asyncio.all_tasks(_loop):
                    _name = _t.get_name() if hasattr(_t, "get_name") else str(_t)
                    _coro = _t.get_coro()
                    _coro_name = getattr(_coro, "__name__", str(_coro)) if _coro else "?"
                    _done = _t.done()
                    _cancelled = _t.cancelled()
                    _write_capped(
                        f"\n--- Task: {_name} ({_coro_name}) "
                        f"done={_done} cancelled={_cancelled} ---\n"
                    )
                    if not _done and not _cancelled:
                        try:
                            for _frame in _t.get_stack():
                                _write_capped(
                                    f"  {_frame.f_code.co_filename}:"
                                    f"{_frame.f_lineno} {_frame.f_code.co_name}\n"
                                )
                        except Exception:
                            _write_capped("  (stack unavailable)\n")
            except RuntimeError:
                _write_capped("  (no running loop)\n")
            except Exception as _te:
                _write_capped(f"  (task dump error: {_te})\n")
            _write_capped("=== end asyncio task stacks ===\n")
            # R-F1334: state_store lock diagnostics — names the HOLDER
            # (task + acquire stack + hold duration), which plain
            # asyncio.Lock cannot reveal (the R-F1333 probe saw waiters
            # only). get_lock_diagnostics() verified in state_store.py.
            _write_capped("\n=== state_store lock status ===\n")
            try:
                from . import state_store as _ss
                _diag = _ss.get_lock_diagnostics()
                _write_capped(f"  initialised={_diag.get('initialised')}\n")
                _write_capped(f"  locked={_diag.get('locked')}\n")
                _write_capped(f"  waiters={_diag.get('waiters')}\n")
                if _diag.get("locked") and "holder_task" in _diag:
                    _write_capped(f"  holder_task={_diag.get('holder_task')}\n")
                    _write_capped(f"  held_for_s={_diag.get('held_for_s')}\n")
                    _write_capped("  holder acquire stack (innermost last):\n")
                    for _line in _diag.get("holder_stack") or []:
                        _write_capped(f"    {_line}\n")
            except Exception as _le:
                _write_capped(f"  (lock probe error: {_le})\n")
            _write_capped("=== end state_store lock status ===\n")
            _write_capped("=== end blackout stack dump ===\n")

        # R-F1435: enforce retention + size budget after writing
        _prune_wedge_dir(wedge_dir)

        logger.info("[self_restart] Blackout wedge saved to %s", wedge_path)
        return wedge_path
    except Exception:
        return None  # Non-fatal — wedge capture is best-effort


async def _blackout_detector_loop() -> None:
    """Background task that monitors heartbeats and triggers recovery on stall.

    Runs every _BLACKOUT_CHECK_INTERVAL_S seconds. For each agent with a
    stale heartbeat beyond _BLACKOUT_THRESHOLD_S, it:
      1. Logs a WARNING with the stale duration
      2. Saves a checkpoint with blackout_detected=True
      3. Captures a wedge stack (if the wedge watchdog is available)
      4. Wires the blackout to the brain
      5. Increments the blackout counter
    """
    global _detector_running
    _detector_running = True
    logger.info(
        "[self_restart] Blackout detector started (interval=%ds, threshold=%ds)",
        _BLACKOUT_CHECK_INTERVAL_S, _BLACKOUT_THRESHOLD_S,
    )

    try:
        while True:
            await asyncio.sleep(_BLACKOUT_CHECK_INTERVAL_S)
            now = time.time()

            for agent_id, last_tick in list(_heartbeats.items()):
                stale = now - last_tick
                if stale < _BLACKOUT_THRESHOLD_S:
                    continue

                # ── Blackout detected ──
                _blackout_counts[agent_id] = _blackout_counts.get(agent_id, 0) + 1
                count = _blackout_counts[agent_id]

                # R-F1332: detect agents that have NEVER had a heartbeat tick
                # (stale == inf) — this means the agent was registered but
                # never started properly. Log a distinct message so ops can
                # distinguish "agent crashed" from "agent never started".
                if stale == float("inf"):
                    logger.warning(
                        "[self_restart] AGENT NEVER STARTED for %s: "
                        "heartbeat was never ticked (threshold=%.1fs). "
                        "The agent registered but never called tick_heartbeat().",
                        agent_id, _BLACKOUT_THRESHOLD_S,
                    )
                else:
                    logger.warning(
                        "[self_restart] BLACKOUT detected for %s: heartbeat stale "
                        "for %.1fs (threshold=%.1fs, count=%d)",
                        agent_id, stale, _BLACKOUT_THRESHOLD_S, count,
                    )

                # Save a blackout checkpoint
                try:
                    await save_checkpoint(
                        agent_id=agent_id,
                        current_task="(blackout recovery)",
                        error_context=f"Heartbeat stale for {stale:.1f}s (threshold={_BLACKOUT_THRESHOLD_S}s)",
                        blackout_detected=True,
                    )
                except Exception as e:
                    logger.error("[self_restart] Failed to save blackout checkpoint: %s", e)

                # Capture a wedge dump (threads + asyncio tasks + lock holder).
                # R-F1334: extracted to _write_wedge_dump so tests drive the
                # real path. Best-effort — returns None on failure.
                _write_wedge_dump(agent_id, stale)

                # Wire to brain
                try:
                    from .engine_wiring import wire_failure
                    wire_failure(
                        module="self_restart",
                        detail=f"Blackout detected for {agent_id}: heartbeat stale {stale:.1f}s",
                        gap_type="blackout",
                        source=f"self_restart:blackout:{agent_id}",
                    )
                except ImportError:
                    pass

                # Reset the heartbeat so we don't keep firing
                _heartbeats[agent_id] = time.time()

    except asyncio.CancelledError:
        pass
    finally:
        _detector_running = False
        logger.info("[self_restart] Blackout detector stopped")


def start_blackout_detector() -> None:
    """Start the blackout detector background task.

    Called from main.py lifespan or from the autonomous engine startup.
    Safe to call multiple times — only starts once.
    """
    global _detector_task, _detector_running
    if _detector_running or _detector_task is not None:
        return
    _detector_task = asyncio.create_task(
        _blackout_detector_loop(),
        name="self_restart.blackout_detector",
    )


def stop_blackout_detector() -> None:
    """Stop the blackout detector background task."""
    global _detector_task, _detector_running
    if _detector_task is not None:
        _detector_task.cancel()
        _detector_task = None
    _detector_running = False


# ── Self-Restart ──────────────────────────────────────────────────────────────


async def trigger_self_restart(agent_id: str = "aria_main") -> dict:
    """Trigger a self-restart for the given agent.

    This is the main recovery entry point. It:
      1. Loads the latest checkpoint
      2. Increments the recovery counter
      3. Saves a recovery checkpoint
      4. Returns the recovery context so the caller can resume

    Returns a dict with:
      - success: bool
      - checkpoint: dict | None — the loaded checkpoint
      - recovery_count: int
      - replay_actions: list — actions to replay
      - message: str
    """
    checkpoint = await load_latest_checkpoint(agent_id)
    recovery_count = _recovery_counts.get(agent_id, 0) + 1
    _recovery_counts[agent_id] = recovery_count

    replay_actions: list[dict] = []
    if checkpoint:
        replay_actions = checkpoint.get("last_actions", [])[-10:]  # last 10 actions

    # Save a recovery checkpoint
    await save_checkpoint(
        agent_id=agent_id,
        current_task=f"(recovery #{recovery_count})",
        last_actions=[AgentAction(
            timestamp=time.time(),
            action_type="self_restart",
            summary=f"Self-restart triggered (recovery #{recovery_count})",
            result="success",
        )],
        error_context=f"Recovery #{recovery_count} from blackout",
    )

    # Wire to brain
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="self_restart",
            summary=f"Self-restart triggered for {agent_id} (recovery #{recovery_count})",
            source_id=f"self_restart:restart:{agent_id}",
        )
    except ImportError:
        pass

    logger.info(
        "[self_restart] Self-restart triggered for %s (recovery #%d, %d replay actions)",
        agent_id, recovery_count, len(replay_actions),
    )

    return {
        "success": True,
        "checkpoint": checkpoint,
        "recovery_count": recovery_count,
        "replay_actions": replay_actions,
        "message": f"Self-restart triggered for {agent_id} (recovery #{recovery_count})",
    }


# ── Status ────────────────────────────────────────────────────────────────────


def get_blackout_status() -> dict:
    """Get the current blackout/recovery status for all agents.

    Returns:
      {
        detector_running: bool,
        agents: {
          agent_id: {
            heartbeat_age_s: float,
            blackout_count: int,
            recovery_count: int,
          }
        }
      }
    """
    agents: dict[str, dict] = {}
    for agent_id in set(list(_heartbeats.keys()) + list(_blackout_counts.keys()) + list(_recovery_counts.keys())):
        agents[agent_id] = {
            "heartbeat_age_s": get_heartbeat_age(agent_id),
            "blackout_count": _blackout_counts.get(agent_id, 0),
            "recovery_count": _recovery_counts.get(agent_id, 0),
        }

    return {
        "detector_running": _detector_running,
        "check_interval_s": _BLACKOUT_CHECK_INTERVAL_S,
        "threshold_s": _BLACKOUT_THRESHOLD_S,
        "agents": agents,
    }


# ── Recovery Replay ───────────────────────────────────────────────────────────


async def replay_actions(actions: list[dict]) -> list[dict]:
    """Replay a list of actions after a restart.

    This doesn't actually re-execute the actions — it returns them in a
    structured format so the agent can re-establish context. The agent
    decides which actions to re-run and which to skip.

    Each action dict is annotated with:
      - replayed: True
      - replay_timestamp: current time

    Returns the annotated action list.
    """
    now = time.time()
    replayed: list[dict] = []
    for action in actions:
        annotated = dict(action)
        annotated["replayed"] = True
        annotated["replay_timestamp"] = now
        replayed.append(annotated)

    logger.info("[self_restart] Replayed %d actions for context recovery", len(replayed))
    return replayed


# ── Cleanup ───────────────────────────────────────────────────────────────────


async def cleanup_agent(agent_id: str) -> None:
    """Clean up all state for an agent (heartbeat, checkpoints, counters).

    Called when an agent is permanently shut down.
    """
    _heartbeats.pop(agent_id, None)
    _blackout_counts.pop(agent_id, None)
    _recovery_counts.pop(agent_id, None)

    # Remove checkpoint files
    d = _resolve_checkpoint_dir()
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    prefix = f"checkpoint_{safe_id}_"
    try:
        for fname in os.listdir(d):
            if fname.startswith(prefix) and fname.endswith(".json"):
                try:
                    os.remove(os.path.join(d, fname))
                except OSError:
                    pass
    except OSError:
        pass

    logger.info("[self_restart] Cleaned up state for %s", agent_id)
