"""R-F3795 — the guard that makes `skipif` on an optional dependency safe.

`_env_probe.requires_module` lets a test skip when its dependency is absent from
THIS machine. On its own that is a hole: if `chromadb` were dropped from
`requirements.txt`, the tests that exercise it would go quietly green-by-skip on
every box instead of failing, and the loss would never surface.

So the two questions are separated:

  * absent from the MACHINE  -> tolerated, the test skips with a reason (§16: five
    of these publish no win-arm64 wheel and are import-guarded, so the service boots
    without them and the features are inert locally)
  * absent from the MANIFEST -> a real defect, and THESE tests cannot skip

Nothing here imports the optional packages, so this file runs identically on a box
that has none of them. That is the point: the guard must not share the weakness it
is guarding.
"""
from __future__ import annotations

import re

import pytest

from ._env_probe import OPTIONAL_BINARIES, OPTIONAL_MODULES
from ._source_probe import repo_path

REQUIREMENTS = repo_path("aria_service/requirements.txt")


def _declared() -> dict[str, str]:
    """Map normalised distribution name -> pinned version from requirements.txt.

    Names are normalised per PEP 503 (case-insensitive, `_`/`.` -> `-`) because the
    manifest writes `PyMuPDF` and `sentence-transformers` while imports read
    `fitz` and `sentence_transformers`. Matching raw strings would give a false
    'not declared' on the two that matter most.
    """
    out: dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)", line)
        if m:
            out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return out


@pytest.mark.parametrize("import_name,distribution", sorted(OPTIONAL_MODULES.items()))
def test_every_skippable_module_is_still_declared(import_name, distribution):
    """A test may skip because the machine lacks the package. It may NOT skip
    because the package was removed from the requirements manifest."""
    key = re.sub(r"[-_.]+", "-", distribution).lower()
    declared = _declared()
    assert key in declared, (
        f"{distribution!r} backs a skippable test (import {import_name!r}) but is NOT "
        f"declared in aria_service/requirements.txt. Every test that drives it would "
        f"now skip on EVERY machine and the loss would be invisible. Either restore "
        f"the dependency or delete it from _env_probe.OPTIONAL_MODULES."
    )


@pytest.mark.parametrize("binary,distribution", sorted(OPTIONAL_BINARIES.items()))
def test_every_skippable_binary_has_a_declared_binding(binary, distribution):
    key = re.sub(r"[-_.]+", "-", distribution).lower()
    assert key in _declared(), (
        f"the {binary!r} executable backs a skippable test via {distribution!r}, "
        f"which is not declared in requirements.txt"
    )


def test_the_declarations_are_pinned_not_floating():
    """R-F3726 pinned every dependency so a build is reproducible. A skippable one
    reverting to `>=` would let the environment move under the baseline again —
    the C-12 failure, which is what R-F3794 exists to make visible."""
    declared = _declared()
    for distribution in set(OPTIONAL_MODULES.values()) | set(OPTIONAL_BINARIES.values()):
        key = re.sub(r"[-_.]+", "-", distribution).lower()
        assert declared.get(key), f"{distribution!r} must be pinned with =="


def test_this_guard_does_not_import_what_it_guards():
    """If this file imported chromadb it would skip or error on exactly the boxes
    where the guard matters most."""
    source = repo_path("aria_service/tests/test_rf3795_optional_deps_declared.py").read_text(
        encoding="utf-8")
    body = source.split('"""', 2)[-1]        # ignore the module docstring's prose
    for import_name in OPTIONAL_MODULES:
        assert f"import {import_name}" not in body, (
            f"this guard must not import {import_name!r} — it has to run on a machine "
            f"that lacks it")
