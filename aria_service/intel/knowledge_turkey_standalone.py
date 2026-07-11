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
from .engine_wiring import wire_failure

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

EXPORT CONTROL + EUC — DETAILED PROCESS

Turkish End-User Certificate (Nihai Kullanıcı Sertifikası):

  The EUC is the foundational document in Turkish defence export control.
  It is the buyer-state government's written undertaking to the Turkish
  exporter, countersigned by Turkish authorities, that the equipment
  will (a) be used only by the declared end-user, (b) remain in the
  declared country of end-use, and (c) not be re-exported or re-
  transferred without prior Turkish written consent.

  Process flow:
    1. Turkish OEM (Baykar, ASELSAN, Roketsan, etc.) applies to SSB
       for export approval via the SSB online export-licence portal,
       citing the end-user country + specific platform/quantity.
    2. Buyer-state MoD (or authorised authority) signs a formal EUC on
       its own letterhead, stamped and dated, naming the end-user unit.
       Template typically includes re-export prohibition language and a
       "if disposed of, return to Türkiye" clause.
    3. Turkish MSB (Milli Savunma Bakanlığı) reviews the EUC + SSB
       recommendation. Inter-agency vetting for strategic alignment.
    4. SSB İcra Komitesi (executive committee) issues formal approval.
       Decision published in Resmî Gazete within days — public.
    5. Turkish customs releases the shipment only after (a) export
       licence issued, (b) EUC copy on file.

  Validity + conditions:
    - EUC is transaction-specific (not a framework)
    - Re-export requires a written SSB waiver + updated EUC from the
      downstream country — NOT retroactively granted
    - Quantity caps are binding; over-shipment triggers re-licensing
    - Turkish authority reserves the right to audit end-use (in
      practice rarely exercised for non-strategic platforms)

  Re-export to African + Gulf buyers (Arkmurus-relevant cases):
    - African re-transfer: if equipment lands in an African buyer
      (Nigeria, Morocco, Togo, etc.) and the buyer wishes to re-export
      to a third country, a new EUC from the new end-user MUST be
      obtained AND a waiver from SSB. Practical reality: rarely granted
      unless the downstream buyer is in Türkiye's strategic interest.
    - Gulf re-transfer: Tawazun / Edge / SAMI integration contracts
      that involve Turkish components sometimes carry EUC flow-through
      — verify the original EUC permits embedded-component re-export
      before accepting a sub-system assembly mandate.

  Bright-line differences vs. UK ECJU / US EAR:
    - UK ECJU open licences (OGEL) do not exist in Turkish regime —
      every defence transaction is individual-licence
    - US ITAR "significant military equipment" controls don't apply;
      Turkish equivalent is the SSB strategic-items list
    - Turkish regime has no equivalent of US "deemed export" rules
      for foreign-national access within TR facilities — compliance
      lives at the physical-export stage

SSB export-licence lists (published in Resmi Gazete):
  - Useful leading indicator of which OEMs are in advanced export talks
    with which counterparty countries
  - Filter: search "Savunma Sanayii" + target-country name on
    resmigazete.gov.tr — usually 3-7 day lag from İcra Komitesi meeting

  Reading the signal:
    - A licence appears in Resmî Gazete ~2-3 weeks after İcra Komitesi
      approval
    - Appearance implies contract is imminent or already signed
    - Non-appearance is NOT evidence of non-approval — some sensitive
      items are redacted from the published list

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
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="knowledge_turkey_standalone",
        summary="Get Turkey Context",
        source_id="knowledge_turkey_standalone:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
