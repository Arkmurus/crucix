"""seed_list — curated domains for ARIA's own search engine.

R-F501 (2026-05-14). Hand-picked starter list of ~160 high-value domains
across the sectors ARIA operates in. Each entry carries:
  tier      1 = primary official (govt, multilateral, registry)
            2 = tier-1 press / sectoral trade press
            3 = tier-3 regional / general news
            4 = secondary / blog / forum
  sector    defence | sanctions | tenders | corporate | multilateral |
            lusophone | africa | news | aviation | maritime | export_control
  region    ISO-style codes joined by "," — e.g., "ao,mz,pt" for Lusophone
  language  primary language code (en, pt, fr, es, ar, ru, zh, ...)

This is the STARTING corpus. The crawler grows it via link discovery,
but the curated seed list anchors the source-tier ranking and prevents
ARIA from being captured by adversarial SEO. New domains discovered via
crawl are tier 4 by default until promoted.

Public:
    SEEDS              — list[dict]
    seed_all()         — async; registers every entry via search_index.db
    seed_by_sector(s)  — async; filtered subset
"""
from __future__ import annotations

from aria_service.search_index import db


# ─────────────────────────────────────────────────────────────────
# Seed registry — keep this hand-curated, alphabetised per group.
# ─────────────────────────────────────────────────────────────────

SEEDS: list[dict] = [
    # ── Tier 1: official / multilateral / registries ────────────
    # Sanctions & export control
    {"domain": "ofac.treasury.gov",                "tier": 1, "sector": "sanctions",       "region": "us",       "language": "en"},
    {"domain": "treasury.gov",                     "tier": 1, "sector": "sanctions",       "region": "us",       "language": "en"},
    {"domain": "bis.doc.gov",                      "tier": 1, "sector": "export_control",  "region": "us",       "language": "en"},
    {"domain": "state.gov",                        "tier": 1, "sector": "sanctions",       "region": "us",       "language": "en"},
    {"domain": "eeas.europa.eu",                   "tier": 1, "sector": "sanctions",       "region": "eu",       "language": "en"},
    {"domain": "consilium.europa.eu",              "tier": 1, "sector": "sanctions",       "region": "eu",       "language": "en"},
    {"domain": "ec.europa.eu",                     "tier": 1, "sector": "multilateral",    "region": "eu",       "language": "en"},
    {"domain": "gov.uk",                           "tier": 1, "sector": "sanctions",       "region": "uk",       "language": "en"},
    {"domain": "opensanctions.org",                "tier": 1, "sector": "sanctions",       "region": "global",   "language": "en"},
    {"domain": "gleif.org",                        "tier": 1, "sector": "corporate",       "region": "global",   "language": "en"},
    # Tenders
    {"domain": "ted.europa.eu",                    "tier": 1, "sector": "tenders",         "region": "eu",       "language": "en"},
    {"domain": "sam.gov",                          "tier": 1, "sector": "tenders",         "region": "us",       "language": "en"},
    {"domain": "find-tender.service.gov.uk",       "tier": 1, "sector": "tenders",         "region": "uk",       "language": "en"},
    {"domain": "ungm.org",                         "tier": 1, "sector": "tenders",         "region": "global",   "language": "en"},
    {"domain": "afdb.org",                         "tier": 1, "sector": "tenders",         "region": "africa",   "language": "en"},
    {"domain": "worldbank.org",                    "tier": 1, "sector": "tenders",         "region": "global",   "language": "en"},
    {"domain": "ebrd.com",                         "tier": 1, "sector": "tenders",         "region": "eu",       "language": "en"},
    {"domain": "eib.org",                          "tier": 1, "sector": "tenders",         "region": "eu",       "language": "en"},
    {"domain": "iadb.org",                         "tier": 1, "sector": "tenders",         "region": "latam",    "language": "en"},
    {"domain": "isdb.org",                         "tier": 1, "sector": "tenders",         "region": "mena",     "language": "en"},
    # Corporate registries
    {"domain": "find-and-update.company-information.service.gov.uk",
                                                    "tier": 1, "sector": "corporate",       "region": "uk",       "language": "en"},
    {"domain": "rfb.gov.br",                       "tier": 1, "sector": "corporate",       "region": "br",       "language": "pt"},
    {"domain": "minjus.gov.ao",                    "tier": 1, "sector": "corporate",       "region": "ao",       "language": "pt"},
    # Multilateral / treaty bodies
    {"domain": "nato.int",                         "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "un.org",                           "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "unctad.org",                       "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "imf.org",                          "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "oecd.org",                         "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "icao.int",                         "tier": 1, "sector": "aviation",        "region": "global",   "language": "en"},
    {"domain": "imo.org",                          "tier": 1, "sector": "maritime",        "region": "global",   "language": "en"},
    {"domain": "iaea.org",                         "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "opcw.org",                         "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "ohchr.org",                        "tier": 1, "sector": "multilateral",    "region": "global",   "language": "en"},
    {"domain": "europa.eu",                        "tier": 1, "sector": "multilateral",    "region": "eu",       "language": "en"},
    # National defence MoDs
    {"domain": "defence.gov.au",                   "tier": 1, "sector": "defence",         "region": "au",       "language": "en"},
    {"domain": "mod.gov.uk",                       "tier": 1, "sector": "defence",         "region": "uk",       "language": "en"},
    {"domain": "defense.gov",                      "tier": 1, "sector": "defence",         "region": "us",       "language": "en"},
    {"domain": "defesa.gov.br",                    "tier": 1, "sector": "defence",         "region": "br",       "language": "pt"},
    {"domain": "mindef.gov.ao",                    "tier": 1, "sector": "defence",         "region": "ao",       "language": "pt"},
    {"domain": "mod.gov.mz",                       "tier": 1, "sector": "defence",         "region": "mz",       "language": "pt"},

    # ── Tier 2: sectoral press / Lusophone press ────────────────
    # Defence press
    {"domain": "defenceweb.co.za",                 "tier": 2, "sector": "defence",         "region": "za,africa","language": "en"},
    {"domain": "defensenews.com",                  "tier": 2, "sector": "defence",         "region": "us,global","language": "en"},
    {"domain": "breakingdefense.com",              "tier": 2, "sector": "defence",         "region": "us,global","language": "en"},
    {"domain": "janes.com",                        "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "armyrecognition.com",              "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "navalnews.com",                    "tier": 2, "sector": "maritime",        "region": "global",   "language": "en"},
    {"domain": "airrecognition.com",               "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "shephardmedia.com",                "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "militarytimes.com",                "tier": 2, "sector": "defence",         "region": "us",       "language": "en"},
    {"domain": "thedefensepost.com",               "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "defenseworld.net",                 "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "defenseone.com",                   "tier": 2, "sector": "defence",         "region": "us",       "language": "en"},
    {"domain": "nationaldefensemagazine.org",      "tier": 2, "sector": "defence",         "region": "us",       "language": "en"},
    {"domain": "aviationweek.com",                 "tier": 2, "sector": "aviation",        "region": "global",   "language": "en"},
    {"domain": "ainonline.com",                    "tier": 2, "sector": "aviation",        "region": "global",   "language": "en"},
    {"domain": "flightglobal.com",                 "tier": 2, "sector": "aviation",        "region": "global",   "language": "en"},
    {"domain": "defensa.com",                      "tier": 2, "sector": "defence",         "region": "es,latam", "language": "es"},
    {"domain": "defesanet.com.br",                 "tier": 2, "sector": "defence",         "region": "br",       "language": "pt"},
    {"domain": "forcaaerea.com.br",                "tier": 2, "sector": "aviation",        "region": "br",       "language": "pt"},
    {"domain": "defesaaereanaval.com.br",          "tier": 2, "sector": "defence",         "region": "br",       "language": "pt"},
    {"domain": "globaldefencemag.com",             "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "defenseromania.ro",                "tier": 3, "sector": "defence",         "region": "ro",       "language": "ro"},
    {"domain": "edrmagazine.eu",                   "tier": 2, "sector": "defence",         "region": "eu",       "language": "en"},
    {"domain": "tactical-life.com",                "tier": 3, "sector": "defence",         "region": "us",       "language": "en"},
    # Maritime
    {"domain": "marinelink.com",                   "tier": 2, "sector": "maritime",        "region": "global",   "language": "en"},
    {"domain": "splash247.com",                    "tier": 2, "sector": "maritime",        "region": "global",   "language": "en"},
    {"domain": "lloydslist.maritimeintelligence.informa.com", "tier": 2, "sector": "maritime", "region": "global", "language": "en"},
    {"domain": "tradewindsnews.com",               "tier": 2, "sector": "maritime",        "region": "global",   "language": "en"},
    # Lusophone press
    {"domain": "jornaldeangola.ao",                "tier": 2, "sector": "lusophone",       "region": "ao",       "language": "pt"},
    {"domain": "expansao.co.ao",                   "tier": 2, "sector": "lusophone",       "region": "ao",       "language": "pt"},
    {"domain": "mercado.co.ao",                    "tier": 2, "sector": "lusophone",       "region": "ao",       "language": "pt"},
    {"domain": "novojornal.co.ao",                 "tier": 2, "sector": "lusophone",       "region": "ao",       "language": "pt"},
    {"domain": "expresso.pt",                      "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "publico.pt",                       "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "jornaldenegocios.pt",              "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "visao.pt",                         "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "dn.pt",                            "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "jn.pt",                            "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "noticiasaominuto.com",             "tier": 3, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "opais.co.mz",                      "tier": 2, "sector": "lusophone",       "region": "mz",       "language": "pt"},
    {"domain": "jornaldomingo.co.mz",              "tier": 3, "sector": "lusophone",       "region": "mz",       "language": "pt"},
    {"domain": "savana.co.mz",                     "tier": 3, "sector": "lusophone",       "region": "mz",       "language": "pt"},
    {"domain": "noticias.sapo.mz",                 "tier": 3, "sector": "lusophone",       "region": "mz",       "language": "pt"},
    {"domain": "folha.uol.com.br",                 "tier": 2, "sector": "lusophone",       "region": "br",       "language": "pt"},
    {"domain": "estadao.com.br",                   "tier": 2, "sector": "lusophone",       "region": "br",       "language": "pt"},
    {"domain": "valor.globo.com",                  "tier": 2, "sector": "lusophone",       "region": "br",       "language": "pt"},
    {"domain": "g1.globo.com",                     "tier": 2, "sector": "lusophone",       "region": "br",       "language": "pt"},
    {"domain": "rfi.fr",                           "tier": 2, "sector": "news",            "region": "fr,africa","language": "fr"},
    # Africa & emerging-market press
    {"domain": "africanews.com",                   "tier": 2, "sector": "africa",          "region": "africa",   "language": "en"},
    {"domain": "africa-confidential.com",          "tier": 2, "sector": "africa",          "region": "africa",   "language": "en"},
    {"domain": "africaintelligence.com",           "tier": 2, "sector": "africa",          "region": "africa",   "language": "en"},
    {"domain": "premiumtimesng.com",               "tier": 2, "sector": "africa",          "region": "ng",       "language": "en"},
    {"domain": "mg.co.za",                         "tier": 2, "sector": "africa",          "region": "za",       "language": "en"},
    {"domain": "iol.co.za",                        "tier": 3, "sector": "africa",          "region": "za",       "language": "en"},
    {"domain": "timeslive.co.za",                  "tier": 2, "sector": "africa",          "region": "za",       "language": "en"},
    {"domain": "businessdailyafrica.com",          "tier": 2, "sector": "africa",          "region": "ke",       "language": "en"},
    {"domain": "nation.africa",                    "tier": 2, "sector": "africa",          "region": "ke",       "language": "en"},
    {"domain": "theafricareport.com",              "tier": 2, "sector": "africa",          "region": "africa",   "language": "en"},
    {"domain": "ewn.co.za",                        "tier": 3, "sector": "africa",          "region": "za",       "language": "en"},
    # General reputable press (paywalled headlines still useful)
    {"domain": "reuters.com",                      "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "bloomberg.com",                    "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "ft.com",                           "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "nytimes.com",                      "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "washingtonpost.com",               "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "theguardian.com",                  "tier": 2, "sector": "news",            "region": "uk",       "language": "en"},
    {"domain": "bbc.com",                          "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "aljazeera.com",                    "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "economist.com",                    "tier": 2, "sector": "news",            "region": "global",   "language": "en"},
    {"domain": "lemonde.fr",                       "tier": 2, "sector": "news",            "region": "fr",       "language": "fr"},
    {"domain": "lefigaro.fr",                      "tier": 2, "sector": "news",            "region": "fr",       "language": "fr"},
    {"domain": "monde-diplomatique.fr",            "tier": 2, "sector": "news",            "region": "fr",       "language": "fr"},
    {"domain": "spiegel.de",                       "tier": 2, "sector": "news",            "region": "de",       "language": "de"},
    {"domain": "elpais.com",                       "tier": 2, "sector": "news",            "region": "es",       "language": "es"},
    # OSINT / think-tanks
    {"domain": "iiss.org",                         "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "csis.org",                         "tier": 2, "sector": "defence",         "region": "us",       "language": "en"},
    {"domain": "rusi.org",                         "tier": 2, "sector": "defence",         "region": "uk",       "language": "en"},
    {"domain": "iss.europa.eu",                    "tier": 2, "sector": "defence",         "region": "eu",       "language": "en"},
    {"domain": "issafrica.org",                    "tier": 2, "sector": "africa",          "region": "africa",   "language": "en"},
    {"domain": "carnegieendowment.org",            "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "brookings.edu",                    "tier": 2, "sector": "defence",         "region": "us",       "language": "en"},
    {"domain": "atlanticcouncil.org",              "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "sipri.org",                        "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "armscontrol.org",                  "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    {"domain": "acleddata.com",                    "tier": 2, "sector": "defence",         "region": "global",   "language": "en"},
    # Arabic / MENA
    {"domain": "alarabiya.net",                    "tier": 2, "sector": "news",            "region": "mena",     "language": "ar"},
    {"domain": "aawsat.com",                       "tier": 2, "sector": "news",            "region": "mena",     "language": "ar"},
    {"domain": "thenationalnews.com",              "tier": 2, "sector": "news",            "region": "ae",       "language": "en"},
    {"domain": "middleeastmonitor.com",            "tier": 2, "sector": "news",            "region": "mena",     "language": "en"},
    # Russian-language (cyber + defence)
    {"domain": "tass.com",                         "tier": 3, "sector": "news",            "region": "ru",       "language": "en"},
    {"domain": "kommersant.ru",                    "tier": 2, "sector": "news",            "region": "ru",       "language": "ru"},
    {"domain": "rbc.ru",                           "tier": 2, "sector": "news",            "region": "ru",       "language": "ru"},
    {"domain": "themoscowtimes.com",               "tier": 2, "sector": "news",            "region": "ru",       "language": "en"},
    # Turkish defence
    {"domain": "ssb.gov.tr",                       "tier": 1, "sector": "defence",         "region": "tr",       "language": "tr"},
    {"domain": "defenceturk.net",                  "tier": 2, "sector": "defence",         "region": "tr",       "language": "tr"},
    # Chinese-language (Taiwan + open)
    {"domain": "scmp.com",                         "tier": 2, "sector": "news",            "region": "hk,cn",    "language": "en"},
    {"domain": "taipeitimes.com",                  "tier": 2, "sector": "news",            "region": "tw",       "language": "en"},

    # ── Tier 3: regional press / general aggregators ───────────
    {"domain": "africaalmanac.com",                "tier": 3, "sector": "africa",          "region": "africa",   "language": "en"},
    {"domain": "thecitizen.co.za",                 "tier": 3, "sector": "africa",          "region": "za",       "language": "en"},
    {"domain": "macauhub.com.mo",                  "tier": 3, "sector": "lusophone",       "region": "mo",       "language": "pt"},
    {"domain": "platinumlist.gw",                  "tier": 3, "sector": "lusophone",       "region": "gw",       "language": "pt"},
    {"domain": "tvi24.iol.pt",                     "tier": 3, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "observador.pt",                    "tier": 3, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "sapo.pt",                          "tier": 3, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "lusa.pt",                          "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "rtp.pt",                           "tier": 2, "sector": "lusophone",       "region": "pt",       "language": "pt"},
    {"domain": "rtc.cv",                           "tier": 3, "sector": "lusophone",       "region": "cv",       "language": "pt"},
    {"domain": "anosaterra.com",                   "tier": 3, "sector": "lusophone",       "region": "gw",       "language": "pt"},
]


async def seed_all() -> int:
    """Register every entry in SEEDS into the search_index domains table.
    Idempotent — re-running upserts unchanged rows."""
    n = 0
    for entry in SEEDS:
        await db.register_domain(
            domain=entry["domain"],
            tier=entry["tier"],
            sector=entry.get("sector"),
            region=entry.get("region"),
            language=entry.get("language"),
            rate_limit_per_sec=entry.get("rate_limit_per_sec", 1.0),
            enabled=entry.get("enabled", True),
            notes=entry.get("notes"),
        )
        n += 1
    return n


async def seed_by_sector(sector: str) -> int:
    n = 0
    for entry in SEEDS:
        if entry.get("sector") != sector:
            continue
        await db.register_domain(
            domain=entry["domain"], tier=entry["tier"],
            sector=entry.get("sector"), region=entry.get("region"),
            language=entry.get("language"),
            rate_limit_per_sec=entry.get("rate_limit_per_sec", 1.0),
            enabled=entry.get("enabled", True),
            notes=entry.get("notes"),
        )
        n += 1
    return n
