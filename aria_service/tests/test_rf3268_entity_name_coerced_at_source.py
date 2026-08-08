"""R-F3268 — R-F3258 guarded a COPY; the field itself was still untyped.

R-F3258 coerced the local `name` inside `_run_digital`. That protected the digital
layer, and I stopped there. It was not the root:

    dd_orchestrator.py:12557
        report.identity.entity_name = target.get("name") or target.get("entity")
                                      or target.get("query", "")

`target` is the caller-supplied DD request and is never type-validated, so a list
lands DIRECTLY on the field. `entity_name: str = ""` is a dataclass annotation,
which Python does not enforce at runtime. Every OTHER consumer then reads the raw
field, not the coerced copy — including the two that matter most:

    dd_orchestrator.py:9382   "entity_name": report.identity.entity_name
    dd_orchestrator.py:13763  "entity_name": report.identity.entity_name

Those build the adverse-media follow-up params. From there the name becomes search
queries handed to `researcher._web_search(query)`, which calls
`_detect_target_languages(query)` at BOTH :1567 and :1743 — the same
`query.lower()` that produced the live failure. So the adverse-media sweep, the
one question the AZURE PARKING LTD report could not answer, ran straight past the
R-F3258 guard.

Fixed at the SOURCE: every assignment to `identity.entity_name` coerces, so the
field is a string for all consumers rather than for the one caller that remembered.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import researcher as res

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_the_crashing_leaf_still_rejects_a_list() -> None:
    """Baseline: this is the live failure, and it is NOT what we are fixing.

    `_detect_target_languages(query: str)` is correctly typed. Guarding it would be
    symptom-patching (§1) — the caller must not hand it a list.
    """
    with pytest.raises(AttributeError, match="lower"):
        res._detect_target_languages(["AZURE PARKING LTD", "azure parking limited"])


def test_entity_name_is_coerced_at_every_assignment_site() -> None:
    """Every `identity.entity_name = ...` must pass through the coercion helper.

    Six sites assign it (1792, 3658, 4565, 9287, 12280, 12557) and each takes
    untrusted input — the request dict, a Companies House field, or a rerun
    lineage blob. A guard applied at five of six is a guard that fails on the
    sixth, silently.

    Checked by AST, not by line text: a line-based check calls a perfectly good
    multi-line assignment a violation, and — the reason that matters — would MISS a
    genuine bypass written across two lines. Verify the instrument before trusting
    what it measures.
    """
    import ast
    import inspect

    tree = ast.parse(module_source(ddo))

    def _is_entity_name_target(node) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "entity_name" and \
            isinstance(node.value, ast.Attribute) and node.value.attr == "identity"

    def _coerces(value) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_coerce_entity_text"
            for n in ast.walk(value)
        )

    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(_is_entity_name_target(t) for t in node.targets):
            continue
        if not _coerces(node.value):
            bare.append(f"line {node.lineno}")
    assert not bare, (
        "these assignments put an unvalidated value on identity.entity_name, which "
        "the adverse-media params read directly: " + ", ".join(bare)
    )


def test_adverse_media_params_never_carry_a_non_string_name() -> None:
    """The consumer that bypassed R-F3258: the follow-up param block.

    Belt-and-braces at the READ side too, because this value leaves the process as
    search queries and a wrong type there costs the entire adverse-media sweep.
    """
    import inspect
    src = module_source(ddo)
    bad = []
    for i, line in enumerate(src.splitlines(), start=1):
        s = line.strip()
        if '"entity_name": report.identity.entity_name' in s and "_coerce_entity_text" not in s:
            bad.append(f"{i}: {s[:100]}")
    assert not bad, (
        "adverse-media params take entity_name unguarded; it becomes a _web_search "
        "query and crashes at researcher.py:1567/1743:\n  " + "\n  ".join(bad)
    )


def test_coercion_helper_handles_every_shape_seen_in_the_wild() -> None:
    c = ddo._coerce_entity_text
    assert c("AZURE PARKING LTD") == "AZURE PARKING LTD"
    assert c(["AZURE PARKING LTD", "azure parking limited"]) == "AZURE PARKING LTD"
    assert c(["", "  ", "Second Choice Ltd"]) == "Second Choice Ltd"
    assert c(None) == ""
    assert c([]) == ""
    assert c({"name": "x"}) == ""          # never guess which key held the name
    assert c(12345) == ""
    assert c(("Tuple Co",)) == "Tuple Co"
