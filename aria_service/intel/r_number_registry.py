"""R-number reservation log (R-F540).

Prevents R-number collisions when multiple sessions/agents ship in parallel.
The pattern observed 2026-05-13..05-15 was 9 collisions in 50h — every
collision required a rename pass after the fact. This module gives a
deterministic claim-before-write API.

Pattern:
    from aria_service.intel.r_number_registry import reserve, mark_shipped

    r_num = reserve("My new feature", agent="claude-session-2026-05-16")
    # ... do the work ...
    mark_shipped(r_num, commit_sha="abc1234")

Storage: data/r_number_reservations.json (git-tracked). Git itself
serialises concurrent writes — second agent to commit gets a merge
conflict, which is the correct failure mode.

In-process safety: a threading.Lock guards the read-modify-write cycle so
two coroutines in the same Python process don't race.

R-F540: foundational. Every R-number from R-F555 onward must be reserved
via this module before ship.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.r_number_registry")

_RESERVATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "r_number_reservations.json"
_LOCK = threading.Lock()
_R_NUMBER_RE = re.compile(r"^R-F(\d+)$")


def _load(path: Path | None = None) -> dict[str, Any]:
    p = path or _RESERVATIONS_PATH
    if not p.exists():
        return {"schema_version": 1, "next_available": 555, "reservations": []}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_atomic(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or _RESERVATIONS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, p)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# R-F1026 — cross-process lock for the read-modify-write critical section.
# The threading.Lock above only serialises threads in ONE process; ARIA's coder
# and Claude (and concurrent tasks) reserve from SEPARATE processes, which raced
# and produced duplicate reservations. This atomic lock-file serialises across
# processes. CRITICAL: it MUST NEVER hang the caller (the whole point is no
# stalls) — so it times out and proceeds WITHOUT the lock rather than block
# forever, and it steals a stale lock left by a crashed holder.
_FILE_LOCK_TIMEOUT_S = float(os.getenv("ARIA_RNUM_LOCK_TIMEOUT", "10"))
_FILE_LOCK_STALE_S = float(os.getenv("ARIA_RNUM_LOCK_STALE", "30"))


@contextmanager
def _file_lock(path: Path | None = None):
    lockp = (path or _RESERVATIONS_PATH).with_suffix(".json.lock")
    acquired = False
    start = time.monotonic()
    while True:
        try:
            fd = os.open(str(lockp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            acquired = True
            break
        except FileExistsError:
            # Steal a stale lock (crashed holder), else wait briefly.
            try:
                if time.time() - os.path.getmtime(lockp) > _FILE_LOCK_STALE_S:
                    os.unlink(lockp)
                    continue
            except OSError:
                pass
            if time.monotonic() - start > _FILE_LOCK_TIMEOUT_S:
                logger.warning(
                    "[r_number_registry] lock wait > %.0fs — proceeding WITHOUT "
                    "the file lock to avoid a stall", _FILE_LOCK_TIMEOUT_S)
                break
            time.sleep(0.05)
        except OSError as exc:
            logger.debug("[r_number_registry] lock error %s — proceeding", exc)
            break
    try:
        yield
    finally:
        if acquired:
            try:
                os.unlink(lockp)
            except OSError:
                pass


def reserve(
    title: str,
    agent: str = "claude",
    notes: str = "",
    *,
    path: Path | None = None,
) -> str:
    """Claim the next available R-number atomically.

    Returns the R-number string (e.g. "R-F563").
    """
    if not title or not title.strip():
        raise ValueError("title required")
    with _LOCK, _file_lock(path):
        data = _load(path)
        n = int(data.get("next_available", 555))
        # Defend against a partial write that left next_available trailing existing entries
        existing_nums = {
            int(_R_NUMBER_RE.match(r["r_number"]).group(1))
            for r in data.get("reservations", [])
            if _R_NUMBER_RE.match(r.get("r_number", ""))
        }
        while n in existing_nums:
            n += 1
        r_num = f"R-F{n}"
        data.setdefault("reservations", []).append({
            "r_number": r_num,
            "title": title.strip(),
            "claimed_at": _utcnow_iso(),
            "claimed_by": agent,
            "status": "in_progress",
            "commit_sha": None,
            "notes": notes.strip() or None,
        })
        data["next_available"] = n + 1
        _save_atomic(data, path)
        logger.info("r_number_reserved: %s by=%s title=%s", r_num, agent, title)
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="r_number_registry",
        summary="Reserve",
        source_id="r_number_registry:R-F1001",
    )

    return r_num


def mark_shipped(
    r_number: str,
    commit_sha: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp an R-number as shipped with the commit SHA. Idempotent."""
    if not _R_NUMBER_RE.match(r_number):
        raise ValueError(f"invalid r_number: {r_number}")
    with _LOCK, _file_lock(path):
        data = _load(path)
        for r in data.get("reservations", []):
            if r["r_number"] == r_number:
                r["status"] = "shipped"
                r["commit_sha"] = commit_sha
                r["shipped_at"] = _utcnow_iso()
                _save_atomic(data, path)
                logger.info("r_number_shipped: %s sha=%s", r_number, commit_sha)
                return
        raise KeyError(f"r_number not reserved: {r_number}")


def mark_abandoned(r_number: str, reason: str, *, path: Path | None = None) -> None:
    """Stamp an R-number as abandoned (work cancelled before ship)."""
    if not _R_NUMBER_RE.match(r_number):
        raise ValueError(f"invalid r_number: {r_number}")
    with _LOCK, _file_lock(path):
        data = _load(path)
        for r in data.get("reservations", []):
            if r["r_number"] == r_number:
                r["status"] = "abandoned"
                r["abandoned_at"] = _utcnow_iso()
                r["abandon_reason"] = reason
                _save_atomic(data, path)
                logger.info("r_number_abandoned: %s reason=%s", r_number, reason)
                return
        raise KeyError(f"r_number not reserved: {r_number}")


def list_reservations(
    status_filter: str | None = None,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return reservations, optionally filtered by status."""
    data = _load(path)
    rs = data.get("reservations", [])
    if status_filter:
        rs = [r for r in rs if r.get("status") == status_filter]
    return rs


def peek_next(*, path: Path | None = None) -> str:
    """Return the next R-number that would be assigned, without claiming."""
    data = _load(path)
    n = int(data.get("next_available", 555))
    existing_nums = {
        int(_R_NUMBER_RE.match(r["r_number"]).group(1))
        for r in data.get("reservations", [])
        if _R_NUMBER_RE.match(r.get("r_number", ""))
    }
    while n in existing_nums:
        n += 1
    return f"R-F{n}"

# R-F2119 §21a — wire failure handler for r_number_registry
try:
    wire_failure(module="r_number_registry", detail="module shutdown",
                gap_type="engine_failure", source="r_number_registry:shutdown")
except Exception:
    pass
