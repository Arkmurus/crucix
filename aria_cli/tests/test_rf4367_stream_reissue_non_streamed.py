"""R-F4367 (C-313) — a tool call the model got RIGHT must not be lost because
the streaming transport truncated it.

MEASURED LIVE 2026-08-26 against the operator's own configuration
(``ARIA_CODER_LLM_PROVIDER=aria-llm``, ``aria-llm-v0.4-dpo`` on the RunPod vLLM,
Mistral tool parser). Five prompts, the SAME payload sent both ways::

    non-streamed   5/5 clean tool calls
    streamed       2/5   — and all three failures were `run`

The three streamed failures are plain truncation: vLLM drops the delta carrying
the closing ``"}``, so the arguments arrive as::

    {"command": "git status                       (from `git status`)
    {"command": "git log --oneline -5             (from `git log --oneline -5`)

R-F4351 (C-296) recovers the OTHER shape — where vLLM re-emits the complete
object as a final delta — and deliberately refuses to guess when nothing
complete was ever emitted, because a fabricated ``run`` EXECUTES. That refusal
is correct and stays. But it leaves the dominant live shape unrecovered:
``agent.py:944`` reports "could not parse arguments as JSON", the tool never
runs, and the turn ends ``tools: 0 calls`` with the model narrating about JSON.
Reproduced end-to-end through the real CLI 2026-08-26::

    aria -p "Run the shell command: git --version"
      ARIA  The command argument contains a comma, which is not valid JSON...
      tools:   0 calls

The fix is not a cleverer parser — a dropped closing quote cannot be
reconstructed without guessing. It is to ask the SAME question over the channel
that is not broken: re-issue the identical request non-streamed, once. That is
stronger evidence, not a better guess (the C-41 idiom), and the non-streamed
channel is measured 5/5 on this exact endpoint.

Fixed by SHAPE, not by a provider allow-list: any serving stack that truncates a
streamed tool call gets the same treatment, so this cannot rot the way a
hardcoded ``aria-llm`` branch would.
"""
from __future__ import annotations

import json

import httpx
import pytest

from aria_cli.llm import LLMClient, LLMConfig

TOOLS = [{"type": "function", "function": {
    "name": "run", "description": "Run a shell command",
    "parameters": {"type": "object",
                   "properties": {"command": {"type": "string"},
                                  "timeout": {"type": "integer"}},
                   "required": ["command"]}}}]

#: Captured off the wire 2026-08-26 — `git status`, streamed. The closing
#: ``"}`` delta is never sent, and no complete object is ever re-emitted.
TRUNCATED_DELTAS = ['{"command": "', 'git', ' status', '', '', '']

#: The same request non-streamed, same second, same pod.
CLEAN_ARGS = '{"command": "git status", "timeout": 10}'

#: R-F4351's shape: broken concatenation, complete object re-emitted last.
REEMISSION_DELTAS = ['{"command": "', 'git', ' status',
                     '{"command": "git status"}']

HEALTHY_DELTAS = ['{"command": "', 'git', ' status', '"}']


def _sse(deltas, name="run"):
    lines = ['data: ' + json.dumps({
        "choices": [{"index": 0, "delta": {
            "tool_calls": [{"id": "call_x", "type": "function", "index": 0,
                            "function": {"name": name}}]}}]})]
    for d in deltas:
        lines.append('data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "function": {"arguments": d}}]}}]}))
    lines.append('data: ' + json.dumps({
        "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}))
    lines.append('data: [DONE]')
    return lines


class _FakeStream:
    def __init__(self, lines):
        self.status_code, self._lines = 200, lines

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeResponse:
    def __init__(self, payload):
        self.status_code, self._p = 200, payload

    def json(self):
        return self._p

    @property
    def text(self):
        return json.dumps(self._p)


def _blocking_payload(arguments):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "iO7i5vD5W", "type": "function",
                        "function": {"name": "run", "arguments": arguments}}]}}],
        "usage": {"prompt_tokens": 90, "completion_tokens": 69}}


def _client(monkeypatch, deltas, *, blocking=None, post_raises=None):
    """Streaming returns ``deltas``; the non-streamed re-issue returns
    ``blocking`` (or raises). ``posts`` records every re-issue attempt."""
    c = LLMClient(LLMConfig(provider="aria-llm", model="aria-llm-v0.4-dpo",
                            base_url="http://127.0.0.1:9/v1"))
    monkeypatch.setattr(c._client, "stream",
                        lambda *a, **k: _FakeStream(_sse(deltas)))
    posts = []

    def _post(*a, **k):
        posts.append(k.get("json"))
        if post_raises is not None:
            raise post_raises
        return _FakeResponse(blocking if blocking is not None
                             else _blocking_payload(CLEAN_ARGS))

    monkeypatch.setattr(c._client, "post", _post)
    return c, posts


# ── the defect ──────────────────────────────────────────────────────────────

def test_truncated_stream_is_reissued_non_streamed_and_the_tool_runs(monkeypatch):
    """THE DEFECT. The operator's `run the shell command` turn must produce an
    executable tool call, not a parse error."""
    c, posts = _client(monkeypatch, TRUNCATED_DELTAS)
    resp = c.chat_stream([{"role": "user", "content": "run git status"}], tools=TOOLS)

    assert len(posts) == 1, "the corrupt turn was not re-issued non-streamed"
    assert posts[0].get("stream") is not True, "the re-issue must NOT stream"
    assert len(resp.tool_calls) == 1
    # The user-visible outcome: agent.py can json.loads() this and execute it.
    assert json.loads(resp.tool_calls[0]["function"]["arguments"]) == {
        "command": "git status", "timeout": 10}


def test_the_reissue_sends_the_same_messages_and_tools(monkeypatch):
    """It must re-ask the SAME question. Re-issuing a different conversation
    would answer a question the operator never asked."""
    msgs = [{"role": "user", "content": "run git status"}]
    c, posts = _client(monkeypatch, TRUNCATED_DELTAS)
    c.chat_stream(msgs, tools=TOOLS)

    sent = posts[0]
    assert [m["content"] for m in sent["messages"]] == ["run git status"]
    assert [t["function"]["name"] for t in sent["tools"]] == ["run"]


# ── it must not engage on anything that is not already broken ───────────────

def test_healthy_stream_is_never_reissued(monkeypatch):
    """A working stream must cost exactly one call. A re-issue on a healthy
    turn would double every tool call's latency and token spend."""
    c, posts = _client(monkeypatch, HEALTHY_DELTAS)
    resp = c.chat_stream([{"role": "user", "content": "x"}], tools=TOOLS)

    assert posts == [], "a healthy stream triggered a needless re-issue"
    assert json.loads(resp.tool_calls[0]["function"]["arguments"]) == {
        "command": "git status"}


def test_reemission_is_repaired_locally_without_a_reissue(monkeypatch):
    """R-F4351 still owns its shape. Re-issuing when the answer is already in
    hand would make that repair dead code."""
    c, posts = _client(monkeypatch, REEMISSION_DELTAS)
    resp = c.chat_stream([{"role": "user", "content": "x"}], tools=TOOLS)

    assert posts == [], "a locally-recoverable stream was re-issued"
    assert json.loads(resp.tool_calls[0]["function"]["arguments"]) == {
        "command": "git status"}


def test_a_content_only_turn_is_never_reissued(monkeypatch):
    """No tool call, nothing corrupt — prose must stream and stop."""
    c, posts = _client(monkeypatch, [])
    lines = ['data: ' + json.dumps(
                 {"choices": [{"index": 0, "delta": {"content": "hello"}}]}),
             'data: [DONE]']
    monkeypatch.setattr(c._client, "stream", lambda *a, **k: _FakeStream(lines))
    resp = c.chat_stream([{"role": "user", "content": "hi"}], tools=TOOLS)

    assert posts == []
    assert resp.content == "hello"


# ── safety: it may never invent a call ──────────────────────────────────────

def test_a_reissue_that_is_also_corrupt_stays_honest(monkeypatch):
    """If the re-issue is broken too there is nothing to recover. Hand
    agent.py the original broken text so it reports an honest parse error —
    never synthesise a command, because a fabricated `run` RUNS."""
    c, posts = _client(monkeypatch, TRUNCATED_DELTAS,
                       blocking=_blocking_payload('{"command": "git stat'))
    resp = c.chat_stream([{"role": "user", "content": "x"}], tools=TOOLS)

    assert len(posts) == 1
    raw = resp.tool_calls[0]["function"]["arguments"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert raw == "".join(TRUNCATED_DELTAS), "broken args were altered"


def test_a_reissue_that_returns_no_tool_call_keeps_the_original(monkeypatch):
    """An empty re-issue is not evidence the model wanted nothing — dropping
    the call would turn a corrupt turn into a silently empty one."""
    c, posts = _client(monkeypatch, TRUNCATED_DELTAS,
                       blocking={"choices": [{"message": {"role": "assistant",
                                                          "content": "ok"}}],
                                 "usage": {}})
    resp = c.chat_stream([{"role": "user", "content": "x"}], tools=TOOLS)

    assert len(posts) == 1
    assert len(resp.tool_calls) == 1, "the original tool call was dropped"
    assert resp.tool_calls[0]["function"]["arguments"] == "".join(TRUNCATED_DELTAS)


def test_a_failing_reissue_never_raises(monkeypatch):
    """The re-issue is a repair attempt, not a new dependency. If the endpoint
    is unreachable the turn must still return the honest broken call rather
    than killing the session."""
    c, posts = _client(monkeypatch, TRUNCATED_DELTAS,
                       post_raises=httpx.ConnectError("refused"))
    resp = c.chat_stream([{"role": "user", "content": "x"}], tools=TOOLS)

    assert len(posts) == 1
    assert resp.tool_calls[0]["function"]["arguments"] == "".join(TRUNCATED_DELTAS)


def test_the_reissue_is_counted_for_observability(monkeypatch):
    """§21a — a silent repair hides a serving defect. The count is how we see
    the pod's tool parser degrading."""
    c, _ = _client(monkeypatch, TRUNCATED_DELTAS)
    assert c.stream_arg_reissues == 0
    c.chat_stream([{"role": "user", "content": "x"}], tools=TOOLS)
    assert c.stream_arg_reissues == 1
