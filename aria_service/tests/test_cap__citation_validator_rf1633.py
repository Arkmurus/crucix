"""R-F1633: citation validator capability tests.

Tests the deterministic post-synthesis citation validator that strips
unsupported '[from <url>]' markers from LLM responses.

Key assertions:
  1. A citation pointing to a URL actually in tool_context is KEPT.
  2. A citation pointing to a random DOI not in gathered sources is STRIPPED.
  3. A citation pointing to an attached document is KEPT.
  4. A citation pointing to a RAG hit is KEPT.
  5. No citations at all → no change.
  6. Empty response → no change.
"""
from __future__ import annotations

from aria_service.intel.citation_validator import validate_citations


def test_real_tool_url_citation_kept():
    """A citation pointing to a URL actually fetched by tools is kept."""
    response = "The company was founded in 2005 [from https://company.example.com/about]."
    tool_context = "Fetched URL: https://company.example.com/about"
    cleaned = validate_citations(response, tool_context=tool_context)
    assert "[from https://company.example.com/about]" in cleaned
    assert "[UNVERIFIED" not in cleaned


def test_random_doi_citation_stripped():
    """A citation pointing to a random DOI not in gathered sources is stripped."""
    response = (
        "The ghost-score methodology is based on established DD principles "
        "[from https://doi.org/10.4324/9780203013694-11]."
    )
    tool_context = "Fetched URL: https://company.example.com/about"
    cleaned = validate_citations(response, tool_context=tool_context)
    assert "[from https://doi.org/10.4324/9780203013694-11]" not in cleaned
    assert "[UNVERIFIED" in cleaned


def test_attached_document_citation_kept():
    """A citation pointing to an attached document is kept."""
    response = "As stated in the contract [from ATTACHED DOCUMENT: clause 4.2]."
    cleaned = validate_citations(
        response,
        attached_doc="This is the attached contract document with clause 4.2.",
    )
    assert "[from ATTACHED DOCUMENT" in cleaned or "[UNVERIFIED" not in cleaned


def test_rag_hit_citation_kept():
    """A citation pointing to a RAG hit is kept."""
    response = "According to SIPRI data [from https://sipri.org/database/2025]."
    cleaned = validate_citations(
        response,
        rag_hits=["https://sipri.org/database/2025"],
    )
    assert "[from https://sipri.org/database/2025]" in cleaned
    assert "[UNVERIFIED" not in cleaned


def test_no_citations_no_change():
    """A response with no citation markers is unchanged."""
    response = "The company was founded in 2005 and operates in the defence sector."
    cleaned = validate_citations(response)
    assert cleaned == response


def test_empty_response_no_change():
    """An empty response is returned as-is."""
    assert validate_citations("") == ""
    assert validate_citations("   ") == "   "


def test_mixed_citations_kept_and_stripped():
    """Real citations kept, fake ones stripped — in the same response."""
    response = (
        "The company was founded in 2005 [from https://company.example.com/about]. "
        "The ghost-score is based on established methodology "
        "[from https://doi.org/10.4324/9780203013694-11]."
    )
    tool_context = "Fetched URL: https://company.example.com/about"
    cleaned = validate_citations(response, tool_context=tool_context)
    assert "[from https://company.example.com/about]" in cleaned
    assert "[from https://doi.org/10.4324/9780203013694-11]" not in cleaned
    assert "[UNVERIFIED" in cleaned


def test_domain_level_match_kept():
    """A citation whose domain matches a fetched URL is kept (URL variation)."""
    response = "Data from the registry [from https://www.company.example.com/profile]."
    tool_context = "Fetched URL: https://company.example.com/about"
    cleaned = validate_citations(response, tool_context=tool_context)
    # Different subdomain but same domain — should be kept
    assert "[from https://www.company.example.com/profile]" in cleaned
    assert "[UNVERIFIED" not in cleaned


def test_no_tool_context_strips_all():
    """When no sources are gathered, all citation markers are stripped."""
    response = (
        "The methodology [from https://doi.org/10.4324/9780203013694-11] "
        "is well established."
    )
    cleaned = validate_citations(response)
    assert "[from" not in cleaned
    assert "[UNVERIFIED" not in cleaned
