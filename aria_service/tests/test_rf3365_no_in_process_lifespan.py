"""R-F3365 — suite wedge #5: a test was booting the real lifespan in-process.
R-F3370 — and this guard's first shipped version could not tell which `app`.

`with TestClient(app)` on **aria_service.main's** app ENTERS THE REAL LIFESPAN,
starting ARIA's background subsystems inside the pytest process. They outlive the
block — bound to an event loop that is then closed — and the next test that calls
asyncio.run() and reaches the embedder waits forever.

R-F3347 diagnosed exactly this mechanism and named exactly this victim
(test_rf1401_held_out_split_eval:209 -> asyncio.run(run_eval(...)) ->
eval_runner -> asyncio.to_thread(_cosine_score) -> model.encode() ->
GetQueuedCompletionStatus), then fixed ONE entry point by moving
test_lifespan_smoke.py's lifespan into a subprocess. It did not sweep the others,
so the wedge did not close — it MOVED.

BISECTED 2026-07-28 over the 336 files collected before rf1401, halving to a
single class: test_rf1231_agent_signup_vault.py::TestVaultAPI. Both real
offenders (rf1231, rf1411) now build the client WITHOUT the context manager.

★ R-F3370 — WHAT THIS GUARD GOT WRONG, and why it matters more than the fix.

The first shipped version matched any `with TestClient(app)` in a file that
mentioned `aria_service.main` anywhere. That flagged
test_rf2379_dd_reports_full_ui_verification.py, which is NOT an offender: it
defines its own `app` FIXTURE (a bare FastAPI with the DD router and auth
overridden), and all 11 of its `with TestClient(app)` sites receive THAT app. Its
single `from aria_service.main import app` is in an unrelated helper that only
reads `app.routes`.

Worse, the false positive was then written into an allowlist with a confident,
WRONG justification: "needs the started app — 44 passed with lifespan vs 11
failed without". The measurement was real; the interpretation was not. The 11
failures came from the FIX ATTEMPT swapping in the real app (so real auth
returned 401), not from removing a lifespan that was never there. A guard that
cannot resolve its own subject manufactures exceptions to itself — the allowlist
would have permanently excused a clean file for a defect it never had.

So detection now RESOLVES the name: a `with TestClient(app)` counts only when
`app` in that scope actually is aria_service.main's. A parameter named `app`
(pytest fixture injection) shadows the module import and is not an offender. The
allowlist is consequently EMPTY, which is the honest state.
"""
from __future__ import annotations

import ast
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent

_MAIN_APP_IMPORT = ("aria_service.main", "app")

# Files permitted to enter the real lifespan in-process, each with the MEASURED
# reason it cannot take the standard remedy. Empty is the correct state: every
# entry is a standing exemption, and R-F3370 exists because a wrong one was
# nearly made permanent.
_DECLARED: dict[str, str] = {}


def _imports_main_app(node: ast.AST) -> bool:
    """Does this scope do `from aria_service.main import app`?"""
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom) and child.module == _MAIN_APP_IMPORT[0]:
            if any(a.name == _MAIN_APP_IMPORT[1] for a in child.names):
                return True
    return False


def _is_ctx_testclient_app(node: ast.AST) -> bool:
    """`with TestClient(app) ...` — the context-manager form runs startup."""
    if not isinstance(node, (ast.With, ast.AsyncWith)):
        return False
    for item in node.items:
        call = item.context_expr
        if (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "TestClient"
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "app"):
            return True
    return False


def _real_app_ctx_sites(src: str) -> int:
    """Count `with TestClient(app)` where `app` really is aria_service.main's.

    Deliberately resolves the NAME rather than grepping the file:
      * a function parameter called `app` is fixture injection and shadows any
        module-level import — that is the rf2379 case, and it is not an offender
      * otherwise the enclosing function's own import decides, falling back to a
        module-level import

    Also deliberately an AST walk, not a regex: an earlier draft matched on source
    text and flagged the DOCSTRINGS of the very files it had just fixed, because
    prose explaining the banned pattern counted as the banned pattern.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0

    module_level_import = any(
        isinstance(n, ast.ImportFrom)
        and n.module == _MAIN_APP_IMPORT[0]
        and any(a.name == _MAIN_APP_IMPORT[1] for a in n.names)
        for n in tree.body
    )

    total = 0
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    seen: set[int] = set()
    for fn in funcs:
        args = fn.args
        params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        shadowed = "app" in params           # pytest fixture injection
        local_import = _imports_main_app(fn)
        resolves_to_main = local_import or (module_level_import and not shadowed)
        for node in ast.walk(fn):
            if id(node) in seen or not _is_ctx_testclient_app(node):
                continue
            seen.add(id(node))
            if resolves_to_main:
                total += 1
    return total


def _offenders() -> dict[str, int]:
    hits: dict[str, int] = {}
    for path in TESTS.glob("test_*.py"):
        if path.name == pathlib.Path(__file__).name:
            continue
        n = _real_app_ctx_sites(path.read_text(encoding="utf-8", errors="replace"))
        if n:
            hits[path.name] = n
    return hits


def test_rf3365_no_undeclared_test_enters_the_real_lifespan():
    """A new `with TestClient(app)` on the real app must fail HERE, not months
    later as a hang in an unrelated test."""
    undeclared = {f: n for f, n in _offenders().items() if f not in _DECLARED}
    assert undeclared == {}, (
        "these tests enter aria_service.main's REAL lifespan in-process, which "
        "leaks background subsystems into the pytest process and hangs the next "
        "test that awaits the embedder (R-F3347, R-F3365): "
        f"{undeclared}. Use TestClient(app) WITHOUT the context manager if the "
        "routes answer unauthenticated, or run the lifespan in a subprocess as "
        "test_lifespan_smoke.py does."
    )


def test_rf3370_a_local_app_fixture_is_not_an_offender():
    """The false positive that R-F3370 exists to remove, pinned so it cannot
    return. rf2379 builds its own bare FastAPI in an `app` fixture; its 11
    `with TestClient(app)` sites take that, not the real app."""
    src = (TESTS / "test_rf2379_dd_reports_full_ui_verification.py").read_text(
        encoding="utf-8", errors="replace")
    assert "from aria_service.main import app" in src, (
        "precondition: the file must still mention the real app somewhere, or "
        "this test is no longer exercising the resolution logic"
    )
    assert _real_app_ctx_sites(src) == 0, (
        "a fixture-injected `app` was counted as aria_service.main's — this is "
        "the R-F3365 false positive, and it previously produced an allowlist "
        "entry excusing a clean file for a defect it never had"
    )


def test_rf3370_the_resolver_still_catches_the_real_thing():
    """Prove the instrument. Synthetic sources, so the check cannot silently
    become a tautology about the current tree."""
    real = (
        "from fastapi.testclient import TestClient\n"
        "def test_x():\n"
        "    from aria_service.main import app\n"
        "    with TestClient(app) as c:\n"
        "        pass\n"
    )
    fixture = (
        "from fastapi.testclient import TestClient\n"
        "from aria_service.main import app\n"
        "def test_y(app):\n"          # parameter shadows the module import
        "    with TestClient(app) as c:\n"
        "        pass\n"
    )
    no_ctx = (
        "from fastapi.testclient import TestClient\n"
        "def test_z():\n"
        "    from aria_service.main import app\n"
        "    c = TestClient(app)\n"    # no context manager -> no lifespan
    )
    assert _real_app_ctx_sites(real) == 1, "the real offender must be caught"
    assert _real_app_ctx_sites(fixture) == 0, "a shadowing parameter must not count"
    assert _real_app_ctx_sites(no_ctx) == 0, "without `with`, no lifespan runs"


def test_rf3365_the_fixed_files_stay_fixed():
    """The two sites this closed must not regress. Named explicitly so a revert
    fails loudly rather than reopening the wedge."""
    for name in ("test_rf1231_agent_signup_vault.py", "test_rf1411_outcome_wire.py"):
        src = (TESTS / name).read_text(encoding="utf-8", errors="replace")
        assert _real_app_ctx_sites(src) == 0, (
            f"{name} re-entered the real lifespan in-process — this is the wedge #5 "
            f"regression, and it will surface as an unrelated hang in "
            f"test_rf1401_held_out_split_eval, not here"
        )


def test_rf3370_the_allowlist_is_empty_and_stays_justified():
    """An allowlist that outlives its reason is a loophole. Empty is correct; any
    future entry must name a file that genuinely offends."""
    offenders = _offenders()
    stale = [f for f in _DECLARED if f not in offenders]
    assert stale == [], f"allowlist entries that no longer offend: {stale}"
