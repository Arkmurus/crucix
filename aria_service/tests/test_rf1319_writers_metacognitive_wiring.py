"""
R-F1319 capability tests — writers/ and metacognitive/ package wiring.

Tests:
  1. All writers modules compile and have wire_success
  2. All metacognitive modules compile and have wire_success
"""
from __future__ import annotations

import ast
import os


WRITERS_MODULES = [
    "anti_corruption_law.py",
    "assessment_writer.py",
    "procurement_paper_writer.py",
    "tech_spec_and_portuguese_writer.py",
    "writer_orchestrator.py",
]

METACOGNITIVE_MODULES = [
    "calibration.py",
    "coding_lessons.py",
    "consciousness.py",
    "cycle.py",
    "domains.py",
    "gaps.py",
    "identity.py",
    "self_improvement_codegen.py",
]


def test_writers_compile():
    """Every writers module must compile without SyntaxError."""
    for f in WRITERS_MODULES:
        path = os.path.join("aria_service", "writers", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


def test_writers_emit_a_brain_signal():
    """Every writers module must reach the brain — §21a, R-F1319's real intent.

    R-F4042 (C-104) STRENGTHENED THIS. It used to assert the substring
    "wire_success" appeared anywhere in the file, which a module-level
    "X active" wire satisfied merely by being IMPORTED — and it was emitted
    under `writers.<stem>`, a name `_MODULE_TOPICS` does not contain, so it was
    structurally invisible to `never_seen` while the registered name it shadowed
    stayed permanently unseen.

    Contract now: emit a signal, and where the registry knows the module, emit
    it under THAT name rather than a namespaced twin.
    """
    import re
    from aria_service.intel.brain_hook import _MODULE_TOPICS

    for f in WRITERS_MODULES:
        path = os.path.join("aria_service", "writers", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()

        names = set(re.findall(r'module\s*=\s*"([^"]+)"', source))
        assert names, f"{f} emits no brain signal at all — it is dark (§21a)"

        stem = f[:-3]
        if stem in _MODULE_TOPICS:
            assert stem in names, (
                f"{f} is registered as {stem!r} but never reports under that "
                f"name (emits {sorted(names)}) — its gauge can never go green"
            )
        # A namespaced twin of ANY registered name is a phantom, whatever the
        # file is called (tech_spec_and_portuguese_writer emits two).
        phantoms = {
            n for n in names
            if "." in n and n.rsplit(".", 1)[1] in _MODULE_TOPICS
        }
        assert not phantoms, (
            f"{f} still emits {sorted(phantoms)} — health recorded under a name "
            f"the registry does not know, where `never_seen` cannot see it"
        )


def test_metacognitive_compile():
    """Every metacognitive module must compile without SyntaxError."""
    for f in METACOGNITIVE_MODULES:
        path = os.path.join("aria_service", "metacognitive", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


def test_metacognitive_have_wire_success():
    """Every metacognitive module must have wire_success at module level."""
    for f in METACOGNITIVE_MODULES:
        path = os.path.join("aria_service", "metacognitive", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "wire_success" in source, f"{f} missing wire_success"
        assert "R-F1319" in source, f"{f} missing R-F1319 reference"
