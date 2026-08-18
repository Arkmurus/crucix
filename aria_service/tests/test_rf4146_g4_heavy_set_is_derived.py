"""R-F4146 (C-173) — the G4 heavy set is DERIVED from the tree, not hand-listed.

`DENYLIST` holds primitives: functions heavy in their own body. But the defect
this gate exists for arrives one level up, through a thin sync wrapper — and the
wrapper is invisible until a human remembers to add it. **Both C-170 and C-172
entered exactly that way, in one session:**

  * C-170 — `verify_premises` (sync, unlisted) reached `search_fact_records`
    two levels down. Four async call sites ran a 2.28s scan on the loop, and it
    took an instrument plus a production reading to find.
  * C-172 — `_get_embedder` (sync, unlisted) imported transformers and loaded a
    model. Caught only by a 5.17s wedge dump.

Twice in one session is a class, not luck. A list that only catches what someone
remembered is the §27d failure mode, in the file that is supposed to be the
vaccine against recurrence.

**The obvious implementation was measured FIRST and rejected.** Full transitive
propagation over bare names, on this tree:

```
seeds 8  ->  transitively heavy: 2,195 functions
async call sites flagged: 16,225
sample: __init__, __enter__, _add, append, list, ...
```

Name-only resolution collides — any method called `append` inherits heaviness
from any other `append` that reaches a seed. A gate flagging 16,225 sites is
noise, and noise gets deleted rather than obeyed. Recorded here so nobody
re-derives it and concludes the idea is unworkable.

Restricting propagation to names that **cannot be ambiguous** is what survives:
defined exactly once in `aria_service`, sync, reaching the heavy set. Measured:
converges in 2 rounds to 11 derived names, 19 total, **0 offenders** — and
`verify_premises` is derived, so C-170 would have been caught here instead of in
production.

The trade is explicit: a heavy wrapper whose name collides is NOT derived. That
false negative is deliberate and is the cheaper error — the alternative was
measured at 16,225 false positives.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest


def _load_gate():
    """Load the gate module by PATH — `aria_service/tests` is not a package, so
    a bare import only resolves when pytest happens to put it on sys.path."""
    p = pathlib.Path(__file__).with_name("test_g4_no_sync_cpu_on_loop.py")
    spec = importlib.util.spec_from_file_location("_g4_gate_rf4146", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


G4 = _load_gate()


@pytest.fixture(scope="module")
def derived() -> set[str]:
    return G4.derive_heavy_names()


def test_the_wrapper_that_caused_C170_is_derived(derived):
    """THE regression test. `verify_premises` is not a primitive and was only on
    the denylist because I hand-added it after production found it. Derivation
    must reach it on its own, or this whole change is decorative."""
    assert "verify_premises" in derived, sorted(derived)


def test_the_intermediate_wrappers_are_derived_too(derived):
    """`verify_premises` is TWO hops from the seed — it calls
    `verify_officeholder_premise`, which calls `search_fact_records`. A one-hop
    implementation would miss it, which is why the derivation iterates."""
    assert "verify_officeholder_premise" in derived
    assert "verify_programme_premise" in derived


def test_the_C169_wrapper_is_derived(derived):
    """`_fetch_prior_facts_sync` gained a `search_fact_records` call in C-169.
    A wrapper created THIS session must be covered without anyone listing it."""
    assert "_fetch_prior_facts_sync" in derived


def test_the_seeds_are_still_included(derived):
    """Derivation extends the hand-maintained primitives, never replaces them."""
    assert G4.DENYLIST <= derived


def test_the_derived_set_cannot_quietly_explode(derived):
    """The measured failure mode of the obvious implementation: 2,195 names.

    This ceiling is not a style preference. If it trips, the derivation has
    started resolving ambiguous names and the gate is about to become noise —
    fix the derivation, do NOT raise the bound."""
    assert len(derived) <= G4._DERIVE_MAX_NAMES, (
        f"derived {len(derived)} names — the naive variant reached 2,195 and "
        f"flagged 16,225 call sites: {sorted(derived)}")


def test_no_ambiguous_or_dunder_name_is_derived(derived):
    """The specific poison from the naive variant. These names exist many times
    over; deriving any of them means the unique-name constraint has broken."""
    for bad in ("append", "list", "get", "run", "__init__", "__call__",
                "close", "load", "save"):
        assert bad not in derived, f"{bad!r} was derived — name collision leaked in"


def test_every_derived_name_is_defined_exactly_once():
    """The constraint that makes derivation sound without import resolution.
    Asserted against the tree rather than trusted from the implementation."""
    root = pathlib.Path(G4._ROOT.as_posix())
    counts: dict[str, int] = {}
    for py in root.rglob("*.py"):
        if "/tests/" in py.as_posix() or "\\tests\\" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                counts[n.name] = counts.get(n.name, 0) + 1

    for name in G4.derive_heavy_names() - G4.DENYLIST:
        assert counts.get(name) == 1, (
            f"{name!r} is defined {counts.get(name)}x — an ambiguous name was "
            "derived, so the gate may be flagging an unrelated function")


def test_the_derivation_terminates(derived):
    """A cyclic call graph must not spin. Convergence is measured at 2 rounds;
    the cap is a backstop, so reaching it means something is wrong."""
    assert G4._DERIVE_MAX_ROUNDS >= 2
    # re-deriving must be stable, i.e. it really reached a fixed point
    assert G4.derive_heavy_names() == derived


def test_the_derived_gate_still_detects_a_violation(derived):
    """A guard that cannot fire is not a guard (R-F3858). Drive the visitor with
    the DERIVED set against a synthetic call to a DERIVED (not seed) name."""
    src = "async def h(pv, m):\n    return pv.verify_premises(m)\n"
    v = G4._Visitor("synthetic", derived)
    v.visit(ast.parse(src))
    assert v.violations, "the derived set flags nothing — the gate is decorative"


def test_the_derived_gate_still_accepts_the_offloaded_form(derived):
    """And must not be unsatisfiable."""
    src = ("import asyncio\n"
           "async def h(pv, m):\n"
           "    return await asyncio.to_thread(pv.verify_premises, m)\n")
    v = G4._Visitor("synthetic", derived)
    v.visit(ast.parse(src))
    assert not v.violations


def test_END_TO_END_a_new_heavy_wrapper_is_caught_without_anyone_listing_it(tmp_path):
    """The claim, proven on a real filesystem tree rather than an AST string.

    This is exactly the scenario that produced C-170 and C-172: someone adds a
    sync wrapper around a heavy primitive, and an async function calls it.
    Nobody touches DENYLIST. The gate must catch it anyway.
    """
    pkg = tmp_path / "svc"
    pkg.mkdir()
    (pkg / "prim.py").write_text(
        "def search_knowledge(q):\n    return 'heavy'\n", encoding="utf-8")
    (pkg / "wrapper.py").write_text(
        "from .prim import search_knowledge\n"
        "def a_brand_new_unique_wrapper(q):\n"
        "    return search_knowledge(q)\n", encoding="utf-8")
    (pkg / "caller.py").write_text(
        "from .wrapper import a_brand_new_unique_wrapper\n"
        "async def handler(q):\n"
        "    return a_brand_new_unique_wrapper(q)\n", encoding="utf-8")

    heavy = G4.derive_heavy_names(pkg)
    assert "a_brand_new_unique_wrapper" in heavy, (
        "the wrapper was not derived — a new heavy path would ship unseen")

    v = G4._Visitor("caller.py", heavy)
    v.visit(ast.parse((pkg / "caller.py").read_text(encoding="utf-8")))
    assert v.violations, "the async caller of a derived wrapper was not flagged"


def test_END_TO_END_the_same_wrapper_offloaded_is_NOT_flagged(tmp_path):
    """The other half: the correct form must pass, or the gate is unsatisfiable
    and gets deleted instead of obeyed."""
    pkg = tmp_path / "svc"
    pkg.mkdir()
    (pkg / "prim.py").write_text(
        "def search_knowledge(q):\n    return 'heavy'\n", encoding="utf-8")
    (pkg / "wrapper.py").write_text(
        "from .prim import search_knowledge\n"
        "def a_brand_new_unique_wrapper(q):\n"
        "    return search_knowledge(q)\n", encoding="utf-8")
    (pkg / "caller.py").write_text(
        "import asyncio\n"
        "from .wrapper import a_brand_new_unique_wrapper\n"
        "async def handler(q):\n"
        "    return await asyncio.to_thread(a_brand_new_unique_wrapper, q)\n",
        encoding="utf-8")

    heavy = G4.derive_heavy_names(pkg)
    v = G4._Visitor("caller.py", heavy)
    v.visit(ast.parse((pkg / "caller.py").read_text(encoding="utf-8")))
    assert not v.violations, "the correctly offloaded form was flagged"


def test_derivation_also_closes_the_NESTED_HELPER_hole(tmp_path):
    """A hole the visitor has always had, closed as a side effect — verified,
    not assumed.

    `visit_FunctionDef` resets `async_depth` to 0, because a plain `def` run via
    `to_thread` is legitimately off the loop. But that also blinds it to a sync
    helper DEFINED inside an async function and then CALLED INLINE:

        async def handler(q):
            def _helper():
                return search_knowledge(q)     # not flagged: inside a plain def
            return _helper()                   # not flagged: _helper is unlisted

    Derivation closes it for uniquely-named helpers: `_helper` reaches a seed,
    so it becomes heavy, so the inline call in the async parent is flagged.

    Checked before being claimed — the reach of a fix is exactly the kind of
    thing that is easy to assert and cheap to verify.
    """
    pkg = tmp_path / "svc"
    pkg.mkdir()
    (pkg / "prim.py").write_text(
        "def search_knowledge(q):\n    return 1\n", encoding="utf-8")
    (pkg / "caller.py").write_text(
        "from .prim import search_knowledge\n"
        "async def handler(q):\n"
        "    def _my_nested_unique_helper():\n"
        "        return search_knowledge(q)\n"
        "    return _my_nested_unique_helper()\n", encoding="utf-8")

    heavy = G4.derive_heavy_names(pkg)
    assert "_my_nested_unique_helper" in heavy, sorted(heavy)

    v = G4._Visitor("caller.py", heavy)
    v.visit(ast.parse((pkg / "caller.py").read_text(encoding="utf-8")))
    assert v.violations, "a nested sync helper called inline is still invisible"
