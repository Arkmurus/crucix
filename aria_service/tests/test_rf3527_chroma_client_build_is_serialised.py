"""R-F3527 — two threads building the chromadb client at once segfaulted production.

THE INCIDENT (2026-07-30). aria-intel crash-looped on SIGSEGV (`exit_code=139`,
`oom_killed=false`) every ~70-150s. It survived three deploys and the pausing of BOTH the
DD reconcile loop and the autonomous engine — which eliminated the load hypothesis and
left the fault in native code.

`PYTHONFAULTHANDLER=1` (a stock env var; no code change, no deploy) named it. Two stacks,
concurrent, in the same dump:

    rag_store.py:498 in _get_client
      chromadb/__init__.py:228 in PersistentClient
        client.py:105 __init__ -> 641/650 _validate_tenant -> 721 get_tenant
          chromadb/api/rust.py:175 in get_tenant            <- Rust core

    chromadb/api/shared_system_client.py:124
      chromadb/config.py:473 in stop
        chromadb/api/rust.py:131 in stop                    <- Rust core

One thread CONSTRUCTING while another STOPS the shared system. chromadb keys its systems
by path in `SharedSystemClient._identifier_to_system`; a second concurrent
`PersistentClient(path=...)` for the same path tears down / re-enters a system the first
is still inside, and the Rust core dereferences freed state.

`_get_client` is SYNC and had no mutual exclusion. The `_init_lock` in the module is an
`asyncio.Lock` guarding a DIFFERENT path — it cannot serialise sync callers, and an
asyncio lock does not protect across threads at all. The process runs ~25 threads (7
executor + 7 aiosqlite workers + uvicorn), so simultaneous first touch is the ordinary
boot pattern once several subsystems warm RAG together, not a rare interleaving.

WHY R-F2855 DID NOT CATCH IT — as important as the race. Its crash counter is bumped
before construction and RESET ON SUCCESS. The first init succeeds and resets to 0; the
racing second construction dies with the counter at 1, below its threshold of 2. It
oscillates 0<->1 forever and can never trip. That is why `/data/.chroma_init_crashes` read
**0** on a box that was actively crash-looping. The breaker is still right for a genuinely
corrupt store — it simply cannot see a CONCURRENCY fault.
"""
from __future__ import annotations

import threading

import pytest

from aria_service.intel import rag_store as rs

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    """Force the build path: pretend nothing is constructed yet."""
    monkeypatch.setattr(rs, "_client", None, raising=False)
    monkeypatch.setattr(rs, "_documents_collection", None, raising=False)
    monkeypatch.setattr(rs, "_facts_collection", None, raising=False)
    monkeypatch.setattr(rs, "_documents_cold_collection", None, raising=False)
    yield


def test_capability_concurrent_callers_build_the_client_exactly_once(monkeypatch):
    """THE DEFECT, reproduced. Ten threads call `_get_client()` on a cold module.

    Without serialisation every one of them enters the build — which is the concurrent
    `PersistentClient` construction that segfaults the Rust core. The assertion is that
    the BUILD runs once, not that the calls succeed.
    """
    entered = []
    barrier = threading.Barrier(10)
    sentinel = object()

    def _fake_build():
        entered.append(threading.current_thread().name)
        # Hold the build open so any unserialised caller is guaranteed to overlap
        # rather than racing through a microsecond-wide window.
        barrier_wait_timeout = 0.25
        threading.Event().wait(barrier_wait_timeout)
        rs._client = sentinel
        rs._documents_collection = sentinel
        rs._facts_collection = sentinel
        return sentinel

    monkeypatch.setattr(rs, "_get_client_unlocked", _fake_build)

    results = []

    def _worker():
        barrier.wait()
        results.append(rs._get_client())

    threads = [threading.Thread(target=_worker, name=f"w{i}") for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(entered) == 1, (
        f"the client was constructed {len(entered)} times concurrently — this is the "
        f"chromadb Rust use-after-free: {entered}")
    assert all(r is sentinel for r in results), "a caller got a half-built client"


def test_the_lock_is_a_thread_lock_not_an_asyncio_lock():
    """An asyncio.Lock protects nothing across threads, and `_get_client` is sync and
    called from executor workers. Using the existing `_init_lock` would have looked
    like a fix and changed nothing."""
    import asyncio
    assert isinstance(rs._client_build_lock, type(threading.Lock())), (
        "the build lock is not a threading lock")
    assert not isinstance(rs._client_build_lock, asyncio.Lock)


def test_the_fast_path_takes_no_lock(monkeypatch):
    """Once built, `_get_client` is on the hot path of every RAG call. Serialising that
    would convert a crash into a contention bottleneck — a different outage."""
    sentinel = object()
    monkeypatch.setattr(rs, "_client", sentinel, raising=False)
    monkeypatch.setattr(rs, "_documents_collection", sentinel, raising=False)
    monkeypatch.setattr(rs, "_facts_collection", sentinel, raising=False)

    rs._client_build_lock.acquire()          # hold it: the fast path must not need it
    try:
        assert rs._get_client() is sentinel
    finally:
        rs._client_build_lock.release()


def test_the_build_is_rechecked_inside_the_lock(monkeypatch):
    """A thread that waited on the lock must NOT build again — the winner already did.
    Without the re-check, serialising merely turns a simultaneous double-build into a
    sequential one, which is the same use-after-free a moment later."""
    calls = []
    sentinel = object()

    def _fake_build():
        calls.append(1)
        rs._client = sentinel
        rs._documents_collection = sentinel
        rs._facts_collection = sentinel
        return sentinel

    monkeypatch.setattr(rs, "_get_client_unlocked", _fake_build)
    assert rs._get_client() is sentinel
    assert rs._get_client() is sentinel      # second caller, client now present
    assert len(calls) == 1, "the build ran twice — the re-check is missing"


def test_the_build_body_cannot_re_enter_the_lock():
    """`threading.Lock` is not reentrant: if the build path called back into
    `_get_client` the process would DEADLOCK — trading a crash-loop for a hang, which
    is harder to diagnose. Pinned so a future edit inside the build cannot introduce it.
    """
    import inspect
    import re
    body = function_source(rs, "_get_client_unlocked")
    assert not re.search(r"[^_\w]_get_client\(", body), (
        "the build body calls _get_client() — non-reentrant lock, guaranteed deadlock")


def test_r_f2855_breaker_still_present_and_not_relied_on_for_this():
    """The breaker is correct for a CORRUPT STORE and stays. It could never have caught
    this: reset-on-success means a racing second build dies at count 1, below the
    threshold of 2, so it oscillates and never trips."""
    assert rs._CRASH_BREAKER_THRESHOLD >= 2
    import inspect
    src = function_source(rs, "_get_client_unlocked")
    assert "_crash_counter_bump()" in src, "the corrupt-store breaker was removed"
