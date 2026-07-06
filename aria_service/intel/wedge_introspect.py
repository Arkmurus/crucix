"""wedge_introspect — read + summarise the R-F704 wedge stack files
(R-F783, 2026-05-21).

Why this module exists
──────────────────────
R-F704 writes a full Python traceback to `/data/wedge_stacks/wedge_<pid>_
<epoch>.log` every time the event-loop heartbeat stalls beyond
`_STALL_WARN_THRESHOLD_S`. The 360-diagnosis audit (2026-05-21) found
that those files were write-only — ARIA had no surface that read them
back, so she couldn't tell you which function was wedging her even
when 35+ wedge files were sitting on the volume.

This module closes that loop:
  - `list_recent_wedges()` enumerates the wedge dir, sorted by mtime.
  - `read_wedge()` returns the raw content of one file (path-traversal
    guarded).
  - `summarise_wedges()` parses the most-recent N files and aggregates
    the top culprit frames across them, so the operator (or an
    autonomous task) can see "top culprit last 24h = X" at a glance.

The culprit extraction is heuristic: it scans every thread's stack in
a dump and returns the deepest frame inside `/app/aria_service/` (or
the dev-mode equivalent), skipping watchdog frames. That frame is
what was on-CPU when the loop missed its heartbeat — almost always
the wedge source.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import os
import re
from typing import Any

logger = logging.getLogger("aria.intel.wedge_introspect")


def _resolve_wedge_dir() -> str:
    """Same resolution rules as main.py:_wedge_watchdog setup —
    prefer the fly /data volume, fall back to repo-local data/ dir
    in dev / CI."""
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data/wedge_stacks"
    repo_local = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "wedge_stacks",
    )
    return os.path.normpath(repo_local)


# Allowed filename pattern. The watchdog at main.py:380 writes files
# named `wedge_<pid>_<epoch>.log` — pid + epoch are non-negative
# integers. Reject anything else (no `..`, no absolute paths, no
# extension other than .log).
_WEDGE_NAME_RE = re.compile(r"^wedge_\d+_\d+\.log$")


def _is_valid_wedge_name(name: str) -> bool:
    return bool(_WEDGE_NAME_RE.match(name or ""))


# Frame line pattern emitted by faulthandler.dump_traceback:
#   File "/app/aria_service/intel/knowledge.py", line 848 in store_fact
_FRAME_RE = re.compile(
    r'File "(?P<path>[^"]+)", line (?P<line>\d+) in (?P<func>\S+)'
)
# Marker line for the watchdog's own thread (we don't want to count it
# as the culprit). The watchdog's "Current thread" frame always shows
# main.py:_wedge_watchdog.
_WATCHDOG_FUNC = "_wedge_watchdog"


def _is_user_frame(path: str) -> bool:
    """True if `path` is inside the project (vs. site-packages /
    stdlib). The fly container puts the project at /app/aria_service/;
    dev shells use the repo path. Match either."""
    p = path.replace("\\", "/")
    if "/aria_service/" in p:
        return True
    # Tests run from repo root; also accept repo-relative paths
    if p.startswith("aria_service/"):
        return True
    return False


def _parse_top_culprit(content: str) -> dict[str, Any]:
    """Return the most-likely culprit frame for a single wedge stack.

    Strategy: walk every line. Track stack depth per `Thread 0x...`
    block. For each block, record the FIRST user-code frame seen
    (faulthandler prints most-recent-call-first, so the first user
    frame is the deepest one currently executing). Skip the
    watchdog's own block. Among remaining blocks, pick the user
    frame from the block with the most-recently-on-CPU work — in
    practice the heaviest block is the one with the most frames,
    so we pick the user frame from the longest block. Tie-break by
    insertion order (i.e. first match).

    Returns: {file, line, func, thread_id, all_user_frames}
    or {} if no user frame found.
    """
    if not content:
        return {}

    # Split into per-thread blocks. A new block starts at a line
    # beginning with "Thread 0x" or "Current thread ".
    blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("Thread 0x") or s.startswith("Current thread"):
            if cur is not None:
                blocks.append(cur)
            cur = {"header": s, "frames": []}
            continue
        if cur is None:
            continue
        m = _FRAME_RE.search(line)
        if m:
            cur["frames"].append({
                "path": m.group("path"),
                "line": int(m.group("line")),
                "func": m.group("func"),
            })
    if cur is not None:
        blocks.append(cur)

    # Filter: drop watchdog blocks + blocks with no user frames.
    candidates: list[dict[str, Any]] = []
    for b in blocks:
        if any(f["func"] == _WATCHDOG_FUNC for f in b["frames"]):
            continue
        user_frames = [f for f in b["frames"] if _is_user_frame(f["path"])]
        if not user_frames:
            continue
        candidates.append({
            "thread": b["header"],
            "user_frames": user_frames,
            "depth": len(b["frames"]),
        })

    if not candidates:
        return {}

    # Pick the block with the deepest stack — that's the one doing
    # the most work. For "what wedged ARIA" the most actionable
    # frame is the SHALLOWEST user-code frame (root of user code in
    # the stack), not the leaf: the leaf is often a stdlib-adjacent
    # helper like `_fact_text` that runs inside a higher-level
    # operation like `_compute_matrix_sync` or `store_fact`. Group
    # by root → "the heatmap is wedging" / "the absorb chain is
    # wedging". The leaf is still surfaced for fine-grained drill-
    # down via `leaf_func`.
    #
    # R-F784 (2026-05-21) — the shallowest user frame for any
    # request-time wedge is `<module>` at main.py:1907 (the uvicorn
    # entry-point), because uvicorn → starlette → handler all sit
    # in site-packages so the call chain's user-code edges are:
    #   ... main.py:<module>  ← shallowest user (uninformative)
    #   (site-packages: uvicorn / starlette / fastapi)
    #   ... routes/aria.py:some_handler
    #   ... intel/something.py:actual_work  ← leaf user
    # The live wedge summary at /admin/wedge-stacks/summary
    # surfaced `<module>` as the top culprit (8 of 20 files) which
    # is correct-but-useless. Now: walk from shallowest toward
    # leaf, skip uninformative entry frames (`<module>`,
    # `<lambda>`, anything in `main.py`), and return the first
    # actionable user frame found. If all frames are noise we
    # fall back to the shallowest (preserves prior behaviour for
    # pure boot wedges).
    candidates.sort(key=lambda c: c["depth"], reverse=True)
    top = candidates[0]
    user_frames = top["user_frames"]
    root = _pick_actionable_frame(user_frames)
    leaf = user_frames[0]    # deepest user-code frame (most on-CPU)
    return {
        "file": root["path"],
        "line": root["line"],
        "func": root["func"],
        "leaf_func": leaf["func"],
        "leaf_file": leaf["path"],
        "leaf_line": leaf["line"],
        "thread": top["thread"],
        "user_stack": user_frames,
    }


# R-F784 — frame names / path suffixes that we treat as "boot / entry"
# noise rather than actionable culprits. `<module>` is the module-level
# code (the uvicorn entry point at main.py:1907), `<lambda>` and
# `<genexpr>` are anonymous wrappers, and `main.py` is the boot path.
# Anything matching these gets skipped in favour of a deeper user frame.
_NOISE_FRAME_FUNCS = frozenset({"<module>", "<lambda>", "<genexpr>"})


def _is_noise_frame(frame: dict[str, Any]) -> bool:
    if frame.get("func") in _NOISE_FRAME_FUNCS:
        return True
    path = (frame.get("path") or "").replace("\\", "/")
    if path.endswith("/aria_service/main.py") or path == "aria_service/main.py":
        return True
    return False


def _pick_actionable_frame(user_frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk user_frames from shallowest (-1) toward leaf (0), return
    the first non-noise frame. Used by `_parse_top_culprit` to pick
    the high-level operation frame while skipping the uninformative
    `<module>` / `main.py` entry-point frames that sit at the bottom
    of every request stack."""
    # Walk from shallowest (-1) toward leaf (0).
    for f in reversed(user_frames):
        if not _is_noise_frame(f):
            return f
    # All frames noise — return shallowest (prior behaviour).
    return user_frames[-1]


def list_recent_wedges(limit: int = 50) -> dict[str, Any]:
    """List the N most-recent wedge stack files.

    Returns:
        {
          wedge_dir: str,
          total_files: int,
          items: [
            {
              name: "wedge_675_1779352332.log",
              mtime: 1779352332.0,
              mtime_iso: "2026-05-21T08:32:12Z",
              size_bytes: 14012,
            },
            ...
          ]
        }
    Each item carries only the metadata cheap enough to gather for
    every file. The summary endpoint parses contents lazily.
    """
    limit = max(1, min(int(limit or 50), 500))
    wedge_dir = _resolve_wedge_dir()
    if not os.path.isdir(wedge_dir):
        return {"wedge_dir": wedge_dir, "total_files": 0, "items": []}

    entries: list[dict[str, Any]] = []
    for name in os.listdir(wedge_dir):
        if not _is_valid_wedge_name(name):
            continue
        full = os.path.join(wedge_dir, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        entries.append({
            "name": name,
            "mtime": st.st_mtime,
            "size_bytes": st.st_size,
        })

    entries.sort(key=lambda e: e["mtime"], reverse=True)
    total = len(entries)

    from datetime import datetime, timezone
    for e in entries[:limit]:
        e["mtime_iso"] = datetime.fromtimestamp(
            e["mtime"], tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="wedge_introspect",
        summary="List Recent Wedges",
        source_id="wedge_introspect:R-F1001",
    )

    return {
        "wedge_dir": wedge_dir,
        "total_files": total,
        "items": entries[:limit],
    }


_MAX_BYTES_HARD_CAP = 1_000_000  # 1 MB ceiling — wedge files are typically 30-80 KB


def read_wedge(name: str, max_bytes: int = 200_000) -> dict[str, Any]:
    """Return the raw content of one wedge stack file. Path-traversal
    guarded — `name` must be a bare filename matching the watchdog's
    naming pattern. Returns {} if the name is invalid or the file
    doesn't exist.

    `max_bytes` is clamped to [1, 1_000_000]: a malicious caller could
    otherwise pass `max_bytes=10**9` and force a multi-GB allocation
    on read, or `max_bytes=-1` which `file.read(-1)` interprets as
    "read everything" — neither is safe with untrusted query params."""
    if not _is_valid_wedge_name(name):
        return {}
    try:
        clamped_max = max(1, min(int(max_bytes), _MAX_BYTES_HARD_CAP))
    except (TypeError, ValueError):
        clamped_max = 200_000
    wedge_dir = _resolve_wedge_dir()
    full = os.path.join(wedge_dir, name)
    if not os.path.isfile(full):
        return {}
    try:
        st = os.stat(full)
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(clamped_max)
        truncated = st.st_size > clamped_max
    except OSError as e:
        logger.warning("read_wedge(%s) failed: %s", name, e)
        return {}

    from datetime import datetime, timezone
    return {
        "name": name,
        "size_bytes": st.st_size,
        "mtime": st.st_mtime,
        "mtime_iso": datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content": content,
        "truncated": truncated,
        "top_culprit": _parse_top_culprit(content),
    }


def summarise_wedges(hours: int = 24, max_files: int = 100) -> dict[str, Any]:
    """Aggregate culprits across the most-recent wedge files within
    the past `hours`. Caps at `max_files` to keep latency bounded
    (parsing 100 files of ~30KB each is well under 1s).

    Returns:
        {
          window_hours: int,
          files_scanned: int,
          culprits: [{func, file, count, last_seen_iso}, ...],
          top_culprit: {func, file, count, ...} | None,
        }
    """
    hours = max(1, min(int(hours or 24), 24 * 30))
    max_files = max(1, min(int(max_files or 100), 500))

    listing = list_recent_wedges(limit=max_files)
    if listing["total_files"] == 0:
        return {
            "window_hours": hours,
            "files_scanned": 0,
            "culprits": [],
            "top_culprit": None,
        }

    import time as _time
    cutoff = _time.time() - hours * 3600

    culprit_counts: dict[tuple, dict[str, Any]] = {}
    scanned = 0
    for item in listing["items"]:
        if item["mtime"] < cutoff:
            continue
        rec = read_wedge(item["name"])
        if not rec:
            continue
        scanned += 1
        culprit = rec.get("top_culprit") or {}
        if not culprit:
            continue
        key = (culprit.get("func", ""), culprit.get("file", ""))
        existing = culprit_counts.get(key)
        if existing is None:
            culprit_counts[key] = {
                "func": culprit.get("func"),
                "file": culprit.get("file"),
                "line": culprit.get("line"),
                "count": 1,
                "last_seen": item["mtime"],
            }
        else:
            existing["count"] += 1
            if item["mtime"] > existing["last_seen"]:
                existing["last_seen"] = item["mtime"]
                existing["line"] = culprit.get("line")

    culprits = sorted(
        culprit_counts.values(),
        key=lambda c: (-c["count"], -c["last_seen"]),
    )
    from datetime import datetime, timezone
    for c in culprits:
        c["last_seen_iso"] = datetime.fromtimestamp(
            c["last_seen"], tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "window_hours": hours,
        "files_scanned": scanned,
        "culprits": culprits,
        "top_culprit": culprits[0] if culprits else None,
    }

# R-F2119 §21a — wire failure handler for wedge_introspect
try:
    wire_failure(module="wedge_introspect", detail="module shutdown",
                gap_type="engine_failure", source="wedge_introspect:shutdown")
except Exception:
    pass
