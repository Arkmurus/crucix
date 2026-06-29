"""R-F2144 — the boot knowledge load must not starve the single event loop.

The 2026-06-29 ~3h outage: the ~223k-fact knowledge blob was loaded with a
monolithic `json.load` ON the loop — one GIL-held parse+object-construction with
zero yield points (verified: orjson+to_thread does NOT help a monolithic parse).
R-F2144 writes a DERIVED, line-delimited JSONL sidecar beside the canonical
monolithic file and streams it in chunks with `await asyncio.sleep(0)` yields at
boot. The canonical monolithic store is unchanged and remains the source of
truth; a missing/stale/corrupt sidecar falls back to the monolithic load — so a
sidecar bug can never lose data.

These tests prove: (1) lossless round-trip, (2) the chunked read does NOT starve
the loop while the monolithic fallback DOES, (3) backward-compat fallback when no
sidecar exists, (4) a STALE sidecar is ignored in favour of the fresh canonical
file, (5) a CORRUPT sidecar falls back losslessly.
"""
import asyncio
import json
import os
import time

import aria_service.intel.knowledge as K


def _measure_starvation(read_coro_factory):
    """Run the async read while a 10ms heartbeat measures loop responsiveness.
    Returns (result, max_gap_seconds)."""
    async def _run():
        gaps: list[float] = []
        stop = asyncio.Event()

        async def _hb():
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        hb = asyncio.create_task(_hb())
        await asyncio.sleep(0.05)
        result = await read_coro_factory()
        stop.set()
        await hb
        return result, (max(gaps) if gaps else 0.0)

    return asyncio.run(_run())


def test_rf2144_roundtrip_lossless(tmp_path, monkeypatch):
    db = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(K, "_DISK_PATH", str(db), raising=False)
    data = {"facts": [{"id": i, "text": f"fact {i}"} for i in range(5000)],
            "queries": ["q1"], "learnings": ["l1"], "version": 7}
    K._write_to_disk_atomic(data)  # writes monolithic + derived sidecar

    jsonl, meta = K._sidecar_paths()
    assert os.path.exists(jsonl) and os.path.exists(meta), "sidecar must be written"

    loaded = asyncio.run(K._read_from_disk_chunked())
    assert loaded is not None
    assert len(loaded["facts"]) == 5000
    assert loaded["facts"][0] == {"id": 0, "text": "fact 0"}
    assert loaded["facts"][4999] == {"id": 4999, "text": "fact 4999"}
    assert loaded["queries"] == ["q1"]
    assert loaded["learnings"] == ["l1"]
    assert loaded["version"] == 7


def test_rf2144_chunked_read_no_starvation_but_monolithic_does(tmp_path, monkeypatch):
    db = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(K, "_DISK_PATH", str(db), raising=False)
    facts = [{"id": i, "entity": f"E{i}", "text": "x" * 300, "ts": 1.0}
             for i in range(300_000)]
    data = {"facts": facts, "queries": [], "learnings": [], "version": 1}
    K._write_to_disk_atomic(data)
    jsonl, _meta = K._sidecar_paths()
    assert os.path.exists(jsonl)

    # (a) chunked sidecar read — must NOT starve the loop
    res, max_gap = _measure_starvation(lambda: K._read_from_disk_chunked())
    assert len(res["facts"]) == 300_000, "chunked read must be lossless"
    assert max_gap < 0.25, f"chunked sidecar read starved the loop {max_gap*1000:.0f}ms"

    # (b) remove the sidecar → forces the monolithic fallback (the OLD behaviour),
    # which must still be lossless but DOES starve — proving the sidecar is the fix.
    os.remove(jsonl)
    res2, gap2 = _measure_starvation(lambda: K._read_from_disk_chunked())
    assert len(res2["facts"]) == 300_000, "monolithic fallback must be lossless"
    assert gap2 > 0.25, (
        f"the monolithic fallback should starve the loop (got {gap2*1000:.0f}ms) "
        f"— if it doesn't, this test isn't exercising the slow path")


def test_rf2144_fallback_when_no_sidecar(tmp_path, monkeypatch):
    """Pre-R-F2144 on-disk state (monolithic only, no sidecar) loads correctly."""
    db = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(K, "_DISK_PATH", str(db), raising=False)
    db.write_text(json.dumps({"facts": [{"id": i} for i in range(1000)], "version": 1}))
    loaded = asyncio.run(K._read_from_disk_chunked())
    assert loaded is not None and len(loaded["facts"]) == 1000


def test_rf2144_stale_sidecar_ignored(tmp_path, monkeypatch):
    """If the canonical file changed out-of-band, the stale sidecar must be
    ignored and the FRESH monolithic loaded."""
    db = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(K, "_DISK_PATH", str(db), raising=False)
    K._write_to_disk_atomic({"facts": [{"id": 1}], "version": 1})  # sidecar marks 1 fact
    # canonical changes out of band → sidecar marker (mtime+size) no longer matches
    db.write_text(json.dumps({"facts": [{"id": 1}, {"id": 2}], "version": 2}))
    loaded = asyncio.run(K._read_from_disk_chunked())
    assert len(loaded["facts"]) == 2, "must load the fresh canonical, not the stale sidecar"
    assert loaded["version"] == 2


def test_rf2144_corrupt_sidecar_falls_back(tmp_path, monkeypatch):
    db = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(K, "_DISK_PATH", str(db), raising=False)
    K._write_to_disk_atomic({"facts": [{"id": i} for i in range(100)], "version": 1})
    jsonl, _meta = K._sidecar_paths()
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json\n")  # corrupt; marker still matches
    loaded = asyncio.run(K._read_from_disk_chunked())
    assert loaded is not None and len(loaded["facts"]) == 100, "corrupt sidecar must fall back losslessly"
