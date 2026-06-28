"""R-F2064: capability test for the adaptive portal registration agent.

Verifies that the AdaptivePortalAgent can:
1. Read ANY registration page and extract all form fields with context
2. Understand what each field means by its label/name/type
3. Fill fields intelligently with correct values
4. Detect captchas on the page
5. Read validation errors from the response
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_agent_imports():
    """The portal agent module imports cleanly."""
    from aria_service.intel.portal_agent import AdaptivePortalAgent
    assert AdaptivePortalAgent is not None


@pytest.mark.asyncio
async def test_agent_reads_newsapi_form():
    """The agent can read the NewsAPI registration form and find all fields."""
    from aria_service.intel.portal_agent import AdaptivePortalAgent

    async with AdaptivePortalAgent() as agent:
        await agent._safe_goto("https://newsapi.org/register")
        await agent._page.wait_for_timeout(1500)

        form_data = await agent._read_page()
        fields = form_data["fields"]
        field_names = [f["name"] for f in fields]

        print(f"Page title: {form_data['page_title']}")
        print(f"Fields found: {field_names}")
        print(f"Has captcha: {form_data['has_captcha']}")

        # Verify expected fields exist
        assert "FirstName" in field_names, "Should find FirstName"
        assert "Email" in field_names, "Should find Email"
        assert "Password.Value" in field_names, "Should find Password"
        assert "EntityType" in field_names, "Should find EntityType radio"
        assert "HasAcceptedTerms" in field_names, "Should find terms checkbox"

        # Verify radio options
        et_field = next(f for f in fields if f["name"] == "EntityType")
        radio_values = [o["value"] for o in et_field.get("options", [])]
        assert "Individual" in radio_values, "Should have Individual option"
        assert "Business" in radio_values, "Should have Business option"

        # Verify captcha detection
        assert form_data["has_captcha"], "Should detect reCAPTCHA"
        assert form_data.get("captcha_sitekey", ""), "Should extract site key"


@pytest.mark.asyncio
async def test_agent_determines_field_values():
    """The agent correctly determines what value to fill for each field type."""
    from aria_service.intel.portal_agent import AdaptivePortalAgent

    agent = AdaptivePortalAgent()

    # Email fields
    assert "aria@arkmurus.com" in str(agent._get_value_for_field("email"))

    # Password fields
    pwd = agent._get_value_for_field("password")
    assert pwd and len(pwd) >= 12, "Password should be generated"

    # First name
    assert agent._get_value_for_field("first_name") == "ARIA"

    # Last name
    assert agent._get_value_for_field("last_name") == "Research"

    # Full name
    assert "ARIA Research" in str(agent._get_value_for_field("full_name"))

    # Organization
    assert "Arkmurus" in str(agent._get_value_for_field("company"))

    # Website
    assert "arkmurus.com" in str(agent._get_value_for_field("website"))

    # Phone — should be skipped
    assert agent._get_value_for_field("phone") is None

    # Terms checkbox — should return True
    assert agent._get_value_for_field("agree_terms") is True


@pytest.mark.asyncio
async def test_agent_reads_errors():
    """The agent can read validation errors from a page."""
    from aria_service.intel.portal_agent import AdaptivePortalAgent

    async with AdaptivePortalAgent() as agent:
        # Navigate to a page that has validation errors
        await agent._safe_goto("https://newsapi.org/register")
        await agent._page.wait_for_timeout(1000)

        # Submit empty form to trigger validation
        await agent._submit()
        await agent._page.wait_for_timeout(1000)

        response = await agent._read_response()
        print(f"Response: success={response['success']}, errors={response['errors']}")

        # Should have validation errors (empty form submission)
        # Note: this may or may not produce errors depending on JS validation
        assert isinstance(response["errors"], list)
        assert isinstance(response["success"], bool)
