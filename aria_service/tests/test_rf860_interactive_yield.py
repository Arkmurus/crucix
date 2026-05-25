"""R-F860 — autonomous absorbs yield the encoder to interactive activity (FIX 1c).

Finding #1, the structural half: the GIL-bound encoder is shared between the
autonomous absorb storm and user-facing requests. When a user uploads a contract
(/read-document) or chats, that request must WIN the encoder — otherwise it times
out behind the storm (the 6× contract-upload failure). mark_interactive() records
user activity; while it's recent, _absorb_pause_ms() returns the larger
interactive pause so autonomous absorbs back off.
"""
from __future__ import annotations

import time
import pytest

from aria_service.intel import brain_hook as bh


def _set_quiet():
    # Far-past timestamp → no recent interactive activity.
    bh._last_interactive_at = time.monotonic() - (bh._INTERACTIVE_YIELD_WINDOW_S + 50)


def test_quiet_uses_base_pause(monkeypatch):
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    _set_quiet()
    assert bh._interactive_active() is False
    assert bh._absorb_pause_ms() == 0


def test_mark_interactive_makes_absorbs_back_off(monkeypatch):
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    monkeypatch.delenv("ARIA_BRAIN_INTERACTIVE_PAUSE_MS", raising=False)
    bh.mark_interactive()
    assert bh._interactive_active() is True
    # default interactive pause = 400ms even with base pause 0
    assert bh._absorb_pause_ms() == 400
    _set_quiet()


def test_operator_base_pause_never_reduced(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "600")
    monkeypatch.delenv("ARIA_BRAIN_INTERACTIVE_PAUSE_MS", raising=False)
    bh.mark_interactive()
    # max(base=600, interactive=400) — interactive never lowers the operator's floor
    assert bh._absorb_pause_ms() == 600
    _set_quiet()


def test_interactive_pause_is_tunable(monkeypatch):
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    monkeypatch.setenv("ARIA_BRAIN_INTERACTIVE_PAUSE_MS", "800")
    bh.mark_interactive()
    assert bh._absorb_pause_ms() == 800
    _set_quiet()


def test_yield_window_expires(monkeypatch):
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    # mark, then simulate the window elapsing
    bh._last_interactive_at = time.monotonic() - (bh._INTERACTIVE_YIELD_WINDOW_S + 1)
    assert bh._interactive_active() is False
    assert bh._absorb_pause_ms() == 0


def test_endpoints_mark_interactive():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    # read-document (the contract path) + both chat paths must mark interactive.
    assert src.count("mark_interactive()") >= 3, (
        "R-F860 regression: fewer than 3 interactive-mark call sites — the "
        "read-document / chat endpoints must mark interactive so absorbs yield."
    )
