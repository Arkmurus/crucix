"""R-F3551 — CI must test the Python production actually runs.

Both GitHub workflows had been failing on EVERY commit (25+ notifications, mine
and the other agent's alike). Two causes, and neither was a product defect:

1. `test-aria.yml` pinned Python **3.11** while production runs **3.13.14** and
   `ci.yml` pins 3.13. `routes/aria.py:12748` (added 2026-05-31 by R-F1176) uses
   a backslash inside an f-string expression, which is legal from 3.12 (PEP 701)
   and a SyntaxError before it. So CI could not even IMPORT the tree, on code
   that has been serving production for two months.

   A CI that tests a runtime you do not ship cannot catch a real defect and
   cannot pass. Worse, a permanently-red pipeline is one nobody reads — which is
   how it stayed broken.

2. `orjson` and `defusedxml` are TOP-LEVEL imports in production modules and were
   missing from the workflow's hand-curated install list, so 8 modules failed
   with "real ImportError, not optional" — a defect that existed only in CI's own
   environment.

`orjson` also turned out to be an UNDECLARED hard dependency: imported at
`neural_memory.py:31`, present in the image only because chromadb happens to
require it (`pip show orjson` -> Required-by: chromadb). It is declared in
aria_service/requirements.txt now, so it is our dependency rather than someone
else's side effect.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml


_REPO = pathlib.Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO / ".github" / "workflows"

#: What production runs. Verified live on aria-intel 2026-07-31: Python 3.13.14.
_PRODUCTION_PYTHON = "3.13"


def _workflow_files():
    return sorted(p for p in _WORKFLOWS.glob("*.yml") if p.is_file())


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_every_workflow_tests_the_production_python(path):
    """A workflow pinned to a different minor version is testing another product."""
    text = path.read_text(encoding="utf-8")
    pins = re.findall(r"python-version:\s*['\"]?(\d+\.\d+)", text)
    wrong = sorted({p for p in pins if p != _PRODUCTION_PYTHON})
    assert not wrong, (
        f"{path.name} pins Python {wrong} but production runs {_PRODUCTION_PYTHON}. "
        "CI that runs a different minor version cannot catch a real defect and "
        "cannot pass — 3.11 turned PEP-701 f-strings into a SyntaxError on code "
        "that had been serving production for two months."
    )


def test_the_pin_scan_actually_finds_pins():
    """Verify the instrument: a regex that matches nothing passes vacuously."""
    found = 0
    for path in _workflow_files():
        found += len(re.findall(r"python-version:\s*['\"]?(\d+\.\d+)", path.read_text(encoding="utf-8")))
    assert found >= 3, f"only {found} python pins found across workflows — the scan has drifted"


def test_top_level_third_party_imports_are_declared():
    """A hard import that is only present transitively is a latent boot failure.

    `orjson` was imported at neural_memory.py:31 and declared nowhere; it existed
    in the image because chromadb depends on it. The comment on that import even
    said "in image; verified live" — verified PRESENT rather than declared
    REQUIRED, which is exactly the difference this asserts.
    """
    reqs = (_REPO / "aria_service" / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("orjson", "defusedxml"):
        assert re.search(rf"^{package}\b", reqs, re.MULTILINE), (
            f"{package} is imported at module top level in production code but is "
            f"not declared in aria_service/requirements.txt"
        )


def test_the_smoke_install_list_covers_those_imports():
    """The curated list is what CI actually installs; requirements.txt is not used
    there (it would pull torch, ~3 GB). So the two must not drift apart."""
    text = (_WORKFLOWS / "test-aria.yml").read_text(encoding="utf-8")
    install_lines = [ln for ln in text.splitlines() if "pip install pytest" in ln or "lxml requests numpy" in ln]
    assert install_lines, "the smoke-test install list could not be located"
    joined = " ".join(install_lines)
    for package in ("orjson", "defusedxml"):
        assert package in joined, (
            f"{package} is a top-level production import missing from CI's install "
            f"list — test_imports will fail on it with a CI-only ImportError"
        )


def test_workflows_still_parse():
    """A workflow that dies at PARSE reports as a 0s run with empty jobs and is
    easy to mistake for 'nothing ran' (the R-F3378 failure, CI dead 2 months)."""
    for path in _workflow_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} does not parse to a mapping"
        assert data.get("jobs"), f"{path.name} declares no jobs"
