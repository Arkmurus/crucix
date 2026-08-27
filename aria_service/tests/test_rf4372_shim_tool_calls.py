"""R-F4372 (C-317) — the eval shim must return tool_calls, or the coder eval
measures nothing.

`serve_eval_shim` returned only `content` and ignored the `tools` block. That is
harmless for `eval_tooluse.py`, which scores the model's final PROSE answer, and
FATAL for any eval that asks whether she CALLS a tool: every response scores
"answered in prose" for the base AND the trained adapter alike. The result is
flat, and a flat result reads as "training changed nothing" rather than as a
broken instrument — which is how GPU hours get spent on a number that means
nothing.

Caught before the paid run, not after.

These tests import ONLY the parser, never the module: importing
`serve_eval_shim` loads torch and a 32B model. The parser is module-level and
pure precisely so it can be tested on a laptop with no GPU.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIM = ROOT / "scripts" / "train" / "serve_eval_shim.py"


def _load_parser():
    """Extract `parse_tool_calls` without executing the module's model load.

    The shim loads weights at import time, so it cannot be imported here. Read
    the source, compile ONLY that function, and bind the one name it needs.
    """
    import ast

    src = SHIM.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "parse_tool_calls")
    mod = types.ModuleType("shim_parser")
    mod.__dict__["_json"] = json
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SHIM), "exec"),
         mod.__dict__)
    return mod.parse_tool_calls


parse_tool_calls = _load_parser()


# ── the two wire formats the shim must serve ────────────────────────────────

def test_chatml_tool_call_is_recovered():
    """Qwen / ChatML — the format the code-specialised base emits."""
    text = ('<tool_call>\n{"name": "edit_file", "arguments": '
            '{"path": "calc.py", "old_string": "a - b", "new_string": "a + b"}}\n'
            '</tool_call>')
    calls = parse_tool_calls(text)

    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "edit_file"
    assert json.loads(calls[0]["function"]["arguments"])["path"] == "calc.py"


def test_two_chatml_calls_are_both_recovered():
    text = ('<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>'
            '<tool_call>{"name": "run", "arguments": {"command": "python a.py"}}</tool_call>')
    calls = parse_tool_calls(text)

    assert [c["function"]["name"] for c in calls] == ["read_file", "run"]


def test_mistral_tool_call_is_recovered():
    """The incumbent base's format, so a base-vs-base comparison is possible."""
    text = ('[TOOL_CALLS] [{"name": "list_dir", "arguments": {"path": "."}}]')
    calls = parse_tool_calls(text)

    assert len(calls) == 1 and calls[0]["function"]["name"] == "list_dir"


def test_mistral_call_followed_by_prose_is_recovered():
    """Measured on the live sovereign: she appends commentary after the array.
    A whole-string json.loads would throw and score it as prose."""
    text = ('[TOOL_CALLS] [{"name": "run", "arguments": {"command": "git status"}}]'
            '\nI will run this and report back.')
    calls = parse_tool_calls(text)

    assert len(calls) == 1 and calls[0]["function"]["name"] == "run"


# ── the arguments must arrive in the shape an OpenAI client expects ─────────

def test_arguments_are_serialised_to_a_json_string():
    """OpenAI-shaped `arguments` is a STRING. Returning a dict would make every
    downstream `json.loads` raise, and the eval would score a correct call as a
    parse failure."""
    calls = parse_tool_calls(
        '<tool_call>{"name": "run", "arguments": {"command": "ls"}}</tool_call>')

    raw = calls[0]["function"]["arguments"]
    assert isinstance(raw, str)
    assert json.loads(raw) == {"command": "ls"}


def test_arguments_already_a_string_are_passed_through():
    calls = parse_tool_calls(
        '<tool_call>{"name": "run", "arguments": "{\\"command\\": \\"ls\\"}"}</tool_call>')

    assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls"}


def test_every_call_gets_a_distinct_id():
    """Duplicate ids break tool-call/result pairing downstream."""
    calls = parse_tool_calls(
        '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>'
        '<tool_call>{"name": "read_file", "arguments": {"path": "b"}}</tool_call>')

    ids = [c["id"] for c in calls]
    assert len(set(ids)) == len(ids) == 2


# ── prose must stay prose: [] is a measurement, not a failure ───────────────

def test_plain_prose_yields_no_calls():
    """THE MEASUREMENT THAT MATTERS. "I cannot execute or modify files" must
    come back as zero calls so the eval can count it as a refusal."""
    assert parse_tool_calls(
        "I cannot execute or modify files. You must manually edit calc.py.") == []


def test_malformed_tool_call_is_not_invented_into_one():
    """Truncated JSON inside the tags is not a call. Guessing one would credit
    the model for something it did not produce."""
    assert parse_tool_calls('<tool_call>{"name": "run", "argum') == []


def test_a_call_without_a_name_is_refused():
    """`arguments` alone names no tool. Emitting it would force a downstream
    guess about WHICH tool to run — and a fabricated `run` executes."""
    assert parse_tool_calls(
        '<tool_call>{"arguments": {"command": "rm -rf /"}}</tool_call>') == []


def test_empty_and_none_are_safe():
    assert parse_tool_calls("") == []
    assert parse_tool_calls(None) == []


# ── the shim's default path must be untouched (the peer's DD cycles) ───────

def test_the_shim_only_parses_when_tools_were_offered():
    """ADDITIVE BY CONSTRUCTION. The DD eval sends no `tools` and must receive
    the byte-identical payload it always has; parsing unconditionally would also
    let prose that merely quotes a call become an executed one."""
    src = SHIM.read_text(encoding="utf-8")
    assert 'tools = body.get("tools") or None' in src
    assert "if tools:" in src, "tool parsing is not gated on tools being offered"
    # And the template kwarg must be conditional too, for tokenizers whose
    # template does not accept it.
    assert '**({"tools": tools} if tools else {})' in src


# ── R-F4374 (C-319): a 0% score must be able to explain itself ──────────────

def test_the_eval_carries_the_prose_back():
    """THE DEFECT. The R-F4372 cycle returned `acted 0.0%` for the base AND the
    trained adapter, byte-identically, over 172 steps. The report recorded only
    the VERDICT — "answered in prose" — so it could not say whether the model
    refused, wrote the call inside a ```json fence, or emitted a shape the
    parser does not know.

    A measurement that cannot explain its own extreme is not finished, and an
    unexplained extreme is exactly where a broken instrument hides: the obvious
    reading of a flat A/B is "training did nothing", which sends you to change
    the corpus rather than the thermometer.
    """
    import inspect

    from scripts.train import eval_coder_tooluse as E

    src = inspect.getsource(E.ask)
    assert src.count("return") >= 4
    # Every path returns THREE values — a mixed arity would be worse than the
    # gap it fixes, because the caller unpacks blindly.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("return "):
            assert stripped.count(",") >= 2, f"arity mismatch: {stripped}"

    ev = inspect.getsource(E.evaluate)
    assert '"said": content[:320]' in ev, "the prose is not recorded"
    assert "call, err, content = ask(" in ev, "caller does not take the prose"


def test_a_refusal_is_counted_separately_from_prose():
    """"I cannot modify files" and "the answer is 42" are both `prose`, and they
    mean opposite things about whether she CAN act."""
    import inspect

    from scripts.train import eval_coder_tooluse as E

    assert E._looks_like_refusal("I cannot modify files.")
    assert not E._looks_like_refusal("The file has 42 lines.")
    ev = inspect.getsource(E.evaluate)
    assert 'total["refused"]' in ev, "refusals are not counted"
