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


class RegistryWriteError(RuntimeError):
    """R-F3187 — a registry write could not be proven to have landed.

    Raised instead of returning an R-number that was never recorded. §2 makes this
    file the claim of record; a claim nobody can read back is not a claim.
    """


# R-F3187 — write-retry budget. Contention here is milliseconds (another agent's
# save, an editor, a virus scanner), so a short bounded retry converts the common
# Windows PermissionError from a lost claim into a successful one.
_SAVE_RETRIES = int(os.getenv("ARIA_RNUM_SAVE_RETRIES", "5"))
_SAVE_RETRY_SLEEP_S = float(os.getenv("ARIA_RNUM_SAVE_RETRY_SLEEP", "0.08"))
# How many times to re-allocate when a concurrent writer clobbered our entry.
_CLAIM_ATTEMPTS = int(os.getenv("ARIA_RNUM_CLAIM_ATTEMPTS", "4"))


#: R-F3899 — git env vars that OVERRIDE `cwd` and must not leak into our scans.
#: GIT_DIR alone is enough to redirect `git log` to another repository entirely.
_GIT_ENV_OVERRIDES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _env_without_git_overrides() -> dict[str, str]:
    """The current environment minus the vars that would override `cwd`.

    Every hook git runs (pre-commit, pre-push, post-checkout) exports GIT_DIR and
    GIT_WORK_TREE, so anything invoked from one inherits them. Stripping them makes
    `cwd` authoritative again, which is what every caller already assumes.
    """
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_OVERRIDES}


def _repo_root_for(path: Path | None) -> Path:
    """R-F3248 — the repo whose history governs THIS registry file.

    Derived from the registry path rather than always from the module's own location,
    so a registry that lives somewhere else (every test's tmp file, any tool pointed
    at a copy) is never allocated against the real repo's history — it would skip
    ~2700 live R-numbers and hand back nonsense.
    """
    return (path or _RESERVATIONS_PATH).resolve().parents[1]


def _load(path: Path | None = None) -> dict[str, Any]:
    p = path or _RESERVATIONS_PATH
    if not p.exists():
        return {"schema_version": 1, "next_available": 555, "reservations": []}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_atomic(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or _RESERVATIONS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    # R-F3187 — the temp file must be UNIQUE PER PROCESS. `.json.tmp` was a single
    # shared name, so two agents saving at once wrote the SAME scratch file and each
    # replaced the registry with the other's half-serialised bytes.
    tmp = p.with_suffix(f".json.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # R-F3187 — os.replace raises PermissionError (WinError 5) on Windows when
        # another process holds the destination open, which is routine here: two
        # agents, an editor, and a virus scanner all touch this file. OBSERVED LIVE
        # 2026-07-26: `reserve` died with
        #   PermissionError: [WinError 5] Access is denied:
        #   'data\\r_number_reservations.json.tmp' -> 'data\\r_number_reservations.json'
        # The claim was simply lost. Retry briefly — the contention is measured in
        # milliseconds — then surface it rather than pretending the write happened.
        _last: Exception | None = None
        for _attempt in range(_SAVE_RETRIES):
            try:
                os.replace(tmp, p)
                return
            except PermissionError as exc:      # Windows: destination briefly locked
                _last = exc
                time.sleep(_SAVE_RETRY_SLEEP_S * (_attempt + 1))
        raise RegistryWriteError(
            f"could not replace {p.name} after {_SAVE_RETRIES} attempts: {_last}"
        ) from _last
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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


def _claim_is_recorded(
    r_number: str, claimed_at: str, path: Path | None = None
) -> bool:
    """R-F3187 — is OUR entry actually in the file on disk?

    Matches on `claimed_at` as well as the number: if a concurrent writer took the
    same R-number for different work (the live R-F3183 collision), the number alone
    would read back as present and the check would pass while our claim was gone.
    """
    try:
        data = _load(path)
    except Exception as exc:                      # unreadable == unverified
        logger.warning("[R-F3187] could not re-read the registry to verify: %s", exc)
        return False
    for r in data.get("reservations", []):
        if not isinstance(r, dict):
            continue
        if r.get("r_number") == r_number and r.get("claimed_at") == claimed_at:
            return True
    return False


def reserve(
    title: str,
    agent: str = "claude",
    notes: str = "",
    *,
    path: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    """Claim the next available R-number atomically.

    Returns the R-number string (e.g. "R-F563").
    """
    if not title or not title.strip():
        raise ValueError("title required")

    # ── R-F3187 — A CLAIM IS NOT A CLAIM UNTIL IT READS BACK ────────────────
    #
    # This function used to return `r_num` the moment `_save_atomic` returned,
    # never checking that the entry survived. Every fail-open path in `_file_lock`
    # therefore lost claims SILENTLY, and the caller went on to write that number
    # into code and commit messages.
    #
    # THREE CASUALTIES IN ONE DAY (2026-07-26), all from this:
    #   * R-F3133 / R-F3134 / R-F3135 — reserved, used, shipped, and ABSENT from
    #     the registry afterwards; recovered only because a peer agent's unrelated
    #     message prompted a look.
    #   * R-F3183 — issued TWICE, 8 minutes apart, to two agents. The later write
    #     clobbered the earlier entry, and a subsequent ship-mark then stamped the
    #     wrong work with the wrong SHA.
    #   * a CleanHead stash pop conflicted on this file mid-deploy.
    #
    # `_file_lock` (R-F1026) is deliberately FAIL-OPEN — it proceeds without the
    # lock on timeout (10s), on ANY OSError, and it steals a "stale" lock after 30s
    # from a holder that may still be alive. That is a defensible choice: this must
    # never hang a deploy. But fail-open writing with no verification is what turns
    # a rare race into silent data loss.
    #
    # So: keep the lock non-blocking, and VERIFY. Re-read the file after saving and
    # confirm our own entry is there. If a concurrent writer clobbered it, allocate
    # again from the file as it now stands (which also picks up their number, so we
    # cannot collide with it). Raise rather than hand back a number that is not
    # recorded — a loud failure the caller can retry beats a number that quietly
    # belongs to someone else.
    #
    # ── R-F3248 — THE FILE IS NOT THE ONLY RECORD, AND IT IS THE LOSABLE ONE ──
    #
    # R-F3187 (above) made a claim verify that it LANDED. It could not make the
    # allocator notice a number that was never in the file to begin with, because
    # the skip loop below was built purely from this file's own entries. Result, on
    # 2026-07-27: THREE collisions in one session — R-F3237, R-F3243 and R-F3245
    # each issued to a second agent while a commit subject already claimed them.
    # R-F3243 landed in two separate commits (`cb061cfe` and `d98c2063`), and the
    # registry entry ended up carrying one agent's title against the other's SHA.
    #
    # Git is the record that cannot be clobbered. Scan it ONCE, here, and union it
    # into the skip set. Deliberately OUTSIDE the lock: this repo is 4k+ commits and
    # a ~1.5s scan held inside a fail-open lock would make contention worse, which is
    # the failure mode `_file_lock` was written to avoid.
    git_nums, git_readable = r_numbers_known_to_git(
        repo_root=repo_root or _repo_root_for(path)
    )
    if not git_readable:
        # Fail OPEN, loudly. Refusing to allocate would hang the very deploys this
        # module must never hang — but a silent degrade here is indistinguishable
        # from the guarantee holding, and that is how a false clean is born (§22).
        logger.warning(
            "[R-F3248] git history unreadable — allocating from the registry file "
            "alone. A number already burned in a commit CAN be reissued; verify by "
            "hand before shipping."
        )

    claimed_at = _utcnow_iso()
    r_num = ""
    for _attempt in range(_CLAIM_ATTEMPTS):
        with _LOCK, _file_lock(path):
            data = _load(path)
            n = int(data.get("next_available", 555))
            # Defend against a partial write that left next_available trailing existing entries
            existing_nums = {
                int(_R_NUMBER_RE.match(r["r_number"]).group(1))
                for r in data.get("reservations", [])
                if _R_NUMBER_RE.match(r.get("r_number", ""))
            }
            while n in existing_nums or n in git_nums:   # R-F3248 — file ∪ git
                n += 1
            r_num = f"R-F{n}"
            claimed_at = _utcnow_iso()
            data.setdefault("reservations", []).append({
                "r_number": r_num,
                "title": title.strip(),
                "claimed_at": claimed_at,
                "claimed_by": agent,
                "status": "in_progress",
                "commit_sha": None,
                "notes": notes.strip() or None,
            })
            data["next_available"] = n + 1
            _save_atomic(data, path)

        # Verify OUTSIDE the lock: we want to see what any concurrent writer left.
        if _claim_is_recorded(r_num, claimed_at, path):
            logger.info("r_number_reserved: %s by=%s title=%s", r_num, agent, title)
            break
        logger.warning(
            "[R-F3187] claim %s did not survive a concurrent write (attempt %d/%d) — "
            "re-allocating", r_num, _attempt + 1, _CLAIM_ATTEMPTS,
        )
    else:
        raise RegistryWriteError(
            f"could not record a reservation for {title.strip()[:60]!r} after "
            f"{_CLAIM_ATTEMPTS} attempts — a concurrent writer keeps clobbering the "
            f"log. NOTHING was claimed; do not use the last number returned."
        )
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="r_number_registry",
        summary="Reserve",
        source_id="r_number_registry:R-F1001",
    )

    return r_num


def _status_is_recorded(
    r_number: str, status: str, path: Path | None = None, **fields: Any
) -> bool:
    """R-F3200 — did the status change actually survive to disk?

    Checks the extra fields too (commit_sha for a ship), so a stamp that landed with
    the wrong SHA — or an entry a concurrent writer replaced wholesale — reads as NOT
    recorded rather than passing on the status word alone.
    """
    try:
        data = _load(path)
    except Exception as exc:
        logger.warning("[R-F3200] could not re-read the registry to verify: %s", exc)
        return False
    for r in data.get("reservations", []):
        if not isinstance(r, dict) or r.get("r_number") != r_number:
            continue
        if r.get("status") != status:
            return False
        return all(r.get(k) == v for k, v in fields.items())
    return False


def _mark_status(
    r_number: str,
    status: str,
    updates: dict[str, Any],
    *,
    verify: dict[str, Any],
    path: Path | None = None,
) -> None:
    """R-F3200 — apply a status change and PROVE it landed.

    THE GAP THIS CLOSES. R-F3187 made `reserve()` verify its write, but
    `mark_shipped` and `mark_abandoned` were left on the identical unverified path:
    save, return, never look again. Same `_file_lock`, same fail-open behaviour
    (proceeds unlocked on timeout, on any OSError, and steals a 30s "stale" lock),
    so a concurrent writer silently drops the status change.

    This is not hypothetical. It has a MEASURED symptom in this repo: R-F3095 exists
    because 372 reservations sat `in_progress` with `commit_sha: null` while their
    code was committed and live. An unverified ship-mark that gets clobbered leaves
    exactly that residue — the registry drift someone then has to reconcile against
    git by hand.

    And a false ship-mark is worse than a missing one: it attributes work to a commit
    that does not contain it. That happened live on 2026-07-26, when a `ship R-F3183`
    stamped a colliding reservation with an unrelated commit's SHA.
    """
    last_err: Exception | None = None
    for attempt in range(_CLAIM_ATTEMPTS):
        with _LOCK, _file_lock(path):
            data = _load(path)
            entry = next(
                (r for r in data.get("reservations", [])
                 if isinstance(r, dict) and r.get("r_number") == r_number),
                None,
            )
            if entry is None:
                # Not a write failure — the number was never reserved. Surface it
                # unchanged so callers keep the existing KeyError contract.
                raise KeyError(f"r_number not reserved: {r_number}")
            entry["status"] = status
            entry.update(updates)
            try:
                _save_atomic(data, path)
            except RegistryWriteError as exc:
                last_err = exc                     # retry: contention is transient
                continue
        if _status_is_recorded(r_number, status, path, **verify):
            return
        logger.warning(
            "[R-F3200] %s -> %s did not survive a concurrent write (attempt %d/%d) — "
            "retrying", r_number, status, attempt + 1, _CLAIM_ATTEMPTS,
        )
    raise RegistryWriteError(
        f"could not record {r_number} as {status} after {_CLAIM_ATTEMPTS} attempts"
        + (f": {last_err}" if last_err else "")
        + " — the status change was NOT saved; do not treat it as recorded."
    )


def mark_shipped(
    r_number: str,
    commit_sha: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp an R-number as shipped with the commit SHA. Idempotent."""
    if not _R_NUMBER_RE.match(r_number):
        raise ValueError(f"invalid r_number: {r_number}")
    _mark_status(
        r_number, "shipped",
        {"commit_sha": commit_sha, "shipped_at": _utcnow_iso()},
        verify={"commit_sha": commit_sha},   # a ship stamped with the WRONG sha is a lie
        path=path,
    )
    logger.info("r_number_shipped: %s sha=%s", r_number, commit_sha)


def mark_abandoned(r_number: str, reason: str, *, path: Path | None = None) -> None:
    """Stamp an R-number as abandoned (work cancelled before ship)."""
    if not _R_NUMBER_RE.match(r_number):
        raise ValueError(f"invalid r_number: {r_number}")
    _mark_status(
        r_number, "abandoned",
        {"abandoned_at": _utcnow_iso(), "abandon_reason": reason},
        verify={"abandon_reason": reason},
        path=path,
    )
    logger.info("r_number_abandoned: %s reason=%s", r_number, reason)


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


def _git_log_records(
    ref: str = "HEAD", *, repo_root: Path | None = None,
) -> tuple[list[tuple[str, str, str]], bool]:
    """R-F3248 — ``(records, readable)``, each record ``(short_sha, subject, body)``.

    Split out of `scan_shipped_r_numbers` so ALLOCATION and RECONCILIATION read the
    same history through the same parser — two parsers would eventually disagree
    about which numbers are taken, which is the bug this whole module exists to stop.

    The boolean is the honest answer to "did the scan actually run?". A dead git and
    a repo with no matching commits both yield an empty list, and only this flag
    tells them apart; a caller that fails open has to be able to SAY it failed open
    rather than treat silence as proof that nothing is taken.
    """
    root = repo_root or _RESERVATIONS_PATH.resolve().parents[1]
    try:
        out = subprocess.run(
            ["git", "log", ref, "--format=%h%x1f%s%x1f%b%x1e"],
            cwd=str(root), capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
            # R-F3899 — GIT_DIR OVERRIDES cwd, SO THE SCAN COULD READ THE WRONG REPO.
            # git sets GIT_DIR/GIT_WORK_TREE for every hook it runs, and any process
            # that inherits them makes this `cwd=root` decorative: git reads the
            # ambient repo instead. R-F3248 built `_repo_root_for` precisely so "the
            # repo whose history governs THIS registry file" decides allocation —
            # ambient env silently defeated it.
            #
            # MEASURED: exporting GIT_DIR makes 4 of the 12 r_number_registry tests
            # fail, because a tmp-path registry starts skipping the real repo's ~3,300
            # numbers. Live consequence: `reserve()` called from inside a git hook (a
            # pre-push verifier, ARIA's autonomous coder under a hook) allocates
            # against whichever repo the environment names, which is exactly the
            # collision this module exists to prevent — and it fails toward
            # over-skipping, so it looks like nothing is wrong.
            env=_env_without_git_overrides(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[R-F3095] cannot read git history: %s", exc)
        return [], False
    if out.returncode != 0:
        logger.warning("[R-F3095] git log failed for ref %r: %s", ref, (out.stderr or "")[:200])
        return [], False

    records: list[tuple[str, str, str]] = []
    for record in (out.stdout or "").split("\x1e"):
        parts = record.split("\x1f")
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        if not sha:
            continue
        records.append((sha, parts[1], parts[2] if len(parts) > 2 else ""))
    return records, True


# R-F3248 — allocation now consults git, and this repo is 4k+ commits (~1.5s per
# scan). Cache per (root, ref) so a peek+reserve pair, or a batch of claims in one
# process, pays for one scan. The TTL is deliberately short: a long-lived process
# must never allocate against an hour-old view of history.
_GIT_SCAN_TTL_S = float(os.getenv("ARIA_RNUM_GIT_SCAN_TTL", "30"))
_git_scan_cache: dict[tuple[str, str], tuple[float, set[int], bool]] = {}


def _published_reservations(
    *, ref: str = "origin/main", repo_root: Path | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """The ledger as PUBLISHED at `ref`. Returns (data, readable).

    R-F4077 — read-only; never fetches. A stale `origin/main` only makes the
    check more conservative (it reports more claims as unpublished), which is
    the safe direction for a hazard warning.
    """
    root = Path(repo_root).resolve() if repo_root else _RESERVATIONS_PATH.resolve().parents[1]
    rel = "data/r_number_reservations.json"
    try:
        # R-F4077 — BYTES, not `text=True`. `text=True` decodes with the
        # platform default, which is cp1252 on Windows; this ledger is UTF-8 and
        # contains an em-dash, so the decode raised and `stdout` came back None.
        # Same trap that has bitten every ad-hoc read of these files.
        out = subprocess.run(
            ["git", "show", f"{ref}:{rel}"],
            cwd=str(root), capture_output=True, timeout=20,
            env=_env_without_git_overrides(),
        )
        if out.returncode != 0 or not out.stdout:
            return None, False
        return json.loads(out.stdout.decode("utf-8", errors="replace")), True
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None, False


def unpublished_claims(
    *, path: Path | None = None, ref: str = "origin/main",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """R-numbers reserved locally but ABSENT from the published ledger.

    R-F4077 (C-127) — WHY THIS EXISTS. `reserve()` already unions the ledger with
    every number git has seen (R-F3248), and `expand_r_numbers` handles ranges.
    But git can only know a claim that has been COMMITTED, so two agents sharing
    a tree can each allocate the same number inside the reserve-to-commit window
    and the ledger merge then keeps only one. That happened on 2026-08-16:
    R-F4061/R-F4062 were issued twice and the loser's code referenced numbers
    whose ledger titles described someone else's work.

    WHY NOT COMPARE TITLES. Measured on this repo, HUNDREDS of entries differ in
    title between local and `origin/main` from ordinary edits and reconciliation.
    A guard that fires hundreds of times is one nobody reads. Absence is the
    quiet, precise signal — normally 0-2 entries, and it is exactly the set a
    merge can lose.

    `unpublished` is None when the published ledger cannot be read: "could not
    measure" must never render as "measured and found nothing" (§1).
    """
    data = _load(path)
    local = [
        r.get("r_number") for r in data.get("reservations", [])
        if r.get("r_number")
    ]
    published, readable = _published_reservations(ref=ref, repo_root=repo_root)
    if not readable or not isinstance(published, dict):
        return {"readable": False, "unpublished": None, "displaced": None,
                "local_total": len(local)}

    pub = {
        r.get("r_number"): r for r in published.get("reservations", [])
        if r.get("r_number")
    }

    # R-F4077 — DISPLACEMENT: the case absence cannot see.
    #
    # Proven live on 2026-08-16 by this fix itself: I committed R-F4076 for
    # C-127 while a peer published R-F4076 for a C-113 follow-up. The number WAS
    # present upstream, so an absence check reported "all published" while my
    # code referenced a number it did not own. Once the number exists, absence
    # is the wrong question — identity is the right one.
    #
    # Identity is (claimed_at, claimed_by, title): the fields that describe WHOSE
    # claim it is. `status` and `commit_sha` are deliberately EXCLUDED — those
    # change on my own claim when it ships, and flagging that would make the
    # check fire on ordinary work, which is how a guard becomes noise nobody
    # reads (C-96).
    def _identity(rec: dict[str, Any]) -> tuple:
        return (rec.get("claimed_at"), rec.get("claimed_by"), rec.get("title"))

    local_by_num = {
        r.get("r_number"): r for r in data.get("reservations", [])
        if r.get("r_number")
    }
    displaced = [
        n for n, rec in local_by_num.items()
        if n in pub and _identity(pub[n]) != _identity(rec)
    ]

    return {
        "readable": True,
        "unpublished": [n for n in local if n not in pub],
        "displaced": displaced,
        "local_total": len(local),
    }


def r_numbers_known_to_git(
    ref: str = "HEAD", *, repo_root: Path | None = None,
) -> tuple[set[int], bool]:
    """R-F3248 — every R-number git has already seen, as ints, plus a readable flag.

    THE REGISTRY FILE IS NOT THE ONLY RECORD, AND IT IS THE LOSABLE ONE. `reserve()`
    allocated purely from `data/r_number_reservations.json`, so any way an entry went
    missing became a reissue: a fail-open clobber (R-F3187), a worktree holding a
    stale checkout of the file, or an agent that committed a number it never reserved.
    Git cannot be clobbered and a commit subject is a permanent public claim, so it is
    the correct second opinion.

    SUBJECT ∪ BODY — deliberately STRICTER than `reconcile_with_git`, and the
    asymmetry is the point. Reconcile refuses to ship-mark on a body-only mention
    because writing a false ship record corrupts the audit trail. Allocation has the
    opposite cost function: skipping a number that was merely mentioned costs ONE
    unused integer, while issuing a number someone else is already using costs a
    rename pass across code, commit messages and this log. So: mentioned anywhere in
    history == not available.
    """
    # Resolve before keying: two spellings of the same root must share one cache
    # entry, or a batch of claims re-scans 4k commits per spelling.
    root = Path(repo_root).resolve() if repo_root else _RESERVATIONS_PATH.resolve().parents[1]
    key = (str(root), ref)
    now = time.monotonic()
    cached = _git_scan_cache.get(key)
    if cached is not None and (now - cached[0]) < _GIT_SCAN_TTL_S:
        return set(cached[1]), cached[2]

    records, readable = _git_log_records(ref, repo_root=root)
    nums: set[int] = set()
    for _sha, subject, body in records:
        for token in expand_r_numbers(subject) | expand_r_numbers(body):
            m = _R_NUMBER_RE.match(token)
            if m:
                nums.add(int(m.group(1)))
    _git_scan_cache[key] = (now, set(nums), readable)
    return nums, readable


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
    records, _readable = _git_log_records(ref, repo_root=repo_root)
    by_subject: dict[str, str] = {}
    by_body: dict[str, str] = {}
    for sha, subject, body in records:
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


def stale_reservations(
    max_age_days: int = 14,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return unresolved reservations older than ``max_age_days``.

    This is deliberately read-only. Age is evidence that a claim needs review,
    never evidence that its engineering work shipped or can be abandoned.
    Unparseable timestamps are included with ``age_days=None`` so malformed
    ledger state fails visible instead of disappearing from the audit.
    """
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    stale: list[dict[str, Any]] = []
    for reservation in list_reservations(status_filter="in_progress", path=path):
        claimed_raw = str(reservation.get("claimed_at") or "").strip()
        age_days: int | None = None
        try:
            claimed_at = datetime.fromisoformat(claimed_raw.replace("Z", "+00:00"))
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            age_days = max(0, (observed_at - claimed_at).days)
        except (TypeError, ValueError):
            pass
        if age_days is None or age_days > max_age_days:
            stale.append({
                "r_number": reservation.get("r_number"),
                "title": reservation.get("title"),
                "claimed_at": claimed_raw or None,
                "claimed_by": reservation.get("claimed_by"),
                "age_days": age_days,
            })
    return sorted(
        stale,
        key=lambda item: (
            item["age_days"] is not None,
            -(item["age_days"] or 0),
            str(item["r_number"]),
        ),
    )


def peek_next(*, path: Path | None = None, repo_root: Path | None = None) -> str:
    """Return the next R-number that would be assigned, without claiming.

    R-F3248 — applies the SAME file ∪ git skip set as `reserve()`. A peek that
    promises a number `reserve` would refuse is a lie the caller acts on: it goes
    into a branch name and a commit subject before the claim is ever made.
    """
    data = _load(path)
    n = int(data.get("next_available", 555))
    existing_nums = {
        int(_R_NUMBER_RE.match(r["r_number"]).group(1))
        for r in data.get("reservations", [])
        if _R_NUMBER_RE.match(r.get("r_number", ""))
    }
    git_nums, _readable = r_numbers_known_to_git(
        repo_root=repo_root or _repo_root_for(path)
    )
    while n in existing_nums or n in git_nums:
        n += 1
    return f"R-F{n}"

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
