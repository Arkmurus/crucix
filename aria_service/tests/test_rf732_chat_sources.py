"""R-F732 (2026-05-20) — regression tests for structured chat sources[].

Pre-R-F732 chat responses surfaced citations only as inline text:
`[from dd_orchestrate:abc123]` markers and bare URLs in the prose.
A consumer (frontend chips, footnote popover, source rail) had to
regex the response to extract citations.

R-F732 adds a `sources[]` field to the non-stream response JSON
AND emits a `sources` SSE event in the stream path. Both pull from
`aria_service.intel.chat_sources.extract(response_text, tool_context)`
which returns a deduplicated, typed list with shape:
  {type, label, url, tool, run_id}

These tests cover the extractor directly + confirm the wiring is
present in both chat paths.
"""
from __future__ import annotations

import inspect


def test_extract_tool_citation():
    """`[from <tool>:<run_id>]` → type=tool with tool + run_id captured."""
    from aria_service.intel import chat_sources as cs

    text = "Findings show no sanctions hits [from dd_orchestrate:abc12345]"
    out = cs.extract(text)
    assert len(out) == 1
    assert out[0]["type"] == "tool"
    assert out[0]["tool"] == "dd_orchestrate"
    assert out[0]["run_id"] == "abc12345"
    assert "dd_orchestrate" in out[0]["label"]


def test_extract_tool_citation_no_run_id():
    """`[from <tool>]` (no run_id) → type=tool with run_id=None."""
    from aria_service.intel import chat_sources as cs

    text = "Per our records [from companies_house] no filings since 2023."
    out = cs.extract(text)
    assert len(out) == 1
    assert out[0]["type"] == "tool"
    assert out[0]["tool"] == "companies_house"
    assert out[0]["run_id"] is None


def test_extract_bare_url():
    """Bare URL → type=url with host as label, full URL preserved."""
    from aria_service.intel import chat_sources as cs

    text = "See the OFAC SDN list at https://sanctionslistservice.ofac.treas.gov/sdn for details."
    out = cs.extract(text)
    urls = [s for s in out if s["type"] == "url"]
    assert len(urls) == 1
    assert urls[0]["url"].startswith("https://sanctionslistservice.ofac.treas.gov/sdn")
    # Label should be the host, not the full URL
    assert "ofac" in urls[0]["label"].lower()


def test_extract_url_strips_trailing_punctuation():
    """`https://example.org/path,` should drop the trailing comma."""
    from aria_service.intel import chat_sources as cs

    text = "Reference: https://www.gov.uk/government/news/sanctions-update."
    out = cs.extract(text)
    urls = [s for s in out if s["type"] == "url"]
    assert urls and not urls[0]["url"].endswith(".")


def test_extract_dedupes_repeated_citations():
    """Same `[from X:Y]` referenced twice → single entry."""
    from aria_service.intel import chat_sources as cs

    text = "[from dd_orchestrate:abc] more text [from dd_orchestrate:abc] yet more"
    out = cs.extract(text)
    assert len([s for s in out if s["type"] == "tool"]) == 1


def test_extract_primary_sanctions_block():
    """`[SANCTIONS LIVE CHECK ...]` → type=primary_source."""
    from aria_service.intel import chat_sources as cs

    text = "[SANCTIONS LIVE CHECK]\nEntity: Acme Corp\nNo matches found."
    out = cs.extract(text)
    ps = [s for s in out if s["type"] == "primary_source"]
    assert len(ps) == 1
    assert "Sanctions live check" in ps[0]["label"]


def test_extract_primary_sanctions_degraded():
    """Degraded variant `[SANCTIONS LIVE CHECK — DEGRADED]` also detected."""
    from aria_service.intel import chat_sources as cs

    text = "[SANCTIONS LIVE CHECK — DEGRADED]\nLive check failed."
    out = cs.extract(text)
    assert any(s["type"] == "primary_source" for s in out)


def test_extract_rag_citation():
    """`[source: <path>]` → type=rag."""
    from aria_service.intel import chat_sources as cs

    text = "Per the Companies House filing [source: docs/ch-2024.pdf], directors changed."
    out = cs.extract(text)
    rag = [s for s in out if s["type"] == "rag"]
    assert len(rag) == 1
    assert "docs/ch-2024.pdf" in rag[0]["label"]


def test_extract_rag_citation_with_url():
    """`[source: https://x.com/y]` → type=rag with url populated."""
    from aria_service.intel import chat_sources as cs

    text = "[source: https://gleif.org/lei/12345] confirmed entity LEI."
    out = cs.extract(text)
    rag = [s for s in out if s["type"] == "rag" and s.get("url")]
    assert len(rag) == 1


def test_extract_returns_empty_for_no_citations():
    """A response with no citations → empty list (NOT None)."""
    from aria_service.intel import chat_sources as cs

    text = "Hello, I am ARIA. How can I help?"
    out = cs.extract(text)
    assert out == []


def test_extract_handles_empty_input():
    """Edge case: empty + None inputs return []."""
    from aria_service.intel import chat_sources as cs

    assert cs.extract("") == []
    assert cs.extract("", tool_context="") == []
    assert cs.extract("a", tool_context="") == []


def test_extract_combines_response_and_tool_context():
    """Citations from BOTH response_text and tool_context are merged."""
    from aria_service.intel import chat_sources as cs

    response = "Per the orchestrator [from dd_orchestrate:run1]"
    tc = "[from sanctions_claim_guard:run2] OFAC clear."
    out = cs.extract(response, tool_context=tc)
    tools = sorted([s["tool"] for s in out if s["type"] == "tool"])
    assert tools == ["dd_orchestrate", "sanctions_claim_guard"]


def test_extract_caps_at_max_sources():
    """50-source cap protects the frontend from pathological responses."""
    from aria_service.intel import chat_sources as cs

    # Build 100 distinct URLs
    urls = "\n".join(f"https://site{i}.example.org/page" for i in range(100))
    out = cs.extract(urls)
    assert len(out) <= cs.MAX_SOURCES


def test_extract_filters_blocklisted_urls():
    """example.com / localhost / 127.0.0.1 are not useful citations."""
    from aria_service.intel import chat_sources as cs

    text = "See https://example.com/foo and https://127.0.0.1:8000/api."
    out = cs.extract(text)
    assert all("example.com" not in (s.get("url") or "") for s in out)
    assert all("127.0.0.1" not in (s.get("url") or "") for s in out)


def test_extract_is_fail_soft():
    """Even with malformed input, extract returns a list (never raises)."""
    from aria_service.intel import chat_sources as cs

    weird_inputs = [
        "[from ][broken]",
        "https://" + ("x" * 5000),
        "\x00\x01\x02 [from tool:id] \x03",
    ]
    for inp in weird_inputs:
        out = cs.extract(inp)
        assert isinstance(out, list)


# ── Wiring presence checks ──

def test_rf732_wiring_present_in_aria_chat_impl():
    """R-F732: sources extraction must be wired before the response
    dict is returned in `_aria_chat_impl`. Catches accidental revert."""
    from aria_service import aria_engine

    src = inspect.getsource(aria_engine._aria_chat_impl)
    assert "R-F732" in src
    assert "chat_sources" in src
    assert '"sources"' in src or "'sources'" in src


def test_rf732_wiring_present_in_aria_chat_stream_impl():
    """R-F732: sources SSE event must be wired before the `done` event
    in `_aria_chat_stream_impl`. Per CLAUDE.md §13 stream-bypass rule."""
    from aria_service import aria_engine

    src = inspect.getsource(aria_engine._aria_chat_stream_impl)
    assert "R-F732" in src
    assert "chat_sources" in src
    assert "sources" in src
