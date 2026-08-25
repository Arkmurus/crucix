"""R-F4321 / C-269 - there were TWO context budgets and they disagreed.

Found by a peer review of R-F4318/4319/4320, and it matters because the
disagreement could re-create the exact HTTP 400 that whole line of work exists
to stop.

`prompt.guidance_budget_chars` reserved 4,000 tokens for the tool schemas.
`agent.compact_budget_chars` reserved NOTHING for them, and multiplied by a
hardcoded 4 chars/token after prompt.py had been MEASURED at 3. Same quantity,
two derivations. Measured on the tree that shipped, sovereign at 16,384:

    history budget allowed   14,745 tok
    completion reserve        4,096
    tool schemas              2,382   (sent on EVERY call; reserved by neither)
    ------------------------------------
    total                    21,223   vs a 16,384 window  ->  overflow 4,839

Worse, R-F4318's own guard encoded the same omission —
`budget <= (max_model_len - max_tokens) * 4` — so it could never have caught
it. A guard that shares the defect's assumption is not a guard; it is the
defect with a green tick next to it.

THE FIX IS ONE ALLOCATION, not a third reservation bolted onto the second.
`context_budget()` divides the window exhaustively:

    window = completion + tool schemas + prompt overhead + guidance + history

and every consumer reads it. This is the §1/R-F2639 rule ("there is ONE measure
now; do not fork it again") applied inside a single feature - the same shape
that produced two Phase A gate aggregators disagreeing per-gate.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import agent as ag  # noqa: E402
from aria_cli import llm as cli_llm  # noqa: E402
from aria_cli import prompt as P  # noqa: E402

_ENV = ("ARIA_LLM_MAX_MODEL_LEN", "ARIA_CODER_GUIDANCE_MAX_CHARS",
        "ARIA_CODER_COMPACT_CHARS", "ARIA_CODER_LLM_MAX_TOKENS",
        "ARIA_CODER_LLM_PROVIDER", "ARIA_LLM_URL", "DEEPSEEK_API_KEY")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)


def _alloc(b) -> int:
    return (b["guidance_tokens"] + b["history_tokens"]
            + b["overhead"] + b["tools"] + b["completion"])


# -- the whole allocation must fit ----------------------------------------

@pytest.mark.parametrize("window,completion", [
    (16384, 4096), (32768, 8192), (65536, 8192), (8192, 2048), (131072, 8192),
])
def test_the_allocation_never_exceeds_the_window(window, completion) -> None:
    """THE CAPABILITY TEST. Every part the model must hold, added up, against
    the window. This is the sum nothing computed before."""
    b = P.context_budget(window_tokens=window, completion_tokens=completion)
    if not b["fits"]:
        # The fixed costs alone exceed the window — reported, not hidden.
        pytest.skip(f"window {window} cannot host the CLI: {b['fixed_tokens']} "
                    "tokens of fixed cost")
    assert _alloc(b) <= window, f"allocation {_alloc(b)} > window {window}: {b}"


def _wire_schemas():
    """EXACTLY what agent.py:255 puts on the wire — not TOOL_SCHEMAS alone."""
    from aria_cli.coder_tools import CODER_TOOL_SCHEMAS
    return ag._dedup_tool_schemas(list(ag.TOOL_SCHEMAS) + list(CODER_TOOL_SCHEMAS))


def test_the_tool_schemas_are_reserved() -> None:
    """FINDING 6 — the reserve was unpinned: zeroing it left every test green.

    R-F4321, SECOND PASS — and the first version of this test was itself the
    defect it was written to catch. It measured `TOOL_SCHEMAS`: 14 tools, 7,147
    chars, 1,936 tokens. What `agent.py:255` actually sends is
    `_dedup_tool_schemas(TOOL_SCHEMAS + CODER_TOOL_SCHEMAS)` — 31 tools, 15,486
    chars, 4,230 tokens against the served tokenizer. So the test went GREEN
    while the reserve was short by ~1,230 tokens on every single call.

    A guard aimed at the wrong object certifies nothing, however carefully it is
    written. Caught by a peer review, not by me. It now measures the wire set,
    and the reserve is DERIVED from that set rather than hardcoded — adding a
    32nd tool moves the budget by itself instead of rotting it.
    """
    import json
    b = P.context_budget(window_tokens=32768, completion_tokens=8192)
    wire_chars = len(json.dumps(_wire_schemas()))
    # the tokenizer's real answer for this set, from scripts/admin probing
    assert wire_chars > 12000, (
        f"the wire schema set is only {wire_chars} chars — is this TOOL_SCHEMAS "
        "alone again?")
    assert b["tools"] >= wire_chars // 4, (
        f"reserve {b['tools']} tok cannot cover {wire_chars} chars of schemas")


def test_the_reserve_tracks_the_real_tool_set() -> None:
    """The anti-rot property: the reserve is measured, not maintained."""
    import json
    assert P._tool_schema_tokens() >= len(json.dumps(_wire_schemas())) // 4
    assert P._tool_schema_tokens() >= 4230, (
        "below the tokenizer's measured cost for the live tool set")


def test_both_consumers_derive_from_the_same_budget() -> None:
    """The two budgets must not be able to disagree again."""
    b = P.context_budget(window_tokens=32768, completion_tokens=8192)
    assert P.guidance_budget_chars(window_tokens=32768,
                                   completion_tokens=8192) == b["guidance_chars"]

    class _Cfg:
        max_model_len = 32768
        max_tokens = 8192
    assert ag.compact_budget_chars(_Cfg()) == max(2000, b["history_chars"])


def test_the_chars_per_token_constant_is_not_forked() -> None:
    """FINDING 3 — agent.py hardcoded 4 while prompt.py was measured at 3.

    Asserted structurally: the history budget must come from the shared
    computation, so agent.py must not carry its own chars/token arithmetic.
    """
    src = (ROOT / "aria_cli/agent.py").read_text(encoding="utf-8")
    i = src.index("def compact_budget_chars")
    body = src[i:i + 1600]
    # Strip comments and docstring prose before checking. An earlier version
    # banned the substring "* 4 * 0.9" and went red on the COMMENT that
    # documents the removed code — the same prose-versus-code confusion that
    # tripped R-F4297, R-F4305 and R-F4317's first draft. Check what executes.
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.strip().startswith("#"))
    assert "context_budget" in code, "compact_budget_chars forked the arithmetic"
    assert "* 4 * 0.9" not in code, "the old hardcoded 4 chars/token survives"


# -- both shares grow with the model --------------------------------------

def test_history_grows_with_the_window() -> None:
    """Guidance must not be greedy: an agent that has read the constitution and
    cannot hold a conversation about it has not been helped."""
    small = P.context_budget(window_tokens=16384, completion_tokens=4096)
    big = P.context_budget(window_tokens=65536, completion_tokens=8192)
    assert big["history_tokens"] > small["history_tokens"] * 4
    assert big["guidance_tokens"] > small["guidance_tokens"] * 4


def test_guidance_takes_the_larger_share() -> None:
    b = P.context_budget(window_tokens=65536, completion_tokens=8192)
    assert b["guidance_tokens"] > b["history_tokens"]


def test_a_tiny_window_does_not_go_negative() -> None:
    b = P.context_budget(window_tokens=2048, completion_tokens=512)
    assert b["guidance_tokens"] >= 0 and b["history_tokens"] >= 0


# -- guidance emission respects its own budget ----------------------------

def test_the_emitted_guidance_stays_within_budget(monkeypatch) -> None:
    """FINDING 4 — the per-file floor made the shares sum ABOVE the total
    (budget 3,864 -> 5,061 emitted, 31% over). A non-binding budget inside the
    one function whose job is making the prompt fit is not a budget."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    guidance = P.load_repo_guidance(ROOT)
    budget = P.guidance_budget_chars()
    assert len(guidance) <= budget + 200, (
        f"emitted {len(guidance)} chars against a {budget}-char budget")


def test_every_file_still_appears(monkeypatch) -> None:
    """Dropping the floor must not drop a FILE. AGENTS.md carries laws 11-20;
    losing it entirely would be worse than clipping it."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")
    guidance = P.load_repo_guidance(ROOT)
    assert "CLAUDE.md" in guidance and "AGENTS.md" in guidance


def test_a_window_too_small_to_host_the_cli_says_so() -> None:
    """An 8,192-token model cannot hold a 2,048 answer + 3,000 of tool schemas
    + a 5,000-token base prompt. No split repairs that, and a budget that
    quietly returned zeros would hand the caller a prompt that 400s anyway."""
    b = P.context_budget(window_tokens=8192, completion_tokens=2048)
    assert b["fits"] is False
    assert b["fixed_tokens"] > 8192


def test_the_windows_we_actually_serve_do_fit() -> None:
    """32,768 is what the sovereign serves; 65,536 is DeepSeek. 16,384 is kept
    in the list because it is what the pod served until 2026-08-25 and a
    regression that made it unhostable would be worth knowing about."""
    for window, completion in ((16384, 4096), (32768, 8192), (65536, 8192)):
        b = P.context_budget(window_tokens=window, completion_tokens=completion)
        assert b["fits"] is True, (window, b)


def test_a_16k_window_leaves_almost_no_room_for_guidance() -> None:
    """Recorded as a FACT, not asserted away: with 31 tools (15,486 chars), a
    7,000-token base prompt and a 4,096 completion, a 16,384 window has ~126
    tokens of slack. It technically hosts the CLI and cannot carry the
    constitution. This is why the pod was raised to 32,768."""
    b = P.context_budget(window_tokens=16384, completion_tokens=4096)
    assert b["fits"] is True
    assert b["guidance_tokens"] < 1000


# -- the operating floor survives ANY window ------------------------------

_FLOOR_FACTS = [
    "git push origin main",      # the shipping sequence — appears ONCE in the repo
    "deploy-fly.yml",            # and it is aria-intel ONLY
    "fly.wa.toml",               # the WA tier deploys separately
    "reserve_r_number.py",       # R-number discipline
    "root cause",                # never bump a timeout to hide a failure
]


@pytest.mark.parametrize("window", [16384, 32768, 65536])
@pytest.mark.parametrize("fact", _FLOOR_FACTS)
def test_the_operating_floor_survives_every_window(fact, window, monkeypatch):
    """R-F4321 — these rules must reach the agent at ANY window size.

    They live in CLAUDE.md, and the CLI used to rely on the whole file being
    injected. The moment the guidance budget started tracking the window that
    stopped being true, and `test_self_mode_prompt_covers_shipping_and_excellence`
    went red: "git push origin main" appears EXACTLY ONCE in the repo — CLAUDE.md
    §11 — and landed in the elided middle. An agent that cannot see the shipping
    sequence cannot ship; it would invent one, and `[deploy]` in a commit message
    (the plausible invention) does nothing at all.

    Pinned across three windows because the failure is window-dependent: it
    passed at DeepSeek's size and failed at the sovereign's, which is precisely
    the kind of regression a single-size test cannot see.
    """
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", str(window))
    sp = P.build_system_prompt(root=ROOT, self_mode=True, repo_root=ROOT)
    assert fact.lower() in sp.lower(), (
        f"{fact!r} is absent from the system prompt at a {window}-token window")


@pytest.mark.parametrize("window", [16384, 32768])
def test_the_prompt_still_fits_after_the_floor_was_added(window, monkeypatch):
    """The floor is paid for on EVERY call. It must not reintroduce the overflow
    it was added alongside — a fix that breaks the fix is not a fix."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", str(window))
    sp = P.build_system_prompt(root=ROOT, self_mode=True, repo_root=ROOT)
    b = P.context_budget()
    # measured 3.10-3.69 chars/token; 3 is the conservative divisor used here.
    est = len(sp) // 3
    # Compare against guidance_CHARS, not guidance_TOKENS: `_GUIDANCE_FLOOR_CHARS`
    # deliberately overrides the budget so a tiny window never ships an EMPTY
    # constitution, and at 16,384 that floor is the binding term (75 tokens of
    # budget, 667 tokens of floor). Asserting against the pre-floor number would
    # demand the code break its own documented guarantee.
    allowed = b["guidance_chars"] // 3 + b["overhead"]
    assert est <= allowed, (
        f"system prompt ~{est} tokens exceeds its allocation ({allowed})")
