"""R-F1626/R-F1627: _check_smoke exception handling.

R-F1626: network-level exceptions (ConnectionError, TimeoutError, etc.)
from is_available() → WARN (upstream unreachable, module structurally
sound). Before R-F1626 these were FAIL, turning the diagnostic RED on
critical modules like ofac_sdn/fcdo_sanctions and masking real failures.

R-F1627: code-bug exceptions (TypeError, AttributeError, etc.) must
still be FAIL — a genuine internal bug in is_available() must not hide
behind 'upstream unreachable'.
"""
import pytest


class _ModuleWithNetworkError:
    """Fake module whose is_available() raises a network-level error."""
    @staticmethod
    async def is_available():
        raise ConnectionError("upstream feed timeout after 10s")


class _ModuleWithCodeBug:
    """Fake module whose is_available() raises a code bug (TypeError)."""
    @staticmethod
    async def is_available():
        raise TypeError("'NoneType' object is not subscriptable")


class _ModuleWithFalseAvailable:
    """Fake module whose is_available() returns False (upstream unreachable)."""
    @staticmethod
    async def is_available():
        return False


class _ModuleWithTrueAvailable:
    """Fake module whose is_available() returns True (upstream reachable)."""
    @staticmethod
    async def is_available():
        return True


class _ModuleWithoutAvailable:
    """Fake module without is_available()."""
    pass


@pytest.mark.asyncio
async def test_check_smoke_network_error_is_warn():
    """R-F1626: network-level exception → WARN, not FAIL."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(_ModuleWithNetworkError())
    assert status == "WARN", f"Expected WARN, got {status}: {note}"
    assert "upstream unreachable" in note


@pytest.mark.asyncio
async def test_check_smoke_code_bug_is_fail():
    """R-F1627: code-bug exception (TypeError) → FAIL, not WARN.
    A genuine internal bug in is_available() must not hide behind
    'upstream unreachable'."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(_ModuleWithCodeBug())
    assert status == "FAIL", f"Expected FAIL, got {status}: {note}"
    assert "internal bug" in note


@pytest.mark.asyncio
async def test_check_smoke_returns_warn_on_false():
    """is_available() returning False → WARN (unchanged behaviour)."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(_ModuleWithFalseAvailable())
    assert status == "WARN", f"Expected WARN, got {status}: {note}"
    assert "upstream unreachable" in note


@pytest.mark.asyncio
async def test_check_smoke_returns_pass_on_true():
    """is_available() returning True → PASS (unchanged behaviour)."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(_ModuleWithTrueAvailable())
    assert status == "PASS", f"Expected PASS, got {status}: {note}"
    assert "upstream reachable" in note


@pytest.mark.asyncio
async def test_check_smoke_returns_warn_on_missing():
    """No is_available() → WARN (unchanged behaviour)."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(_ModuleWithoutAvailable())
    assert status == "WARN", f"Expected WARN, got {status}: {note}"
    assert "no is_available() exposed" in note


@pytest.mark.asyncio
async def test_check_smoke_returns_warn_on_none():
    """None module → WARN (unchanged behaviour)."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(None)
    assert status == "WARN", f"Expected WARN, got {status}: {note}"
    assert "no is_available() exposed" in note
