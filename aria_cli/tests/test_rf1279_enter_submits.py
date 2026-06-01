"""R-F1279 — Capability test: the REPL prompt submits on Enter.

Regression: R-F1267 created the prompt with ``multiline=True`` but
``KeyBindings`` was never imported, so ``_build_key_bindings()`` raised
``NameError`` and returned ``None``. With no bindings + multiline, prompt_toolkit's
default made plain Enter insert a newline (Alt+Enter submitted). The operator's
typed task was therefore never handed to the agent — "ARIA does not respond at
all" — and the completion menu appeared instead.

The fix: import ``KeyBindings`` and bind Enter→submit, Alt+Enter→newline. This
test invokes the actual broken path (``_build_key_bindings``) and asserts the
user-visible contract: Enter submits, Alt+Enter inserts a newline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_cli import cli


class _FakeBuffer:
    def __init__(self) -> None:
        self.submitted = False
        self.text = ""

    def validate_and_handle(self) -> None:
        self.submitted = True

    def insert_text(self, t: str) -> None:
        self.text += t


class _FakeEvent:
    def __init__(self) -> None:
        self.current_buffer = _FakeBuffer()


def _binding(kb, *, n_keys: int, last: str = "Keys.ControlM"):
    return [b for b in kb.bindings if len(b.keys) == n_keys and str(b.keys[-1]) == last]


@pytest.mark.skipif(not cli.PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit not installed")
def test_build_key_bindings_does_not_return_none() -> None:
    """The function must build real bindings — the NameError bug returned None."""
    assert cli._build_key_bindings() is not None


@pytest.mark.skipif(not cli.PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit not installed")
def test_enter_submits() -> None:
    kb = cli._build_key_bindings()
    enter = _binding(kb, n_keys=1)
    assert enter, "no plain-Enter binding — Enter would never submit"
    ev = _FakeEvent()
    enter[0].handler(ev)
    assert ev.current_buffer.submitted is True
    assert ev.current_buffer.text == "", "Enter must submit, not insert a newline"


@pytest.mark.skipif(not cli.PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit not installed")
def test_alt_enter_inserts_newline() -> None:
    kb = cli._build_key_bindings()
    alt_enter = _binding(kb, n_keys=2)
    assert alt_enter, "no Alt+Enter binding"
    ev = _FakeEvent()
    alt_enter[0].handler(ev)
    assert ev.current_buffer.text == "\n"
    assert ev.current_buffer.submitted is False, "Alt+Enter must insert a newline, not submit"


@pytest.mark.skipif(not cli.PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit not installed")
def test_ctrl_k_palette_binding_present() -> None:
    """Ctrl+K (command palette) was also dead while the function returned None."""
    kb = cli._build_key_bindings()
    ctrl_k = _binding(kb, n_keys=1, last="Keys.ControlK")
    assert ctrl_k, "no Ctrl+K binding"
