"""R-F2600 source activation coverage."""

from aria_service.intel.portal_registry import PORTALS


def test_trade_gov_csl_portal_is_registered_for_key_onboarding() -> None:
    portal = next((p for p in PORTALS if p.id == "trade_gov"), None)
    assert portal is not None
    assert portal.registration_type == "api_key"
    assert "Consolidated Screening List" in portal.description
    assert portal.url == "https://developer.trade.gov"
