"""R-F3433 — the live-network test guard, extracted and extended to cover DNS.

R-F3319 introduced this guard after FOUR suite blockers that were all one defect: a
unit test doing real network I/O, which presents as a HANG rather than a failure
(R-F2812, R-F3298, R-F3307, R-F3318). It hooked ``socket.connect``.

R-F3433 — measured, not assumed: with the guard ENABLED over the 140-file DD set,
the run made **25 live DNS lookups to 9 external hosts** (OFSI blob storage, OFAC,
UN, World Bank, SEC, Treasury, api.opensanctions.org, two cloud buckets) and **zero
connects**. The guard reported nothing, because ``getaddrinfo`` is not ``connect``.

That is the dangerous shape: enabling the guard to diagnose a hang returned a clean
run while live resolution still went out, so a clean run certified "no live network"
when the suite was still reaching the internet. A guard that covers part of a path
reads exactly like one that covers all of it.

DNS matters here specifically because ``getaddrinfo`` is a BLOCKING syscall with no
application-level timeout — the same reason R-F3318's block inside
``ssl.create_default_context()`` could not be saved by a request ``timeout=``. When a
resolver is slow or dropping (as happened twice on this machine on 2026-07-29), each
lookup can stall for the OS resolver timeout, and the calling thread is stuck.

Honest scope: extending coverage to DNS removes a real unbounded external dependency
and makes the diagnostic trustworthy. It is NOT proof that DNS caused the
intermittent hang — that would need a stack dump caught mid-hang, which has not been
obtained. Do not let a later reader upgrade this into a root-cause claim.

Still OFF BY DEFAULT (see conftest): enabling it globally would change the outcome of
any test that reaches the network, and the full-suite baseline was measured without
it. Blast radius when enabled, measured over the DD set: exactly one test, fixed by
R-F3433 so the diagnostic now runs clean there.
"""
from __future__ import annotations

import ipaddress
import socket

_LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")

_state: dict = {}


class LiveNetworkBlocked(RuntimeError):
    """An un-mocked outbound network operation was attempted from a test."""


def _host_of(addr) -> str:
    try:
        host = addr[0] if isinstance(addr, tuple) else addr
    except Exception:
        return ""
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii", "replace")
        except Exception:
            return ""
    return str(host)


def _is_local(addr) -> bool:
    return _host_of(addr) in _LOCAL_HOSTS


def is_ip_literal(host: str) -> bool:
    """True if ``host`` is already an IP address.

    Resolving a literal needs NO network — and ``url_safety.is_safe_url`` legitimately
    resolves literals to classify them as private/public. Blocking that would break
    correct SSRF-guard code and prove nothing about egress, so literals stay allowed
    even when the guard is on.
    """
    try:
        ipaddress.ip_address(str(host).strip("[]"))
        return True
    except ValueError:
        return False


def install() -> None:
    """Patch socket so live network use raises a NAMED error instead of hanging."""
    if _state:
        return
    _state["connect"] = socket.socket.connect
    _state["connect_ex"] = socket.socket.connect_ex
    _state["getaddrinfo"] = socket.getaddrinfo

    real_connect = _state["connect"]
    real_connect_ex = _state["connect_ex"]
    real_getaddrinfo = _state["getaddrinfo"]

    def _blocked_connect(self, addr, *a, **kw):
        if _is_local(addr):
            return real_connect(self, addr, *a, **kw)
        raise LiveNetworkBlocked(
            f"[R-F3319] a test attempted a LIVE outbound connection to {addr!r}. "
            "Unit tests must not do real network I/O: it hangs the run rather "
            "than failing it. Stub the call at its boundary "
            "(see R-F3298 and R-F3318 for the pattern)."
        )

    def _blocked_connect_ex(self, addr, *a, **kw):
        if _is_local(addr):
            return real_connect_ex(self, addr, *a, **kw)
        raise LiveNetworkBlocked(
            f"[R-F3319] a test attempted a LIVE outbound connection to {addr!r}."
        )

    def _blocked_getaddrinfo(host, port, *a, **kw):
        h = _host_of(host)
        if h in _LOCAL_HOSTS or is_ip_literal(h):
            return real_getaddrinfo(host, port, *a, **kw)
        raise LiveNetworkBlocked(
            f"[R-F3433] a test attempted LIVE DNS resolution of {h!r}. "
            "getaddrinfo is a BLOCKING syscall with no application timeout, so a "
            "slow or dropping resolver stalls the calling thread and the run looks "
            "hung rather than failed. Stub the call at its boundary."
        )

    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex
    socket.getaddrinfo = _blocked_getaddrinfo


def uninstall() -> None:
    """Restore the real socket functions (used by the guard's own tests)."""
    if not _state:
        return
    socket.socket.connect = _state.pop("connect")
    socket.socket.connect_ex = _state.pop("connect_ex")
    socket.getaddrinfo = _state.pop("getaddrinfo")
    _state.clear()


def is_installed() -> bool:
    return bool(_state)
