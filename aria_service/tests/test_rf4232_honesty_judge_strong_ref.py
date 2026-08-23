"""R-F4232 / C-212 — the honesty axis depended on a garbage-collectable task.

## Why this matters more than a normal fire-and-forget leak

`honesty_rate` is 25% of the Phase A gate #1 composite, and Phase A is the
**Honesty foundation**. R-F4231 (C-211) fixed the gate's side of this — it no
longer certifies while that axis is dark. This is the other side: why the axis is
dark in the first place.

Measured live 2026-08-22 through the running server (§17):

    GET /api/aria/honesty/stats
    -> total 55, by_status {ok 41, no_claims 10, judge_failed 4},
       by_status_24h {}, scored_sample_size 0, lifetime_sample_size 41

**55 judgments in the platform's entire lifetime.**

## The defect, and it is a named house class

`routes/aria.py` spawned the judge as a bare
`_aio.create_task(_judge_bg())` with **no stored reference**. asyncio keeps only
a WEAK reference to a task, so under a saturated loop it can be garbage-collected
before it ever runs. This repo has already paid for that exact class SIX times
and documents it at `_CODER_BG_TASKS`:

> "a fire-and-forget `_run_fix()` task with no stored ref was garbage-collected
>  before it ever ran fix_gap — so operator /coder/request calls queued (returned
>  a fix_id) but NEVER executed"

and again at `_ASYNC_JOB_TASKS` (R-F1377, WhatsApp doc review "hitting timeout").
Both were fixed by pinning the task. The honesty judge — the only writer of the
signal Phase A is named after — was left on the unfixed pattern.

## R-F2420 already diagnosed this and fixed the wrong half

Its comment in the same file says, verbatim, that the "ROOT CAUSE of honesty
never reaching scored_n>=5" is that honesty "was recorded ONLY in the
fire-and-forget background _judge_bg task (create_task, no strong ref) — which
loses writes when the loop is saturated". Its remedy was to ALSO record
synchronously — but only inside the `_grounding_judgment is not None` branch,
which is reachable only when `ARIA_GROUNDING_MARKERS_ENABLED` is on, and that flag
is **default OFF** ("Kept OFF until the operator has reviewed the diff").

So in production the sync path does not run, and every honesty judgment goes
through the one path R-F2420 itself identified as lossy. A correct diagnosis, a
workaround applied to a disabled branch, and the live path untouched.

The loop was measurably saturated for months — C-95 recorded `/health.loop`
`starved`, p95 **3264 ms**, until 2026-08-14 — which is exactly the condition
under which an unreferenced task is dropped.

## The fix

Reuse `_hold_job_task` (R-F1377). No new mechanism, one line, house idiom.
"""
from __future__ import annotations

import asyncio
import gc

import pytest

from ._source_probe import module_code, module_source


# The writers of Phase A gate #1's two measurable signals. `/chat` records
# verification synchronously (R-F2420), so its background task is the judge only;
# the stream fork (R-F2364, §13) spawns both.
_SIGNAL_WRITERS = {
    "_judge_bg":         "honesty (chat)",
    "_r2364_judge_bg":   "honesty (stream, §13 mirror)",
    "_r2364_verify_bg":  "verification (stream, §13 mirror)",
}


def _bare_spawns_in(source: str) -> dict[str, str]:
    """The detector, over ANY source — so it can be proven on a known-bad sample."""
    import ast

    tree = ast.parse(source)
    bare: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "create_task"):
            continue
        for arg in node.value.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                bare[arg.func.id] = "discarded"
    return bare


def _bare_spawns() -> dict[str, str]:
    """AST: coroutine names passed to a create_task whose RESULT IS DISCARDED.

    Deliberately not a line/substring scan. R-F3597/§16 records what line-anchored
    source assertions cost here, and the first cut of this very guard went brittle
    the moment the fix wrapped the call across lines — reporting the defect as
    fixed-and-then-broken when only the formatting had changed. An `ast.Expr`
    whose value is the `create_task(...)` call IS the definition of "nobody kept
    the reference".
    """
    import ast
    from aria_service.routes import aria as aria_routes

    # RAW source, not `module_code`: that strips comments AND docstrings, and a
    # class whose body is only a docstring (`_DDAdmissionBusy`) then has an empty
    # body, so the stripped text does not re-parse. Comments carry no AST nodes,
    # so stripping buys nothing here anyway.
    tree = ast.parse(module_source(aria_routes))
    bare: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "create_task"):
            continue
        for arg in node.value.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                bare[arg.func.id] = "discarded"
    return bare


def _pinned_spawns() -> set[str]:
    """Coroutine names whose create_task result is handed to _hold_job_task."""
    import ast
    from aria_service.routes import aria as aria_routes

    tree = ast.parse(module_source(aria_routes))
    pinned: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_hold_job_task"):
            continue
        for held in node.args:
            if not (isinstance(held, ast.Call)
                    and isinstance(held.func, ast.Attribute)
                    and held.func.attr == "create_task"):
                continue
            for arg in held.args:
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                    pinned.add(arg.func.id)
    return pinned


class TestTheSpawnSiteHoldsAReference:
    """The assertions that FAIL against the pre-fix tree."""

    @pytest.mark.parametrize("coro,what", sorted(_SIGNAL_WRITERS.items()))
    def test_gate1_signal_writers_are_pinned(self, coro, what):
        from aria_service.routes import aria as aria_routes

        assert coro in module_code(aria_routes), (
            f"{coro} has moved or been renamed — re-point this guard rather than "
            f"deleting it (a guard whose subject vanished silently certifies)")
        assert coro not in _bare_spawns(), (
            f"{coro} ({what}) is spawned by a create_task whose result is "
            f"DISCARDED. asyncio keeps only a WEAK reference, so under a saturated "
            f"loop the task can be garbage-collected before it runs — the "
            f"R-F1363/_CODER_BG_TASKS and R-F1377/_ASYNC_JOB_TASKS class, already "
            f"paid for twice in this file. This one writes a Phase A gate #1 "
            f"signal. Pin it with _hold_job_task()."
        )

    @pytest.mark.parametrize("coro", sorted(_SIGNAL_WRITERS))
    def test_it_uses_the_existing_helper_not_a_new_mechanism(self, coro):
        """A seventh bespoke task set is how this class keeps recurring.

        AST again, not a text window: the first cut searched for the coroutine
        NAME and found its `async def` — sixty lines from the spawn — so it was
        asserting against the wrong site entirely.
        """
        assert coro in _pinned_spawns(), (
            f"{coro} should be pinned with the established "
            f"_hold_job_task/_ASYNC_JOB_TASKS helper (R-F1377), not stored in a "
            f"new bespoke set")

    def test_the_guard_can_still_see_a_bare_spawn(self):
        """A guard that cannot detect the defect is not a guard (R-F3858).

        This USED to assert that bare spawns still existed in routes/aria.py —
        C-212 left nine of them as known debt, and their presence doubled as
        proof the detector worked. R-F4237 (C-217) then pinned all seventeen, so
        that proof evaporated: the assertion would have gone green because the
        defect is gone, which is indistinguishable from green because the
        detector broke. Proven on a SYNTHETIC known-bad sample instead, which
        does not require production to carry debt forever.
        """
        bad = _bare_spawns_in(
            "import asyncio\n"
            "async def _f():\n"
            "    pass\n"
            "def g():\n"
            "    asyncio.create_task(_f())\n"
        )
        assert "_f" in bad, (
            "the AST detector no longer recognises a bare create_task — it is "
            "now certifying an absence")

        good = _bare_spawns_in(
            "import asyncio\n"
            "async def _f():\n"
            "    pass\n"
            "def g():\n"
            "    _hold_job_task(asyncio.create_task(_f()))\n"
        )
        assert "_f" not in good, (
            "the detector flags a PINNED spawn — it would fail on correct code")

    def test_no_bare_spawn_remains_in_the_module(self):
        """R-F4237 (C-217) — the debt C-212 recorded is now closed.

        Seventeen bare `create_task` expression statements existed; C-212 said
        twelve, because that number came from a grep and an AST sweep found five
        more. Every one is now pinned with `_hold_job_task`. This assertion is
        what stops the class returning one call site at a time.
        """
        bare = _bare_spawns()
        assert not bare, (
            f"bare create_task(s) reintroduced in routes/aria.py: {sorted(bare)}. "
            f"asyncio keeps only a WEAK reference, so under a saturated loop "
            f"these are garbage-collected before they run (R-F1363/R-F1377). "
            f"Wrap with _hold_job_task(...).")


class TestThePinningMechanismActuallySurvivesGC:
    """Capability: prove the helper does the thing the fix relies on.

    Deliberately NOT 'spawn a bare task and assert it gets collected' — whether
    CPython collects a given object is not deterministic, and a flaky guard is
    worse than none. What IS deterministic is that a pinned task holds a live
    reference across a forced collection and still completes.
    """

    def test_a_pinned_task_completes_after_its_local_ref_is_dropped(self):
        from aria_service.routes import aria as aria_routes

        ran: list[str] = []

        async def _drive():
            async def _work():
                await asyncio.sleep(0)
                ran.append("yes")

            t = asyncio.create_task(_work())
            aria_routes._hold_job_task(t)
            assert t in aria_routes._ASYNC_JOB_TASKS, (
                "the task must be held while pending — that IS the strong ref")
            del t                      # drop the only local reference
            gc.collect()
            gc.collect()
            # Nothing local points at it now; only _ASYNC_JOB_TASKS does.
            for _ in range(10):
                await asyncio.sleep(0)

        asyncio.run(_drive())
        assert ran == ["yes"], (
            "a pinned task must still run after its local reference is dropped "
            "and a collection is forced")

    def test_the_holder_self_cleans_so_it_cannot_grow_unbounded(self):
        from aria_service.routes import aria as aria_routes

        async def _drive():
            async def _work():
                await asyncio.sleep(0)

            before = len(aria_routes._ASYNC_JOB_TASKS)
            t = asyncio.create_task(_work())
            aria_routes._hold_job_task(t)
            await t
            for _ in range(5):
                await asyncio.sleep(0)
            return before, len(aria_routes._ASYNC_JOB_TASKS)

        before, after = asyncio.run(_drive())
        assert after == before, (
            "the done-callback must discard the ref; an ever-growing holder set "
            "would be a leak of its own")


class TestTheWorkaroundBranchIsNotTheLivePath:
    """Pin WHY the sync path does not cover this: it is behind a default-OFF flag.

    If someone later reads R-F2420's comment and concludes honesty is already
    safe, this says otherwise in one assertion.
    """

    def test_grounding_markers_default_off(self, monkeypatch):
        from aria_service.routes import aria as aria_routes

        monkeypatch.delenv("ARIA_GROUNDING_MARKERS_ENABLED", raising=False)
        assert aria_routes._grounding_markers_enabled() is False, (
            "R-F2420's synchronous honesty write lives inside the "
            "`_grounding_judgment is not None` branch, which this flag gates. "
            "With it OFF — the default — the ONLY honesty writer in production is "
            "the background task, so that task must be pinned.")
