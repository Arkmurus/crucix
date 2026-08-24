"""R-F4308 / C-261 - coder CLI sessions produced no training signal at all.

The operator asked that ARIA compound from every interaction, including code
sessions. She does not. A grep of aria_cli/agent.py, cli.py and memory.py for
capture / harvest / distill / ingest returns NOTHING: the coder runs, edits
files, runs tests, and every turn evaporates. `output_harvester` captures CHAT
outputs and `claude_distill` captures teacher notes; the coder - the surface
doing the most substantive work - captures nothing.

This mirrors output_harvester's proven shape rather than inventing one: score
deterministically (no LLM call), redact BEFORE writing, append one JSONL row per
accepted turn. What differs is the QUALITY SIGNAL, because what makes a good chat
answer is not what makes a good coding turn.

FOUR GATES, and each exists because its absence would poison the corpus:

  * ABORTED TURNS ARE NEVER CAPTURED. An aborted turn is a demonstration of
    failure. Training on it teaches the failure.
  * A TURN THAT USED NO TOOLS IS NOT CODING SIGNAL. In a coder CLI a zero-step
    answer is a chat reply; capturing it trains conversation, not engineering.
  * REFUSALS AND TRIVIA ARE DROPPED, on the same reasoning as C-257's floor: the
    claude corpus was 41 unique texts with a 26-character median, and fragments
    dilute the few rows that carry anything.
  * REDACTION HAPPENS BEFORE SCORING AND BEFORE WRITING. A coder reads source
    files and shell output, so a captured turn can contain credentials. Redacting
    after the write would mean the secret was already on disk.

CAPTURE MUST NEVER AFFECT THE SESSION. It is best-effort: any failure is
swallowed, and no exception from it can reach the operator's turn. A learning
sink that can break the tool it observes will be turned off.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import session_capture as sc  # noqa: E402


class _Result:
    def __init__(self, final_text="", steps=3, aborted=False, resumable=False):
        self.final_text = final_text
        self.steps = steps
        self.aborted = aborted
        self.resumable = resumable


_GOOD = ("Read config.py, found the stale timeout, replaced it with the value "
         "from settings and re-ran the suite - 41 passed. " * 6)


# -- the four gates ---------------------------------------------------------

def test_a_good_turn_is_captured(tmp_path) -> None:
    """THE CAPABILITY TEST - a real coding turn becomes a training row."""
    ok, reason = sc.capture_turn("fix the stale timeout in config.py",
                                 _Result(_GOOD), out_dir=tmp_path)
    assert ok is True, reason
    rows = [json.loads(l) for l in
            next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"]
    assert r["messages"][0]["content"] == "fix the stale timeout in config.py"
    assert r["source"] == "coder_session"


def test_an_aborted_turn_is_never_captured(tmp_path) -> None:
    """An aborted turn demonstrates failure. Training on it teaches the failure."""
    ok, reason = sc.capture_turn("do the thing", _Result(_GOOD, aborted=True),
                                 out_dir=tmp_path)
    assert ok is False and "aborted" in reason
    assert not list(tmp_path.glob("*.jsonl"))


def test_a_turn_that_used_no_tools_is_not_coding_signal(tmp_path) -> None:
    """A zero-step answer in a coder CLI is a chat reply, not engineering."""
    ok, reason = sc.capture_turn("what is a decorator?", _Result(_GOOD, steps=0),
                                 out_dir=tmp_path)
    assert ok is False and "tool" in reason.lower()


def test_a_refusal_is_dropped(tmp_path) -> None:
    for text in ("I can't do that.", "I'm unable to help with this.",
                 "As an AI, I cannot run commands."):
        ok, reason = sc.capture_turn("do it", _Result(text), out_dir=tmp_path)
        assert ok is False, text


def test_a_fragment_is_dropped(tmp_path) -> None:
    ok, reason = sc.capture_turn("ok?", _Result("done"), out_dir=tmp_path)
    assert ok is False and "short" in reason.lower()


def test_an_empty_instruction_is_dropped(tmp_path) -> None:
    """No instruction means no (instruction, response) pair - the same rule
    C-257 applies to unpaired teacher notes: drop, never invent."""
    ok, reason = sc.capture_turn("   ", _Result(_GOOD), out_dir=tmp_path)
    assert ok is False


# -- redaction happens BEFORE the write -------------------------------------

def test_a_secret_never_reaches_disk(tmp_path) -> None:
    """A coder reads source and shell output, so a turn can carry credentials.
    Redacting after the write would mean the secret was already on disk."""
    leak = ("Updated the client. " * 20) + '\nDEEPSEEK_API_KEY = "sk-abc123def456ghi789jkl"\n'
    ok, reason = sc.capture_turn("wire the client", _Result(leak), out_dir=tmp_path)
    assert ok is True, reason
    written = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "sk-abc123def456ghi789jkl" not in written, "a live-looking key was written"


def test_the_instruction_is_redacted_too(tmp_path) -> None:
    """The operator can paste a credential into the PROMPT just as easily."""
    ok, _ = sc.capture_turn(
        'use token Bearer abcdefghijklmnop1234 to call it and report the result',
        _Result(_GOOD), out_dir=tmp_path)
    assert ok is True
    written = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "abcdefghijklmnop1234" not in written


# -- capture must never affect the session ----------------------------------

def test_an_unwritable_destination_never_raises(tmp_path) -> None:
    """A learning sink that can break the tool it observes gets switched off."""
    blocked = tmp_path / "file_not_dir"
    blocked.write_text("x", encoding="utf-8")
    ok, reason = sc.capture_turn("do it", _Result(_GOOD), out_dir=blocked)
    assert ok is False and reason          # reported, not raised


def test_a_malformed_result_never_raises(tmp_path) -> None:
    for junk in (None, object(), "not a result"):
        ok, reason = sc.capture_turn("do it", junk, out_dir=tmp_path)
        assert ok is False


def test_capture_is_off_unless_enabled(tmp_path, monkeypatch) -> None:
    """Opt-in. It writes the operator's source and shell output to disk, so it
    must not start doing that because a version changed under them."""
    monkeypatch.setenv("ARIA_CODER_CAPTURE_ENABLED", "0")
    ok, reason = sc.capture_turn("fix it", _Result(_GOOD), out_dir=tmp_path,
                                 respect_env=True)
    assert ok is False and "disabled" in reason.lower()


# -- it must actually be WIRED, or it is a capability nothing calls ---------

def test_the_agent_calls_the_capture() -> None:
    """R-F3099's shape: built, tested, never invoked."""
    src = (ROOT / "aria_cli/agent.py").read_text(encoding="utf-8")
    assert "session_capture" in src, "agent.py never calls the capture"
    assert "capture_turn" in src


def test_the_capture_is_after_the_turn_completes() -> None:
    """Capturing mid-turn would record an unfinished demonstration."""
    src = (ROOT / "aria_cli/agent.py").read_text(encoding="utf-8")
    i = src.index("def run_until_complete")
    body = src[i:i + 3000]
    assert "capture_turn" in body, (
        "capture is not in run_until_complete - a mid-turn capture records an "
        "unfinished demonstration")
