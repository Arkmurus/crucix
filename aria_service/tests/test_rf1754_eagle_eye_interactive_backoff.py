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
