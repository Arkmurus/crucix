"""R-F2269 — the defence-source seed catalogue carried ~26 dead/moved URLs that
the uptime sweep reported as "down" (404 / NXDOMAIN). These were replaced with
current, reachability-verified URLs. This test guards against the dead ones
creeping back and asserts the catalogue is intact + well-formed.
"""
import pytest

import aria_service.intel.defence_source_seed as seed


# domains/paths confirmed dead (NXDOMAIN or 404) that must never return
_DEAD_FRAGMENTS = [
    "riafan.ru",                       # tass_en — RIAFAN shut down 2023
    "novicias.uol",                    # uol_brazil — typo of "noticias"
    "buyandsell.gc.ca",                # ca_buyandsell — retired → CanadaBuys
    "atrocityforecastingproject",      # atrocity_forecasting — dead domain
    "ddpdod.gov.in",                   # in_ddp — domain changed → ddpmod
    "e-justice.europa.eu/content_business_registers",  # eu_business_registers 404 path
    "aselsan.com/en/press-room",       # aselsan_press 404 path
    "macauhub.com",                    # macauhub — dead .com.mo / broken-cert .com
    "https://c4defence.com/",          # c4defence apex has cert mismatch; use www path
    "koneps.go.kr/eng/",               # kr_koneps moved behind PPS English portal
    "thedrive.com/the-war-zone",       # The War Zone moved to twz.com
    "qinetiq.com/en/news",             # QinetiQ news path 404s
    "https://www.helsing.ai/news",     # Helsing moved newsroom path
    "www.dfat.gov.au/international-relations/security/sanctions",
    "https://www.gem.gov.in/",
]


def test_rf2269_no_dead_urls_remain():
    urls = [e[0] for e in seed._DEFENCE_SOURCES]
    blob = "\n".join(urls)
    for frag in _DEAD_FRAGMENTS:
        assert frag not in blob, f"dead URL fragment resurfaced in seed: {frag}"


def test_rf2269_replacements_present():
    byname = {e[1]: e[0] for e in seed._DEFENCE_SOURCES}
    expected = {
        "ca_buyandsell": "https://canadabuys.canada.ca/en",
        "tass_en": "https://tass.com/",
        "in_ddp": "https://www.ddpmod.gov.in/",
        "sanctionslist_io": "https://www.opensanctions.org/search/",
        "kr_dapa": "https://www.dapa.go.kr/dapa_en/main.do",
        "c4defence": "https://www.c4defence.com/en/home/",
        "kr_koneps": "https://www.pps.go.kr/eng/index.do",
        "the_war_zone": "https://www.twz.com/",
        "qinetiq_press": "https://www.qinetiq.com/",
        "helsing_press": "https://helsing.ai/newsroom",
        "macauhub": "https://www.macaubusiness.com/",
        "au_dfat_sanctions": "https://sanctions.dfat.gov.au/",
        "in_gem": "https://www.india.gov.in/services/details/explore-business-opportunities-on-government-e-marketplace-gem",
    }
    for name, url in expected.items():
        assert byname.get(name) == url, f"{name} should be {url}, got {byname.get(name)}"


def test_rf2269_catalogue_intact_and_wellformed():
    srcs = seed._DEFENCE_SOURCES
    assert len(srcs) == 200, f"catalogue size changed: {len(srcs)}"
    # every entry is (url, name, ...) with an https URL and a non-empty name
    for e in srcs:
        assert e[0].startswith("https://"), f"non-https seed URL: {e}"
        assert e[1] and isinstance(e[1], str)
    # names remain unique
    names = [e[1] for e in srcs]
    assert len(names) == len(set(names)), "duplicate source names introduced"


@pytest.mark.asyncio
async def test_rf2672_seed_errors_wire_failure(monkeypatch):
    """seed_web_atlas reports real seeding failures to the brain wiring sink."""
    from aria_service.intel import engine_wiring
    from aria_service.intel import web_atlas

    calls = []

    async def _stats():
        return {"source_families": 0}

    async def _add_source(**_kwargs):
        raise RuntimeError("atlas write failed")

    def _wire_failure(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(seed, "_DEFENCE_SOURCES", [("https://example.com/source", "example_source", "tier1", ["test"])])
    monkeypatch.setattr(web_atlas, "stats", _stats)
    monkeypatch.setattr(web_atlas, "add_source", _add_source)
    monkeypatch.setattr(engine_wiring, "wire_failure", _wire_failure)

    out = await seed.seed_web_atlas(skip_if_populated=False)

    assert out["ok"] is False
    assert out["errors"] == 1
    assert calls
    assert calls[0]["module"] == "defence_source_seed"
    assert calls[0]["gap_type"] == "source_seed_failure"
