"""R-F3374 — the subject roster for tool-use corpus capture.

PUBLIC RECORD ONLY. Designated entities, listed companies and state-owned
enterprises. Customer DD subjects are deliberately absent: tenant data in a
system with a history of cross-tenant leaks does not go into model weights
without an operator decision.

Breadth is deliberate — ARIA's field is security, defence, sanctions and KYC
across jurisdictions, so the roster spans Russia/Belarus, Iran, DPRK, Syria,
Myanmar and the Balkans as well as UK/EU/US primes and financial institutions.
Entities present in the frozen 500-Q eval are filtered at build time by the
contamination blocklist, not here, so this list stays a plain roster.
"""
from __future__ import annotations

# Designated / sanctioned entities — expected to produce real hits.
SANCTIONED: list[str] = [
    "Sberbank", "Gazprombank", "Bank Rossiya", "VTB Bank", "Alfa-Bank",
    "Sovcombank", "Novikombank", "Promsvyazbank", "Otkritie Bank",
    "Kalashnikov Concern", "Almaz-Antey", "United Shipbuilding Corporation",
    "Uralvagonzavod", "Tactical Missiles Corporation", "United Aircraft Corporation",
    "Islamic Revolutionary Guard Corps", "Bank Melli Iran", "Bank Saderat Iran",
    "Mahan Air", "Islamic Republic of Iran Shipping Lines",
    "Korea Mining Development Trading Corporation", "Tanchon Commercial Bank",
    "Belaruskali", "Belarusian Potash Company", "Belavia",
    "Myanma Economic Holdings", "Myanmar Economic Corporation",
    "Syrian Petroleum Company", "Commercial Bank of Syria",
    "Aeroflot", "Rosneft", "Transneft", "Rostec", "Sovcomflot",
]

# Listed public companies — expected to be clean; the true-negative half.
LISTED_CLEAN: list[str] = [
    "Tesco plc", "Unilever plc", "Diageo plc", "Siemens AG", "Airbus SE",
    "Rolls-Royce Holdings plc", "Marks and Spencer Group plc", "Sage Group plc",
    "Barclays plc", "HSBC Holdings plc", "Lloyds Banking Group plc",
    "Prudential plc", "Legal & General Group plc", "Aviva plc",
    "GSK plc", "AstraZeneca plc", "Reckitt Benckiser Group plc",
    "National Grid plc", "SSE plc", "Centrica plc", "BT Group plc",
    "Vodafone Group plc", "Compass Group plc", "Whitbread plc",
]

# UK-registered defence/security primes — the registry -> officers -> screen chain.
UK_REGISTRY_SUBJECTS: list[str] = [
    "Rolls-Royce Holdings plc", "Babcock International Group plc",
    "QinetiQ Group plc", "Serco Group plc", "Chemring Group plc",
    "Meggitt plc", "Ultra Electronics Holdings plc", "Melrose Industries plc",
    "Smiths Group plc", "Cobham Limited", "Tesco plc", "Unilever plc",
    "Diageo plc", "BT Group plc", "National Grid plc", "SSE plc",
    "Compass Group plc", "Whitbread plc", "Sage Group plc", "Aviva plc",
    "Barclays plc", "Prudential plc", "Centrica plc", "AstraZeneca plc",
]

# SHORT / partial names — what operators actually type, and where the register's
# relevance ranking is dangerous (R-F3372).
AMBIGUOUS_SHORT: list[str] = [
    "Chemring", "Babcock", "QinetiQ", "Cobham", "Serco", "Meggitt",
    "Ultra Electronics", "Smiths Group", "Melrose", "Diageo", "Tesco",
    "Unilever", "Rolls-Royce", "Barclays", "Prudential", "Centrica",
    "National Grid", "Compass", "Sage", "Aviva",
]

# Entities to interpret current reporting about (news -> exposure).
NEWS_SUBJECTS: list[str] = [
    "Rolls-Royce Holdings plc", "BAE Systems", "Babcock International Group plc",
    "QinetiQ Group plc", "Serco Group plc", "Chemring Group plc",
    "Thales Group", "Leonardo S.p.A.", "Saab AB", "Rheinmetall AG",
    "Airbus SE", "Boeing", "Lockheed Martin", "Northrop Grumman",
    "Sberbank", "Rosneft", "Wagner Group", "Gazprom",
]
