"""R-F4318 / C-266 - the coder CLI sent more context than the model can hold.

Observed live the moment the CLI was pointed at the sovereign model:

    HTTP 400: This model's maximum context length is 16384 tokens. However, you
    requested 71611 tokens (63419 in the messages, 8192 in the completion).

Two independent causes, both of which assume a large-window provider:

  1. COMPACT_CHAR_BUDGET is a FIXED 180,000 chars (~45k tokens). Its own comment
     says it is tuned for "deepseek-chat ~64K tokens". Nothing ties it to the
     model actually in use, so against a 16,384-token model the CLI compacts far
     too late - or never.
  2. max_tokens defaults to 8192, which is HALF the sovereign's entire window
     before a single message is added. Even an empty conversation reserves half
     the context for the answer.

Neither is a bug in R-F2164 or in the default; both are the same shape - a
constant that silently encodes one provider's capacity. The server side already
learned this (R-F1363 clamps, R-F4317 refuses); the CLI never did.

THE FIX DERIVES BOTH FROM THE MODEL. The window is config, not a literal, so a
model swap moves the budget with it.

WHAT MUST NOT HAPPEN: silently dropping the user's own instructions. Compaction
stubs OLD TOOL OUTPUT - re-runnable, and R-F2164 deliberately keeps message
structure and all reasoning intact. Trimming the human's turns to fit would make
the model answer a question nobody asked, which is the same class as inventing a
prompt (C-257) or truncating a DD prompt (C-265).
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import llm as cli_llm  # noqa: E402

_ENV = ("ARIA_LLM_URL", "ARIA_LLM_MODEL", "ARIA_LLM_MAX_MODEL_LEN",
        "ARIA_CODER_LLM_PROVIDER", "ARIA_CODER_LLM_MAX_TOKENS",
        "ARIA_CODER_COMPACT_CHARS", "DEEPSEEK_API_KEY")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)


def _sovereign(monkeypatch, window="16384"):
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    monkeypatch.setenv("ARIA_LLM_URL", "http://sovereign.invalid/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", window)
    return cli_llm.LLMConfig.from_env()


# -- the window is known to the config ------------------------------------

def test_the_config_knows_the_model_window(monkeypatch) -> None:
    c = _sovereign(monkeypatch)
    assert c.max_model_len == 16384


def test_a_big_window_provider_keeps_a_big_budget(monkeypatch) -> None:
    """The fix must not shrink DeepSeek's headroom - that would make every long
    coding session compact needlessly."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    c = cli_llm.LLMConfig.from_env()
    assert c.max_model_len >= 64000


# -- max_tokens must not eat the window ------------------------------------

def test_max_tokens_cannot_exceed_the_window(monkeypatch) -> None:
    """8192 is half the sovereign's entire context before any message exists.

    R-F4321 (C-269) — REWRITTEN because the original assertion ADMITTED THE
    DEFECT. It read `max_tokens < max_model_len // 2 + 1`, and the `+ 1` makes
    8192 < 8193 true: the exact value the docstring calls out as the bug passed
    its own guard. A peer review deleted the clamp entirely and all 51 tests
    stayed green, which is the proof that matters — one of the two causes
    R-F4318 names was protected by nothing.

    The clamp is `window // 4`, so that is what is asserted.
    """
    monkeypatch.setenv("ARIA_CODER_LLM_MAX_TOKENS", "8192")
    c = _sovereign(monkeypatch)
    assert c.max_tokens <= c.max_model_len // 4, (
        f"max_tokens={c.max_tokens} was not clamped to a quarter of the "
        f"{c.max_model_len} window")
    assert c.max_tokens == 4096, c.max_tokens


def test_the_completion_reserve_leaves_room_for_messages(monkeypatch) -> None:
    """Also rewritten: `>= 8000` was true for BOTH 8192 and 4096 on a 16,384
    window, so it could not tell the defect from the fix either."""
    c = _sovereign(monkeypatch)
    assert c.max_model_len - c.max_tokens >= c.max_model_len * 3 // 4, (
        "the conversation must get at least three quarters of the window")


# -- the compaction budget follows the model -------------------------------

def test_the_compaction_budget_is_derived_not_fixed(monkeypatch) -> None:
    """THE CAPABILITY TEST. 180,000 chars (~45k tokens) against a 16,384-token
    model is the defect: the CLI compacts far too late, or never."""
    import importlib
    from aria_cli import agent as ag
    c = _sovereign(monkeypatch)
    budget = ag.compact_budget_chars(c)
    # R-F4321 (C-269) — this assertion USED TO SHARE THE DEFECT'S ASSUMPTION.
    # It read `budget <= (max_model_len - max_tokens) * 4`, reserving nothing
    # for the tool schemas sent on every call, so it could never catch the very
    # omission that let the budget overflow by 4,839 tokens. It now checks the
    # WHOLE allocation, which is the only thing the model actually sees.
    from aria_cli.prompt import context_budget
    b = context_budget(window_tokens=c.max_model_len,
                       completion_tokens=c.max_tokens)
    total = (b["guidance_tokens"] + b["history_tokens"]
             + b["overhead"] + b["tools"] + b["completion"])
    assert total <= c.max_model_len, (
        f"the full allocation is {total} tokens against a {c.max_model_len} "
        f"window: {b}")
    # R-F4321 — NOT an absolute floor. At a 16,384 window, once the constitution
    # and the tool schemas are paid for, there is genuinely little room left for
    # conversation; asserting otherwise would demand capacity the model does not
    # have. The windows we actually serve are pinned separately below.
    assert budget > 0
    assert budget == max(2000, b["history_chars"])


def test_a_large_window_still_gets_a_large_budget(monkeypatch) -> None:
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    from aria_cli import agent as ag
    c = cli_llm.LLMConfig.from_env()
    # Scaled with the window rather than a literal: the history share is now a
    # fraction of real slack, so a fixed floor here would just re-encode one
    # vendor's size — the defect this file exists to record.
    assert ag.compact_budget_chars(c) >= 50000


def test_an_explicit_override_still_wins(monkeypatch) -> None:
    """The operator lever must survive; deriving a default is not the same as
    removing the control."""
    monkeypatch.setenv("ARIA_CODER_COMPACT_CHARS", "50000")
    from aria_cli import agent as ag
    c = _sovereign(monkeypatch)
    assert ag.compact_budget_chars(c) == 50000


# -- the human's turns are never dropped -----------------------------------

def test_compaction_stubs_tool_output_not_user_turns() -> None:
    """Trimming the human's own instructions would make the model answer a
    question nobody asked - the same class as inventing a prompt (C-257)."""
    src = (ROOT / "aria_cli/agent.py").read_text(encoding="utf-8")
    i = src.index("def _compact")
    body = src[i:i + 2500]
    assert 'role") == "tool"' in body or "'tool'" in body, (
        "compaction must target tool output specifically")
    assert '"user"' not in body.split("stub =")[0], (
        "compaction appears to consider user turns - it must not")


# -- reading config must never mutate the process --------------------------

def test_the_provider_override_does_not_export_to_the_environment(monkeypatch) -> None:
    """R-F4318 (C-266) — the first version of R-F4303's override wrote
    provider_override into os.environ, so READING the config MUTATED the
    process. Every later caller inherited it, and it leaked across test
    boundaries: a peer's sub-agent test passed alone and failed after mine.

    A getter with a side effect on global state is a defect whether or not the
    value it returns is correct.
    """
    monkeypatch.delenv("ARIA_CODER_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ARIA_LLM_URL", "http://sovereign.invalid/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")

    before = os.environ.get("ARIA_CODER_LLM_PROVIDER")
    cfg = cli_llm.LLMConfig.from_env(provider_override="aria-llm")
    after = os.environ.get("ARIA_CODER_LLM_PROVIDER")

    assert cfg.provider == "aria-llm", "the override must still take effect"
    assert after == before, (
        f"from_env exported the override to os.environ ({after!r}); reading "
        "config must not mutate the process")


def test_a_second_call_without_the_override_is_unaffected(monkeypatch) -> None:
    """The observable consequence of the leak: the next caller silently got the
    previous caller's provider."""
    monkeypatch.delenv("ARIA_CODER_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("ARIA_LLM_URL", "http://sovereign.invalid/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")

    cli_llm.LLMConfig.from_env(provider_override="aria-llm")
    plain = cli_llm.LLMConfig.from_env()
    assert plain.provider == "deepseek", (
        f"a later call inherited the earlier override ({plain.provider})")


def test_the_served_windows_leave_real_room_for_conversation(monkeypatch) -> None:
    """R-F4321 — a coding agent needs history, not just instructions.

    Pinned at 32,768 (what the sovereign now serves, raised from 16,384) and
    65,536 (DeepSeek). A 16,384-token model is genuinely marginal for this CLI
    and that is recorded rather than asserted away.
    """
    from aria_cli import agent as ag
    for window, floor in ((32768, 12000), (65536, 50000)):
        monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", str(window))
        c = _sovereign(monkeypatch, window=str(window))
        assert ag.compact_budget_chars(c) >= floor, (
            f"only {ag.compact_budget_chars(c)} chars of history at a {window} "
            "window")
