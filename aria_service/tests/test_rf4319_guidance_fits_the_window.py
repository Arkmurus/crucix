"""R-F4319 / C-267 - the CLI system prompt was 3x the model's context window.

Measured after R-F4318 clamped the completion. The 400 persisted, and the numbers
said why: the completion reserve had halved (8192 -> 4096) while the message side
was UNCHANGED at 63,419 tokens - on `status`, an early command with no history.
So it was never accumulated conversation.

    system prompt   188,814 chars  ~47,203 tokens
    tool schemas     15,486 chars  ~ 3,871 tokens
    combined                       ~51,075 tokens   vs a 16,384 window

The prompt alone is three times the window, so compaction could never help: it
stubs old TOOL OUTPUT, and the hog is the prompt.

The dominant contributors are the governance docs injected into every session:

    CLAUDE.md   132,540 chars  ~33,135 tokens
    AGENTS.md    37,308 chars  ~ 9,327 tokens

bounded by `_GUIDANCE_MAX_CHARS`, a FIXED 200,000 chars (~50k tokens). That is
the third instance of one shape in this area - a constant that silently encodes
a large vendor's capacity (COMPACT_CHAR_BUDGET was 180,000; max_tokens was 8192).
Each was correct for deepseek-chat and fatal for a 16k model.

THE FIX DERIVES THE BUDGET FROM THE MODEL, and reserves room for the things that
must also fit: the tool schemas, some conversation, and the completion. A
governance budget that does not know the window is a budget for one vendor.

THE ELISION SHAPE IS PRESERVED. `_clip_guidance` keeps head (60%) + tail (40%)
with a marked elision, because the binding floor is at the top of CLAUDE.md and
the operational rules are at the bottom; a head-only truncation would silently
drop half the constitution. Shrinking the budget must not change that.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import prompt as P  # noqa: E402


def test_the_budget_is_derived_from_the_window() -> None:
    """THE CAPABILITY TEST. 200,000 chars against a 16,384-token model is the
    defect: the system prompt cannot fit before a single message exists."""
    assert hasattr(P, "guidance_budget_chars"), "no window-aware budget exists"
    small = P.guidance_budget_chars(window_tokens=16384, completion_tokens=4096)
    assert small < 200000, "the budget ignored the window"
    # must leave room for tool schemas (~3,900 tok) + some conversation
    assert small <= (16384 - 4096 - 3900 - 2000) * 4


def test_a_large_window_keeps_a_large_budget() -> None:
    """A big window must buy a much bigger budget — but NOT the whole file.

    This assertion originally read `big >= 150000`, on the belief that DeepSeek
    has room for the entire constitution and clipping it there would lose rules
    for no reason. Measuring against the real tokenizer disproved that belief:

        CLAUDE.md + AGENTS.md   169,848 chars = ~53,700 tokens
        + system-prompt base                    ~5,300
        + tool schemas                          ~1,900
        + completion reserve                     8,192
        =                                      ~69,100  vs a 65,536 window

    So the full constitution does not fit DeepSeek either, and has not for some
    time — R-F4080's "inject it whole" guarantee stopped being an option that
    exists rather than one we chose against. Nothing measured it in TOKENS, so
    nothing noticed.

    What is asserted instead is the property that actually matters: the budget
    scales with the window, and never exceeds what the window can hold.
    """
    big = P.guidance_budget_chars(window_tokens=65536, completion_tokens=8192)
    small = P.guidance_budget_chars(window_tokens=16384, completion_tokens=4096)
    assert big > small * 4, "the budget is not scaling with the window"
    # never promise more room than exists
    assert big <= (65536 - 8192) * 4


def test_the_budget_never_goes_absurdly_small() -> None:
    """A tiny window must still yield a usable floor rather than an empty
    constitution - no rules at all is worse than elided rules."""
    tiny = P.guidance_budget_chars(window_tokens=4096, completion_tokens=1024)
    assert tiny >= 2000


def test_an_explicit_override_still_wins(monkeypatch) -> None:
    """The operator lever survives; deriving a default is not removing control."""
    monkeypatch.setenv("ARIA_CODER_GUIDANCE_MAX_CHARS", "12345")
    assert P.guidance_budget_chars(window_tokens=16384, completion_tokens=4096) == 12345


# -- the elision shape must not change -------------------------------------

def test_clipping_keeps_head_and_tail() -> None:
    """The binding floor is at the TOP of CLAUDE.md and the operational rules at
    the BOTTOM. A head-only truncation silently drops half the constitution."""
    text = ("HEAD-MARKER\n" + ("x" * 50000) + "\nTAIL-MARKER")
    out = P._clip_guidance(text, 2000)
    assert "HEAD-MARKER" in out
    assert "TAIL-MARKER" in out
    assert "ELIDED" in out


def test_clipping_is_a_noop_under_the_cap() -> None:
    text = "short constitution"
    assert P._clip_guidance(text, 100000) == text


# -- the real prompt must fit the real window ------------------------------

def test_the_built_prompt_fits_a_16k_window(monkeypatch) -> None:
    """End to end: the thing actually sent must fit, with room for tools and a
    conversation. This is the assertion the live 400 would have failed."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)
    sp = P.build_system_prompt(root=ROOT, self_mode=True, repo_root=ROOT)
    est_tokens = len(sp) // 4
    assert est_tokens < 9000, (
        f"system prompt is ~{est_tokens} tokens; with ~3,900 for tool schemas "
        "and 4,096 reserved for the answer it cannot fit a 16,384 window")


def test_the_prompt_still_carries_the_binding_rules(monkeypatch) -> None:
    """Fitting the window must not mean shipping an empty constitution."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    sp = P.build_system_prompt(root=ROOT, self_mode=True, repo_root=ROOT)
    low = sp.lower()
    assert "r-number" in low or "verify" in low, (
        "the governance content was clipped away entirely")


# -- the fallback must not be more permissive than what it replaces ---------

def test_an_unbuildable_config_still_honours_the_declared_window(monkeypatch) -> None:
    """R-F4319, second pass — caught by my own test, not by reading.

    `LLMConfig.from_env` RAISES for provider 'aria-llm' with no ARIA_LLM_URL
    ("Refusing to fall back to another model"), which is correct of it. The
    first version of this fix caught that and fell back to a 65,536 window, so
    a MISCONFIGURED sovereign got the LARGEST budget of all: 169,894 chars of
    guidance into a 16,384-token window - the exact overflow this exists to
    prevent, reached through its own error path.

    A fallback must never be more permissive than the thing it stands in for.
    """
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)          # makes from_env raise
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    monkeypatch.delenv("ARIA_CODER_GUIDANCE_MAX_CHARS", raising=False)

    win, completion = P.model_window_tokens()
    assert win == 16384, f"the declared window was discarded (got {win})"
    assert completion <= 16384 // 4

    budget = P.guidance_budget_chars()
    assert budget < 20000, (
        f"budget {budget} came from a fallback larger than the declared window")


def test_a_declared_window_wins_over_the_provider_default(monkeypatch) -> None:
    """ARIA_LLM_MAX_MODEL_LEN must match the vLLM --max-model-len. A provider
    default that silently overrode it would disagree with the server."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "8192")
    win, _ = P.model_window_tokens()
    assert win == 8192


def test_no_declared_window_and_no_config_assumes_large(monkeypatch) -> None:
    """The opposite error matters too: collapsing to a tiny budget when nothing
    states a window would strip the constitution for no reason."""
    for v in ("ARIA_LLM_MAX_MODEL_LEN", "ARIA_LLM_URL", "DEEPSEEK_API_KEY",
              "ARIA_CODER_GUIDANCE_MAX_CHARS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")   # from_env raises
    win, _ = P.model_window_tokens()
    assert win >= 32768
