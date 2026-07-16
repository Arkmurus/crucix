"""R-F2639 + R-F2640 — one measure, one verdict; and gate #6 measures the PIN.

R-F2639 — two Phase A gate aggregators were live at once and DISAGREED:
    main.py            → GET /phase/gates           (unauthenticated)
    routes/aria.py     → GET /api/aria/phase/gates  (Bearer-gated)
  so "is gate #N closed?" had two answers depending on which URL you asked.
  Audited 2026-07-16, the fork was serving:
    * gate #3 as `get_error_count(7) == 0 → closed` — the EXACT vacuous pass
      R-F2622 killed in the other aggregator (an empty/evicted ledger reads as
      a clean week). CLAUDE.md §1 says do not restore it; the fork never lost it.
    * gate #7 closed from DISTINCT CHAT SESSIONS — ARIA's own traffic closing a
      gate §1 defines as operator-owned and uncodeable.
    * gate #6 from a redis key NOTHING in the tree ever wrote → uncloseable.

R-F2640 — gate #6 is "500-Q eval FROZEN", but neither fork measured frozen:
    main passed on `len(golden_set) >= 500` — the SIZE of a mutable, appendable
    list, which cannot detect mutation at all. The honest measure is a PIN:
    count + content hash recorded at freeze time, re-opening on any drift.

Capability tests (CLAUDE.md §3c): every test below drives a REAL route handler
(`main.phase_gates` / `routes.aria.phase_gates_ep`), not a helper, and asserts
the operator-visible verdict. Verified to FAIL against the pre-R-F2639 tree and
pass after — the disagreement tests fail loudest, which is the point.
"""
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    """Run a coroutine WITHOUT destroying the ambient event loop.

    NOT cosmetic: asyncio.run() closes the loop it creates AND clears the
    thread's current loop, so every sibling suite still using the legacy
    `asyncio.get_event_loop().run_until_complete(...)` idiom (test_rf1557 does)
    then dies with "There is no current event loop" — but only when run in the
    same session as this file, which is exactly the kind of order-dependent
    breakage that looks like someone else's bug. A test must not sabotage its
    neighbours; leave a usable loop behind.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# ── Fakes ──────────────────────────────────────────────────────────────────

def _install(monkeypatch, *, golden=None, freeze_record=None, design_partners=0,
             chat_sessions=10, error_count=0, streak=None, heatmap=None,
             composite=0.55, quarantine_total=2, quarantine_open=0):
    """Point every gate source at controlled data.

    Deliberately drives BOTH aggregators' sources — including the ones ONLY the
    old fork read (chat_audit_log, error_log_handler) — so the same fixture
    exercises the old code path and the new one.

    NOTE (R-F2643): this fixture used to seed `crucix:aria:dd:quarantined`, the
    key gate #4 read. Doing so made the FAKE agree with the code and hid the
    fact that NOTHING IN THE TREE EVER WROTE THAT KEY — gate #4 was an
    unconditional pass and the fixture certified it. Same trap as R-F2638's
    fixtures (7/7 green, 0/20 on real data). Gate #4 now drives the real
    run_quarantine surface, and this fixture fakes THAT.
    """
    from aria_service.intel import (
        redis_store, student, autonomy_scorer, error_streak, eval_runner,
        design_partner_tracker, engine_wiring, chat_audit_log, error_log_handler,
        run_quarantine,
    )

    store: dict = {}
    store["crucix:aria:eval:golden_set"] = list(golden or [])
    if freeze_record is not None:
        store[eval_runner.FREEZE_KEY] = freeze_record

    async def _get_json(key, *a, **k):
        return store.get(key)

    async def _set_json(key, value, *a, **k):
        store[key] = value
        return True

    async def _get(key, *a, **k):
        v = store.get(key)
        return v if v is None or isinstance(v, str) else str(v)

    monkeypatch.setattr(redis_store, "get_json", _get_json)
    monkeypatch.setattr(redis_store, "set_json", _set_json)
    monkeypatch.setattr(redis_store, "get", _get)
    # R-F1392 strict readers — gate #6 uses these so a store failure is
    # DISTINGUISHABLE from an absent key. Patch them or the real ones run.
    monkeypatch.setattr(redis_store, "get_json_strict", _get_json)

    async def _closure_summary():
        open_ids = [f"dd_open_{i}" for i in range(quarantine_open)]
        return {
            "total": quarantine_total,
            "closed": quarantine_total - quarantine_open,
            "open": quarantine_open,
            "gate_passes": quarantine_open == 0 and quarantine_total > 0,
            "open_run_ids": open_ids,
        }
    monkeypatch.setattr(run_quarantine, "closure_summary", _closure_summary)

    async def _heatmap():
        return heatmap if heatmap is not None else {
            "heatmap": {"sanctions": {"RU": 0.82, "IR": 0.55}},
            "floor_breach_cells": [{"topic": "sanctions", "region": "IR", "score": 0.55}],
        }
    monkeypatch.setattr(student, "get_regional_heatmap", _heatmap)

    async def _composite():
        return {"composite_score": composite, "confidence": 0.3, "low_confidence": True}
    monkeypatch.setattr(autonomy_scorer, "compute_composite", _composite)

    async def _streak():
        return streak if streak is not None else {
            "consecutive_clean_days": 0,
            "phase_a_gate_3_pass": False,
            "insufficient_history": True,
            "streak_basis": "anchor",
            "clean_since": None,
            "gate_blocked_reason": "insufficient_history",
        }
    monkeypatch.setattr(error_streak, "compute_error_streak", _streak)

    class _FakeTracker:
        def stats(self):
            return {"total": design_partners, "by_status": {"contacted": design_partners},
                    "gate_pass": design_partners >= 4, "gate_target": 4}
    monkeypatch.setattr(design_partner_tracker, "get_tracker", lambda: _FakeTracker())

    # Sources ONLY the pre-R-F2639 fork consulted — populated so the "before"
    # run is realistic (a clean-looking ledger + plenty of chat traffic).
    async def _get_recent(limit=500, *a, **k):
        return [{"session_id": f"s{i}"} for i in range(chat_sessions)]
    monkeypatch.setattr(chat_audit_log, "get_recent", _get_recent, raising=False)

    async def _get_error_count(days=7, *a, **k):
        return error_count
    monkeypatch.setattr(error_log_handler, "get_error_count", _get_error_count, raising=False)

    # Keep the brain wire hermetic.
    monkeypatch.setattr(engine_wiring, "wire_success", lambda **k: None, raising=False)
    monkeypatch.setattr(engine_wiring, "wire_failure", lambda **k: None, raising=False)
    monkeypatch.setattr(eval_runner, "wire_success", lambda **k: None, raising=False)
    monkeypatch.setattr(eval_runner, "wire_failure", lambda **k: None, raising=False)

    # Env: gate #5 fully set unless a test says otherwise.
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")
    monkeypatch.setenv("ARIA_OUTPUT_HARVEST_ENABLED", "1")
    monkeypatch.setenv("ARIA_AUTONOMY_LEVEL", "3")
    return store


def _golden(n: int) -> list[dict]:
    return [{"id": f"gold_{i}", "question": f"q{i}", "expected_answer": f"a{i}"} for i in range(n)]


def _both(monkeypatch, **kw):
    """Drive BOTH real route handlers against identical data."""
    _install(monkeypatch, **kw)
    import aria_service.main as _main
    from aria_service.routes.aria import phase_gates_ep

    main_res = _run(_main.phase_gates())
    fork_res = _run(phase_gates_ep())
    main_gates = main_res["gates"]
    fork_gates = {g["id"]: g for g in fork_res["gates"]}
    return main_gates, fork_gates


_KEY_BY_ID = {
    1: "gate_1_composite", 2: "gate_2_heatmap_floor", 3: "gate_3_zero_errors",
    4: "gate_4_quarantine_closed", 5: "gate_5_env_vars", 6: "gate_6_eval_frozen",
    7: "gate_7_design_partners",
}


def _status(passed):
    return "unknown" if passed is None else ("closed" if passed else "open")


# ── R-F2639: the two endpoints must agree, gate by gate ────────────────────

def test_both_aggregators_agree_on_every_gate(monkeypatch):
    """THE capability test for R-F2639. Same data, same instant, both real
    handlers — every gate must reach the same verdict.

    Pre-R-F2639 this failed on gate #3 (fork fabricated closed from a clean-
    looking ledger) and gate #7 (fork closed from 10 chat sessions while the
    honest tracker had 0 design partners).
    """
    main_gates, fork_gates = _both(
        monkeypatch, golden=_golden(500), design_partners=0,
        chat_sessions=10, error_count=0,
    )
    assert set(fork_gates) == {1, 2, 3, 4, 5, 6, 7}, "all 7 gates present"
    for gid, key in _KEY_BY_ID.items():
        expected = _status(main_gates[key]["pass"])
        actual = fork_gates[gid]["status"]
        assert actual == expected, (
            f"gate #{gid} DISAGREES: /phase/gates says {expected!r} but "
            f"/api/aria/phase/gates says {actual!r} — one measure, one verdict "
            f"(R-F2639). main={main_gates[key]} fork={fork_gates[gid]}"
        )


def test_fork_gate3_no_longer_fabricates_a_clean_week(monkeypatch):
    """R-F2622's honesty must hold on BOTH endpoints.

    An empty/clean-looking error ledger (get_error_count → 0) must NOT close
    gate #3 while the durable streak anchor cannot PROVE 7 clean days. The fork
    used to read exactly that as `closed`.
    """
    main_gates, fork_gates = _both(monkeypatch, golden=_golden(500), error_count=0)
    g3 = fork_gates[3]
    assert g3["status"] != "closed", (
        "gate #3 closed on an unproven streak — the R-F560 vacuous pass is back"
    )
    assert g3["insufficient_history"] is True
    assert "error_streak" in g3["evidence"], "must read the R-F2622 anchor, not get_error_count"
    assert main_gates["gate_3_zero_errors"]["pass"] is False


def test_fork_gate7_cannot_close_from_chat_sessions(monkeypatch):
    """Gate #7 is operator-owned (CLAUDE.md §1). 200 chat sessions and zero
    logged design partners must leave it OPEN — ARIA's own traffic must never
    certify it."""
    main_gates, fork_gates = _both(monkeypatch, golden=_golden(500),
                                   chat_sessions=200, design_partners=0)
    assert fork_gates[7]["status"] == "open", (
        "gate #7 closed from chat sessions — ARIA is certifying an "
        "operator-owned gate with her own traffic"
    )
    assert fork_gates[7]["value"] == 0, "value must be design partners, not sessions"
    assert main_gates["gate_7_design_partners"]["pass"] is False


def test_gate7_closes_only_on_real_logged_partners(monkeypatch):
    """The honest close path still works — 4 logged partners closes it."""
    main_gates, fork_gates = _both(monkeypatch, golden=_golden(500),
                                   chat_sessions=0, design_partners=4)
    assert fork_gates[7]["status"] == "closed"
    assert main_gates["gate_7_design_partners"]["pass"] is True


def test_unmeasurable_stays_unknown_never_open(monkeypatch):
    """A store failure means COULD NOT MEASURE. Rendering that as `open` would
    assert a failure that was never measured (the mirror of R-F560's sin)."""
    main_gates, fork_gates = _both(
        monkeypatch, golden=_golden(500),
        streak={"error": "anchor_fetch_failed:StoreReadError",
                "consecutive_clean_days": None},
    )
    assert main_gates["gate_3_zero_errors"]["pass"] is None
    assert main_gates["gate_3_zero_errors"]["measurable"] is False
    assert fork_gates[3]["status"] == "unknown", "unmeasurable must not render as open"


def test_gate5_checks_autonomy_level_on_both(monkeypatch):
    """R-F2639 kept the FORK's stricter 3-var gate #5. main.py checked only 2
    and omitted ARIA_AUTONOMY_LEVEL — consolidating onto main wholesale would
    have LOOSENED a gate that currently reads PASS."""
    _install(monkeypatch, golden=_golden(500))
    monkeypatch.delenv("ARIA_AUTONOMY_LEVEL", raising=False)
    monkeypatch.delenv("AUTONOMY_LEVEL", raising=False)
    import aria_service.main as _main
    from aria_service.routes.aria import phase_gates_ep

    main_res = _run(_main.phase_gates())
    fork_res = _run(phase_gates_ep())
    g5_main = main_res["gates"]["gate_5_env_vars"]
    g5_fork = next(g for g in fork_res["gates"] if g["id"] == 5)
    assert g5_main["pass"] is False, "gate #5 must open when ARIA_AUTONOMY_LEVEL is unset"
    assert "ARIA_AUTONOMY_LEVEL" in g5_main["value"]["missing"]
    assert g5_fork["status"] == "open"


# ── R-F2640: gate #6 measures the PIN, not the size ────────────────────────

def test_gate6_500_entries_unpinned_is_not_frozen(monkeypatch):
    """THE R-F2640 capability test. 500 entries and no pin: main.py used to
    close gate #6 on `len >= 500`. Size is not frozenness — a set you can still
    append to is not a benchmark."""
    main_gates, fork_gates = _both(monkeypatch, golden=_golden(500), freeze_record=None)
    g6 = main_gates["gate_6_eval_frozen"]
    assert g6["pass"] is False, "500 unpinned entries must NOT close gate #6"
    assert g6["reason"] == "not_frozen"
    assert g6["value"] == 500, "the live count is still reported honestly"
    assert fork_gates[6]["status"] == "open"


def test_gate6_closes_when_pinned_and_reopens_on_drift(monkeypatch):
    """Freeze → closed. Then mutate the set → the pin no longer matches → the
    gate RE-OPENS. That re-opening is the whole point of freezing."""
    from aria_service.intel import eval_runner
    import aria_service.main as _main

    store = _install(monkeypatch, golden=_golden(500))

    # Operator pins it — the real close path.
    res = _run(eval_runner.freeze_golden_set(frozen_by="operator"))
    assert res["ok"] is True, res
    assert res["count"] == 500

    g6 = _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]
    assert g6["pass"] is True, f"a pinned, intact 500-Q set closes gate #6: {g6}"
    assert g6["reason"] == "frozen_and_intact"
    assert g6["frozen_by"] == "operator"

    # Drift: one more question sneaks into the "frozen" benchmark.
    store["crucix:aria:eval:golden_set"] = _golden(501)
    g6b = _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]
    assert g6b["pass"] is False, "a mutated set must re-open gate #6"
    assert g6b["drifted"] is True
    assert g6b["reason"] == "drifted_from_pin"


def test_gate6_reopens_when_an_entry_is_reworded(monkeypatch):
    """Drift is by CONTENT, not just count — rewording an expected_answer keeps
    the count at 500 but changes the benchmark."""
    from aria_service.intel import eval_runner
    import aria_service.main as _main

    store = _install(monkeypatch, golden=_golden(500))
    _run(eval_runner.freeze_golden_set())
    assert _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]["pass"] is True

    mutated = _golden(500)
    mutated[7]["expected_answer"] = "silently reworded"
    store["crucix:aria:eval:golden_set"] = mutated
    g6 = _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]
    assert g6["pass"] is False, "same count, different content — must re-open"
    assert g6["drifted"] is True
    assert g6["value"] == 500


def test_gate6_refuses_to_pin_below_target(monkeypatch):
    """Freezing 12 questions must not 'close' a 500-Q gate."""
    from aria_service.intel import eval_runner
    import aria_service.main as _main

    _install(monkeypatch, golden=_golden(12))
    res = _run(eval_runner.freeze_golden_set())
    assert res["ok"] is False
    assert "12" in res["reason"]
    g6 = _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]
    assert g6["pass"] is False


def test_gate6_ordering_is_not_drift(monkeypatch):
    """The hash sorts by id — a reordered set is the same benchmark."""
    from aria_service.intel import eval_runner
    import aria_service.main as _main

    store = _install(monkeypatch, golden=_golden(500))
    _run(eval_runner.freeze_golden_set())
    store["crucix:aria:eval:golden_set"] = list(reversed(_golden(500)))
    g6 = _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]
    assert g6["pass"] is True, "reordering is not a content change"
    assert g6["drifted"] is False


def test_gate6_unmeasurable_when_store_fails(monkeypatch):
    """A store failure is not 'measured, and it is not frozen'.

    Patches the STRICT reader, which is the one that actually raises on a store
    failure. The plain get_json() swallows errors and returns None
    (redis_store.py:299-303), so a test that patches get_json to raise proves
    nothing about production — that was the earlier version of this test, and it
    gave false confidence in a dead branch.
    """
    from aria_service.intel import redis_store, eval_runner
    import aria_service.main as _main

    _install(monkeypatch, golden=_golden(500))

    async def _boom(key, *a, **k):
        raise redis_store.StoreReadError("state_store: connection wedged")
    monkeypatch.setattr(redis_store, "get_json_strict", _boom)

    fs = _run(eval_runner.get_freeze_status())
    assert fs["measurable"] is False
    assert fs["gate_pass"] is None
    g6 = _run(_main.phase_gates())["gates"]["gate_6_eval_frozen"]
    assert g6["pass"] is None, "could-not-measure must not read as a failure"
    assert g6["measurable"] is False


def test_gate6_uses_strict_reads_so_a_wedged_store_cannot_claim_zero(monkeypatch):
    """Regression guard for the real R-F2277 failure mode: a wedged store must
    NOT produce the confident sentence 'golden set has 0 entries but has never
    been pinned'. If someone swaps get_json_strict back to get_json, the plain
    reader returns None → live_count 0 → that false statement ships."""
    from aria_service.intel import redis_store, eval_runner

    _install(monkeypatch, golden=_golden(500))

    async def _wedged(key, *a, **k):
        raise redis_store.StoreReadError("state_store: SELECT timed out")
    monkeypatch.setattr(redis_store, "get_json_strict", _wedged)
    # The swallowing reader still "works" — proving the two are different.
    fs = _run(eval_runner.get_freeze_status())
    assert fs.get("live_count") is None, "must not report a count it never read"
    assert "0 entries" not in str(fs.get("detail") or "")


# ── R-F2643: gate #4 was a permanently fabricated pass ─────────────────────

def test_gate4_fails_when_quarantined_runs_are_open(monkeypatch):
    """THE R-F2643 capability test.

    Gate #4 read `crucix:aria:dd:quarantined` — a key with NO WRITER anywhere in
    the tree. get_json returns None for an absent key AND swallows store errors,
    so `[] -> 0 -> pass=True` was unconditional: the gate could not fail. With
    open (un-investigated) quarantined runs the gate MUST fail.
    """
    main_gates, fork_gates = _both(
        monkeypatch, golden=_golden(500), quarantine_total=5, quarantine_open=3)
    g4 = main_gates["gate_4_quarantine_closed"]
    assert g4["pass"] is False, "3 open quarantined runs must FAIL gate #4"
    assert g4["value"] == 3
    assert g4["open_run_ids"] == ["dd_open_0", "dd_open_1", "dd_open_2"]
    assert "run_quarantine" in g4["source"], "must read the real quarantine surface"
    assert fork_gates[4]["status"] == "open"


def test_gate4_closes_only_when_all_investigated(monkeypatch):
    """The honest close: quarantined runs exist and are ALL investigated."""
    main_gates, fork_gates = _both(
        monkeypatch, golden=_golden(500), quarantine_total=5, quarantine_open=0)
    assert main_gates["gate_4_quarantine_closed"]["pass"] is True
    assert main_gates["gate_4_quarantine_closed"]["closed"] == 5
    assert fork_gates[4]["status"] == "closed"


def test_gate4_does_not_read_the_phantom_key():
    """Structural: the dead key must never come back. If gate #4 is re-pointed
    at `crucix:aria:dd:quarantined`, nothing writes it, so the gate silently
    becomes an unconditional pass again."""
    import aria_service.intel.phase_gates  # noqa: F401 — resolve the real path
    import ast
    import pathlib

    src = pathlib.Path(
        aria_service.intel.phase_gates.__file__
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Only flag the key as an EXECUTABLE string literal. Comments and
    # docstrings are excluded on purpose: this module documents the phantom key
    # at length so the mistake isn't repeated, and prose about a bug must not
    # register as the bug.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)

    used = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "crucix:aria:dd:quarantined" in n.value
        and n.value not in docstrings
    ]
    assert not used, (
        f"gate #4 must not read the writer-less phantom key (R-F2643) — found {used}"
    )


# ── Summary honesty ────────────────────────────────────────────────────────

def test_all_pass_is_false_when_any_gate_is_unmeasurable(monkeypatch):
    """`all_pass` means PHASE A IS EXITABLE — it must not read True while gates
    went unmeasured. The old form (passed == len(measurable)) could report
    all_pass on the PUBLIC endpoint with half the gates dark."""
    import aria_service.main as _main

    _install(monkeypatch, golden=_golden(500), heatmap={"heatmap": {}, "floor_breach_cells": []},
             streak={"error": "anchor_fetch_failed:StoreReadError"})
    res = _run(_main.phase_gates())
    summary = res["summary"]
    assert summary["unmeasurable"] >= 2, "gates #2 and #3 are unmeasurable here"
    assert summary["all_pass"] is False, (
        "all_pass must require every gate measured AND passing"
    )


# ── Auth: the freeze pin is operator-only ──────────────────────────────────

def test_freeze_routes_are_operator_only():
    """The pin decides Phase A gate #6. The shared SERVICE token (held by
    aria-web + aria-wa) must never reach it — only ARIA_OPERATOR_TOKEN. This
    asserts the path regex directly so a future prefix change can't silently
    drop these routes to service tier."""
    from aria_service.routes.aria import _OPERATOR_ONLY_RE as R

    assert R.search("/api/aria/eval/golden/freeze"), "freeze must be operator-only"
    assert R.search("/api/aria/eval/golden/unfreeze"), "unfreeze must be operator-only"


# ── Structural: no second aggregator may reappear ──────────────────────────

def test_neither_endpoint_measures_anything_itself(monkeypatch):
    """R-F2639 regression guard: both handlers must go through
    intel.phase_gates.compute_phase_gates(). If someone re-adds local
    measurement to either fork, this fails — the drift can't silently return.
    """
    import aria_service.main as _main
    import aria_service.routes.aria as _aria
    from aria_service.intel import phase_gates as _pg

    calls = {"n": 0}
    real = _pg.compute_phase_gates

    async def _counting():
        calls["n"] += 1
        return await real()

    _install(monkeypatch, golden=_golden(500))
    monkeypatch.setattr(_pg, "compute_phase_gates", _counting)

    _run(_main.phase_gates())
    assert calls["n"] == 1, "/phase/gates must render the canonical measure"
    _run(_aria.phase_gates_ep())
    assert calls["n"] == 2, "/api/aria/phase/gates must render the canonical measure"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
