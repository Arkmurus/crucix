"""R-F4154 (C-176) — the coder advertised "starting up" to the agent registry
for its entire life.

Found by a 360 ecosystem review, in the registry itself:

```
aria_coder | current_task: "starting up" | status: active | heartbeat_age: 11.4s
registered_at 1787063949  ->  last_heartbeat 1787067014   = 3,065s
```

**51 minutes of "starting up"** — on a coder that was in fact perfectly healthy
(`brain/stats`: `aria_coder total=85, ok=85, fail=0`).

`current_task` was written exactly once, at registration, as the literal string
`"starting up"` (`coder_entrypoint.py:327`), and **nowhere else in the tree**.
The heartbeat tick passed no task, so the field could never hold anything else.

**It is not cosmetic.** R-F1160 registers the coder here precisely *"so other
agents (gap_detector, research_engine, Claude Code sessions) know the coder is
active and what it's working on"*, and gap claiming (`claim_gap`) is coordinated
through this same registry. A field that can only ever hold its initial value is
an absence dressed as a measurement — the defect class this register records
more than any other — and it cost real time in the review that found it, where
"starting up for 51 minutes" read as a hung loop and had to be retracted.

The registry already supported the fix: `tick_heartbeat` accepts
`current_task`, and `update_task` exists. Neither was ever called.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from aria_service.autonomous import self_coder as sc

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_phase():
    before = dict(sc._CODER_PHASE)
    yield
    sc._CODER_PHASE.clear()
    sc._CODER_PHASE.update(before)


def test_the_phase_is_publishable_and_readable():
    sc._set_phase("scanning", "gap detection cycle")
    assert sc._CODER_PHASE["phase"] == "scanning"
    assert sc._CODER_PHASE["detail"] == "gap detection cycle"


def test_the_label_reflects_the_published_phase():
    from aria_service.autonomous.coder_entrypoint import _coder_task_label
    sc._set_phase("scanning", "gap detection cycle")
    assert "scanning" in _coder_task_label()
    sc._set_phase("error", "boom")
    assert "error" in _coder_task_label()


def test_the_label_never_raises_even_if_the_phase_is_unreadable(monkeypatch):
    """The heartbeat must keep ticking. A missing heartbeat is read as a
    blackout (R-F1146), so a broken status label must not escalate into a false
    recovery trigger."""
    from aria_service.autonomous import coder_entrypoint as ce
    monkeypatch.setattr(sc, "_CODER_PHASE", None, raising=False)
    out = ce._coder_task_label()
    assert isinstance(out, str) and out


def test_set_phase_never_raises_on_junk():
    """It is called from the loop's exception handler; it must not be able to
    raise there."""
    sc._set_phase(object(), object())          # type: ignore[arg-type]
    assert isinstance(sc._CODER_PHASE.get("phase"), str)


def test_the_label_is_bounded():
    """It goes into a registry row other agents read; an unbounded exception
    string must not become the payload."""
    from aria_service.autonomous.coder_entrypoint import _coder_task_label
    sc._set_phase("error", "x" * 5000)
    assert len(_coder_task_label()) <= 200


def test_the_heartbeat_actually_passes_a_task():
    """The regression itself: a tick with no `current_task` leaves the field
    frozen at whatever registration set. Asserted on the AST so a future edit
    that drops the argument fails here rather than in production silence."""
    src = (_ROOT / "autonomous" / "coder_entrypoint.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ticks = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) == "tick_heartbeat")
        and any(isinstance(a, ast.Constant) and a.value == "aria_coder" for a in n.args)
    ]
    registry_ticks = [n for n in ticks if n.keywords]
    assert registry_ticks, (
        "the agent-registry heartbeat passes no current_task — the coder will "
        "advertise its registration-time label forever")
    assert any(kw.arg == "current_task" for n in registry_ticks for kw in n.keywords)


def test_the_loop_publishes_a_phase_at_each_boundary():
    """Scanning, idle and error must each be reachable, or the label is simply a
    different constant."""
    src = (_ROOT / "autonomous" / "self_coder.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    run_forever = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_forever"), None)
    assert run_forever is not None, "run_forever is gone"

    published = {
        a.value
        for call in ast.walk(run_forever)
        if isinstance(call, ast.Call)
        and (getattr(call.func, "id", None) == "_set_phase")
        for a in call.args[:1] if isinstance(a, ast.Constant)
    }
    for expected in ("scanning", "idle", "error"):
        assert expected in published, (
            f"the coder never publishes a {expected!r} phase; the registry would "
            f"show a constant again. published={sorted(published)}")


def test_no_heartbeat_or_update_call_hardcodes_the_placeholder():
    """The point of the change, as a property rather than a word count.

    A first draft of this test counted occurrences of the literal in the file
    and failed at 4 — because three of them were in the comments EXPLAINING the
    defect. Counting prose is not testing code. What actually matters is that
    no live status write pins the placeholder: registration may set it as the
    initial value, but a heartbeat or update must never re-assert it.
    """
    src = (_ROOT / "autonomous" / "coder_entrypoint.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if getattr(call.func, "attr", None) not in ("tick_heartbeat", "update_task"):
            continue
        for node in list(call.args) + [kw.value for kw in call.keywords]:
            if isinstance(node, ast.Constant) and node.value == "starting up":
                raise AssertionError(
                    f"{call.lineno}: a live status write hardcodes 'starting up' — "
                    "the registry would be frozen again")
