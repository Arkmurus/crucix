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
import subprocess          # R-F3095 — reconcile the registry against git history
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


# ── R-F3095 — THE REGISTRY DRIFTS BECAUSE NOTHING RECONCILES IT ─────────────
#
# THE DEFECT (found 2026-07-26 by a cross-review of R-F3083). R-F3083's code was
# committed AND live, while `data/r_number_reservations.json` still recorded it as
# `in_progress` with `commit_sha: null` — and R-F3085, which BUILDS ON IT, was
# correctly ship-marked. The registry claimed a dependency shipped before its
# dependency.
#
# But R-F3083 is not the bug. A sweep of the registry found **372 `in_progress`
# entries with no SHA**, many of them long live. CLAUDE.md §2 says "Mark shipped at
# push" — and nothing anywhere enforces, checks, or even reports it. Ship-marking is
# a manual step at the end of a long task, which is exactly the step that gets
# dropped. Fixing R-F3083 by hand would have fixed one row and left the mechanism
# that produced 372 of them untouched.
#
# So: derive the truth from git, which cannot forget. A commit message carrying
# "R-F1234" IS the ship record; this reconciles the registry against it.
_R_IN_TEXT_RE = re.compile(r"\bR-F(\d{1,6})\b")

# ── R-F3100 — THE SCANNER COULD NOT READ HALF THE SHIP RECORDS ──────────────
#
# THE DEFECT. R-F3095 held 39 reservations back as "mentioned only in a commit
# BODY", for a human to judge. Reading them showed the premise was wrong for most:
# they were named in the SUBJECT, in a shorthand `\bR-F(\d+)\b` cannot see —
#
#   R-F1559/61/62/63/66/67/68/71: aria-intel brain hardening batch
#   R-F2912/2913/2914/2916/2917 — stop the Claude overspend
#   feat: R-F1099-R-F1107 — Phase 1 reading + Phase 2 registration
#
# The plain pattern matched only the FIRST number in each, so seven of eight
# subject-line ship records in one commit were misfiled as body references. 46
# commits use the slash form and 4 use a range — i.e. the "needs human judgement"
# pile was mostly a parsing gap, and hand-marking those rows would have left the
# gap in place for the next sweep.
#
# SUFFIX SEMANTICS. A suffix replaces the LAST k digits of the base, so
# `R-F1559/61` is R-F1561 (not R-F61), and every suffix is relative to the ORIGINAL
# base, not the previous expansion. A same-length suffix (`R-F2912/2913`) simply
# replaces the whole number.
_R_SLASH_ABBREV_RE = re.compile(r"\bR-F(\d{1,6})((?:/\d{1,6})+)")
_R_RANGE_RE = re.compile(r"\bR-F(\d{1,6})\s*-\s*R-F(\d{1,6})\b")
#: A range wider than this is not a batch, it is a typo or a plan document.
_R_RANGE_MAX = 30


def expand_r_numbers(text: str) -> set[str]:
    """R-F3100 — every R-number a piece of text names, including the shorthand.

    Handles the plain form, the slash abbreviation and the inclusive range. Pure and
    module-level so the expansion rules are directly testable — this decides what
    counts as a ship record, so it must not be buried in a git-reading function."""
    out: set[str] = set()
    text = text or ""
    for m in _R_IN_TEXT_RE.finditer(text):
        out.add(f"R-F{int(m.group(1))}")
    for m in _R_SLASH_ABBREV_RE.finditer(text):
        base = m.group(1)
        out.add(f"R-F{int(base)}")
        for suffix in m.group(2).lstrip("/").split("/"):
            if not suffix or len(suffix) > len(base):
                continue
            expanded = base[: len(base) - len(suffix)] + suffix
            out.add(f"R-F{int(expanded)}")
    for m in _R_RANGE_RE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi and (hi - lo) < _R_RANGE_MAX:
            for n in range(lo, hi + 1):
                out.add(f"R-F{n}")
    return out


def scan_shipped_r_numbers(
    ref: str = "HEAD", *, repo_root: Path | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """R-F3095 — map R-numbers to the SHORT SHA of the commit that shipped them.

    Returns ``(by_subject, by_body_only)``.

    THE SUBJECT LINE IS THE SHIP RECORD; THE BODY IS NOT. A first cut of this
    matched anywhere in the message and immediately produced a false positive:
    R-F3057 resolved to `68c0bf24`, whose subject is "R-F3055 + R-F3056 - adverse
    media on every surface" — it merely MENTIONS R-F3057 in the body as a forward
    reference. The commit that actually shipped it is `ceced1fc` ("R-F3056 +
    R-F3057 — the two REDs"). This repo's convention is `fix: R-F#### — title`, so a
    subject match is evidence of implementation while a body match is evidence only
    of a reference — a supersession note, a "see also", a follow-up.

    Body-only hits are returned SEPARATELY and are never auto-applied: they are
    reported for a human to judge. An automated ship-mark on a reference would
    write a false audit record into the one log that exists to be trustworthy.

    Earliest match wins (git log is newest-first, so the last write per key is the
    oldest commit). Only commits reachable from `ref` count, so unmerged branch work
    is never recorded as shipped. Returns empty maps when git is unavailable — a
    reconciler that cannot read history must report nothing, never guess.
    """
    root = repo_root or _RESERVATIONS_PATH.resolve().parents[1]
    try:
        out = subprocess.run(
            ["git", "log", ref, "--format=%h%x1f%s%x1f%b%x1e"],
            cwd=str(root), capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[R-F3095] cannot read git history: %s", exc)
        return {}, {}
    if out.returncode != 0:
        logger.warning("[R-F3095] git log failed for ref %r: %s", ref, (out.stderr or "")[:200])
        return {}, {}

    by_subject: dict[str, str] = {}
    by_body: dict[str, str] = {}
    for record in (out.stdout or "").split("\x1e"):
        parts = record.split("\x1f")
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        subject = parts[1]
        body = parts[2] if len(parts) > 2 else ""
        if not sha:
            continue
        # R-F3100 — read the shorthand too, or a batch commit's subject line looks
        # like a single ship record with N-1 stray body references.
        for num in expand_r_numbers(subject):
            by_subject[num] = sha
        for num in expand_r_numbers(body):
            by_body[num] = sha
    # A subject match always wins; drop it from the review pile.
    by_body = {k: v for k, v in by_body.items() if k not in by_subject}
    return by_subject, by_body


def reconcile_with_git(
    ref: str = "HEAD",
    *,
    apply: bool = False,
    path: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """R-F3095 — find reservations whose work is already in git but which the
    registry still calls `in_progress`, and (with `apply=True`) ship-mark them.

    DRY RUN BY DEFAULT. Only `in_progress` entries are touched: `abandoned` and
    `cancelled` are deliberate operator decisions and an R-number can legitimately
    appear in a commit that merely mentions it. `shipped` entries are left alone
    even if the SHA differs — rewriting a recorded ship SHA would destroy the audit
    trail this log exists to be.

    Only SUBJECT-line matches are applied. Body-only mentions are returned under
    `"review"` for a human to judge — see `scan_shipped_r_numbers` for why.

    Returns {"checked", "drifted", "applied", "entries": [...], "review": [...]}.
    """
    by_subject, by_body = scan_shipped_r_numbers(ref, repo_root=repo_root)
    data = _load(path)
    reservations = data.get("reservations", [])
    drifted: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for r in reservations:
        if r.get("status") != "in_progress":
            continue
        num = r.get("r_number") or ""
        title = r.get("title") or ""
        if num in by_subject:
            drifted.append({"r_number": num, "commit_sha": by_subject[num], "title": title})
        elif num in by_body:
            review.append({"r_number": num, "commit_sha": by_body[num], "title": title})

    applied = 0
    if apply and drifted:
        with _LOCK, _file_lock(path):
            data = _load(path)
            by_num = {r["r_number"]: r for r in data.get("reservations", [])}
            for d in drifted:
                r = by_num.get(d["r_number"])
                # Re-check status under the lock: another agent may have marked it
                # between the scan and the write.
                if not r or r.get("status") != "in_progress":
                    continue
                r["status"] = "shipped"
                r["commit_sha"] = d["commit_sha"]
                r["shipped_at"] = _utcnow_iso()
                r["notes"] = ((r.get("notes") or "") + " [R-F3095 reconciled from git]").strip()
                applied += 1
            if applied:
                _save_atomic(data, path)
        logger.info("[R-F3095] reconciled %s R-number(s) from git history", applied)

    return {
        "checked": len(reservations),
        "drifted": len(drifted),
        "applied": applied,
        "entries": drifted,
        "review": review,
    }


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

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
