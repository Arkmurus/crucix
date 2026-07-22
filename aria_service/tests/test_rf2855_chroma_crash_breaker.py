"""R-F2855 — a segfaulting chromadb init must not crash-loop the brain.

LIVE INCIDENT (2026-07-22). aria-intel crash-looped ~every 3 min, exit_code=139
(SIGSEGV). The boot traceback lands in chromadb's Rust core constructing the
PersistentClient on /data/aria_rag:

    chromadb.PersistentClient(...) -> shared_system_client.__init__
    -> chromadb/api/rust.py get_tenant / stop  -> SIGSEGV

The persistent store is corrupt in a way that crashes the Rust reader on OPEN —
before any collection operation. R-F2798 moved the older `.count()` fault out of
boot; R-F2808's own note warned the fault would resurface elsewhere and said the
structural fix is to REBUILD the store, "do not add error handling here, because a
Windows access violation cannot be caught by try/except". Correct — a SIGSEGV is a
signal, not a Python exception; no try/except can catch it.

But rebuilding needs the brain UP, and it is crash-looping at boot. Chicken and egg.

THE FIX IS NOT try/except — it is PREVENTION. A native crash kills the process, so an
in-memory failure flag (the existing _chromadb_failed / cooldown) never survives it.
A PERSISTENT crash counter on the /data volume DOES survive: increment it immediately
before the risky PersistentClient() call, reset it on success. If the process dies in
between (a segfault), the counter stays elevated, and the NEXT boot — reading a counter
at/over the threshold — SKIPS the construction entirely and boots the brain ALIVE with
RAG degraded. The store can then be rebuilt while the brain runs.

Self-healing (auto-degrades after N consecutive crashed inits), reversible (an env
force-retry clears the counter after a rebuild), and manually overridable (an env
kill-switch skips unconditionally for immediate incident control). A CAUGHT exception
is NOT a segfault, so it resets the counter — the existing R-F2151 cooldown handles
transients; the breaker is only for a process that DIED mid-init.
"""
import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def rag(tmp_path, monkeypatch):
    """Fresh rag_store pointed at a tmp volume, chromadb globals reset."""
    monkeypatch.setenv("ARIA_RAG_PATH", str(tmp_path / "aria_rag"))
    monkeypatch.delenv("ARIA_RAG_DISABLED", raising=False)
    monkeypatch.delenv("ARIA_RAG_FORCE_RETRY", raising=False)
    from aria_service.intel import rag_store as rs
    importlib.reload(rs)
    # reset module globals the client init mutates
    rs._client = None
    rs._documents_collection = None
    rs._facts_collection = None
    rs._chromadb_failed = False
    rs._chromadb_retry_after = 0.0
    return rs


def _fake_chromadb():
    """A chromadb whose PersistentClient succeeds, with spies."""
    m = MagicMock(name="chromadb")
    client = MagicMock(name="PersistentClient")
    client.get_or_create_collection.return_value = MagicMock(name="collection")
    m.PersistentClient.return_value = client
    return m, client


def test_the_breaker_helpers_exist_and_are_pure(rag):
    for fn in ("_crash_breaker_should_skip", "_crash_counter_bump", "_crash_counter_reset"):
        assert hasattr(rag, fn), f"rag_store must expose {fn} for a testable breaker"


def test_success_resets_the_counter(rag, monkeypatch):
    fake, client = _fake_chromadb()
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        rag._crash_counter_bump()           # pretend a prior in-progress marker
        c = rag._get_client()
    assert c is not None, "a healthy chromadb must still initialise"
    assert rag._crash_counter_read() == 0, "a successful init must reset the crash counter"


def test_a_counter_at_threshold_SKIPS_construction(rag, monkeypatch):
    """CAPABILITY: the core of the fix — never call the segfaulting constructor."""
    fake, client = _fake_chromadb()
    # simulate two prior segfaults: counter is at the threshold
    for _ in range(rag._CRASH_BREAKER_THRESHOLD):
        rag._crash_counter_bump()
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        c = rag._get_client()
    assert c is None, "at/over the crash threshold, init must be skipped"
    assert not fake.PersistentClient.called, (
        "the whole point: the segfaulting PersistentClient() must NOT be constructed "
        "when the breaker has tripped — you cannot try/except a SIGSEGV, you must "
        "prevent the call"
    )
    assert rag._chromadb_failed is True, "a tripped breaker marks RAG unavailable"


def test_below_threshold_still_attempts(rag):
    """One crash must not disable RAG — a deploy SIGTERM mid-init is not a segfault loop."""
    fake, client = _fake_chromadb()
    rag._crash_counter_bump()   # a single prior incomplete init
    assert rag._CRASH_BREAKER_THRESHOLD >= 2, "one crash must not trip the breaker"
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        c = rag._get_client()
    assert c is not None and fake.PersistentClient.called


def test_env_kill_switch_skips_unconditionally(rag, monkeypatch):
    """Immediate manual incident control, independent of the counter."""
    monkeypatch.setenv("ARIA_RAG_DISABLED", "1")
    fake, client = _fake_chromadb()
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        c = rag._get_client()
    assert c is None and not fake.PersistentClient.called


def test_force_retry_clears_a_tripped_breaker(rag, monkeypatch):
    """Recovery: after the store is rebuilt, force-retry re-enables init."""
    for _ in range(rag._CRASH_BREAKER_THRESHOLD + 2):
        rag._crash_counter_bump()
    monkeypatch.setenv("ARIA_RAG_FORCE_RETRY", "1")
    fake, client = _fake_chromadb()
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        c = rag._get_client()
    assert c is not None, "force-retry must clear the counter and attempt init"
    assert rag._crash_counter_read() == 0


def test_the_counter_survives_on_the_persistent_volume(rag):
    """It must be a file under the RAG volume, not an in-memory flag.

    An in-memory flag dies WITH the segfault — the whole reason the existing
    _chromadb_failed flag cannot break a crash loop.
    """
    rag._crash_counter_bump()
    assert rag._crash_counter_read() == 1
    # a fresh reload (simulating a process restart) must still see it
    import importlib
    importlib.reload(rag)
    assert rag._crash_counter_read() == 1, "the counter must persist across restarts"


def test_a_caught_exception_is_not_counted_as_a_segfault(rag):
    """A handled init error must reset the counter — the cooldown owns transients."""
    fake = MagicMock()
    fake.PersistentClient.side_effect = RuntimeError("sqlite locked (transient)")
    rag._crash_counter_bump()
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        c = rag._get_client()
    assert c is None
    assert rag._crash_counter_read() == 0, (
        "a CAUGHT exception is not a segfault — it must not accumulate toward the "
        "breaker, or a transient sqlite lock would eventually disable RAG"
    )


def test_a_broken_counter_file_fails_OPEN(rag, monkeypatch, tmp_path):
    """The guard must never itself disable a working RAG.

    If the counter cannot be read, attempt init (current behaviour) rather than
    skipping — a bug in the guard must not be worse than no guard.
    """
    def _boom(*a, **k):
        raise OSError("counter unreadable")
    monkeypatch.setattr(rag, "_crash_counter_read", _boom)
    fake, client = _fake_chromadb()
    with patch.dict("sys.modules", {"chromadb": fake, "chromadb.config": MagicMock()}):
        c = rag._get_client()
    assert c is not None and fake.PersistentClient.called, (
        "an unreadable counter must fail OPEN (attempt), never disable RAG"
    )
