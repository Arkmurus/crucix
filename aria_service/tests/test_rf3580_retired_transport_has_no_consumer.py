"""R-F3580 — R-F3577 wired a reader to a DEAD PIPE. This is the correction.

R-F3577 registered `intel/brain_signal_consumer.py` in lifespan to poll the Redis
list `crucix:brain:incoming_signals`, on the premise that the Node web tier writes
to it. **It does not.** A repo-wide grep for that key finds only: a stale COMMENT
in `apis/briefing.mjs`, the consumer's own constant, and R-F3577's own code and
test. There is no writer, and there has not been one for a long time.

HOW THE ERROR SURVIVED, because the mechanism is the point:

  * I verified the producer FUNCTION was still called — `pushSignalsToBrain(` is
    live at `server.mjs:7709`. I did NOT verify that the function still writes the
    KEY. It POSTs to `/brain/signal/bulk` (R-F2505); the Redis write is long gone.
  * `apis/briefing.mjs:629` said "The brain reads from
    crucix:brain:incoming_signals and generates ML leads". That comment was false
    and load-bearing in the wrong direction.
  * My own capability test asserted `"crucix:brain:incoming_signals" in briefing`
    — which matched THE COMMENT. The test enshrined the defect it was written to
    prove, so it passed while the premise was wrong.

This is the exact failure CLAUDE.md records for Phase A gate #4 (R-F2643): a gate
certified by a key nothing writes. **Grep the WRITER of every key.** A function
being called is not evidence about what it writes.

The cross-tier path was never dark. Live-verified on /api/aria/brain/stats: 120
signals under `cross_tier:crucix_briefing_signal`, last seen 0.1h ago, arriving
over HTTP. Only the retired transport was empty.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


_REPO = pathlib.Path(__file__).resolve().parents[2]
_RETIRED_KEY = "crucix:brain:incoming_signals"


def _writer_sites() -> list[str]:
    """Every place that could WRITE the retired key — a list push or a set.

    Deliberately searches for the WRITE VERB near the key, not the key alone:
    counting mentions is what made R-F3577 believe a producer existed.
    """
    hits: list[str] = []
    patterns = re.compile(r"(lpush|rpush|lPush|rPush|\.set\(|setJson|set_json)")
    for path in list(_REPO.rglob("*.mjs")) + list(_REPO.rglob("*.js")) + list(_REPO.rglob("*.py")):
        if any(p in path.parts for p in ("node_modules", ".venv", "tests", "test", ".git")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if _RETIRED_KEY not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _RETIRED_KEY in line and patterns.search(line):
                hits.append(f"{path.relative_to(_REPO)}:{i}")
    return hits


def test_nothing_writes_the_retired_key():
    """The fact R-F3577 should have established before writing any code."""
    writers = _writer_sites()
    assert not writers, (
        f"something now WRITES {_RETIRED_KEY}: {writers}. If the Redis transport "
        f"has been deliberately revived, a consumer must be restored with it — "
        f"but do not restore one without a writer, which is what R-F3577 did."
    )


def test_the_consumer_module_is_gone():
    """A 60s loop polling a key nothing writes is pure cost, and leaving it in
    place is what made the stale comment look corroborated."""
    assert not (_REPO / "aria_service" / "intel" / "brain_signal_consumer.py").exists()

    main_src = (_REPO / "aria_service" / "main.py").read_text(encoding="utf-8")
    assert "_singleton_task(_brain_signal_loop" not in main_src, (
        "the consumer is registered in lifespan again — it polls a dead key"
    )


def test_the_stale_comment_that_caused_this_is_corrected():
    """THE ROOT CAUSE. A comment naming a transport is a claim about behaviour."""
    briefing = (_REPO / "apis" / "briefing.mjs").read_text(encoding="utf-8", errors="replace")
    assert "The brain reads from crucix:brain:incoming_signals and generates ML leads." not in briefing, (
        "the false comment is back — it is what R-F3577 read and believed"
    )
    assert "RETIRED" in briefing


def test_the_live_transport_is_the_http_bulk_endpoint():
    """What actually carries web-tier signals, so a future reader does not have to
    re-derive it from a comment."""
    briefing = (_REPO / "apis" / "briefing.mjs").read_text(encoding="utf-8", errors="replace")
    assert "/brain/signal/bulk" in briefing, "the live producer no longer targets the bulk endpoint"

    routes = (_REPO / "aria_service" / "routes" / "aria.py").read_text(encoding="utf-8")
    assert '@router.post("/brain/signal/bulk")' in routes, "the bulk consumer endpoint is gone"
    assert 'module=f"cross_tier:{sig_type}"' in routes, (
        "the bulk endpoint no longer absorbs as cross_tier:* — that prefix is what "
        "proves the path live on /api/aria/brain/stats"
    )


# ── What R-F3577 got right, kept and still guarded ──────────────────────────


def test_the_monitor_does_not_re_read_source_files_every_cycle():
    """Independent of the mistaken premise, and a real defect either way:
    `test_brain_signal_path()` runs on the monitor loop and was doing SEVEN
    synchronous source-file reads per cycle, on the event loop, for answers that
    are constant for the life of the process (the code executing IS the code on
    disk). Adding one more made test_rf1091's 0.5s-budgeted loop test fail."""
    import inspect

    from aria_service.intel import wiring_monitor

    # C-31 moved the memoised seam: `_read_source` returns (content, readable) and
    # carries the lru_cache; `_cached_source` is now a thin content-only wrapper so
    # existing callers keep "absent reads as empty". The GUARD is unchanged in
    # intent — one read per path per process — so it follows the cache.
    assert hasattr(wiring_monitor, "_read_source")
    assert hasattr(wiring_monitor._read_source, "cache_info"), (
        "_read_source is not memoised — every monitor cycle re-reads the files"
    )
    assert hasattr(wiring_monitor, "_cached_source")
    wrapper_src = function_source(wiring_monitor, "_cached_source")
    assert "with open(" not in wrapper_src, (
        "_cached_source opens files directly again, bypassing the memoised read"
    )
    src = function_source(wiring_monitor, "test_brain_signal_path")
    assert "with open(" not in src, (
        "a raw open() is back in the monitor's per-cycle path; route it through "
        "_cached_source so the loop does not block on constant data"
    )


def test_the_cached_read_degrades_to_empty_not_an_exception(tmp_path):
    """Every caller treats a missing file as 'token not present'. Raising would
    turn a missing file into a monitor crash."""
    from aria_service.intel.wiring_monitor import _cached_source

    assert _cached_source(str(tmp_path / "nope.py")) == ""
    real = tmp_path / "real.py"
    real.write_text("token_here = 1\n", encoding="utf-8")
    assert "token_here" in _cached_source(str(real))


def test_the_monitor_no_longer_asserts_a_modules_source_text():
    """R-F3577's other correct finding: the monitor claimed the consumer was
    'wired' because `"_auto_started"` appeared in its source. That stayed true for
    the module's whole life while the loop had never run."""
    src = (_REPO / "aria_service" / "intel" / "wiring_monitor.py").read_text(encoding="utf-8")
    assert '"consumer_has_auto_start"' not in src
    assert '"consumer_polls_key"' not in src
    assert 'result["redis_signal_transport"] = "retired"' in src
