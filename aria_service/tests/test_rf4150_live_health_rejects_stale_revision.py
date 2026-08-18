"""R-F4150 capability tests for revision-strict live health verification."""
from unittest.mock import patch

import httpx
import pytest

from scripts.live_health_check import APPS, check_app_health


@pytest.mark.parametrize(
    ("app_name", "data"),
    [
        ("web", {"status": "ok", "build_rev": "sha 11111111"}),
        ("wa", {"status": "connected", "build_rev": "sha 11111111"}),
    ],
)
def test_alive_service_with_stale_revision_fails(app_name: str, data: dict) -> None:
    with patch("scripts.live_health_check.fetch_json", return_value=data):
        with httpx.Client() as client:
            result = check_app_health(client, app_name, APPS[app_name], "22222222")

    assert result is False


def test_web_uses_revision_bearing_health_endpoint() -> None:
    assert APPS["web"]["health_endpoint"] == "/api/health"
