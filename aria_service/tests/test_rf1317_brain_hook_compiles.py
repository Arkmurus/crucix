"""R-F1317 capability test — brain_hook.py must actually COMPILE.

R-F1316 closed absorb()'s docstring early with a stray triple-quote, turning the
Args: block into code and producing a SyntaxError in the brain's central module.
Its tests passed anyway because they only grep the source string — they never
import or compile the file (a CLAUDE.md §3c violation: a capability test must
invoke the broken path). On Windows the file would crash on import; on Fly
(Linux) aria-intel would fail to boot — a full outage had it deployed.

This test invokes the path that was actually broken: it compiles the module.
It is platform-independent (Python's parser does not differ Windows vs Linux),
which also disproves the "em-dashes only break Windows" claim in R-F1316.
"""
from __future__ import annotations

import py_compile
from pathlib import Path

_BRAIN_HOOK = (
    Path(__file__).resolve().parents[1] / "intel" / "brain_hook.py"
)


def test_brain_hook_compiles_cleanly():
    """brain_hook.py must compile with no SyntaxError (R-F1316 regression)."""
    assert _BRAIN_HOOK.exists(), f"missing: {_BRAIN_HOOK}"
    # doraise=True → raises py_compile.PyCompileError on any SyntaxError.
    py_compile.compile(str(_BRAIN_HOOK), doraise=True)


def test_absorb_docstring_is_not_prematurely_closed():
    """Guard the exact R-F1316 defect: the absorb() docstring must stay open
    through its Args: block, not be closed right after the R-F1316 note."""
    src = _BRAIN_HOOK.read_text(encoding="utf-8")
    i = src.index("async def absorb(")
    block = src[i:i + 1200]
    # The R-F1316 note and the Args: header must live in the SAME docstring,
    # i.e. no closing triple-quote between them.
    note = block.index("R-F1316: self-observes")
    args = block.index("Args:")
    assert note < args, "unexpected ordering"
    assert '"""' not in block[note:args], (
        "absorb() docstring is closed before its Args: block — the R-F1316 bug"
    )
