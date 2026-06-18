"""R-F1668 — streamed knowledge dump must be byte-correct + GIL-yielding.

Cure 3 for the brain-freeze disease: the 87k-fact json.dump held the GIL 2-7s
(C encoder never yields), starving the event loop. The fix streams the facts
array with time.sleep(0) every 2048 facts. These tests are SAFETY-CRITICAL —
they prove the streamed file is valid JSON that round-trips identically (no data
loss/corruption) and that the GIL-yield path actually executes.
"""
import json
import os
import tempfile

from aria_service.intel import knowledge


def _write(tmp_dir, data):
    """Point the dumper at a temp file and run it (it's sync)."""
    target = os.path.join(tmp_dir, "aria_knowledge.json")
    orig = knowledge._DISK_PATH
    knowledge._DISK_PATH = target
    try:
        knowledge._write_to_disk_atomic(data)
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)  # raises if the streamed JSON is invalid
    finally:
        knowledge._DISK_PATH = orig


def test_streamed_dump_round_trips_identically():
    data = {
        "version": 7,
        "queries": [{"q": "alpha"}, {"q": "beta"}],
        "learnings": ["x", "y"],
        "facts": [
            {"id": f"f{i}", "topic": f"t{i % 5}", "content": f"fact number {i}",
             "confidence": "CONFIRMED", "nested": {"a": [1, 2, i]}}
            for i in range(5000)  # > 2048 so the chunk-yield path fires
        ],
    }
    loaded = _write(tempfile.gettempdir(), data)
    # Facts must round-trip EXACTLY — this is the data-integrity guarantee.
    assert loaded["facts"] == data["facts"], "R-F1668: facts corrupted in streamed dump"
    assert len(loaded["facts"]) == 5000
    # Non-facts keys preserved.
    assert loaded["version"] == 7
    assert loaded["queries"] == data["queries"]
    assert loaded["learnings"] == data["learnings"]


def test_streamed_dump_is_valid_json_with_only_facts():
    data = {"facts": [{"id": "f1", "content": "solo"}]}
    loaded = _write(tempfile.gettempdir(), data)
    assert loaded["facts"] == data["facts"]


def test_streamed_dump_handles_non_native_via_default_str():
    # A stray non-JSON-native value must not crash the dump (default=str path).
    class _Weird:
        def __str__(self):
            return "weird-value"
    data = {"facts": [{"id": "f1", "blob": _Weird()}], "meta": _Weird()}
    loaded = _write(tempfile.gettempdir(), data)
    # Coerced to str — no crash, valid JSON.
    assert loaded["facts"][0]["blob"] == "weird-value"
    assert loaded["meta"] == "weird-value"


def test_dump_yields_gil_for_large_fact_set(monkeypatch):
    calls = {"n": 0}
    real_sleep = knowledge.time.sleep

    def counting_sleep(secs):
        if secs == 0:
            calls["n"] += 1
        return real_sleep(secs)

    monkeypatch.setattr(knowledge.time, "sleep", counting_sleep)
    data = {"facts": [{"id": f"f{i}"} for i in range(5000)]}
    _write(tempfile.gettempdir(), data)
    # >2048 facts → at least one GIL-yield (sleep(0)) must have fired.
    assert calls["n"] >= 1, (
        "R-F1668: GIL-yield (time.sleep(0)) never fired during a 5000-fact dump."
    )
