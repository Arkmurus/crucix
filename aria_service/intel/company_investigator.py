"""R-F1061 — Company Deep Investigation Pipeline.

Chains every available OSINT capability into a single structured investigation
of a company or entity. Produces a cited, grounded report.

Pipeline:
  1. Entity resolution — canonical name, jurisdiction, identifiers
  2. Web search — multi-engine (SearXNG, Brave, Google, Bing)
  3. Deep crawl — follow links, extract content, JS rendering
  4. Company registry — Companies House, OpenCorporates
  5. Sanctions screening — OFAC, EU, UK, UN
  6. Conflict/risk — ACLED, GDELT, World Bank
  7. News — RSS feeds, News API
  8. Social media — LinkedIn, Twitter (public)
  9. Technology stack — Wappalyzer, BuiltWith
  10. SSL/DNS — certificate transparency, WHOIS
  11. Procurement — tender history, contract awards
  12. Synthesis — LLM-powered report with citations

Gate: ARIA_COMPANY_INVESTIGATOR_ENABLED=1 to enable (default ON).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("aria.company_investigator")

# Gate
_ENABLED = os.getenv("ARIA_COMPANY_INVESTIGATOR_ENABLED", "1") == "1"

# Time budgets per phase (seconds)
_PHASE_TIMEOUTS = {
    "entity_resolution": 10.0,
    "web_search": 30.0,
    "deep_crawl": 60.0,
    "company_registry": 15.0,
    "sanctions": 10.0,
    "conflict_risk": 15.0,
    "news": 20.0,
    "social_media": 15.0,
    "tech_stack": 10.0,
    "ssl_dns": 10.0,
    "procurement": 20.0,
    "synthesis": 45.0,
}
_TOTAL_TIMEOUT = 120.0  # max total for the whole pipeline


@dataclass
class InvestigationFinding:
    """A single finding with provenance."""
    category: str          # "web", "registry", "sanctions", "news", etc.
    title: str             # Short title
    summary: str           # 1-2 sentence summary
    source: str            # URL or document ID
    confidence: float      # 0..1
    detail: str = ""       # Longer detail (truncated to 1000 chars)
    tags: list[str] = field(default_factory=list)


@dataclass
class InvestigationReport:
    """Complete investigation result."""
    entity_name: str
    canonical_name: str = ""
    jurisdiction: str = ""
    findings: list[InvestigationFinding] = field(default_factory=list)
    summary: str = ""
    risk_indicators: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    sources_cited: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str = ""


def _looks_like_document_text(text: str) -> bool:
    """Heuristic check: is this string likely a document body rather than a
    company name? Returns True for text that is too long, contains document
    markers, legal boilerplate, or newlines — none of which belong in a
    legitimate company name.

    R-F1326: defensive guard against mis-routing when _detect_tool_intent
    fails to catch a doc-review question and passes the document body as
    company_name to investigate_company.

    R-F1515: added question-pattern exemption. A legitimate query about a
    company ("what is your legal understanding of the repercussions for a
    CEO...") can easily exceed 200 chars. If the text looks like a question
    (starts with a question word), it's treated as a query, not a document
    body — the LLM will handle it appropriately.
    """
    if not text:
        return False
    low = text.lower()
    
    # R-F1515: if the text starts with a question word or instruction verb,
    # it's a query, not a document body. Don't reject it based on length.
    _query_starters = [
        "what ", "who ", "where ", "when ", "why ", "how ",
        "is ", "are ", "can ", "could ", "would ", "should ", "did ", "does ",
        "tell ", "find ", "research ", "investigate ", "look ",
        "check ", "analyse ", "analyze ", "review ", "explain ",
        "aria ",  # "Aria, what is..." or "Aria, can you..."
    ]
    stripped = low.lstrip(" .,:;!?\"'")
    for starter in _query_starters:
        if stripped.startswith(starter):
            # It's a question/instruction — allow up to 2000 chars
            if len(text) > 2000:
                return True
            return False
    
    # Too long — real company names are under ~100 chars
    if len(text) > 200:
        return True
    # Contains newlines — company names are single-line
    if "\n" in text or "\r" in text:
        return True
    # Contains the attached-document marker
    if "[ATTACHED DOCUMENT" in text or "[/ATTACHED DOCUMENT]" in text:
        return True
    # Contains legal boilerplate phrases
    _legal_boilerplate = [
        "confidential", "privileged", "hereby", "hereinafter",
        "whereas", "witnesseth", "indemnify", "indemnification",
        "governing law", "force majeure", "entire agreement",
        "non-disclosure", "non disclosure", "intellectual property",
        "representations and warranties",
    ]
    if any(p in low for p in _legal_boilerplate):
        return True
    # Contains common document-structure markers
    _doc_markers = [
        "clause ", "section ", "article ", "schedule ", "exhibit ",
        "page ", "appendix ",
    ]
    if any(p in low for p in _doc_markers):
        return True
    return False


async def investigate_company(
    company_name: str,
    jurisdiction: str = "",
    website: str = "",
    max_depth: int = 2,
    uei: str = "",
    ncage: str = "",
) -> InvestigationReport:
    """Run the full investigation pipeline on a company.

    Args:
        company_name: Name of the company to investigate.
        jurisdiction: Optional jurisdiction hint (e.g. "UK", "UAE").
        website: Optional website URL to start from.
        max_depth: Max crawl depth (default 2).
        uei: Optional SAM.gov UEI for contract lookup.
        ncage: Optional NCAGE code for additional verification.

    Returns:
        InvestigationReport with all findings.
    """
    start = time.monotonic()
    report = InvestigationReport(entity_name=company_name, jurisdiction=jurisdiction)

    if not _ENABLED:
        report.error = "Company investigator disabled (set ARIA_COMPANY_INVESTIGATOR_ENABLED=1)"
        report.duration_ms = (time.monotonic() - start) * 1000
        return report

    # R-F1326 — defensive short-circuit: reject inputs that are clearly not
    # company names (document text, legal boilerplate, overly long strings).
    # Prevents mis-routing when _detect_tool_intent fails to catch a doc-
    # review question and passes the document body as company_name.
    # R-F1515: improved error message to guide the user instead of a dead-end.
    if _looks_like_document_text(company_name):
        report.error = (
            "Input appears to be a document or legal text, not a company name. "
            "Routing to document review instead."
        )
        report.summary = (
            f"That doesn't look like a company name — it reads as a question or "
            f"document text. If you're asking about a specific company, please "
            f"send just the company name (e.g. 'TAC DMCC' or 'Turkhan Mahmudov'). "
            f"If you need legal analysis, I can help with that too — just ask "
            f"directly without wrapping it in a company investigation request."
        )
        report.duration_ms = (time.monotonic() - start) * 1000
        return report

    try:
        async def _run_pipeline() -> None:
            # Phase 1: Entity resolution
            await _phase_entity_resolution(report, company_name, jurisdiction, website)

            # Phase 2-13: Run ALL data-gathering phases CONCURRENTLY
            gather_tasks = [
                _phase_web_search(report, company_name, website),
                _phase_deep_crawl(report, company_name, website, max_depth),
                _phase_company_registry(report, company_name, jurisdiction),
                _phase_sanctions(report, company_name, jurisdiction),
                _phase_conflict_risk(report, company_name, jurisdiction),
                _phase_news(report, company_name),
                _phase_social_media(report, company_name),
                _phase_tech_stack(report, website),
                _phase_ssl_dns(report, website),
                _phase_procurement(report, company_name, jurisdiction),
                _phase_contract_lookup(report, company_name, uei=uei),
            ]
            await asyncio.gather(*gather_tasks, return_exceptions=True)

            # Phase 13: Synthesis
            await _phase_synthesis(report, company_name)

        await asyncio.wait_for(_run_pipeline(), timeout=_TOTAL_TIMEOUT)

    except asyncio.TimeoutError:
        logger.warning("[company_investigator] pipeline timed out after %ss", _TOTAL_TIMEOUT)
        report.error = f"Investigation timed out after {_TOTAL_TIMEOUT}s"
    except Exception as e:
        logger.exception("[company_investigator] pipeline failed")
        report.error = str(e)[:500]

    report.duration_ms = (time.monotonic() - start) * 1000
    return report


# ── Phase implementations ──────────────────────────────────────────────


async def _phase_entity_resolution(
    report: InvestigationReport,
    company_name: str,
    jurisdiction: str,
    website: str,
) -> None:
    """Resolve entity to canonical form."""
    try:
        from . import entity_resolver as _er
        resolved = await _er.resolve(company_name, jurisdiction=jurisdiction)
        if resolved:
            report.canonical_name = resolved.get("canonical", company_name)
            if not report.jurisdiction:
                report.jurisdiction = resolved.get("jurisdiction", "")
    except Exception as e:
        logger.debug("[company_investigator] entity resolution failed: %s", e)


async def _phase_web_search(
    report: InvestigationReport,
    company_name: str,
    website: str,
) -> None:
    """Search the web for company information."""
    try:
        from . import web_search as _ws
        queries = [
            f"{company_name} company",
            f"{company_name} {report.jurisdiction}",
            f"{company_name} owner director",
            f"{company_name} contract award",
        ]
        if website:
            queries.insert(0, f"site:{website} {company_name}")

        for query in queries[:3]:  # Limit to 3 queries
            try:
                results = await asyncio.wait_for(
                    _ws.search(query, num_results=5),
                    timeout=8.0,
                )
                for r in (results or [])[:3]:
                    url = r.get("url", "") or r.get("link", "")
                    title = r.get("title", "")[:200]
                    snippet = r.get("snippet", "")[:300]
                    if url:
                        report.findings.append(InvestigationFinding(
                            category="web",
                            title=title or f"Search result: {url[:80]}",
                            summary=snippet or title,
                            source=url,
                            confidence=0.6,
                            tags=["web_search", query[:50]],
                        ))
            except asyncio.TimeoutError:
                logger.debug("[company_investigator] web search query timed out: %s", query)
    except Exception as e:
        logger.debug("[company_investigator] web search failed: %s", e)


async def _phase_deep_crawl(
    report: InvestigationReport,
    company_name: str,
    website: str,
    max_depth: int,
) -> None:
    """Deep-crawl the company website and related pages."""
    if not website:
        return
    try:
        from . import web_crawler as _wc
        pages = await asyncio.wait_for(
            _wc.crawl(website, max_pages=10, max_depth=max_depth),
            timeout=_PHASE_TIMEOUTS["deep_crawl"],
        )
        for page in (pages or [])[:5]:
            url = page.get("url", "")
            title = page.get("title", "")[:200]
            text = page.get("text", "")[:500]
            if url:
                report.findings.append(InvestigationFinding(
                    category="crawl",
                    title=title or f"Crawled: {url[:80]}",
                    summary=text[:300],
                    source=url,
                    confidence=0.8,
                    tags=["deep_crawl"],
                ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] deep crawl timed out")
    except Exception as e:
        logger.debug("[company_investigator] deep crawl failed: %s", e)


async def _phase_company_registry(
    report: InvestigationReport,
    company_name: str,
    jurisdiction: str,
) -> None:
    """Check company registries."""
    try:
        from . import companies_house as _ch
        results = await asyncio.wait_for(
            _ch.search(company_name, jurisdiction=jurisdiction),
            timeout=_PHASE_TIMEOUTS["company_registry"],
        )
        for r in (results or [])[:3]:
            report.findings.append(InvestigationFinding(
                category="registry",
                title=f"Company registry: {r.get('name', company_name)}",
                summary=f"Status: {r.get('status', 'unknown')}. "
                        f"Registered: {r.get('incorporation_date', 'unknown')}",
                source=r.get("url", "companies_house"),
                confidence=0.9,
                tags=["registry", r.get("jurisdiction", "")],
            ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] company registry timed out")
    except Exception as e:
        logger.debug("[company_investigator] company registry failed: %s", e)


async def _phase_sanctions(
    report: InvestigationReport,
    company_name: str,
    jurisdiction: str,
) -> None:
    """Screen against sanctions lists."""
    try:
        from .sanctions_canonical import check_sanctions as _sc
        result = await asyncio.wait_for(
            _sc(company_name, jurisdiction=jurisdiction),
            timeout=_PHASE_TIMEOUTS["sanctions"],
        )
        verdict = result.get("verdict", "CLEAR")
        matches = result.get("matches", [])
        if matches:
            for m in matches[:3]:
                report.findings.append(InvestigationFinding(
                    category="sanctions",
                    title=f"Sanctions match: {m.get('formatted_name', '?')}",
                    summary=f"Source: {m.get('source', '?')}. "
                            f"Score: {m.get('match_score', 0):.2f}",
                    source=m.get("source", "sanctions_canonical"),
                    confidence=0.95,
                    tags=["sanctions", verdict],
                ))
                if verdict == "HARD_STOP":
                    report.risk_indicators.append(
                        f"Sanctions HARD STOP: {m.get('formatted_name', '?')} "
                        f"on {m.get('source', '?')}"
                    )
        else:
            report.findings.append(InvestigationFinding(
                category="sanctions",
                title="No sanctions matches",
                summary=f"No sanctions hits for {company_name}",
                source="sanctions_canonical",
                confidence=0.9,
                tags=["sanctions", "clean"],
            ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] sanctions check timed out")
    except Exception as e:
        logger.debug("[company_investigator] sanctions check failed: %s", e)


async def _phase_conflict_risk(
    report: InvestigationReport,
    company_name: str,
    jurisdiction: str,
) -> None:
    """Check conflict and risk indicators."""
    try:
        from . import conflict_tracker as _ct
        events = await asyncio.wait_for(
            _ct.get_events(jurisdiction or company_name, days=365),
            timeout=_PHASE_TIMEOUTS["conflict_risk"],
        )
        if events:
            report.findings.append(InvestigationFinding(
                category="conflict",
                title=f"Conflict events in {jurisdiction or company_name}",
                summary=f"{len(events)} events in the last year",
                source="ACLED/GDELT",
                confidence=0.7,
                tags=["conflict", "risk"],
            ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] conflict check timed out")
    except Exception as e:
        logger.debug("[company_investigator] conflict check failed: %s", e)


async def _phase_news(
    report: InvestigationReport,
    company_name: str,
) -> None:
    """Search news for company mentions."""
    try:
        from . import news_monitor as _nm
        articles = await asyncio.wait_for(
            _nm.search(company_name, max_articles=5),
            timeout=_PHASE_TIMEOUTS["news"],
        )
        for a in (articles or [])[:5]:
            report.findings.append(InvestigationFinding(
                category="news",
                title=a.get("title", "")[:200],
                summary=a.get("summary", "")[:300],
                source=a.get("url", ""),
                confidence=0.6,
                tags=["news", a.get("source", "")],
            ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] news search timed out")
    except Exception as e:
        logger.debug("[company_investigator] news search failed: %s", e)


async def _phase_social_media(
    report: InvestigationReport,
    company_name: str,
) -> None:
    """Search social media for company presence."""
    try:
        from . import web_search as _ws
        queries = [
            f"site:linkedin.com {company_name}",
            f"site:twitter.com {company_name}",
        ]
        for query in queries:
            try:
                results = await asyncio.wait_for(
                    _ws.search(query, num_results=3),
                    timeout=7.0,
                )
                for r in (results or [])[:2]:
                    url = r.get("url", "") or r.get("link", "")
                    if url:
                        report.findings.append(InvestigationFinding(
                            category="social",
                            title=r.get("title", "")[:200],
                            summary=f"Social media presence: {url[:100]}",
                            source=url,
                            confidence=0.5,
                            tags=["social_media"],
                        ))
            except asyncio.TimeoutError:
                continue
    except Exception as e:
        logger.debug("[company_investigator] social media search failed: %s", e)


async def _phase_tech_stack(
    report: InvestigationReport,
    website: str,
) -> None:
    """Identify technology stack from website."""
    if not website:
        return
    try:
        from . import web_crawler as _wc
        headers = await asyncio.wait_for(
            _wc.get_headers(website),
            timeout=_PHASE_TIMEOUTS["tech_stack"],
        )
        if headers:
            server = headers.get("server", "")
            powered_by = headers.get("x-powered-by", "")
            techs = []
            if server:
                techs.append(f"Server: {server}")
            if powered_by:
                techs.append(f"Powered by: {powered_by}")
            if techs:
                report.findings.append(InvestigationFinding(
                    category="tech",
                    title="Technology stack",
                    summary="; ".join(techs),
                    source=website,
                    confidence=0.7,
                    tags=["tech_stack"],
                ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] tech stack check timed out")
    except Exception as e:
        logger.debug("[company_investigator] tech stack check failed: %s", e)


async def _phase_ssl_dns(
    report: InvestigationReport,
    website: str,
) -> None:
    """Check SSL certificate, DNS records, and WHOIS data."""
    if not website:
        return
    domain = website.replace("https://", "").replace("http://", "").split("/")[0]

    async def _check_certificates() -> None:
        """Check SSL certificates via certificate transparency."""
        try:
            from .sources import cert_transparency as _ct
            certs = await asyncio.wait_for(
                _ct.search(domain),
                timeout=_PHASE_TIMEOUTS["ssl_dns"] * 0.4,
            )
            if certs:
                report.findings.append(InvestigationFinding(
                    category="ssl",
                    title=f"SSL certificates for {domain}",
                    summary=f"{len(certs)} certificates found",
                    source=f"crt.sh/{domain}",
                    confidence=0.8,
                    tags=["ssl", "certificate_transparency"],
                ))
        except asyncio.TimeoutError:
            logger.debug("[company_investigator] SSL check timed out")
        except Exception as e:
            logger.debug("[company_investigator] SSL check failed: %s", e)

    async def _check_whois() -> None:
        """Check WHOIS record for the domain."""
        try:
            import socket as _sock
            loop = asyncio.get_running_loop()
            # Use whois via socket (no external dependency — pure Python stdlib)
            whois_server = "whois.verisign-grs.com"
            whois_port = 43

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(whois_server, whois_port),
                timeout=5.0,
            )
            writer.write(f"{domain}\r\n".encode("utf-8"))
            await writer.drain()

            response = await asyncio.wait_for(
                reader.read(4096), timeout=5.0,
            )
            writer.close()
            await writer.wait_closed()

            text = response.decode("utf-8", errors="replace")
            # Extract key fields
            import re as _re
            fields = {}
            for pattern, key in [
                (r"Registry Domain ID:\s*(.+)", "domain_id"),
                (r"Registrar:\s*(.+)", "registrar"),
                (r"Creation Date:\s*(.+)", "created"),
                (r"Registry Expiry Date:\s*(.+)", "expires"),
                (r"Registrant Organization:\s*(.+)", "org"),
                (r"Registrant Country:\s*(.+)", "country"),
                (r"Name Server:\s*(.+)", "nameserver"),
                (r"DNSSEC:\s*(.+)", "dnssec"),
            ]:
                m = _re.search(pattern, text, _re.IGNORECASE)
                if m:
                    fields[key] = m.group(1).strip()

            if fields:
                summary_parts = []
                if fields.get("org"):
                    summary_parts.append(f"Registrant: {fields['org']}")
                if fields.get("country"):
                    summary_parts.append(f"Country: {fields['country']}")
                if fields.get("created"):
                    summary_parts.append(f"Created: {fields['created']}")
                if fields.get("expires"):
                    summary_parts.append(f"Expires: {fields['expires']}")
                if fields.get("registrar"):
                    summary_parts.append(f"Registrar: {fields['registrar']}")

                report.findings.append(InvestigationFinding(
                    category="whois",
                    title=f"WHOIS: {domain}",
                    summary=" | ".join(summary_parts) if summary_parts else f"WHOIS record found for {domain}",
                    source=f"whois:{domain}",
                    confidence=0.9,
                    tags=["whois", "domain_registration"],
                ))
            else:
                report.findings.append(InvestigationFinding(
                    category="whois",
                    title=f"WHOIS: {domain}",
                    summary=f"WHOIS record retrieved but key fields not parseable for {domain}",
                    source=f"whois:{domain}",
                    confidence=0.5,
                    tags=["whois", "domain_registration"],
                ))
        except asyncio.TimeoutError:
            logger.debug("[company_investigator] WHOIS check timed out")
        except ConnectionRefusedError:
            logger.debug("[company_investigator] WHOIS server refused connection")
        except Exception as e:
            logger.debug("[company_investigator] WHOIS check failed: %s", e)

    # Run SSL cert check and WHOIS concurrently
    await asyncio.gather(
        _check_certificates(),
        _check_whois(),
        return_exceptions=True,
    )


async def _phase_procurement(
    report: InvestigationReport,
    company_name: str,
    jurisdiction: str,
) -> None:
    """Search procurement history."""
    try:
        from . import procurement_history as _ph
        contracts = await asyncio.wait_for(
            _ph.search(company_name, jurisdiction=jurisdiction),
            timeout=_PHASE_TIMEOUTS["procurement"],
        )
        for c in (contracts or [])[:3]:
            report.findings.append(InvestigationFinding(
                category="procurement",
                title=c.get("title", "")[:200],
                summary=f"Value: {c.get('value', 'unknown')}. "
                        f"Awarded: {c.get('date', 'unknown')}",
                source=c.get("url", ""),
                confidence=0.7,
                tags=["procurement", "contract"],
            ))
    except asyncio.TimeoutError:
        logger.debug("[company_investigator] procurement search timed out")
    except Exception as e:
        logger.debug("[company_investigator] procurement search failed: %s", e)


async def _phase_contract_lookup(
    report: InvestigationReport,
    company_name: str,
    uei: str = "",
) -> None:
    """Look up US government contract awards via USASpending.gov API.

    Uses the free, open USASpending.gov API — no registration required.
    If a UEI is available, does a targeted lookup by UEI.
    Otherwise searches by company name.
    """
    try:
        from .portal_registry import lookup_contracts_by_uei

        if uei:
            data = await asyncio.wait_for(
                lookup_contracts_by_uei(uei),
                timeout=15.0,
            )
            if data:
                awards = data.get("results") or data.get("awards") or []
                total_obligated = 0
                agency_count: dict[str, int] = {}
                for award in awards[:20]:
                    agency = award.get("funding_agency", award.get("agency", "unknown"))
                    agency_count[agency] = agency_count.get(agency, 0) + 1
                    total_obligated += award.get("obligated_amount", 0)

                report.findings.append(InvestigationFinding(
                    category="contracts",
                    title=f"USASpending.gov: {len(awards)} contract awards found",
                    summary=(
                        f"Total obligated: ${total_obligated:,.2f}. "
                        f"Agencies: {', '.join(sorted(agency_count.keys())[:5])}. "
                        f"UEI: {uei}"
                    ),
                    source=f"usaspending.gov/recipient/{uei}",
                    confidence=0.95,
                    tags=["contracts", "usaspending", "federal_awards"],
                ))

                # Add individual awards
                for award in awards[:5]:
                    report.findings.append(InvestigationFinding(
                        category="contracts",
                        title=award.get("title", "Contract award")[:200],
                        summary=(
                            f"Agency: {award.get('funding_agency', award.get('agency', 'unknown'))}. "
                            f"Amount: ${award.get('obligated_amount', 0):,.2f}. "
                            f"Date: {award.get('period_of_performance_start_date', award.get('date', 'unknown'))}"
                        ),
                        source=award.get("url", f"usaspending.gov/award/{award.get('id', '')}"),
                        confidence=0.95,
                        tags=["contracts", "award"],
                    ))

                # Check for red flags
                if total_obligated < 100000:
                    report.risk_indicators.append(
                        f"Low contract volume: ${total_obligated:,.2f} total — "
                        f"claims may exceed verified past performance"
                    )
                if len(agency_count) < 2:
                    report.risk_indicators.append(
                        f"Single-agency dependency: only {list(agency_count.keys())[0] if agency_count else 'unknown'} — "
                        f"limited agency diversity"
                    )
                return

        # No UEI or lookup failed — try name-based search
        from . import web_search as _ws
        query = f"{company_name} site:usaspending.gov contract award"
        results = await asyncio.wait_for(
            _ws.search(query, num_results=5),
            timeout=10.0,
        )
        for r in (results or [])[:3]:
            url = r.get("url", "") or r.get("link", "")
            if url:
                report.findings.append(InvestigationFinding(
                    category="contracts",
                    title=r.get("title", "")[:200],
                    summary=(r.get("snippet", "")[:300] or "USASpending.gov result"),
                    source=url,
                    confidence=0.5,
                    tags=["contracts", "web_search"],
                ))

    except asyncio.TimeoutError:
        logger.debug("[company_investigator] contract lookup timed out")
    except Exception as e:
        logger.debug("[company_investigator] contract lookup failed: %s", e)


async def _phase_synthesis(
    report: InvestigationReport,
    company_name: str,
) -> None:
    """Synthesize all findings into a structured report using LLM."""
    if not report.findings:
        report.summary = f"No findings could be gathered for {company_name}."
        return

    try:
        from . import llm_pipeline as _llm

        # Build evidence summary
        evidence_lines = []
        for f in report.findings:
            evidence_lines.append(
                f"[{f.category.upper()}] {f.title}\n"
                f"  Source: {f.source}\n"
                f"  Summary: {f.summary}\n"
            )

        evidence_text = "\n".join(evidence_lines)[:4000]

        prompt = (
            "You are an OSINT investigation analyst. Based ONLY on the evidence "
            "below, produce a structured investigation report for the company.\n\n"
            "Format your response as:\n"
            "1. EXECUTIVE SUMMARY (2-3 sentences)\n"
            "2. KEY FINDINGS (bullet points, each with source citation)\n"
            "3. RISK INDICATORS (if any)\n"
            "4. OPEN QUESTIONS (what we still don't know)\n"
            "5. SOURCES (list of URLs/references)\n\n"
            "If evidence is insufficient for any section, say so honestly. "
            "Do NOT fabricate information.\n\n"
            f"Company: {company_name}\n"
            f"Jurisdiction: {report.jurisdiction or 'unknown'}\n\n"
            f"Evidence:\n{evidence_text}"
        )

        llm = _llm.LLMPipeline()
        resp = await asyncio.wait_for(
            llm.complete("", prompt, max_tokens=2000),
            timeout=_PHASE_TIMEOUTS["synthesis"],
        )
        if resp and resp.content:
            report.summary = resp.content[:5000]

        # Extract risk indicators from findings
        for f in report.findings:
            if any(kw in f.summary.lower() for kw in
                   ("sanctions", "hard_stop", "risk", "warning", "negative",
                    "lawsuit", "investigation", "penalty", "fine")):
                if f.title not in report.risk_indicators:
                    report.risk_indicators.append(f.title)

        # Collect cited sources
        for f in report.findings:
            if f.source and f.source not in report.sources_cited:
                report.sources_cited.append(f.source)

    except asyncio.TimeoutError:
        logger.debug("[company_investigator] synthesis timed out")
        report.summary = "Synthesis timed out — raw findings are available."
    except Exception as e:
        logger.debug("[company_investigator] synthesis failed: %s", e)


# ── Wire to brain ──────────────────────────────────────────────────────

try:
    from .engine_wiring import wire_success as _ws
    _ws(
        module="company_investigator",
        summary="Company Investigator Engine active",
        detail="Pipeline: entity_resolution → web_search → deep_crawl → "
               "registry → sanctions → conflict → news → social → "
               "tech_stack → ssl_dns → procurement → synthesis",
        source_id="company_investigator:R-F1061",
    )
except Exception:
    pass
