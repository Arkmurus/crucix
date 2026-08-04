"""R-F3677 / R-F3678 — the archive's retry queue could neither shrink nor drain.

R-F3486 built half of a contract. Its docstring promises a failed stage is
"visible and retryable instead of swallowed"; it made failures VISIBLE and never
built the retry. Two separate defects kept it that way:

R-F3677 — a SUCCESSFUL deep read did not settle the prior failure row. Proven on
production while fixing R-F3676: two articles were re-read successfully
(``extraction_status='enriched'``, 5,766 and 3,478 chars) and the pending count
stayed at 343. The queue could not shrink even when the underlying problem was
fixed, and the §25 surface permanently over-reported failure.

R-F3678 — nothing drained it. ``news_archive.pending_stage`` is documented as
"the retry queue" and had ZERO production callers, only tests. Enrichment is
attempted only at INGEST, so an article that failed — or that arrived while the
deep reader was broken, which R-F3676 showed was its ENTIRE history — never got a
second chance. 343 tier_1a/1b articles carried ``enrichment_failed`` from a
fetcher that could not possibly have succeeded, and fixing the fetcher did
nothing for a single one of them.

All confirmed FAILING against the pre-fix code (§3c).
"""

import sqlite3
import time

import pytest

from aria_service.intel import news_archive as na
from aria_service.intel import news_enrichment as ne


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path, monkeypatch):
    """`_reset_for_tests` only drops the CONNECTION, not the data — without this
    every test here reads the developer's real data/news_archive.db and sees
    whatever earlier runs left in it."""
    monkeypatch.setattr(na, "_DB_PATH", tmp_path / "news_archive.db")
    na._reset_for_tests()
    yield
    na._reset_for_tests()


def _age_queue(seconds: float) -> None:
    """Backdate every stage row. Production's queue is hours-to-days old; a row
    created microseconds ago is correctly INSIDE the retry cooldown, so a test
    that does not age it is testing the cooldown, not the drain."""
    conn = sqlite3.connect(str(na._DB_PATH))
    conn.execute("UPDATE news_article_stages SET updated_at = updated_at - ?", (seconds,))
    conn.commit()
    conn.close()


async def _archived(url: str, tier: str = "tier_1a") -> str:
    res = await na.archive_article({
        "url": url, "title": "t", "summary": "s",
        "category": "defence_global", "tier": tier,
    })
    return res["article_id"]


# ── R-F3677: a success must settle the failure ──────────────────────────────


@pytest.mark.asyncio
async def test_rf3677_success_removes_the_article_from_the_retry_queue():
    """THE DEFECT: re-reading an article successfully left its ok=0 row behind."""
    aid = await _archived("https://a.example/1")

    await na.set_extraction_status(aid, ne.STATUS_FAILED,
                                   detail="body too short to be a real read (0 chars)")
    q = await na.pending_stage(f"extraction:{ne.STATUS_FAILED}", limit=10)
    assert [r["article_id"] for r in q] == [aid], "precondition: it is queued"

    # The same call a successful read makes: excerpt, no detail.
    await na.set_extraction_status(aid, ne.STATUS_ENRICHED, excerpt="a real body " * 40)

    q = await na.pending_stage(f"extraction:{ne.STATUS_FAILED}", limit=10)
    assert q == [], "a successfully read article must leave the retry queue"


@pytest.mark.asyncio
async def test_rf3677_the_audit_trail_survives_settling():
    """Settled, not deleted — that the article was once hard to read is history
    worth keeping (§7)."""
    aid = await _archived("https://a.example/2")
    await na.set_extraction_status(aid, ne.STATUS_FAILED, detail="consent wall")
    await na.set_extraction_status(aid, ne.STATUS_ENRICHED, excerpt="body " * 100)

    rows = await na.get_versions(aid)  # unrelated table, just proving no wipe
    assert isinstance(rows, list)

    import sqlite3
    conn = sqlite3.connect(str(na._DB_PATH))
    got = dict(conn.execute(
        "SELECT stage, detail FROM news_article_stages "
        "WHERE article_id=? AND stage LIKE 'extraction:%' AND ok=1", (aid,)
    ).fetchall())
    conn.close()
    assert any("superseded" in (d or "") for d in got.values()), (
        f"the prior failure must be settled with a reason, got {got}"
    )


@pytest.mark.asyncio
async def test_rf3677_a_repeat_failure_stays_queued():
    """REGRESSION GUARD: only a success settles. A second failure must remain."""
    aid = await _archived("https://a.example/3")
    await na.set_extraction_status(aid, ne.STATUS_FAILED, detail="0 chars")
    await na.set_extraction_status(aid, ne.STATUS_FAILED, detail="0 chars again")
    q = await na.pending_stage(f"extraction:{ne.STATUS_FAILED}", limit=10)
    assert [r["article_id"] for r in q] == [aid]


# ── R-F3678: the drain ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rf3678_capability_a_previously_unreadable_article_is_recovered(monkeypatch):
    """THE OUTCOME: the R-F3676 backlog actually gets re-read.

    Pre-fix nothing ever called pending_stage, so a fixed fetcher recovered zero
    of the 343 articles it could now read.
    """
    aids = [await _archived(f"https://a.example/r{i}") for i in range(3)]
    for aid in aids:
        await na.set_extraction_status(aid, ne.STATUS_FAILED,
                                       detail="body too short to be a real read (0 chars)")

    async def _now_readable(url, timeout=0):
        return "The article body, now readable. " * 40

    from aria_service.intel import researcher
    monkeypatch.setattr(researcher, "_fetch_article_text", _now_readable)
    _age_queue(ne._RETRY_COOLDOWN_S + 60)

    out = await ne.drain_enrichment_retries(limit=10)

    assert out["attempted"] == 3
    assert out["recovered"] == 3, f"expected all 3 recovered, got {out}"
    assert out["still_failing"] == 0
    assert await na.pending_stage(f"extraction:{ne.STATUS_FAILED}", limit=10) == []
    for aid in aids:
        rec = await na.get_article(aid)
        assert rec["extraction_status"] == ne.STATUS_ENRICHED


@pytest.mark.asyncio
async def test_rf3678_is_bounded_per_cycle(monkeypatch):
    """A 343-item backlog must not be fetched in one poll (§17)."""
    for i in range(12):
        aid = await _archived(f"https://a.example/b{i}")
        await na.set_extraction_status(aid, ne.STATUS_FAILED, detail="0 chars")

    calls: list[str] = []

    async def _count(url, timeout=0):
        calls.append(url)
        return "body " * 100

    from aria_service.intel import researcher
    monkeypatch.setattr(researcher, "_fetch_article_text", _count)
    _age_queue(ne._RETRY_COOLDOWN_S + 60)

    out = await ne.drain_enrichment_retries(limit=4)
    assert out["attempted"] == 4 and len(calls) == 4


@pytest.mark.asyncio
async def test_rf3678_cooldown_stops_a_short_queue_being_hammered(monkeypatch):
    """Once only unreadable URLs remain, rotation alone would retry them every
    poll. The cooldown is what bounds that."""
    aid = await _archived("https://dead.example/1")
    await na.set_extraction_status(aid, ne.STATUS_FAILED, detail="0 chars")

    async def _still_dead(url, timeout=0):
        return ""

    from aria_service.intel import researcher
    monkeypatch.setattr(researcher, "_fetch_article_text", _still_dead)
    _age_queue(ne._RETRY_COOLDOWN_S + 60)

    first = await ne.drain_enrichment_retries(limit=4)
    assert first["attempted"] == 1 and first["still_failing"] == 1

    # The retry stamped updated_at, so it is now inside the cooldown window.
    second = await ne.drain_enrichment_retries(limit=4)
    assert second["attempted"] == 0, (
        "a just-attempted article must not be retried again immediately"
    )


@pytest.mark.asyncio
async def test_rf3678_never_raises_on_an_unreadable_queue(monkeypatch):
    """It runs on the poll tail; it must not cost the poll its state write."""
    async def _boom(stage, limit=100, not_before=0.0):
        raise RuntimeError("archive locked")

    monkeypatch.setattr(na, "pending_stage", _boom)
    out = await ne.drain_enrichment_retries(limit=4)
    assert out["attempted"] == 0
    assert "archive locked" in out["error"]


@pytest.mark.asyncio
async def test_rf3678_reports_its_outcome_for_the_poll_summary():
    """§25 — a drain nobody can see is another silent loop."""
    out = await ne.drain_enrichment_retries(limit=4)
    for k in ("attempted", "recovered", "still_failing", "queue_seen"):
        assert k in out, f"{k} missing from the drain report"


@pytest.mark.asyncio
async def test_rf3678_is_actually_called_by_the_poll():
    """An engine with no caller is the exact defect this closes — so pin the wiring."""
    from . import _source_probe

    src = _source_probe.function_source("aria_service/intel/news_monitor.py", "poll_feeds")
    assert "drain_enrichment_retries" in src, (
        "poll_feeds must call the drain, or the retry queue has no caller again"
    )


@pytest.mark.asyncio
async def test_rf3678_pending_stage_default_is_unfiltered():
    """REGRESSION GUARD: existing callers pass no cut-off and must still see the
    whole queue — a `not_before` of 0 must not mean 'nothing'."""
    aid = await _archived("https://a.example/z")
    await na.set_extraction_status(aid, ne.STATUS_FAILED, detail="0 chars")
    q = await na.pending_stage(f"extraction:{ne.STATUS_FAILED}", limit=10)
    assert [r["article_id"] for r in q] == [aid]
    q2 = await na.pending_stage(f"extraction:{ne.STATUS_FAILED}", limit=10,
                                not_before=time.time() + 60)
    assert [r["article_id"] for r in q2] == [aid]
