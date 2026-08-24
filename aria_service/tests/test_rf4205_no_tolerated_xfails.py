"""R-F4205 capability gates for zero-tolerance pytest regression handling."""

from pathlib import Path
import sys

import scripts.verify_commit as verifier


def test_verifier_detects_direct_and_marked_xfails(tmp_path: Path):
    """The push verifier must reject both executable pytest xfail forms."""
    direct = tmp_path / "test_direct.py"
    direct.write_text(
        "import pytest\ndef test_gap():\n    pytest.xfail('known gap')\n",
        encoding="utf-8",
    )
    marked = tmp_path / "test_marked.py"
    marked.write_text(
        "import pytest\n@pytest.mark.xfail(reason='known gap')\n"
        "def test_gap():\n    assert False\n",
        encoding="utf-8",
    )

    findings = verifier._tolerated_xfails([direct, marked])

    assert findings == [(direct, 3), (marked, 2)]


def test_verifier_detects_aliased_xfail_imports(tmp_path: Path):
    """Import aliases must not provide a trivial bypass around the guard."""
    module_alias = tmp_path / "test_module_alias.py"
    module_alias.write_text(
        "import pytest as pt\npt.xfail('known gap')\n", encoding="utf-8"
    )
    direct_alias = tmp_path / "test_direct_alias.py"
    direct_alias.write_text(
        "from pytest import xfail as expected_gap\nexpected_gap('known gap')\n",
        encoding="utf-8",
    )
    mark_alias = tmp_path / "test_mark_alias.py"
    mark_alias.write_text(
        "from pytest import mark as test_mark\n@test_mark.xfail(reason='gap')\n"
        "def test_gap():\n    assert False\n",
        encoding="utf-8",
    )

    assert verifier._tolerated_xfails(
        [module_alias, direct_alias, mark_alias]
    ) == [(module_alias, 2), (direct_alias, 2), (mark_alias, 2)]


def test_verifier_does_not_confuse_skip_with_tolerated_failure(tmp_path: Path):
    """A genuine environment exclusion remains distinct from a known regression."""
    skipped = tmp_path / "test_optional_platform.py"
    skipped.write_text(
        "import pytest\n@pytest.mark.skipif(True, reason='platform unavailable')\n"
        "def test_optional():\n    pass\n",
        encoding="utf-8",
    )

    assert verifier._tolerated_xfails([skipped]) == []


def test_repository_contains_no_tolerated_xfail_markers():
    """The current test tree must satisfy the same zero-tolerance push contract."""
    assert verifier._tolerated_xfails() == []


# R-F4298 (C-252) — THIS TEST HAD NEVER PASSED UNDER A DEFAULT TMPDIR.
# It hands the gate a path in pytest's tmp_path, which lives under the system
# temp directory, and the gate rendered findings with `path.relative_to(_REPO)`
# — ValueError for anything outside the checkout. So it crashed with a
# traceback instead of reporting, and only went green when run with
# `--basetemp` pointed inside the repo (the leftover `.pytest_rf4205_*`
# directories are exactly that). A test that passes because of HOW it was
# invoked is not evidence about the code. The gate now renders relative when
# it can and absolute when it cannot — do not put `relative_to` back.
def test_verifier_main_blocks_a_tolerated_xfail(monkeypatch, capsys, tmp_path: Path):
    """Drive the real verifier gate and prove a staged regression blocks the push."""
    staged = tmp_path / "test_staged_gap.py"
    staged.write_text("import pytest\npytest.xfail('gap')\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "_r_numbers_in_range", lambda _rev: set())
    monkeypatch.setattr(verifier, "_tolerated_xfails", lambda: [(staged, 2)])
    monkeypatch.setattr(sys, "argv", ["verify_commit.py", "--fast"])

    assert verifier.main() == 5
    assert "tolerated pytest xfail markers are forbidden" in capsys.readouterr().err
