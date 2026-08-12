"""C-35 / R-F3915 — the reliability producer ignored provenance the rest of the DD reads.

C-29 made the registry reliability EMA readable. It then measured, live, **one family
across the module's lifetime** despite 63 DD layer-runs in seven days. This is why.

`dd_orchestrator._record_source_reliability` skips any finding whose `url` is None.
But `Finding.url` was introduced by R-F2691 as **purely additive** — its own comment
states both the constraint and the scale: *"All optional → every existing construction
site keeps working unchanged"*, across *"~127 construction sites"*. Almost all of them
predate it and still carry provenance the original way, embedded in the `source`
string as ``"bailii [from https://www.bailii.org/ew/cases/...]"``.

That embedded url is **not** a display artifact — R-F2691 measured it as load-bearing:
`origin_key` / `_is_tier_1a_source` resolve it to `pub:bailii.org`, which is what
clears the R-5005 Tier-1a gate; a bare ``"bailii"`` yields `external_unclassified` and
FAILS it. So the DD pipeline already treats the suffix as authoritative provenance.
Only the reliability producer refused to look at it.

THE FIX IS NOT A 127-SITE SWEEP. R-F2691 correctly scoped that as separate work
because it touches the tier gate, and a careless pass would silently re-tier findings
across every report. The surgical fix is to make the *producer* read provenance the
way the rest of the system already does — by REUSING `registrable_domain` and
`origin_key` rather than writing a second parser. Writing a second one is exactly how
C-29 happened: two components each deciding independently what a source key looks like,
and drifting apart.

WHAT MUST NOT HAPPEN: attribution must never be invented. An internal compute label
(`ghost_scorer`, `network_walker`) and an unclassifiable source carry no publisher, and
recording reliability against them would credit ARIA's own machinery as an external
source. `origin_key` already draws exactly that line, so it is the gate used here.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import web_atlas as wa


class _Layer:
    def __init__(self, findings):
        self.findings = findings


class _Finding:
    def __init__(self, source="", url=None, confidence="CONFIRMED", gate_demoted=False):
        self.source = source
        self.url = url
        self.confidence = confidence
        self.gate_demoted = gate_demoted


class _Report:
    def __init__(self, layer_name, findings):
        self.layers_run = [layer_name]
        setattr(self, layer_name, _Layer(findings))


@pytest.fixture
def recorded(monkeypatch):
    """Capture what the producer asks web_atlas to record."""
    calls: list[tuple[str, str, bool]] = []

    async def _fake_record_ingest(url, topic, *, success=True):
        calls.append((url, topic, success))
        return {}

    monkeypatch.setattr(wa, "record_ingest", _fake_record_ingest)
    return calls


@pytest.mark.asyncio
async def test_embedded_from_url_provenance_is_recorded(recorded) -> None:
    """THE SYMPTOM: the shape ~127 construction sites actually produce.

    No structured `url`; the source string carries `[from <url>]`, which the R-5005
    tier gate already resolves. The producer must see it.
    """
    report = _Report("compliance", [
        _Finding(source="bailii [from https://www.bailii.org/ew/cases/EWHC/2024/1.html]")
    ])

    n = await ddo._record_source_reliability(report)

    assert n == 1, "C-35: embedded provenance ignored — the producer stays near-silent"
    url, topic, success = recorded[0]
    assert wa._source_family(url) == "bailii.org", (
        f"resolved to the wrong family: {wa._source_family(url)}"
    )
    assert topic == "compliance" and success is True


@pytest.mark.asyncio
async def test_structured_url_still_wins(recorded) -> None:
    """R-F2691's field remains authoritative where a site does supply it."""
    report = _Report("identity", [
        _Finding(
            source="companies_house [from https://example.invalid/x]",
            url="https://find-and-update.company-information.service.gov.uk/company/1",
        )
    ])

    await ddo._record_source_reliability(report)

    assert wa._source_family(recorded[0][0]) == (
        "find-and-update.company-information.service.gov.uk"
    ), "the structured url must take precedence over the embedded one"


@pytest.mark.asyncio
async def test_internal_compute_labels_are_never_credited(recorded) -> None:
    """ARIA's own machinery is not an external source.

    `ghost_scorer` and friends are internal compute. Crediting them would inflate the
    registry with families that do not exist, and `origin_key` already draws this
    line — which is why it is the gate rather than a hand-written blocklist.
    """
    report = _Report("digital", [
        _Finding(source="ghost_scorer"),
        _Finding(source="network_walker"),
        _Finding(source=""),
    ])

    n = await ddo._record_source_reliability(report)

    assert n == 0, f"internal/blank sources were credited as publishers: {recorded}"


@pytest.mark.asyncio
async def test_unclassifiable_bare_label_is_not_credited(recorded) -> None:
    """A bare label with no domain certifies no family — do not invent one."""
    report = _Report("network", [_Finding(source="some_analyst_note")])

    assert await ddo._record_source_reliability(report) == 0


@pytest.mark.asyncio
async def test_the_confidence_gate_is_unchanged(recorded) -> None:
    """C-35 widens WHERE provenance is read, never WHAT qualifies.

    Only a gate-cleared CONFIRMED finding means the source's claim held up; a demoted
    or lower-confidence finding must still record nothing, or the EMA starts crediting
    sources for claims that never passed verification.
    """
    report = _Report("compliance", [
        _Finding(source="bailii [from https://www.bailii.org/a]", confidence="ASSESSED"),
        _Finding(source="ofac [from https://sanctionssearch.ofac.treas.gov/x]",
                 confidence="CONFIRMED", gate_demoted=True),
    ])

    assert await ddo._record_source_reliability(report) == 0, (
        "C-35 must not relax the R-5005 confidence gate"
    )


@pytest.mark.asyncio
async def test_dedup_still_holds_per_family_and_layer(recorded) -> None:
    """One observation per (family, layer) per run — a single report must not be able
    to inflate the EMA by repeating one source across N findings."""
    report = _Report("compliance", [
        _Finding(source="bailii [from https://www.bailii.org/a]"),
        _Finding(source="bailii [from https://www.bailii.org/b]"),
        _Finding(source="bailii [from https://bailii.org/c]"),
    ])

    assert await ddo._record_source_reliability(report) == 1, (
        f"deduplication broke: {recorded}"
    )
