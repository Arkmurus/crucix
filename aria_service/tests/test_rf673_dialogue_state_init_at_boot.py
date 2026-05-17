"""R-F673 — dialogue_state DB init explicit in lifespan.

Audit 2026-05-17: "Add explicit dialogue_state DB init to main.py
lifespan + pin test."

dialogue_state.py is lazy-init: the aiosqlite connection + schema
materialise on the first call to _ensure_conn(). That's fine for
normal traffic but means a boot-time misconfiguration (volume not
mounted, schema mismatch) only surfaces minutes later when the first
chat turn lands. Worse: /api/aria/health/live can go green over a
broken dialogue store.

R-F673 calls _ensure_conn() inside lifespan so any failure shows up
in the boot log and the operator catches it before traffic resumes.

The pin test prevents a future refactor from dropping the call.
"""
from __future__ import annotations

import re
from pathlib import Path


_MAIN_SRC = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def _lifespan_body() -> str:
    m = re.search(r"async def lifespan\(", _MAIN_SRC)
    assert m, "lifespan() not found"
    rest = _MAIN_SRC[m.end():]
    m2 = re.search(r"\nasync def \w+\(|\ndef \w+\(|\nclass \w+", rest)
    return _MAIN_SRC[m.start(): m.end() + (m2.start() if m2 else len(rest))]


def test_rf673_marker_present_in_lifespan():
    body = _lifespan_body()
    assert "R-F673" in body, "R-F673 marker missing from lifespan"


def test_rf673_dialogue_state_ensure_conn_called_at_boot():
    """The lifespan body must explicitly call dialogue_state._ensure_conn()
    so any boot-time DB issue surfaces in the boot log."""
    body = _lifespan_body()
    # Allow either `_ds_boot._ensure_conn()` (aliased import) or the
    # direct `dialogue_state._ensure_conn()` form.
    assert re.search(
        r"_ensure_conn\(\s*\)",
        body,
    ), "R-F673: _ensure_conn() not called inside lifespan"
    assert "dialogue_state" in body, (
        "R-F673: dialogue_state module must be imported in lifespan so "
        "the init call references the right module"
    )


def test_rf673_failure_logs_warning_not_silent():
    """If the init fails, it must log — never pass silently."""
    body = _lifespan_body()
    assert "R-F673] dialogue_state init failed" in body, (
        "R-F673: failure path must logger.warning so the operator sees "
        "the degradation in fly logs"
    )


def test_rf673_marker_present_in_repo():
    """Belt-and-braces: marker must show up in main.py at the repo level
    (the body-extraction above is fragile to refactors)."""
    assert "R-F673" in _MAIN_SRC
