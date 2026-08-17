"""R-F4102 (C-154) — the stolen-decorator gate watched ONE file, so the same
theft shipped in another module the same day.

R-F3928 built a guard for exactly this class after R-F3919 stole a `@fail_wire`
by anchoring an insertion on the `def` instead of the decorator above it. Its
docstring states the rule in capitals and warns the gate "must never be muted or
baselined away". It is scoped to a single target::

    _TARGET = "aria_service/autonomous/safety.py"

So when R-F4097 inserted `_read_domains_strict` above `get_all_domains` in
`learning_progress.py` — anchoring on the `async def`, the exact forbidden move —
the pre-existing `@fail_wire` landed on the new PRIVATE helper and
`get_all_domains` was silently un-wired. Nothing failed. It shipped, was
deployed, and was live-verified as healthy, because the thing it broke is the
reporting of failure.

It surfaced only by accident: the NEXT insertion (R-F4101) put a comment between
the orphaned decorator and the following `def`, which is a SyntaxError. A
compile error found a wiring defect that six test selections and a live smoke had
all passed over.

**A guard that enumerates one file is a guard that certifies the other 104.**
That is the same shape as R-F3791 (a check whose universe went empty always
passes) and as the three Phase A gates §1 records as "certified by an absence" —
here the absence is the rest of the tree.

Measured: 105 modules opt into `@fail_wire`. Requiring the DECORATOR flagged 16
functions across 9 modules, but three of those were false positives (see the
note on `_WIRE_TOKENS`). Under §21a's actual definition the honest figure is
**9 genuinely dark public async functions across 4 modules** - a small, landable
baseline, so the gate can be widened today rather than after a cleanup nobody
schedules.

`_KNOWN_DARK` is **SHRINK-ONLY**, the same contract as `KNOWN_DEAD_CALLS` and
`LEGACY_COLLISIONS`: an entry may be removed by wiring the function, never
added. A new entry means the gate was bypassed.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from aria_service.tests._source_probe import repo_path

# Baselined 2026-08-17. SHRINK-ONLY - wire the function and delete the line.
# Adding to this set means a path went dark and the gate was silenced.
_KNOWN_DARK: frozenset[str] = frozenset({
    "aria_service/intel/brreg.py::get_company",
    "aria_service/intel/brreg.py::get_officers",
    "aria_service/intel/ecosystem_map.py::build_structure",
    "aria_service/intel/ecosystem_map.py::get_graph",
    "aria_service/intel/ecosystem_map.py::get_node",
    "aria_service/intel/ecosystem_map.py::get_coverage",
    "aria_service/intel/ecosystem_map.py::get_coverage_nonblocking",
    "aria_service/intel/wiring_harness.py::probe_wedge_stacks",
    "aria_service/llm/local_llm.py::stream",
})

# §21a defines wiring as a SINK, not a syntax: "brain_hook.absorb /
# capability_gaps.record_gap / mistake_ledger.record / a metric / a POST to
# /api/aria/brain/signal". A first version of this gate required the
# `@fail_wire` DECORATOR specifically and flagged `chat_ep` and
# `chat_stream_ep` - the two most important user-facing paths in the product,
# both of which are wired perfectly well through in-body `absorb` calls.
#
# That false positive mattered more than it looks. A gate that shouts about
# correct code is a gate someone mutes, and R-F3928's docstring says this one
# "must never be muted or baselined away". So the rule matches the DEFINITION:
# a public async function is dark only when it has neither the decorator nor
# any wiring call in its body.
_WIRE_TOKENS = ("wire", "absorb", "record_gap", "record_error", "emit_metric")


def _decorator_names(node) -> set[str]:
    out = set()
    for d in node.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        out.add(getattr(f, "id", None) or getattr(f, "attr", ""))
    return out


def _wired_in_body(node) -> bool:
    """Does this function reach a brain sink from inside its own body?"""
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            name = (c.func.id if isinstance(c.func, ast.Name)
                    else getattr(c.func, "attr", "")) or ""
            if any(tok in name for tok in _WIRE_TOKENS):
                return True
    return False


def _unwired_public_async(src: str) -> list[str]:
    """Public module-level async defs that reach NO brain sink at all."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for n in tree.body:
        if not isinstance(n, ast.AsyncFunctionDef) or n.name.startswith("_"):
            continue
        if "fail_wire" in _decorator_names(n):
            continue
        if _wired_in_body(n):
            continue
        out.append(n.name)
    return out


def _scan() -> dict[str, list[str]]:
    root = repo_path("aria_service")
    found: dict[str, list[str]] = {}
    for f in sorted(root.rglob("*.py")):
        if "tests" in f.parts:
            continue
        try:
            src = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if "fail_wire" not in src:
            continue          # module has not opted in; not this gate's business
        bad = _unwired_public_async(src)
        if bad:
            found[f.relative_to(root.parent).as_posix()] = bad
    return found


def test_no_module_that_uses_fail_wire_has_an_unwired_public_async_function():
    """The widened gate. R-F3928's version asserted this for safety.py alone."""
    offenders = {
        f"{path}::{fn}"
        for path, fns in _scan().items()
        for fn in fns
    } - _KNOWN_DARK
    assert offenders == set(), (
        "public async function(s) in a fail_wire module have no @fail_wire — "
        "a decorator was almost certainly stolen by an insertion above them. "
        "ANCHOR EDITS ON THE DECORATOR, NOT THE `def` (R-F3928). Do NOT add "
        f"these to _KNOWN_DARK; wire them. {sorted(offenders)}")


def test_the_specific_regression_get_all_domains_is_wired():
    """R-F4097 stole this one and it shipped. Pin it by name."""
    src = repo_path("aria_service/intel/learning_progress.py").read_text(
        encoding="utf-8")
    assert "get_all_domains" not in _unwired_public_async(src), (
        "learning_progress.get_all_domains lost its @fail_wire — that is the "
        "R-F4097 theft recurring")


def test_the_baseline_is_shrink_only():
    """Every baselined entry must still EXIST and still be unwired. An entry
    that has been fixed must be deleted, or the set silently grows into a
    permanent exemption list."""
    live = {f"{path}::{fn}" for path, fns in _scan().items() for fn in fns}
    stale = _KNOWN_DARK - live
    assert stale == set(), (
        "these baselined entries are now wired (or gone) — delete them from "
        f"_KNOWN_DARK so the gate keeps shrinking: {sorted(stale)}")


def test_the_gate_can_still_fail(tmp_path):
    """A guard that cannot fail is the defect this whole module is about."""
    good = "from .wire import fail_wire\n\n@fail_wire(module='x')\nasync def go():\n    pass\n"
    assert _unwired_public_async(good) == []

    stolen = ("from .wire import fail_wire\n\n"
              "@fail_wire(module='x')\n"
              "async def _helper():\n    pass\n\n"
              "async def go():\n    return 1\n")
    assert _unwired_public_async(stolen) == ["go"], (
        "the exact theft shape - decorator on the new private helper, public "
        "function bare - must be detected")

    # ...and a function wired IN THE BODY is not a violation, which is what
    # keeps chat_ep/chat_stream_ep out of the offender list.
    in_body = ("from .wire import fail_wire\n\n"
               "async def go():\n    await absorb('x')\n")
    assert _unwired_public_async(in_body) == [], (
        "§21a counts any brain sink, not just the decorator")


def test_private_helpers_are_not_required_to_be_wired():
    """Wiring a private helper is noise; the contract is about public paths."""
    src = "from .wire import fail_wire\n\n@fail_wire(module='x')\nasync def go():\n    pass\n\nasync def _quiet():\n    pass\n"
    assert _unwired_public_async(src) == []
