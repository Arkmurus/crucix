"""R-F4194 — the sanctions coverage wire is deterministic across test order."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import sanctions
from aria_service.intel.sanctions import _SourceQuery


def _down(*_args, **_kwargs) -> _SourceQuery:
    return _SourceQuery([], False, "auth")


@pytest.mark.asyncio
async def test_rf4194_degraded_coverage_announces_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive two degraded screens and prove the first—and only first—is wired."""
    monkeypatch.setattr(sanctions, "_COVERAGE_DEGRADED_ANNOUNCED", False)
    with patch.object(
        sanctions,
        "_opensanctions_match",
        AsyncMock(side_effect=_down),
    ), patch.object(
        sanctions,
        "_opensanctions_search",
        AsyncMock(side_effect=_down),
    ), patch(
        "aria_service.intel.sanctions_canonical.lookup.check_sanctions",
        return_value={"verdict": "CLEAR", "matches": []},
    ), patch.object(sanctions, "wire_failure", MagicMock()) as wire_failure:
        first = await sanctions.fuzzy_screen("Vladimir Testovich Putin")
        second = await sanctions.fuzzy_screen("Vladimir Testovich Putin")

    assert first["coverage"]["mode"] == "local_canonical_floor"
    assert second["coverage"]["mode"] == "local_canonical_floor"
    wire_failure.assert_called_once()
