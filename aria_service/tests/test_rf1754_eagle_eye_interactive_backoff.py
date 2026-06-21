"""R-F1754 — EagleEye codebase indexing backs off during interactive traffic.

index_codebase_structure() calls sentence_transformers.encode(), which is
GIL-serialised: even in a worker thread it starves the main event loop, so a
concurrent user /dd or Research stream gets 0 bytes (live wedge capture
2026-06-20: eagle_eye:303 _index_codebase → _encode_edges blocked the loop
while /dd produced nothing for 130s).

Capability: when brain_hook reports interactive traffic active, _index_codebase
DEFERS (does NOT call the encoder); when quiet, it indexes normally.
"""
from pathlib import Path


def _guardian(tmp_path):
    from aria_service.intel import eagle_eye
    return eagle_eye.EagleEyeGuardian(tmp_path)


def test_index_deferred_when_interactive(monkeypatch, tmp_path):
    from aria_service.intel import brain_hook
    import aria_service.intel.coding_rag_indexer as cri

    calls = {"n": 0}
    monkeypatch.setattr(cri, "index_codebase_structure",
                        lambda *_a, **_k: calls.__setitem__("n", calls["n"] + 1))
    # Interactive → must back off.
    monkeypatch.setattr(brain_hook, "_interactive_active", lambda: True)

    g = _guardian(tmp_path)
    g._index_codebase(tmp_path / "x.py")
    assert calls["n"] == 0, "must NOT index while interactive traffic is active"


def test_index_runs_when_quiet(monkeypatch, tmp_path):
    from aria_service.intel import brain_hook
    import aria_service.intel.coding_rag_indexer as cri

    calls = {"n": 0}
    monkeypatch.setattr(cri, "index_codebase_structure",
                        lambda *_a, **_k: calls.__setitem__("n", calls["n"] + 1))
    # Quiet → indexes normally.
    monkeypatch.setattr(brain_hook, "_interactive_active", lambda: False)

    g = _guardian(tmp_path)
    g._index_codebase(tmp_path / "x.py")
    assert calls["n"] == 1, "must index when no interactive traffic"


def test_deferred_file_not_dropped(monkeypatch, tmp_path):
    """A deferred file must NOT be silently dropped: it's tracked in
    _deferred_index so the next quiet scan re-indexes it (and its hash is not
    advanced in the meantime — see _scan_files_sync guard)."""
    from aria_service.intel import brain_hook
    import aria_service.intel.coding_rag_indexer as cri

    f = tmp_path / "y.py"
    g = _guardian(tmp_path)

    # 1) Interactive → defer; file tracked, encoder NOT called.
    calls = {"n": 0}
    monkeypatch.setattr(cri, "index_codebase_structure",
                        lambda *_a, **_k: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(brain_hook, "_interactive_active", lambda: True)
    g._index_codebase(f)
    assert calls["n"] == 0
    assert str(f) in g._deferred_index, "deferred file must be tracked for retry"

    # 2) Quiet → re-index succeeds, deferral cleared (hash may now advance).
    monkeypatch.setattr(brain_hook, "_interactive_active", lambda: False)
    g._index_codebase(f)
    assert calls["n"] == 1, "deferred file must re-index once quiet"
    assert str(f) not in g._deferred_index, "deferral must clear after successful index"


def test_hash_not_advanced_while_deferred(monkeypatch, tmp_path):
    """End-to-end through _scan_files_sync: a file deferred during interactive
    traffic keeps its (absent) hash so the NEXT scan still sees it as changed."""
    from aria_service.intel import brain_hook
    import aria_service.intel.coding_rag_indexer as cri

    f = tmp_path / "z.py"
    f.write_text("a = 1\n", encoding="utf-8")
    g = _guardian(tmp_path)
    g._baseline_seeded = True   # so _index_codebase is reached for changed files
    monkeypatch.setattr(cri, "index_codebase_structure", lambda *_a, **_k: None)
    monkeypatch.setattr(brain_hook, "_interactive_active", lambda: True)

    g._scan_files_sync([f])
    # Deferred → hash must NOT have been recorded, so next scan re-detects change.
    assert str(f) not in g.file_hashes, "deferred file's hash must not advance"
    assert str(f) in g._deferred_index
