"""R-F1008 — ARIA Intel Source Expander.

Adds 100+ new intelligence sources across all categories:
- Defence intelligence
- Sanctions & compliance
- Corporate intelligence
- Geopolitical risk
- Financial intelligence
- Cyber intelligence
- Regional intelligence
- Open source intelligence
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("aria.intel_expander")


@dataclass
class IntelSource:
    """A single intelligence source with metadata."""
    id: str
    name: str
    category: str
    region: str = "global"
    type: str = "api"  # api, web, feed, database, file
    reliability: float = 0.5  # 0.0 to 1.0
    freshness_hours: int = 24
    url: str = ""
    description: str = ""
    enabled: bool = True
    last_fetch: Optional[float] = None
    error_count: int = 0


class IntelSourceExpander:
    """Manages 100+ intelligence sources across all categories."""

    def __init__(self):
        self._sources: dict[str, IntelSource] = {}
        self._load_all_sources()

    def _load_all_sources(self) -> None:
        """Load all 100+ intelligence sources."""
        
        # ═══════════════════════════════════════════════════════════════════════
        # DEFENCE INTELLIGENCE (20 sources)
        # ═══════════════════════════════════════════════════════════════════════
        defence = [
            ("def_001", "SIPRI Arms Transfers Database", "defence", "global", 0.95, 168),
            ("def_002", "SIPRI Military Expenditure Database", "defence", "global", 0.95, 168),
            ("def_003", "IISS Military Balance", "defence", "global", 0.90, 720),
            ("def_004", "Janes Defence Intelligence", "defence", "global", 0.85, 24),
            ("def_005", "UK MOD Defence Intelligence", "defence", "global", 0.80, 24),
            ("def_006", "US DIA Reports", "defence", "global", 0.75, 24),
            ("def_007", "NATO Cooperative Cyber Defence", "defence", "global", 0.80, 168),
            ("def_008", "European Defence Agency", "defence", "europe", 0.75, 168),
            ("def_009", "UN Register of Conventional Arms", "defence", "global", 0.90, 720),
            ("def_010", "Conflict Armament Research", "defence", "global", 0.85, 168),
            ("def_011", "Small Arms Survey", "defence", "global", 0.85, 720),
            ("def_012", "BICC Defence Data", "defence", "global", 0.70, 720),
            ("def_013", "RUSI Defence Analysis", "defence", "global", 0.80, 168),
            ("def_014", "CSIS Defence Programs", "defence", "global", 0.75, 168),
            ("def_015", "FAS Military Analysis", "defence", "global", 0.70, 168),
            ("def_016", "GlobalSecurity.org", "defence", "global", 0.65, 168),
            ("def_017", "Army Recognition", "defence", "global", 0.60, 24),
            ("def_018", "Naval News", "defence", "global", 0.60, 24),
            ("def_019", "Airforce Technology", "defence", "global", 0.55, 24),
            ("def_020", "Defence Blog", "defence", "global", 0.50, 24),
        ]
        for sid, name, cat, region, rel, fresh in defence:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # SANCTIONS & COMPLIANCE (20 sources)
        # ═══════════════════════════════════════════════════════════════════════
        sanctions = [
            ("san_001", "OFAC SDN List", "sanctions", "global", 0.98, 24),
            ("san_002", "OFAC Consolidated List", "sanctions", "global", 0.98, 24),
            ("san_003", "EU Consolidated Sanctions", "sanctions", "global", 0.95, 24),
            ("san_004", "UK OFSI Sanctions List", "sanctions", "global", 0.95, 24),
            ("san_005", "UN Security Council Sanctions", "sanctions", "global", 0.98, 24),
            ("san_006", "UNSC 1718 Committee (DPRK)", "sanctions", "asia", 0.95, 24),
            ("san_007", "UNSC 1267/1989 (ISIL/Al-Qaida)", "sanctions", "global", 0.95, 24),
            ("san_008", "UNSC 1970 (Libya)", "sanctions", "africa", 0.95, 24),
            ("san_009", "UNSC 2140 (Yemen)", "sanctions", "mena", 0.95, 24),
            ("san_010", "Australia DFAT Sanctions", "sanctions", "apac", 0.90, 24),
            ("san_011", "Canada GAC Sanctions", "sanctions", "global", 0.90, 24),
            ("san_012", "Japan METI Sanctions", "sanctions", "asia", 0.85, 24),
            ("san_013", "Switzerland SECO Sanctions", "sanctions", "europe", 0.85, 24),
            ("san_014", "FATF High-Risk Jurisdictions", "sanctions", "global", 0.90, 168),
            ("san_015", "World Bank Debarred Firms", "sanctions", "global", 0.90, 168),
            ("san_016", "EBRB Debarment List", "sanctions", "global", 0.80, 168),
            ("san_017", "ADB Sanctions List", "sanctions", "apac", 0.80, 168),
            ("san_018", "AfDB Debarment List", "sanctions", "africa", 0.75, 168),
            ("san_019", "IDB Sanctions List", "sanctions", "americas", 0.75, 168),
            ("san_020", "BIS Entity List", "sanctions", "global", 0.90, 24),
        ]
        for sid, name, cat, region, rel, fresh in sanctions:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # CORPORATE INTELLIGENCE (15 sources)
        # ═══════════════════════════════════════════════════════════════════════
        corporate = [
            ("corp_001", "OpenCorporates", "corporate", "global", 0.85, 24),
            ("corp_002", "Companies House (UK)", "corporate", "europe", 0.95, 24),
            ("corp_003", "SEC EDGAR", "corporate", "americas", 0.95, 24),
            ("corp_004", "EU Business Registers", "corporate", "europe", 0.80, 168),
            ("corp_005", "Dun & Bradstreet", "corporate", "global", 0.75, 168),
            ("corp_006", "Bloomberg Company Data", "corporate", "global", 0.85, 24),
            ("corp_007", "Reuters Company Profiles", "corporate", "global", 0.80, 24),
            ("corp_008", "Orbis Company Database", "corporate", "global", 0.80, 168),
            ("corp_009", "Zephyr M&A Database", "corporate", "global", 0.75, 168),
            ("corp_010", "Crunchbase", "corporate", "global", 0.60, 24),
            ("corp_011", "PitchBook", "corporate", "global", 0.70, 168),
            ("corp_012", "LinkedIn Company Pages", "corporate", "global", 0.50, 24),
            ("corp_013", "Google Business Profile", "corporate", "global", 0.45, 168),
            ("corp_014", "UK Gazette Notices", "corporate", "europe", 0.85, 24),
            ("corp_015", "EU Official Journal", "corporate", "europe", 0.85, 24),
        ]
        for sid, name, cat, region, rel, fresh in corporate:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # GEOPOLITICAL RISK (15 sources)
        # ═══════════════════════════════════════════════════════════════════════
        geopolitical = [
            ("geo_001", "EIU Country Risk Service", "geopolitical", "global", 0.85, 168),
            ("geo_002", "World Bank Country Data", "geopolitical", "global", 0.95, 720),
            ("geo_003", "IMF Country Reports", "geopolitical", "global", 0.90, 720),
            ("geo_004", "CIA World Factbook", "geopolitical", "global", 0.80, 720),
            ("geo_005", "US State Department Reports", "geopolitical", "global", 0.75, 168),
            ("geo_006", "UK FCDO Travel Advice", "geopolitical", "global", 0.80, 24),
            ("geo_007", "ACLED Conflict Data", "geopolitical", "global", 0.90, 24),
            ("geo_008", "UCDP Conflict Encyclopedia", "geopolitical", "global", 0.95, 720),
            ("geo_009", "ICG Crisis Watch", "geopolitical", "global", 0.85, 168),
            ("geo_010", "HRW World Reports", "geopolitical", "global", 0.75, 168),
            ("geo_011", "Amnesty International Reports", "geopolitical", "global", 0.75, 168),
            ("geo_012", "Transparency International CPI", "geopolitical", "global", 0.85, 720),
            ("geo_013", "Freedom House Index", "geopolitical", "global", 0.80, 720),
            ("geo_014", "Global Peace Index", "geopolitical", "global", 0.80, 720),
            ("geo_015", "WEF Global Risks Report", "geopolitical", "global", 0.75, 720),
        ]
        for sid, name, cat, region, rel, fresh in geopolitical:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # FINANCIAL INTELLIGENCE (10 sources)
        # ═══════════════════════════════════════════════════════════════════════
        financial = [
            ("fin_001", "World Bank Open Data", "financial", "global", 0.95, 168),
            ("fin_002", "IMF Data Portal", "financial", "global", 0.95, 168),
            ("fin_003", "UN Comtrade Trade Data", "financial", "global", 0.90, 720),
            ("fin_004", "WTO Trade Statistics", "financial", "global", 0.90, 720),
            ("fin_005", "OECD Economic Data", "financial", "global", 0.95, 168),
            ("fin_006", "Federal Reserve Economic Data", "financial", "global", 0.95, 24),
            ("fin_007", "ECB Statistical Data", "financial", "europe", 0.95, 24),
            ("fin_008", "Bank for International Settlements", "financial", "global", 0.90, 168),
            ("fin_009", "World Economic Outlook", "financial", "global", 0.85, 720),
            ("fin_010", "Global Financial Integrity", "financial", "global", 0.75, 720),
        ]
        for sid, name, cat, region, rel, fresh in financial:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # REGIONAL INTELLIGENCE (20 sources)
        # ═══════════════════════════════════════════════════════════════════════
        regional = [
            ("reg_001", "African Union Peace & Security", "regional", "africa", 0.70, 24),
            ("reg_002", "ECOWAS Commission", "regional", "africa", 0.65, 168),
            ("reg_003", "SADC Secretariat", "regional", "africa", 0.60, 168),
            ("reg_004", "IGAD Security Reports", "regional", "africa", 0.55, 168),
            ("reg_005", "GCC Secretariat", "regional", "mena", 0.60, 168),
            ("reg_006", "Arab League Reports", "regional", "mena", 0.50, 168),
            ("reg_007", "ASEAN Secretariat", "regional", "apac", 0.65, 168),
            ("reg_008", "SCO Secretariat", "regional", "asia", 0.50, 168),
            ("reg_009", "CSTO Secretariat", "regional", "eurasia", 0.45, 168),
            ("reg_010", "OAS Secretariat", "regional", "americas", 0.65, 168),
            ("reg_011", "MERCOSUR Secretariat", "regional", "americas", 0.55, 168),
            ("reg_012", "Pacific Islands Forum", "regional", "pacific", 0.50, 168),
            ("reg_013", "SAARC Secretariat", "regional", "asia", 0.50, 168),
            ("reg_014", "Shanghai Cooperation Org", "regional", "asia", 0.45, 168),
            ("reg_015", "Collective Security Treaty Org", "regional", "eurasia", 0.45, 168),
            ("reg_016", "Gulf Cooperation Council", "regional", "mena", 0.55, 168),
            ("reg_017", "Arab Maghreb Union", "regional", "africa", 0.40, 168),
            ("reg_018", "EAC Secretariat", "regional", "africa", 0.50, 168),
            ("reg_019", "ECCAS Secretariat", "regional", "africa", 0.45, 168),
            ("reg_020", "CEMAC Commission", "regional", "africa", 0.45, 168),
        ]
        for sid, name, cat, region, rel, fresh in regional:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # CYBER INTELLIGENCE (10 sources)
        # ═══════════════════════════════════════════════════════════════════════
        cyber = [
            ("cyb_001", "MITRE ATT&CK Framework", "cyber", "global", 0.90, 168),
            ("cyb_002", "CISA Advisories", "cyber", "global", 0.85, 24),
            ("cyb_003", "NCSC Threat Reports", "cyber", "global", 0.85, 24),
            ("cyb_004", "ENISA Threat Landscape", "cyber", "europe", 0.80, 168),
            ("cyb_005", "Kaspersky APT Reports", "cyber", "global", 0.70, 168),
            ("cyb_006", "Mandiant Threat Intelligence", "cyber", "global", 0.75, 168),
            ("cyb_007", "Recorded Future", "cyber", "global", 0.70, 24),
            ("cyb_008", "VirusTotal", "cyber", "global", 0.75, 24),
            ("cyb_009", "Shodan", "cyber", "global", 0.65, 24),
            ("cyb_010", "Censys", "cyber", "global", 0.65, 24),
        ]
        for sid, name, cat, region, rel, fresh in cyber:
            self._add_source(sid, name, cat, region, rel, fresh)

        # ═══════════════════════════════════════════════════════════════════════
        # OPEN SOURCE INTELLIGENCE (10 sources)
        # ═══════════════════════════════════════════════════════════════════════
        osint = [
            ("osi_001", "Bellingcat Investigations", "osint", "global", 0.75, 168),
            ("osi_002", "OCCRP Investigations", "osint", "global", 0.80, 168),
            ("osi_003", "ICIJ Offshore Leaks", "osint", "global", 0.85, 720),
            ("osi_004", "Wikileaks", "osint", "global", 0.40, 720),
            ("osi_005", "Global Witness Reports", "osint", "global", 0.75, 168),
            ("osi_006", "Transparency International", "osint", "global", 0.80, 168),
            ("osi_007", "Human Rights Watch", "osint", "global", 0.75, 168),
            ("osi_008", "Amnesty International", "osint", "global", 0.75, 168),
            ("osi_009", "Reporters Without Borders", "osint", "global", 0.70, 168),
            ("osi_010", "Committee to Protect Journalists", "osint", "global", 0.70, 168),
        ]
        for sid, name, cat, region, rel, fresh in osint:
            self._add_source(sid, name, cat, region, rel, fresh)

    def _add_source(self, sid: str, name: str, category: str, region: str, reliability: float, freshness_hours: int) -> None:
        """Add a single source."""
        self._sources[sid] = IntelSource(
            id=sid, name=name, category=category, region=region,
            reliability=reliability, freshness_hours=freshness_hours,
        )

    def get_source(self, source_id: str) -> Optional[IntelSource]:
        """Get a source by ID."""
        return self._sources.get(source_id)

    def get_sources_by_category(self, category: str) -> list[IntelSource]:
        """Get all sources in a category."""
        return [s for s in self._sources.values() if s.category == category]

    def get_sources_by_region(self, region: str) -> list[IntelSource]:
        """Get all sources for a region."""
        return [s for s in self._sources.values() if s.region == region]

    def get_high_reliability_sources(self, min_reliability: float = 0.8) -> list[IntelSource]:
        """Get sources with reliability above threshold."""
        return [s for s in self._sources.values() if s.reliability >= min_reliability]

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about all sources."""
        categories = {}
        regions = {}
        for s in self._sources.values():
            categories[s.category] = categories.get(s.category, 0) + 1
            regions[s.region] = regions.get(s.region, 0) + 1
        
        return {
            "total_sources": len(self._sources),
            "categories": categories,
            "regions": regions,
            "avg_reliability": round(sum(s.reliability for s in self._sources.values()) / len(self._sources), 2) if self._sources else 0,
            "high_reliability": len(self.get_high_reliability_sources()),
        }

    def get_all_sources(self) -> list[dict]:
        """Get all sources as dicts."""
        return [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "region": s.region,
                "reliability": s.reliability,
                "freshness_hours": s.freshness_hours,
            }
            for s in sorted(self._sources.values(), key=lambda x: x.reliability, reverse=True)
        ]

# R-F1008 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="intel_expander", summary="Intel Expander Active", source_id="intel_expander:R-F1008")

# R-F2119 §21a — wire failure handler for intel_expander
try:
    wire_failure(module="intel_expander", detail="module shutdown",
                gap_type="engine_failure", source="intel_expander:shutdown")
except Exception:
    pass
