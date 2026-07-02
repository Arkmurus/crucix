"""R-F2303 — COMMAND-mode prompt must not instruct fabricated per-tool ETAs.

The frozen "web_search … running, ETA 2m" chat preview (which sat for 12.5h after
a DD died, 2026-07-02) came from ARIA narrating tools per the COMMAND-mode prompt,
which used to say "what result (or 'running, ETA Xm')". That invites the LLM to
fabricate per-tool countdowns that freeze on reload. The block now forbids that and
directs honest background-task guidance.
"""
from aria_service.intel.dialogue_router import (
    build_response_mode_block, DialogueIntent, _COMMAND_BLOCK,
)


def test_command_block_forbids_fabricated_per_tool_eta():
    blk = build_response_mode_block(DialogueIntent.COMMAND)
    assert blk == _COMMAND_BLOCK
    low = blk.lower()
    # No longer INVITES the frozen 'running, ETA Xm' per-tool narration...
    assert "(or 'running, eta xm')" not in low
    # ...and explicitly forbids fabricated per-tool countdowns + directs honesty.
    assert "background" in low
    assert "freeze" in low or "frozen" in low
    assert "do not" in low or "don't" in low or "never" in low
    assert "10-15 min" in low or "when ready" in low


def test_other_modes_unchanged():
    # Only COMMAND changed; DIALOGUE/REPORT keep their contract.
    assert "DIALOGUE" in build_response_mode_block(DialogueIntent.DIALOGUE)
    assert "REPORT" in build_response_mode_block(DialogueIntent.REPORT)
