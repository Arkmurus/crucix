r"""C-number reservation log for the Cure defect register (R-F3878).

WHY THIS EXISTS. §2 abolished R-number collisions by making the number claimable
only through a registry — "Don't claim a number by writing it in a comment; claim it
via the registry" — after 9 collisions in 50h. C-numbers never got the same
treatment: a C-number was claimed by *writing a heading into `docs/cure/defects.md`*,
which is exactly the mechanism §2 abolished.

So it went on colliding, unnoticed, four times. Measured in the live register
2026-08-11 — 29 canonical claims under 25 distinct numbers:

    C-18 · The primary search backend served NOISE as success
    C-18 · Node/JS tier security audit — 10 findings
    C-19 · Noise reached a CUSTOMER-FACING DD report
    C-19 · The C-18 XSS residual, measured and gated
    C-22 · SearXNG's surviving engine was serving a soft-404 page
    C-22 · Deep review of C-19..C-21 — one more live XSS
    C-23 · Task-list closeout — five residuals fixed
    C-23 · Two blind spots in the search stack

And the damage compounds, because **the register's own cross-references are already
broken**: "the C-18 XSS residual" names one of two unrelated C-18s, and "Deep review
of C-19..C-21" is a range over numbers that are themselves ambiguous. A defect
register whose identifiers cannot be cited has lost the property that makes it a
register — and §26 makes this file the binding record of what may be worked on.

────────────────────────────────────────────────────────────────────────────────
THE LESSON INHERITED RATHER THAN RE-LEARNED (R-F3248)

    "The file is not the only record, and it is the losable one."

For R-numbers the un-clobberable second record is git. For C-numbers it is THE
REGISTER ITSELF: 25 numbers are claimed in `defects.md` and have never appeared in
any JSON ledger. An allocator reading only its own ledger would confidently hand out
`C-01` and collide with all of them — R-F3248's defect, written fresh, in the module
built to prevent collisions. So allocation is the union of TWO sources:

    register headings  ∪  reservation ledger

and an UNREADABLE register refuses to allocate rather than guessing. That asymmetry
is deliberate: `r_number_registry` fails OPEN when git is unreadable because it must
never hang a deploy, but nothing here is on a deploy path, and issuing a number that
is already taken is the exact failure this module exists to prevent (§22 — an
absence must never be read as a measurement).

GIT IS **NOT** A THIRD SOURCE HERE, AND THE REASON IS MEASURED. The obvious move is
to copy R-F3248 exactly and scan commit messages. It was implemented, and it pushed
the allocator from `C-26` to **`C-296`** — because `\bC-\d+\b` is not a defect
reference, it is a common real-world token. The two matches, both genuine text in
this repo's history:

    "aircraft (Super Tucano, Gripen, FA-50, C-295)"   ← the Airbus C-295 transport
    "Closes the last C-3 false positive"              ← an unrelated internal gate

`R-F####` is a distinctive coined token, so scanning history for it is safe. `C-`
is a bigram that appears in aircraft designations, standards, part numbers and
prose. A source that inflates the next number by 270 does not fail safe: it makes
`peek` contradict `audit` — the precise "a peek that promises a number reserve would
refuse is a lie the caller acts on" failure R-F3248 names — and hands out an absurd
number, which is how a tool gets bypassed and the collisions resume.

It is also unnecessary. The register is itself a git-tracked, durable artifact, so
every legitimately claimed number is already in source #1; a number mentioned in a
commit whose heading never landed is precisely the drift `audit()` reports. Do not
re-add a git scan here without a prefix that cannot collide with ordinary English.

WHAT IS DELIBERATELY NOT A CLAIM. `C-11a`, `C-14b`, `C-18b`, `C-19-orig` and
`C-22 POSTSCRIPT` continue an existing entry. Counting them would report five false
collisions, and a gate that cries wolf is a gate that gets muted (R-F3858).

THE WRITE PRIMITIVES ARE IMPORTED, NOT COPIED. `_file_lock`, `_save_atomic` and
`_utcnow_iso` carry fixes bought at real cost — a per-PID temp file, a Windows
`PermissionError` retry, a cross-process lock that fails open rather than hanging,
and read-back verification (R-F1026 / R-F3187 / R-F3200). Re-implementing them here
would fork those lessons and let the two registries drift. Extracting them into a
shared module would be the tidier shape, but §26 forbids refactoring inside a fix PR
and `r_number_registry` is load-bearing — so they are imported as-is, and both
registries stay fixed by one fix.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

# R-F3878 — see the module docstring: imported so the R-F1026/R-F3187/R-F3200
# concurrency fixes cannot drift between the two registries.
from .r_number_registry import (
    RegistryWriteError,
    _file_lock,
    _save_atomic,
    _utcnow_iso,
)

logger = logging.getLogger("aria.c_number_registry")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESERVATIONS_PATH = _REPO_ROOT / "data" / "c_number_reservations.json"
_REGISTER_PATH = _REPO_ROOT / "docs" / "cure" / "defects.md"
_LOCK = threading.Lock()

#: THE CLAIM FORM. `### C-NN ·` — the middot separator is what distinguishes a new
#: claim from a continuation (`### C-14b`, `### C-22 POSTSCRIPT`, `### C-19-orig`).
#: Measured against all 34 headings in the live register: 29 canonical, 5 variants.
_CLAIM_RE = re.compile(r"^###\s+C-(\d{1,4})\s+·\s*(.*)$", re.MULTILINE)
_C_NUMBER_RE = re.compile(r"^C-(\d{1,4})$")

_CLAIM_ATTEMPTS = int(os.getenv("ARIA_CNUM_CLAIM_ATTEMPTS", "4"))

#: THE COLLISIONS THAT ALREADY EXIST, baselined so the gate can be turned on TODAY
#: instead of after someone renumbers four entries and breaks every citation to them.
#: SHRINK-ONLY — the same contract as `KNOWN_DEAD_CALLS` in scripts/pre-commit. A
#: THIRD claim on C-18 still fails, so this is a record of debt, never an amnesty.
#: Do not add to it: a new entry here means the allocator was bypassed.
LEGACY_COLLISIONS: dict[int, int] = {18: 2, 19: 2, 22: 2, 23: 2}


class RegisterUnreadableError(RuntimeError):
    """The defect register could not be read, so its claims are invisible.

    Raised instead of allocating. Most of the taken numbers live ONLY in that file;
    handing one out because we could not read it is the collision this module exists
    to prevent, and it would look exactly like a successful allocation.
    """


def format_c(n: int) -> str:
    """`C-01`, matching how the register already spells its first nine entries.

    An allocator emitting `C-1` would create a second spelling of one identifier —
    a collision that greps as two distinct things.
    """
    return f"C-{n:02d}"


def claims_in_register(
    register: Path | None = None,
) -> tuple[dict[int, list[str]], bool]:
    """Every C-number CLAIMED by a canonical heading → its title(s), plus a readable flag.

    The boolean is the honest answer to "did the scan actually run?" — an absent file
    and a register with no claims both yield `{}`, and only this flag tells them
    apart. Callers that would otherwise treat silence as proof that nothing is taken
    depend on it (§22).

    Returns a LIST of titles per number so the same parse serves both allocation and
    collision detection. One parser, two consumers, deliberately: R-F3248 records
    that two parsers "would eventually disagree about which numbers are taken, which
    is the bug this whole module exists to stop".
    """
    p = register or _REGISTER_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("[R-F3878] cannot read defect register %s: %s", p, exc)
        return {}, False
    out: dict[int, list[str]] = {}
    for m in _CLAIM_RE.finditer(text):
        out.setdefault(int(m.group(1)), []).append(m.group(2).strip())
    return out, True


def new_collisions(claims: dict[int, list[str]]) -> dict[int, int]:
    """Collisions BEYOND the baselined legacy set — i.e. the ones a gate must fail on.

    A number claimed more times than `LEGACY_COLLISIONS` allows is a genuinely new
    collision, so baselining can never become a permanent amnesty for that number.
    """
    return {
        n: len(titles)
        for n, titles in claims.items()
        if len(titles) > max(1, LEGACY_COLLISIONS.get(n, 1))
    }


def _load(path: Path | None = None) -> dict[str, Any]:
    p = path or _RESERVATIONS_PATH
    if not p.exists():
        return {"schema_version": 1, "reservations": []}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _ledger_numbers(data: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for r in data.get("reservations", []):
        if not isinstance(r, dict):
            continue
        m = _C_NUMBER_RE.match(str(r.get("c_number") or ""))
        if m:
            out.add(int(m.group(1)))
    return out


def _taken(data: dict[str, Any], register: Path | None) -> set[int]:
    """The union of every source that could already hold a number.

    Raises `RegisterUnreadableError` when the register cannot be read — see the
    class docstring for why that is not fail-open here.
    """
    claims, readable = claims_in_register(register)
    if not readable:
        raise RegisterUnreadableError(
            f"cannot read {register or _REGISTER_PATH} — most claimed C-numbers exist "
            f"ONLY in that file, so any number allocated now may already be taken. "
            f"NOTHING was claimed."
        )
    return set(claims) | _ledger_numbers(data)


def _next_free(taken: set[int]) -> int:
    """MONOTONIC — one past the highest number ever seen. Never fills a gap.

    A gap is not evidence that a number is free. It can mean a heading was renamed
    or deleted, an entry was superseded, a claim was abandoned, or a number was
    burned in a commit subject whose heading never landed. Reissuing it would
    collide with whatever still cites it — and the register's citations are exactly
    what the four existing collisions already broke.

    This mirrors `r_number_registry`, which allocates from `next_available` and only
    ever increments; it has never reused a hole in ~3,300 numbers. An integer is the
    cheapest thing here, and a rename pass across a register that cites itself by
    number is the most expensive.
    """
    return (max(taken) + 1) if taken else 1


def peek_next(
    *,
    path: Path | None = None,
    register: Path | None = None,
) -> str:
    """The C-number `reserve()` would hand out, without claiming it.

    Applies the IDENTICAL skip set as `reserve`. R-F3248 records why: a peek that
    promises a number reserve would refuse is a lie the caller acts on — it goes
    into a heading and a commit subject before the claim is ever made.
    """
    data = _load(path)
    return format_c(_next_free(_taken(data, register)))


def _claim_is_recorded(c_number: str, claimed_at: str, path: Path | None) -> bool:
    """R-F3187 — is OUR entry actually on disk? Matched on `claimed_at` too, so a
    concurrent writer that took the same number for different work reads as absent
    rather than passing on the number alone."""
    try:
        data = _load(path)
    except Exception as exc:
        logger.warning("[R-F3878] could not re-read the ledger to verify: %s", exc)
        return False
    return any(
        isinstance(r, dict)
        and r.get("c_number") == c_number
        and r.get("claimed_at") == claimed_at
        for r in data.get("reservations", [])
    )


def reserve(
    title: str,
    agent: str = "claude",
    notes: str = "",
    *,
    path: Path | None = None,
    register: Path | None = None,
) -> str:
    """Claim the next available C-number atomically. Returns e.g. ``"C-26"``.

    Claim BEFORE writing the heading — that gap is precisely where C-18, C-19, C-22
    and C-23 each collided.
    """
    if not title or not title.strip():
        raise ValueError("title required")

    c_num = ""
    claimed_at = _utcnow_iso()
    for attempt in range(_CLAIM_ATTEMPTS):
        with _LOCK, _file_lock(path or _RESERVATIONS_PATH):
            data = _load(path)
            taken = _taken(data, register)
            c_num = format_c(_next_free(taken))
            claimed_at = _utcnow_iso()
            data.setdefault("reservations", []).append({
                "c_number": c_num,
                "title": title.strip(),
                "claimed_at": claimed_at,
                "claimed_by": agent,
                "status": "open",
                "r_numbers": [],
                "notes": notes.strip() or None,
            })
            _save_atomic(data, path or _RESERVATIONS_PATH)

        # Verify OUTSIDE the lock, to see what any concurrent writer left behind.
        if _claim_is_recorded(c_num, claimed_at, path):
            logger.info("c_number_reserved: %s by=%s title=%s", c_num, agent, title)
            _wire_ok(f"reserved {c_num}")
            return c_num
        logger.warning(
            "[R-F3878] claim %s did not survive a concurrent write (attempt %d/%d) — "
            "re-allocating", c_num, attempt + 1, _CLAIM_ATTEMPTS)

    _wire_fail(f"could not record a C-number for {title.strip()[:60]!r}")
    raise RegistryWriteError(
        f"could not record a reservation for {title.strip()[:60]!r} after "
        f"{_CLAIM_ATTEMPTS} attempts — a concurrent writer keeps clobbering the "
        f"ledger. NOTHING was claimed; do not use the last number returned."
    )


def mark_closed(
    c_number: str,
    r_numbers: list[str],
    *,
    path: Path | None = None,
) -> None:
    """Close a C-number against the R-numbers that fixed it.

    That link is the only thing connecting the defect register to shipped code, and
    §26 makes the register the record of what may be worked on at all.
    """
    if not _C_NUMBER_RE.match(c_number):
        raise ValueError(f"invalid c_number: {c_number}")
    with _LOCK, _file_lock(path or _RESERVATIONS_PATH):
        data = _load(path)
        entry = next(
            (r for r in data.get("reservations", [])
             if isinstance(r, dict) and r.get("c_number") == c_number),
            None,
        )
        if entry is None:
            raise KeyError(f"c_number not reserved: {c_number}")
        entry["status"] = "closed"
        entry["r_numbers"] = list(r_numbers)
        entry["closed_at"] = _utcnow_iso()
        _save_atomic(data, path or _RESERVATIONS_PATH)
    logger.info("c_number_closed: %s r_numbers=%s", c_number, r_numbers)
    _wire_ok(f"closed {c_number}")


def backfill_from_register(
    *, path: Path | None = None, register: Path | None = None,
) -> dict[str, Any]:
    """Import the 25 numbers that were claimed by writing a heading, ONCE.

    WHY THIS IS NOT OPTIONAL. Without it, `audit` reports 25 "in the register but
    never reserved" on every run, forever — and a signal that always fires is a
    signal nobody reads, so the 26th (the real drift, the next collision forming)
    would arrive inside noise. The same reasoning that makes the R-F3873 block alert
    fire on the TRANSITION rather than on every query.

    HONESTY ABOUT WHAT THESE ROWS ARE. They are not reservations and must never
    claim to be: nobody reserved them, which is the whole defect. Each is stamped
    `claimed_by: "backfill:register"` with `claimed_at: None`, so a reader can always
    tell a real claim from an imported heading. Fabricating a timestamp would put
    invented data into the one log that exists to be trustworthy.

    Idempotent — a number already in the ledger is left exactly as it is.
    """
    claims, readable = claims_in_register(register)
    if not readable:
        raise RegisterUnreadableError(
            f"cannot read {register or _REGISTER_PATH} — nothing to backfill from")
    added: list[str] = []
    with _LOCK, _file_lock(path or _RESERVATIONS_PATH):
        data = _load(path)
        have = _ledger_numbers(data)
        for num in sorted(set(claims) - have):
            titles = claims[num]
            data.setdefault("reservations", []).append({
                "c_number": format_c(num),
                "title": titles[0],
                # A claim that was never made has no honest timestamp.
                "claimed_at": None,
                "claimed_by": "backfill:register",
                "status": "closed" if "CLOSED" in titles[0].upper() else "open",
                "r_numbers": [],
                "notes": (
                    "imported from a defects.md heading (R-F3878); never reserved"
                    + (f" — COLLIDED, other claim(s): {titles[1:]}" if len(titles) > 1 else "")
                ),
            })
            added.append(format_c(num))
        if added:
            _save_atomic(data, path or _RESERVATIONS_PATH)
    logger.info("[R-F3878] backfilled %d C-number(s) from the register", len(added))
    return {"added": added, "count": len(added)}


def list_reservations(
    status_filter: str | None = None, *, path: Path | None = None,
) -> list[dict[str, Any]]:
    data = _load(path)
    rs = data.get("reservations", [])
    if status_filter:
        rs = [r for r in rs if r.get("status") == status_filter]
    return rs


def audit(
    *, path: Path | None = None, register: Path | None = None,
) -> dict[str, Any]:
    """What is wrong with the register right now: collisions, and drift.

    DRIFT IS THE LEADING INDICATOR. A heading with no reservation means someone
    claimed a number by writing it — the exact mechanism that produced all four
    collisions. Catching that is how the next one is prevented rather than recorded.
    """
    claims, readable = claims_in_register(register)
    data = _load(path)
    reserved = _ledger_numbers(data)
    collisions = {n: t for n, t in claims.items() if len(t) > 1}
    return {
        "register_readable": readable,
        "claims": len(claims),
        "collisions": {format_c(n): t for n, t in sorted(collisions.items())},
        "new_collisions": {format_c(n): c for n, c in sorted(new_collisions(claims).items())},
        "legacy_collisions": {format_c(n): c for n, c in sorted(LEGACY_COLLISIONS.items())},
        # In the register but never reserved — claimed by writing a heading.
        "unreserved": [format_c(n) for n in sorted(set(claims) - reserved)],
        # Reserved but not yet written up; normal while work is in flight.
        "unwritten": [format_c(n) for n in sorted(reserved - set(claims))],
        "next_available": format_c(_next_free(set(claims) | reserved)),
    }


# ── §21a wiring: success AND failure both reach the brain ───────────────────────

def _wire_ok(summary: str) -> None:
    try:
        from .engine_wiring import wire_success
        wire_success(module="c_number_registry", summary=summary,
                     source_id="c_number_registry:R-F3878")
    except Exception:      # pragma: no cover - bookkeeping never blocks a claim
        pass


def _wire_fail(detail: str) -> None:
    try:
        from .engine_wiring import wire_failure
        wire_failure(module="c_number_registry", detail=detail,
                     gap_type="engine_failure",
                     source="c_number_registry:reserve")
    except Exception:      # pragma: no cover
        pass
