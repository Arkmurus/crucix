"""R-F4329 / C-277 — the sovereign emits MULTI-step tool calls as content text.

MEASURED LIVE 2026-08-25, and the pattern is sharp rather than random:

    "Run the tests in test_calc.py."                  -> tool_calls: run
    "Read calc.py and then run the tests."            -> tool_calls: read_file
    "List the files, then read calc.py."              -> tool_calls: list_dir
    "Fix calc.py so divide returns None when b is
     zero. Then run the tests to confirm."            -> CONTENT, no tool_calls

The last one is deterministic — reproduced at max_tokens 150, 800, 2400 and
8192, byte-identical content each time, so it is the TASK that decides, not
the length budget. What comes back is:

    [{"name": "run", "arguments": {"command": "..."}},
     {"name": "edit_file", "arguments": {"path": "...", "old_string": "..."}}]

A well-formed Mistral tool-call ARRAY, in the `content` field. When she wants
ONE call she uses the tool_calls channel; when she plans SEVERAL she writes
them as JSON prose. Multi-step plans are exactly what a coding task produces,
which is why the operator saw "files: 0 changed, tools: 0 calls" on every
real coding request while single-step questions worked.

THE INTENT IS CORRECT AND MACHINE-READABLE. This is a CHANNEL failure, not a
comprehension failure — she named real tools with real arguments. Recovering
it is a transport-layer repair of a known provider quirk, the same class as
`_mistral_contract` (R-F4320) and `_sanitize_messages` (R-F1290), and it is
the difference between an agent that codes and one that narrates.

IT MUST NOT BECOME A PROSE PARSER. The danger is inventing a call the model
did not make — worse than dropping one, because a fabricated `run` executes.
So recovery is deliberately narrow and every condition below is load-bearing:
  * tools must have been OFFERED on the request,
  * the content must PARSE as JSON (optionally behind Mistral's [TOOL_CALLS]
    token or a ```json fence), not merely contain JSON,
  * EVERY element must be an object with a `name` that is an OFFERED tool and
    an `arguments` object — one bad element rejects the whole array,
  * anything else is left exactly as it was.
Scoped to the sovereign; other providers use the proper channel and must not
be second-guessed.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import llm as cli_llm  # noqa: E402

OFFERED = [
    {"type": "function", "function": {"name": "run", "parameters": {}}},
    {"type": "function", "function": {"name": "edit_file", "parameters": {}}},
    {"type": "function", "function": {"name": "read_file", "parameters": {}}},
]

# Verbatim from the live endpoint.
LIVE = ('[{"name": "run", "arguments": {"command": "python -m pytest"}},\n'
        ' {"name": "edit_file", "arguments": {"path": "calc.py", '
        '"old_string": "return a / b", "new_string": "return None"}}]')


def _recover(content, tools=OFFERED, provider="aria-llm"):
    return cli_llm.recover_tool_calls_from_content(content, tools, provider)


# -- THE CAPABILITY TEST ------------------------------------------------

def test_the_live_multi_call_array_is_recovered():
    """THE OPERATOR'S SYMPTOM: 'files: 0 changed, tools: 0 calls' on every
    coding task, because both calls arrived as text."""
    calls, remaining = _recover(LIVE)
    assert len(calls) == 2, f"expected 2 recovered calls, got {calls}"
    assert [c["function"]["name"] for c in calls] == ["run", "edit_file"]
    assert json.loads(calls[1]["function"]["arguments"])["path"] == "calc.py"
    assert not remaining.strip(), f"leftover prose: {remaining!r}"


def test_recovered_calls_are_openai_shaped():
    """The agent loop consumes OpenAI-shaped calls; a recovered one must be
    indistinguishable from a natively-parsed one or the loop breaks on it."""
    calls, _ = _recover(LIVE)
    for c in calls:
        assert c["type"] == "function"
        assert isinstance(c["id"], str) and c["id"]
        assert isinstance(c["function"]["arguments"], str), (
            "arguments must be a JSON STRING, as the OpenAI shape requires"
        )


def test_the_mistral_token_prefix_is_handled():
    """R-F4325 observed the literal [TOOL_CALLS] token arriving as text."""
    calls, _ = _recover("[TOOL_CALLS] " + LIVE)
    assert len(calls) == 2


def test_a_json_fence_is_handled():
    calls, _ = _recover("```json\n" + LIVE + "\n```")
    assert len(calls) == 2


def test_a_single_object_is_recovered():
    calls, _ = _recover('{"name": "run", "arguments": {"command": "ls"}}')
    assert len(calls) == 1 and calls[0]["function"]["name"] == "run"


# -- it must NOT invent calls -------------------------------------------

def test_ordinary_prose_is_left_alone():
    """The failure that matters: a fabricated `run` EXECUTES."""
    for prose in ("The function is defined in calc.py.",
                  "I will run the tests next.",
                  "Use the run tool with command 'pytest'.",
                  ""):
        calls, remaining = _recover(prose)
        assert calls == [], f"invented a call from prose: {prose!r} -> {calls}"
        assert remaining == prose


def test_prose_that_merely_contains_json_is_left_alone():
    """Containing JSON is not being JSON."""
    calls, _ = _recover('Here is what I would send: [{"name": "run", '
                        '"arguments": {"command": "ls"}}] — shall I?')
    assert calls == []


def test_an_unoffered_tool_name_is_refused():
    """A name we never advertised is a hallucination, not a call."""
    calls, _ = _recover('[{"name": "delete_everything", "arguments": {}}]')
    assert calls == []


def test_one_bad_element_rejects_the_whole_array():
    """Partial recovery would execute half a plan — worse than none, because
    the model never intended the half."""
    calls, _ = _recover('[{"name": "run", "arguments": {"command": "ls"}}, '
                        '{"name": "nope", "arguments": {}}]')
    assert calls == []


def test_arguments_must_be_an_object():
    calls, _ = _recover('[{"name": "run", "arguments": "ls"}]')
    assert calls == []


def test_a_bare_json_list_of_strings_is_refused():
    calls, _ = _recover('["run", "edit_file"]')
    assert calls == []


# -- scope --------------------------------------------------------------

def test_other_providers_are_not_second_guessed():
    """DeepSeek uses the proper channel; parsing its content would only ever
    create false positives."""
    calls, _ = _recover(LIVE, provider="deepseek")
    assert calls == []


def test_recovery_requires_tools_to_have_been_offered():
    """With no tools on the request there is no call to recover, and any
    match would be pure invention."""
    calls, _ = _recover(LIVE, tools=None)
    assert calls == []


def test_it_is_wired_into_the_response_path():
    """A recovery nothing calls is the §1 'certified by an absence' shape."""
    src = (ROOT / "aria_cli/llm.py").read_text(encoding="utf-8", errors="replace")
    # Count CALL sites, not the definition. Counting every occurrence let a
    # mutation that deleted the streaming call still pass (def + 1 call = 2) —
    # found by mutation testing, which is the only reason this is precise.
    call_sites = src.count("recover_tool_calls_from_content(") - src.count(
        "def recover_tool_calls_from_content(")
    assert call_sites >= 2, (
        f"recovery is applied at {call_sites} call site(s); it must be on BOTH "
        "the blocking and the streaming path (§13 stream-bypass) — a coding "
        "turn that works only when not streaming would be near-impossible to "
        "attribute"
    )


# -- the REAL shape: arrays separated by prose --------------------------

INTERLEAVED = """[{"name": "run", "arguments": {"command": "python -m pytest"}}]

After the test run, if it still fails, inspect the code and find the root cause.

[{"name": "edit_file", "arguments": {"path": "calc.py", "old_string": "return a / b", "new_string": "return None"}}]

After editing the file, run the tests again to confirm."""


def test_the_real_interleaved_output_is_recovered():
    """THE ACTUAL LIVE SHAPE, and the reason a strict whole-content JSON rule
    recovered nothing on the case that matters. She narrates BETWEEN the steps
    she plans, so the arrays arrive separated by prose."""
    calls, leftover = _recover(INTERLEAVED)
    assert [c["function"]["name"] for c in calls] == ["run", "edit_file"], calls
    assert "After the test run" in leftover, (
        "her narration was discarded; it is the visible reasoning the operator "
        "reads and must survive"
    )


def test_a_mid_sentence_mention_is_still_refused():
    """THE DISCRIMINATOR. A block she is EMITTING starts a line; a block she is
    TALKING ABOUT sits inside a sentence. Position, not punctuation — this is
    what keeps the loosened rule from becoming a prose parser."""
    calls, _ = _recover('I could send [{"name": "run", "arguments": '
                        '{"command": "rm -rf /"}}] but I will not.')
    assert calls == [], f"recovered a call from a sentence: {calls}"


def test_an_unoffered_name_in_an_interleaved_block_rejects_everything():
    """Validation still runs element-by-element after extraction."""
    bad = INTERLEAVED.replace('"edit_file"', '"delete_everything"')
    calls, _ = _recover(bad)
    assert calls == []


def test_prose_only_content_is_returned_untouched():
    text = "I will look at calc.py and then run the tests."
    calls, leftover = _recover(text)
    assert calls == [] and leftover == text
