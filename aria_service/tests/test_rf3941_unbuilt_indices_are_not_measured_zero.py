"""R-F3941 — R-F3932 guarded one probe and left its two siblings exposed.

R-F3932 is correct and its reasoning is the right one: in `subsystem_census`,
`None` MEANS NOT LOADED and `0` MEANS MEASURED AND EMPTY. It found the defect live —
`facts: 0` reported at 2552MB RSS on a freshly booted process, because an unhydrated
`knowledge._cache` collapsed into the same 0 as a genuinely empty one — and fixed
the `facts` probe by testing `_cache is None`.

ITS TWO SIBLINGS ON THE SAME READING WERE STILL WRONG. `_topic` and `_content` did a
bare `len(_k._topic_index)` / `len(_k._content_index)`, and those dicts are `{}`
until the indices are built. So on the very reading that prompted R-F3932, all three
numbers were reported as zero and only one of them was honest about it.

The sentinel was already there and already documented — `knowledge.py:99`:

    _index_count: int = -1                  # len(facts) the indices reflect (-1 = unbuilt)

so this is not a new concept, it is an existing one the probes did not consult.

WHY IT MATTERS CONCRETELY, in R-F3932's own words: "2.5GB with zero facts" invites
the conclusion that knowledge is not the memory consumer, when the truth may simply
be that it has not loaded yet — a wrong cause pointing at a wrong fix. Three zeros
make that conclusion look corroborated by three independent probes when in fact none
of them had measured anything.

This is the absence-reads-as-a-measurement class (§1, §22) reproduced inside the
diagnostic written to surface it — which is exactly what R-F3932's own docstring
says about the defect it fixed.
"""
from __future__ import annotations

from aria_service.intel import knowledge as k
from aria_service.intel import memory_leak_detector as mld


# ── the defect, reproduced on each sibling ─────────────────────────────────────

def test_unbuilt_indices_report_none_not_zero(monkeypatch):
    """THE REGRESSION TEST. Indices not yet built must not read as measured-empty."""
    monkeypatch.setattr(k, "_cache", {"facts": []}, raising=False)
    monkeypatch.setattr(k, "_index_count", -1, raising=False)   # unbuilt sentinel
    monkeypatch.setattr(k, "_topic_index", {}, raising=False)
    monkeypatch.setattr(k, "_content_index", {}, raising=False)

    census = mld.subsystem_census()

    assert census["topic_index"] is None, (
        "an UNBUILT topic index must report None, not 0 — 0 claims a measurement "
        "that never happened (R-F3941)")
    assert census["content_index"] is None, (
        "an UNBUILT content index must report None, not 0 (R-F3941)")
    # the sibling R-F3932 already fixed must stay fixed
    assert census["facts"] == 0, "a hydrated-but-empty cache is a real measured 0"


def test_built_but_empty_indices_report_zero(monkeypatch):
    """THE CONTROL. `0` must still be reachable, or the fix would just hide the axis.

    R-F3858 — a probe that answered None unconditionally would pass the test above
    while measuring nothing at all.
    """
    monkeypatch.setattr(k, "_cache", {"facts": []}, raising=False)
    monkeypatch.setattr(k, "_index_count", 0, raising=False)    # BUILT, over 0 facts
    monkeypatch.setattr(k, "_topic_index", {}, raising=False)
    monkeypatch.setattr(k, "_content_index", {}, raising=False)

    census = mld.subsystem_census()

    assert census["topic_index"] == 0, "a BUILT empty index is a genuine 0"
    assert census["content_index"] == 0


def test_populated_indices_report_their_size(monkeypatch):
    """And the ordinary case still measures."""
    monkeypatch.setattr(k, "_cache", {"facts": [1, 2, 3]}, raising=False)
    monkeypatch.setattr(k, "_index_count", 3, raising=False)
    monkeypatch.setattr(k, "_topic_index", {"a": [], "b": []}, raising=False)
    monkeypatch.setattr(k, "_content_index", {"x": {}}, raising=False)

    census = mld.subsystem_census()

    assert census == {**census, "facts": 3, "topic_index": 2, "content_index": 1}


# ── the delta must not compare against a non-measurement ───────────────────────

def test_a_not_loaded_reading_produces_no_delta(monkeypatch):
    """R-F3932's delta rule has to hold for the siblings too.

    Subtracting against None would either crash or invent a number; both are worse
    than saying "not comparable".
    """
    monkeypatch.setattr(mld, "_LAST_REPORT_CENSUS", {}, raising=False)
    monkeypatch.setattr(k, "_cache", None, raising=False)
    monkeypatch.setattr(k, "_index_count", -1, raising=False)

    first = mld.process_memory_report()
    assert first["subsystems"]["topic_index"] is None
    assert first["subsystems_delta_since_last_call"] is None, (
        "no prior reading is not the same as no change")

    # now everything hydrates
    monkeypatch.setattr(k, "_cache", {"facts": [1, 2]}, raising=False)
    monkeypatch.setattr(k, "_index_count", 2, raising=False)
    monkeypatch.setattr(k, "_topic_index", {"a": []}, raising=False)
    monkeypatch.setattr(k, "_content_index", {}, raising=False)

    second = mld.process_memory_report()
    assert "topic_index" not in (second["subsystems_delta_since_last_call"] or {}), (
        "a subsystem that was NOT LOADED on the previous reading has no delta — "
        "reporting one would invent growth that was really just hydration (R-F3941)")


# ── a probe that cannot be measured is omitted, never guessed ──────────────────

def test_a_raising_probe_is_omitted_rather_than_reported_as_a_number(monkeypatch):
    """An unmeasurable subsystem must vanish from the census, not appear as 0.

    Note the sibling honesty fix in `_probe` — an UNEXPECTED TYPE is now omitted
    too, rather than being mapped to None ("not loaded"), which would have claimed
    a subsystem was unhydrated when the probe had returned something nonsensical.
    That branch is defensive and unreachable while every probe returns `len()` or
    None, so it is deliberately not given a contrived test here.
    """
    class _Explodes:
        def __len__(self):
            raise RuntimeError("index is mid-rebuild")

    monkeypatch.setattr(k, "_cache", {"facts": [1]}, raising=False)
    monkeypatch.setattr(k, "_index_count", 1, raising=False)
    monkeypatch.setattr(k, "_topic_index", _Explodes(), raising=False)
    monkeypatch.setattr(k, "_content_index", {"x": {}}, raising=False)

    census = mld.subsystem_census()

    assert "topic_index" not in census, (
        "a probe that could not be measured must be ABSENT, not reported as a "
        "number the caller would treat as real (R-F3941)")
    assert census["content_index"] == 1, "one bad probe must not blind the rest"
