"""R-F4377 (C-322) — a tool-use corpus must be TRAINED in the prompt it is SERVED in.

`sft_train._render_text` called `apply_chat_template(msgs)` with no `tools=`,
while the evaluator and the CLI always send the tool schemas. Measured on
Qwen2.5-Coder-32B with a real corpus row:

    training render    1,397 chars   <tools> block ABSENT
    inference render   2,659 chars   <tools> block present

So the model was trained to emit tool calls in a context that never showed it a
tool, and served in one that always does — a ~1,260-char systematic difference
at the head of every prompt.

THE COST WAS NOT SUBTLE. On the identical 172-step eval:

    untrained base   acted 77.9%   right_tool 50.0%
    after SFT        acted  2.3%   right_tool  1.7%

The LoRA emitted degenerate shapes (`grep\\n{...}`, `inspect>\\n{...}`) and
recited the system prompt's own rules ("First, I need an R-number") instead of
acting. **Training made the model markedly worse, and the only difference was
the render.** Same class as R-F4325 (train/serve prompt mismatch) and R-F4338
(the Mistral template silently dropping the system turn): a rendering
difference nobody compared.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import sft_train  # noqa: E402
from scripts.train.coder_tool_contract import tool_schemas  # noqa: E402

ROW = {"messages": [
    {"role": "system", "content": "You are ARIA, an autonomous coding agent."},
    {"role": "user", "content": "Fix the subtraction bug in calc.py."},
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "abcdefghi", "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "calc.py"}'}}]},
    {"role": "tool", "tool_call_id": "abcdefghi", "content": "return a - b"},
    {"role": "assistant", "content": "Fixed."},
]}


class _FakeTokenizer:
    """Records whether `tools` reached the template, and renders them if so.

    A stand-in rather than the real 32B tokenizer: this must run on a laptop
    with no network and no torch, and the property under test is whether the
    kwarg is PASSED — not how Qwen formats it.
    """

    def __init__(self):
        self.saw_tools = None

    def apply_chat_template(self, msgs, tokenize=False, tools=None, **kw):
        self.saw_tools = tools
        head = ""
        if tools:
            head = "<tools>" + json.dumps(
                [t["function"]["name"] for t in tools]) + "</tools>\n"
        body = "\n".join(f"{m.get('role')}: {m.get('content') or ''}" for m in msgs)
        return head + body


def test_tools_reach_the_template_when_supplied():
    """THE DEFECT."""
    tok = _FakeTokenizer()
    out = sft_train._render_text(tok, ROW, tool_schemas())

    assert tok.saw_tools, "tools never reached apply_chat_template"
    assert "<tools>" in out
    assert "read_file" in out


def test_without_tools_the_render_is_unchanged():
    """Every non-tool corpus must render exactly as it always has — this is
    additive, not a rewrite of how 45 existing corpora are trained."""
    tok = _FakeTokenizer()
    out = sft_train._render_text(tok, ROW)

    assert tok.saw_tools in (None, [], {}), "tools were injected uninvited"
    assert "<tools>" not in out


def test_empty_tools_is_treated_as_no_tools():
    """`[]` must not be passed through: some templates render an empty tool
    block, which is a third prompt shape that matches neither side."""
    assert sft_train._tool_kwargs([]) == {}
    assert sft_train._tool_kwargs(None) == {}
    assert sft_train._tool_kwargs(tool_schemas())["tools"]


def test_the_system_turn_fold_still_renders_with_tools():
    """The R-F4338 fold re-renders the row after moving the system turn. If
    that second render dropped the tools, the fix would apply to some rows and
    not others — the worst outcome, because the corpus would look correct."""
    import inspect

    src = inspect.getsource(sft_train._render_text)
    calls = src.count("apply_chat_template(")
    with_tools = src.count("_tool_kwargs(tools)")
    assert calls == with_tools, (
        f"{calls} template renders but only {with_tools} pass tools — a row "
        f"taking the other path would train the mismatch this fixes")


# ── the guard: a mismatch must be refused, not trained ──────────────────────

def test_a_tool_corpus_without_schemas_is_refused(tmp_path, monkeypatch):
    """FAIL CLOSED. Rendering tool_calls with no tools block is the exact run
    that scored 2.3% against an untrained 77.9%. It cost an H100 cycle, and it
    is invisible in the data — only the render differs — so the trainer must
    refuse rather than produce a plausible-looking adapter."""
    import inspect

    src = inspect.getsource(sft_train)
    assert "--tool-schemas" in src
    # The refusal must name the number, so the next reader knows it is not
    # pedantry.
    assert "R-F4377" in src and "tool_calls but --tool-schemas" in src
    assert "raise SystemExit(" in src
