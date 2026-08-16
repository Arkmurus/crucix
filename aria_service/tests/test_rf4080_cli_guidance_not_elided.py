"""R-F4080 (C-129) — the CLI agent was working from a third of the constitution.

`aria_cli/prompt.py` injects CLAUDE.md + AGENTS.md into the system prompt, capped
by `_GUIDANCE_MAX_CHARS`. R-F2160 raised that cap from 16000 to 40000 because
the old value "silently dropped ~58% of each file — and the dropped half is
exactly where the load-bearing coding rules live". It sized 40000 to fit both
files "WHOLE (~38KB each today)".

MEASURED 2026-08-16:

    CLAUDE.md   120,871 chars   vs cap 40,000  ->  80,871 elided (67%)
    AGENTS.md    37,308 chars   vs cap 40,000  ->  fits

CLAUDE.md has TRIPLED. The cap that was sized to fit whole now drops **more than
the defect R-F2160 fixed**, and what lands in the elided middle is the operating
core: §22 verification discipline, §23 cross-check-before-claiming-fixed, §25
proprioception — and §21e, the rule that mandates this very injection.

Two comments in `prompt.py` still asserted "the coder still gets the full
CLAUDE.md/AGENTS.md via load_repo_guidance". That was true when written and had
become false, which is the failure mode this repo keeps paying for: a stale
claim in the one place a reader checks.

WHY RAISING THE NUMBER IS NOT ENOUGH. A fixed cap against a monotonically growing
file rots by construction — it has now rotted twice (16000, then 40000). §7
forbids eviction and this file only accretes. So the cap is raised AND a guard
fails loudly the moment a guidance file outgrows it, turning silent elision into
a decision someone has to make.

Cost is affordable because `load_repo_guidance` is called from
`build_system_prompt` — ONCE per session, not per turn.
"""
from __future__ import annotations

import pathlib

import pytest

from aria_cli import prompt as cli_prompt

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _guidance_files():
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = _ROOT / name
        if p.is_file():
            yield name, p.read_text(encoding="utf-8", errors="replace")


def test_the_binding_files_fit_the_cap():
    """ANTI-ROT: the moment a guidance file outgrows the cap, this fails loudly.

    Silent elision is what put the CLI agent on 33% of the constitution twice.
    If this goes red, decide deliberately — raise the cap, or split the file —
    but never let it elide unnoticed.
    """
    cap = cli_prompt._GUIDANCE_MAX_CHARS
    oversize = {
        name: (len(text), len(text) - cap)
        for name, text in _guidance_files() if len(text) > cap
    }
    assert not oversize, (
        f"guidance file(s) exceed _GUIDANCE_MAX_CHARS={cap} and will be ELIDED "
        f"in the CLI system prompt {{name: (size, overflow)}}: {oversize}"
    )


@pytest.mark.parametrize("marker", [
    "R-number discipline",   # §2  — head
    "proprioception",        # §25 — middle (the section that exposed this)
    "CURE MODE",             # §26 — tail
])
def test_load_repo_guidance_carries_head_middle_and_tail(marker):
    """The whole file must survive, not just its ends.

    Head-and-tail clipping is the right FALLBACK, but it silently dropped the
    operating core. Probing all three regions is what distinguishes "injected"
    from "injected whole".
    """
    guidance = cli_prompt.load_repo_guidance(_ROOT)
    assert guidance, "no guidance loaded at all"
    assert marker in guidance, (
        f"{marker!r} is missing from the injected guidance — the constitution "
        f"is being elided before it reaches the CLI agent"
    )


def test_clip_still_elides_when_genuinely_over_cap():
    """The fallback must remain honest — and must still MARK the elision.

    Raising the cap must not quietly disable the mechanism that protects the
    tail when a file really is too large.
    """
    text = "HEAD-MARKER" + ("x" * 5000) + "TAIL-MARKER"
    out = cli_prompt._clip_guidance(text, 1000)

    assert len(out) <= 1200, "clip did not bound the output"
    assert "HEAD-MARKER" in out, "the binding floor at the top was lost"
    assert "TAIL-MARKER" in out, "the operational rules at the bottom were lost"
    assert "ELIDED" in out.upper(), (
        "an elision must be MARKED — an unmarked truncation reads as a complete "
        "file, which is how the agent came to believe it had the whole thing"
    )
