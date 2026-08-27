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


# ── R-F4375 (C-320): bare JSON, no tags — the shape Qwen actually emits ─────

OFFERED = {"read_file", "write_file", "edit_file", "list_dir", "grep", "run"}

#: VERBATIM from the pod, 2026-08-27. Qwen2.5-Coder-32B emits a perfectly formed
#: call and simply omits the <tool_call> wrapper. Requiring the tags scored 172
#: of 172 such calls as "answered in prose" and produced a flat 0.0% for the
#: base AND its own LoRA.
#: Built with json.dumps rather than hand-written: the wire string contains a
#: regex full of backslashes, and escaping it through the test source produced a
#: literal that was NOT valid JSON — so the test failed while the parser was
#: correct. Constructing it guarantees the fixture is what the model can send.
LIVE_BARE = json.dumps({
    "name": "grep",
    "arguments": {"path": "calc.py",
                  "pattern": r"def add\(.*?\):.*?return.*?",
                  "context": "3"},
}) + "\n"
LIVE_TWO = ('{"name": "edit_file", "arguments": "{\\"new_string\\": \\"return a + b\\", '
            '\\"old_string\\": \\"return a - b\\", \\"path\\": \\"calc.py\\"}"}\n'
            '{"name": "run", "arguments": "{\\"command\\": \\"python calc.py\\"}"}\n')


def test_the_bare_call_the_model_actually_emits_is_recovered():
    """THE DEFECT, from the wire."""
    calls = parse_tool_calls(LIVE_BARE, OFFERED)

    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "grep"
    assert json.loads(calls[0]["function"]["arguments"])["path"] == "calc.py"


def test_two_newline_separated_bare_calls_are_both_recovered():
    """She plans several steps at once — measured. Taking only the first would
    silently drop half the plan."""
    calls = parse_tool_calls(LIVE_TWO, OFFERED)

    assert [c["function"]["name"] for c in calls] == ["edit_file", "run"]
    assert json.loads(calls[1]["function"]["arguments"]) == {"command": "python calc.py"}


def test_arguments_as_a_nested_json_string_survive():
    """`arguments` arrives BOTH as an object and as a JSON string, in the same
    run. A client that assumes one shape drops the other."""
    calls = parse_tool_calls(LIVE_TWO, OFFERED)
    assert isinstance(calls[0]["function"]["arguments"], str)
    assert json.loads(calls[0]["function"]["arguments"])["old_string"] == "return a - b"


# ── and it must still refuse everything R-F4329 forbids ────────────────────

def test_prose_around_a_quoted_call_is_refused():
    """THE SAFETY PROPERTY. One character of prose outside the JSON and the
    whole content is refused — text that merely QUOTES a call must never be
    executed, because a fabricated `run` runs."""
    text = ('I would call {"name": "run", "arguments": {"command": "rm -rf /"}} '
            'but I need your approval first.')
    assert parse_tool_calls(text, OFFERED) == []


def test_a_bare_call_naming_an_unoffered_tool_is_refused():
    text = '{"name": "delete_everything", "arguments": {"path": "/"}}'
    assert parse_tool_calls(text, OFFERED) == []


def test_one_bad_element_rejects_the_whole_set():
    """Partial recovery would run half a plan the model never intended as a
    half — R-F4329's all-or-nothing rule."""
    text = ('{"name": "read_file", "arguments": {"path": "a.py"}}\n'
            '{"name": "not_a_tool", "arguments": {}}\n')
    assert parse_tool_calls(text, OFFERED) == []


def test_bare_json_is_refused_when_no_tools_were_offered():
    """With no offer list any match is invention. The DD path passes none."""
    assert parse_tool_calls(LIVE_BARE, None) == []


def test_plain_prose_is_still_prose():
    assert parse_tool_calls(
        "I cannot execute or modify files.", OFFERED) == []
    assert parse_tool_calls("The file has 42 lines.", OFFERED) == []


def test_a_bare_json_object_that_is_not_a_call_is_refused():
    """Valid JSON is not a tool call. A config blob has no `name`."""
    assert parse_tool_calls('{"path": "a.py", "lines": 3}', OFFERED) == []


def test_tagged_forms_still_win_and_are_unaffected():
    """The tagged paths return before the bare-JSON path is reached."""
    calls = parse_tool_calls(
        '<tool_call>{"name": "run", "arguments": {"command": "ls"}}</tool_call>',
        OFFERED)
    assert [c["function"]["name"] for c in calls] == ["run"]


# ── the strongest guard: the REAL wire output, not a hand-written fixture ───

def _reference_is_wellformed(text, offered):
    """An INDEPENDENT reading of "is this content entirely tool calls?".

    Deliberately not the parser under test: comparing the parser to itself
    proves nothing. This is the plain-English rule written a second way — decode
    every JSON value in the content, and require that they are all objects
    naming an offered tool, with nothing else present.
    """
    dec = json.JSONDecoder()
    body = (text or "").strip()
    idx, found = 0, []
    while idx < len(body):
        while idx < len(body) and body[idx].isspace():
            idx += 1
        if idx >= len(body):
            break
        try:
            obj, idx = dec.raw_decode(body, idx)
        except Exception:
            return False
        found.append(obj)
    return bool(found) and all(
        isinstance(o, dict) and o.get("name") in offered for o in found)


def test_the_parser_agrees_exactly_with_the_rule_on_live_output():
    """R-F4375 (C-320) — 40 responses captured from Qwen2.5-Coder-32B on the
    pod, every one of which the tag-only parser scored as "answered in prose".

    NO THRESHOLD. An earlier version of this test asserted "at least 90%
    recovered", which is a number I made up; the measured rate was 62.5%, and
    the 15 misses were genuinely malformed — truncated mid-plan by the eval's
    own 320-token budget, which R-F4375 raised. A rate would have to be
    re-tuned every time that budget changes, and would hide a real regression
    behind a slack threshold.

    So this asserts AGREEMENT with the rule instead: the parser recovers a
    response exactly when the content really is nothing but tool calls naming
    offered tools. That is exact, and it stays true whatever the truncation
    rate happens to be.

    The fixture is real bytes because a hand-escaped copy of one of these
    strings was not valid JSON, and the resulting test failed while the parser
    was already correct.
    """
    fixture = json.loads(
        (ROOT / "aria_service" / "tests" / "fixtures" /
         "qwen_bare_tool_calls.json").read_text(encoding="utf-8"))
    samples = fixture["samples"]
    assert len(samples) >= 20, "fixture too small to be evidence"

    disagreements = []
    recovered = 0
    for s in samples:
        got = bool(parse_tool_calls(s, OFFERED))
        want = _reference_is_wellformed(s, OFFERED)
        recovered += got
        if got != want:
            disagreements.append((want, got, s[:120]))
    assert not disagreements, f"parser disagrees with the rule: {disagreements[:3]}"

    # And the fixture must actually EXERCISE recovery, or agreement is vacuous:
    # a parser that returned [] for everything would "agree" on an all-malformed
    # set.
    assert recovered >= 10, (
        f"only {recovered}/{len(samples)} recovered — the fixture no longer "
        f"demonstrates that recovery works")

    for s in samples:
        for c in parse_tool_calls(s, OFFERED):
            assert c["function"]["name"] in OFFERED
            json.loads(c["function"]["arguments"])
