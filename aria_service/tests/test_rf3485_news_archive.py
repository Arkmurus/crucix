"""R-F3485 — permanent news archive: retention separated from serving.

The hot store is a 1,000-item rolling list (news_monitor.py:67) written with
``lpush`` + ``ltrim`` (:673-674). Article 1,001 therefore DELETES the oldest raw
record — the only copy of that source observation. Downstream, the intel ledger
keeps 500 chars and the brain absorbs 200 (intel_ledger.py:608), so once the raw
record is trimmed ARIA retains a durable statement that something happened but
not enough source material to re-extract claims, detect a correction, or audit
how a conclusion was reached.

That cap is doing two unrelated jobs: bounding a hot operational list (correct,
keep it) and enforcing retention (wrong, remove it). This module is the archive
half of that split.

Design decisions and why:

* DEDICATED SQLite file, following R-F1446 (agent_registry, dd_vault,
  dd_evidence_store). A separate file is a separate write lock, so the archive
  cannot contend with the shared state_store — the wedge in
  memory/incident_state_store_wedge_2026_07_02.md was a single aiosqlite writer.

* ALL access off the event loop behind a lock, per R-F3468 today: the connection
  is opened ``check_same_thread=False``, which permits off-thread use but does
  NOT make it safe for concurrent use.

* PERMANENT (CLAUDE.md §7): no TTL, no prune, no eviction, no oldest-first drop.

* Statistics are COUNT/aggregate queries, never "read every row and decode".
  news_monitor.get_stats() currently decodes all 1,000 records every 30s
  (:2446); repeating that against a growing archive is how an archive becomes an
  outage.

* Identity is THREE-part, because URL-only dedup (sha256(url)[:16], :642) cannot
  tell a syndicated copy from independent corroboration, and never notices a
  correction published at the same URL.

* Full article bodies are NOT stored by default. Permanent metadata, hashes,
  provenance and a bounded excerpt carry the analytical value at far lower
  copyright and personal-data exposure; body retention is opt-in per source.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import news_archive


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(news_archive, "_DB_PATH", tmp_path / "news_archive.db")
    news_archive._reset_for_tests()
    yield
    news_archive._reset_for_tests()


def _article(url="https://janes.com/a1", title="Poland orders 32 K2 tanks",
             summary="Warsaw signed a contract for 32 additional K2 tanks.",
             **kw):
    art = {
        "url": url,
        "title": title,
        "summary": summary,
        "source": kw.pop("source", "Janes"),
        "category": kw.pop("category", "global_defence"),
        "tier": kw.pop("tier", "1A"),
        "published_at": kw.pop("published_at", "2026-07-30T09:00:00+00:00"),
    }
    art.update(kw)
    return art


class TestArchiveIsPermanent:

    @pytest.mark.asyncio
    async def test_article_survives_far_beyond_the_hot_cap(self):
        """The whole point: record 1 must still be readable after 1,500 more."""
        first = await news_archive.archive_article(_article(url="https://x.com/first"))
        assert first["status"] == "new"
        for i in range(1500):
            await news_archive.archive_article(_article(url=f"https://x.com/{i}"))
        got = await news_archive.get_article(first["article_id"])
        assert got is not None, "the oldest article was lost — archive is not permanent"
        assert got["canonical_url"] == "https://x.com/first"

    @pytest.mark.asyncio
    async def test_module_exposes_no_delete_or_prune(self):
        """§7 — no TTL, no prune, no eviction. Guard the API surface itself."""
        banned = [n for n in dir(news_archive)
                  if any(w in n.lower() for w in ("delete", "prune", "evict", "trim", "purge"))
                  and not n.startswith("_reset_for_tests")]
        assert not banned, f"archive exposes destructive operations: {banned}"


class TestThreePartIdentity:

    @pytest.mark.asyncio
    async def test_same_url_same_content_is_a_duplicate(self):
        a = await news_archive.archive_article(_article())
        b = await news_archive.archive_article(_article())
        assert b["status"] == "duplicate"
        assert b["article_id"] == a["article_id"]

    @pytest.mark.asyncio
    async def test_same_url_changed_content_is_a_REVISION_not_a_duplicate(self):
        """URL-only dedup misses corrections and retractions entirely."""
        a = await news_archive.archive_article(_article())
        b = await news_archive.archive_article(
            _article(summary="CORRECTION: Warsaw signed for 18 tanks, not 32."))
        assert b["status"] == "revision", b
        assert b["article_id"] == a["article_id"]
        versions = await news_archive.get_versions(a["article_id"])
        assert len(versions) == 2, "the prior wording was not preserved"
        assert any("CORRECTION" in (v["summary"] or "") for v in versions)

    @pytest.mark.asyncio
    async def test_tracking_params_do_not_create_a_second_article(self):
        a = await news_archive.archive_article(_article(url="https://janes.com/a1"))
        b = await news_archive.archive_article(
            _article(url="https://janes.com/a1?utm_source=rss&utm_medium=feed"))
        assert b["article_id"] == a["article_id"], "tracking params split one article in two"

    @pytest.mark.asyncio
    async def test_syndicated_copy_is_a_distinct_article_but_same_content_hash(self):
        """Different publisher, identical text — must be linkable as syndication,
        NOT silently merged and NOT counted as independent corroboration."""
        a = await news_archive.archive_article(_article(url="https://janes.com/a1"))
        b = await news_archive.archive_article(
            _article(url="https://reuters.com/x9", source="Reuters"))
        assert b["article_id"] != a["article_id"]
        assert b["content_hash"] == a["content_hash"]
        sibs = await news_archive.find_by_content_hash(a["content_hash"])
        assert len(sibs) == 2


class TestProvenanceIsRecorded:

    @pytest.mark.asyncio
    async def test_publisher_family_is_stored(self):
        res = await news_archive.archive_article(_article(url="https://reuters.com/x"))
        got = await news_archive.get_article(res["article_id"])
        assert got["publisher_family"], "no publisher family — independence cannot be judged"

    @pytest.mark.asyncio
    async def test_promotion_verdict_is_persisted_onto_the_record(self):
        """The relevance decision must be reproducible FROM the stored record."""
        res = await news_archive.archive_article(_article())
        await news_archive.record_relevance(
            res["article_id"], score=0.12, on_topic=False,
            terms=["tank", "contract"], classifier_version="rel.v3")
        got = await news_archive.get_article(res["article_id"])
        assert got["relevance_score"] == pytest.approx(0.12)
        assert got["off_topic"] is True
        assert got["classifier_version"] == "rel.v3"
        assert "tank" in (got["relevance_terms"] or "")


class TestPerStageProcessingStatus:
    """So ARIA can answer 'did this article become usable knowledge?' (§25)."""

    @pytest.mark.asyncio
    async def test_stage_outcomes_are_recorded_and_queryable(self):
        res = await news_archive.archive_article(_article())
        aid = res["article_id"]
        await news_archive.mark_stage(aid, "ledger_written", ok=True)
        await news_archive.mark_stage(aid, "brain_absorbed", ok=False,
                                      detail="absorb timed out")
        got = await news_archive.get_article(aid)
        assert got["stages"]["ledger_written"]["ok"] is True
        assert got["stages"]["brain_absorbed"]["ok"] is False
        assert "timed out" in got["stages"]["brain_absorbed"]["detail"]

    @pytest.mark.asyncio
    async def test_incomplete_articles_are_retrievable_for_retry(self):
        done = await news_archive.archive_article(_article(url="https://x.com/done"))
        await news_archive.mark_stage(done["article_id"], "brain_absorbed", ok=True)
        stuck = await news_archive.archive_article(_article(url="https://x.com/stuck"))
        await news_archive.mark_stage(stuck["article_id"], "brain_absorbed", ok=False,
                                      detail="boom")
        pending = await news_archive.pending_stage("brain_absorbed", limit=10)
        ids = {p["article_id"] for p in pending}
        assert stuck["article_id"] in ids
        assert done["article_id"] not in ids


class TestReplayIsArchiveWideAndResumable:

    @pytest.mark.asyncio
    async def test_replay_walks_the_whole_archive_in_pages(self):
        """Classifier replay currently caps at 200 over a 1,000 store
        (news_monitor.py:1424) so history can never be reinterpreted."""
        for i in range(250):
            await news_archive.archive_article(_article(url=f"https://x.com/{i}"))
        seen, cursor = [], 0
        while True:
            page = await news_archive.iter_for_replay(cursor=cursor, limit=100)
            if not page["rows"]:
                break
            seen.extend(r["article_id"] for r in page["rows"])
            cursor = page["next_cursor"]
        assert len(seen) == 250, f"replay covered {len(seen)} of 250"
        assert len(set(seen)) == 250, "replay returned duplicates"


class TestStatsDoNotScanEveryRow:

    @pytest.mark.asyncio
    async def test_stats_are_aggregates(self):
        for i in range(120):
            await news_archive.archive_article(_article(url=f"https://x.com/{i}"))
        stats = await news_archive.archive_stats()
        assert stats["total_articles"] == 120
        assert stats["oldest_at"] and stats["newest_at"]

    @pytest.mark.asyncio
    async def test_stats_stay_fast_as_the_archive_grows(self):
        """An O(N) decode per call is how an archive becomes an outage."""
        for i in range(400):
            await news_archive.archive_article(_article(url=f"https://x.com/{i}"))
        t0 = time.perf_counter()
        await news_archive.archive_stats()
        assert (time.perf_counter() - t0) < 0.5


class TestArchiveNeverBlocksTheEventLoop:
    """R-F3468/R-F3475 — today's lesson, applied at construction time."""

    @pytest.mark.asyncio
    async def test_writes_keep_the_loop_responsive(self):
        samples: list[float] = []
        state = {"stop": False}

        async def _ticker():
            last = time.perf_counter()
            while not state["stop"]:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                samples.append(now - last)
                last = now

        t = asyncio.create_task(_ticker())
        try:
            for i in range(300):
                await news_archive.archive_article(_article(url=f"https://x.com/{i}"))
        finally:
            state["stop"] = True
            await asyncio.sleep(0.02)
            t.cancel()
        assert samples and max(samples) < 0.25, (
            f"archive writes blocked the loop for {max(samples):.2f}s"
        )
