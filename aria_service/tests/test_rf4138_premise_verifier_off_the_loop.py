"""R-F4138 (C-170) — the premise verifier runs an O(corpus) scan ON the event
loop, on every chat turn. Found by R-F4137's instrument, not by inspection.

The live reading that produced this, over 8 hours on aria-intel:

```
total_calls=99  total_seconds=225.8  mean=2.28  |  ON_LOOP calls=7  seconds=10.725
   to_thread:autonomous_research        calls=86  secs=186.55  onloop=0
   aria_service.intel.premise_verifier  calls= 7  secs= 10.72  onloop=7 / 10.72
   ...
```

`premise_verifier` is the ONLY caller with on-loop time, and every one of its
calls is on-loop, up to **2.21s each**. That is the loop blocked outright, not
merely contended for — exactly what R-F4137's on-loop/off-loop split was built
to distinguish.

**The comment that made it look safe:**

```python
# Sync + side-effect-free + ~0.5ms hot-path cost (regex + SQLite
# lookup against the 24,955-row canonical cache). Never raises.
_report = _pv.verify_premises(message)          # aria_engine.py:3538
```

That was TRUE when R-F534 wrote it. `verify_officeholder_premise` and
`verify_programme_premise` later grew a `_kb.search_fact_records(q, limit=3)`
call — the O(corpus) ranking scan, now **2.28s mean against 570,254 facts**.
Nobody revisited the comment, so the justification for calling it synchronously
outlived the fact that justified it. Same shape as C-98: a change somewhere else
silently voided a decision that was correct when it was made.

Four async call sites, all sync-calling it:

  * `aria_engine._build_calibrated_system_prompt` — the shared prompt builder,
    reached by BOTH chat paths (`:4305` complete, `:5224` stream), so §13 is
    satisfied by fixing it once. Verified, not assumed.
  * `grounded_reasoner._extract_premises`, `:405`, `_from_premise_verifier`

The fix is `asyncio.to_thread`, matching the pattern `deep_researcher` already
uses at four sites. The scan itself is untouched: C-166 stays open, and this
does not pretend to close it — it stops the loop being blocked by it.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import threading

import pytest

import aria_service.aria_engine as engine
from aria_service.intel import grounded_reasoner as gr
from aria_service.intel import premise_verifier as pv

_WATCHED = ("aria_service/aria_engine.py", "aria_service/intel/grounded_reasoner.py")


class _Report:
    premises: list = []
    has_refuted = False
    has_injection = False
    duration_ms = 0


def _thread_recording_verifier(seen: list):
    def _fn(text, **kw):
        seen.append(threading.current_thread() is threading.main_thread())
        return _Report()
    return _fn


def _bare_async_calls() -> list[str]:
    """Async functions that INVOKE verify_premises directly.

    A `to_thread(pv.verify_premises, ...)` passes the function as an ARGUMENT,
    so it never appears as a Call node — reaching this list means it is being
    invoked inline, on the loop.
    """
    offenders: list[str] = []
    for rel in _WATCHED:
        tree = ast.parse(pathlib.Path(rel).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "verify_premises":
                    offenders.append(f"{rel}:{node.lineno} in async {fn.name}")
    return offenders


def test_the_shared_prompt_builder_does_not_verify_on_the_loop(monkeypatch):
    """The chat hot path. Before the fix this recorded True — the verifier ran
    on the main thread, i.e. the event loop, blocking it for up to 2.21s."""
    seen: list[bool] = []
    monkeypatch.setattr(pv, "verify_premises", _thread_recording_verifier(seen),
                        raising=True)
    monkeypatch.setattr(pv, "format_for_system_prompt", lambda r: "", raising=True)

    asyncio.run(engine._build_calibrated_system_prompt(
        "is CHALLENGER 4 a real programme?"))

    assert seen, "the verifier was never called — this test is not exercising the path"
    assert not any(seen), (
        "premise verification ran ON the event loop thread; it holds the GIL for "
        "~2.3s against 570k facts and blocks every other request")


def test_grounded_reasoner_does_not_verify_on_the_loop(monkeypatch):
    """The second caller. It carried no on-loop time in the live reading only
    because it had not run in that window — the code path is identical."""
    seen: list[bool] = []
    monkeypatch.setattr(pv, "verify_premises", _thread_recording_verifier(seen),
                        raising=True)
    if not hasattr(gr, "GroundedReasoner"):            # pragma: no cover
        pytest.skip("GroundedReasoner not present")
    r = gr.GroundedReasoner()

    async def _no_llm():
        return None
    monkeypatch.setattr(r, "_get_llm", _no_llm, raising=False)

    asyncio.run(r._extract_premises("is CHALLENGER 4 a real programme?"))

    assert seen, "the verifier was never called"
    assert not any(seen), "grounded_reasoner verified premises on the event loop"


def test_every_async_call_site_is_offloaded():
    """The gate. Curating a list of fixed call sites is whack-a-mole; this
    asserts the property across all of them, so a FIFTH async call site added
    later cannot quietly reintroduce the block. Sync callers are exempt by
    construction — they have no loop to block."""
    offenders = _bare_async_calls()
    assert not offenders, (
        "verify_premises is invoked directly inside an async function; it runs "
        "an O(corpus) scan and must go through asyncio.to_thread:\n  "
        + "\n  ".join(offenders))


def test_the_gate_can_actually_fail():
    """A guard that cannot fail is not a guard (R-F3858)."""
    tree = ast.parse("async def f(pv, m):\n    return pv.verify_premises(m)\n")
    found = [n for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef)
             for n in ast.walk(fn) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
             == "verify_premises"]
    assert found, "the detector cannot see a bare async call — it certifies nothing"


def test_the_gate_accepts_the_correct_form():
    """The other half: it must accept `to_thread`, or the gate is unsatisfiable
    and the next person deletes it."""
    tree = ast.parse(
        "import asyncio\n"
        "async def f(pv, m):\n"
        "    return await asyncio.to_thread(pv.verify_premises, m)\n")
    bare = [n for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef)
            for n in ast.walk(fn) if isinstance(n, ast.Call)
            and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
            == "verify_premises"]
    assert not bare, "to_thread form wrongly flagged — the gate would be unsatisfiable"


def test_the_verifier_result_still_reaches_the_prompt(monkeypatch):
    """Offloading must not drop the verdict. R-F534 exists to inject REFUTED and
    INJECTION verdicts into the system prompt; a fix that made the call async
    and lost its output would silently disable a security control."""
    monkeypatch.setattr(pv, "verify_premises", lambda text, **kw: _Report(), raising=True)
    monkeypatch.setattr(pv, "format_for_system_prompt",
                        lambda r: "[PREMISE VERIFIER] REFUTED: test marker",
                        raising=True)
    out = asyncio.run(engine._build_calibrated_system_prompt("anything"))
    assert "REFUTED: test marker" in out, out[:400]


def test_a_raising_verifier_still_does_not_break_chat(monkeypatch):
    """R-F534's contract is non-fatal. to_thread re-raises in the awaiting task,
    so the existing try/except must still cover it."""
    def boom(text, **kw):
        raise RuntimeError("verifier exploded")
    monkeypatch.setattr(pv, "verify_premises", boom, raising=True)
    out = asyncio.run(engine._build_calibrated_system_prompt("anything"))
    assert isinstance(out, str)
