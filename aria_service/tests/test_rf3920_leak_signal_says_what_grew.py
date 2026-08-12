"""R-F3920 — the leak detector announced growth it could not diagnose.

MEASURED LIVE 2026-08-12, across 15 monitoring cycles:

    [memory_leak_detector] LEAK DETECTED — growth=114.84MB/interval, current=6681.6MB
    [memory_leak_detector] RSS 6690.5MB exceeds threshold 6144MB — triggering GC
    [memory_leak_detector] GC freed 0.0MB (RSS: 6690.5MB → 6690.5MB)

`GC freed 0.0MB` every pass means the memory is LIVE — reachable state, not garbage —
so the detector's one remedy cannot work by construction. And the gap it recorded
carried only the rate and the totals, so neither a human nor the autonomous coder
(which DID pick that gap up) had anything to act on.

An alarm that cannot be diagnosed is the same shape as the Node gate that refused
without saying why (R-F3903): correct, and useless.

WHY NOT gc.get_objects()/tracemalloc: a generic object histogram on a 6.7GB process
walks millions of tracked objects and can block for seconds. This runs on the
monitoring loop, and this repo has already paid for event-loop starvation twice
(R-F2144, R-F2200). A targeted len() census of ARIA's own growth candidates is O(1)
per probe and MORE actionable: "facts +8,214" names a subsystem; "dict +190,000"
does not.
"""
from __future__ import annotations

from aria_service.intel import memory_leak_detector as mld


def _detector():
    for name in ("MemoryLeakDetector", "LeakDetector"):
        if hasattr(mld, name):
            return getattr(mld, name)()
    raise AssertionError(f"detector class not found in {dir(mld)}")


def test_the_first_census_says_it_has_no_delta_yet():
    """An absolute size is not evidence of a leak. Saying so beats implying it."""
    d = _detector()
    out = d._subsystem_census_delta()
    assert "first sample" in out, out
    assert "facts=" in out


def test_the_second_census_reports_the_DELTA():
    """THE CAPABILITY: what changed between detections is the thing to chase."""
    d = _detector()
    d._subsystem_census_delta()                 # prime
    d._last_census = {"facts": 100, "topic_index": 10}
    out = d._subsystem_census_delta()
    assert "(+" in out or "(-" in out, f"no delta rendered: {out}"


def test_one_broken_probe_does_not_blind_the_census(monkeypatch):
    """A diagnosis that fails closed on a single bad probe is no diagnosis."""
    import aria_service.intel.knowledge as k
    monkeypatch.delattr(k, "_topic_index", raising=False)

    d = _detector()
    out = d._subsystem_census_delta()
    assert "facts=" in out, "a missing subsystem must not take the whole census down"


def test_the_census_never_raises(monkeypatch):
    """Diagnosis must never break the monitor it serves."""
    import aria_service.intel.knowledge as k
    monkeypatch.setattr(k, "_cache", "not-a-dict", raising=False)
    d = _detector()
    assert isinstance(d._subsystem_census_delta(), str)


def test_the_leak_signal_carries_the_census():
    """Pinned at the emission site: the gap the coder receives must say WHAT grew."""
    from aria_service.tests._source_probe import function_source

    src = function_source(mld, "_emit_leak_signal") if hasattr(mld, "_emit_leak_signal") else ""
    if not src:
        from aria_service.tests._source_probe import module_source
        src = module_source(mld)
        src = src[src.find("_emit_leak_signal"):]
    assert "_subsystem_census_delta" in src, (
        "the leak gap must carry a subsystem census — without it the alarm is "
        "undiagnosable and the coder has nothing to work with (R-F3920)")


def test_the_census_does_not_walk_the_object_graph():
    """The safety property. gc.get_objects()/tracemalloc on a 6.7GB process can
    block the monitoring loop for seconds — the R-F2144/R-F2200 starvation class.

    Checked by AST, not substring: this function's own DOCSTRING names those APIs to
    explain why they are avoided, and a text scan flags the explanation as the
    offence. That is the R-F3888 defect (a guard matching prose instead of code),
    which cost a blocked commit earlier today.
    """
    import ast
    import textwrap

    from aria_service.tests._source_probe import function_source

    tree = ast.parse(textwrap.dedent(function_source(mld, "_subsystem_census_delta")))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            called.add(node.attr)
        elif isinstance(node, ast.Name):
            called.add(node.id)

    for banned in ("get_objects", "get_referrers", "tracemalloc"):
        assert banned not in called, (
            f"{banned} traverses the whole heap on the monitoring loop — use "
            f"bounded len() probes (R-F3920)")
