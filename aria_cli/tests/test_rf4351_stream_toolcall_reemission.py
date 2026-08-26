"""R-F4351 (C-296) — streamed tool-call arguments must survive vLLM's
partial-delta re-emission.

MEASURED LIVE 2026-08-26 against the sovereign pod
(``aria-llm-v0.4-dpo`` on vLLM, Mistral tool parser). The SAME payload,
non-streamed, returns a clean tool call::

    {"command": "cat README.md | wc -l", "timeout": 10}       # parses

Streamed, vLLM emits 21 argument deltas. Deltas 1-20 assemble to a string
that is MISSING the closing quote after ``-l`` — and then delta 21 re-emits
the WHOLE object again::

    delta[01] '{"command": "'
    delta[11] 'l'
    delta[17] ', "timeout": 1'
    delta[21] '{"command": "cat README.md | wc -l", "timeout": 10}'

``chat_stream`` concatenated all 21 (llm.py:963, per the OpenAI streaming
contract), producing::

    {"command": "cat README.md | wc -l, "timeout": 10{"command": ...}

which fails ``json.loads``. So a call the model got RIGHT was corrupted by
the transport, agent.py recorded "could not parse arguments as JSON", and
the turn ended ``tools: 0 calls`` with the model narrating about JSON
formatting instead of acting.

The three tests below pin the three properties the repair must have, and
the last two are the ones that keep it honest: a repair that could invent a
call would be worse than the defect, because a fabricated ``run`` EXECUTES.
"""
from __future__ import annotations

import json

import pytest

from aria_cli.llm import LLMClient, LLMConfig


# ── the exact deltas captured off the wire ──────────────────────────────────

#: vLLM re-emission: 1-20 assemble to broken JSON, 21 is the complete object.
REEMISSION_DELTAS = [
    '{"command": "', 'cat', ' READ', 'ME', '.', 'md', ' |', ' w', 'c', ' -',
    'l', '', '', '', '', '', ', "timeout": 1', '0', '', '',
    '{"command": "cat README.md | wc -l", "timeout": 10}',
]

#: A healthy incremental stream (captured from the same pod, `git status`).
#: Plain concatenation is CORRECT here and must not be second-guessed.
HEALTHY_DELTAS = ['{"command": "', 'git', ' status', '"}']

#: Genuinely truncated: the model emitted an invalid JSON escape (C:\Code)
#: and vLLM stopped. No complete object is ever emitted, so there is nothing
#: to recover and the transport must NOT invent one.
TRUNCATED_DELTAS = ['{"command": "', 'powershell', ' -Command ', 'Get-Content -Path C:']


def _sse(deltas: list[str], name: str = "run") -> list[str]:
    """Render argument deltas as the SSE lines vLLM actually sends."""
    lines = [
        'data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {
                "tool_calls": [{"id": "call_x", "type": "function", "index": 0,
                                "function": {"name": name}}]}}]}),
    ]
    for d in deltas:
        lines.append('data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "function": {"arguments": d}}]}}]}))
    lines.append('data: ' + json.dumps({
        "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}))
    lines.append('data: [DONE]')
    return lines


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self._lines = lines

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _client_streaming(monkeypatch, deltas: list[str]) -> LLMClient:
    client = LLMClient(LLMConfig(provider="aria-llm", model="aria-llm-v0.4-dpo",
                                 base_url="http://127.0.0.1:9/v1"))
    monkeypatch.setattr(client._client, "stream",
                        lambda *a, **k: _FakeStream(_sse(deltas)))
    return client


TOOLS = [{"type": "function", "function": {
    "name": "run", "description": "Run a shell command",
    "parameters": {"type": "object",
                   "properties": {"command": {"type": "string"},
                                  "timeout": {"type": "integer"}},
                   "required": ["command"]}}}]


def test_reemitted_arguments_are_recovered_not_concatenated(monkeypatch):
    """THE DEFECT. The call the model got right must survive the transport."""
    client = _client_streaming(monkeypatch, REEMISSION_DELTAS)
    resp = client.chat_stream([{"role": "user", "content": "count lines"}], tools=TOOLS)

    assert len(resp.tool_calls) == 1, "the tool call must not be dropped"
    raw = resp.tool_calls[0]["function"]["arguments"]

    # The user-visible outcome: agent.py can json.loads() this and act.
    args = json.loads(raw)
    assert args == {"command": "cat README.md | wc -l", "timeout": 10}


def test_healthy_incremental_stream_is_untouched(monkeypatch):
    """A well-formed stream must still be plain concatenation — the repair
    may only ever engage on something ALREADY broken."""
    client = _client_streaming(monkeypatch, HEALTHY_DELTAS)
    resp = client.chat_stream([{"role": "user", "content": "status"}], tools=TOOLS)

    raw = resp.tool_calls[0]["function"]["arguments"]
    assert raw == '{"command": "git status"}', "healthy stream was rewritten"
    assert json.loads(raw) == {"command": "git status"}


#: Arguments containing a NESTED object. The inner ``{"old": ..., "new": ...}``
#: arrives as its own delta and is itself a complete JSON object — so it is a
#: standing trap for any repair that scans fragments without first checking
#: whether the plain concatenation already works.
NESTED_HEALTHY_DELTAS = [
    '{"path": "x.py", "edit": ', '{"old": "a", "new": "b"}', '}',
]

#: The same nested call, but broken mid-stream and then re-emitted whole.
NESTED_REEMISSION_DELTAS = [
    '{"path": "x.py", "edit": ', '{"old": "a", "new": "b"}', ', "mode": ',
    '{"path": "x.py", "edit": {"old": "a", "new": "b"}, "mode": "patch"}',
]


def test_healthy_nested_arguments_are_not_replaced_by_an_inner_object(monkeypatch):
    """A healthy stream whose arguments CONTAIN an object must be returned by
    concatenation. Scanning fragments first would find the inner object, return
    it, and silently discard every outer field — a wrong call that still
    parses, which is the worst possible failure here."""
    client = _client_streaming(monkeypatch, NESTED_HEALTHY_DELTAS)
    resp = client.chat_stream([{"role": "user", "content": "edit"}], tools=TOOLS)

    args = json.loads(resp.tool_calls[0]["function"]["arguments"])
    assert args == {"path": "x.py", "edit": {"old": "a", "new": "b"}}


def test_the_last_complete_object_wins_not_the_first(monkeypatch):
    """When the stream IS broken and holds several complete objects, the
    re-emission is the model's final word and sits LAST. Taking the first would
    pick up a nested fragment instead of the whole call."""
    client = _client_streaming(monkeypatch, NESTED_REEMISSION_DELTAS)
    resp = client.chat_stream([{"role": "user", "content": "edit"}], tools=TOOLS)

    args = json.loads(resp.tool_calls[0]["function"]["arguments"])
    assert args == {"path": "x.py", "edit": {"old": "a", "new": "b"},
                    "mode": "patch"}


def test_a_scalar_delta_is_never_mistaken_for_the_arguments(monkeypatch):
    """SAFETY. A lone numeric delta (``'0'``, ``'10'``) is VALID JSON and vLLM
    emits them routinely. Only an OBJECT may be taken as the arguments —
    accepting a scalar would drop every real field while still 'parsing'."""
    # Broken concatenation, and the LAST parseable fragment is the scalar '10'.
    deltas = ['{"command": "', 'ls, "timeout": ', '10']
    client = _client_streaming(monkeypatch, deltas)
    resp = client.chat_stream([{"role": "user", "content": "ls"}], tools=TOOLS)

    raw = resp.tool_calls[0]["function"]["arguments"]
    assert raw != "10", "a scalar delta was taken as the arguments object"
    assert raw == "".join(deltas), "unrecoverable args must pass through intact"


def test_truncated_stream_is_not_fabricated_into_a_call(monkeypatch):
    """SAFETY. With no complete object anywhere in the stream there is nothing
    to recover. The transport must hand agent.py the broken text so it reports
    a parse error — never guess a command, because a fabricated `run` RUNS."""
    client = _client_streaming(monkeypatch, TRUNCATED_DELTAS)
    resp = client.chat_stream([{"role": "user", "content": "logs"}], tools=TOOLS)

    raw = resp.tool_calls[0]["function"]["arguments"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert raw == "".join(TRUNCATED_DELTAS), "truncated args were altered"
