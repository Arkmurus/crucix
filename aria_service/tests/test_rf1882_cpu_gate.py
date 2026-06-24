"""R-F1882 — the HTML parse and the JSON snapshot encoders must share ONE
CPU-serialisation gate, so concurrent pure-Python GIL holders can't pile up and
starve the event-loop heartbeat (R-F703 GIL wedge).

Evidence (wedge stacks, 2026-06-24): during the 7-19s stalls the event-loop
thread was idle-but-starved while worker threads held the GIL in
semantic_search._safe_encode, neural_memory._encode_edges (throttled) AND
crawler._strip_html_to_text (NOT throttled — raw asyncio.to_thread). The HTML
parse running concurrently with a JSON encode = multiple pure-Python GIL holders.

Capability test: drive the real throttle. With concurrency=1, a run_in_thread_cpu
task (HTML-parse stand-in) and a run_in_thread_throttled task (snapshot-encode
stand-in) launched concurrently must NOT overlap — proving they share the same
semaphore (the fix). Also assert the gate tunes with concurrency.
"""
from __future__ import annotations

import asyncio
import threading
import time

from aria_service.intel import _snapshot_throttle as st


def _make_counter():
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def task(_name):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)  # hold the "GIL" slot briefly
        with lock:
            state["now"] -= 1
        return _name

    return task, state


def test_cpu_gate_and_throttle_share_one_semaphore():
    """concurrency=1: an HTML-parse (run_in_thread_cpu) and a snapshot-encode
    (run_in_thread_throttled) launched together must serialise (max overlap 1)."""
    async def run():
        st.reset_for_tests()  # rebind semaphore to this test loop (concurrency=_CONCURRENCY=1)
        task, state = _make_counter()
        await asyncio.gather(
            st.run_in_thread_throttled(task, "snapshot"),
            st.run_in_thread_cpu(task, "html-1"),
            st.run_in_thread_cpu(task, "html-2"),
        )
        return state["max"]
    assert asyncio.run(run()) == 1, "cpu-gate and throttle must share one semaphore (max overlap 1)"


def test_cpu_gate_returns_value_and_propagates_args():
    """run_in_thread_cpu must behave like to_thread (return value, pass args)."""
    async def run():
        st.reset_for_tests()
        return await st.run_in_thread_cpu(lambda a, b: a + b, 2, 3)
    assert asyncio.run(run()) == 5


def test_cpu_gate_tunable_concurrency(monkeypatch):
    """Bumping ARIA_SNAPSHOT_THROTTLE_CONCURRENCY raises the cap (operator lever)."""
    async def run():
        monkeypatch.setattr(st, "_CONCURRENCY", 2)
        st.reset_for_tests()
        task, state = _make_counter()
        await asyncio.gather(*[st.run_in_thread_cpu(task, f"t{i}") for i in range(4)])
        return state["max"]
    assert asyncio.run(run()) == 2
