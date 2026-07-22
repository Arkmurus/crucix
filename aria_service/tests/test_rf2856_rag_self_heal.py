"""R-F2856 — RAG per-collection corruption SELF-HEAL.

CONTEXT (2026-07-22). A single corrupt collection (`aria_documents` HNSW) segfaulted
the brain on query. R-F2855's breaker stopped the crash-LOOP by disabling ALL of RAG —
correct as a circuit breaker, but it takes the 451K-fact index and all coding_* down
with the one bad collection, and it needed an OPERATOR to (a) identify WHICH collection
was corrupt, (b) quarantine it, (c) clear the breaker, (d) re-enable RAG. This module
AUTOMATES exactly that, so the next corrupt collection self-heals with no human and no
restart — the healthy collections stay UP.

SAFETY IS THE WHOLE POINT (auto-quarantine REMOVES a collection from live RAG):
  * a corrupt HNSW segfaults on query and a SIGSEGV CANNOT be caught in-process, so each
    probe runs in a SUBPROCESS — its death cannot kill the brain;
  * a collection is quarantined ONLY on a DEFINITIVE, REPRODUCED segfault (exit -11/139).
    A timeout or a plain error is AMBIGUOUS (slow disk, dim mismatch, empty) and must
    NEVER trigger a quarantine — an over-eager heal that parks a HEALTHY collection is a
    self-inflicted regression, the exact failure this test guards against;
  * quarantine RENAMES aside (never deletes — §7): the data is preserved, recoverable.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def rag(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_RAG_PATH", str(tmp_path / "aria_rag"))
    monkeypatch.delenv("ARIA_RAG_DISABLED", raising=False)
    monkeypatch.delenv("ARIA_RAG_FORCE_RETRY", raising=False)
    from aria_service.intel import rag_store as rs
    importlib.reload(rs)
    rs._client = None
    rs._documents_collection = None
    rs._facts_collection = None
    rs._chromadb_failed = False
    rs._chromadb_retry_after = 0.0
    return rs


def test_the_heal_entrypoint_and_helpers_exist(rag):
    for fn in ("diagnose_and_heal_corrupt_collections",
               "_probe_collection_isolated",
               "_quarantine_collection",
               "_list_collection_names_via_sqlite"):
        assert hasattr(rag, fn), f"rag_store must expose {fn}"


def test_a_definitively_corrupt_collection_is_quarantined_and_RAG_heals(rag, monkeypatch):
    """CAPABILITY: the whole flow — identify the bad one, park ONLY it, heal."""
    monkeypatch.setattr(rag, "_list_collection_names_via_sqlite",
                        lambda: ["aria_facts", "aria_documents", "coding_fixes"])
    # aria_documents segfaults every probe; the others are healthy.
    probe = MagicMock(side_effect=lambda name: "corrupt" if name == "aria_documents" else "healthy")
    quarantined = []
    def _q(name):
        quarantined.append(name)
        return f"{name}__corrupt_TS"
    # arm the breaker so we can prove the heal CLEARS it
    for _ in range(rag._CRASH_BREAKER_THRESHOLD):
        rag._crash_counter_bump()
    reinit = MagicMock(return_value=object())   # pretend re-init now succeeds

    res = rag.diagnose_and_heal_corrupt_collections(
        probe_fn=probe, quarantine_fn=_q, reinit_fn=reinit, reproduce=2)

    assert quarantined == ["aria_documents"], "ONLY the corrupt collection may be parked"
    assert res["healed"] is True
    assert rag._crash_counter_read() == 0, "a successful heal must clear the breaker"
    assert reinit.called, "RAG must be re-initialised so healthy collections come back UP"


def test_healthy_collections_are_NEVER_touched(rag, monkeypatch):
    """No corruption -> no quarantine, breaker untouched, no re-init side effects."""
    monkeypatch.setattr(rag, "_list_collection_names_via_sqlite",
                        lambda: ["aria_facts", "aria_documents"])
    rag._crash_counter_bump()   # one prior mark
    reinit = MagicMock(return_value=object())
    res = rag.diagnose_and_heal_corrupt_collections(
        probe_fn=lambda name: "healthy", quarantine_fn=lambda n: "x",
        reinit_fn=reinit, reproduce=2)
    assert res["quarantined"] == []
    assert res["healed"] is False
    assert not reinit.called, "no quarantine -> no re-init"


def test_a_timeout_or_error_NEVER_quarantines(rag, monkeypatch):
    """AMBIGUOUS != corrupt. A slow/erroring probe must not park a collection."""
    monkeypatch.setattr(rag, "_list_collection_names_via_sqlite",
                        lambda: ["aria_documents"])
    q = MagicMock()
    res = rag.diagnose_and_heal_corrupt_collections(
        probe_fn=lambda name: "unknown", quarantine_fn=q, reproduce=2)
    assert res["quarantined"] == []
    assert not q.called, "an 'unknown' verdict must NEVER trigger a quarantine"


def test_quarantine_requires_REPRODUCTION(rag, monkeypatch):
    """One segfault then healthy = not definitive -> no quarantine."""
    monkeypatch.setattr(rag, "_list_collection_names_via_sqlite",
                        lambda: ["aria_documents"])
    seq = iter(["corrupt", "healthy"])
    q = MagicMock()
    res = rag.diagnose_and_heal_corrupt_collections(
        probe_fn=lambda name: next(seq), quarantine_fn=q, reproduce=2)
    assert res["quarantined"] == []
    assert not q.called, "a single non-reproduced segfault must not park a collection"


def test_already_parked_collections_are_skipped(rag, monkeypatch):
    """Never re-probe/re-park a collection already quarantined."""
    monkeypatch.setattr(rag, "_list_collection_names_via_sqlite",
                        lambda: ["aria_documents__corrupt_20260722", "aria_facts"])
    probe = MagicMock(return_value="healthy")
    rag.diagnose_and_heal_corrupt_collections(
        probe_fn=probe, quarantine_fn=lambda n: "x", reproduce=2)
    probed = [c.args[0] for c in probe.call_args_list]
    assert "aria_documents__corrupt_20260722" not in probed, "parked collections must be skipped"
    assert "aria_facts" in probed


def test_probe_interprets_a_SIGSEGV_as_corrupt(rag):
    """The prober must read a subprocess SIGSEGV (-11/139) as 'corrupt'."""
    with patch("subprocess.run") as sp:
        sp.return_value = MagicMock(returncode=-11, stdout=b"", stderr=b"")
        assert rag._probe_collection_isolated("aria_documents") == "corrupt"
        sp.return_value = MagicMock(returncode=139, stdout=b"", stderr=b"")
        assert rag._probe_collection_isolated("aria_documents") == "corrupt"


def test_probe_interprets_clean_exit_as_healthy(rag):
    with patch("subprocess.run") as sp:
        sp.return_value = MagicMock(returncode=0, stdout=b"HEALTHY\n", stderr=b"")
        assert rag._probe_collection_isolated("aria_facts") == "healthy"


def test_probe_interprets_timeout_and_other_as_unknown(rag):
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
        assert rag._probe_collection_isolated("aria_documents") == "unknown"
    with patch("subprocess.run") as sp:
        sp.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"dim mismatch")
        assert rag._probe_collection_isolated("aria_documents") == "unknown"
