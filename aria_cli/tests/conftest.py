"""R-F2810 — network guard for the aria_cli test suite.

WHY THIS EXISTS
───────────────
The aria_cli suite could not be run as a SINGLE pytest invocation: it hung
indefinitely inside `ssl.recv` — a test making a real HTTPS call to a host that
never answered. Every file passed when run on its own, so the failure only
appeared in the combined run, and the practical effect was that nobody could get
one authoritative pass/fail number for the suite. Work got "verified" file by
file instead, which is exactly the kind of partial evidence CLAUDE.md §23 warns
about.

This is the same guard the Node suite already has (`test/helpers/net_guard.mjs`,
R-F2731), added for the same reason plus a sharper one: several tests there were
found silently hitting LIVE services, including one that POSTed real signals to
the production brain. A test suite that can reach production is a test suite that
can corrupt it.

The guard works at the SOCKET layer rather than by patching `httpx`/`requests`/
`urllib` individually, so it covers every client library uniformly — including
whichever one a future test reaches for.

LOOPBACK IS ALLOWED. Some tests legitimately boot a local server and drive it
over the wire (the R-F2739 pattern on the Node side). 127.0.0.1/::1 stays open;
everything else raises immediately with a message naming the destination, so an
un-mocked test fails LOUDLY and fast instead of hanging.

DELIBERATE INTEGRATION TESTS opt in:

    def test_something_real(allow_real_network):
        ...
"""
from __future__ import annotations

import socket

import pytest

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _is_loopback(address) -> bool:
    try:
        host = address[0] if isinstance(address, (tuple, list)) else address
    except Exception:
        return False
    if not isinstance(host, str):
        return False
    return host in _LOOPBACK or host.startswith("127.")


class NetworkBlockedInTests(RuntimeError):
    """Raised when a test tries to reach a non-loopback host."""


def _blocked(address):
    return NetworkBlockedInTests(
        f"[R-F2810] live network blocked in tests: connect({address!r}). "
        "Mock the client in this test, or request the `allow_real_network` "
        "fixture for a deliberate integration test."
    )


def _guarded_connect(self, address):
    if not _is_loopback(address):
        raise _blocked(address)
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    if not _is_loopback(address):
        raise _blocked(address)
    return _REAL_CONNECT_EX(self, address)


@pytest.fixture(autouse=True)
def _block_live_network(request, monkeypatch):
    """Block non-loopback sockets for every test unless it opts out."""
    if "allow_real_network" in request.fixturenames:
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex, raising=True)


@pytest.fixture
def allow_real_network():
    """Opt out of the guard for a genuine integration test.

    Requesting this fixture is a deliberate, greppable decision — it should be
    rare, and it should never appear in a test that runs in CI by default.
    """
    return True
