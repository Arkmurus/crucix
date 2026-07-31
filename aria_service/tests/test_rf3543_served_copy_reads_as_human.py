"""R-F3543 — copy the CUSTOMER reads, checked where it is actually written.

Operator, 2026-07-31, on the DD run panel: the source-selection copy reads as
AI-written and does not fully hang together.

Two findings, and the second is why the first survived.

1. THE DASH GUARD ONLY SCANNED HTML. `no-ai-dashes-in-copy-rf3278` walks
   `public/*.html`, and it PASSED on dd-reports.html the whole time — because the
   sentences the operator was reading are not in the page. They are Python string
   constants (`decision`, `note`, `remedy`, `reason`, `unavailable_reason`)
   served by `/api/aria/dd/scope-options` and rendered into the panel. A guard
   that checks the template while the copy lives in the API is a guard on the
   wrong file.

2. THE CARD CONTRADICTED ITSELF. `required_for` and `enhances` were concatenated
   under a single "Answers" label, so Find Case Law was shown as *answering*
   IS-17a directly above a decision line stating that another source already
   covers it. A source only ANSWERS a question when nothing else currently can.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest


_REPO = pathlib.Path(__file__).resolve().parents[2]

#: em dash and en dash. Both read as machine-written in product copy.
_DASH = re.compile("[—–]")

#: Keyword arguments whose string values are rendered to a customer.
_SERVED_KWARGS = {
    "note", "remedy", "reason", "text", "decision",
    "unavailable_reason", "detail", "summary",
}

#: Modules whose string constants reach the DD run panel or the DD report.
_SERVED_MODULES = (
    "aria_service/intel/dd_standard.py",
    "aria_service/intel/sources/registry_trust.py",
    "aria_service/intel/sources/find_case_law.py",
)


def _served_strings(path: pathlib.Path):
    """Yield (lineno, kwarg, value) for every customer-facing string constant.

    Deliberately AST-based rather than a line scan: a docstring explaining a
    defect may legitimately contain a dash, and an engineer's comment is not
    product copy. Only values passed to a rendering keyword count.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords or []:
                if (kw.arg in _SERVED_KWARGS
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)):
                    yield kw.value.lineno, kw.arg, kw.value.value
        # `row["decision"] = "..."` — the DD panel's four decision lines are written
        # this way, so a kwargs-only selector missed exactly the copy the operator
        # complained about. Verifying the instrument caught this, not a code review.
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value in _SERVED_KWARGS):
                    yield node.value.lineno, str(target.slice.value), node.value.value


@pytest.mark.parametrize("module", _SERVED_MODULES)
def test_served_copy_contains_no_em_or_en_dashes(module):
    path = _REPO / module
    offenders = [
        f"{module}:{lineno} [{arg}] {value[:90]}"
        for lineno, arg, value in _served_strings(path)
        if _DASH.search(value)
    ]
    assert not offenders, (
        "customer-facing copy contains em/en dashes (reads as AI-written):\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_actually_finds_served_strings(module=_SERVED_MODULES[0]):
    """Verify the instrument. A selector that matches nothing passes vacuously."""
    found = list(_served_strings(_REPO / module))
    assert len(found) > 20, (
        f"only {len(found)} served strings found in {module} — the AST selector "
        "has drifted and this guard is passing on an empty set"
    )
    assert any(arg == "decision" for _, arg, _ in found), "decision lines not scanned"
    assert any(arg == "note" for _, arg, _ in found), "cost notes not scanned"


def test_the_scan_would_catch_a_dash_if_one_were_introduced():
    """Force the guard to FAIL on a known-bad input before trusting a pass."""
    src = 'f(note="a metered source — needs approval")'
    tree = ast.parse(src)
    hits = [
        kw.value.value
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        for kw in node.keywords or []
        if kw.arg in _SERVED_KWARGS and isinstance(kw.value, ast.Constant)
        and _DASH.search(str(kw.value.value))
    ]
    assert hits, "the detector does not fire on a dash it should catch"


# ── The panel must not claim more than the source does ───────────────────────


def test_a_source_that_only_enhances_is_not_shown_as_answering():
    page = (_REPO / "public" / "dd-reports.html").read_text(encoding="utf-8")
    assert "chipRow('Answers', o.required_for)" in page, (
        "'Answers' must be built from required_for only"
    )
    assert "chipRow('Adds depth to', o.enhances)" in page, (
        "an enhancing source is still presented as answering the question"
    )
    # Scoped to the RENDERER. `ddBuildScope` legitimately concatenates both lists,
    # because electing a source elects every question it touches — that is a
    # different statement from the label on the card, and a whole-file assertion
    # conflated the two.
    start = page.index("box.innerHTML = d.options.map(")
    renderer = page[start:page.index("}).join('');", start)]
    assert "(o.required_for || []).concat(o.enhances || [])" not in renderer, (
        "the CARD still concatenates required_for and enhances under one label"
    )


def test_the_decision_lines_state_a_consequence_not_a_status_enum():
    """The operator is deciding where to spend; the line should tell them what
    happens, not shout an enum at them."""
    from aria_service.intel import dd_standard

    src = (_REPO / "aria_service" / "intel" / "dd_standard.py").read_text(encoding="utf-8")
    decisions = [v for _, arg, v in _served_strings(_REPO / _SERVED_MODULES[0]) if arg == "decision"]
    # the four branch strings are assigned via subscript, so read them from source
    for legacy in ("REQUIRED: usable now; select to search, decline to waive",
                   "OPTIONAL: adds depth; another source already covers these",
                   "BLOCKING: these questions cannot be answered without it",
                   "OPTIONAL: unavailable, and something else covers these"):
        assert legacy not in src, f"legacy shouty decision line still served: {legacy!r}"
    for expected in ("Tick to search it, or leave it to record a waiver.",
                     "Tick to add depth.",
                     "Nothing else can answer these questions"):
        assert expected in src, f"missing rewritten decision copy: {expected!r}"
    assert dd_standard is not None  # module imports cleanly with the new copy


def test_the_panel_intro_does_not_contradict_its_own_blocked_case():
    """The first sentence used to state, unconditionally, that a ticked source is
    searched and may be chargeable — which is exactly what does NOT happen for a
    blocked source, as the same paragraph then went on to say."""
    page = (_REPO / "public" / "dd-reports.html").read_text(encoding="utf-8")
    intro_start = page.index("Choose before running.")
    # The copy is wrapped across source lines; compare on normalised whitespace so
    # the guard checks the SENTENCE and not the indentation.
    intro = re.sub(r"\s+", " ", page[intro_start:intro_start + 600])
    assert "A ticked source is searched and may be" not in intro, (
        "the intro still asserts unconditionally that ticking means searching"
    )
    assert "Ticking a source that is available searches it" in intro
    assert "cannot run yet orders it instead" in intro
    assert "charges nothing" in intro
