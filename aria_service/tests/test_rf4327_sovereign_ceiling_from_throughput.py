"""R-F4327 / C-275 — the sovereign's chat ceiling was calibrated for hardware
we no longer run.

`SOVEREIGN_COMPLETION_CEILING = 800` is justified in its own comment by a
throughput figure:

    "The 7B/14B sovereign serves at ~10 tok/s on the bf16 shim, so a
     4000-token completion takes 150-250s and blows the chat timeout."

That reasoning is correct and the constant follows from it: 800 tokens at
10 tok/s is 80s, comfortably inside the 120s chat timeout.

MEASURED LIVE 2026-08-25 against the served endpoint (A40 46GB, vLLM,
Mistral-7B-Instruct-v0.3 as aria-llm-v0.4-dpo):

    max_tokens=200  ->  200 tok in  7.5s = 26.6 tok/s
    max_tokens=800  ->  800 tok in 24.8s = 32.2 tok/s
    max_tokens=2000 ->  591 tok in 17.9s = 33.0 tok/s

~3x the assumed rate. The premise moved when the hardware moved, and the
constant did not — so a ceiling meant to spend 80% of the timeout now spends
about 20% of it, and the operator sees answers cut off at 800 tokens on a
model that could comfortably produce three times that.

This is the R-F4028 shape, not a tuning miss: the number was RIGHT when
written, something else changed underneath it, and nothing re-derived it.
CLAUDE.md §1 forbids raising a limit as a band-aid — so this does not raise
the constant, it DERIVES the ceiling from the two facts that actually
determine it (throughput and the timeout we must fit inside), which is the
same move R-F4318/R-F4321/R-F4326 made for context windows.

WHAT MUST NOT HAPPEN — and this is the whole reason the ceiling exists:
R-F1360 tried to enforce this in the CALLER and capped DeepSeek instead,
causing a total chat outage on 2026-08-01 (R-F3606). The scope stays exactly
where R-F3606 put it: chat call sites in model_router only. Batch callers —
the self-coder generating a whole file at max_tokens=8192 — must NEVER be
clamped, because a truncated generated file is the R-F904 failure class.

NOT "UNLIMITED". A completion is bounded by the chat timeout and by the
32,768-token window; those are physics, not policy. What is removed is the
ARTIFICIAL part — a ceiling frozen against a machine that no longer exists.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.llm import aria_llm_provider as P  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("ARIA_SOVEREIGN_COMPLETION_CEILING", "ARIA_SOVEREIGN_TOKENS_PER_SEC",
              "ARIA_LLM_CHAT_TIMEOUT_S", "ARIA_LLM_MAX_MODEL_LEN"):
        monkeypatch.delenv(v, raising=False)


# -- THE CAPABILITY TEST ------------------------------------------------

def test_the_ceiling_reflects_measured_throughput_not_the_old_shim():
    """THE DEFECT. 800 tokens assumes ~10 tok/s; the box measures 26-33."""
    ceiling = P.sovereign_completion_ceiling()
    assert ceiling > 1500, (
        f"ceiling {ceiling} is still calibrated for ~10 tok/s; measured "
        f"throughput is 26-33 tok/s, so an answer is being cut to roughly a "
        f"fifth of what fits the chat timeout"
    )


def test_the_ceiling_still_fits_inside_the_chat_timeout():
    """The constraint is real and must keep binding — an answer that arrives
    after the timeout is worse than a shorter one that arrives."""
    ceiling = P.sovereign_completion_ceiling()
    seconds = ceiling / P.sovereign_tokens_per_sec()
    assert seconds <= P.sovereign_chat_timeout_s(), (
        f"{ceiling} tokens at {P.sovereign_tokens_per_sec()} tok/s needs "
        f"{seconds:.0f}s, past the {P.sovereign_chat_timeout_s():.0f}s chat timeout"
    )


def test_it_tracks_throughput_rather_than_being_a_new_constant(monkeypatch):
    """A hardcoded 3000 would pass both tests above. Halve the throughput and
    the ceiling must halve — otherwise the next hardware change rots it again,
    which is the entire defect."""
    monkeypatch.setenv("ARIA_SOVEREIGN_TOKENS_PER_SEC", "30")
    fast = P.sovereign_completion_ceiling()
    monkeypatch.setenv("ARIA_SOVEREIGN_TOKENS_PER_SEC", "15")
    slow = P.sovereign_completion_ceiling()
    assert slow < fast, (
        f"ceiling did not move with throughput ({fast} vs {slow}) — it is a "
        f"constant wearing a function's clothes"
    )


def test_it_tracks_the_timeout_too(monkeypatch):
    """The other half of the derivation."""
    monkeypatch.setenv("ARIA_LLM_CHAT_TIMEOUT_S", "120")
    long_ = P.sovereign_completion_ceiling()
    monkeypatch.setenv("ARIA_LLM_CHAT_TIMEOUT_S", "30")
    short = P.sovereign_completion_ceiling()
    assert short < long_, f"ceiling ignored the timeout ({long_} vs {short})"


def test_the_ceiling_never_exceeds_the_served_window(monkeypatch):
    """Physics beats policy. A completion larger than the window is an HTTP
    400, which is the failure R-F4317 exists to prevent."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "4096")
    monkeypatch.setenv("ARIA_SOVEREIGN_TOKENS_PER_SEC", "500")
    monkeypatch.setenv("ARIA_LLM_CHAT_TIMEOUT_S", "600")
    assert P.sovereign_completion_ceiling() < 4096


# -- clamp_for_sovereign keeps its contract -----------------------------

def test_clamp_only_ever_lowers():
    """A caller asking for less than the ceiling is honoured as-is."""
    assert P.clamp_for_sovereign(50) == 50


def test_clamp_lowers_an_over_large_request():
    ceiling = P.sovereign_completion_ceiling()
    assert P.clamp_for_sovereign(ceiling * 10) == ceiling


def test_clamp_survives_junk():
    """Never raise on the chat path."""
    for junk in (None, "", "abc", object()):
        assert P.clamp_for_sovereign(junk) > 0


def test_an_operator_override_still_wins(monkeypatch):
    """Deriving a default is not removing the lever."""
    monkeypatch.setenv("ARIA_SOVEREIGN_COMPLETION_CEILING", "1234")
    assert P.sovereign_completion_ceiling() == 1234


# -- the R-F3606 scope must not move ------------------------------------

def test_the_clamp_is_not_applied_inside_the_provider():
    """R-F1360 enforced this in the caller and capped DEEPSEEK instead — a
    total chat outage on 2026-08-01 (R-F3606). The clamp belongs at the chat
    call sites in model_router, and batch callers (the self-coder asking for
    max_tokens=8192 to write a whole file) must reach the provider unclamped,
    or generated files are truncated (the R-F904 class)."""
    src = (ROOT / "aria_service/llm/aria_llm_provider.py").read_text(
        encoding="utf-8", errors="replace")
    i = src.index("async def complete")
    body = src[i:i + 4000]
    assert "clamp_for_sovereign(" not in body, (
        "clamp_for_sovereign is being called inside complete() — that is the "
        "'tidy' the module comment explicitly says was tried and is wrong; it "
        "truncates self-coder output"
    )
