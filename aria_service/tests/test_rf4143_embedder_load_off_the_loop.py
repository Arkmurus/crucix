"""R-F4143 (C-172) — a cold `import transformers` + model load ran ON the event
loop, from a function whose only job is to report numbers.

**Found by reading the one wedge dump that survived C-170/C-171**, not by
guessing. Before assuming C-166's remaining GIL contention was the culprit, the
actual post-fix stall was measured: 1 dump in 27 minutes (down from 21 in one
pre-fix process), and its loop thread read:

```
File ".../pathlib/_local.py", line 546 in read_text
File ".../importlib/metadata/__init__.py", line 915 in read_text
File ".../importlib/metadata/__init__.py", line 1045 in packages_distributions
File ".../transformers/utils/import_utils.py", line 47 in <module>
File "<frozen importlib._bootstrap_external>", line 1023 in exec_module
```

The loop was inside `import transformers`, in `packages_distributions()` —
which walks every installed distribution's metadata off disk. The app frames in
the same dump gave the whole chain:

```
main.py:3754            _proactive_loop
proactive.py:854        daily_briefing_check
reasoning_library:1301  get_stats
reasoning_library:295   _get_embedder
```

`async def get_stats()` contained:

```python
"embedder_available": _get_embedder() is not None,
```

and `_get_embedder` does `import torch`, `from sentence_transformers import
SentenceTransformer`, then **loads the model**. Seconds of blocking work on the
loop, to fill in one boolean.

**Same class as C-99** (`memory_leak_detector`'s synchronous `import torch`,
caught the same way by a 5.25s dump). Second instance, so it is a class, not an
incident — hence the gate below rather than two more one-line fixes.

### The gate had to be made honest before it could be widened

Adding `_get_embedder` to the G4 denylist produced **six** hits, and **four were
false positives**: `await self._get_embedder()` against genuinely `async def`
methods in `llm_eval_framework` and `contamination_check`. An awaited call MUST
be a coroutine — awaiting a sync function is a TypeError — so it cannot be the
sync-CPU-on-the-loop defect this gate exists for. Shipping those four as
"violations" would have forced either bogus edits or an exemption list, and per
§27d a gate that cannot distinguish is worse than no gate.

With awaited calls exempted, exactly the two genuine sync call sites remained,
both in `reasoning_library`. One of them (`:320`) was also **redundant**: it
pre-checked `_get_embedder() is not None` on the loop before offloading to
`_embed`, which performs the identical check on the first line of its own body,
inside the worker thread.
"""
from __future__ import annotations

import ast
import asyncio
import threading

import pytest

from aria_service.intel import reasoning_library as rl

def _g4():
    """Load the gate module by PATH — the tests directory is not a package, so
    a plain `import test_g4_no_sync_cpu_on_loop` only resolves when pytest
    happens to have inserted this directory on sys.path (rootdir-dependent)."""
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).with_name("test_g4_no_sync_cpu_on_loop.py")
    spec = importlib.util.spec_from_file_location("_g4_gate", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


_G4 = _g4()
DENYLIST = _G4.DENYLIST
_Visitor = _G4._Visitor


def _violations(src: str) -> list[str]:
    v = _Visitor("synthetic")
    v.visit(ast.parse(src))
    return v.violations


# ── the gate ────────────────────────────────────────────────────────────────

def test_the_embedder_loader_is_on_the_denylist():
    """It performs a cold transformers import and a model load. Nothing may
    call it inline from an async function."""
    assert "_get_embedder" in DENYLIST


def test_an_AWAITED_call_is_not_flagged():
    """The refinement that made the denylist entry usable. `await x.fn()` is a
    coroutine by construction — awaiting a sync function raises TypeError — so
    it cannot be the blocking defect. Four real call sites take this form."""
    assert not _violations(
        "async def h(self):\n"
        "    return await self._get_embedder()\n"
    ), "an awaited coroutine was flagged as sync CPU on the loop"


def test_a_BARE_call_is_still_flagged():
    """The exemption must not swallow the real thing (R-F3858). This is the
    exact shape of both defects fixed here."""
    assert _violations(
        "async def h():\n"
        "    return _get_embedder() is not None\n"
    ), "the bare sync call is no longer detected — the gate certifies nothing"


def test_the_await_exemption_does_not_leak_to_arguments():
    """`await asyncio.to_thread(_get_embedder)` must stay clean, and a bare call
    nested INSIDE an awaited expression must still be caught — otherwise
    wrapping an offending call in any await would launder it."""
    assert not _violations(
        "import asyncio\n"
        "async def h():\n"
        "    return await asyncio.to_thread(_get_embedder)\n"
    )
    assert _violations(
        "import asyncio\n"
        "async def h():\n"
        "    return await asyncio.sleep(0, result=_get_embedder())\n"
    ), "a bare call hidden inside an awaited expression escaped the gate"


# ── the behaviour ───────────────────────────────────────────────────────────

def test_get_stats_does_not_load_the_embedder_on_the_loop(monkeypatch):
    """The line the wedge dump caught. `get_stats` is a numbers function; it
    must not do a multi-second import on the loop to fill in one boolean."""
    seen: list[bool] = []

    def _probe():
        seen.append(threading.current_thread() is threading.main_thread())
        return None

    monkeypatch.setattr(rl, "_get_embedder", _probe, raising=True)
    out = asyncio.run(rl.get_stats())
    assert seen, "get_stats no longer consults the embedder at all"
    assert not any(seen), "the embedder load still runs on the event loop"
    assert out.get("embedder_available") is False, out


def test_get_stats_still_reports_availability(monkeypatch):
    """Offloading must not turn the field into a constant."""
    monkeypatch.setattr(rl, "_get_embedder", lambda: object(), raising=True)
    out = asyncio.run(rl.get_stats())
    assert out.get("embedder_available") is True, out


def test_embed_async_does_not_touch_the_embedder_on_the_loop(monkeypatch):
    """The second site. The pre-check was redundant — `_embed` makes the same
    check inside the worker thread — but it ran on the loop and could trigger
    the cold load."""
    seen: list[bool] = []

    def _probe():
        seen.append(threading.current_thread() is threading.main_thread())
        return None

    monkeypatch.setattr(rl, "_get_embedder", _probe, raising=True)
    got = asyncio.run(rl._embed_async("hello world"))
    assert got is None
    assert seen, "the embedder was never consulted — the path is not exercised"
    assert not any(seen), "the embedder pre-check still runs on the event loop"


def test_embed_async_still_returns_a_vector_when_the_embedder_works(monkeypatch):
    """Deleting the pre-check must not change the successful path."""
    class _E:
        def encode(self, texts, normalize_embeddings=True):
            return [[0.5, 0.25]]

    monkeypatch.setattr(rl, "_get_embedder", lambda: _E(), raising=True)
    got = asyncio.run(rl._embed_async("hello world"))
    assert got == [0.5, 0.25], got
