"""R-F3985 / C-72 — the boot-acceleration sidecar was rewritten on EVERY flush,
though it is only ever read at boot.

`_write_to_disk_atomic` serialises the canonical file (~150-171 MB at ~223k
facts), fsyncs it, renames, fsyncs the directory — and then unconditionally
calls `_write_facts_sidecar(data)` (`knowledge.py:677`), writing the SAME data
again as JSONL with its OWN fsync. That doubles the I/O of every flush for a
file with exactly one consumer: `_read_from_disk_chunked`, once per process, at
boot.

**Why the obvious fix is wrong, and why C-61 deliberately did not attempt it.**
The reader only USES the sidecar when it is CURRENT:

    knowledge.py  marker = meta.get("_canonical");  current = _canonical_marker()
                  if marker == current or current is None:   # mtime+size

so simply writing it on a slow timer would leave it permanently stale behind the
canonical file — never used, and R-F2144's boot acceleration silently deleted
while the I/O cost merely moved. The saving would be real and the feature would
be gone, with nothing failing to say so.

**The correct question is not "how often" but "when could a boot follow".**
A boot follows either a clean shutdown or a crash:

  * CLEAN SHUTDOWN — `shutdown()` performs a final flush. Writing the sidecar
    there makes it current against the canonical written in that same call, so
    the next boot is fast. This is the overwhelmingly common case: deploys.
  * CRASH — nothing runs. A slow-cadence write is the only hedge, and it is
    genuinely useful because C-61 made flushes MATERIAL-only, so quiet periods
    now exist during which a written sidecar stays current.

Either way a stale sidecar is SAFE by construction: the reader detects the
marker mismatch, falls back to the monolithic load, and regenerates the sidecar
off the boot path. That is the same path every fresh deploy already takes.

So: write it on the final flush, and at most once per `SIDECAR_MIN_INTERVAL_S`
otherwise. Correctness is unchanged; only redundant writes are removed.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import knowledge as K


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(K, "_cache", {"facts": [{"id": "f1", "content": "x"}]})
    monkeypatch.setattr(K, "_dirty", False)
    monkeypatch.setattr(K, "_dirty_bookkeeping_since", None)
    monkeypatch.setattr(K, "_last_sidecar_write", None)
    monkeypatch.setattr(K, "_ensure_flusher", lambda: None)
    yield


def _spy(monkeypatch):
    """Count sidecar writes without touching the disk."""
    calls = []
    monkeypatch.setattr(K, "_write_facts_sidecar",
                        lambda data, marker=None: calls.append(marker))
    monkeypatch.setattr(K, "_write_to_disk_atomic_canonical_only",
                        lambda data: None, raising=False)
    return calls


# ── the contract ─────────────────────────────────────────────────────────────

def test_should_write_sidecar_is_true_on_a_final_flush():
    assert K._should_write_sidecar(final=True, now=100.0) is True


def test_should_write_sidecar_is_false_immediately_after_one(monkeypatch):
    monkeypatch.setattr(K, "_last_sidecar_write", 100.0)
    assert K._should_write_sidecar(final=False, now=101.0) is False


def test_should_write_sidecar_is_true_once_the_interval_has_passed(monkeypatch):
    monkeypatch.setattr(K, "_last_sidecar_write", 100.0)
    later = 100.0 + K.SIDECAR_MIN_INTERVAL_S + 1
    assert K._should_write_sidecar(final=False, now=later) is True


def test_a_final_flush_writes_even_inside_the_interval(monkeypatch):
    """A clean shutdown must ALWAYS leave a current sidecar — that is the case
    the whole mechanism exists for."""
    monkeypatch.setattr(K, "_last_sidecar_write", 100.0)
    assert K._should_write_sidecar(final=True, now=100.5) is True


def test_the_first_ever_write_is_allowed():
    """None means never-written-in-this-process, not "written at time 0".
    `time.monotonic()`'s origin is platform-defined, so 0.0 would mean
    "long ago" on one host and "just now" on another."""
    assert K._last_sidecar_write is None
    assert K._should_write_sidecar(final=False, now=1.0) is True


# ── it must still be written, just not every time ────────────────────────────

def test_shutdown_forces_a_sidecar_write():
    from ._source_probe import function_code
    src = function_code(K, "shutdown")
    assert "final=True" in src or "_flush_to_disk(final=True)" in src, (
        "shutdown does not force a sidecar write — every clean restart would "
        "fall back to the ~10-minute monolithic boot load"
    )


def test_the_flush_path_passes_the_decision_through():
    from ._source_probe import function_code
    src = function_code(K, "_write_to_disk_atomic")
    assert "write_sidecar" in src, (
        "the sidecar is still written unconditionally on every canonical flush"
    )


# ── a stale sidecar must remain SAFE, not merely rarer ───────────────────────

def test_a_marker_mismatch_still_falls_back(monkeypatch, tmp_path):
    """The safety property the whole change rests on: a stale sidecar is
    detected and ignored, never loaded as if it were current."""
    import json as _json
    jsonl = tmp_path / "facts.jsonl"
    meta = tmp_path / "facts.meta.json"
    jsonl.write_text('{"id": "stale"}\n', encoding="utf-8")
    meta.write_text(_json.dumps({"_canonical": {"mtime": 1.0, "size": 1}}),
                    encoding="utf-8")
    monkeypatch.setattr(K, "_sidecar_paths", lambda: (str(jsonl), str(meta)))
    monkeypatch.setattr(K, "_canonical_marker",
                        lambda: {"mtime": 999.0, "size": 999})
    monkeypatch.setattr(K, "_read_from_disk", lambda: {"facts": [{"id": "real"}]})

    out = asyncio.run(K._read_from_disk_chunked())
    assert out is not None
    assert [f["id"] for f in out["facts"]] == ["real"], (
        "a STALE sidecar was loaded as current — the marker check is the only "
        "thing making a reduced write cadence safe"
    )


def test_a_matching_marker_is_still_used(monkeypatch, tmp_path):
    """And the acceleration must still work when the sidecar IS current."""
    import json as _json
    jsonl = tmp_path / "facts.jsonl"
    meta = tmp_path / "facts.meta.json"
    jsonl.write_text('{"id": "fast"}\n', encoding="utf-8")
    marker = {"mtime": 42.0, "size": 7}
    meta.write_text(_json.dumps({"_canonical": marker}), encoding="utf-8")
    monkeypatch.setattr(K, "_sidecar_paths", lambda: (str(jsonl), str(meta)))
    monkeypatch.setattr(K, "_canonical_marker", lambda: dict(marker))

    out = asyncio.run(K._read_from_disk_chunked())
    assert out is not None
    assert [f["id"] for f in out["facts"]] == ["fast"], (
        "R-F2144's boot acceleration stopped working — the saving would be real "
        "and the feature would be gone"
    )
