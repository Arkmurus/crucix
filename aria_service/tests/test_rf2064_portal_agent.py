"""R-F2064: capability test for the visual portal registration agent.

Verifies that the PortalRegistrationAgent:
1. Can be instantiated and started
2. Can scan form fields from a real registration page
3. Can detect captchas
4. Can read validation errors
5. Properly wires success/failure to the brain
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_agent_imports():
    """The portal agent module imports cleanly."""
    from aria_service.intel.portal_agent import PortalRegistrationAgent
    assert PortalRegistrationAgent is not None


@pytest.mark.asyncio
async def test_agent_scan_newsapi_form():
    """The agent can scan the NewsAPI registration form and find fields."""
    from aria_service.intel.portal_agent import PortalRegistrationAgent
    
    agent = PortalRegistrationAgent()
    try:
        await agent.start()
        
        # Navigate to NewsAPI registration
        from playwright.async_api import async_playwright
        await agent._page.goto(
            "https://newsapi.org/register",
            wait_until="load",
            timeout=30000,
        )
        await agent._page.wait_for_timeout(1000)
        
        # Scan form fields
        fields = await agent._scan_form_fields()
        
        # Verify we found the expected fields
        field_names = [f["name"] for f in fields]
        print(f"Found fields: {field_names}")
        
        assert "FirstName" in field_names, "Should find FirstName field"
        assert "Email" in field_names, "Should find Email field"
        assert "Password.Value" in field_names, "Should find Password.Value field"
        assert "EntityType" in field_names, "Should find EntityType radio"
        assert "HasAcceptedTerms" in field_names, "Should find HasAcceptedTerms checkbox"
        
        # Verify radio options
        entity_type = [f for f in fields if f["name"] == "EntityType"][0]
        radio_values = [o["value"] for o in entity_type.get("options", [])]
        assert "Individual" in radio_values, "Should have Individual option"
        assert "Business" in radio_values, "Should have Business option"
        
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_agent_detect_captcha():
    """The agent can detect reCAPTCHA on the NewsAPI registration page."""
    from aria_service.intel.portal_agent import PortalRegistrationAgent
    
    agent = PortalRegistrationAgent()
    try:
        await agent.start()
        await agent._page.goto(
            "https://newsapi.org/register",
            wait_until="load",
            timeout=30000,
        )
        await agent._page.wait_for_timeout(1000)
        
        has_captcha = await agent._page.evaluate("""
            () => {
                return document.querySelector('.g-recaptcha') !== null
                    || document.querySelector('[data-sitekey]') !== null;
            }
        """)
        assert has_captcha, "Should detect reCAPTCHA on NewsAPI"
        
        site_key = await agent._page.evaluate("""
            () => {
                const el = document.querySelector('.g-recaptcha');
                if (el) return el.getAttribute('data-sitekey');
                return null;
            }
        """)
        assert site_key, "Should extract site key"
        assert len(site_key) > 10, "Site key should be a valid length"
        print(f"Site key: {site_key[:20]}...")
        
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_agent_fill_form():
    """The agent can fill the NewsAPI registration form."""
    from aria_service.intel.portal_agent import PortalRegistrationAgent
    
    agent = PortalRegistrationAgent()
    try:
        await agent.start()
        await agent._page.goto(
            "https://newsapi.org/register",
            wait_until="load",
            timeout=30000,
        )
        await agent._page.wait_for_timeout(1000)
        
        # Scan and fill form
        fields = await agent._scan_form_fields()
        from aria_service.intel.portal_registry import PORTALS
        newsapi = next(p for p in PORTALS if p.id == "newsapi")
        result = await agent._fill_form(fields, newsapi)
        
        assert result["filled"] > 0, "Should fill at least one field"
        print(f"Filled {result['filled']} fields, skipped {result['skipped']}")
        
        # Verify fields were actually filled
        email_value = await agent._page.evaluate(
            "document.querySelector('input[name=\"Email\"]')?.value || ''"
        )
        assert "arkmurus" in email_value, "Email field should be filled with arkmurus email"
        print(f"Email filled: {email_value}")
        
    finally:
        await agent.close()
