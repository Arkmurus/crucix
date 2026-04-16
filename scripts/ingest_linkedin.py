#!/usr/bin/env python3
"""
LinkedIn Data Export → ARIA Intelligence Ingestion Script

Parses a LinkedIn Complete Data Export and feeds high-value data into
ARIA's contact intelligence, competitor tracking, and knowledge base.

Usage:
    python scripts/ingest_linkedin.py /path/to/linkedin_export_dir

    Or with a zip file:
    python scripts/ingest_linkedin.py /path/to/Complete_LinkedInDataExport.zip

What gets ingested:
    1. Connections.csv → Contact Intelligence (defence-relevant contacts)
    2. Company Follows.csv → Competitor/OEM monitoring list
    3. Profile.csv + Positions.csv → Owner context (knowledge base)

What gets SKIPPED (by design):
    - messages.csv (sensitive — manual review required)
    - ImportedContacts.csv (too noisy, phone contacts)
    - Ads, Reactions, Shares, Learning (irrelevant to BD intelligence)
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Ensure the project root is importable
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("linkedin_ingest")


# =============================================================================
# DEFENCE RELEVANCE FILTERS
# =============================================================================

# Keywords that indicate a connection is defence/security/procurement relevant.
# Matched case-insensitively against Company + Position fields.
_DEFENCE_KEYWORDS = re.compile(
    r"\b(?:"
    r"defen[cs]e|military|army|navy|air\s+force|marine|armed\s+forces|"
    r"procurement|arms|weapon|missile|drone|uav|uas|munition|ammunition|"
    r"security|intelligence|osint|nato|aerospace|"
    r"ministry\s+of|attaché|attache|ambassador|consul|diplomat|"
    r"arkmurus|citysec|"
    # Major OEMs
    r"rafael|hanwha|elbit|lockheed|rheinmetall|bae\s+system|thales|"
    r"leonardo|mbda|general\s+dynamics|northrop|kongsberg|"
    r"aselsan|fnss|otokar|baykar|turkish\s+aerospace|roketsan|"
    r"embraer|saab|airbus|boeing|raytheon|safran|"
    r"denel|paramount|roshel|"
    # Industry terms
    r"broker|export\s+control|sanctions|compliance|"
    r"tender|contract|euc|sitcl|ecju|"
    r"c-uas|counter.?drone|armou?red|naval|shipbuild|"
    r"cyber|surveillance|c4isr|electronic\s+warfare"
    r")\b",
    re.IGNORECASE,
)

# Keywords for HIGH importance classification
_HIGH_IMPORTANCE_KEYWORDS = re.compile(
    r"\b(?:"
    r"minister|director|vp|vice\s+president|chief|ceo|cto|cfo|coo|"
    r"head\s+of|general\s+manager|managing\s+director|"
    r"commander|general|admiral|colonel|brigadier|"
    r"ambassador|attaché|attache|"
    r"arkmurus"
    r")\b",
    re.IGNORECASE,
)

# Company names that indicate competitor/OEM relevance for Company Follows
_OEM_KEYWORDS = re.compile(
    r"\b(?:"
    r"defen[cs]e|military|army|navy|aerospace|arms|weapon|"
    r"drone|uav|uas|c-uas|counter.?drone|"
    r"armou?red|naval|shipbuild|ammunition|munition|"
    r"security|surveillance|cyber|intelligence|"
    r"procurement|tender|expo|summit|conference|"
    r"rafael|hanwha|elbit|lockheed|rheinmetall|bae|thales|"
    r"leonardo|mbda|kongsberg|aselsan|fnss|otokar|baykar|"
    r"embraer|saab|airbus|boeing|raytheon|safran|sikorsky|"
    r"denel|paramount|roshel|photonis|"
    r"nato|sipri|iiss|janes"
    r")\b",
    re.IGNORECASE,
)


# =============================================================================
# COUNTRY INFERENCE FROM COMPANY/POSITION/NAME
# =============================================================================

_COUNTRY_HINTS = {
    "angola": "AO", "angolan": "AO",
    "mozambiq": "MZ",
    "nigeria": "NG", "nigerian": "NG",
    "ghana": "GH", "ghanaian": "GH",
    "kenya": "KE", "kenyan": "KE",
    "turkey": "TR", "turkish": "TR", "türk": "TR",
    "brazil": "BR", "brazilian": "BR", "brasil": "BR",
    "saudi": "SA",
    "uae": "AE", "emirates": "AE", "dubai": "AE", "abu dhabi": "AE",
    "estonia": "EE", "estonian": "EE",
    "israel": "IL", "israeli": "IL",
    "south africa": "ZA",
    "south korea": "KR", "korean": "KR",
    "south sudan": "SS",
    "sudan": "SD",
    "uk": "GB", "united kingdom": "GB", "british": "GB", "london": "GB",
    "usa": "US", "united states": "US", "american": "US",
    "germany": "DE", "german": "DE",
    "france": "FR", "french": "FR",
    "poland": "PL", "polish": "PL",
    "romania": "RO", "romanian": "RO",
    "czech": "CZ",
    "slovakia": "SK", "slovak": "SK",
    "hungary": "HU", "hungarian": "HU",
    "netherlands": "NL", "dutch": "NL",
    "portugal": "PT", "portuguese": "PT",
    "ukraine": "UA", "ukrainian": "UA",
    "pakistan": "PK",
    "india": "IN", "indian": "IN",
    "indonesia": "ID",
    "japan": "JP", "japanese": "JP",
    "china": "CN", "chinese": "CN",
    "russia": "RU", "russian": "RU",
    "italy": "IT", "italian": "IT",
    "spain": "ES", "spanish": "ES",
    "canada": "CA", "canadian": "CA",
    "australia": "AU", "australian": "AU",
    "kosovo": "XK",
    "albania": "AL",
    "bosnia": "BA", "sarajevo": "BA",
    "bangladesh": "BD",
}


def _infer_country(company: str, position: str) -> str:
    """Try to infer ISO2 country from company name or position text."""
    text = f"{company} {position}".lower()
    for hint, iso2 in _COUNTRY_HINTS.items():
        if hint in text:
            return iso2
    return ""


# =============================================================================
# TAG GENERATION
# =============================================================================

_TAG_PATTERNS = [
    (re.compile(r"\bprocurement\b", re.I), "procurement"),
    (re.compile(r"\bnavy|naval\b", re.I), "naval"),
    (re.compile(r"\barmy|armou?red\b", re.I), "land"),
    (re.compile(r"\bair\s+force|aerospace|aviation\b", re.I), "air"),
    (re.compile(r"\bdrone|uav|uas|c-uas\b", re.I), "uav"),
    (re.compile(r"\bcyber|surveillance\b", re.I), "cyber"),
    (re.compile(r"\bsanctions|compliance|export\s+control|ecju\b", re.I), "compliance"),
    (re.compile(r"\bintelligence|osint\b", re.I), "intelligence"),
    (re.compile(r"\bminister|government|ministry\b", re.I), "government"),
    (re.compile(r"\bexpo|conference|summit|exhibition\b", re.I), "events"),
    (re.compile(r"\bconsult\b", re.I), "consulting"),
]


def _generate_tags(company: str, position: str) -> list[str]:
    """Generate categorisation tags from company and position."""
    text = f"{company} {position}"
    tags = ["linkedin_import"]
    for pattern, tag in _TAG_PATTERNS:
        if pattern.search(text):
            tags.append(tag)
    return tags


# =============================================================================
# CSV PARSERS
# =============================================================================

@dataclass
class LinkedInConnection:
    first_name: str
    last_name: str
    url: str
    email: str
    company: str
    position: str
    connected_on: str
    # Derived
    is_defence_relevant: bool = False
    importance: str = "NORMAL"
    country: str = ""
    tags: list[str] = None

    def __post_init__(self):
        combined = f"{self.company} {self.position}"
        self.is_defence_relevant = bool(_DEFENCE_KEYWORDS.search(combined))
        if self.is_defence_relevant and _HIGH_IMPORTANCE_KEYWORDS.search(combined):
            self.importance = "HIGH"
        self.country = _infer_country(self.company, self.position)
        self.tags = _generate_tags(self.company, self.position)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class CompanyFollow:
    organization: str
    followed_on: str
    is_oem_relevant: bool = False

    def __post_init__(self):
        self.is_oem_relevant = bool(_OEM_KEYWORDS.search(self.organization))


def _read_csv(path: str, skip_notes: bool = False) -> list[dict]:
    """Read a CSV file, optionally skipping LinkedIn's Notes header lines."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    if skip_notes:
        # Connections.csv has 3 lines of notes before the actual header
        lines = content.split("\n")
        # Find the header line (starts with "First Name,")
        for i, line in enumerate(lines):
            if line.startswith("First Name,"):
                content = "\n".join(lines[i:])
                break

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def parse_connections(export_dir: str) -> list[LinkedInConnection]:
    """Parse Connections.csv and return structured connection objects."""
    path = os.path.join(export_dir, "Connections.csv")
    if not os.path.exists(path):
        logger.warning("Connections.csv not found in %s", export_dir)
        return []

    rows = _read_csv(path, skip_notes=True)
    connections = []
    for row in rows:
        conn = LinkedInConnection(
            first_name=row.get("First Name", "").strip(),
            last_name=row.get("Last Name", "").strip(),
            url=row.get("URL", "").strip(),
            email=row.get("Email Address", "").strip(),
            company=row.get("Company", "").strip(),
            position=row.get("Position", "").strip(),
            connected_on=row.get("Connected On", "").strip(),
        )
        if conn.full_name:
            connections.append(conn)

    logger.info(
        "Parsed %d connections (%d defence-relevant)",
        len(connections),
        sum(1 for c in connections if c.is_defence_relevant),
    )
    return connections


def parse_company_follows(export_dir: str) -> list[CompanyFollow]:
    """Parse Company Follows.csv and return structured follow objects."""
    path = os.path.join(export_dir, "Company Follows.csv")
    if not os.path.exists(path):
        logger.warning("Company Follows.csv not found in %s", export_dir)
        return []

    rows = _read_csv(path)
    follows = []
    for row in rows:
        org = row.get("Organization", "").strip()
        if org:
            follows.append(CompanyFollow(
                organization=org,
                followed_on=row.get("Followed On", "").strip(),
            ))

    logger.info(
        "Parsed %d company follows (%d OEM/defence-relevant)",
        len(follows),
        sum(1 for f in follows if f.is_oem_relevant),
    )
    return follows


# =============================================================================
# ARIA INGESTION
# =============================================================================

async def ingest_connections(connections: list[LinkedInConnection]) -> dict:
    """Ingest defence-relevant connections into ARIA contact intelligence."""
    from aria_service.intel import contact_intelligence

    stats = {"total": 0, "ingested": 0, "skipped": 0, "errors": 0, "high_value": 0}
    defence_connections = [c for c in connections if c.is_defence_relevant]
    stats["total"] = len(defence_connections)

    for conn in defence_connections:
        try:
            contact = await contact_intelligence.add_contact(
                name=conn.full_name,
                org=conn.company,
                role=conn.position,
                country=conn.country,
                email=conn.email,
                source="linkedin",
                importance=conn.importance,
                tags=conn.tags,
            )
            stats["ingested"] += 1
            if conn.importance == "HIGH":
                stats["high_value"] += 1
            logger.debug(
                "  ✓ %s — %s at %s [%s] %s",
                conn.full_name, conn.position, conn.company,
                conn.importance, conn.country or "??",
            )
        except Exception as e:
            stats["errors"] += 1
            logger.warning("  ✗ %s: %s", conn.full_name, e)

    logger.info(
        "Contact ingestion complete: %d/%d ingested (%d HIGH, %d errors)",
        stats["ingested"], stats["total"], stats["high_value"], stats["errors"],
    )
    return stats


async def ingest_company_follows(follows: list[CompanyFollow]) -> dict:
    """Ingest defence-relevant company follows into ARIA knowledge base."""
    from aria_service.intel import knowledge

    stats = {"total": 0, "ingested": 0, "skipped": 0, "errors": 0}
    oem_follows = [f for f in follows if f.is_oem_relevant]
    stats["total"] = len(oem_follows)

    for follow in oem_follows:
        try:
            await knowledge.store_fact(
                topic=f"OEM Watch: {follow.organization}",
                content=(
                    f"{follow.organization} is on Arkmurus's LinkedIn watchlist "
                    f"(followed {follow.followed_on}). Monitor for contract awards, "
                    f"market entries, partnerships, and competitive moves."
                ),
                source="linkedin_import",
                confidence="CONFIRMED",
                entity_name=follow.organization,
                entity_type="company",
            )
            stats["ingested"] += 1
            logger.debug("  ✓ %s", follow.organization)
        except Exception as e:
            stats["errors"] += 1
            logger.warning("  ✗ %s: %s", follow.organization, e)

    logger.info(
        "Company follows ingestion: %d/%d ingested (%d errors)",
        stats["ingested"], stats["total"], stats["errors"],
    )
    return stats


async def ingest_all_connections_as_knowledge(connections: list[LinkedInConnection]) -> dict:
    """Store ALL connections (not just defence) as a network overview fact."""
    from aria_service.intel import knowledge

    # Group by company for a network summary
    by_company: dict[str, list[str]] = {}
    for conn in connections:
        if conn.company:
            by_company.setdefault(conn.company, []).append(conn.full_name)

    # Top companies by connection count
    top_companies = sorted(by_company.items(), key=lambda kv: len(kv[1]), reverse=True)[:30]

    summary_lines = [
        f"Antonio's LinkedIn network: {len(connections)} connections.",
        f"Defence-relevant: {sum(1 for c in connections if c.is_defence_relevant)}.",
        "",
        "Top companies by connection count:",
    ]
    for company, names in top_companies:
        summary_lines.append(f"  - {company}: {len(names)} ({', '.join(names[:3])}{'...' if len(names) > 3 else ''})")

    await knowledge.store_fact(
        topic="Antonio LinkedIn Network Overview",
        content="\n".join(summary_lines),
        source="linkedin_import",
        confidence="CONFIRMED",
    )

    logger.info("Stored LinkedIn network overview (top %d companies)", len(top_companies))
    return {"top_companies": len(top_companies), "total_connections": len(connections)}


async def notify_brain(connections_stats: dict, follows_stats: dict) -> None:
    """Notify ARIA's brain hook about the bulk ingestion."""
    try:
        from aria_service.intel import brain_hook
        await brain_hook.absorb(
            module="person_resolver",
            summary=(
                f"LinkedIn bulk import: {connections_stats['ingested']} defence contacts ingested "
                f"({connections_stats['high_value']} HIGH importance). "
                f"{follows_stats['ingested']} OEM/competitor companies added to watchlist."
            ),
            success=True,
            confidence="CONFIRMED",
            extra_topics=["relationships", "competitor_intel"],
        )
    except Exception as e:
        logger.debug("Brain hook notification failed (non-fatal): %s", e)


# =============================================================================
# MAIN
# =============================================================================

def _resolve_export_dir(path: str) -> str:
    """If path is a zip, extract to temp dir. Otherwise return as-is."""
    if path.endswith(".zip"):
        import tempfile
        extract_dir = tempfile.mkdtemp(prefix="linkedin_export_")
        logger.info("Extracting zip to %s", extract_dir)
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(extract_dir)
        return extract_dir
    return path


async def main(export_path: str) -> None:
    """Main ingestion pipeline."""
    export_dir = _resolve_export_dir(export_path)

    logger.info("=" * 60)
    logger.info("LinkedIn → ARIA Ingestion")
    logger.info("Source: %s", export_path)
    logger.info("=" * 60)

    # Parse
    connections = parse_connections(export_dir)
    follows = parse_company_follows(export_dir)

    if not connections and not follows:
        logger.error("No data found. Check the export path.")
        return

    # Preview defence-relevant connections
    defence = [c for c in connections if c.is_defence_relevant]
    high_value = [c for c in defence if c.importance == "HIGH"]

    logger.info("")
    logger.info("=== DEFENCE-RELEVANT CONTACTS (%d) ===", len(defence))
    for c in sorted(defence, key=lambda x: x.importance == "HIGH", reverse=True):
        marker = "★" if c.importance == "HIGH" else " "
        logger.info(
            "  %s %s — %s at %s [%s] %s",
            marker, c.full_name, c.position[:50], c.company[:40],
            c.country or "??", c.email or "",
        )

    logger.info("")
    logger.info("=== OEM/DEFENCE COMPANY FOLLOWS (%d) ===",
                sum(1 for f in follows if f.is_oem_relevant))
    for f in follows:
        if f.is_oem_relevant:
            logger.info("  • %s", f.organization)

    logger.info("")
    logger.info("Summary: %d defence contacts (%d HIGH), %d OEM companies",
                len(defence), len(high_value),
                sum(1 for f in follows if f.is_oem_relevant))
    logger.info("")

    # Ingest
    logger.info("Ingesting into ARIA...")
    conn_stats = await ingest_connections(connections)
    follow_stats = await ingest_company_follows(follows)
    await ingest_all_connections_as_knowledge(connections)
    await notify_brain(conn_stats, follow_stats)

    logger.info("")
    logger.info("=" * 60)
    logger.info("DONE")
    logger.info("  Contacts: %d ingested (%d HIGH importance)",
                conn_stats["ingested"], conn_stats["high_value"])
    logger.info("  Companies: %d ingested", follow_stats["ingested"])
    logger.info("  Errors: %d contacts, %d companies",
                conn_stats["errors"], follow_stats["errors"])
    logger.info("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path-to-linkedin-export-dir-or-zip>")
        print(f"Example: {sys.argv[0]} ~/Downloads/Complete_LinkedInDataExport_04-14-2026.zip.zip")
        sys.exit(1)

    asyncio.run(main(sys.argv[1]))
