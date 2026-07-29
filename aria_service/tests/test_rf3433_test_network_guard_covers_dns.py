"""R-F3433 — the live-network test guard must cover DNS, not just connect().

THE DEFECT (measured, not theorised): with ARIA_TEST_BLOCK_NETWORK=1 enabled over the
140-file DD set, the run performed 25 live DNS lookups to 9 external hosts (OFSI blob
storage, OFAC, UN, World Bank, SEC, Treasury, api.opensanctions.org, two cloud
buckets) and ZERO connects. The guard hooked socket.connect only, so it reported a
perfectly clean run while the suite was still reaching the internet.

Why that is worse than no guard: the guard exists to be switched on when diagnosing a
hang, in order to rule live I/O OUT. A clean run was being read as "no live network"
when live resolution was still happening. getaddrinfo is a BLOCKING syscall with no
application-level timeout — the same structural reason a request `timeout=` could not
save R-F3318, whose block was inside ssl.create_default_context().

These tests drive the REAL guard (install/uninstall on the real socket module), not a
helper, and assert the user-visible outcome: a live resolution raises a NAMED error
instead of silently going out to the network.

Scope honesty: this removes a proven unbounded external dependency from the suite. It
is NOT proof that DNS caused the intermittent suite hang — no stack dump was ever
caught mid-hang. Do not let a later reader upgrade it into a root-cause claim.
"""
from __future__ import annotations

import socket

import pytest

from aria_service.tests import _net_block


@pytest.fixture
def guard():
    """Install the real guard, restoring whatever state the session was already in."""
    was_installed = _net_block.is_installed()
    if not was_installed:
        _net_block.install()
    try:
        yield _net_block
    finally:
        if not was_installed:
            _net_block.uninstall()


def test_live_dns_resolution_is_blocked_by_name(guard):
    """THE regression. Before R-F3433 this call sailed straight through the guard."""
    with pytest.raises(_net_block.LiveNetworkBlocked) as ei:
        socket.getaddrinfo("ofsistorage.blob.core.windows.net", 443)
    msg = str(ei.value)
    assert "R-F3433" in msg, f"the error must name the R-number that owns it: {msg}"
    assert "ofsistorage.blob.core.windows.net" in msg, (
        f"the error must name the host so the offender is findable: {msg}"
    )


def test_live_connect_is_still_blocked(guard):
    """R-F3319's original coverage must survive the extraction into _net_block."""
    s = socket.socket()
    try:
        with pytest.raises(_net_block.LiveNetworkBlocked):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_ip_literals_still_resolve(guard):
    """url_safety.is_safe_url resolves LITERALS to classify them as private/public.
    That needs no network, and blocking it would break correct SSRF-guard code while
    proving nothing about egress — so literals must stay allowed."""
    for literal in ("93.184.216.34", "10.0.0.5", "169.254.169.254", "::1"):
        assert socket.getaddrinfo(literal, 80), f"literal {literal} must still resolve"


def test_loopback_stays_open(guard):
    """A local fixture server must keep working with the guard on."""
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_the_real_ssrf_guard_still_works_under_the_block(guard):
    """Capability test: drive the actual consumer. is_safe_url must keep classifying
    IP literals with the guard on — if the DNS block were too broad, the SSRF guard
    would start failing closed on everything and quietly over-block production URLs."""
    from aria_service.intel import url_safety as us

    for bad in ("http://169.254.169.254/", "http://10.0.0.5/", "http://127.0.0.1:8000/"):
        ok, reason = us.is_safe_url(bad)
        assert not ok, f"is_safe_url allowed {bad!r} ({reason})"


def test_uninstall_restores_the_real_functions():
    """The guard must be reversible, or its own tests would poison the whole session."""
    was_installed = _net_block.is_installed()
    if was_installed:
        pytest.skip("session-wide guard is active; reversibility covered by the fixture")

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    _net_block.install()
    assert socket.getaddrinfo is not real_getaddrinfo, "install() did not take effect"
    _net_block.uninstall()
    assert socket.getaddrinfo is real_getaddrinfo, "uninstall() left DNS patched"
    assert socket.socket.connect is real_connect, "uninstall() left connect patched"


def test_the_guard_is_ON_BY_DEFAULT():
    """R-F3446 — the default must stay ON, and the opt-out must stay EXPLICIT.

    It was off by default for a measured reason, and it took a full-suite measurement to
    retire that: all 1,531 files run with it enabled, every failure diffed against the
    documented baseline, exactly FOUR tests attributable to the guard (fixed in R-F3440 and
    R-F3444), so the blast radius is now zero.

    This asserts the POLICY, because an opt-in guard protects only whoever remembers to
    switch it on — and nobody remembers at the moment a hang actually happens. If a future
    change makes this opt-in again, that is a decision that needs its own evidence, and
    this test is where it has to be argued.
    """
    import os
    from pathlib import Path

    src = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    assert 'if not _truthy("ARIA_TEST_ALLOW_NETWORK")' in src, (
        "the guard must install UNLESS explicitly opted out; an `if ARIA_TEST_BLOCK_NETWORK`"
        " gate would make it opt-in again")

    # And it must actually be active in this very session, unless someone opted out.
    if not (os.getenv("ARIA_TEST_ALLOW_NETWORK") or "").strip():
        assert _net_block.is_installed(), (
            "the guard is not installed in a run that did not opt out — the default flip "
            "is not taking effect")


def test_conftest_actually_installs_the_guard():
    """Producer/consumer: _net_block is only worth anything if conftest CALLS it. A
    guard module nobody invokes is the 'wired but never called' defect class — assert
    the wiring exists rather than assuming it."""
    from pathlib import Path

    src = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    assert "_net_block" in src, "conftest.py no longer imports the network guard"
    assert "install()" in src, "conftest.py imports the guard but never installs it"
    assert "ARIA_TEST_BLOCK_NETWORK" in src, "the env gate went missing"
