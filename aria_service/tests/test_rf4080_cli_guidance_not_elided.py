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


def test_the_injected_guidance_fits_its_budget(monkeypatch):
    """ANTI-ROT, RESTATED against the thing that now governs.

    R-F4321 — this test used to compare each file against `_GUIDANCE_MAX_CHARS`,
    and a peer review caught that it had become a guard certifying nothing:
    `load_repo_guidance` no longer reads that constant at all (it is only the
    explicit operator override), so the assertion stayed GREEN while CLAUDE.md
    was being clipped to ~3,015 chars under the sovereign. A pass meant "the
    file is under 200,000 chars", which is not a fact anyone needs.

    That is the "certified by an absence" shape §1 records three times, and it
    appeared here within hours of the code moving. The test now asserts the
    property that actually protects the prompt: whatever is injected fits the
    budget the model can afford.
    """
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")

    guidance = cli_prompt.load_repo_guidance(_ROOT)
    budget = cli_prompt.guidance_budget_chars()
    # + a small allowance for the per-file "----- NAME -----" banners
    assert len(guidance) <= budget + 200, (
        f"injected guidance is {len(guidance)} chars against a {budget}-char "
        f"budget — the prompt will overflow the window")


def test_the_override_still_caps_when_set(monkeypatch):
    """`_GUIDANCE_MAX_CHARS` is now ONLY the operator lever. Prove it still
    works, rather than leaving a constant nothing reads."""
    monkeypatch.setenv("ARIA_CODER_GUIDANCE_MAX_CHARS", "6000")
    guidance = cli_prompt.load_repo_guidance(_ROOT)
    assert len(guidance) <= 6200, len(guidance)


@pytest.mark.parametrize("marker", [
    "R-number discipline",   # §2  — head
    "proprioception",        # §25 — middle (the section that exposed this)
    "CURE MODE",             # §26 — tail
])
def test_load_repo_guidance_carries_head_middle_and_tail(marker, monkeypatch):
    """The whole file must survive, not just its ends.

    Head-and-tail clipping is the right FALLBACK, but it silently dropped the
    operating core. Probing all three regions is what distinguishes "injected"
    from "injected whole".

    R-F4319 (C-267) — NOW SCOPED TO A WINDOW THAT CAN ACTUALLY HOLD IT, and the
    scoping is the honest half of a collision this guard exposed.

    R-F4080 asserted an unconditional property: the constitution reaches the
    agent whole. That was right against the defect it was written for — a FIXED
    cap eliding text the model had ample room for. It is not achievable at all
    sizes: CLAUDE.md alone is ~33,135 tokens, and the sovereign's window is
    16,384. No cap can make 33,135 fit in 16,384; that is arithmetic, not a bug.

    So the two causes of elision are now distinguished, because they demand
    opposite responses:
      * elided though the model had ROOM  -> a stale constant. Still fails here.
      * elided because the model CANNOT hold it -> unavoidable; must be
        proportional and MARKED, which the companion test below pins.

    Pinning the large-window case keeps this guard able to fail: if the budget
    ever stops tracking the window, DeepSeek-class sessions lose the operating
    core again and this goes red exactly as it did in 2026-08-16.
    ⚠️ READ THIS BEFORE TRUSTING A GREEN RUN HERE. Since R-F4319 this test can
    pass because the marker appears in the injected TABLE OF CONTENTS rather
    than in the section BODY. That is a genuinely weaker guarantee than the one
    R-F4080 wrote, and it is stated out loud because a guard that quietly
    certifies less than its name claims is the exact shape §1 records three
    times. What it now proves is that no section is INVISIBLE — the agent can
    see the rule exists and `read_file` it. The companion test below pins the
    stronger property where the window can still afford it.
    """
    # A DeepSeek-class window — the largest we actually serve.
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.delenv("ARIA_LLM_MAX_MODEL_LEN", raising=False)
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)

    guidance = cli_prompt.load_repo_guidance(_ROOT)
    assert guidance, "no guidance loaded at all"
    assert marker in guidance, (
        f"{marker!r} is missing from the injected guidance entirely — not even "
        f"named in the contents. The agent cannot know the rule exists, which "
        f"is worse than eliding its body."
    )


@pytest.mark.parametrize("marker", [
    "R-number discipline", "proprioception", "CURE MODE",
])
def test_the_section_bodies_survive_when_the_window_can_afford_them(marker,
                                                                    monkeypatch):
    """R-F4080's ORIGINAL, stronger property, pinned where it is still reachable.

    Given a window large enough to hold the constitution, the section BODIES —
    not merely their headings — must arrive. This is what stops the budget
    derivation from silently degrading into "ship a table of contents" on a
    model that had room for the real thing.
    """
    monkeypatch.delenv("ARIA_CODER_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "200000")

    guidance = cli_prompt.load_repo_guidance(_ROOT)
    assert "ELIDED" not in guidance, (
        "the constitution was clipped on a window with ample room for it")
    assert marker in guidance


def test_a_small_window_elides_proportionally_and_says_so(monkeypatch):
    """R-F4319 — the unavoidable case must stay HONEST.

    When the model cannot hold the constitution, three things must hold, and
    none of them is 'ship it anyway and let the server 400':

      1. the guidance is smaller than the file (it really was clipped),
      2. BOTH files survive in some form — a budget that spends everything on
         CLAUDE.md and drops AGENTS.md entirely would silently lose laws 11-20,
      3. the elision is MARKED, so the agent knows to read the full file rather
         than concluding the rule does not exist.

    (3) is what stops a clipped constitution from reading as a complete one —
    the same absence-reads-as-present shape this repo keeps paying for.
    """
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)

    guidance = cli_prompt.load_repo_guidance(_ROOT)
    claude_len = len((_ROOT / "CLAUDE.md").read_text(encoding="utf-8",
                                                     errors="replace"))
    assert 0 < len(guidance) < claude_len, "expected clipping at a 16k window"
    assert "CLAUDE.md" in guidance and "AGENTS.md" in guidance, (
        "one file was dropped entirely instead of sharing the budget")
    assert "ELIDED" in guidance, (
        "clipped guidance must say so, or the agent reads a partial "
        "constitution as a complete one")


def test_the_budget_tracks_the_window_rather_than_a_constant(monkeypatch):
    """The anti-rot property, restated for the derived budget.

    R-F4080's lesson was that a FIXED cap rots by construction — it had already
    rotted twice (16000, then 40000). A budget that moves with the model cannot
    rot that way, and this asserts it actually moves.
    """
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)
    small = cli_prompt.guidance_budget_chars(window_tokens=16384,
                                             completion_tokens=4096)
    large = cli_prompt.guidance_budget_chars(window_tokens=65536,
                                             completion_tokens=8192)
    assert large > small * 4, (
        f"the budget is not tracking the window (small={small}, large={large})")


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
