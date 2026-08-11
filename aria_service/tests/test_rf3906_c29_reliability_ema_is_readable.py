"""C-29 / R-F3906 — the registry reliability EMA was STRUCTURALLY BLIND.

THE SYMPTOM (measured live 2026-08-11 on build_rev c35fbc0e, aria-intel):

    GET /api/aria/atlas/stats            -> topics_tracked: 1
    GET /api/aria/atlas/rank?topic=identity
        -> find-and-update.company-information.service.gov.uk
           confirmed: 21, score: 0.9954, last_update 2026-08-06
    GET /api/aria/source_validator/health
        -> that SAME family: bucket=unmeasured, samples=0
           healthy=0 degraded=0 failing=0 dead=0 UNMEASURED=194 of 194

Twenty-one real observations existed and the panel built to display them
reported the source as never measured.

CAUSE — a producer/consumer KEY-SPACE MISMATCH:

  * producer  `dd_orchestrator._record_source_reliability` (R-F2735) calls
    `web_atlas.record_ingest(url, layer_name)`, which writes
        aria:atlas:reliability:{family}:{DD_LAYER_NAME}
  * consumer  `source_validator._measured_reliability` read
        aria:atlas:reliability:{family}:{CATALOGUE_TOPIC_TAG}
    enumerating the topics the family was TAGGED with at seed time.

The 12 DD layer names and the 94 seeded catalogue topic tags intersect on
exactly ONE token, `compliance`, so 11 of 12 layers wrote to keys no consumer
would ever read — and only 50 of 200 families even carry that tag, leaving 150
structurally unmeasurable no matter what a DD did.

The consumer was enumerating the WRONG UNIVERSE: it assumed the set of topics a
family has OBSERVATIONS under equals the set it was TAGGED with. Those are
different things — tags describe editorial coverage, observations are recorded
per DD layer.

R-F3254/R-F3255 are NOT the bug: they correctly stopped the panel reporting an
unmeasured source as `0.5 = failing` and gave `unmeasured` its own bucket. They
made the emptiness honest. Nobody checked whether the wire underneath was
connected, so the fix made a permanent blindness legible instead of curing it.

THE FIX: the consumer reads WHAT WAS WRITTEN (one prefix scan of
`aria:atlas:reliability:*`, grouped by family) instead of guessing a vocabulary.
That is why `test_novel_vocabulary_is_still_visible` matters more than the
symptom test — it is the guard that stops this CLASS of defect recurring the
next time anyone records under a name the catalogue never heard of.

Same family as the three Phase A gates "certified by an absence" (CLAUDE.md §1),
`route_audit` returning {} for a 770-route app (§16), and the cost meter reading
$0.00 through a store-less process (§17): an instrument that cannot see is
indistinguishable from a clean reading.
"""
from __future__ import annotations

import fnmatch

import pytest

from aria_service.intel import source_validator as sv
from aria_service.intel import web_atlas as wa
from aria_service.intel.redis_store import StoreReadError


class SharedFakeStore:
    """One store both modules bind to, so the producer's writes are exactly what
    the consumer reads. A fake that split them could not detect this defect.

    Mirrors the real contracts that matter here:
      * `scan_keys(pattern)` glob-matches and is capped by `count`
      * `get_json_strict` raises on store failure, returns None on genuine absence
    """

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.fail_reads = False

    async def get_json(self, key: str):
        if self.fail_reads:
            return None  # the R-F1 None-on-error contract
        return self.data.get(key)

    async def get_json_strict(self, key: str):
        if self.fail_reads:
            raise StoreReadError(f"store unreadable: {key}")
        return self.data.get(key)

    async def set_json(self, key: str, obj, ex=None, keepttl=False) -> None:
        self.data[key] = obj

    async def scan_keys(self, pattern: str, count: int = 200) -> list[str]:
        if self.fail_reads:
            return []  # scan_keys CANNOT signal failure — it returns []
        return [k for k in self.data if fnmatch.fnmatch(k, pattern)][:count]


@pytest.fixture
def store(monkeypatch):
    st = SharedFakeStore()
    monkeypatch.setattr(sv, "rs", st)
    monkeypatch.setattr(wa, "rs", st)
    return st


def _seed_family(store: SharedFakeStore, family: str, topics: list[str], tier: str = "tier_1a") -> None:
    """Register a family the way the defence seed does: catalogue TAGS only."""
    store.data["aria:atlas:index:families"] = sorted(
        set(store.data.get("aria:atlas:index:families") or []) | {family}
    )
    store.data[f"aria:atlas:source:{family}"] = {
        "family": family, "tier": tier, "topics": topics, "last_ok": None,
    }


def _bucket_of(report: dict, family: str) -> str | None:
    for name in ("top_performers", "degraded", "failing", "dead", "unmeasured"):
        for row in report.get(name) or []:
            if row.get("family") == family:
                return name
    return None


# ─────────────────────────────────────────────────────────────────────────────
# THE SYMPTOM — the live production reading, reproduced end-to-end.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dd_layer_observation_is_visible_to_the_registry_report(store) -> None:
    """Records through the REAL producer, reads through the REAL consumer.

    This is the exact live shape: Companies House, seeded with catalogue tags
    that do not include `identity`, then observed 21x by the DD identity layer.
    """
    family = "find-and-update.company-information.service.gov.uk"
    url = f"https://{family}/company/12345678"
    # Seeded with CATALOGUE tags — none of which is a DD layer name.
    _seed_family(store, family, topics=["registry", "uk", "compliance"])

    # The producer: dd_orchestrator calls exactly this, with the LAYER name.
    for _ in range(21):
        await wa.record_ingest(url, "identity", success=True)

    # Precondition: the observation really was written (guards against a test
    # that passes because the producer silently no-opped).
    written = store.data.get(f"aria:atlas:reliability:{family}:identity")
    assert written is not None, "producer wrote nothing — test proves nothing"
    assert written["confirmed"] == 21

    report = await sv.registry_health_report()

    bucket = _bucket_of(report, family)
    assert bucket is not None, f"family absent from every bucket: {report}"
    assert bucket != "unmeasured", (
        "REGRESSION C-29: 21 recorded observations reported as never measured. "
        "The consumer is enumerating catalogue tags instead of written keys."
    )
    assert report["unmeasured_count"] == 0
    assert report["healthy_count"] == 1


@pytest.mark.asyncio
async def test_novel_vocabulary_is_still_visible(store) -> None:
    """THE CLASS GUARD — this is the test that must never be deleted.

    The consumer must not depend on ANY shared vocabulary with the producer. A
    topic the catalogue has never heard of must still be measured, otherwise the
    next producer to record under a new name reintroduces C-29 silently.

    Do NOT 'fix' a failure here by adding the new name to a list in
    source_validator — a hand-maintained vocabulary is the defect, not the cure
    (cf. CLAUDE.md §27d on hand-maintained engine lists).
    """
    family = "example-registry.gov"
    _seed_family(store, family, topics=["registry"])

    await wa.record_ingest(f"https://{family}/x", "a_layer_invented_tomorrow", success=True)

    report = await sv.registry_health_report()
    assert _bucket_of(report, family) != "unmeasured", (
        "consumer is coupled to a known vocabulary — C-29 will recur"
    )


# ─────────────────────────────────────────────────────────────────────────────
# HONESTY REGRESSION GUARDS — the fix must not buy visibility with fabrication.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_genuinely_unmeasured_family_stays_unmeasured(store) -> None:
    """R-F3254 must survive: absent is not false, and it is not healthy either."""
    family = "never-observed.example"
    _seed_family(store, family, topics=["compliance", "defence"])

    report = await sv.registry_health_report()

    assert _bucket_of(report, family) == "unmeasured"
    assert report["unmeasured_count"] == 1
    assert report["healthy_count"] == report["failing_count"] == 0
    row = next(r for r in report["unmeasured"] if r["family"] == family)
    assert row["overall_health"] is None, "a source nobody sampled carries NO score"
    assert row["samples"] == 0


@pytest.mark.asyncio
async def test_a_contradicted_source_is_not_laundered_into_healthy(store) -> None:
    """Reading written keys must not flatten a bad score into a good one."""
    family = "unreliable.example"
    _seed_family(store, family, topics=["geopolitics"])
    for _ in range(30):
        await wa.record_ingest(f"https://{family}/y", "network", success=False)

    report = await sv.registry_health_report()
    bucket = _bucket_of(report, family)
    assert bucket in {"failing", "dead"}, f"expected a poor verdict, got {bucket}"
    assert report["healthy_count"] == 0


@pytest.mark.parametrize(
    "netloc",
    [
        "example.com",          # the ordinary case
        "example.com:8080",     # PORT — `_source_family` returns urlparse().netloc,
        "[::1]:9",              # which keeps it. IPv6 literals carry colons too.
    ],
)
@pytest.mark.asyncio
async def test_family_containing_a_colon_is_still_grouped_correctly(store, netloc) -> None:
    """C-29 edge case, found by taint analysis of the fix itself.

    The reliability key is `{prefix}{family}:{topic}` and the family is
    `urlparse(url).netloc`, WHICH KEEPS THE PORT. Splitting that key at the FIRST
    colon files `example.com:8080`'s observation under `example.com` and leaves the
    real family with zero — C-29 reproduced for every ported or IPv6 source, by the
    fix for C-29. Split at the LAST colon and confirm against the family index.
    """
    _seed_family(store, netloc, topics=["registry"])
    await wa.record_ingest(f"https://{netloc}/x", "identity", success=True)

    assert f"aria:atlas:reliability:{netloc}:identity" in store.data

    report = await sv.registry_health_report()
    assert _bucket_of(report, netloc) != "unmeasured", (
        f"observation for family {netloc!r} was mis-grouped and lost"
    )
    assert report["unmeasured_count"] == 0


@pytest.mark.asyncio
async def test_grouping_is_correct_without_the_family_index(store) -> None:
    """Isolates the last-colon split from the family-index fallback.

    With `known_families=None` there is no index to correct a bad split, so this
    fails outright if the key is ever split at the first colon again. Without this
    test the two protections mask each other and neither is really guarded.
    """
    store.data["aria:atlas:reliability:example.com:8080:identity"] = {"score": 0.9}
    store.data["aria:atlas:reliability:example.com:identity"] = {"score": 0.9}

    by_family, complete = await sv._observed_reliability_keys(None)

    assert complete is True
    assert set(by_family) == {"example.com:8080", "example.com"}, (
        f"ported family mis-grouped without index help: {sorted(by_family)}"
    )
    assert by_family["example.com:8080"] == [
        "aria:atlas:reliability:example.com:8080:identity"
    ]


@pytest.mark.asyncio
async def test_topic_containing_a_colon_resolves_via_the_family_index(store) -> None:
    """`_normalise_topic` does not strip colons, so the last-colon split alone is
    not sufficient. The family index is the authority and must win."""
    family = "ported.example:8443"
    _seed_family(store, family, topics=["registry"])
    # A topic that itself carries a colon — defeats rpartition on its own.
    store.data[f"aria:atlas:reliability:{family}:weird:topic"] = {"score": 0.92}

    report = await sv.registry_health_report()
    assert _bucket_of(report, family) == "top_performers", (
        "family-index resolution failed for a colon-bearing topic"
    )


@pytest.mark.asyncio
async def test_suspend_failing_sources_can_finally_see_a_failing_source(store) -> None:
    """The SECOND blind consumer.

    `suspend_failing_sources` shares `_measured_reliability`, so it was blind in
    exactly the same way: `overall` was None for every family, the `overall is
    None` guard short-circuited, and auto-suspend could NEVER fire whatever the
    threshold. "Never silently trust a failing source" was unenforceable.
    """
    family = "rotten.example"
    _seed_family(store, family, topics=["geopolitics"])
    for _ in range(40):
        await wa.record_ingest(f"https://{family}/z", "sanctions_divergence", success=False)

    result = await sv.suspend_failing_sources(threshold=0.40)

    assert result["suspended"] == 1, f"auto-suspend still blind: {result}"
    assert family in result["families"]
    assert store.data[f"aria:atlas:source:{family}"]["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_suspend_never_fires_on_an_unreadable_store(store) -> None:
    """A wedged store must not be able to suspend anything, and the failure return
    must keep the success shape (`suspended` is a COUNT, `families` the list) so a
    caller's `result["suspended"] > 0` cannot raise on the failure path."""
    _seed_family(store, "some.example", topics=["compliance"])
    store.fail_reads = True

    result = await sv.suspend_failing_sources(threshold=0.99)

    assert result["store_readable"] is False
    assert result["families"] == []
    # The shape contract: `suspended` must be an int on BOTH paths, so the
    # ordinary caller idiom below cannot raise TypeError when the store is down.
    assert isinstance(result["suspended"], int)
    assert (result["suspended"] > 0) is False


@pytest.mark.asyncio
async def test_unreadable_store_is_declared_not_reported_as_unmeasured(store) -> None:
    """An unreadable store must NEVER render as 'nobody has measured anything'.

    `scan_keys` returns [] on failure exactly as it does on a genuinely empty
    keyspace, so the fix would otherwise relocate C-29 rather than cure it: a
    wedged store would report all 194 families unmeasured and read as a fact.
    """
    _seed_family(store, "some.example", topics=["compliance"])
    store.fail_reads = True

    report = await sv.registry_health_report()

    assert report.get("store_readable") is False, (
        "an unreadable store reported as a measurement — C-29 relocated, not cured"
    )
