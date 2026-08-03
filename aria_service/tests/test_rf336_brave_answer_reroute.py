"""R-F336 — brave_answer chat tool dispatch reroutes to web_explorer.

R-F320 permanently stubbed brave_answers. But the chat dispatch
handler at routes/aria.py:tool=="brave_answer" was still calling the
stub and reporting "tool unavailable" — falling back to training
knowledge only. Live failure 2026-05-11 22:40: operator asked "who is
Michele Zagaria" and got a brave_answer-UNAVAILABLE response with
biographical knowledge from training instead of real web facts.

R-F336 reroutes the brave_answer chat tool to web_explorer.explore
(memory-first + free multi-backend search) — same Q&A capability,
zero cost, real provenance.
"""
from __future__ import annotations

from ._source_probe import repo_path


def test_rf336_brave_answer_handler_routes_to_web_explorer():
    """Source-level capability — the brave_answer chat handler now
    imports and calls web_explorer.explore, not brave_answers.ask."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")

    # Find the brave_answer handler block
    marker = 'if tool == "brave_answer":'
    idx = src.find(marker)
    assert idx > 0, "brave_answer dispatch block not found"
    # The block continues until the next `if tool ==` or similar boundary
    next_tool = src.find('\n        if tool == "', idx + 1)
    if next_tool < 0:
        next_tool = idx + 6000
    block = src[idx:next_tool]

    # Must reference web_explorer + R-F336
    assert "web_explorer" in block, (
        "R-F336 regression: brave_answer handler doesn't import web_explorer"
    )
    assert "R-F336" in block, (
        "R-F336 regression: reroute not annotated for traceability"
    )
    # Must NOT call the stubbed brave_answers.ask any more
    assert "brave_answers as _ba" not in block, (
        "R-F336 regression: still importing brave_answers — stub will be called"
    )
    assert "_ba.ask(" not in block


def test_rf336_handler_emits_explore_health_signal():
    """The chat tool output now includes the ecosystem health signal
    so the LLM can SEE which backends fired vs silent — operator can
    distinguish 'real zero results' from 'all backends down'."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find('if tool == "brave_answer":')
    block = src[idx:idx + 6000]
    assert "health=" in block
    assert "backends_active=" in block
    assert "memory=" in block


def test_rf336_handler_emits_provenance_per_fact():
    """Each fact in the chat output must carry tier + backend + URL
    so the LLM can cite verifiably. Without this the LLM falls back
    to training knowledge."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find('if tool == "brave_answer":')
    block = src[idx:idx + 6000]
    # tier + backend + URL must appear in the output formatting
    assert "tier" in block
    assert "URL:" in block
    assert "f.source_url" in block or "source_url" in block


def test_rf336_handler_instructs_llm_to_avoid_training_fallback():
    """When zero facts come back from web_explorer, the handler must
    instruct the LLM to say so explicitly — NOT silently fall back to
    training knowledge. That's what R-F315 (content-vs-allegation) +
    R-F304 (GROUNDING_CHECK) demand."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find('if tool == "brave_answer":')
    block = src[idx:idx + 6000]
    # Must reference the R-F315 / R-F304 discipline anchors
    assert "R-F315" in block or "content-vs-allegation" in block.lower()
    assert "R-F304" in block or "GROUNDING_CHECK" in block
    # Must explicitly forbid training-knowledge fallback when facts empty
    fallback_indicators = ["do not fall back", "do not silently",
                           "do not invent", "training knowledge"]
    assert any(s in block.lower() for s in fallback_indicators), (
        f"R-F336: handler doesn't ban training-knowledge fallback. "
        f"Need an explicit instruction so the LLM doesn't hallucinate "
        f"biographical facts."
    )
