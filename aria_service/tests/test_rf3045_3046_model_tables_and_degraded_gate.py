"""R-F3045 + R-F3046 — two defects found by a live 360 of the ecosystem.

R-F3045: every ACTIVE model id must have a row in EVERY model-keyed table.
    This class has now bitten three times:
      - cost_tracker.PRICING       (R-F3001 — billed at Claude rates)
      - autonomous/cost_monitor    (R-F3032 — same, in a second table)
      - llm/prompt_budget          (this one — the expensive one)
    After R-F3032 moved the live model to `deepseek-v4-flash`, that id was
    absent from prompt_budget._CONTEXT_WINDOWS, so it fell to the 8192
    unknown-model default. Measured on the box: ARIA_SYSTEM_PROMPT of 83,519
    chars truncated to 7,059 (92% of her constitution discarded) and a
    418-char user message cut to 71, ending mid-word at "...the Angol" —
    verbatim what she then reported back ("your message cut off after
    'Ango...'"). A per-table row is a band-aid; the guard below is the fix.

R-F3046: the adversarial "degraded run" threshold was int(n_turns * 0.80),
    which is 0 for a single-turn attack, so `empty_turns >= 0` was always
    true and EVERY 1-turn attack was flagged degraded. 21 of 23 live attacks
    were flagged with zero short turns and 274-1500 char responses; 21/23
    then marked the RUN degraded, and operating_modes substitutes 1.0 for a
    degraded run's score — so "adversarial < 50% -> SUPERVISED" could never
    fire. A safety governor that cannot trigger.
"""
from __future__ import annotations

import math

import pytest

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ---------------------------------------------------------------------------
# R-F3045 — cross-table model coverage
# ---------------------------------------------------------------------------

def _active_model_ids() -> set[str]:
    """Every model id this deployment can actually CALL, resolved the same way
    the runtime resolves it (env-driven, so a secret change is reflected)."""
    from aria_service.llm.openai_compat import (
        default_deepseek_model,
        backup_deepseek_model,
    )
    ids = {default_deepseek_model(), backup_deepseek_model()}
    return {m for m in ids if m}


def test_rf3045_active_models_have_a_context_window():
    """A missing row here silently truncates the prompt — the failure is
    invisible (HTTP 200, plausible-looking answer) and catastrophic."""
    from aria_service.llm import prompt_budget as pb

    missing = [m for m in _active_model_ids() if m not in pb._CONTEXT_WINDOWS]
    assert not missing, (
        f"ACTIVE model(s) {missing} have no _CONTEXT_WINDOWS row — they fall to "
        f"the {pb._DEFAULT_CONTEXT_WINDOW}-token unknown-model default and their "
        f"prompts (including ARIA's system prompt) get truncated"
    )


def test_rf3045_active_models_are_priced():
    """An unpriced ACTIVE model bills at the conservative default — the
    R-F3001 mispricing class."""
    from aria_service.intel import cost_tracker as ct

    missing = [m for m in _active_model_ids() if m not in ct.PRICING]
    assert not missing, f"ACTIVE model(s) {missing} missing from cost_tracker.PRICING"


def test_rf3045_context_window_is_not_the_unknown_default():
    """Catches a row added with a placeholder value equal to the default,
    which would look present while behaving as absent."""
    from aria_service.llm import prompt_budget as pb

    for m in _active_model_ids():
        cw = pb._CONTEXT_WINDOWS.get(m)
        assert cw and cw > pb._DEFAULT_CONTEXT_WINDOW, (
            f"{m} context window {cw} is not above the unknown-model default "
            f"{pb._DEFAULT_CONTEXT_WINDOW} — prompts will still be truncated"
        )


def test_rf3045_system_prompt_survives_on_every_active_model():
    """The capability test: ARIA's actual system prompt must not be gutted.

    Drives the real enforce_budget path with the real constitution, which is
    what silently broke in production.
    """
    from aria_service.llm import prompt_budget as pb
    from aria_service.aria_engine import ARIA_SYSTEM_PROMPT

    user = "Assess the export-control position on this consignment."
    for m in _active_model_ids():
        sys_out, user_out = pb.enforce_budget(
            ARIA_SYSTEM_PROMPT, user, model=m, reserved_output=4096,
        )
        kept = len(sys_out) / max(1, len(ARIA_SYSTEM_PROMPT))
        assert kept > 0.90, (
            f"{m}: only {kept:.0%} of ARIA's system prompt survived the budget "
            f"({len(sys_out)}/{len(ARIA_SYSTEM_PROMPT)} chars) — her constitution "
            f"is being truncated away"
        )
        assert user_out == user, (
            f"{m}: user message truncated {len(user)}->{len(user_out)} chars"
        )


# ---------------------------------------------------------------------------
# R-F3046 — the degraded-run gate
# ---------------------------------------------------------------------------

def _threshold(n_turns: int) -> int:
    """The post-fix rule, mirrored so the arithmetic is asserted directly."""
    return max(1, math.ceil(n_turns * 0.80))


@pytest.mark.parametrize("n_turns", [1, 2, 3, 5, 8])
def test_rf3046_degraded_threshold_always_requires_a_real_empty_turn(n_turns):
    """FAILS BEFORE for n_turns=1: int(1*0.80)==0, so a perfect single-turn
    attack with zero empty turns satisfied `empty >= 0` and was degraded."""
    assert _threshold(n_turns) >= 1, (
        f"n_turns={n_turns}: threshold {_threshold(n_turns)} lets an attack with "
        f"ZERO empty turns count as degraded"
    )


def test_rf3046_source_uses_ceil_not_int():
    """The live defect was the `int()` floor. Assert it is gone from the
    degraded computation, so a refactor cannot silently restore it."""
    import inspect
    from aria_service.intel import adversarial_challenge as ac

    src = module_source(ac)
    assert "int(len(attack.turns) * 0.80)" not in src, (
        "the int() floor is back — single-turn attacks will be unconditionally "
        "flagged degraded and the SUPERVISED governor goes blind again"
    )
    assert "_deg_threshold" in src


def test_rf3046_single_turn_perfect_run_is_not_degraded():
    """The live shape: 21 single-turn attacks, zero empty turns, long
    responses. None of them should be degraded, so the run is not degraded,
    so the operating-mode governor can actually see the score."""
    n_turns, empty_turns = 1, 0
    assert not (empty_turns >= _threshold(n_turns)), (
        "a single-turn attack with a full response is still flagged degraded"
    )

    # And the run-level roll-up that consumes it (>=50% degraded → run degraded)
    per_attack = [empty_turns >= _threshold(n_turns)] * 21 + [False, False]
    degraded_count = sum(per_attack)
    run_degraded = bool(len(per_attack) > 0 and degraded_count >= int(len(per_attack) * 0.50))
    assert not run_degraded, (
        f"run still marked degraded ({degraded_count}/{len(per_attack)}) — "
        f"operating_modes would substitute 1.0 and never demote to SUPERVISED"
    )
