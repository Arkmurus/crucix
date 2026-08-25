"""R-F4326 / C-274 — the prompt budget guessed the sovereign's window, wrongly.

OBSERVED LIVE 2026-08-25, seconds after ARIA_LLM_PRIMARY_ALL=1 made her the
chain primary:

    ERROR | aria.llm.prompt_budget | Even after truncation, prompt ~8495
    tokens exceeds budget 7392 for model 'aria-llm-v0.4-dpo' —
    this should not happen

7392 is 8192 - 800. The served window is 32768.

MECHANISM. `_CONTEXT_WINDOWS` carries `"aria-llm-v0.1": 32768`. The served
model is `aria-llm-v0.4-dpo`. Exact match misses; the prefix pass asks
`"aria-llm-v0.4-dpo".startswith("aria-llm-v0.1")`, which is False; so it
falls through to `_DEFAULT_CONTEXT_WINDOW = 8192` — a QUARTER of the real
window. Every sovereign prompt was then truncated to fit a budget four
times too small, and the guard fired on prompts that fit comfortably.

The entry was correct when written. The model was renamed v0.1 -> v0.4-dpo
and this table was never touched, so the rot arrived with a rename rather
than an edit — nothing in the diff of any commit shows it breaking.

THIS IS THE THIRD INDEPENDENT WINDOW FOR ONE MODEL:
  1. ARIA_LLM_MAX_MODEL_LEN  (the env var that MUST match vLLM's
     --max-model-len; the authoritative statement of what is served)
  2. aria_llm_provider._max_model_len()  (reads that env var)
  3. prompt_budget._CONTEXT_WINDOWS      (a per-version literal — this bug)
CLAUDE.md §1/R-F2639 records the same shape twice ("there is ONE measure
now, do not fork it again"), and R-F4318/R-F4321 have just finished
collapsing the CLI's two disagreeing budgets into one.

THE FIX IS TO ASK THE MODEL, NOT A LITERAL. For the sovereign, the window
comes from ARIA_LLM_MAX_MODEL_LEN — the same value the provider and the
server itself read. A version literal cannot survive the next rename;
reading the env var makes v0.5, v0.6 and every future checkpoint correct
with no edit.

WHAT MUST NOT HAPPEN: "fixing" this by adding "aria-llm-v0.4-dpo": 32768 to
the table. That greens today and rots at the next rename — it is the defect,
re-applied. A test below pins that the resolution is env-driven.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.llm import prompt_budget as pb  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("ARIA_LLM_MAX_MODEL_LEN", "ARIA_LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)


# -- THE CAPABILITY TEST ------------------------------------------------

def test_the_served_sovereign_gets_its_real_window(monkeypatch):
    """THE LIVE ERROR. 'aria-llm-v0.4-dpo' resolved to 8192, so the budget was
    7392 and every prompt over that was truncated for no reason."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")
    assert pb.get_context_window("aria-llm-v0.4-dpo") == 32768, (
        "the sovereign's window is still coming from a version literal; "
        "the served window is ARIA_LLM_MAX_MODEL_LEN"
    )


def test_a_future_checkpoint_needs_no_code_change(monkeypatch):
    """The whole point. v0.1 was right, then a rename made it wrong. A version
    that does not exist yet must already resolve correctly."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.9-grpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")
    assert pb.get_context_window("aria-llm-v0.9-grpo") == 32768


def test_the_budget_no_longer_truncates_a_prompt_that_fits(monkeypatch):
    """The user-visible symptom: a ~8,495-token prompt is comfortable in a
    32,768 window and was being cut to 7,392."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")
    window = pb.get_context_window("aria-llm-v0.4-dpo")
    budget = window - pb._RESERVED_OUTPUT_TOKENS
    assert budget > 8495, (
        f"budget {budget} still cannot hold the observed 8,495-token prompt"
    )


# -- the fix must not be a new literal ----------------------------------

def test_the_resolution_is_env_driven_not_a_version_entry():
    """Adding 'aria-llm-v0.4-dpo': 32768 to the table would green the tests
    above and rot at the next rename. Pin that the env var is consulted."""
    import inspect
    src = inspect.getsource(pb.get_context_window)
    assert "ARIA_LLM_MAX_MODEL_LEN" in src, (
        "get_context_window does not consult ARIA_LLM_MAX_MODEL_LEN — if this "
        "was 'fixed' by adding another version literal, the next rename "
        "reintroduces C-274"
    )


def test_it_tracks_the_env_var_rather_than_returning_a_constant(monkeypatch):
    """A hardcoded 32768 would pass every test above. Move the env var and the
    answer must move with it."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    assert pb.get_context_window("aria-llm-v0.4-dpo") == 16384


# -- the safety properties ----------------------------------------------

def test_an_unset_window_does_not_invent_a_large_one(monkeypatch):
    """If nothing states the window we must NOT assume it is big — an
    over-large budget posts a prompt the server rejects with HTTP 400, which
    is the failure R-F4317 exists to prevent. Falling back to the documented
    default is the safe direction."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.delenv("ARIA_LLM_MAX_MODEL_LEN", raising=False)
    w = pb.get_context_window("aria-llm-v0.4-dpo")
    assert w <= 32768, f"invented a window of {w} with nothing declaring one"


def test_a_junk_env_value_does_not_crash_or_zero_the_budget(monkeypatch):
    """A typo in a secret must not take the budget to zero and truncate every
    prompt to nothing."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    for junk in ("", "  ", "not-a-number", "0", "-5"):
        monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", junk)
        w = pb.get_context_window("aria-llm-v0.4-dpo")
        assert w >= 512, f"junk value {junk!r} produced an unusable window {w}"


def test_other_models_are_untouched(monkeypatch):
    """Scoped to the sovereign. Every other entry is a real published window
    and must keep resolving from it.

    ARIA_LLM_MAX_MODEL_LEN IS SET HERE DELIBERATELY, and that is the whole
    point of the test. In production it is always set, so a scope bug — one
    that treats every model as the sovereign — would hand DeepSeek a 32,768
    window instead of its real 65,536 and silently halve its usable prompt.
    Mutation testing found exactly that: without this env var set, widening
    the match to every model left all eight tests green.
    """
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")
    assert pb.get_context_window("deepseek-chat") == pb._CONTEXT_WINDOWS["deepseek-chat"]
    assert pb.get_context_window("gpt-4o") == 128000
    assert pb.get_context_window("totally-unknown-model") == pb._DEFAULT_CONTEXT_WINDOW
