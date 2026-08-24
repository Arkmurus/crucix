"""R-F4307 / C-260 - two forwarders would have POSTed every note twice.

MY OWN DEFECT, and worth recording as such. R-F4302 added a forwarder to
scripts/agent_bridge.py after `git log -S "collab/ingest"` over THAT FILE came
back empty. The conclusion drawn - "the forwarder was never built" - was true of
the file and false of the system: `aria_cli/bridge.py` has had
`_forward_to_server` since R-F2400, and `bridge.send()` calls it on every
message. I searched one file and generalised to the codebase.

The root-cause diagnosis was still right - the forward was a no-op because
ARIA_SERVICE_URL and ARIA_INTERNAL_TOKEN were unset - but the remedy added a
SECOND implementation beside the existing one. `scripts/agent_bridge.py send`
calls `bridge.send()` (which forwards, R-F2400) and then reports via its own
forwarder (which forwards again, R-F4302). Every note would have produced TWO
collab messages and TWO corpus rows.

IT HAS NOT FIRED YET, and only by accident: the R-F2400 forwarder reads
`os.getenv`, while the R-F4302 one reads the repo `.env`. The credentials went
into `.env`, so exactly one of them was live. The moment anyone exports those
vars into the shell - the ordinary way to configure a tool - both fire and the
corpus doubles. That is C-254's 450x amplification in miniature, reintroduced by
the fix for C-255.

THE RULE IS THE ONE §1 AND R-F2639 ALREADY STATE: one measure, not two. There is
now a single implementation. `forward_to_server()` returns (ok, reason) so the
CLI can SAY what happened - the genuinely useful half of R-F4302, since a silent
best-effort forward is how a corpus reaches 24 notes unnoticed. `_forward_to_server()`
survives as a thin bool wrapper because R-F2399 pins that contract in three
assertions, and breaking a passing test to suit a refactor is not a fix.

The .env fallback moves to the canonical forwarder, which is where it should have
gone in the first place: that, not a missing implementation, was why nothing
forwarded.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import bridge  # noqa: E402
import scripts.agent_bridge as ab  # noqa: E402

_ENV = ("ARIA_SERVICE_URL", "ARIA_BRAIN_URL", "ARIA_INTERNAL_TOKEN", "ARIA_API_TOKEN")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(bridge, "_env_file_values", lambda: {}, raising=False)


def _msg(frm="claude"):
    return {"id": "m1", "frm": frm, "to": "aria", "kind": "note",
            "text": "teacher signal", "reply_to": ""}


# -- exactly one implementation ---------------------------------------------

def test_agent_bridge_does_not_carry_its_own_poster() -> None:
    """THE CAPABILITY TEST. A second POST path is a second corpus row."""
    src = (ROOT / "scripts/agent_bridge.py").read_text(encoding="utf-8")
    assert not re.search(r"urllib\.request\.urlopen|httpx\.post|requests\.post", src), (
        "scripts/agent_bridge.py posts to the server itself - that duplicates "
        "aria_cli.bridge._forward_to_server (R-F2400) and doubles every note")


def test_agent_bridge_reports_the_result_rather_than_forwarding() -> None:
    """It must not CALL the forwarder either.

    `bridge.send()` already forwards, so invoking `forward_to_server` here would
    re-post the note - the same duplication, one layer up. The result is carried
    back on the message instead, and this file only prints it.
    """
    src = (ROOT / "scripts/agent_bridge.py").read_text(encoding="utf-8")
    assert '"_forward"' in src, (
        "agent_bridge does not read the carried forward result")
    assert not re.search(r"^\s*(?!#).*\bforward_to_server\s*\(", src, re.M), (
        "agent_bridge CALLS the forwarder - send() has already forwarded, so "
        "this would post the note twice")


def test_send_forwards_exactly_once(tmp_path, monkeypatch) -> None:
    """Drive the real send path and count POSTs."""
    calls = []
    monkeypatch.setattr(bridge, "_post_ingest",
                        lambda url, payload, headers: calls.append(url) or (200, "{}"),
                        raising=False)
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
    ab.main(["send", "a note"]) if False else bridge.send(
        tmp_path, frm="claude", to="aria", text="a note", kind="note")
    assert len(calls) == 1, f"expected ONE forward, got {len(calls)}"


# -- the reason survives (R-F4302's useful half) ----------------------------

def test_the_forwarder_reports_a_reason() -> None:
    ok, reason = bridge.forward_to_server(_msg())
    assert ok is False
    assert "ARIA_SERVICE_URL" in reason, "unconfigured must SAY so, not fail silently"


def test_aria_side_is_refused_with_a_reason() -> None:
    ok, reason = bridge.forward_to_server(_msg(frm="aria"))
    assert ok is False and "teacher" in reason.lower()


# -- R-F2399's bool contract must NOT break ---------------------------------

def test_the_legacy_bool_wrapper_still_returns_a_bool() -> None:
    """test_rf2399 asserts `is False` in three places. Breaking a passing test to
    suit a refactor is not a fix."""
    r = bridge._forward_to_server(_msg())
    assert r is False and isinstance(r, bool)


def test_the_wrapper_delegates_rather_than_duplicating() -> None:
    src = (ROOT / "aria_cli/bridge.py").read_text(encoding="utf-8")
    body = src[src.index("def _forward_to_server"):]
    body = body[:body.index("\ndef ", 5)] if "\ndef " in body[5:] else body
    assert "forward_to_server(" in body, (
        "_forward_to_server must delegate to the one implementation")
    assert "httpx" not in body, "_forward_to_server still posts on its own"


# -- .env belongs to the canonical forwarder --------------------------------

def test_the_canonical_forwarder_reads_dot_env(monkeypatch) -> None:
    """This - not a missing implementation - is why nothing ever forwarded."""
    monkeypatch.setattr(bridge, "_env_file_values",
                        lambda: {"ARIA_SERVICE_URL": "https://x.invalid",
                                 "ARIA_INTERNAL_TOKEN": "tok"}, raising=False)
    seen = {}
    monkeypatch.setattr(bridge, "_post_ingest",
                        lambda url, payload, headers: (seen.update(url=url, h=headers), (200, "{}"))[1],
                        raising=False)
    ok, reason = bridge.forward_to_server(_msg())
    assert ok is True, reason
    assert seen["url"].endswith("/api/aria/collab/ingest")
    assert seen["h"]["Authorization"] == "Bearer tok"


def test_the_environment_wins_over_dot_env(monkeypatch) -> None:
    """An explicit export must override a stale file."""
    monkeypatch.setattr(bridge, "_env_file_values",
                        lambda: {"ARIA_SERVICE_URL": "https://stale.invalid",
                                 "ARIA_INTERNAL_TOKEN": "old"}, raising=False)
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://fresh.invalid")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "new")
    seen = {}
    monkeypatch.setattr(bridge, "_post_ingest",
                        lambda url, payload, headers: (seen.update(url=url, h=headers), (200, "{}"))[1],
                        raising=False)
    bridge.forward_to_server(_msg())
    assert "fresh.invalid" in seen["url"]
    assert seen["h"]["Authorization"] == "Bearer new"
