"""R-F3528 / R-F3529 — the OpenSanctions monthly quota, and what screening does about it.

TWO DEFECTS, both found by probing the LIVE system rather than by reading.

R-F3528 — a 429 is two different failures wearing one status code.
Probed live 2026-07-30 with the production key, one request:

    HTTP 429
    {"detail":"This API key has exceeded its rate limit for the month.
               Please wait to retry or contact support for a higher limit."}

That is not the per-second limit the R-F3476 pacing exists to avoid; it is the
PLAN allowance, and it stays spent until the month rolls or the plan is upgraded.
Both were reported as reason="rate_limit", so the DD obstacle line told the reader
ARIA was going too fast when the truth was that the plan was exhausted — a wrong
cause pointing at a wrong fix, when the only real fix is the operator's.

What was NOT wrong, checked before changing it: the R-F469 breaker does not churn
at 300s. R-F1834 already backs it off exponentially to a 24h cap. The first draft
of this change added a second cooldown on that false premise; it was removed.

R-F3529 — the DD screen depended solely on the metered aggregator.
`screen_with_aliases` / `fuzzy_screen` query OpenSanctions and nothing else, so
with the quota spent, DD sanctions screening is unavailable. Meanwhile, on the
same production box, the LOCAL canonical store answers correctly:

    POST /api/aria/compliance/screen {"entity_name":"Islamic Revolutionary Guard Corps"}
    -> status BLOCKED, matched against ofac_sdn AND eu_consolidated

Free, authoritative primary lists, already loaded, already right — and the
due-diligence path did not consult them. §6 puts the burden of proof on any
third-party; §15 is pay-once-remember-forever. OpenSanctions stays PRIMARY for
breadth; this is the floor beneath it.

THE PROPERTY THAT GOVERNS BOTH, and most of this file:
a fallback must never convert "could not screen" into "clean". That is the single
worst output a compliance tool can emit, and adding a second source is exactly the
kind of change that could introduce it. check_sanctions is used precisely because
it refuses — empty store, partial coverage, or stale data all yield
INSUFFICIENT_DATA, never CLEAR — so `screened` flips to True only when the local
store genuinely answered.
"""
from __future__ import annotations

import pytest

from aria_service.intel import sanctions as S
from aria_service.intel._sanctions_classify import is_corroborated_match


class TestTheTwo429sAreDistinguished:
    """Verbatim from the live response body — the only available signal.
    This endpoint sends no Retry-After and no RateLimit-* header (checked)."""

    _LIVE_QUOTA_BODY = ('{"detail":"This API key has exceeded its rate limit for '
                        'the month. Please wait to retry or contact support for a '
                        'higher limit."}')

    def test_the_live_monthly_body_is_classified_as_quota(self):
        assert S._classify_429(self._LIVE_QUOTA_BODY) == "quota_exhausted"

    def test_a_per_second_limit_stays_transient(self):
        assert S._classify_429("Rate limit exceeded, slow down") == "rate_limit"

    def test_an_unreadable_body_takes_the_RECOVERABLE_reading(self):
        """Ambiguity must not silently disable screening for weeks. Mislabelling a
        blip as a month-long outage is far more costly than the reverse."""
        for body in ("", "   ", "429", None):
            assert S._classify_429(body) == "rate_limit", body


class TestALocalHitCanActuallyBlock:
    """The shape is load-bearing. is_corroborated_match decides whether a match may
    BLOCK, and reads `lists` and `string_similarity`. Get `lists` wrong and a real
    OFAC designation is silently demoted to a 'related name observation' — a false
    clean produced by a fix intended to prevent one."""

    def test_an_ofac_hit_is_blocking(self):
        m = S._local_match_to_aria(
            {"source": "ofac_sdn", "formatted_name": "ROSOBORONEXPORT",
             "match_score": 1.0, "match_method": "exact",
             "countries": ["Russia"]}, "Rosoboronexport")
        assert is_corroborated_match(m), (
            "a local OFAC SDN designation could not drive a blocking verdict — "
            "it would be reported as a mere related-name observation"
        )

    def test_an_eu_consolidated_hit_is_blocking(self):
        m = S._local_match_to_aria(
            {"source": "eu_consolidated", "formatted_name": "X",
             "match_score": 0.9}, "X")
        assert is_corroborated_match(m)

    def test_a_weak_name_match_is_not_blocking(self):
        """The R-F2840 discipline still applies to local hits."""
        m = S._local_match_to_aria(
            {"source": "ofac_sdn", "formatted_name": "Y", "match_score": 0.30}, "Y")
        assert not is_corroborated_match(m)

    def test_the_match_says_where_it_came_from(self):
        m = S._local_match_to_aria({"source": "ofac_sdn", "match_score": 1.0}, "Z")
        assert m["source_kind"] == "local_canonical", (
            "an auditor cannot tell which source carried the finding"
        )
        assert m["matched_via_variant"] == "local_canonical"


def _no_opensanctions(monkeypatch, reason="quota_exhausted"):
    """Every OpenSanctions call fails — the live condition being fixed."""
    async def _dead_match(name, entity_type="Thing"):
        return S._SourceQuery([], False, reason)

    async def _dead_search(query, limit=5):
        return S._SourceQuery([], False, reason)

    monkeypatch.setattr(S, "_opensanctions_match", _dead_match)
    monkeypatch.setattr(S, "_opensanctions_search", _dead_search)


def _local_store(monkeypatch, verdict, matches=(), reason=""):
    """Stand in for sanctions_canonical.check_sanctions."""
    from aria_service.intel.sanctions_canonical import lookup as _canon

    def _fake(name, jurisdiction="", address="", **kw):
        return {"queried_name": name, "verdict": verdict,
                "matches": list(matches), "reason": reason}

    monkeypatch.setattr(_canon, "check_sanctions", _fake)


class TestTheFallbackNeverManufacturesAClean:
    """The property that matters more than the feature."""

    @pytest.mark.asyncio
    async def test_both_sources_down_stays_UNVERIFIED(self, monkeypatch):
        _no_opensanctions(monkeypatch)
        _local_store(monkeypatch, "INSUFFICIENT_DATA",
                     reason="sanctions_store_empty_or_unavailable")

        res = await S.fuzzy_screen("Some Company Ltd")
        assert res["screened"] is False, (
            "NEITHER source answered and the screen reported itself performed — "
            "this is the false clean the whole module exists to prevent"
        )
        assert res.get("source_unavailable") is True
        assert res.get("error") == "sanctions_source_unavailable"

    @pytest.mark.asyncio
    async def test_an_empty_local_store_is_not_a_clean(self, monkeypatch):
        """An unloaded store returning INSUFFICIENT_DATA must not read as 'no hits'."""
        _no_opensanctions(monkeypatch)
        _local_store(monkeypatch, "INSUFFICIENT_DATA")
        res = await S.fuzzy_screen("Rosoboronexport")
        assert res["screened"] is False
        assert res["blocked"] is False   # not a block either — it is UNKNOWN

    @pytest.mark.asyncio
    async def test_the_reasons_name_BOTH_failures(self, monkeypatch):
        """The obstacle line should say the aggregator is out of quota AND that the
        local store could not cover for it — not just one of the two."""
        _no_opensanctions(monkeypatch, reason="quota_exhausted")
        _local_store(monkeypatch, "INSUFFICIENT_DATA", reason="sanctions_data_stale")
        res = await S.fuzzy_screen("Some Company Ltd")
        reasons = " ".join(res.get("source_reasons") or [])
        assert "quota_exhausted" in reasons, reasons
        assert "stale" in reasons, reasons

    @pytest.mark.asyncio
    async def test_a_local_crash_does_not_fabricate_a_screen(self, monkeypatch):
        from aria_service.intel.sanctions_canonical import lookup as _canon

        def _boom(name, **kw):
            raise RuntimeError("store corrupt")

        _no_opensanctions(monkeypatch)
        monkeypatch.setattr(_canon, "check_sanctions", _boom)
        res = await S.fuzzy_screen("Some Company Ltd")
        assert res["screened"] is False
        assert any("local_crash" in r for r in (res.get("source_reasons") or []))


class TestTheFallbackActuallyScreens:
    """The capability. Without this the change is only a safety net that never fires."""

    @pytest.mark.asyncio
    async def test_a_designated_entity_is_caught_with_opensanctions_down(
            self, monkeypatch):
        _no_opensanctions(monkeypatch, reason="quota_exhausted")
        _local_store(monkeypatch, "HARD_STOP", matches=[{
            "source": "ofac_sdn", "formatted_name": "ROSOBORONEXPORT",
            "match_score": 1.0, "match_method": "exact", "countries": ["Russia"],
            "source_uid": "OFAC-1234",
        }])

        res = await S.fuzzy_screen("Rosoboronexport")
        assert res["screened"] is True, (
            "the local canonical store answered and the screen still called "
            "itself unperformed"
        )
        assert res["blocked"] is True, (
            "an exact OFAC SDN designation did not block when OpenSanctions was "
            "out of quota — screening was silently degraded to nothing"
        )
        assert res["match_count"] >= 1
        assert res["matches"][0]["source_kind"] == "local_canonical"

    @pytest.mark.asyncio
    async def test_a_genuine_local_clear_counts_as_screened(self, monkeypatch):
        """CLEAR from a LOADED store is a real screen — that is the whole point of
        the fallback. check_sanctions only returns CLEAR once its own emptiness,
        coverage and staleness gates have passed."""
        _no_opensanctions(monkeypatch)
        _local_store(monkeypatch, "CLEAR")
        res = await S.fuzzy_screen("Definitely Unlisted Ltd")
        assert res["screened"] is True
        assert res["blocked"] is False
        assert res.get("source_unavailable") is not True

    @pytest.mark.asyncio
    async def test_the_local_path_is_NOT_used_when_opensanctions_answers(
            self, monkeypatch):
        """Surgical scope: normal operation is unchanged. OpenSanctions stays
        primary; this is a floor beneath it, not a replacement."""
        called = []

        async def _ok_match(name, entity_type="Thing"):
            return S._SourceQuery([], True, "ok")

        async def _ok_search(query, limit=5):
            return S._SourceQuery([], True, "ok")

        monkeypatch.setattr(S, "_opensanctions_match", _ok_match)
        monkeypatch.setattr(S, "_opensanctions_search", _ok_search)

        from aria_service.intel.sanctions_canonical import lookup as _canon
        monkeypatch.setattr(_canon, "check_sanctions",
                            lambda *a, **k: called.append(1) or {"verdict": "CLEAR"})

        res = await S.fuzzy_screen("Some Company Ltd")
        assert not called, (
            "the local store was consulted even though OpenSanctions answered — "
            "this change must not alter behaviour on the healthy path"
        )
        assert res["screened"] is True


class TestTheQuotaStateIsOperatorVisible:
    """§19e — only the operator can clear a spent plan, so they must be able to see
    it without reading a log line."""

    @pytest.mark.asyncio
    async def test_a_clean_state_reports_not_exhausted(self, monkeypatch):
        from aria_service.intel import redis_store as _rs

        async def _none(key):
            return None

        monkeypatch.setattr(_rs, "get_json", _none)
        assert (await S.get_opensanctions_quota_state())["exhausted"] is False

    @pytest.mark.asyncio
    async def test_an_exhausted_state_carries_the_operator_action(self, monkeypatch):
        from aria_service.intel import redis_store as _rs

        async def _state(key):
            return {"since": "2026-07-30T12:00:00", "detail": "exceeded for the month",
                    "action": "operator: upgrade the OpenSanctions plan or wait"}

        monkeypatch.setattr(_rs, "get_json", _state)
        out = await S.get_opensanctions_quota_state()
        assert out["exhausted"] is True
        assert "operator" in out["action"], (
            "the state must name WHO can clear it — retrying cannot"
        )

    @pytest.mark.asyncio
    async def test_the_endpoint_actually_reports_it(self, monkeypatch):
        """The CARRIER. Earlier today R-F3521 computed a value that every surface
        dropped, so it was computed and unreadable. Drive the real route."""
        from aria_service.routes import aria as routes
        from aria_service.intel import sanctions as _s

        async def _exhausted():
            return {"exhausted": True, "since": "2026-07-30T12:00:00",
                    "action": "operator: upgrade the plan or wait for the reset"}

        monkeypatch.setattr(_s, "get_opensanctions_quota_state", _exhausted)
        out = await routes.sanctions_source_status_ep()

        assert out["opensanctions"]["quota_exhausted"] is True
        assert "operator" in out["opensanctions"]["operator_action"]
        assert out["note"], "a degraded state must explain itself"

    @pytest.mark.asyncio
    async def test_the_endpoint_does_not_claim_screening_when_both_are_down(
            self, monkeypatch):
        """screening_available must be None (unknown/unsafe), never True, when the
        quota is spent AND the local store is empty."""
        from aria_service.routes import aria as routes
        from aria_service.intel import sanctions as _s
        from aria_service.intel.sanctions_canonical import store as _store

        async def _exhausted():
            return {"exhausted": True, "since": "x", "action": "operator: upgrade"}

        monkeypatch.setattr(_s, "get_opensanctions_quota_state", _exhausted)
        monkeypatch.setattr(_store, "count_entries", lambda source=None: 0)
        out = await routes.sanctions_source_status_ep()
        assert out["screening_available"] is not True, (
            "the status endpoint claimed screening was available with both "
            "sources down"
        )
        assert "never clean" in out["note"]

    @pytest.mark.asyncio
    async def test_an_unreadable_state_is_unknown_not_false(self, monkeypatch):
        """'could not read' is not 'not exhausted' — the same tri-state discipline
        the phase gates use."""
        from aria_service.intel import redis_store as _rs

        async def _boom(key):
            raise RuntimeError("store down")

        monkeypatch.setattr(_rs, "get_json", _boom)
        out = await S.get_opensanctions_quota_state()
        assert out["exhausted"] is None, out
