"""ARIA Capability Domains — the map of what she can know.

Every domain ARIA operates in is tracked with a skill level, confidence
calibration score, last-updated timestamp, and known gaps. This is her
self-model — the foundation for all metacognitive operations.

Designed for expansion: add new domains by appending to CAPABILITY_DOMAINS.
The calibration engine, gap detector, and consciousness map all discover
domains dynamically from this registry.
"""
from __future__ import annotations

CAPABILITY_DOMAINS: dict[str, dict] = {

    # ── INTELLIGENCE METHODOLOGY ────────────────────────────────────────
    "osint_methodology": {
        "description": "Open source intelligence collection and analysis",
        "sub_domains": [
            "source verification", "entity tracing", "network mapping",
            "digital footprint analysis", "disinformation detection",
            "maritime OSINT", "aviation OSINT", "satellite imagery analysis",
            "social media intelligence", "financial intelligence",
        ],
        "reference_standards": [
            "CIA Tradecraft Primer",
            "Bellingcat methodology guides",
            "NATO OSINT doctrine",
        ],
        "weight": 1.0,  # importance multiplier for aggregate scoring
    },

    "intelligence_analysis": {
        "description": "Structured analytic techniques and reasoning",
        "sub_domains": [
            "ACH (Analysis of Competing Hypotheses)",
            "PMESII framework", "red teaming", "scenario planning",
            "confidence calibration", "cognitive bias detection",
            "key assumptions check", "indicators and warnings",
            "pre-mortem analysis", "devil's advocacy",
        ],
        "reference_standards": [
            "CIA Tradecraft Primer",
            "Heuer - Psychology of Intelligence Analysis",
            "US Army Red Team Handbook",
        ],
        "weight": 1.0,
    },

    # ── DOMAIN KNOWLEDGE ────────────────────────────────────────────────
    "military_hardware": {
        "description": "Military equipment technical knowledge",
        "sub_domains": [
            "small arms and ammunition", "armoured vehicles",
            "artillery and indirect fire", "air defence systems",
            "military aviation", "naval systems", "UAS/drone systems",
            "electronic warfare", "C4ISR systems",
            "export control classification per equipment type",
        ],
        "reference_standards": [
            "Jane's Defence Equipment",
            "Army Recognition database",
            "SIPRI Arms Transfers data",
            "Oryx combat verification",
        ],
        "weight": 1.2,
    },

    "lusophone_africa_geopolitics": {
        "description": "CPLP and Lusophone Africa political and security dynamics",
        "sub_domains": [
            "Angola political economy", "Mozambique security situation",
            "Guinea-Bissau institutional fragility",
            "Cape Verde maritime domain",
            "CPLP defence cooperation framework",
            "Exercicio Felino history",
            "Brazilian influence in CPLP Africa",
            "Chinese influence in CPLP Africa",
            "Russian legacy relationships",
            "French competition in adjacent markets",
        ],
        "reference_standards": [
            "ISS Africa publications",
            "Crisis Group Africa reports",
            "CPLP official documents",
        ],
        "weight": 1.5,  # core domain — weighted highest
    },

    "export_control_compliance": {
        "description": "UK, US, EU and international export control law",
        "sub_domains": [
            "UK SITCL requirements",
            "ECJU licence types and application",
            "ITAR categories and brokering rules",
            "Wassenaar ML categories",
            "EU Common Position on arms exports",
            "UN arms embargoes",
            "End-user certification requirements",
            "SAR obligations (NCA)",
            "OFAC/HMT/EU sanctions application",
            "Dual-use goods classification",
        ],
        "reference_standards": [
            "ECJU Annual Reports",
            "GOV.UK export control guidance",
            "DDTC ITAR regulations (22 CFR 120-130)",
            "Wassenaar control lists",
        ],
        "weight": 1.3,
    },

    "due_diligence_investigation": {
        "description": "Corporate and counterparty investigation methodology",
        "sub_domains": [
            "UBO tracing", "shell company detection",
            "ghost entity identification",
            "PEP screening", "sanctions screening",
            "AML typology application",
            "financial forensics", "digital footprint assessment",
            "source credibility evaluation",
            "network relationship mapping",
        ],
        "reference_standards": [
            "FATF guidance on due diligence",
            "Egmont Group typologies",
            "UNODC financial crime guidance",
        ],
        "weight": 1.2,
    },

    "world_geopolitics": {
        "description": "Global strategic affairs and power dynamics",
        "sub_domains": [
            "US foreign and defence policy",
            "Russia strategic posture",
            "China global strategy",
            "NATO cohesion and capabilities",
            "Middle East security dynamics",
            "Sub-Saharan Africa conflict patterns",
            "Indo-Pacific competition",
            "European defence integration",
            "Turkey strategic positioning",
            "Non-state armed actor analysis",
        ],
        "reference_standards": [
            "IISS Military Balance",
            "RAND geopolitical research",
            "CFR Global Conflict Tracker",
            "NIC Global Trends",
        ],
        "weight": 0.8,
    },

    # ── PROFESSIONAL SKILLS ─────────────────────────────────────────────
    "research_methodology": {
        "description": "How to research — hierarchy, triangulation, gaps",
        "sub_domains": [
            "source hierarchy application",
            "triangulation discipline",
            "gap identification",
            "disinformation detection",
            "multilingual research",
            "live web search query construction",
            "corpus navigation",
            "primary vs secondary source distinction",
        ],
        "reference_standards": [
            "CIA Tradecraft Primer",
            "Bellingcat methodology",
            "RAND research methods",
        ],
        "weight": 1.0,
    },

    "writing_and_communication": {
        "description": "Intelligence report writing and correspondence",
        "sub_domains": [
            "executive summary writing",
            "intelligence report structure",
            "confidence hedging language",
            "Portuguese business correspondence",
            "English formal correspondence",
            "BD proposal writing (Challenger Sale)",
            "compliance documentation",
            "technical specification writing",
            "BLUF (Bottom Line Up Front) discipline",
            "audience calibration",
        ],
        "reference_standards": [
            "US Intelligence Community writing standards",
            "UK GCHQ analytical writing guidance (open)",
            "Arkmurus brand voice doctrine",
        ],
        "weight": 0.9,
    },

    "coding_and_systems": {
        "description": "Technical capability for self-improvement",
        "sub_domains": [
            "Python for data processing",
            "API integration (Anthropic, Mem0)",
            "RAG pipeline construction",
            "web scraping and crawling",
            "ChromaDB vector operations",
            "Redis cache management",
            "Node.js/JavaScript understanding",
            "Fly.io deployment",
            "Seenode integration",
            "prompt engineering optimization",
        ],
        "reference_standards": [
            "Anthropic API documentation",
            "ChromaDB documentation",
            "Redis documentation",
        ],
        "weight": 0.7,
    },
}


def get_domain_names() -> list[str]:
    return list(CAPABILITY_DOMAINS.keys())


def get_domain(name: str) -> dict | None:
    return CAPABILITY_DOMAINS.get(name)


def get_sub_domains(name: str) -> list[str]:
    d = CAPABILITY_DOMAINS.get(name)
    return d["sub_domains"] if d else []


def domain_summary(name: str) -> str:
    """One-line summary for prompt injection."""
    d = CAPABILITY_DOMAINS.get(name)
    if not d:
        return f"Unknown domain: {name}"
    subs = ", ".join(d["sub_domains"][:5])
    return f"{name}: {d['description']} ({subs}, ...)"
