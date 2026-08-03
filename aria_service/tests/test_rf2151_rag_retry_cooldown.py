"""R-F2151 — RAG must self-heal from a TRANSIENT chromadb init failure.

Root cause (2026-06-30): a runtime init failure (slow/contended disk during the
boot warmup storm, a transient sqlite lock) set `_chromadb_failed=True`
PERMANENTLY, disabling grounding for the whole process life with no retry —
witnessed live: RAG sat unavailable for hours while a fresh subprocess init
succeeded in 1.2s. These tests drive the broken path (`_get_client`) and assert
the transient failure now arms a cooldown and recovers, while a genuinely
missing package stays permanently disabled.
"""
import time
from unittest.mock import MagicMock

import pytest

# chromadb ships no win-arm64 wheel, so a bare module-scope `import chromadb`
# aborted COLLECTION of the entire aria_service suite on a Windows/ARM dev box —
# one unavailable optional dependency took every other test down with it. Every
# other chromadb consumer in the codebase already guards its import; this test
# was the sole unguarded one. importorskip keeps the module importable and marks
# just these tests skipped where the package is genuinely absent (it still runs
# in CI and in the Linux image, where chromadb installs normally).
chromadb = pytest.importorskip("chromadb")

from aria_service.intel import rag_store  # noqa: E402 — must follow importorskip


def _reset_state():
    rag_store._client = None
    rag_store._documents_collection = None
    rag_store._facts_collection = None
    rag_store._documents_cold_collection = None
    rag_store._chromadb_failed = False
    rag_store._chromadb_retry_after = 0.0


def _fake_client():
    cl = MagicMock(name="PersistentClient")
    col = MagicMock(name="collection")
    col.count.return_value = 0
    cl.get_or_create_collection.return_value = col
    return cl


def test_transient_failure_arms_cooldown_not_permanent(monkeypatch):
    _reset_state()
    monkeypatch.setattr(
        chromadb, "PersistentClient",
        MagicMock(side_effect=RuntimeError("database is locked")),
    )
    assert rag_store._get_client() is None
    # THE FIX: a runtime failure must NOT permanently disable RAG.
    assert rag_store._chromadb_failed is False
    assert rag_store._chromadb_retry_after > time.monotonic()


def test_recovers_after_cooldown(monkeypatch):
    _reset_state()
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return _fake_client()

    monkeypatch.setattr(chromadb, "PersistentClient", _flaky)

    # 1st attempt fails → cooldown armed.
    assert rag_store._get_client() is None
    assert rag_store._chromadb_retry_after > 0

    # While cooling, no re-attempt (still None, init NOT called again).
    assert rag_store._get_client() is None
    assert calls["n"] == 1

    # Expire the cooldown → next call re-attempts and self-heals.
    rag_store._chromadb_retry_after = time.monotonic() - 1
    client = rag_store._get_client()
    assert client is not None
    assert calls["n"] == 2
    assert rag_store._chromadb_retry_after == 0.0  # cleared on success
    _reset_state()


def test_importerror_stays_permanent(monkeypatch):
    _reset_state()
    import builtins
    real_import = builtins.__import__

    def _no_chromadb(name, *a, **k):
        if name == "chromadb":
            raise ImportError("no module named chromadb")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_chromadb)
    assert rag_store._get_client() is None
    # A genuinely missing package SHOULD stay permanently disabled (no point retrying).
    assert rag_store._chromadb_failed is True
    _reset_state()
