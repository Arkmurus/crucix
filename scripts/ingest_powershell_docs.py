"""
R-F1888: Ingest comprehensive PowerShell documentation into ARIA's RAG + knowledge.

Fetches key pages from Microsoft's official PowerShell documentation and ingests
them into ARIA's RAG store and knowledge base so she has deep understanding of
PowerShell for Windows development and automation.

Sources ingested:
  1. PowerShell documentation overview
  2. What is PowerShell?
  3. Installing PowerShell on Windows
  4. PowerShell language reference (variables, operators, syntax)
  5. PowerShell modules overview
  6. PowerShell remoting (WinRM, SSH)
  7. PowerShell execution policies
  8. PowerShell scripting (functions, scripts, scope)
  9. PowerShell Desired State Configuration (DSC)
  10. PowerShell providers (registry, file system, certificate)
  11. PowerShell jobs and runspaces
  12. PowerShell CIM/WMI
  13. PowerShell security (signing, JEA, Just Enough Administration)
  14. PowerShell .NET interop
  15. PowerShell error handling (try/catch/finally, trap, $Error)
  16. PowerShell pipeline and object manipulation
  17. PowerShell profiles
  18. PowerShell parameter binding and advanced functions
  19. PowerShell module manifests
  20. PowerShell Active Directory module
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ingest_powershell")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


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
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        if not self._in_pre:
            raw = re.sub(r"[ \t]+", " ", raw)
        return raw.strip()


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML."""
    parser = _HTMLToText()
    parser.feed(html)
    return parser.get_text()


# ── Documentation sources ──────────────────────────────────────────────────

# Each entry: (url, title, topic_tag, section_facts)
# section_facts are key topics to extract as individual knowledge facts
DOC_SOURCES = [
    (
        "https://learn.microsoft.com/en-us/powershell/scripting/overview",
        "PowerShell documentation overview",
        "powershell_overview",
        [
            "What is PowerShell",
            "PowerShell editions",
            "PowerShell supported platforms",
            "PowerShell compared to other shells",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/scripting/what-is-windows-powershell",
        "What is Windows PowerShell",
        "powershell_what_is",
        [
            "Windows PowerShell features",
            "PowerShell cmdlet architecture",
            "PowerShell provider model",
            "PowerShell scripting language",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-windows",
        "Installing PowerShell on Windows",
        "powershell_install",
        [
            "Install PowerShell using winget",
            "Install PowerShell using MSI",
            "Install PowerShell from GitHub",
            "PowerShell installation paths",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_language_keywords",
        "PowerShell language keywords",
        "powershell_language_keywords",
        [
            "PowerShell keywords",
            "PowerShell language syntax",
            "PowerShell reserved words",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_variables",
        "PowerShell variables",
        "powershell_variables",
        [
            "Variable types and scopes",
            "Automatic variables",
            "Preference variables",
            "Environment variables",
            "Variable syntax and assignment",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators",
        "PowerShell operators",
        "powershell_operators",
        [
            "Arithmetic operators",
            "Comparison operators",
            "Logical operators",
            "Redirection operators",
            "Split and join operators",
            "Type operators",
            "Unary operators",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_functions_advanced",
        "PowerShell advanced functions",
        "powershell_advanced_functions",
        [
            "Advanced function syntax",
            "Parameter attributes",
            "CmdletBinding attribute",
            "Input processing methods",
            "Begin/Process/End blocks",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_functions_advanced_parameters",
        "PowerShell advanced function parameters",
        "powershell_parameters",
        [
            "Parameter declaration",
            "Parameter attributes",
            "Mandatory parameters",
            "Parameter sets",
            "Parameter validation",
            "Dynamic parameters",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pipelines",
        "PowerShell pipelines",
        "powershell_pipelines",
        [
            "Pipeline fundamentals",
            "Pipeline object passing",
            "Pipeline parameter binding",
            "Pipeline chain operators",
            "Pipeline output",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_modules",
        "PowerShell modules",
        "powershell_modules",
        [
            "Module types",
            "Module manifests",
            "Module discovery and installation",
            "PowerShellGet and PSResourceGet",
            "Module scope",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_remote",
        "PowerShell remoting",
        "powershell_remoting",
        [
            "WinRM remoting",
            "SSH remoting",
            "Enter-PSSession",
            "Invoke-Command",
            "Session management",
            "Remoting security",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies",
        "PowerShell execution policies",
        "powershell_execution_policy",
        [
            "Execution policy scope",
            "Execution policy types",
            "Setting execution policy",
            "Bypass execution policy",
            "Execution policy for signed scripts",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_scopes",
        "PowerShell scopes",
        "powershell_scopes",
        [
            "Scope types",
            "Scope hierarchy",
            "Private and public scope",
            "Using scope modifier",
            "Script scope",
            "Global scope",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_try_catch_finally",
        "PowerShell error handling",
        "powershell_error_handling",
        [
            "Try/Catch/Finally",
            "Trap statement",
            "ErrorAction preference",
            "ErrorVariable",
            "ErrorRecord object",
            "Error categories and exceptions",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_providers",
        "PowerShell providers",
        "powershell_providers",
        [
            "Provider overview",
            "Registry provider",
            "FileSystem provider",
            "Certificate provider",
            "Environment provider",
            "Function provider",
            "Alias provider",
            "Variable provider",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_jobs",
        "PowerShell jobs",
        "powershell_jobs",
        [
            "Background jobs",
            "Start-Job",
            "Receive-Job",
            "Wait-Job",
            "Job lifecycle",
            "Thread jobs",
            "Scheduled jobs",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_cim_session",
        "PowerShell CIM sessions",
        "powershell_cim",
        [
            "CIM/WMI overview",
            "CIM sessions",
            "Get-CimInstance",
            "Invoke-CimMethod",
            "CIM vs WMI cmdlets",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_signing",
        "PowerShell script signing",
        "powershell_signing",
        [
            "Code signing certificates",
            "Set-AuthenticodeSignature",
            "Signing requirements",
            "Trusted publishers",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_profiles",
        "PowerShell profiles",
        "powershell_profiles",
        [
            "Profile types and locations",
            "PROFILE automatic variable",
            "Profile scope and loading order",
            "Creating and editing profiles",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe",
        "PowerShell.exe command-line",
        "powershell_exe",
        [
            "PowerShell.exe parameters",
            "pwsh.exe parameters",
            "Command-line options",
            "Execution policy on command line",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_desiredstateconfiguration",
        "PowerShell DSC overview",
        "powershell_dsc",
        [
            "DSC architecture",
            "DSC configurations",
            "DSC resources",
            "DSC LCM Local Configuration Manager",
            "DSC pull server",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_script_internationalization",
        "PowerShell script internationalization",
        "powershell_i18n",
        [
            "Data sections",
            "ConvertFrom-StringData",
            "Localized strings",
            "Culture-specific scripts",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_type_operators",
        "PowerShell type operators and .NET interop",
        "powershell_dotnet",
        [
            "Type operators -is -as",
            "Accessing .NET classes",
            "Creating .NET objects",
            "Using C# code in PowerShell",
            "Add-Type cmdlet",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables",
        "PowerShell automatic variables",
        "powershell_automatic_variables",
        [
            "Automatic variables reference",
            "Common automatic variables",
            "Preference variables",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_arrays",
        "PowerShell arrays and collections",
        "powershell_arrays",
        [
            "Array creation and syntax",
            "ArrayList and List[T]",
            "Array operators",
            "Hashtables",
            "Ordered dictionaries",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_hash_tables",
        "PowerShell hashtables",
        "powershell_hashtables",
        [
            "Hashtable creation",
            "Hashtable operations",
            "Hashtable as data structures",
            "Splatting with hashtables",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_regular_expressions",
        "PowerShell regular expressions",
        "powershell_regex",
        [
            "Regex in PowerShell",
            "-match operator",
            "-replace operator",
            "Select-String",
            "Regex options and syntax",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_comparison_operators",
        "PowerShell comparison operators",
        "powershell_comparison",
        [
            "Equality operators",
            "Matching operators",
            "Containment operators",
            "Replacement operators",
            "Type comparison",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_switch",
        "PowerShell switch statement",
        "powershell_switch",
        [
            "Switch syntax",
            "Switch with regex",
            "Switch with wildcard",
            "Switch with script block",
            "Switch performance",
        ],
    ),
    (
        "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_using",
        "PowerShell using statement",
        "powershell_using",
        [
            "Using module",
            "Using namespace",
            "Using assembly",
            "Using statement scope",
        ],
    ),
]


async def fetch_page(url: str, title: str) -> tuple[str, str] | None:
    """Fetch a documentation page and extract text."""
    import httpx

    logger.info("Fetching: %s (%s)", title, url)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "ARIA-Knowledge-Ingest/1.0",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            html = resp.text
            text = extract_text_from_html(html)
            if len(text) < 200:
                logger.warning("  Too short (%d chars) for %s", len(text), title)
                return None
            logger.info("  Extracted %d chars", len(text))
            return (text, html)
    except Exception as e:
        logger.error("  Failed to fetch %s: %s", url, e)
        return None


async def main() -> None:
    from aria_service.intel import rag_store, knowledge

    # ── Step 1: Fetch all documentation pages ───────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 1: Fetching PowerShell documentation pages")
    logger.info("=" * 60)

    pages = []
    for url, title, topic_tag, section_facts in DOC_SOURCES:
        result = await fetch_page(url, title)
        if result:
            text, html = result
            pages.append((url, title, topic_tag, section_facts, text))
        # Rate limit: be gentle to Microsoft's servers
        await asyncio.sleep(0.5)

    logger.info("\nFetched %d/%d pages successfully", len(pages), len(DOC_SOURCES))

    if not pages:
        logger.error("No pages fetched — aborting")
        return

    # ── Step 2: Ingest into RAG store ───────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("STEP 2: Ingesting into RAG store")
    logger.info("=" * 60)

    for url, title, topic_tag, section_facts, text in pages:
        try:
            result = await rag_store.ingest_document(
                text,
                source=f"learn.microsoft.com:{topic_tag}",
                source_type="article",
                title=title[:300],
                url=url,
                extra_metadata={
                    "domain": "learn.microsoft.com",
                    "doc_type": "official_documentation",
                    "topic": topic_tag,
                    "technology": "powershell",
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info("  RAG: %s — %s", title[:60], result.get("ingested", False))
        except Exception as e:
            logger.error("  RAG failed for %s: %s", title[:60], e)

    # ── Step 3: Store knowledge facts ───────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("STEP 3: Storing knowledge facts")
    logger.info("=" * 60)

    for url, title, topic_tag, section_facts, text in pages:
        # Store the full page as a primary fact (first 8K chars)
        try:
            fact_result = await knowledge.store_fact(
                topic=f"PowerShell — {title}",
                content=text[:8000],
                source=f"learn.microsoft.com:{topic_tag}",
                confidence="CONFIRMED",
                source_url=url,
                fact_type="TECHNICAL_REFERENCE",
                entity_name=f"PowerShell — {title}",
            )
            logger.info(
                "  FACT: %s — %s", title[:60], fact_result.get("action", "unknown")
            )
        except Exception as e:
            logger.error("  FACT failed for %s: %s", title[:60], e)

        # Store section-level facts for key topics
        for section in section_facts:
            # Find the section in the text
            section_text = _find_section(text, section)
            if section_text and len(section_text) > 100:
                try:
                    section_result = await knowledge.store_fact(
                        topic=f"PowerShell — {section}",
                        content=section_text[:2000],
                        source=f"learn.microsoft.com:{topic_tag}",
                        confidence="CONFIRMED",
                        source_url=url,
                        fact_type="TECHNICAL_REFERENCE",
                        entity_name=f"PowerShell — {section}",
                    )
                    logger.debug(
                        "  SECTION: %s — %s", section, section_result.get("action", "unknown")
                    )
                except Exception:
                    pass

    logger.info("\n" + "=" * 60)
    logger.info("DONE: PowerShell documentation ingested successfully!")
    logger.info("=" * 60)


def _find_section(text: str, section_title: str) -> str:
    """Find a section in the extracted text by title."""
    # Try to find the section by looking for the title in the text
    idx = text.lower().find(section_title.lower())
    if idx == -1:
        return ""
    # Return ~2000 chars starting from the section title
    return text[idx : idx + 2500]


if __name__ == "__main__":
    asyncio.run(main())
