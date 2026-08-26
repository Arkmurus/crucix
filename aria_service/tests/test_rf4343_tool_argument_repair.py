"""R-F4343 — C-287: a truncated ``arguments`` string wedged every following turn.

CAPTURED LIVE off the wire during a real coding turn — the sovereign emitted a
tool call cut off mid-value:

    {"command": "pytest C:\\\\Users\\\\anton\\\\...\\\\calc.py

no closing quote, no closing brace. `agent.py` handles that correctly: it catches
the parse error and records a tool result saying so. But the broken assistant
message stayed in the history and was echoed on the NEXT request, and the
provider rejects it —

    HTTP 400 "Unterminated string starting at: line 1 column 13 (char 12)"

— so ONE malformed generation killed every remaining turn of the session. That is
the operator's "files: 0 changed, tools: 0 calls" on real coding work.

The model's own mistake is survivable. Echoing it back is what made it fatal.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli.llm import _sanitize_messages, _wire_messages  # noqa: E402

#: The EXACT arguments captured on the wire, truncated exactly as she emitted it.
TRUNCATED = '{"command": "pytest C:\\\\Users\\\\anton\\\\scratchpad\\\\calc.py'
VALID = '{"path": "calc.py"}'


def _call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


def _history(*calls):
    return [
        {"role": "user", "content": "fix calc.py"},
        {"role": "assistant", "content": "", "tool_calls": list(calls)},
        *[{"role": "tool", "tool_call_id": c["id"], "content": "result"}
          for c in calls],
    ]


def _args_of(msgs):
    for m in msgs:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return [tc["function"]["arguments"] for tc in m["tool_calls"]]
    return []


# -- THE CAPABILITY TEST -------------------------------------------------------

def test_the_truncated_call_captured_live_is_repaired():
    """Before this fix the array below produced HTTP 400 on every later turn."""
    out = _sanitize_messages(_history(_call("abcdefghi", "run", TRUNCATED)))
    for args in _args_of(out):
        json.loads(args)  # raises -> the wedge is back


def test_every_argument_on_the_wire_parses_as_json():
    """The invariant, stated once: nothing unparseable leaves this layer."""
    out = _sanitize_messages(_history(
        _call("aaaaaaaaa", "run", TRUNCATED),
        _call("bbbbbbbbb", "read_file", VALID),
        _call("ccccccccc", "grep", {"pattern": "x"}),
        _call("ddddddddd", "run", None),
    ))
    got = _args_of(out)
    assert len(got) == 4
    for args in got:
        assert isinstance(args, str)
        json.loads(args)


# -- it must not touch what is already correct --------------------------------

def test_valid_arguments_are_passed_through_byte_identical():
    """A repair that rewrites healthy input is a corruption. The exact bytes
    matter: re-serialising would reorder keys and change spacing under the
    model's own formatting."""
    out = _sanitize_messages(_history(_call("bbbbbbbbb", "read_file", VALID)))
    assert _args_of(out) == [VALID]


def test_a_dict_is_serialised_not_blanked():
    """Some providers send a dict. That IS recoverable, so recover it — blanking
    would silently drop a perfectly good call."""
    out = _sanitize_messages(_history(_call("ccccccccc", "grep", {"pattern": "x"})))
    assert json.loads(_args_of(out)[0]) == {"pattern": "x"}


# -- the repair must not cost the model its feedback ---------------------------

def test_the_tool_result_survives_the_repair():
    """Dropping the malformed CALL instead would orphan its tool message, which
    the same pass then discards — taking with it the error text that tells her
    the arguments were unparseable. She must still learn what went wrong."""
    out = _sanitize_messages(_history(_call("abcdefghi", "run", TRUNCATED)))
    tools = [m for m in out if m.get("role") == "tool"]
    assert len(tools) == 1 and tools[0]["tool_call_id"] == "abcdefghi"


def test_it_is_pure():
    """Documented contract of this layer: does not mutate the input."""
    msgs = _history(_call("abcdefghi", "run", TRUNCATED))
    _sanitize_messages(msgs)
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == TRUNCATED


# -- §13: both transports, and every provider ---------------------------------

@pytest.mark.parametrize("provider", ["aria-llm", "deepseek", "anthropic", ""])
def test_the_repair_is_not_scoped_to_one_provider(provider):
    """An `arguments` value that is not a JSON string is invalid in the OpenAI
    tool contract for EVERYONE, so this can only ever repair something already
    broken. Scoping it would leave the other providers holding the same wedge."""
    out = _wire_messages(_history(_call("abcdefghi", "run", TRUNCATED)), provider)
    for args in _args_of(out):
        json.loads(args)


def test_both_transports_share_one_entry_point():
    """§13 stream-bypass: a guard on one path only is this repo's repeat failure.
    `_wire_messages` is the single point both call, so assert the repair is
    reached THROUGH it rather than trusting that both were remembered."""
    out = _wire_messages(_history(_call("abcdefghi", "run", TRUNCATED)), "aria-llm")
    assert _args_of(out) == ["{}"]
