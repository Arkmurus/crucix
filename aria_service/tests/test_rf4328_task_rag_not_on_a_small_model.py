"""R-F4328 / C-276 — the code-RAG injection undid the compact prompt.

R-F4325 gave the sovereign a 344-char system prompt because a 20,885-char one
destroyed her tool-calling. The CLI then appends the task-RAG block as a
SECOND system message (`Agent._inject_task_rag`), and Mistral's contract
hoists every system turn into one leading block — so she receives 4,542
chars and the fix is undone on the very next line.

MEASURED LIVE 2026-08-25, five representative CLI tasks scored on whether the
CORRECT tool was called, narrowed 5-tool set throughout:

    RAG budget     0 ch -> total   344 ch -> 3/5
    RAG budget   200 ch -> total   546 ch -> 2/5
    RAG budget   400 ch -> total   746 ch -> 2/5
    RAG budget   800 ch -> total 1,146 ch -> 1/5
    RAG budget 1,500 ch -> total 1,846 ch -> 0/5
    RAG budget 4,198 ch -> total 4,544 ch -> 1/5   <- what the CLI sent

THERE IS NO AFFORDABLE BUDGET. This is why the fix is a skip and not a cap:
200 chars already costs a task, so any "just trim it" answer is a slower way
to reach the same failure. The dose-response is monotonic and was measured,
not assumed.

THE OPERATOR'S SYMPTOM: asked for a deep log review, she replied with a
markdown ```python block CALLING grep(...) and read_file(...) as source code,
then "task finished" with zero tool calls. She was not refusing — she cannot
reach the tool-call channel under that much system text, which is the same
mechanism R-F4325 recorded (the literal "[TOOL_CALLS]" token arriving as
plain text).

WHAT IS ACTUALLY LOST, AND WHY IT IS ACCEPTABLE. The RAG block carries
constitutional rules. The compact prompt already states the operating floor —
R-number before code, root cause not band-aid, verify before claiming, never
delete data — so the loss is redundancy, not the constitution. A model that
receives every rule and can no longer ACT on any of them is strictly worse
than one that holds four rules and works.

SCOPED, NOT DELETED. DeepSeek and Anthropic keep the full RAG block; it helps
them and costs them nothing. The skip is tied to `compact_prompt_active`, the
same predicate R-F4325 uses, so the two halves cannot drift apart and a future
checkpoint that no longer needs the compact prompt gets its RAG back
automatically.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import agent as cli_agent    # noqa: E402
from aria_cli import prompt as cli_prompt  # noqa: E402


class _Cfg:
    def __init__(self, provider): self.provider = provider


class _LLM:
    def __init__(self, provider): self.config = _Cfg(provider)


class _Toolbox:
    """Minimal stand-in — CoderToolbox reads .root off whatever it wraps."""
    root = pathlib.Path(".")


def _agent(provider, monkeypatch, tmp_path=None):
    """A real Agent wired to `provider`, with task_rag ON (self-mode).

    The RAG lookup is patched on `aria_cli.prompt`, NOT on `aria_cli.agent`:
    `_inject_task_rag` imports it inside the function body, so patching the
    agent module has no effect. (Caught by this test failing while the fix
    worked — the harness was wrong, not the code.)
    """
    monkeypatch.setattr(cli_prompt, "_query_coding_rag",
                        lambda *_a, **_k: "RULE BLOCK " * 400, raising=False)
    return cli_agent.Agent(
        llm=_LLM(provider),
        toolbox=_Toolbox(),
        system_prompt="sys",
        ui=type("UI", (), {"info": lambda *a, **k: None,
                           "tool_output": None})(),
        task_rag=True,
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("ARIA_CLI_COMPACT_PROMPT", "ARIA_CODER_LLM_PROVIDER"):
        monkeypatch.delenv(v, raising=False)


# -- THE CAPABILITY TEST ------------------------------------------------

def test_the_sovereign_gets_no_task_rag_block(monkeypatch):
    """THE LIVE SYMPTOM. 4,198 chars of RAG on top of a 344-char prompt took
    her from 3/5 correct tool calls to 1/5."""
    a = _agent("aria-llm", monkeypatch)
    before = len(a.messages)
    a._inject_task_rag("deep live log monitoring, do a deep dd")
    injected = [m for m in a.messages[before:] if m.get("role") == "system"]
    assert not injected, (
        f"a {sum(len(m.get('content') or '') for m in injected)}-char RAG block "
        "was injected for a model measured to lose tool-calling at 200 chars"
    )


def test_a_large_model_still_gets_it(monkeypatch):
    """SCOPED. DeepSeek handles the full block and benefits from it; removing
    it there would be a real capability loss bought for nothing."""
    a = _agent("deepseek", monkeypatch)
    before = len(a.messages)
    a._inject_task_rag("some coding task")
    injected = [m for m in a.messages[before:] if m.get("role") == "system"]
    assert injected, "the RAG block was dropped for a provider that can use it"


def test_an_unknown_provider_still_gets_it(monkeypatch):
    """Fail OPEN, matching R-F4325: the concession belongs to the model whose
    ceiling was measured, not to every model added later."""
    a = _agent("some-new-vendor", monkeypatch)
    before = len(a.messages)
    a._inject_task_rag("some coding task")
    assert [m for m in a.messages[before:] if m.get("role") == "system"]


# -- the two halves must not drift --------------------------------------

def test_the_skip_uses_the_same_predicate_as_the_compact_prompt():
    """R-F4325 compacts the prompt and R-F4328 skips the RAG for the SAME
    reason. Two independent conditions would drift, and the drift would be
    invisible — a compacted prompt with a 4KB RAG block behind it looks fine
    in code and fails only against a live model."""
    src = (ROOT / "aria_cli/agent.py").read_text(encoding="utf-8", errors="replace")
    i = src.index("def _inject_task_rag")
    body = src[i:i + 2500]
    assert "compact_prompt_active" in body, (
        "_inject_task_rag does not consult compact_prompt_active — if the skip "
        "was written as its own provider list, it will drift from R-F4325"
    )


def test_the_operator_lever_still_governs_both(monkeypatch):
    """ARIA_CLI_COMPACT_PROMPT=0 says 'this model can take the full prompt'.
    It must then also take the RAG block, or the lever means two things."""
    monkeypatch.setenv("ARIA_CLI_COMPACT_PROMPT", "0")
    assert cli_prompt.compact_prompt_active("aria-llm") is False
    a = _agent("aria-llm", monkeypatch)
    before = len(a.messages)
    a._inject_task_rag("some coding task")
    assert [m for m in a.messages[before:] if m.get("role") == "system"], (
        "compact prompt was switched OFF but the RAG block stayed suppressed"
    )


def test_forcing_compact_on_suppresses_it_for_a_big_model(monkeypatch):
    """The lever in the other direction, so it is genuinely one switch."""
    monkeypatch.setenv("ARIA_CLI_COMPACT_PROMPT", "1")
    a = _agent("deepseek", monkeypatch)
    before = len(a.messages)
    a._inject_task_rag("some coding task")
    assert not [m for m in a.messages[before:] if m.get("role") == "system"]


def test_skipping_does_not_break_the_turn(monkeypatch):
    """The RAG path is best-effort; suppressing it must leave the conversation
    untouched and never raise on the hot path."""
    a = _agent("aria-llm", monkeypatch)
    snapshot = list(a.messages)
    a._inject_task_rag("anything")
    assert a.messages == snapshot
