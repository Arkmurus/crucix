"""R-F1623 — eagle_eye must persist file_hashes across restarts so a
deploy/restart does NOT re-encode the entire codebase (sentence_transformers,
GIL-bound → 9-12s event-loop wedges, the post-deploy storm). Drives the REAL
_scan_files_sync and asserts a simulated restart re-indexes ZERO unchanged
files, but still re-indexes a changed one.
"""
from __future__ import annotations

from aria_service.intel import eagle_eye as EE


def _make_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("a = 1\n", encoding="utf-8")
    (proj / "b.py").write_text("b = 2\n", encoding="utf-8")
    return proj


def test_hashes_persist_no_reindex_on_restart(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    files = [p for p in proj.rglob("*.py")]

    # Isolate the persistence path to this test's tmp dir (the dev box has a
    # real /data volume; both guardians must share THIS tmp file to simulate
    # the persistent volume without contaminating real /data or other runs).
    hashes_file = tmp_path / "persisted_hashes.json"
    monkeypatch.setattr(EE.EagleEyeGuardian, "_hashes_path", lambda self: hashes_file, raising=True)

    calls = {"index": 0}

    def _count_index(self, fp):  # stub the heavy sentence_transformers encode
        calls["index"] += 1

    monkeypatch.setattr(EE.EagleEyeGuardian, "_index_codebase", _count_index, raising=True)

    # First run (TRULY cold — no persisted hashes). R-F1625: the cold-start
    # baseline must NOT re-encode the codebase (that storm is the recurring
    # wedge; the coding RAG already persists). It records hashes + persists,
    # and indexes ZERO files.
    g1 = EE.EagleEyeGuardian(proj)
    assert g1._baseline_seeded is False, "truly cold start: baseline not yet seeded"
    g1._scan_files_sync(list(files))
    assert calls["index"] == 0, (
        "R-F1625: cold-start baseline must NOT re-encode the codebase (no wedge storm)"
    )
    assert g1._baseline_seeded is True, "baseline must be marked seeded after the first scan"
    assert g1._hashes_path().exists(), "file_hashes must be persisted to disk"

    # Simulate a RESTART: a fresh guardian must LOAD the persisted hashes.
    calls["index"] = 0
    g2 = EE.EagleEyeGuardian(proj)
    assert len(g2.file_hashes) == 2, "restart must load persisted hashes, not start empty"
    g2._scan_files_sync(list(files))
    assert calls["index"] == 0, (
        "unchanged files must NOT be re-indexed after restart — this is the wedge fix"
    )

    # A genuinely changed file IS re-indexed (correctness preserved).
    (proj / "a.py").write_text("a = 999\n", encoding="utf-8")
    calls["index"] = 0
    g2._scan_files_sync(list(files))
    assert calls["index"] == 1, "a changed file must be re-indexed"
