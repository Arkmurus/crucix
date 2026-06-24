"""R-F1866 — _check_hallucinated_api must not false-flag local-variable method calls.

Live 2026-06-24 (log DD): the advisory R-F1460 hallucinated-API gate logged
"potential hallucinated API: out.append() not found / e.get() not found /
counts_by_sev.get() not found" for aria_service/intel/pending_actions.py. Those
are local variables calling built-in list/dict methods — not hallucinated module
APIs. The check treated every obj.method() receiver as a module/class and grepped
the target for `def <method>`. Fix: skip receivers that are locally bound,
imported, or self/cls; only flag genuinely free (undefined) receivers.

Capability test: drive the real ARIACoder._check_hallucinated_api with code that
reproduces the live false positives and assert it does NOT flag them, while a
genuinely undefined receiver IS still flagged.
"""
from __future__ import annotations

from aria_service.autonomous.self_coder import ARIACoder


def _check(code: str):
    # target file path that exists in the repo (read by the check); its contents
    # are irrelevant to the local-var cases since those are skipped before the grep.
    return ARIACoder._check_hallucinated_api(code, "aria_service/intel/pending_actions.py")


def test_local_var_builtin_methods_not_flagged():
    """The exact live false positives: list.append / dict.get on local vars."""
    code = (
        "def f(counts_by_sev):\n"
        "    out = []\n"
        "    out.append(1)\n"
        "    for e in []:\n"
        "        e.get('x')\n"
        "    counts_by_sev.get('high')\n"
        "    return out\n"
    )
    ok, reasons = _check(code)
    assert ok, f"local-var built-in calls must not be flagged; got: {reasons}"
    assert reasons == []


def test_imported_module_method_not_flagged():
    """A method on an imported module isn't verifiable by grepping the target."""
    code = (
        "import sentence_transformers as st\n"
        "def g():\n"
        "    return st.SomeHelper().run()\n"
    )
    ok, reasons = _check(code)
    assert ok, f"imported-module calls must not be flagged; got: {reasons}"


def test_self_method_not_flagged():
    """self.method() (incl. inherited) is not greppable in one file → skip."""
    code = (
        "class C:\n"
        "    def run(self):\n"
        "        return self.helper_that_lives_in_a_base_class()\n"
    )
    ok, reasons = _check(code)
    assert ok, f"self.* calls must not be flagged; got: {reasons}"


def test_fake_method_on_local_class_instance_flagged():
    """The real catch this gate exists for: a non-existent method on an INSTANCE
    of a locally-defined class IS flagged (e.g. detector = GapDetector();
    detector._nonexistent_method_xyz()). Here the class is defined in the code."""
    code = (
        "class Widget:\n"
        "    def real_method(self):\n"
        "        return 1\n"
        "def h():\n"
        "    w = Widget()\n"
        "    return w.totally_fake_method()\n"
    )
    ok, reasons = _check(code)
    assert not ok, "a fake method on a local-class instance should be flagged"
    assert any("totally_fake_method" in r for r in reasons)


def test_real_method_on_local_class_instance_passes():
    """A real method on a local-class instance must NOT be flagged."""
    code = (
        "class Widget:\n"
        "    def real_method(self):\n"
        "        return 1\n"
        "def h():\n"
        "    w = Widget()\n"
        "    return w.real_method()\n"
    )
    ok, reasons = _check(code)
    assert ok, f"a real local-class method must pass; got: {reasons}"
