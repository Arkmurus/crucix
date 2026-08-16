"""R-F4079 (C-128) — the ARIA CLI terminal ran degraded, and its tests could not run.

TWO DEFECTS, both found by running the thing rather than reading it.

**1. The interactive terminal was silently degraded everywhere.**
`aria_cli/cli.py` imports `prompt_toolkit` inside a try/except and sets
`PROMPT_TOOLKIT_AVAILABLE`. That guard is correct — but the package was declared
in NO manifest (`aria_service/requirements.txt`, `requirements-ci.txt`,
`requirements-dev.txt`), so it was absent from this environment and the CLI fell
back for every operator who had not installed it by accident. What silently
disappears with it is the whole interactive layer:

  * tab completion (`WordCompleter`)
  * persistent history (`FileHistory`, incl. R-F1308's surrogate-safe subclass)
  * auto-suggest from history
  * R-F1383's `patch_stdout`, which is what lets the agent print ABOVE an
    always-active bottom input box instead of corrupting it

Measured here: with the package installed, `PROMPT_TOOLKIT_AVAILABLE` flips
False -> True. Nothing else changed.

**2. One missing optional dep aborted 67 test files.**
`aria_cli/tests/test_rf2053_terminal_capability.py` did a MODULE-LEVEL
`import prompt_toolkit.output.defaults`, so pytest raised a COLLECTION error:

    ERROR collecting aria_cli/tests/test_rf2053_terminal_capability.py
    ModuleNotFoundError: No module named 'prompt_toolkit'
    !!!! Interrupted: 1 error during collection !!!!

A collection error is worse than a failing test: pytest stops, so **45 CLI test
files and 22 service test files never ran at all**. A suite that cannot be
collected certifies nothing — the §1 shape, at suite scale.

`pytest.importorskip` is the fix for the second: a genuinely absent optional
dependency should skip ONE module, never silence the suite.

WHERE THE DEPENDENCY BELONGS. `aria_cli` is a local operator tool — verified
ABSENT from the production image (`/app/aria_cli` does not exist on aria-intel).
So it goes in `requirements-dev.txt`, not the prod manifest, keeping the runtime
lean per §6.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEV_REQS = _ROOT / "requirements-dev.txt"
_TERMINAL_TEST = _ROOT / "aria_cli" / "tests" / "test_rf2053_terminal_capability.py"


def _declared(name: str) -> bool:
    text = _DEV_REQS.read_text(encoding="utf-8")
    pattern = re.compile(rf"^\s*{re.escape(name)}\b", re.M | re.I)
    return bool(pattern.search(text))


def test_terminal_dependencies_are_declared():
    """An undeclared dep means the CLI degrades for everyone who didn't guess."""
    missing = [n for n in ("prompt_toolkit", "rich") if not _declared(n)]
    assert not missing, (
        f"{missing} power the CLI's interactive terminal (completion, history, "
        f"auto-suggest, R-F1383's always-active input box) but are declared in "
        f"no manifest — so the CLI silently runs in its fallback path"
    )


def test_the_capability_actually_turns_on():
    """Declaring is not enough — the guard must flip.

    Asserts the user-visible outcome rather than the import: `cli.py` sets this
    flag from a try/except, so it is the honest signal for "the interactive
    terminal is available".
    """
    import aria_cli.cli as cli

    assert cli.PROMPT_TOOLKIT_AVAILABLE is True, (
        "prompt_toolkit is declared but the CLI still reports its interactive "
        "layer unavailable — the terminal is running degraded"
    )


def test_a_missing_optional_dep_cannot_abort_collection():
    """The 67-file blast radius: one absent dep must skip ONE module, not the suite.

    Pinned on the SOURCE because the failure mode is collection-time: by the
    time a test body runs, collection has already succeeded, so no runtime
    assertion can observe the defect it guards against.
    """
    src = _TERMINAL_TEST.read_text(encoding="utf-8")

    assert "importorskip" in src, (
        "the terminal capability test hard-imports an optional dependency at "
        "module level. When it is absent pytest raises a COLLECTION error and "
        "stops — 45 CLI test files and 22 service test files never ran."
    )
    # A bare module-level `import prompt_toolkit...` must not come back.
    hard = re.search(r"^import\s+prompt_toolkit", src, re.M)
    assert hard is None, (
        f"module-level hard import restored at line "
        f"{src[:hard.start()].count(chr(10)) + 1 if hard else '?'} — use "
        f"pytest.importorskip so an absent optional dep skips one module"
    )
