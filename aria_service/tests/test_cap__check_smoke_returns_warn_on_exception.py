"""R-F1626: _check_smoke returns WARN (not FAIL) when is_available() raises.

Before R-F1626, an exception from is_available() (e.g. upstream feed
unreachable) produced FAIL, which on critical modules like ofac_sdn and
fcdo_sanctions turned the diagnostic RED — masking real internal failures.
After R-F1626, upstream unreachable is WARN because the module itself is
structurally sound and serves cached data or gracefully degrades.
"""
import pytest


class _ModuleWithRaisingAvailable:
    """Fake module whose is_available() raises an exception."""
    @staticmethod
    async def is_available():
        raise ConnectionError("upstream feed timeout after 10s")


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
async def test_check_smoke_returns_warn_on_exception():
    """R-F1626: exception from is_available() → WARN, not FAIL."""
    from aria_service.intel.self_diagnostic import _check_smoke
    status, note = await _check_smoke(_ModuleWithRaisingAvailable())
    assert status == "WARN", f"Expected WARN, got {status}: {note}"
    assert "upstream unreachable" in note


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
