"""R-F3820 — a domain may only enter the permanent registry if it is ON MISSION.

THE DEFECT, measured live on 2026-08-09.

ARIA was crawling porn. Confirmed from the production machine, not inferred:
`GET https://jerk-porn.com/` with `User-Agent: ARIA-Intel/1.0 (research crawler)`.
The registry held 163 adult domains and 41 gambling domains, ALL enabled and swept
every 6 hours, and the pages reached the brain: porn titles were absorbed as
`reading_region:market_intel:lusophone` facts — "market intel Angola Mozambique: Fake
Taxi Uk Porn Videos | Pornhub.com" — and graded regional mastery, which is Phase A
gate #2.

Root cause, three lines: `researcher.py` registers the domain of EVERY external
search result, `auto_register_domain` accepts anything `_safe_domain_for_register`
does not reject, and that function only checks length, numeric labels and RFC-2606
placeholders. There is no notion of what ARIA is FOR. Combined with §7 (no TTL, no
eviction, never delete), one SEO-spam SERP puts a porn farm in the registry forever.

WHY THIS IS A RELEVANCE GATE AND NOT A BLOCKLIST — this is the whole design, and it
was decided from measurement rather than taste. A substring blocklist of adult terms
was tried first and it flagged, from ARIA's OWN live data:

    internationaldefenceanalysis.com   "anal" inside ANALysis
    stockanalysis.com                  "anal"
    repository.essex.ac.uk             "sex"  inside esSEX
    "The Defense Post", "ASPI Strategist", "Africa Confidential"
    "Brazilian frigate Tamandare ... Fraterno XXXIX"   "xxx" in a Roman numeral
    "Fersensporn: Endlich schmerzfrei"                 German for HEEL SPUR

Those are core defence sources. A blocklist is also endless — a new porn farm is a
new string — whereas an on-mission test excludes porn, gambling, Amazon and consumer
noise BY CONSTRUCTION, because none of them are ever about defence.

THE ASYMMETRY THAT SETS THE DEFAULT. §7 means a wrongly ADMITTED domain is permanent;
a wrongly REJECTED one is simply re-encountered later with better evidence. So the
gate fails CLOSED, including when a caller supplies no evidence at all — forgetting to
pass evidence must DENY, never admit.
"""
from __future__ import annotations

import pytest

from aria_service.crawler import on_demand


# ── the discriminator itself ─────────────────────────────────────────────────

OFF_MISSION = [
    ("porn", "Fake Taxi Uk Porn Videos | Pornhub.com"),
    ("porn", "Indonesia Porn - Indonesia Bokep & Streaming Bokep Indonesia"),
    ("porn", "Free Taboo porn videos - Incest Porn"),
    ("gambling", "CasinoWhizz: Trusted Online Casino Reviews & Payout Tests"),
    ("retail", "Amazon.com: Best Sellers in Electronics"),
    ("retail", "Timbuk2 Commute messenger bag (Cambridge) $80"),
    ("medical", "Fersensporn: Endlich schmerzfrei - das hilft wirklich"),
]

ON_MISSION = [
    ("procurement", "Poland awarded a contract to Rheinmetall for 200 armoured vehicles"),
    ("peacekeeping", "South Africa to end 27-year DR Congo UN peacekeeping mission"),
    ("sanctions", "EU sanctions three entities over Russian arms procurement"),
    ("defence media", "The Defense Post - Global defense news, analysis and opinion"),
    ("compliance", "Companies House filing shows new director at defence supplier"),
]


@pytest.mark.parametrize("kind,evidence", OFF_MISSION, ids=[k for k, _ in OFF_MISSION])
def test_off_mission_evidence_is_refused(kind, evidence):
    ok, reason = on_demand._registration_is_on_mission("example.com", evidence)
    assert ok is False, f"{kind}: {evidence!r} must not earn a permanent registry row"
    assert reason, "a refusal must carry a reason — an unexplained drop is not auditable"


@pytest.mark.parametrize("kind,evidence", ON_MISSION, ids=[k for k, _ in ON_MISSION])
def test_on_mission_evidence_is_admitted(kind, evidence):
    """The half that keeps this a GATE and not a wall. The expensive error for a
    collection system is the false negative — intel lost invisibly."""
    ok, reason = on_demand._registration_is_on_mission("example.com", evidence)
    assert ok is True, f"{kind}: {evidence!r} is exactly what ARIA is for — got {reason}"


def test_absent_evidence_fails_CLOSED():
    """§7 makes admission permanent and rejection cheap, so forgetting to pass
    evidence must DENY. If this ever flips to admit, the gate is bypassable by
    omission — which is how the original hole worked."""
    for empty in ("", "   ", None):
        ok, _ = on_demand._registration_is_on_mission("example.com", empty)
        assert ok is False, f"empty evidence {empty!r} must not admit a domain"


# ── the registrar must actually consult it ───────────────────────────────────

@pytest.mark.asyncio
async def test_auto_register_refuses_an_off_mission_domain(monkeypatch):
    """CAPABILITY: drive the real registrar. A porn SERP hit must not create a row."""
    created = []

    async def _no_existing(_d):
        return None

    async def _register(**kw):
        created.append(kw.get("domain"))

    monkeypatch.setattr(on_demand.db, "get_domain", _no_existing)
    monkeypatch.setattr(on_demand.db, "register_domain", _register)

    made = await on_demand.auto_register_domain(
        "jerk-porn.com", evidence="Free Taboo porn videos - Incest Porn")
    assert made is False
    assert created == [], "an off-mission domain was written to the permanent registry"


@pytest.mark.asyncio
async def test_auto_register_admits_an_on_mission_domain(monkeypatch):
    created = []

    async def _no_existing(_d):
        return None

    async def _register(**kw):
        created.append(kw.get("domain"))

    monkeypatch.setattr(on_demand.db, "get_domain", _no_existing)
    monkeypatch.setattr(on_demand.db, "register_domain", _register)

    made = await on_demand.auto_register_domain(
        "janes.com",
        evidence="Poland awarded a contract to Rheinmetall for 200 armoured vehicles")
    assert made is True
    assert created == ["janes.com"]


@pytest.mark.asyncio
async def test_auto_register_without_evidence_registers_nothing(monkeypatch):
    """The bypass-by-omission case, driven through the real entry point."""
    created = []

    async def _no_existing(_d):
        return None

    async def _register(**kw):
        created.append(kw.get("domain"))

    monkeypatch.setattr(on_demand.db, "get_domain", _no_existing)
    monkeypatch.setattr(on_demand.db, "register_domain", _register)

    assert await on_demand.auto_register_domain("anything.com") is False
    assert created == []


# ── the caller that caused it must pass evidence ─────────────────────────────

def test_the_researcher_serp_loop_passes_evidence():
    """R-F507's loop registered `domain_of(link)` and discarded the title/snippet —
    it threw away the only signal that could have judged the domain. Pinned by
    source: the registration call must carry evidence."""
    from aria_service.tests._source_probe import module_source

    import aria_service.intel.researcher as researcher

    src = module_source(researcher)
    assert "auto_register_domain(" in src
    for call in src.split("auto_register_domain(")[1:]:
        head = call[:200]
        if head.lstrip().startswith(("looks_like_entity_query", "\n")):
            continue          # the import line, not a call
        assert "evidence" in head, (
            "researcher.py must pass evidence to auto_register_domain — without it "
            "the gate fails closed and discovery silently stops")


# ── the half that protects the PRODUCT, not just the registry ────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("entity", [
    "Acme Ventures Ltd",        # scores 0.0 on the defence lexicon
    "Gazprom",                  # scores 0.0
    "Modirum Gespi",            # the rf507 fixture entity, scores 0.0
])
async def test_a_requested_entity_is_still_discoverable(entity, monkeypatch):
    """R-F3820 — due diligence is the business of investigating names nobody has
    heard of, so an unknown counterparty MUST still be registerable.

    Relevance-gating this path would have disabled the product's primary function
    while looking like a safety improvement. Measured before the split: all three of
    these score zero, so a single relevance gate would refuse every one of them.

    Safe because provenance differs — `guess_entity_urls` derives candidates from the
    QUERY STRING, so this path cannot admit jerk-porn.com unless somebody explicitly
    asks ARIA to research it. The porn arrived as unsolicited SERP domains.
    """
    created = []

    async def _no_existing(_d):
        return None

    async def _register(**kw):
        created.append(kw.get("domain"))

    monkeypatch.setattr(on_demand.db, "get_domain", _no_existing)
    monkeypatch.setattr(on_demand.db, "register_domain", _register)

    made = await on_demand.auto_register_domain("counterparty.example",
                                                requested_entity=entity)
    assert made is True, f"{entity!r} must remain discoverable — DD depends on it"
    assert created == ["counterparty.example"]


@pytest.mark.asyncio
async def test_the_entity_path_does_not_become_a_blanket_bypass(monkeypatch):
    """An empty requested_entity must NOT count as a request — otherwise the
    parameter is just the old unguarded behaviour with a new name."""
    created = []

    async def _no_existing(_d):
        return None

    async def _register(**kw):
        created.append(kw.get("domain"))

    monkeypatch.setattr(on_demand.db, "get_domain", _no_existing)
    monkeypatch.setattr(on_demand.db, "register_domain", _register)

    for blank in ("", "   "):
        assert await on_demand.auto_register_domain("x.com", requested_entity=blank) is False
    assert created == []
