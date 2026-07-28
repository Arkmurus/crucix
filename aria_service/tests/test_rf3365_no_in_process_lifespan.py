"""R-F3365 — suite wedge #5: a second test was booting the real lifespan.

`with TestClient(app)` on aria_service.main ENTERS THE REAL LIFESPAN, starting
ARIA's background subsystems inside the pytest process. They outlive the block —
they are bound to an event loop that is then closed — and the next test that
calls asyncio.run() and reaches the embedder waits forever.

R-F3347 diagnosed exactly this mechanism, named exactly this victim
(test_rf1401_held_out_split_eval:209 -> asyncio.run(run_eval(...)) ->
eval_runner -> asyncio.to_thread(_cosine_score) -> model.encode() ->
GetQueuedCompletionStatus), and fixed ONE entry point: it moved
test_lifespan_smoke.py's lifespan into a subprocess. It did not sweep the others,
so the wedge did not close — it MOVED to the next in-process entry.

BISECTED 2026-07-28 over the 336 files collected before rf1401, halving to a
single class: test_rf1231_agent_signup_vault.py::TestVaultAPI, whose `client`
fixture used `with TestClient(app)`. All three of its tests poison rf1401; the
two-file pair reproduces the hang and either file alone passes. That is the same
signature as wedges #1-#4, and it killed the full-suite run with no summary, so
no complete baseline could be measured.

This test is the mechanism R-F3347 lacked: a NEW in-process lifespan entry now
fails here instead of surfacing months later as an unrelated hang.

DECLARED EXCEPTION — test_rf2379_dd_reports_full_ui_verification.py. It has 11
sites and genuinely needs the started app: without the lifespan those DD routes
fail closed with 401 ("Missing or malformed Authorization header"), so removing
it would change what the test exercises rather than fix a leak. Measured both
ways: 44 passed with the lifespan, 11 failed without it. It sorts AFTER rf1401,
so it is not the wedge #5 poisoner — it is a real remaining hazard for tests
that follow it, recorded here rather than quietly tolerated.
"""
from __future__ import annotations

import ast
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent

# Files allowed to enter the real lifespan in-process, each with the measured
# reason it cannot take the standard remedy. Adding a name here is a deliberate,
# reviewable act — which is the point.
_DECLARED = {
    "test_rf2379_dd_reports_full_ui_verification.py":
        "needs the started app: the DD routes fail closed with 401 without it "
        "(44 passed with lifespan vs 11 failed without). Sorts after rf1401, so "
        "it is not the wedge #5 poisoner. Should move to a subprocess like "
        "test_lifespan_smoke.py (R-F3347) rather than stay in-process.",
}

def _ctx_testclient_count(src: str) -> int:
    """Count `with TestClient(app) ...` statements — the context-manager form is
    the one that runs startup; a bare TestClient(app) does not.

    Deliberately an AST walk, not a regex. The first draft of this guard matched
    on source text and reported the DOCSTRINGS of the very files it had just
    fixed — prose explaining the banned pattern counted as the banned pattern.
    A guard that cannot tell code from a comment about code is not a guard.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "TestClient"
                    and len(call.args) == 1
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id == "app"):
                n += 1
    return n


def _offenders() -> dict[str, int]:
    hits: dict[str, int] = {}
    for path in TESTS.glob("test_*.py"):
        if path.name == pathlib.Path(__file__).name:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if "from aria_service.main" not in src:
            continue
        n = _ctx_testclient_count(src)
        if n:
            hits[path.name] = n
    return hits


def test_rf3365_no_undeclared_test_enters_the_real_lifespan():
    """A new `with TestClient(app)` must fail HERE, not months later as a hang in
    an unrelated test."""
    undeclared = {f: n for f, n in _offenders().items() if f not in _DECLARED}
    assert undeclared == {}, (
        "these tests enter aria_service.main's REAL lifespan in-process, which "
        "leaks background subsystems into the pytest process and hangs the next "
        "test that awaits the embedder (R-F3347, R-F3365): "
        f"{undeclared}. Use TestClient(app) WITHOUT the context manager if the "
        "routes answer unauthenticated, or run the lifespan in a subprocess as "
        "test_lifespan_smoke.py does."
    )


def test_rf3365_the_declared_exceptions_are_still_real():
    """An allowlist that outlives its reason becomes a loophole. If a declared
    file no longer enters the lifespan, it must be removed from _DECLARED."""
    offenders = _offenders()
    stale = [f for f in _DECLARED if f not in offenders]
    assert stale == [], (
        f"these files no longer enter the lifespan and must leave the allowlist: {stale}"
    )


def test_rf3365_the_fixed_files_stay_fixed():
    """The two sites this R-number closed must not regress. Named explicitly so a
    revert fails loudly rather than reopening the wedge."""
    for name in ("test_rf1231_agent_signup_vault.py", "test_rf1411_outcome_wire.py"):
        src = (TESTS / name).read_text(encoding="utf-8", errors="replace")
        assert _ctx_testclient_count(src) == 0, (
            f"{name} re-entered the real lifespan in-process — this is the wedge #5 "
            f"regression, and it will surface as an unrelated hang in "
            f"test_rf1401_held_out_split_eval, not here"
        )
