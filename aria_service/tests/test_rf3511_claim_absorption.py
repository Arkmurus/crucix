"""R-F3511 — ARIA absorbed truncated article summaries, not claims.

What reaches the brain from news today is this (intel_ledger.py:608):

    summary=f"Ledger signal: {text[:200]}"

Two hundred characters of prose. Everything that makes a statement checkable is
gone: who asserted it, where it can be re-read, how many INDEPENDENT sources
carry it, what earlier belief it revises, and the exact words that support it.

That shape cannot compound. A later, better source cannot correct an earlier
belief, because there is no earlier belief — only a truncated sentence. So new
evidence ADDS text beside the old text and both sit there, indistinguishable.
Contradiction becomes invisible and retraction impossible.

A claim, by contrast, is a thing that can be revised:

    subject / predicate / object     what is asserted
    source_url + publisher_family    who asserted it, and whether the next one
                                     carrying it is genuinely independent
    excerpt                          the EXACT words that support it
    observed_at / event_date         when it was said vs when it happened
    confidence                       graded by evidence, capped by extraction
    corroboration                    counted by independent ORIGIN, not by copies
    supersedes                       what this revises, with the old kept

THE HONESTY FLOOR, and the reason extraction here is deterministic rather than
generated: a claim MUST carry a verbatim excerpt from the source text. If ARIA
cannot quote the words, ARIA does not make the claim. An LLM asked to "extract
claims" will happily produce fluent, plausible, unsupported ones — which is the
fabrication this product exists to prevent. The moat is verification, not
generation (memory/north_star_zero_fabrication).
"""
from __future__ import annotations

import pytest

from aria_service.intel import news_claims as nc


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "_DB_PATH", tmp_path / "news_claims.db")
    nc._reset_for_tests()
    yield
    nc._reset_for_tests()


_TEXT = ("Poland has signed a contract with Hyundai Rotem for 180 K2 tanks, the "
         "defence ministry said on Tuesday. Deliveries begin in 2027.")


def _claim(**kw):
    c = {
        "subject": "Poland",
        "predicate": "signed_contract",
        "object": "Hyundai Rotem — 180 K2 tanks",
        "excerpt": "Poland has signed a contract with Hyundai Rotem for 180 K2 tanks",
        "source_url": "https://janes.com/poland-k2",
        "source_tier": "tier_1a",
        "confidence": "HIGH",
        "extraction_status": "enriched",
    }
    c.update(kw)
    return c


class TestAClaimMustBeQuotable:
    """The honesty floor: if ARIA cannot quote it, ARIA does not claim it."""

    @pytest.mark.asyncio
    async def test_a_claim_without_an_excerpt_is_refused(self):
        res = await nc.record_claim(_claim(excerpt=""), source_text=_TEXT)
        assert res["recorded"] is False
        assert "excerpt" in res["reason"].lower()

    @pytest.mark.asyncio
    async def test_an_excerpt_absent_from_the_source_is_refused(self):
        """The defence against a fluent invention that reads plausibly."""
        res = await nc.record_claim(
            _claim(excerpt="Poland cancelled the K2 programme"), source_text=_TEXT)
        assert res["recorded"] is False
        assert "not found" in res["reason"].lower() or "verbatim" in res["reason"].lower()

    @pytest.mark.asyncio
    async def test_a_verbatim_excerpt_is_accepted(self):
        res = await nc.record_claim(_claim(), source_text=_TEXT)
        assert res["recorded"] is True, res
        got = await nc.get_claim(res["claim_id"])
        assert got["excerpt"] in _TEXT

    @pytest.mark.asyncio
    async def test_whitespace_differences_do_not_defeat_verification(self):
        res = await nc.record_claim(
            _claim(excerpt="Poland  has signed a contract with Hyundai Rotem"),
            source_text=_TEXT)
        assert res["recorded"] is True, res


class TestCorroborationCountsOriginsNotCopies:
    """Same discipline as R-F3487: N copies of one story is ONE witness."""

    @pytest.mark.asyncio
    async def test_syndicated_repeats_do_not_raise_corroboration(self):
        base = _claim()
        await nc.record_claim(base, source_text=_TEXT)
        for host in ("news.yahoo.com", "msn.com", "aol.com"):
            await nc.record_claim(
                _claim(source_url=f"https://{host}/x", excerpt=base["excerpt"]),
                source_text=_TEXT, story_id="one-wire-report")
        c = await nc.get_claim_by_fingerprint(nc.claim_fingerprint(base))
        assert c["independent_origins"] <= 2, (
            f"syndicated copies inflated corroboration to "
            f"{c['independent_origins']}"
        )

    @pytest.mark.asyncio
    async def test_genuinely_independent_publishers_raise_corroboration(self):
        base = _claim()
        await nc.record_claim(base, source_text=_TEXT)
        await nc.record_claim(
            _claim(source_url="https://defensenews.com/y", excerpt=base["excerpt"]),
            source_text=_TEXT, story_id="separate-report")
        c = await nc.get_claim_by_fingerprint(nc.claim_fingerprint(base))
        assert c["independent_origins"] >= 2

    @pytest.mark.asyncio
    async def test_the_same_url_twice_is_not_two_sources(self):
        base = _claim()
        await nc.record_claim(base, source_text=_TEXT)
        await nc.record_claim(base, source_text=_TEXT)
        c = await nc.get_claim_by_fingerprint(nc.claim_fingerprint(base))
        assert c["independent_origins"] == 1


class TestRevisionKeepsHistory:
    """A correction must update understanding WITHOUT deleting what was believed."""

    @pytest.mark.asyncio
    async def test_a_superseding_claim_keeps_the_original(self):
        first = await nc.record_claim(_claim(), source_text=_TEXT)
        corrected_text = "Poland has signed a contract with Hyundai Rotem for 116 K2 tanks"
        second = await nc.record_claim(
            _claim(object="Hyundai Rotem — 116 K2 tanks",
                   excerpt="signed a contract with Hyundai Rotem for 116 K2 tanks"),
            source_text=corrected_text, supersedes=first["claim_id"])
        assert second["recorded"] is True
        old = await nc.get_claim(first["claim_id"])
        assert old is not None, "the superseded claim was deleted (§7)"
        assert old["superseded_by"] == second["claim_id"]
        assert old["is_current"] is False
        new = await nc.get_claim(second["claim_id"])
        assert new["is_current"] is True

    @pytest.mark.asyncio
    async def test_current_view_shows_only_the_live_claim(self):
        first = await nc.record_claim(_claim(), source_text=_TEXT)
        await nc.record_claim(
            _claim(object="revised", excerpt="signed a contract with Hyundai Rotem"),
            source_text=_TEXT, supersedes=first["claim_id"])
        current = await nc.current_claims(subject="Poland")
        assert len(current) == 1
        assert current[0]["object"] == "revised"

    @pytest.mark.asyncio
    async def test_history_is_retrievable(self):
        first = await nc.record_claim(_claim(), source_text=_TEXT)
        await nc.record_claim(
            _claim(object="revised", excerpt="signed a contract with Hyundai Rotem"),
            source_text=_TEXT, supersedes=first["claim_id"])
        hist = await nc.claim_history(first["claim_id"])
        assert len(hist) >= 2, "the revision chain was not preserved"


class TestConfidenceRespectsTheEvidenceGrade:

    @pytest.mark.asyncio
    async def test_a_feed_only_claim_cannot_be_high(self):
        res = await nc.record_claim(
            _claim(extraction_status="feed_only"), source_text=_TEXT)
        got = await nc.get_claim(res["claim_id"])
        assert got["confidence"] != "HIGH", (
            "a claim from an unread headline was stored as HIGH confidence"
        )


class TestTheStoreIsPermanent:

    @pytest.mark.asyncio
    async def test_no_destructive_api(self):
        banned = [n for n in dir(nc)
                  if any(w in n.lower() for w in ("delete", "prune", "evict", "purge"))
                  and not n.startswith("_reset_for_tests")]
        assert not banned, f"claims store exposes destructive operations: {banned}"
