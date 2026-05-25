"""R-F865 — periodic briefs get a larger completion token budget.

The 2026-05-25 "Arkmurus Weekly Intelligence Brief" truncated mid-section 2/8.
An 8-section brief needs more than the 4000-token chat default; R-F865 routes
periodic-brief requests to an 8000-token budget while normal chat keeps 4000.
"""
from __future__ import annotations

from aria_service.aria_engine import _completion_max_tokens


def test_brief_gets_higher_budget():
    assert _completion_max_tokens("Generate the Arkmurus Weekly Strategic Intelligence Brief") == 8000
    assert _completion_max_tokens("weekly intelligence brief") == 8000
    assert _completion_max_tokens("send me the daily briefing") == 8000


def test_normal_message_keeps_default():
    assert _completion_max_tokens("what is the status of the angola deal") == 4000
    assert _completion_max_tokens("who is the defence minister of kenya") == 4000
    assert _completion_max_tokens("") == 4000
    assert _completion_max_tokens(None) == 4000  # type: ignore[arg-type]


def test_both_completion_sites_use_helper():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "aria_engine.py").read_text(encoding="utf-8")
    assert src.count("_completion_max_tokens(message)") >= 2, (
        "R-F865 regression: both the chat and chat-stream completion calls must "
        "use the brief-aware token budget."
    )
