"""R-F4302 / C-255 - the bridge never forwarded, and a docstring said it did.

POST /api/aria/collab/ingest (routes/aria.py) exists to close the loop, and its
docstring states both the problem and the remedy:

    "Claude runs on the operator's LOCAL machine and writes to the local
     .agent_bridge/ file mailbox, which is NEVER shipped to aria-intel - so the
     server's Redis collab log had zero writers and everything Claude taught ARIA
     was forgotten. The local scripts/agent_bridge.py now best-effort POSTs
     Claude's messages here."

THE LAST SENTENCE IS FALSE. scripts/agent_bridge.py is 105 lines with no http,
urllib, requests, POST or env reference of any kind; it calls bridge.send(base,
...), which writes a local file and returns. `git log -S "collab/ingest" --
scripts/agent_bridge.py` is EMPTY - the forwarder was never built, not
built-and-removed.

The consequence is measurable and is why C-254 found what it did: the teacher
corpus holds 24 substantial notes across its entire lifetime, because nothing was
carrying Claude's side of the conversation to the server. The one mechanism
designed to let ARIA compound from a stronger agent has never run.

This is the documented-capability-with-no-call-sites shape. A docstring is not an
implementation, and a session reading that route would reasonably conclude the
forward worked and go hunting for a config problem instead - setting
ARIA_SERVICE_URL / ARIA_INTERNAL_TOKEN, which a sibling docstring recommends,
would have configured a no-op and been reported as done.

DESIGN CONSTRAINTS, each pinned below:

  * THE LOCAL WRITE IS THE SOURCE OF TRUTH. The forward is best-effort and must
    never be able to lose a note. If the POST fails the message is still in the
    local mailbox.
  * IT MUST NEVER RAISE. This runs inside a CLI the operator uses interactively.
  * IT MUST SAY WHAT HAPPENED. A silent best-effort forward is how a corpus ends
    up with 24 notes and nobody notices. Unconfigured, refused and failed each
    report a reason on stdout.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.agent_bridge as ab  # noqa: E402

_ENV = ("ARIA_SERVICE_URL", "ARIA_INTERNAL_TOKEN", "ARIA_BRAIN_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    # never read the developer's real .env during tests
    monkeypatch.setattr(ab, "_env_file_values", lambda: {}, raising=False)


def _msg():
    return {"id": "cb_local_1", "frm": "claude", "to": "aria",
            "kind": "note", "text": "R-F4302 probe", "reply_to": ""}


# -- it exists at all -------------------------------------------------------

def test_the_forwarder_exists() -> None:
    assert hasattr(ab, "forward_to_server"), (
        "scripts/agent_bridge.py has no forwarder - the route docstring claims "
        "it POSTs, and it does not")


# -- unconfigured is REPORTED, never silent ---------------------------------

def test_unconfigured_reports_a_reason_and_posts_nothing(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(ab, "_http_post", lambda *a, **k: calls.append(a))
    ok, reason = ab.forward_to_server(_msg())
    assert ok is False
    assert reason, "an unconfigured forward must SAY so - silence is the defect"
    assert "ARIA_SERVICE_URL" in reason
    assert calls == [], "no request may be attempted without a target"


def test_a_url_without_a_token_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    calls = []
    monkeypatch.setattr(ab, "_http_post", lambda *a, **k: calls.append(a))
    ok, reason = ab.forward_to_server(_msg())
    assert ok is False
    assert "ARIA_INTERNAL_TOKEN" in reason
    assert calls == [], "the ingest route is a brain WRITE - never post unauthenticated"


# -- the happy path ---------------------------------------------------------

def test_a_configured_forward_posts_the_message(monkeypatch) -> None:
    seen = {}

    def _post(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return 200, json.dumps({"ok": True, "seq": 175, "id": "cb_175"})

    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok-abc")
    monkeypatch.setattr(ab, "_http_post", _post)

    ok, reason = ab.forward_to_server(_msg())
    assert ok is True, reason
    assert seen["url"].endswith("/api/aria/collab/ingest")
    assert seen["headers"]["Authorization"] == "Bearer tok-abc"
    body = json.loads(seen["payload"])
    assert body["text"] == "R-F4302 probe"
    assert body["frm"] == "claude" and body["to"] == "aria"
    assert body["kind"] == "note"


def test_a_trailing_slash_does_not_double_up(monkeypatch) -> None:
    seen = {}

    def _post(url, payload, headers, timeout):
        seen["url"] = url
        return 200, "{}"

    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev/")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    monkeypatch.setattr(ab, "_http_post", _post)
    ab.forward_to_server(_msg())
    assert "//api/aria" not in seen["url"]


def test_a_reply_carries_its_reply_to(monkeypatch) -> None:
    seen = {}

    def _post(url, payload, headers, timeout):
        seen["payload"] = payload
        return 200, "{}"

    monkeypatch.setenv("ARIA_SERVICE_URL", "https://x.invalid")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    monkeypatch.setattr(ab, "_http_post", _post)
    m = _msg()
    m["kind"] = "answer"
    m["reply_to"] = "cb_7"
    ab.forward_to_server(m)
    body = json.loads(seen["payload"])
    assert body["reply_to"] == "cb_7" and body["kind"] == "answer"


# -- failure must be loud, never fatal --------------------------------------

def test_an_http_error_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://x.invalid")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    monkeypatch.setattr(ab, "_http_post", lambda *a, **k: (401, "unauthorized"))
    ok, reason = ab.forward_to_server(_msg())
    assert ok is False
    assert "401" in reason


def test_a_transport_failure_never_raises(monkeypatch) -> None:
    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setenv("ARIA_SERVICE_URL", "https://x.invalid")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    monkeypatch.setattr(ab, "_http_post", _boom)
    ok, reason = ab.forward_to_server(_msg())          # must not raise
    assert ok is False and "OSError" in reason


# -- the capability test: the operator-visible behaviour --------------------

def test_send_writes_locally_AND_reports_the_forward(tmp_path, monkeypatch, capsys) -> None:
    """THE CAPABILITY TEST. The local mailbox is the source of truth: a failed
    forward must never lose the note, and the CLI must say what happened."""
    monkeypatch.setattr(ab, "_base", lambda: tmp_path)
    monkeypatch.setattr(ab, "_http_post", lambda *a, **k: (500, "boom"))
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://x.invalid")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")

    rc = ab.main(["send", "a real teacher note"])
    assert rc == 0
    out = capsys.readouterr().out

    from aria_cli import bridge
    local = bridge._all(tmp_path)
    assert any(m.get("text") == "a real teacher note" for m in local), (
        "the note was LOST when the forward failed - the local write is the "
        "source of truth and must be independent of the POST")
    assert "forward" in out.lower(), (
        "a failed forward must be visible to the operator, not silent")


def test_send_reports_success_when_the_forward_lands(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ab, "_base", lambda: tmp_path)
    monkeypatch.setattr(
        ab, "_http_post",
        lambda *a, **k: (200, json.dumps({"ok": True, "id": "cb_9"})))
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    ab.main(["send", "teacher signal"])
    out = capsys.readouterr().out.lower()
    assert "forward" in out and "cb_9" in out


def test_aria_side_notes_are_not_forwarded_as_teacher_signal(tmp_path, monkeypatch) -> None:
    """Only Claude->ARIA is teacher signal. Forwarding ARIA's own notes back would
    feed her own output into the corpus she learns from."""
    calls = []

    def _post(*a, **k):
        calls.append(a)
        return 200, "{}"

    monkeypatch.setattr(ab, "_base", lambda: tmp_path)
    monkeypatch.setattr(ab, "_http_post", _post)
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    ab.main(["--as", "aria", "send", "aria speaking"])
    assert calls == [], "ARIA->Claude must not be forwarded into the teacher corpus"
