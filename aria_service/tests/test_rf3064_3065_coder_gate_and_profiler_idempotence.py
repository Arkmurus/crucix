"""R-F3064 + R-F3065 — two defects found in a 20-cycle live-log review.

R-F3064: `ARIA_CODER_ENABLED=0` did not stop the coder. The flag gated only
    coder_entrypoint (the loop starter), so every other caller of
    `ARIACoder.fix_gap` ran regardless. Measured live with the flag at 0:
    repeated `[aria_coder] fix_gap … stage=starting` on "Event-loop stall in
    aria_service/main.py", including TWO fix_ids inside the same second.
    `self_introspect_guard.py:224,313` already recorded the same contradiction
    — it was documented but never gated.

R-F3065: `start_profiler()` set `_state["running"]=True` but never CHECKED it,
    so every call spawned another daemon sampler thread plus two asyncio tasks.
    A live wedge dump showed SEVEN `continuous_profiler.py:_sample_thread`
    threads where exactly one is correct — each sampling
    sys._current_frames() every 100ms. The same dump had 203 threads total (83
    executor workers, 67 aiosqlite connection threads); at that count GIL
    contention alone stalls the loop, which is what the R-F703 detector kept
    firing on (~3-6 stalls/hour, 526 wedge files on disk).
"""
from __future__ import annotations

import asyncio
import types

import types as _types

import pytest


# ---------------------------------------------------------------------------
# R-F3064 — the off switch must actually switch off
# ---------------------------------------------------------------------------

def _gap():
    from aria_service.autonomous.gap_detector import Gap, GapType
    try:
        return Gap(gap_id="g_test", gap_type=GapType.MODULE_BUG,
                   title="Event-loop stall in aria_service/main.py",
                   description="x", severity="HIGH")
    except Exception:
        g = types.SimpleNamespace()
        g.gap_id, g.title, g.description, g.severity = "g_test", "t", "d", "HIGH"
        return g


def _coder():
    from aria_service.autonomous.self_coder import ARIACoder
    return ARIACoder(redis_client=None, aria_service_url="http://localhost:8000")


def test_rf3064_fix_gap_refuses_when_flag_is_off(monkeypatch, tmp_path):
    """THE bug, verbatim: the flag was 0 in production and fix_gap still ran.

    FAILS BEFORE: fix_gap ignored ARIA_CODER_ENABLED entirely and proceeded to
    create a workspace and start the pipeline.
    """
    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")
    c = _coder()
    c.workspace_base = tmp_path
    res = asyncio.run(c.fix_gap(_gap()))
    assert res.success is False
    assert res.failure_reason == "coder_disabled", (
        f"fix_gap ran with ARIA_CODER_ENABLED=0 (reason={res.failure_reason!r})"
    )
    # and it must not have created a workspace for a refused run
    assert not any(tmp_path.iterdir()), "a refused fix still created a workspace"


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
def test_rf3064_only_a_truthy_flag_enables_the_lane(monkeypatch, tmp_path, val):
    monkeypatch.setenv("ARIA_CODER_ENABLED", val)
    c = _coder()
    c.workspace_base = tmp_path
    res = asyncio.run(c.fix_gap(_gap()))
    assert res.failure_reason == "coder_disabled", f"{val!r} should not enable the lane"


def test_rf3064_unset_flag_refuses(monkeypatch, tmp_path):
    """An absent flag is not consent."""
    monkeypatch.delenv("ARIA_CODER_ENABLED", raising=False)
    c = _coder()
    c.workspace_base = tmp_path
    res = asyncio.run(c.fix_gap(_gap()))
    assert res.failure_reason == "coder_disabled"


class _PastTheGate(Exception):
    """Sentinel raised at the first statement AFTER the gate, so the test
    proves the gate's decision without running the real pipeline (which makes
    network + LLM calls and hangs a unit test)."""


def test_rf3064_operator_initiated_is_exempt(monkeypatch, tmp_path):
    """A human explicitly asking for this fix IS consent (R-F824). The gate
    must not block the operator's own request — that would over-correct a
    safety fix into a broken feature."""
    from aria_service.autonomous import self_coder as sc

    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")

    def _boom():
        raise _PastTheGate()

    # uuid4() is the first statement after the gate
    monkeypatch.setattr(sc.uuid, "uuid4", _boom)
    c = _coder()
    c.workspace_base = tmp_path

    with pytest.raises(_PastTheGate):
        asyncio.run(c.fix_gap(_gap(), operator_initiated=True))


def test_rf3064_autonomous_call_never_reaches_the_pipeline(monkeypatch, tmp_path):
    """The mirror of the above: with the flag off and no operator, execution
    must stop BEFORE the pipeline begins."""
    from aria_service.autonomous import self_coder as sc

    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")

    def _boom():
        raise _PastTheGate()

    monkeypatch.setattr(sc.uuid, "uuid4", _boom)
    c = _coder()
    c.workspace_base = tmp_path

    res = asyncio.run(c.fix_gap(_gap()))   # must NOT raise _PastTheGate
    assert res.failure_reason == "coder_disabled"


# ---------------------------------------------------------------------------
# R-F3065 — the profiler must not multiply itself
# ---------------------------------------------------------------------------

def test_rf3065_repeat_start_does_not_spawn_a_second_sampler(monkeypatch):
    """THE bug: 7 live sampler threads where 1 is correct.

    FAILS BEFORE: each start_profiler() call spawned a new daemon thread.
    """
    import threading
    from aria_service.intel import continuous_profiler as cp

    monkeypatch.setattr(cp, "_ENABLED", True, raising=False)
    started: list[str] = []
    real_thread = threading.Thread

    class _CountingThread(real_thread):
        """Never runs the sampler body, but DOES report alive after start() —
        the production guard keys on Thread.is_alive(), so a double that always
        reports dead would make the guard look broken when it is not."""
        _alive = False

        def start(self):
            started.append(self.name)
            self._alive = True

        def is_alive(self):
            return self._alive

    # R-F3323 — patch continuous_profiler's REFERENCE, not the threading module.
    # `cp.threading` IS the global threading module, so setattr on it replaced
    # threading.Thread PROCESS-WIDE for the duration of this test, and
    # _CountingThread.start() deliberately never starts anything. Any component
    # that created a worker thread in that window got one that never ran, and
    # anything waiting on it waited forever. continuous_profiler uses exactly one
    # name from threading (Thread, line ~227), so a module-local shim is
    # equivalent for this test and cannot leak.
    monkeypatch.setattr(cp, "threading", _types.SimpleNamespace(Thread=_CountingThread))
    monkeypatch.setitem(cp._state, "running", False)
    monkeypatch.setitem(cp._state, "sampler_thread", None)

    async def _go():
        a = cp.start_profiler()
        b = cp.start_profiler()
        c = cp.start_profiler()
        # Cancel AND AWAIT — a cancelled-but-unawaited task makes asyncio.run
        # block forever at loop shutdown, which hung the whole suite when this
        # file ran alongside others.
        for t in {id(t): t for t in (a + b + c)}.values():
            t.cancel()
        await asyncio.gather(*{id(t): t for t in (a + b + c)}.values(),
                             return_exceptions=True)
        return a, b, c

    a, b, c = asyncio.run(_go())
    assert len(started) == 1, (
        f"start_profiler spawned {len(started)} sampler threads across 3 calls "
        f"— each one samples every 100ms, so the profiler multiplies the "
        f"overhead it exists to measure"
    )
    # Tasks are loop-bound and are deliberately NOT cached/reused — only the
    # sampler THREAD is guarded. Each call returns fresh tasks for the current
    # loop; what must not repeat is the thread.
    assert cp._state.get("sampler_thread") is not None


def test_rf3065_stop_then_start_works_again(monkeypatch):
    """The guard must not make the profiler un-restartable — that would trade
    a leak for a permanently dead profiler."""
    import threading
    from aria_service.intel import continuous_profiler as cp

    monkeypatch.setattr(cp, "_ENABLED", True, raising=False)
    started: list[str] = []

    class _CountingThread(threading.Thread):
        _alive = False

        def start(self):
            started.append(self.name)
            self._alive = True

        def is_alive(self):
            return self._alive

    # R-F3323 — patch continuous_profiler's REFERENCE, not the threading module.
    # `cp.threading` IS the global threading module, so setattr on it replaced
    # threading.Thread PROCESS-WIDE for the duration of this test, and
    # _CountingThread.start() deliberately never starts anything. Any component
    # that created a worker thread in that window got one that never ran, and
    # anything waiting on it waited forever. continuous_profiler uses exactly one
    # name from threading (Thread, line ~227), so a module-local shim is
    # equivalent for this test and cannot leak.
    monkeypatch.setattr(cp, "threading", _types.SimpleNamespace(Thread=_CountingThread))
    monkeypatch.setitem(cp._state, "running", False)
    monkeypatch.setitem(cp._state, "sampler_thread", None)

    async def _go():
        t1 = cp.start_profiler()
        cp.stop_profiler(t1)          # cancels t1
        t2 = cp.start_profiler()
        for t in t2:
            t.cancel()
        await asyncio.gather(*t1, *t2, return_exceptions=True)

    asyncio.run(_go())
    assert len(started) == 2, (
        f"stop→start should spawn a fresh sampler; got {len(started)} starts"
    )


def test_rf3065_state_exposes_the_guard():
    from aria_service.intel import continuous_profiler as cp
    assert "sampler_thread" in cp._state


def test_rf3065_tasks_are_not_cached_across_loops():
    """Guard the correction: caching loop-bound tasks and returning them to a
    later caller on a different loop hung the suite. _state must not hold them."""
    from aria_service.intel import continuous_profiler as cp
    assert "tasks" not in cp._state
