"""Defence source seed — curated Tier-1/Tier-1b/Tier-2 sources for web_atlas.

Why this exists
───────────────
web_atlas was shipping empty on first boot — it only knew about sources
we had fetched from at least once. That meant ARIA's early research on
any new topic had no source-tier preference and treated e.g. a random
LinkedIn post the same as a SIPRI publication or UK ECJU guidance.

This file ships with a curated seed list covering:
  - Tier 1a (regulator / primary source / official gazette)
  - Tier 1b (defence industry authority — SIPRI, Janes equivalents)
  - Tier 2  (specialist defence press — high credibility, not primary)

On startup, `seed_web_atlas()` adds these to the atlas if they're not
already there. Subsequent fetches update reliability EMAs on top. The
seed is never overwritten — it's additive.

Organised by domain so an operator can see at a glance what ARIA
trusts a priori.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging

logger = logging.getLogger("aria.defence_source_seed")


# ── Source catalogue ──────────────────────────────────────────────────────
# Format: (url, family, tier, topic_tags)
# - url must be a real reachable homepage (uptime monitor will ping it)
# - family groups subdomains under a canonical name (web_atlas._source_family)
# - tier is "tier_1a" (regulator/primary), "tier_1b" (industry authority),
#   "tier_2" (specialist press)
# - topic_tags help rank_sources_for_topic produce good defaults

_DEFENCE_SOURCES: list[tuple[str, str, str, list[str]]] = [
    # ══════════════════════════════════════════════════════════════════════
    # TIER 1a — PRIMARY REGULATORS / OFFICIAL GAZETTES
    # These are the canonical sources. If they say it, it's the fact.
    # ══════════════════════════════════════════════════════════════════════

    # Sanctions + export control — global
    ("https://ofac.treasury.gov/", "ofac_treasury",
     "tier_1a", ["compliance", "sanctions", "legal"]),
    ("https://www.gov.uk/government/organisations/office-of-financial-sanctions-implementation",
     "uk_ofsi", "tier_1a", ["compliance", "sanctions", "legal"]),
    ("https://www.un.org/securitycouncil/sanctions/information",
     "un_security_council", "tier_1a", ["compliance", "sanctions", "legal", "geopolitics"]),
    ("https://eur-lex.europa.eu/", "eu_lex",
     "tier_1a", ["compliance", "legal", "eu_regulation"]),
    ("https://www.state.gov/bureaus-offices/under-secretary-for-arms-control-and-international-security-affairs/",
     "us_state_dept", "tier_1a", ["compliance", "export_control"]),

    # UK-specific regulators
    ("https://www.gov.uk/government/organisations/export-control-joint-unit",
     "uk_ecju", "tier_1a", ["compliance", "export_control", "uk"]),
    ("https://find-and-update.company-information.service.gov.uk/",
     "uk_companies_house", "tier_1a", ["registry", "uk"]),
    ("https://www.gov.uk/government/publications/uk-strategic-export-control-lists-the-consolidated-list-of-strategic-military-and-dual-use-items-that-require-export-authorisation",
     "uk_strategic_control_lists", "tier_1a", ["compliance", "export_control", "uk"]),

    # MDBs — procurement debarment
    ("https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms",
     "world_bank_debarred", "tier_1a", ["compliance", "procurement"]),
    ("https://www.afdb.org/en/projects-and-operations/procurement/debarment-and-sanctions-procedures",
     "afdb_sanctions", "tier_1a", ["compliance", "procurement", "africa"]),

    # NATO + standards
    ("https://www.nato.int/cps/en/natohq/topics_77646.htm",
     "nato_stanags", "tier_1a", ["technical", "nato_standards"]),
    ("https://nso.nato.int/", "nato_nso",
     "tier_1a", ["technical", "nato_standards"]),

    # Procurement portals — primary
    ("https://ted.europa.eu/", "ted_europa",
     "tier_1a", ["procurement", "eu"]),
    ("https://sam.gov/", "us_sam_gov",
     "tier_1a", ["procurement", "us"]),
    ("https://www.contractsfinder.service.gov.uk/",
     "uk_contracts_finder", "tier_1a", ["procurement", "uk"]),
    ("https://www.ungm.org/", "un_global_marketplace",
     "tier_1a", ["procurement", "un"]),

    # Sanctions aggregator (authoritative aggregation of Tier 1a primary lists)
    ("https://www.opensanctions.org/", "opensanctions",
     "tier_1a", ["compliance", "sanctions", "pep"]),

    # SEC + equivalents
    ("https://www.sec.gov/", "us_sec",
     "tier_1a", ["finance", "registry", "us"]),

    # ══════════════════════════════════════════════════════════════════════
    # TIER 1b — DEFENCE INDUSTRY AUTHORITIES
    # Specialist research houses, think tanks with primary data.
    # ══════════════════════════════════════════════════════════════════════

    # SIPRI — arms transfers, military expenditure
    ("https://www.sipri.org/", "sipri",
     "tier_1b", ["market_intel", "geopolitics", "procurement"]),
    ("https://armstrade.sipri.org/", "sipri_armstrade",
     "tier_1b", ["market_intel", "geopolitics"]),

    # Janes — premium defence intelligence (paid API; seed for when wired)
    ("https://www.janes.com/", "janes",
     "tier_1b", ["market_intel", "technical", "geopolitics"]),

    # IISS
    ("https://www.iiss.org/", "iiss",
     "tier_1b", ["geopolitics", "market_intel"]),

    # RUSI
    ("https://rusi.org/", "rusi",
     "tier_1b", ["geopolitics", "compliance", "market_intel"]),

    # ISW — conflict tracking
    ("https://www.understandingwar.org/", "isw",
     "tier_1b", ["geopolitics", "conflict"]),

    # UCDP — Uppsala Conflict Data Program
    ("https://ucdp.uu.se/", "ucdp",
     "tier_1b", ["geopolitics", "conflict"]),

    # ACLED
    ("https://acleddata.com/", "acled",
     "tier_1b", ["geopolitics", "conflict"]),

    # CSIS
    ("https://www.csis.org/", "csis",
     "tier_1b", ["geopolitics", "market_intel"]),

    # Stimson
    ("https://www.stimson.org/", "stimson",
     "tier_1b", ["geopolitics", "compliance"]),

    # ══════════════════════════════════════════════════════════════════════
    # TIER 2 — SPECIALIST DEFENCE PRESS
    # High credibility but not primary. Good for corroboration.
    # ══════════════════════════════════════════════════════════════════════

    ("https://www.defensenews.com/", "defense_news",
     "tier_2", ["market_intel", "geopolitics"]),
    ("https://breakingdefense.com/", "breaking_defense",
     "tier_2", ["market_intel", "technical"]),
    ("https://www.janes.com/defence-news", "janes_news",
     "tier_2", ["market_intel", "technical"]),
    ("https://www.defenceweb.co.za/", "defenceweb",
     "tier_2", ["market_intel", "africa"]),
    ("https://www.c4defence.com/en/home/", "c4defence",
     "tier_2", ["market_intel", "technical", "turkey"]),
    ("https://www.defenseone.com/", "defense_one",
     "tier_2", ["market_intel", "geopolitics"]),
    ("https://www.reuters.com/business/aerospace-defense/",
     "reuters_defense", "tier_2", ["market_intel", "finance"]),
    ("https://www.ft.com/aerospace-defence", "ft_defence",
     "tier_2", ["market_intel", "finance"]),
    ("https://thediplomat.com/", "the_diplomat",
     "tier_2", ["geopolitics", "asia"]),

    # Region-specific
    ("https://jamestown.org/", "jamestown",
     "tier_2", ["geopolitics", "russia_china"]),
    ("https://www.meforum.org/", "me_forum",
     "tier_2", ["geopolitics", "middle_east"]),
    ("https://www.dailysabah.com/", "daily_sabah",
     "tier_2", ["geopolitics", "turkey"]),
    ("https://jornaldeangola.ao/", "jornal_angola",
     "tier_2", ["geopolitics", "angola", "lusophone"]),
    ("https://www.dailytrust.com/", "daily_trust_nigeria",
     "tier_2", ["geopolitics", "nigeria"]),

    # Academic / open research
    ("https://arxiv.org/", "arxiv",
     "tier_1b", ["academic", "technical"]),
    ("https://api.openalex.org/", "openalex",
     "tier_1b", ["academic"]),
    ("https://api.crossref.org/", "crossref",
     "tier_1a", ["academic", "registry"]),

    # ══════════════════════════════════════════════════════════════════════
    # R-F137 (2026-05-10) — EXPANDED FREE + RELIABLE SOURCE SEED
    # 45 → ~120 sources. All free or freely indexable; the public-record
    # primary set is biased toward government / regulator / MDB / NGO so
    # ARIA gets Tier 1a coverage for the 30+ jurisdictions that matter
    # for global defence DD. Tier 2 country press fills out regional
    # coverage where Tier 1a is sparse.
    # ══════════════════════════════════════════════════════════════════════

    # ── Tier 1a — Export-control regulators (15 jurisdictions) ──
    ("https://www.bafa.de/EN/Foreign_Trade/foreign_trade_node.html",
     "de_bafa", "tier_1a", ["compliance", "export_control", "germany"]),
    ("https://www.entreprises.gouv.fr/service-des-biens-double-usage-sbdu",
     "fr_sbdu", "tier_1a", ["compliance", "export_control", "france"]),
    ("https://www.esteri.it/en/ministero/struttura/uama/", "it_uama",
     "tier_1a", ["compliance", "export_control", "italy"]),
    ("https://www.seco.admin.ch/seco/en/home/Aussenwirtschaftspolitik_Wirtschaftliche_Zusammenarbeit/Wirtschaftsbeziehungen/exportkontrollen-und-sanktionen/sanktionen-embargos.html",
     "ch_seco", "tier_1a", ["compliance", "export_control", "sanctions", "switzerland"]),
    ("https://isp.se/eng/", "se_isp",
     "tier_1a", ["compliance", "export_control", "sweden"]),
    ("https://www.regjeringen.no/en/dep/ud/the-foreign-service/", "no_ud",
     "tier_1a", ["compliance", "export_control", "norway"]),
    ("https://www.government.nl/topics/export-controls-of-strategic-goods",
     "nl_cdiu", "tier_1a", ["compliance", "export_control", "netherlands"]),
    ("https://comercio.gob.es/ImportacionExportacion/Regimenes/Paginas/FAQS/productos-doble-uso.aspx", "es_sgcd",
     "tier_1a", ["compliance", "export_control", "spain"]),
    ("https://www.meti.go.jp/english/policy/external_economy/trade_control/index.html",
     "jp_meti", "tier_1a", ["compliance", "export_control", "japan"]),
    ("https://www.dapa.go.kr/dapa_en/main.do", "kr_dapa",
     "tier_1a", ["compliance", "procurement", "south_korea"]),
    ("https://www.ddpmod.gov.in/", "in_ddp",
     "tier_1a", ["compliance", "procurement", "india"]),
    ("https://www.gov.il/en/departments/ministry_of_defense", "il_sibat",
     "tier_1a", ["compliance", "export_control", "israel"]),
    ("https://sanctions.dfat.gov.au/",
     "au_dfat_sanctions", "tier_1a", ["compliance", "sanctions", "australia"]),
    ("https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/index.aspx",
     "ca_gac_sanctions", "tier_1a", ["compliance", "sanctions", "canada"]),
    ("https://www.mfat.govt.nz/en/peace-rights-and-security/sanctions/",
     "nz_mfat_sanctions", "tier_1a", ["compliance", "sanctions", "new_zealand"]),

    # ── Tier 1a — Multilateral development banks ──
    ("https://www.adb.org/site/integrity/sanctions",
     "adb_sanctions", "tier_1a", ["compliance", "procurement", "asia"]),
    ("https://www.iadb.org/en/who-we-are/transparency/sanctions-system/sanctioned-firms-and-individuals",
     "idb_sanctions", "tier_1a", ["compliance", "procurement", "latam"]),
    ("https://www.eib.org/en/about/accountability/anti-fraud/exclusion/index",
     "eib_debarment", "tier_1a", ["compliance", "procurement", "eu"]),
    ("https://www.isdb.org/who-we-are/integrity/integrity-ethics-and-anti-corruption",
     "isdb_integrity", "tier_1a", ["compliance", "procurement", "mena"]),
    ("https://www.ebrd.com/integrity-and-compliance.html",
     "ebrd_integrity", "tier_1a", ["compliance", "procurement", "europe_emerging"]),

    # ── Tier 1a — UN agencies + IGOs ──
    ("https://disarmament.unoda.org/", "un_oda",
     "tier_1a", ["compliance", "geopolitics", "international_law"]),
    ("https://unosat.org/", "un_osat",
     "tier_1a", ["geopolitics", "conflict", "imagery"]),
    ("https://www.unhcr.org/", "unhcr",
     "tier_1a", ["geopolitics", "conflict", "humanitarian"]),
    ("https://www.unocha.org/", "unocha",
     "tier_1a", ["geopolitics", "conflict", "humanitarian"]),
    ("https://www.osce.org/", "osce",
     "tier_1a", ["geopolitics", "compliance", "international_law"]),
    ("https://www.opcw.org/", "opcw",
     "tier_1a", ["compliance", "international_law", "wmd"]),
    ("https://www.iaea.org/", "iaea",
     "tier_1a", ["compliance", "international_law", "nuclear"]),
    ("https://wassenaar.org/", "wassenaar_arrangement",
     "tier_1a", ["compliance", "export_control", "international_law"]),
    ("https://www.fatf-gafi.org/", "fatf",
     "tier_1a", ["compliance", "finance", "aml"]),

    # ── Tier 1a — Procurement portals (15+ national) ──
    ("https://www.austender.gov.au/", "au_austender",
     "tier_1a", ["procurement", "australia"]),
    ("https://canadabuys.canada.ca/en", "ca_buyandsell",
     "tier_1a", ["procurement", "canada"]),
    ("https://www.pps.go.kr/eng/index.do", "kr_koneps",
     "tier_1a", ["procurement", "south_korea"]),
    ("https://www.india.gov.in/services/details/explore-business-opportunities-on-government-e-marketplace-gem", "in_gem",
     "tier_1a", ["procurement", "india"]),
    ("https://www.gebiz.gov.sg/", "sg_gebiz",
     "tier_1a", ["procurement", "singapore"]),
    ("https://www.comprasnet.gov.br/", "br_comprasnet",
     "tier_1a", ["procurement", "brazil"]),
    ("https://www.etenders.gov.za/", "za_etenders",
     "tier_1a", ["procurement", "south_africa"]),
    ("https://contrataciondelestado.es/wps/portal/plataforma",
     "es_placsp", "tier_1a", ["procurement", "spain"]),
    ("https://www.anticorruzione.it/", "it_anac",
     "tier_1a", ["procurement", "italy", "compliance"]),
    ("https://www.evergabe-online.de/", "de_evergabe",
     "tier_1a", ["procurement", "germany"]),
    ("https://doffin.no/", "no_doffin",
     "tier_1a", ["procurement", "norway"]),
    ("https://www.upphandlingsmyndigheten.se/", "se_upphandling",
     "tier_1a", ["procurement", "sweden"]),

    # ── Tier 1a — Registries (more jurisdictions) ──
    ("https://opencorporates.com/", "opencorporates",
     "tier_1a", ["registry", "global"]),
    ("https://offshoreleaks.icij.org/", "icij_offshore_leaks",
     "tier_1a", ["registry", "global", "compliance"]),
    ("https://e-justice.europa.eu/topics/registers-business-insolvency-land/business-registers-search-company-eu_en",
     "eu_business_registers", "tier_1a", ["registry", "eu"]),
    ("https://www.handelsregister.de/", "de_handelsregister",
     "tier_1a", ["registry", "germany"]),
    ("https://www.infogreffe.fr/", "fr_infogreffe",
     "tier_1a", ["registry", "france"]),
    ("https://companies-register.companiesoffice.govt.nz/", "nz_companies_office",
     "tier_1a", ["registry", "new_zealand"]),
    ("https://abr.business.gov.au/", "au_abr",
     "tier_1a", ["registry", "australia"]),

    # ── Tier 1a — National research labs / DSO ──
    ("https://www.darpa.mil/", "us_darpa",
     "tier_1a", ["technical", "us"]),
    ("https://www.gov.uk/government/organisations/defence-science-and-technology-laboratory",
     "uk_dstl", "tier_1a", ["technical", "uk"]),
    ("https://www.defense.gouv.fr/dga", "fr_dga",
     "tier_1a", ["technical", "france"]),
    ("https://www.faa.gov/", "us_faa",
     "tier_1a", ["technical", "aerospace", "us"]),
    ("https://www.easa.europa.eu/", "eu_easa",
     "tier_1a", ["technical", "aerospace", "eu"]),
    ("https://www.imo.org/", "imo",
     "tier_1a", ["maritime", "international_law"]),

    # ── Tier 1b — Think tanks (NATO + non-NATO perspectives) ──
    ("https://www.belfercenter.org/", "belfer_center",
     "tier_1b", ["geopolitics", "compliance"]),
    ("https://carnegieendowment.org/", "carnegie",
     "tier_1b", ["geopolitics", "compliance"]),
    ("https://www.brookings.edu/", "brookings",
     "tier_1b", ["geopolitics"]),
    ("https://www.atlanticcouncil.org/", "atlantic_council",
     "tier_1b", ["geopolitics", "nato"]),
    ("https://www.gmfus.org/", "gmf",
     "tier_1b", ["geopolitics", "transatlantic"]),
    ("https://ecfr.eu/", "ecfr",
     "tier_1b", ["geopolitics", "eu"]),
    ("https://www.ifri.org/", "ifri",
     "tier_1b", ["geopolitics", "france"]),
    ("https://www.swp-berlin.org/", "swp_berlin",
     "tier_1b", ["geopolitics", "germany"]),
    ("https://www.crisisgroup.org/", "icg",
     "tier_1b", ["geopolitics", "conflict"]),
    ("https://www.iss.europa.eu/", "iss_eu",
     "tier_1b", ["geopolitics", "eu"]),
    ("https://www.prio.org/", "prio",
     "tier_1b", ["geopolitics", "conflict"]),
    ("https://hiik.de/", "hiik",
     "tier_1b", ["geopolitics", "conflict"]),
    ("https://www.globalsecurity.org/", "global_security_org",
     "tier_1b", ["geopolitics", "technical"]),
    ("https://www.fas.org/", "fas",
     "tier_1b", ["geopolitics", "technical", "compliance"]),
    ("https://thebulletin.org/", "bulletin_atomic_scientists",
     "tier_1b", ["geopolitics", "nuclear"]),

    # ── Tier 1b — Anti-corruption / crime + sanctions intel (free) ──
    ("https://www.occrp.org/en", "occrp",
     "tier_1b", ["compliance", "crime", "investigative"]),
    ("https://www.transparency.org/", "transparency_international",
     "tier_1b", ["compliance"]),
    ("https://www.globalwitness.org/en/", "global_witness",
     "tier_1b", ["compliance", "crime"]),
    ("https://thesentry.org/", "the_sentry",
     "tier_1b", ["compliance", "crime", "africa"]),
    ("https://gfintegrity.org/", "gfi",
     "tier_1b", ["compliance", "finance"]),
    ("https://www.castellum.ai/", "castellum_ai",
     "tier_1b", ["compliance", "sanctions"]),
    ("https://www.opensanctions.org/search/", "sanctionslist_io",
     "tier_1b", ["compliance", "sanctions"]),

    # ── Tier 2 — Specialist defence press (specialty + region) ──
    ("https://www.twz.com/", "the_war_zone",
     "tier_2", ["technical", "market_intel"]),
    ("https://www.navalnews.com/", "naval_news",
     "tier_2", ["technical", "naval"]),
    ("https://aviationweek.com/", "aviation_week",
     "tier_2", ["technical", "aerospace"]),
    ("https://www.airforcemag.com/", "air_force_mag",
     "tier_2", ["technical", "aerospace"]),
    ("https://www.nationaldefensemagazine.org/", "national_defense_mag",
     "tier_2", ["market_intel", "us"]),
    ("https://www.shephardmedia.com/", "shephard_defence",
     "tier_2", ["market_intel", "technical"]),
    ("https://asiatimes.com/", "asia_times",
     "tier_2", ["geopolitics", "asia"]),
    ("https://asia.nikkei.com/", "nikkei_asia",
     "tier_2", ["finance", "asia"]),

    # ── Tier 2 — Country press (defence beats, free RSS) ──
    ("https://en.yna.co.kr/", "yonhap_en",
     "tier_2", ["geopolitics", "south_korea"]),
    ("https://www.koreaherald.com/", "korea_herald",
     "tier_2", ["geopolitics", "south_korea"]),
    ("https://www.timesofisrael.com/", "times_of_israel",
     "tier_2", ["geopolitics", "israel"]),
    ("https://www.jpost.com/", "jerusalem_post",
     "tier_2", ["geopolitics", "israel"]),
    ("https://www.aa.com.tr/en", "anadolu_agency",
     "tier_2", ["geopolitics", "turkey"]),
    ("https://www.gulfnews.com/", "gulf_news",
     "tier_2", ["geopolitics", "uae"]),
    ("https://www.khaleejtimes.com/", "khaleej_times",
     "tier_2", ["geopolitics", "uae"]),
    ("https://www.arabnews.com/", "arab_news",
     "tier_2", ["geopolitics", "saudi_arabia"]),
    ("https://english.alarabiya.net/", "al_arabiya",
     "tier_2", ["geopolitics", "saudi_arabia"]),
    ("https://www.thehindu.com/news/national/", "the_hindu",
     "tier_2", ["geopolitics", "india"]),
    ("https://indiandefencereview.com/", "indian_defence_review",
     "tier_2", ["market_intel", "technical", "india"]),
    ("https://www.scmp.com/", "scmp",
     "tier_2", ["geopolitics", "asia"]),
    ("https://tass.com/", "tass_en",  # Russian state press caveat — kept for visibility, tier_4 in scoring
     "tier_2", ["geopolitics", "russia"]),
    ("https://www.macaubusiness.com/", "macauhub",
     "tier_2", ["geopolitics", "lusophone"]),
    ("https://www.lusa.pt/Default.aspx", "lusa",
     "tier_2", ["geopolitics", "lusophone", "portugal"]),
    ("https://noticias.uol.com.br/", "uol_brazil",
     "tier_2", ["geopolitics", "brazil"]),
    ("https://www.estadao.com.br/", "estadao",
     "tier_2", ["geopolitics", "brazil"]),
    ("https://elpais.com/", "el_pais",
     "tier_2", ["geopolitics", "spain"]),
    ("https://www.lemonde.fr/", "le_monde",
     "tier_2", ["geopolitics", "france"]),
    ("https://www.faz.net/", "faz",
     "tier_2", ["geopolitics", "germany"]),

    # ── Tier 1b — Industry associations ──
    ("https://www.aia-aerospace.org/", "aia_aerospace",
     "tier_1b", ["market_intel", "aerospace", "us"]),
    ("https://www.asd-europe.org/", "asd_europe",
     "tier_1b", ["market_intel", "aerospace", "eu"]),
    ("https://www.eda.europa.eu/", "eu_eda",
     "tier_1a", ["market_intel", "procurement", "eu"]),
    ("https://www.adsgroup.org.uk/", "ads_uk",
     "tier_1b", ["market_intel", "uk"]),

    # ── Tier 1a — OEM corporate primary sources (press / IR pages) ──
    # Adds the OEM publishers as trusted sources for the auto-language
    # search fan-out + DD digital layer. Tier 1a because OEM primary
    # sources are authoritative for their own contracts/press.
    ("https://www.leonardo.com/en/press-release-detail",
     "leonardo_press", "tier_1a", ["market_intel", "italy", "oem"]),
    ("https://www.rheinmetall.com/en/media", "rheinmetall_press",
     "tier_1a", ["market_intel", "germany", "oem"]),
    ("https://www.knds.com/news", "knds_press",
     "tier_1a", ["market_intel", "france", "oem"]),
    ("https://www.thalesgroup.com/en/global/press_release",
     "thales_press", "tier_1a", ["market_intel", "france", "oem"]),
    ("https://www.saab.com/newsroom",
     "saab_press", "tier_1a", ["market_intel", "sweden", "oem"]),
    ("https://www.airbus.com/en/newsroom",
     "airbus_press", "tier_1a", ["market_intel", "france", "oem"]),
    ("https://www.baesystems.com/en/our-company/news",
     "bae_press", "tier_1a", ["market_intel", "uk", "oem"]),
    ("https://www.fincantieri.com/en/media/press-releases/",
     "fincantieri_press", "tier_1a", ["market_intel", "italy", "oem"]),
    ("https://www.navantia.es/en/news/", "navantia_press",
     "tier_1a", ["market_intel", "spain", "oem"]),
    ("https://www.safran-group.com/news", "safran_press",
     "tier_1a", ["market_intel", "france", "oem"]),
    ("https://www.mbda-systems.com/press-releases/", "mbda_press",
     "tier_1a", ["market_intel", "france", "oem"]),
    ("https://www.hanwhaaerospace.co.kr/news/news_list.do",
     "hanwha_press", "tier_1a", ["market_intel", "south_korea", "oem"]),
    ("https://news.lockheedmartin.com/", "lockheed_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://www.rtx.com/news", "rtx_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://news.northropgrumman.com/news", "northrop_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://www.gd.com/news", "general_dynamics_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://boeing.mediaroom.com/", "boeing_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://www.l3harris.com/newsroom", "l3harris_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://www.kongsberg.com/newsroom/", "kongsberg_press",
     "tier_1a", ["market_intel", "norway", "oem"]),
    ("https://www.iai.co.il/media", "iai_press",
     "tier_1a", ["market_intel", "israel", "oem"]),
    ("https://www.rafael.co.il/press-releases-list/", "rafael_press",
     "tier_1a", ["market_intel", "israel", "oem"]),
    ("https://elbitsystems.com/pr-new/", "elbit_press",
     "tier_1a", ["market_intel", "israel", "oem"]),
    ("https://www.aselsan.com/en/news", "aselsan_press",
     "tier_1a", ["market_intel", "turkey", "oem"]),
    ("https://www.roketsan.com.tr/en/media/news", "roketsan_press",
     "tier_1a", ["market_intel", "turkey", "oem"]),
    ("https://baykartech.com/en/press/", "baykar_press",
     "tier_1a", ["market_intel", "turkey", "oem"]),
    ("https://hal-india.co.in/news_room.aspx", "hal_press",
     "tier_1a", ["market_intel", "india", "oem"]),
    ("https://www.embraer.com/media-center/en/", "embraer_press",
     "tier_1a", ["market_intel", "brazil", "oem"]),
    ("https://www.indragroup.com/en/press-room", "indra_press",
     "tier_1a", ["market_intel", "spain", "oem"]),
    ("https://www.babcockinternational.com/news/", "babcock_press",
     "tier_1a", ["market_intel", "uk", "oem"]),
    ("https://www.qinetiq.com/", "qinetiq_press",
     "tier_1a", ["market_intel", "uk", "oem"]),
    ("https://www.diehl.com/group/en/press-and-media/", "diehl_press",
     "tier_1a", ["market_intel", "germany", "oem"]),
    ("https://www.hensoldt.net/news/", "hensoldt_press",
     "tier_1a", ["market_intel", "germany", "oem"]),
    ("https://helsing.ai/newsroom", "helsing_press",
     "tier_1a", ["market_intel", "germany", "oem"]),
    ("https://www.anduril.com/news/", "anduril_press",
     "tier_1a", ["market_intel", "us", "oem"]),
    ("https://www.ga-asi.com/news-events/news",
     "ga_asi_press", "tier_1a", ["market_intel", "us", "oem"]),

    # ══════════════════════════════════════════════════════════════════════
    # R-F777 (2026-05-21) — SOURCE EXPANSION BATCH 1
    # Twelve high-value free feeds covering cyber/vulnerabilities, maritime
    # sanctions-evasion, commodities pricing (the user-explicit "security,
    # defence AND commodities" mandate), defence policy research, and
    # specialist OSINT. Each verified reachable on 2026-05-21 and chosen
    # because it lives in a gap Claude/DeepSeek training cutoff cannot
    # cover (real-time CVE exploitation, current commodity prices,
    # active investigations).
    # ══════════════════════════════════════════════════════════════════════

    # Cyber — US gov primary actively-exploited CVE list (defence-sector
    # contractors are obligated to remediate from this list within 30 days).
    ("https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
     "cisa_kev", "tier_1a", ["cyber", "vulnerability", "us", "defence_sector"]),

    # Maritime — UN-affiliated ship inspection database. Sanctions-evasion
    # detection (dark-fleet vessels, ownership chain).
    ("https://www.equasis.org/",
     "equasis", "tier_1a", ["maritime", "sanctions_evasion", "registry", "vessel"]),

    # Commodities — London Metal Exchange (steel, aluminium, copper
    # primary pricing — defence procurement valuation anchor).
    ("https://www.lme.com/",
     "lme_metal_exchange", "tier_1a",
     ["commodities", "metals", "pricing", "procurement"]),

    # Commodities — IMF Pink Sheet (cross-commodity monthly index).
    ("https://www.imf.org/en/Research/commodity-prices",
     "imf_commodity_prices", "tier_1a",
     ["commodities", "pricing", "imf", "geopolitics"]),

    # Commodities — OPEC Monthly Oil Market Report (oil supply/demand,
    # defence-economy linkage in producer states).
    ("https://www.opec.org/opec_web/en/publications/202.htm",
     "opec_mmr", "tier_1a", ["commodities", "oil", "geopolitics", "producer_states"]),

    # Cyber — MITRE ATT&CK framework. The defence-industry-standard TTP
    # taxonomy; every threat-intel platform maps to it.
    ("https://attack.mitre.org/",
     "mitre_attack", "tier_1b", ["cyber", "ttp", "defence_industry"]),

    # Defence policy — RAND Corporation publications.
    ("https://www.rand.org/",
     "rand_corp", "tier_1b", ["policy", "defence", "research", "us"]),

    # Defence enforcement — Conflict Armament Research (weapons-tracing
    # field investigations; sanctions-busting evidence).
    ("https://www.conflictarm.com/",
     "conflict_arm_research", "tier_1b",
     ["weapons", "sanctions", "investigation", "field_intel"]),

    # Foresight — Atrocity Forecasting Project (academic, calibrated
    # probabilities for mass-atrocity onset; defence-procurement leading
    # indicator in fragile states).
    ("https://earlywarningproject.ushmm.org/",
     "atrocity_forecasting", "tier_1b",
     ["foresight", "atrocity", "academic", "fragile_states"]),

    # Cyber — current cybersecurity advisories from CISA.
    ("https://www.cisa.gov/news-events/cybersecurity-advisories",
     "cisa_advisories", "tier_2", ["cyber", "advisory", "us", "current_threat"]),

    # OSINT — Bellingcat investigations (Wagner, dark fleets, sanctions
    # evasion, war-crime documentation; specialist primary OSINT).
    ("https://www.bellingcat.com/",
     "bellingcat", "tier_2",
     ["osint", "investigation", "sanctions_evasion", "specialist"]),

    # Cyber — ransomware.live live ransomware victim tracker (defence-
    # sector ransomware exposure indicator).
    ("https://www.ransomware.live/",
     "ransomware_live", "tier_2",
     ["cyber", "ransomware", "defence_sector", "current_threat"]),
]


async def seed_web_atlas(skip_if_populated: bool = True) -> dict:
    """Bootstrap the defence source catalogue into web_atlas.

    Args:
        skip_if_populated: If True (default), don't run if web_atlas
            already has sources — avoids re-adding on every restart.
            Pass False to force a re-seed (useful after curating the
            list above).

    Returns a summary dict: {added, skipped, errors, final_count}.
    """
    try:
        from . import web_atlas
    except Exception as e:
        return {"ok": False, "error": f"web_atlas import failed: {e}"}

    # Check current state — skip if already populated. web_atlas.stats()
    # exposes the family count under `source_families`; the previous
    # `total_sources`/`total` keys never existed, so the guard always
    # returned 0 and re-seeded on every boot. That fired ~30 brain_hook +
    # audit_log writes per startup with no new information (visible in
    # fly logs as a 30-line `source_atlas_update` storm at 09:21:00).
    if skip_if_populated:
        try:
            stats = await web_atlas.stats()
            existing_count = int(
                stats.get("source_families", 0)
                or stats.get("total_sources", 0)
                or stats.get("total", 0)
                or 0
            )
            if existing_count >= len(_DEFENCE_SOURCES) // 2:
                return {
                    "ok": True,
                    "action": "skipped",
                    "reason": f"web_atlas already has {existing_count} sources",
                    "final_count": existing_count,
                }
        except Exception:
            pass  # proceed with seeding anyway

    added = 0
    skipped = 0
    errors = 0
    for url, family, tier, tags in _DEFENCE_SOURCES:
        try:
            # R-F3660 — this was SILENT DATA CORRUPTION, not a dead call.
            # web_atlas.add_source is (url, tier, topic_tags, region=..., added_by=...)
            # and has NO `family` parameter — it derives family itself via
            # _source_family(url). So the keyword call always raised TypeError,
            # and the "older signature" fallback then bound POSITIONALLY as
            # url=url, tier=family, topic_tags=tier — writing the family string
            # into the tier field and the tier string into topic_tags. Because
            # add_source does `sorted(set(topics + topic_tags))`, a str in
            # topic_tags iterates its CHARACTERS, so every seeded defence source
            # was registered with a wrong tier and single-letter topics — while
            # `added += 1` counted it a success. Call the real signature; the
            # TypeError fallback is deleted because it is what let the
            # corruption through.
            await web_atlas.add_source(
                url=url,
                tier=tier,
                topic_tags=list(tags),
            )
            added += 1
        except Exception as e:
            msg = str(e).lower()
            if "already" in msg or "exists" in msg or "duplicate" in msg:
                skipped += 1
            else:
                errors += 1
                logger.debug("[defence_seed] failed on %s: %s", family, e)

    # Feed brain once on successful seed
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_atlas",
            summary=(
                f"Defence source seed: added={added} skipped={skipped} "
                f"errors={errors} of {len(_DEFENCE_SOURCES)} candidates"
            ),
            success=errors == 0,
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    logger.info(
        "[defence_seed] complete: added=%d skipped=%d errors=%d",
        added, skipped, errors,
    )
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="defence_source_seed",
        summary="Seed Web Atlas",
        source_id="defence_source_seed:R-F996",
    )
    if errors:
        wire_failure(
            module="defence_source_seed",
            detail=f"seed_web_atlas: {errors} source(s) failed to seed",
            gap_type="source_seed_failure",
            source="defence_source_seed:R-F2672",
        )

    return {
        "ok": errors == 0,
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "candidates": len(_DEFENCE_SOURCES),
    }


def catalogue_summary() -> dict:
    """Return a read-only summary of the curated catalogue — used for
    dashboard display + the capability_card."""
    by_tier: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    for _url, _family, tier, tags in _DEFENCE_SOURCES:
        by_tier[tier] = by_tier.get(tier, 0) + 1
        for t in tags:
            by_topic[t] = by_topic.get(t, 0) + 1
    return {
        "total_curated": len(_DEFENCE_SOURCES),
        "by_tier": by_tier,
        "top_topics": dict(
            sorted(by_topic.items(), key=lambda kv: -kv[1])[:10]
        ),
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
