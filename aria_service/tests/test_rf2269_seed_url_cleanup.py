"""R-F2269 — the defence-source seed catalogue carried ~26 dead/moved URLs that
the uptime sweep reported as "down" (404 / NXDOMAIN). These were replaced with
current, reachability-verified URLs. This test guards against the dead ones
creeping back and asserts the catalogue is intact + well-formed.
"""
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
        "macauhub": "https://www.macaubusiness.com/",
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
