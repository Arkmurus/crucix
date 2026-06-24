"""
Ingest the Python on Windows documentation into ARIA's RAG + knowledge base.

Fetches https://docs.python.org/3/using/windows.html, extracts the text,
and ingests it into:
  1. RAG store (chromadb) — for semantic retrieval at query time
  2. Knowledge facts — for deep semantic understanding

Usage:
    python scripts/ingest_python_windows_docs.py

Requires the .venv to be active and the ARIA service modules importable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ingest_python_windows")

# ── HTML-to-text extractor (no external deps) ──────────────────────────────


class _HTMLToText(HTMLParser):
    """Simple HTML-to-text converter. Strips tags, normalises whitespace."""

    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._skip = False
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "nav", "footer", "header", "aside"):
            self._skip = True
        if tag == "pre":
            self._in_pre = True
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "header", "aside"):
            self._skip = False
        if tag == "pre":
            self._in_pre = False
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th"):
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._text_parts)
        # Collapse multiple newlines
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        # Collapse horizontal whitespace (but keep pre-formatted)
        if not self._in_pre:
            raw = re.sub(r"[ \t]+", " ", raw)
        return raw.strip()


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML."""
    parser = _HTMLToText()
    parser.feed(html)
    return parser.get_text()


# ── Key sections to extract as standalone facts ────────────────────────────

# These are the major headings in the Python on Windows docs. We'll extract
# the text under each and store as separate facts for granular retrieval.
KEY_SECTIONS = [
    "Installing Python",
    "The full installer",
    "The Microsoft Store package",
    "The nuget.org package",
    "The embeddable package",
    "Alternative bundles",
    "Configuring Python",
    "Excursus: Setting environment variables",
    "UTF-8 mode",
    "Python Launcher for Windows",
    "Getting started",
    "From the command-line",
    "Shebang lines",
    "Python in the Windows terminal",
    "Finding the Python executable",
    "Additional Windows GUI tools",
    "Running Python on Windows",
    "Running Python as a child process",
    "Running Python with Windows scripting host",
    "Running Python with Windows scheduler",
    "Running Python as a Windows service",
    "Compiling Python on Windows",
    "Installing without UI",
    "Embedding Python",
    "Other resources",
    "Additional modules",
    "PyWin32",
    "cx_Freeze",
    "WMI Scripting",
    "Windows-specific modules",
    "MSYS / MinGW / Git Bash",
    "Windows Subsystem for Linux",
]


async def main() -> None:
    # ── Step 1: Fetch the page ──────────────────────────────────────────
    url = "https://docs.python.org/3/using/windows.html"
    logger.info("Fetching %s ...", url)

    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            url,
            headers={"User-Agent": "ARIA-Knowledge-Ingest/1.0"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text

    logger.info("Fetched %d bytes of HTML", len(html))

    # ── Step 2: Extract text ────────────────────────────────────────────
    text = extract_text_from_html(html)
    logger.info("Extracted %d chars of text", len(text))

    if len(text) < 500:
        logger.error("Extracted text too short (%d chars) — aborting", len(text))
        sys.exit(1)

    # ── Step 3: Ingest into RAG store ───────────────────────────────────
    logger.info("Ingesting into RAG store ...")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from aria_service.intel import rag_store

    rag_result = await rag_store.ingest_document(
        text,
        source="docs.python.org:using/windows",
        source_type="article",
        title="Python on Windows — Python 3 documentation",
        url=url,
        extra_metadata={
            "domain": "python.org",
            "doc_type": "official_documentation",
            "topic": "python_windows",
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info("RAG ingest result: %s", rag_result)

    # ── Step 4: Store key facts ─────────────────────────────────────────
    from aria_service.intel import knowledge

    # Store the full document as a comprehensive fact
    fact_result = await knowledge.store_fact(
        topic="Python on Windows — complete guide",
        content=text[:8000],  # store first 8k chars as the primary fact
        source="docs.python.org:using/windows",
        confidence="CONFIRMED",
        source_url=url,
        fact_type="TECHNICAL_REFERENCE",
        entity_name="Python on Windows",
    )
    logger.info("Primary fact result: %s", fact_result.get("action", "unknown"))

    # Store key section summaries as individual facts
    sections = _extract_sections(text)
    for section_title, section_text in sections.items():
        if len(section_text) < 100:
            continue
        content = section_text[:2000]  # cap each section fact
        section_result = await knowledge.store_fact(
            topic=f"Python on Windows — {section_title}",
            content=content,
            source="docs.python.org:using/windows",
            confidence="CONFIRMED",
            source_url=url,
            fact_type="TECHNICAL_REFERENCE",
            entity_name=f"Python on Windows — {section_title}",
        )
        logger.info(
            "Section fact '%s': %s", section_title, section_result.get("action", "unknown")
        )

    logger.info("✅ Python on Windows documentation ingested successfully!")


def _extract_sections(text: str) -> dict[str, str]:
    """Split extracted text into sections by heading patterns."""
    sections: dict[str, str] = {}
    current_section = "preamble"
    current_lines: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        # Detect headings (all-caps lines or lines ending with —)
        if stripped and (stripped.isupper() or stripped.endswith("—")):
            if current_lines:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped.rstrip("—").strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_section] = "\n".join(current_lines).strip()

    return sections


if __name__ == "__main__":
    asyncio.run(main())
