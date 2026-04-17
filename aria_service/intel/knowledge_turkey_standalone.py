"""ARIA Turkey Knowledge Module — explicitly standalone (not NATO).

Per 2026-04-17 strategic directive (memory: `heatmap_expansion_regions.md`):
Turkey must be its OWN heat map column. SSB operates independently of NATO
procurement. Turkey sells to markets NATO members will not — which is
exactly the positioning gap Arkmurus occupies. Treating Turkey only
through a NATO lens misses the commercial thesis.

Public sources only. The commercial specifics move fast (Baykar export
deals, SSB approvals) — ARIA cites live tender_monitor and
competitor_tracker for those rather than baking them in here.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_turkey_standalone")


TURKEY_STANDALONE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — TURKEY DEFENCE LANDSCAPE (STANDALONE, NOT NATO-FRAMED)
═══════════════════════════════════════════════════════════════════════

CORE INSTITUTIONAL STACK

SSB — Savunma Sanayii Başkanlığı (Presidency of Defence Industries):
  - Reports directly to the President of the Republic (Erdoğan since 2018)
  - Primary authority for defence procurement planning, industrial
    strategy, export approvals, and OEM relationships
  - Operates independently of NATO procurement — Turkey's defence
    posture is not NATO-aligned in practice
  - Website: https://www.ssb.gov.tr/

Ministry of National Defence (MSB — Millî Savunma Bakanlığı):
  - Operational defence policy; Turkish Armed Forces administration

Resmi Gazete — the Turkish Official Gazette:
  - Publishes SSB announcements, export-licence lists, defence
    legislation. Monitor: https://www.resmigazete.gov.tr/

KEY OEMs

Baykar:
  - Privately held (Bayraktar family) — fastest-rising UAV exporter globally
  - Products: TB2 (operational in 30+ countries), Akıncı (HALE),
    Kızılelma (unmanned fighter), DIHA
  - Africa footprint: Angola, Mali, Niger, Burkina Faso, Chad,
    Morocco, Nigeria, Djibouti, Togo

ASELSAN:
  - State-affiliated electronics + radar + EW + C4ISR integrator
  - Platform integration across Turkish Army, Air Force, Navy
  - Growing export success in GCC (Saudi / UAE) and Asia (Pakistan,
    Malaysia, Indonesia)

Roketsan:
  - Missiles, rockets, smart munitions (MAM, L-UMTAS)
  - Tactical ballistic missiles (SOM, ATMACA anti-ship)

TAI / Turkish Aerospace Industries:
  - KAAN (5th-gen fighter programme), Hürjet (trainer), T129 ATAK helicopter
  - Airframes + MRO

STM (Savunma Teknolojileri Mühendislik):
  - Naval + UAV integration (KARGU loitering munition)

FNSS, Otokar, BMC:
  - Armoured vehicles (Pars, Kaplan, ALTAY main battle tank)

EXPORT CONTROL + EUC

Turkish End-User Certificate (Nihai Kullanıcı Sertifikası):
  - Required for defence-equipment transactions
  - Issued through MSB / SSB chain
  - Re-export restrictions apply per contract terms; differ from UK ECJU
    or US EAR/ITAR in both process and bright lines

SSB export-licence lists (published in Resmi Gazete):
  - Useful leading indicator of which OEMs are in advanced export talks
    with which counterparty countries

COMMERCIAL POSITIONING GAP

Turkey is selling to markets NATO members will not:
  - African markets under AES Alliance (Mali, Burkina Faso, Niger)
  - Sanctions-adjacent markets with tolerance for Turkish suppliers
  - Markets where US / European OEMs are constrained by end-use controls
This is the exact commercial positioning gap Arkmurus occupies, and is
why Turkey must be treated as its own market (not collapsed into NATO).

KEY MEDIA + MONITORING SOURCES

  - Savunma Sanayii Dergisi (Turkish defence industry journal)
  - Defence Turkey (English-language trade press)
  - Daily Sabah Defence section
  - SSB press announcements (resmigazete.gov.tr mirror)
  - Industrial Days (annual, Ankara) + IDEF (biennial, Istanbul)
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "turkey_standalone_landscape",
        "text": TURKEY_STANDALONE_LANDSCAPE,
        "keywords": [
            "turkey", "türkiye", "turkish",
            "ssb", "savunma sanayii", "msb", "milli savunma",
            "baykar", "bayraktar", "tb2", "akinci", "kizilelma",
            "aselsan", "roketsan", "tai", "stm", "fnss", "otokar", "bmc",
            "kaan", "hurjet", "atak", "altay", "atmaca",
            "euc turkey", "nihai kullanici", "turkish eud",
            "idef", "savunma sanayii dergisi", "resmi gazete",
        ],
    },
]


def get_turkey_context(query: str, max_blocks: int = 1) -> str:
    """Return Turkey-specific knowledge (standalone, NOT NATO-framed)."""
    if not query or not query.strip():
        return ""
    q = query.lower().strip()
    tokens = set(q.split())
    scored: list[tuple[int, dict]] = []
    for entry in _SEARCH_INDEX:
        score = 0
        for kw in entry["keywords"]:
            if kw in q:
                score += 2
            elif any(t in kw for t in tokens):
                score += 1
        if score:
            scored.append((score, entry))
    if not scored:
        return ""
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
