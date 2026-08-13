"""R-F3961 guard against restoring stale Anthropic outage instructions."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_claude_guidance_records_recovery_and_requires_live_remeasurement() -> None:
    guidance = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section_start = guidance.index("Anthropic billing: RESTORED 2026-08-13")
    section_end = guidance.index("- Autonomy gate:", section_start)
    section = guidance[section_start:section_end]

    assert "Re-measure before you trust any billing line here" in section
    assert "HTTP 200" in section
    assert "CREDITS EXHAUSTED as of 2026-08-12" not in section
    assert "do NOT clear it" not in section
