r"""R-F3937 — structural tests kept matching their own explanations.

THREE TIMES IN ONE DAY, in three unrelated modules, a test that greps source matched
the COMMENT documenting a defect rather than the code:

  * R-F3873's comment contained the literal ``if normalised:`` the test asserted did
    NOT precede a call. The guard matched its own prose and blocked a good commit.
  * R-F3920's docstring names ``gc.get_objects`` / ``tracemalloc`` to explain why
    they are avoided; the test asserting their absence matched that sentence.
  * R-F3935's target quotes the removed truncation
    ``db["facts"] = db["facts"][:MAX_FACTS]`` verbatim, so a §7 guard asserting it
    was gone found it in the comment recording its removal.

Each was patched locally — twice with a hand-rolled stripper — and THAT duplication
is what says the fix belongs in the shared helper. 217 test files call
``function_source``/``module_source``; every one is exposed to this.

This repo documents heavily and on purpose: the best comments quote the defect they
removed. So source-grepping tests will keep colliding with prose unless the shared
tool makes the safe path the easy one.

WHY NOT A LINE FILTER. ``ln.startswith("#")`` — my first two attempts — misses a
TRAILING comment on a code line, and wrongly strips a line inside a multi-line string
that begins with ``#``. Both matter here: half the offending strings were trailing
comments.
"""
from __future__ import annotations

from aria_service.tests._source_probe import (
    code_only,
    function_code,
    function_source,
)


# ── the three real cases, reproduced ───────────────────────────────────────────

def test_a_comment_quoting_code_is_not_the_code():
    """R-F3935's shape: a comment recording a removed line."""
    src = (
        '# Pre-R-F239 the truncation `db["facts"] = db["facts"][:MAX_FACTS]`\n'
        '# dropped the OLDEST facts. Removed as a §7 violation.\n'
        'keep_everything = True\n'
    )
    assert 'db["facts"][:MAX_FACTS]' in src, "precondition: prose contains it"
    assert 'db["facts"][:MAX_FACTS]' not in code_only(src), (
        "a comment quoting removed code must not read as that code (R-F3937)")
    assert "keep_everything = True" in code_only(src), "real code must survive"


def test_a_docstring_naming_a_banned_api_is_not_a_use():
    """R-F3920's shape: a docstring explaining why an API is avoided."""
    src = (
        'def census():\n'
        '    """Deliberately does NOT call gc.get_objects or tracemalloc —\n'
        '    both walk the heap and would block the loop."""\n'
        '    return len(x)\n'
    )
    stripped = code_only(src)
    assert "gc.get_objects" not in stripped
    assert "tracemalloc" not in stripped
    assert "return len(x)" in stripped


def test_a_trailing_comment_is_stripped_not_just_a_whole_line():
    """The case a naive line filter misses — and half the real ones were this."""
    src = 'do_work()  # never call gc.get_objects here\n'
    stripped = code_only(src)
    assert "gc.get_objects" not in stripped
    assert "do_work()" in stripped


# ── the properties that keep it honest ─────────────────────────────────────────

def test_a_hash_inside_a_string_is_not_a_comment():
    """Quote-aware by construction. A line filter would corrupt this."""
    src = 'colour = "#ff0000"  # a real comment\nurl = "http://x/#frag"\n'
    stripped = code_only(src)
    assert '"#ff0000"' in stripped, "a # inside a string must survive"
    assert "#frag" in stripped
    assert "a real comment" not in stripped


def test_line_numbers_are_preserved():
    """Callers report file:line and assert ordering (X before Y). Collapsing lines
    would silently invalidate both."""
    src = "a = 1\n# comment\nb = 2\n"
    assert len(code_only(src).splitlines()) == len(src.splitlines())
    assert code_only(src).splitlines()[2] == "b = 2"


def test_it_never_returns_empty_on_unparseable_source():
    """THE BLIND-GUARD PROPERTY. A helper that yields "" would make every assertion
    built on it pass — the exact failure this repo keeps finding (§1, §22). Broken
    source must still yield something to assert against."""
    broken = "def f(:\n    # comment\n    pass\n"
    out = code_only(broken)
    assert out.strip(), "code_only must not blank everything on a syntax error"


def test_the_helper_can_actually_fail():
    """R-F3858 — prove it does not simply strip everything, or green means nothing."""
    src = "real = 1\n"
    assert "real = 1" in code_only(src)


# ── the convenience wrappers ───────────────────────────────────────────────────

def test_function_code_strips_where_function_source_does_not():
    """The pairing is the point: `function_source` keeps prose (for asserting a
    rationale is documented), `function_code` drops it (for asserting behaviour)."""
    from aria_service.tests import _source_probe as sp

    raw = function_source(sp, "code_only")
    stripped = function_code(sp, "code_only")
    assert "R-F3937" in raw, "the rationale lives in the docstring"
    assert "R-F3937" not in stripped, "function_code must drop the docstring"
    assert "tokenize" in stripped, "the actual implementation must survive"
