"""R-F751 (2026-05-20) — per-phase connect_timeout on sources/_common httpx calls.

Wedge round 10 (wedge_675_1779301744.log) captured a 6.23s main-loop
stall with the main thread parked at:
  ssl.wrap_bio (Python ssl module, sync code)
  → anyio TLS stream wrap
  → httpcore connection establish
  → _common.http_get_text
  → un_sc_sanctions.is_available
  → self_diagnostic._check_smoke

Root cause: httpx.AsyncClient was constructed with a SCALAR timeout
which httpx applies as the read timeout but leaves the connect /
SSL-handshake phase using the LIBRARY default. When a probe upstream
(scsanctions.un.org in this case) has slow DNS or SSL setup, the
sync ssl.wrap_bio call leaks the full wait onto the event loop.

R-F751: switch the `_common.http_get_text` / `_common.http_get_json`
helpers to construct an `httpx.Timeout(total, connect=3.0)` —
explicit per-phase. The connect/SSL phase is capped at 3s regardless
of the read timeout. Real outages still surface as a timeout
exception within budget; the main-loop stall is bounded.

Tests:
  - _DEFAULT_CONNECT_TIMEOUT_S is the expected 3.0s ceiling
  - _make_httpx_timeout returns an httpx.Timeout with connect=3.0
  - both http_get_text and http_get_json wire the per-phase timeout
  - existing callers' `timeout=` kwarg semantics preserved (now =
    `read` budget)
"""
from __future__ import annotations

import inspect

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_default_connect_timeout_is_3s():
    """R-F751: connect-phase ceiling = 3.0 seconds."""
    from aria_service.intel.sources import _common
    assert _common._DEFAULT_CONNECT_TIMEOUT_S == 3.0


def test_make_httpx_timeout_caps_connect():
    """R-F751: helper returns a per-phase Timeout with connect bounded."""
    from aria_service.intel.sources import _common
    t = _common._make_httpx_timeout(12.0)
    # httpx.Timeout exposes connect via .connect attribute
    assert t.connect == 3.0
    # Other phases should default to the `total` budget
    # (httpx.Timeout(total, connect=X) sets read/write/pool to total)
    assert t.read == 12.0


def test_http_get_text_uses_make_httpx_timeout():
    """R-F751: http_get_text source wires the per-phase Timeout."""
    from aria_service.intel.sources import _common
    src = function_source(_common, "http_get_text")
    assert "_make_httpx_timeout" in src
    assert "R-F751" in src


def test_http_get_json_uses_make_httpx_timeout():
    """R-F751: http_get_json source wires the per-phase Timeout."""
    from aria_service.intel.sources import _common
    src = function_source(_common, "http_get_json")
    assert "_make_httpx_timeout" in src
    assert "R-F751" in src


def test_scalar_timeout_arg_still_accepted():
    """R-F751: the existing `timeout=` kwarg contract is preserved —
    callers pass a scalar float and we translate it to per-phase
    internally."""
    from aria_service.intel.sources import _common
    sig_text = inspect.signature(_common.http_get_text)
    sig_json = inspect.signature(_common.http_get_json)
    # Both keep `timeout: float = _DEFAULT_TIMEOUT_S` as a kwarg
    assert "timeout" in sig_text.parameters
    assert sig_text.parameters["timeout"].default == _common._DEFAULT_TIMEOUT_S
    assert "timeout" in sig_json.parameters
    assert sig_json.parameters["timeout"].default == _common._DEFAULT_TIMEOUT_S


def test_make_httpx_timeout_with_varied_total():
    """R-F751: the connect cap stays at 3s regardless of the `total`
    budget — a slow read (30s timeout) shouldn't extend connect window."""
    from aria_service.intel.sources import _common
    for total in (5.0, 12.0, 30.0):
        t = _common._make_httpx_timeout(total)
        assert t.connect == 3.0
        assert t.read == total
