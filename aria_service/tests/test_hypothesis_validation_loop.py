"""Regression guard for F27 — hypothesis validation key bug.

Live observation 2026-04-27 19:32:25: '[Research] Validated 3/177
hypotheses'. Looked like the validator was sampling 3 of 177 OPEN
hypotheses per cycle. Actually: main.py read h.get("statement", "")
but the dict key is "hypothesis" (set by researcher._process_analysis
line 1132). Empty-string lookup then matched the FIRST hypothesis via
substring fallback inside validate_hypothesis, so the SAME hypothesis
was re-validated 3 times per cycle while the other 174 were ignored.

This test asserts the contract: hypotheses are stored under key
'hypothesis', not 'statement'. The main.py loop must read the right
key, otherwise we burn 3 LLM calls per cycle on the same item forever.
"""
from __future__ import annotations


def test_hypothesis_dict_key_contract():
    """The key inserted by _process_analysis must be 'hypothesis'."""
    from aria_service.intel import researcher
    import inspect
    src = inspect.getsource(researcher._process_analysis)
    # Confirm the dict literal uses "hypothesis" as key
    assert '"hypothesis": hyp["statement"]' in src, (
        "researcher._process_analysis stores hypotheses under key "
        "'hypothesis' — if you change this, the main.py validation loop "
        "and validate_hypothesis() must be updated too."
    )


def test_main_validation_loop_reads_hypothesis_key():
    """The validation loop in main.py must read 'hypothesis', not 'statement'.
    Pre-F27 the wrong key (silently empty) caused 3× re-validation of
    hypothesis #0 per cycle."""
    import inspect
    from aria_service import main
    # Locate the lifespan source
    src = inspect.getsource(main.lifespan)
    # The new code reads hyp_text = h.get("hypothesis", ""); the bug-pattern
    # was h.get("statement", ""). Assert the bug-pattern is gone.
    # Allow both `h.get("hypothesis"` and the assigned-then-used variant.
    assert 'h.get("hypothesis"' in src, (
        "main.py lifespan validation loop must read 'hypothesis' key"
    )
    # And the bug pattern must NOT be back
    assert 'h.get("statement"' not in src, (
        "main.py validation loop has the F27 bug pattern again — "
        "h.get('statement', '') always returns '' because dicts use "
        "'hypothesis' not 'statement'"
    )


def test_validation_loop_sorts_oldest_first():
    """Sort key was added so we work the oldest-OPEN backlog first."""
    import inspect
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    assert "open_hyps.sort" in src, (
        "validation loop should sort open hypotheses by age so older "
        "ones get a fresh evidence pass first"
    )


def test_validation_loop_skips_empty_hypothesis_text():
    """Defensive guard — if a hypothesis somehow has empty text, the
    loop must skip it instead of running validate_hypothesis (which
    would substring-match anything and validate the wrong record)."""
    import inspect
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    assert "if not hyp_text" in src, (
        "validation loop should skip empty-hypothesis-text rather than "
        "feed empty string into validate_hypothesis"
    )
