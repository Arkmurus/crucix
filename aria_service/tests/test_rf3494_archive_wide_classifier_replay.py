"""R-F3494 — classifier replay could only ever repair the newest 200 articles.

``_replay_recent_articles_for_classifier(limit=200)`` read the HOT list
(``_ARTICLES_KEY``, capped at 1,000 and trimmed destructively), so a classifier
upgrade could reach at most 200 records — and only the most recent ones. Three
consequences, all of which defeat compounding:

  * historical false negatives could never be recovered
  * historical false positives stayed embedded in derived memory
  * a better extractor could not be applied to the history that already existed

A genuinely compounding system becomes more capable over the SAME retained
evidence. That one waited for new URLs.

Two further defects the limit hid:

  NOT RESUMABLE. The completion marker was written only after a full pass, so a
  crash or a restart part-way through discarded all progress and started again
  from the newest record. Over an archive that only grows, a non-resumable
  full-scan eventually never completes at all.

  NO RECORD OF WHAT CHANGED. It returned scanned/promoted only. A classifier
  upgrade must be able to answer what it CHANGED — how many decisions flipped in
  each direction — or there is no way to tell an improvement from a regression,
  and no way to audit a reclassification after the fact.

R-F3485 built the archive and ``iter_for_replay`` (resumable, archive-wide,
paged). This rewires replay onto it.
"""
from __future__ import annotations

import pytest

from aria_service.intel import news_monitor as nm, news_archive


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(news_archive, "_DB_PATH", tmp_path / "news_archive.db")
    news_archive._reset_for_tests()
    yield
    news_archive._reset_for_tests()


async def _seed(n: int, prefix: str = "a", on_topic_text: bool = True):
    """Archive n articles. Defence wording so _topical_relevance can score them."""
    title = ("Poland signs defence procurement contract for missile systems"
             if on_topic_text else "Local bakery wins cake competition")
    for i in range(n):
        await news_archive.archive_article({
            "url": f"https://janes.com/{prefix}{i}",
            "title": f"{title} {i}",
            "summary": "Defence ministry tender award for air defence systems."
                       if on_topic_text else "A pleasant story about cake.",
            "source": "Janes", "category": "global_defence", "tier": "1A",
        })


class TestReplayCoversTheWholeArchive:

    @pytest.mark.asyncio
    async def test_replay_reaches_beyond_the_old_200_limit(self, monkeypatch):
        """The load-bearing property: history is reachable."""
        await _seed(450)
        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        monkeypatch.setattr(nm.rs, "get_json", _none)
        saved = {}
        monkeypatch.setattr(nm.rs, "set_json", _capture(saved))

        result = await nm._replay_articles_for_classifier(batch=100, max_batches=50)
        assert result["scanned"] == 450, (
            f"replay reached {result['scanned']} of 450 archived articles"
        )
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_replay_reads_the_archive_not_the_hot_list(self, monkeypatch):
        """The hot list is destructively trimmed; it is not the evidence base."""
        await _seed(30)
        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        monkeypatch.setattr(nm.rs, "get_json", _none)
        monkeypatch.setattr(nm.rs, "set_json", _capture({}))

        async def _explode(*_a, **_kw):
            raise AssertionError("replay read the hot list instead of the archive")

        monkeypatch.setattr(nm.rs, "lrange", _explode)
        result = await nm._replay_articles_for_classifier(batch=50, max_batches=5)
        assert result["scanned"] == 30


class TestReplayIsResumable:

    @pytest.mark.asyncio
    async def test_a_bounded_run_persists_its_cursor(self, monkeypatch):
        await _seed(120)
        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        monkeypatch.setattr(nm.rs, "get_json", _none)
        saved = {}
        monkeypatch.setattr(nm.rs, "set_json", _capture(saved))

        res = await nm._replay_articles_for_classifier(batch=25, max_batches=2)
        assert res["status"] == "in_progress", res
        assert res["scanned"] == 50
        state = saved.get("value") or {}
        assert state.get("cursor", 0) > 0, "no cursor persisted — a restart loses all progress"

    @pytest.mark.asyncio
    async def test_a_second_run_resumes_instead_of_restarting(self, monkeypatch):
        await _seed(120)
        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        saved = {}
        monkeypatch.setattr(nm.rs, "set_json", _capture(saved))
        monkeypatch.setattr(nm.rs, "get_json", _none)

        first = await nm._replay_articles_for_classifier(batch=25, max_batches=2)
        # Feed the persisted state back in, as a real restart would.
        state = saved["value"]
        monkeypatch.setattr(nm.rs, "get_json", _returns(state))
        second = await nm._replay_articles_for_classifier(batch=25, max_batches=10)

        assert first["scanned"] + second["scanned"] == 120, (
            f"resume double-counted or skipped: {first['scanned']} + {second['scanned']}"
        )
        assert second["status"] == "completed"

    @pytest.mark.asyncio
    async def test_a_completed_version_does_not_rerun(self, monkeypatch):
        await _seed(10)
        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        monkeypatch.setattr(nm.rs, "set_json", _capture({}))
        monkeypatch.setattr(nm.rs, "get_json", _returns({
            "version": nm._CLASSIFIER_REPLAY_VERSION,
            "status": "completed", "cursor": 999999999.0,
        }))
        res = await nm._replay_articles_for_classifier()
        assert res["status"] == "current"
        assert res["scanned"] == 0


class TestReplayRecordsWhatItChanged:

    @pytest.mark.asyncio
    async def test_decision_flips_are_counted_in_both_directions(self, monkeypatch):
        """Without this a classifier upgrade cannot be told from a regression."""
        await _seed(4, prefix="on", on_topic_text=True)
        await _seed(4, prefix="off", on_topic_text=False)
        # Prior verdicts: mark everything as previously OFF-topic.
        page = await news_archive.iter_for_replay(cursor=0.0, limit=100)
        for row in page["rows"]:
            await news_archive.record_relevance(
                row["article_id"], score=0.0, on_topic=False,
                classifier_version="rel.v0")

        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        monkeypatch.setattr(nm.rs, "get_json", _none)
        monkeypatch.setattr(nm.rs, "set_json", _capture({}))

        res = await nm._replay_articles_for_classifier(batch=50, max_batches=5)
        assert res["scanned"] == 8
        assert "decisions_changed" in res, res
        assert res["decisions_changed"] >= 1, (
            "no decision flips recorded despite every prior verdict being off_topic"
        )

    @pytest.mark.asyncio
    async def test_replay_persists_the_new_verdict_onto_the_archive(self, monkeypatch):
        """Otherwise the next replay cannot tell what the previous one decided."""
        await _seed(3)
        monkeypatch.setattr(nm, "_promote_article_signal", _always_true)
        monkeypatch.setattr(nm.rs, "get_json", _none)
        monkeypatch.setattr(nm.rs, "set_json", _capture({}))

        await nm._replay_articles_for_classifier(batch=10, max_batches=2)
        page = await news_archive.iter_for_replay(cursor=0.0, limit=10)
        stamped = [r for r in page["rows"] if r.get("classifier_version")]
        assert len(stamped) == 3, (
            "replay did not stamp its verdict on the archived records"
        )


class TestTheOldNarrowReplayIsGone:

    def test_no_caller_uses_the_hot_list_replay(self):
        import ast, pathlib
        src = pathlib.Path(nm.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
        assert "_replay_recent_articles_for_classifier" not in names, (
            "the 200-article hot-list replay still exists; a caller can still "
            "reach it and believe history was reclassified"
        )


# ── helpers ────────────────────────────────────────────────────────────────

async def _always_true(_article):
    return True


async def _none(_key):
    return None


def _returns(value):
    async def _get(_key):
        return value
    return _get


def _capture(sink):
    async def _set(_key, value, **_kw):
        sink["value"] = value
        return True
    return _set
