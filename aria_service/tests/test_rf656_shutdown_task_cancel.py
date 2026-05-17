"""R-F656 — shutdown cancels all background tasks started in lifespan.

Audit 2026-05-17 found that lifespan started 14 background tasks but
the shutdown block only cancelled 10. Three tasks survived:

  - reasoning_purge_task (started line 615)
  - weekly_report_task    (started line 943)
  - watchlist_rescreen_task (started line 987)

Effect on fly.io: the task keeps running in the dying event loop until
fly's graceful-stop window expires and SIGKILL fires. Across deploys
this accumulates worker-zombie warnings and lengthens deploy latency.

Fix: add three `.cancel()` calls to the shutdown block matching the
existing pattern (null-guard then cancel).

Test pins the symptom: every `_task = asyncio.create_task(...)` that
exists in lifespan must have a matching `.cancel()` in the shutdown
half of the same function.
"""
from __future__ import annotations

import re
from pathlib import Path


_SRC = (
    Path(__file__).resolve().parent.parent / "main.py"
).read_text(encoding="utf-8")


def _split_lifespan_startup_and_shutdown(src: str) -> tuple[str, str]:
    """Lifespan is a single async function whose halves are split by
    `yield`. Returns (startup_text, shutdown_text). The shutdown text
    only includes lines AFTER `yield` and BEFORE the `# ── App ──`
    section header, so it doesn't accidentally include the lifespan
    epilogue that lives outside the function body."""
    # Find the lifespan function. There's only one yield in it, marking
    # the startup/shutdown boundary.
    m = re.search(r"async def lifespan\b", src)
    assert m, "lifespan function not found in main.py"
    body_start = m.start()
    # Find the boundary `yield` inside the function (first one after the def).
    yield_match = re.search(r"^\s+yield\s*$", src[body_start:], re.MULTILINE)
    assert yield_match, "lifespan yield not found"
    yield_idx = body_start + yield_match.start()
    # Shutdown ends at the next top-level construct (the `# ── App ──` banner).
    end_marker = re.search(r"^# ── App ──", src[yield_idx:], re.MULTILINE)
    assert end_marker, "App marker after lifespan not found"
    shutdown_end = yield_idx + end_marker.start()
    return src[body_start:yield_idx], src[yield_idx:shutdown_end]


_STARTUP, _SHUTDOWN = _split_lifespan_startup_and_shutdown(_SRC)


def _find_task_names(startup: str) -> set[str]:
    """Pull every name on the left side of `<name>_task = asyncio.create_task(...)`
    in lifespan startup. These are the tasks the shutdown half MUST cancel."""
    pattern = re.compile(r"^\s*([a-z_][a-z0-9_]*_task)\s*=\s*asyncio\.create_task\(", re.MULTILINE)
    return set(pattern.findall(startup))


def _find_cancelled_names(shutdown: str) -> set[str]:
    """Pull every `<name>_task.cancel()` from the shutdown half."""
    pattern = re.compile(r"\b([a-z_][a-z0-9_]*_task)\.cancel\(", re.MULTILINE)
    return set(pattern.findall(shutdown))


def test_rf656_marker_present():
    """The R-F656 marker comment must survive — if it's removed the
    fix has been reverted."""
    assert "R-F656" in _SRC, (
        "R-F656 marker missing from main.py — fix reverted?"
    )


def test_rf656_three_missing_cancels_now_present():
    """The three tasks the 2026-05-17 audit flagged must all be cancelled
    in shutdown. Pre-R-F656: 0/3 cancelled. Post-R-F656: 3/3."""
    cancelled = _find_cancelled_names(_SHUTDOWN)
    for required in ("reasoning_purge_task", "weekly_report_task", "watchlist_rescreen_task"):
        assert required in cancelled, (
            f"R-F656: {required} is created in lifespan startup but NOT "
            f"cancelled in shutdown. fly's graceful-stop window will expire "
            "before SIGKILL, leaking a zombie wakeup loop across deploys."
        )


def test_rf656_no_other_tasks_silently_uncancelled():
    """Capability test (CLAUDE.md §5): proves the broader symptom is gone,
    not just the three audit-flagged tasks. Every `*_task = asyncio.create_task`
    in lifespan startup must have a matching `*_task.cancel()` in the
    shutdown half of the same function.

    If a future commit adds a new task and forgets the cancel, this test
    fails and pins the regression at exactly the new name."""
    started = _find_task_names(_STARTUP)
    cancelled = _find_cancelled_names(_SHUTDOWN)
    missing = started - cancelled
    # _crawler_task is cancelled via the R-F507 stop-event path rather
    # than a direct .cancel() — exempt it explicitly so the test stays
    # accurate without forcing a duplicate cancel.
    missing.discard("_crawler_task")
    assert not missing, (
        f"R-F656: these tasks are created in lifespan startup but never "
        f"cancelled in shutdown: {sorted(missing)}. Each will survive the "
        "fly graceful-stop window and force a SIGKILL on every deploy."
    )
