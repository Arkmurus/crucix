"""Capability test: /teach with a bare URL must NOT store a fake fact.

R-F1526: when store_fact receives content that is just a URL string
(no actual extracted text), it must reject it with action="rejected_no_content".
This prevents the "✅ Learned!" problem where the LLM stores a URL
as a fact when page extraction failed silently.
"""
import pytest
from aria_service.intel.knowledge import store_fact


@pytest.mark.asyncio
class TestTeachContentVerification:

    async def test_rejects_bare_url_as_content(self):
        """A bare URL as content must be rejected."""
        result = await store_fact(
            topic="https://uaelegislation.gov.ae/en/legislations/1610",
            content="https://uaelegislation.gov.ae/en/legislations/1610",
            source="user",
            confidence="CONFIRMED",
        )
        assert result.get("action") == "rejected_no_content", (
            f"Bare URL content should be rejected, got: {result}"
        )

    async def test_rejects_short_content(self):
        """Very short content (<50 chars) without source_url must be rejected."""
        result = await store_fact(
            topic="UAE Law",
            content="Some law",
            source="user",
            confidence="CONFIRMED",
        )
        assert result.get("action") == "rejected_no_content", (
            f"Short content should be rejected, got: {result}"
        )

    async def test_accepts_real_content(self):
        """Real extracted content must still be accepted."""
        result = await store_fact(
            topic="UAE Commercial Register",
            content="Federal Decree by Law Concerning The Commercial Register. "
                     "Registration requires submission of trade name, legal form, "
                     "and type of activity. The competent local authority shall "
                     "create a Commercial Register to record names of persons "
                     "subject to the Decree.",
            source="user",
            confidence="CONFIRMED",
        )
        assert result.get("action") in ("created", "updated", "superseded"), (
            f"Real content should be accepted, got: {result}"
        )

    async def test_accepts_short_content_with_source_url(self):
        """Short content with a source_url (verified pipeline) must be accepted."""
        result = await store_fact(
            topic="CEO: John Doe",
            content="John Doe is CEO of Acme Corp",
            source="verified_intel",
            confidence="CONFIRMED",
            source_url="https://example.com/company",
            fact_type="GENERAL_CLAIM",
            entity_name="Acme Corp",
        )
        # This goes through the verified_intel pipeline which may fail
        # if the verification engine is not configured, but it should NOT
        # be rejected by the content guard.
        assert result.get("action") != "rejected_no_content", (
            f"Short content with source_url should not be rejected by guard, got: {result}"
        )

    async def test_rejects_empty_content(self):
        """Empty content must be rejected."""
        result = await store_fact(
            topic="Empty Test",
            content="",
            source="user",
            confidence="CONFIRMED",
        )
        assert result.get("action") == "rejected_no_content", (
            f"Empty content should be rejected, got: {result}"
        )
