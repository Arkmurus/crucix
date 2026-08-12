"""C-38 / R-F3925 — C-35's provenance fallback invented publishers and split real ones.

Found by the high-effort review of the C-29..C-36 fixes. Two CONFIRMED defects, both
inside R-F3915 (C-35), neither yet realised in production only because no DD had
finalised since the deploy.

FINDING 5 — INTERNAL MODULE LABELS WERE ENROLLED AS EXTERNAL PUBLISHERS.
C-35 gated on `origin_key(source).startswith("pub:")`, believing `pub:` meant "a real
external publisher". It does not. `origin_key` tests for a dot BEFORE it tests
`_is_internal`, so any dotted internal label passes. Measured with the real resolvers:

    origin_key('sources.ofac_sdn')          -> 'pub:sources.ofac_sdn'
    origin_key('sanctions.person_screen')   -> 'pub:sanctions.person_screen'
    origin_key('companies_house.charges')   -> 'pub:companies_house.charges'
    origin_key('ghost_scorer')              -> 'internal'      (only the UNDOTTED case)

`dotted.module_label` is the overwhelmingly common `Finding.source` format in
dd_orchestrator (`sources.ofac_sdn` at :3064, `sanctions.person_screen`, …), so the
fallback would have written `https://sources.ofac_sdn/` into web_atlas and enrolled
ARIA's own internal compute as an external source family with a reliability score —
inventing publishers that do not exist, in the registry a DD cites.

FINDING 7 — ONE PUBLISHER SPLIT ACROSS TWO FAMILIES, AND SEVERAL MERGED INTO ONE.
The fallback rebuilt a URL from `registrable_domain`, which STRIPS SUBDOMAINS, while
the `f.url` branch passes the raw URL and `web_atlas._source_family` keeps the full
netloc. Measured:

    f.url branch   -> find-and-update.company-information.service.gov.uk
    fallback       -> service.gov.uk

So Companies House accumulates two separate EMAs with half the samples each, and every
unrelated `*.service.gov.uk` publisher is merged into that second family. The registry
both fragments one source and conflates others.

THE ROOT FIX IS THE SAME FOR BOTH, AND IT IS SMALLER THAN WHAT IT REPLACES: extract
the REAL url from the `[from <url>]` suffix and hand it to `record_ingest` unchanged,
exactly as the `f.url` branch does. Then one function — `_source_family` — derives the
family for both paths, so they cannot disagree; and a bare label carries no url, so it
is skipped without needing `origin_key` to adjudicate anything.

The discarded approach is the lesson: C-35 RECONSTRUCTED a URL from a domain, and a
reconstruction is a second derivation of the same fact. C-29 was caused by exactly
that — two components each deciding independently what a source key looks like.
Reusing `origin_key`/`registrable_domain` felt like "reuse the canonical resolver",
but those answer a DIFFERENT question (independence grouping), and borrowing an answer
to a different question is how this class of defect keeps recurring.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import web_atlas as wa


class _Layer:
    def __init__(self, findings):
        self.findings = findings


class _F:
    def __init__(self, source="", url=None, confidence="CONFIRMED", gate_demoted=False):
        self.source = source
        self.url = url
        self.confidence = confidence
        self.gate_demoted = gate_demoted


class _Report:
    def __init__(self, layer, findings):
        self.layers_run = [layer]
        setattr(self, layer, _Layer(findings))


@pytest.fixture
def recorded(monkeypatch):
    calls: list[tuple[str, str, bool]] = []

    async def _fake(url, topic, *, success=True):
        calls.append((url, topic, success))
        return {}

    monkeypatch.setattr(wa, "record_ingest", _fake)
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# FINDING 5 — no fabricated publishers.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "label",
    [
        "sources.ofac_sdn",            # origin_key says pub: — it is NOT a publisher
        "sanctions.person_screen",
        "companies_house.charges",
        "ghost_scorer",                # undotted internal (already handled)
        "network_walker",
        "some_analyst_note",
    ],
)
@pytest.mark.asyncio
async def test_a_bare_label_never_becomes_a_publisher(recorded, label) -> None:
    """No URL in the source string means no attributable publisher. Full stop.

    Do NOT re-introduce an `origin_key`-based allowlist here: it answers a different
    question (independence grouping) and returns `pub:` for any dotted string.
    """
    report = _Report("compliance", [_F(source=label)])

    n = await ddo._record_source_reliability(report)

    assert n == 0, f"{label!r} was enrolled into web_atlas as a source: {recorded}"
    assert recorded == []


@pytest.mark.asyncio
async def test_provenance_url_resolver_rejects_bare_labels_directly() -> None:
    """Unit-level, so the failure points at the resolver rather than the caller."""
    for label in ("sources.ofac_sdn", "companies_house.charges", "ghost_scorer", ""):
        assert ddo._finding_provenance_url(_F(source=label)) is None, label


# ─────────────────────────────────────────────────────────────────────────────
# FINDING 7 — one publisher, one family, whichever path supplied it.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embedded_and_structured_provenance_agree_on_the_family(recorded) -> None:
    """THE SPLIT: the same source must not land in two reliability families.

    Companies House is the live case — 21 real observations sit under the full
    netloc, and the C-35 fallback would have opened a second `service.gov.uk` family
    beside it, each with half the samples.
    """
    full = "find-and-update.company-information.service.gov.uk"
    url = f"https://{full}/company/12345678"

    via_url = ddo._finding_provenance_url(_F(source="companies_house", url=url))
    via_suffix = ddo._finding_provenance_url(_F(source=f"companies_house [from {url}]"))

    assert wa._source_family(via_url) == wa._source_family(via_suffix) == full, (
        "the two provenance paths derive different families for one publisher"
    )


@pytest.mark.asyncio
async def test_subdomains_are_not_collapsed_into_a_shared_family(recorded) -> None:
    """THE MERGE: distinct publishers under one registrable domain stay distinct."""
    a = ddo._finding_provenance_url(_F(source="x [from https://alpha.service.gov.uk/1]"))
    b = ddo._finding_provenance_url(_F(source="y [from https://beta.service.gov.uk/2]"))

    assert wa._source_family(a) != wa._source_family(b), (
        "two unrelated *.service.gov.uk publishers were merged into one family"
    )


@pytest.mark.asyncio
async def test_embedded_url_is_still_recorded(recorded) -> None:
    """C-35's actual purpose must survive the fix."""
    report = _Report(
        "compliance",
        [_F(source="bailii [from https://www.bailii.org/ew/cases/EWHC/2024/1.html]")],
    )

    assert await ddo._record_source_reliability(report) == 1
    assert wa._source_family(recorded[0][0]) == "bailii.org"


@pytest.mark.asyncio
async def test_confidence_gate_and_dedup_still_hold(recorded) -> None:
    """Widening WHERE provenance is read must not widen WHAT qualifies."""
    demoted = _Report("compliance", [
        _F(source="a [from https://www.bailii.org/a]", confidence="ASSESSED"),
        _F(source="b [from https://www.bailii.org/b]", gate_demoted=True),
    ])
    assert await ddo._record_source_reliability(demoted) == 0

    dupes = _Report("compliance", [
        _F(source="a [from https://www.bailii.org/a]"),
        _F(source="b [from https://www.bailii.org/b]"),
    ])
    assert await ddo._record_source_reliability(dupes) == 1
