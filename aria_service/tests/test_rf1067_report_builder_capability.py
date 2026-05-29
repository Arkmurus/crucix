"""R-F1067 — Capability test for report_builder.build_report() fallback path.

Verifies that build_report does NOT crash with KeyError when no tier-D
template is found (the fallback skeleton path).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestReportBuilderCapability:
    """Capability test: build_report must not crash on fallback skeleton."""

    @pytest.mark.asyncio
    async def test_build_report_fallback_skeleton_no_keyerror(self) -> None:
        """build_report must not raise KeyError when no tier-D template found.

        The fallback skeleton has ~40 placeholders ({client}, {ref}, etc.)
        that were causing .format() to raise KeyError. The raw skeleton
        should be passed to the LLM fill step instead.
        """
        from aria_service.intel.report_builder import build_report

        # Mock _retrieve_tier_d_template to return nothing (trigger fallback)
        mock_retrieve = AsyncMock(return_value=("", 0))

        # Mock LLM provider
        mock_llm = MagicMock()
        mock_llm.is_configured = True
        mock_llm.complete = AsyncMock(return_value=MagicMock(
            content="Test report content with all sections filled in.",
        ))

        with patch("aria_service.intel.report_builder._retrieve_tier_d_template", mock_retrieve):
            # This should NOT raise KeyError
            try:
                result = await build_report(
                    llm=mock_llm,
                    report_type="dd",
                    subject="Test Company Ltd",
                    extra_context="Test investigation data",
                )
                # Should return a dict with response
                assert isinstance(result, dict)
                assert "draft" in result or "error" in result
            except KeyError as e:
                pytest.fail(f"build_report raised KeyError on fallback skeleton: {e}")
