"""R-F1134 — Capability tests for provenance watermarking.

Tests that:
1. Watermark is correctly applied to content
2. Watermark is extractable from content
3. Watermark is tamper-evident (hash mismatch detected)
4. Provenance chain is traceable
5. Watermark can be stripped before LLM presentation
6. Injection source tracing works
7. Brain wiring works
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from aria_service.intel.provenance_watermark import (
    WATERMARK_HEADER,
    WATERMARK_FOOTER,
    Watermark,
    _compute_content_hash,
    apply_watermark,
    extract_watermark,
    report_injection_source,
    strip_watermark,
    trace_provenance,
    verify_integrity,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_content() -> bytes:
    return b"This is a sample document about defence procurement."


@pytest.fixture
def sample_url() -> str:
    return "https://www.defensenews.com/article123"


# ── Tests for watermark application ─────────────────────────────────────────

class TestApplyWatermark:
    """Proves watermark application works."""

    async def test_applies_watermark(self, sample_content, sample_url):
        """Watermark is correctly applied to content."""
        wm = await apply_watermark(
            content=sample_content,
            source_url=sample_url,
            source_type="web_fetch",
            source_tier="2",
        )

        assert wm.source_url == sample_url
        assert wm.source_type == "web_fetch"
        assert wm.source_tier == "2"
        assert wm.content_hash.startswith("sha256:")
        assert wm.passed_scan is True
        assert wm.fetched_at is not None

    async def test_content_hash_matches(self, sample_content, sample_url):
        """Content hash matches the actual content."""
        wm = await apply_watermark(
            content=sample_content,
            source_url=sample_url,
        )

        expected_hash = _compute_content_hash(sample_content)
        assert wm.content_hash == expected_hash

    async def test_different_content_different_hash(self, sample_url):
        """Different content produces different hashes."""
        wm1 = await apply_watermark(content=b"content A", source_url=sample_url)
        wm2 = await apply_watermark(content=b"content B", source_url=sample_url)

        assert wm1.content_hash != wm2.content_hash

    async def test_watermark_to_block(self, sample_content, sample_url):
        """Watermark renders to a block string."""
        wm = await apply_watermark(
            content=sample_content,
            source_url=sample_url,
        )

        block = wm.to_block()
        assert WATERMARK_HEADER in block
        assert WATERMARK_FOOTER in block
        assert sample_url in block

    async def test_watermark_from_block(self, sample_content, sample_url):
        """Watermark can be reconstructed from a block."""
        wm = await apply_watermark(
            content=sample_content,
            source_url=sample_url,
        )

        block = wm.to_block()
        reconstructed = Watermark.from_block(
            block[len(WATERMARK_HEADER) + 1:-len(WATERMARK_FOOTER) - 1]
        )

        assert reconstructed is not None
        assert reconstructed.source_url == sample_url
        assert reconstructed.content_hash == wm.content_hash

    async def test_wires_to_brain(self, sample_content, sample_url):
        """Watermark application wires to brain."""
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws:
            wm = await apply_watermark(
                content=sample_content,
                source_url=sample_url,
            )

        mock_ws.assert_called_once()
        args, kwargs = mock_ws.call_args
        assert kwargs.get("module") == "provenance_watermark"


# ── Tests for watermark extraction ──────────────────────────────────────────

class TestExtractWatermark:
    """Proves watermark extraction works."""

    async def test_extracts_from_content(self, sample_content, sample_url):
        """Watermark is extractable from content that contains it."""
        wm = await apply_watermark(content=sample_content, source_url=sample_url)
        block = wm.to_block()

        # Simulate content with watermark embedded
        content_with_wm = f"{block}\n\n{strip_watermark(block)}"

        extracted = extract_watermark(content_with_wm)
        assert extracted is not None
        assert extracted.source_url == sample_url

    def test_returns_none_for_clean_content(self):
        """Content without watermark returns None."""
        extracted = extract_watermark("Clean content without watermark")
        assert extracted is None


# ── Tests for tamper evidence ───────────────────────────────────────────────

class TestTamperEvidence:
    """Proves watermarks are tamper-evident."""

    async def test_detects_tampered_content(self, sample_content, sample_url):
        """Modified content is detected as tampered."""
        wm = await apply_watermark(content=sample_content, source_url=sample_url)

        # Content was modified after watermark was applied
        tampered = b"MODIFIED " + sample_content
        assert verify_integrity(tampered, wm) is False

    async def test_passes_unchanged_content(self, sample_content, sample_url):
        """Unchanged content passes integrity check."""
        wm = await apply_watermark(content=sample_content, source_url=sample_url)
        assert verify_integrity(sample_content, wm) is True


# ── Tests for provenance chain ──────────────────────────────────────────────

class TestProvenanceChain:
    """Proves provenance chain tracking works."""

    async def test_single_source_chain(self, sample_content, sample_url):
        """Single source has a chain of length 1."""
        wm = await apply_watermark(content=sample_content, source_url=sample_url)
        chain = trace_provenance(wm)

        assert len(chain) == 1
        assert chain[0]["source_url"] == sample_url

    async def test_parent_child_chain(self, sample_content, sample_url):
        """Child content inherits parent's provenance."""
        parent = await apply_watermark(
            content=b"Original source content",
            source_url="https://original.com/doc",
        )

        child = await apply_watermark(
            content=sample_content,
            source_url=sample_url,
            parent_watermark=parent,
        )

        chain = trace_provenance(child)
        assert len(chain) == 2
        assert chain[0]["source_url"] == sample_url  # Most recent first
        assert chain[1]["source_url"] == "https://original.com/doc"  # Original


# ── Tests for watermark stripping ───────────────────────────────────────────

class TestStripWatermark:
    """Proves watermark stripping works."""

    async def test_strips_watermark(self, sample_content, sample_url):
        """Watermark block is removed from content."""
        wm = await apply_watermark(content=sample_content, source_url=sample_url)
        content_with_wm = f"{wm.to_block()}\n\nActual analysis text here"

        stripped = strip_watermark(content_with_wm)
        assert WATERMARK_HEADER not in stripped
        assert WATERMARK_FOOTER not in stripped
        assert "Actual analysis text here" in stripped

    def test_clean_content_unchanged(self):
        """Content without watermark is unchanged."""
        content = "Clean content without any watermark"
        stripped = strip_watermark(content)
        assert stripped == content


# ── Tests for injection source tracing ──────────────────────────────────────

class TestInjectionSourceTracing:
    """Proves injection source tracing works."""

    async def test_reports_injection_source(self, sample_content, sample_url):
        """Injection source is reported to the brain."""
        wm = await apply_watermark(content=sample_content, source_url=sample_url)

        with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
            await report_injection_source(wm, injection_type="prompt_injection")

        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert kwargs.get("module") == "provenance_watermark"
        assert kwargs.get("gap_type") == "security_threat"
        assert sample_url in kwargs.get("detail", "")
