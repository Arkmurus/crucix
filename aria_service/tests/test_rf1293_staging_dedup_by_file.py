"""R-F1293 — Capability test: staging keeps ONE pending entry per (file,
change_type); a new proposal supersedes the older one (newest wins) with a
visible churn counter.

The old R-F903 dedup only collapsed byte-identical proposals, so the coder's
non-deterministic re-generations of the SAME file piled up unbounded (live
2026-06-03: memory_leak_detector.py staged 186×). R-F1293 bounds the queue at the
number of distinct files and turns a would-be 186-deploy storm into one deploy.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.self_improve as si


class _FakeRS:
    def __init__(self):
        self.store = {}

    async def get_json(self, k):
        return self.store.get(k)

    async def set_json(self, k, v, ex=None):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)


def _setup(monkeypatch, tmp_path, *files):
    monkeypatch.setattr(si, "_root", tmp_path)
    monkeypatch.setattr(si, "rs", _FakeRS())
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | set(files))


def test_rf1293_restage_supersedes_one_per_file(tmp_path, monkeypatch):
    rel = "rf1293_mod.py"
    _setup(monkeypatch, tmp_path, rel)

    async def run():
        # five non-identical re-generations of the same file
        for i in range(5):
            await si.stage_improvement(rel, f"def a():\n    return {i}\n", "bug_fix", f"v{i}")
        return await si.rs.get_json(si.STAGED_KEY) or []

    staged = asyncio.run(run())
    same = [s for s in staged if s["file"] == rel]
    assert len(same) == 1, f"queue must stay 1-per-file, got {len(same)}"
    assert same[0]["new_content"] == "def a():\n    return 4\n", "newest wins"
    assert same[0]["supersede_count"] == 4, same[0].get("supersede_count")
    # the original staged time is preserved so churn duration is visible
    assert "first_staged_at" in same[0]


def test_rf1293_identical_is_noop_duplicate(tmp_path, monkeypatch):
    rel = "rf1293_mod2.py"
    _setup(monkeypatch, tmp_path, rel)
    content = "def b():\n    return 9\n"

    async def run():
        await si.stage_improvement(rel, content, "bug_fix", "x")
        r2 = await si.stage_improvement(rel, content, "bug_fix", "x")
        return r2, (await si.rs.get_json(si.STAGED_KEY) or [])

    r2, staged = asyncio.run(run())
    assert r2.get("duplicate") is True
    assert len([s for s in staged if s["file"] == rel]) == 1


def test_rf1293_collapse_heals_preexisting_backlog(tmp_path, monkeypatch):
    """A queue that already piled up (old dedup) collapses to 1-per-file on the
    next stage — the live 327->~32 heal."""
    rel = "rf1293_churned.py"
    _setup(monkeypatch, tmp_path, rel, "rf1293_other.py")

    async def run():
        # pre-seed 6 stale near-duplicate entries for the same file (old format)
        backlog = [
            {"id": f"old{i}", "file": rel, "change_type": "bug_fix",
             "new_content": f"def x():\n    return {i}\n", "status": "staged",
             "staged_at": 100 + i, "description": "old"}
            for i in range(6)
        ]
        await si.rs.set_json(si.STAGED_KEY, backlog)
        # stage an unrelated file → triggers collapse-on-load
        await si.stage_improvement("rf1293_other.py", "def y():\n    return 1\n", "bug_fix", "other")
        return await si.rs.get_json(si.STAGED_KEY) or []

    staged = asyncio.run(run())
    churned = [s for s in staged if s["file"] == rel]
    assert len(churned) == 1, f"backlog must collapse to 1, got {len(churned)}"
    assert churned[0]["new_content"] == "def x():\n    return 5\n", "newest content kept"
    assert churned[0]["supersede_count"] >= 5, churned[0].get("supersede_count")


def test_rf1293_distinct_files_coexist(tmp_path, monkeypatch):
    a, b = "rf1293_a.py", "rf1293_b.py"
    _setup(monkeypatch, tmp_path, a, b)

    async def run():
        await si.stage_improvement(a, "def a():\n    return 1\n", "bug_fix", "a")
        await si.stage_improvement(b, "def b():\n    return 1\n", "bug_fix", "b")
        return await si.rs.get_json(si.STAGED_KEY) or []

    staged = asyncio.run(run())
    files = {s["file"] for s in staged}
    assert a in files and b in files
