"""R-F1440 — regression guard: routes/aria.py must not reference an undefined
`logger`.

Live incident 2026-06-08: R-F1435 added `logger.warning(...)` inside
coder_llm_ep's prompt-clamp branch, but this module's logger is named `_log`
(line 194: `_log = logging.getLogger("aria.routes")`). `logger` is never
defined, so the call raised `NameError: name 'logger' is not defined` — but
ONLY on the clamp branch (prompt > 40k chars), which is exactly the
autonomous coder's real code-writing prompts. Result: every coder fix
returned 500 and spammed the operator's WhatsApp. Compile + the existing
tests passed because the branch was never exercised.

This is a static guard (the full app import hangs locally on the HF embedder,
so a TestClient test isn't runnable here). The actual user-path proof is the
post-deploy live re-reproduction: POST a >40k-char prompt to
/api/aria/coder/llm and assert it no longer 500s.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _routes_aria_path() -> Path:
    return Path(__file__).resolve().parents[1] / "routes" / "aria.py"


def test_no_bare_logger_reference_in_routes_aria():
    """Assert routes/aria.py uses `_log`, never an undefined `logger`."""
    src = _routes_aria_path().read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Collect every name that IS defined as `logger` (assignment / import-as),
    # so a legitimately-defined local `logger` would not trip this guard.
    defined_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "logger":
                    defined_logger = True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name) == "logger":
                    defined_logger = True

    # Find every attribute access on a bare name `logger` (logger.warning, etc.)
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "logger"
        ):
            offenders.append(node.lineno)

    assert not (offenders and not defined_logger), (
        f"routes/aria.py references an undefined `logger` at lines {offenders} "
        f"— use `_log` (the module logger). This raises NameError at runtime."
    )
