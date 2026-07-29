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
    # R-F3396 — the registry -> officers -> screen chain is UK-only by
    # construction (it walks Companies House), so breadth here means more UK
    # defence-sector filings, including subsidiaries of foreign primes where
    # the officer chain crosses a border.
    "BAE Systems plc", "MBDA UK Limited", "Leonardo MW Ltd",
    "Thales UK Limited", "General Dynamics United Kingdom Limited",
    "Lockheed Martin UK Limited", "Raytheon Systems Limited",
    "Airbus Operations Limited", "GKN Aerospace Services Limited",
    "Devonport Royal Dockyard Limited", "Rolls-Royce Submarines Limited",
    "BMT Group Ltd", "Frazer-Nash Consultancy Limited",
    "Roke Manor Research Limited", "QinetiQ Limited",
    "Supacat Limited", "Pearson Engineering Limited",
    "Survitec Group Limited", "Marshall of Cambridge (Holdings) Limited",
    "Northrop Grumman UK Limited",
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
    # R-F3396 — widen beyond UK/Russia so news interpretation is not a
    # two-jurisdiction skill.
    "Dassault Aviation", "Hanwha Aerospace", "Korea Aerospace Industries",
    "Mitsubishi Heavy Industries", "Israel Aerospace Industries",
    "Elbit Systems", "Embraer", "Aselsan", "Baykar",
    "Hindustan Aeronautics Limited", "RTX Corporation", "General Dynamics",
    "L3Harris Technologies", "Huawei Technologies", "Hikvision",
    "Deutsche Bank AG", "Danske Bank", "Swedbank",
    "Raiffeisen Bank International", "Saudi Aramco", "Petrobras",
    "SOCAR", "KazMunayGas", "Eskom",
]

# ---------------------------------------------------------------------------
# R-F3396 — jurisdictional and sectoral breadth.
#
# ARIA's field is security, defence, sanctions and KYC across jurisdictions, and
# the roster was heavily UK + Russia. A model trained on that learns those two
# registries, not the skill. Everything below is public record: listed issuers,
# state-owned enterprises, and designated persons.
#
# NOTHING HERE ASSERTS A VERDICT. Every trace's answer is derived from the tool
# payload actually returned and checked by `validate_trace`, so an entity that
# turns out to be clean produces a clean trace and an entity that is designated
# produces a hit. The roster decides what gets ASKED, never what gets answered.
# ---------------------------------------------------------------------------

# Non-UK defence primes — the true-negative half needs to span more than Britain.
INTERNATIONAL_PRIMES: list[str] = [
    "Thales Group", "Leonardo S.p.A.", "Saab AB", "Rheinmetall AG",
    "Dassault Aviation", "Naval Group", "Hanwha Aerospace",
    "Korea Aerospace Industries", "Mitsubishi Heavy Industries",
    "Kawasaki Heavy Industries", "Israel Aerospace Industries",
    "Elbit Systems", "Rafael Advanced Defense Systems", "Embraer",
    "Turkish Aerospace Industries", "Aselsan", "Baykar",
    "Bharat Electronics Limited", "Hindustan Aeronautics Limited",
    "General Dynamics", "RTX Corporation", "L3Harris Technologies",
    "Leidos Holdings", "Northrop Grumman", "Lockheed Martin",
]

# Financial institutions — KYC breadth, including several with real, public
# AML enforcement histories (Danske, Swedbank, Raiffeisen) so adverse-media
# reasoning meets genuine reporting rather than only clean names.
FINANCIAL_INSTITUTIONS: list[str] = [
    "Deutsche Bank AG", "BNP Paribas", "Societe Generale", "UniCredit S.p.A.",
    "Banco Santander", "ING Groep", "Nordea Bank", "Danske Bank",
    "Swedbank", "Raiffeisen Bank International", "Emirates NBD",
    "First Abu Dhabi Bank", "Qatar National Bank", "Saudi National Bank",
    "Standard Bank Group", "Absa Group", "Itau Unibanco", "Banco do Brasil",
    "DBS Group Holdings", "Oversea-Chinese Banking Corporation",
    "Mizuho Financial Group", "Mitsubishi UFJ Financial Group",
    "Industrial and Commercial Bank of China", "Bank of China",
]

# State-owned enterprises — where ownership, control and PEP exposure actually
# live, across Gulf, Asia, Africa and LatAm.
STATE_OWNED_ENTERPRISES: list[str] = [
    "Saudi Aramco", "Abu Dhabi National Oil Company", "QatarEnergy",
    "Petronas", "Pertamina", "Sonatrach", "Nigerian National Petroleum Company",
    "Sonangol", "Petrobras", "Pemex", "Codelco", "Eskom", "Transnet",
    "KazMunayGas", "SOCAR", "Turkiye Petrolleri", "PetroChina", "Sinopec",
    "China National Offshore Oil Corporation", "Uzbekneftegaz",
]

# Designated persons — public record. The PEP/individual axis: screening a
# person is not the same shape of task as screening a company, and a corpus of
# companies alone never teaches it.
DESIGNATED_PERSONS: list[str] = [
    "Alisher Usmanov", "Roman Abramovich", "Oleg Deripaska",
    "Viktor Vekselberg", "Igor Sechin", "Nikolai Patrushev",
    "Ramzan Kadyrov", "Alexander Lukashenko", "Min Aung Hlaing",
    "Bashar al-Assad", "Mikhail Fridman", "Petr Aven",
    "Andrey Kostin", "German Gref", "Yevgeny Prigozhin",
    "Konstantin Malofeev", "Dmitry Rogozin", "Sergey Chemezov",
]

# Technology / dual-use entities that appear on export-control and investment
# restriction lists rather than the classic sanctions files — a different
# regime, and a screen that returns "no OFAC match" for one of these is a
# TRUE negative the model must not over-read as "clean".
DUAL_USE_TECH: list[str] = [
    "Huawei Technologies", "Semiconductor Manufacturing International Corporation",
    "Hikvision", "Dahua Technology", "SenseTime Group", "DJI",
    "Inspur Group", "Sugon", "Yangtze Memory Technologies",
    "China Electronics Technology Group Corporation",
]


def single_hop_roster() -> list[str]:
    """Subjects for the base screen->answer axis.

    ONE ROSTER, NOT TWO. This axis previously carried its own hardcoded
    fourteen-name list inside build_tooluse_corpus, which could drift from this
    file without anything noticing — the same producer/consumer split that has
    bitten this repo repeatedly. It now draws from the same source as every
    other axis, spanning designated entities, clean issuers, persons,
    state-owned enterprises and dual-use tech so the base skill is not learnt
    on one jurisdiction.
    """
    seen: set[str] = set()
    out: list[str] = []
    for group in (SANCTIONED, LISTED_CLEAN, DESIGNATED_PERSONS,
                  STATE_OWNED_ENTERPRISES, INTERNATIONAL_PRIMES,
                  FINANCIAL_INSTITUTIONS, DUAL_USE_TECH):
        for s in group:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out
