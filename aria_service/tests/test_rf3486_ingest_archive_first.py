"""R-F3486 — an article was marked "seen" before it was durably stored.

The ingest order in news_monitor.poll_feeds was:

    await _mark_seen(article["url"])      # :2110
    await _store_article(article)         # :2111
    if await _feed_to_brain(article):     # :2112

If ANY step after _mark_seen fails — the store write, the ledger ingest, the
promotion, the brain absorb — the URL is already recorded as processed. The next
poll calls ``_is_seen`` and skips it. Permanently:

    seen registry : yes
    raw evidence  : possibly absent
    ledger        : possibly absent
    brain         : possibly absent
    future retry  : SUPPRESSED

The seen map holds 50,000 URL hashes (_MAX_SEEN_URLS, :69), so that article is
not reconsidered for a very long time — and the poll summary still counted it as
"new", so nothing anywhere reported a loss.

This is more important than the 1,000-article retention cap, and it is fully
independent of it: uncapping storage does not fix an article that was never
stored. It is also the one defect here that destroys evidence SILENTLY.

The upstream review located one instance. There are TWO — the deep-scrape path
marks seen at :1929 and stores at :1959. Fixing only the cited one would leave
the class alive on the vault-curated path, which handles the operator's own
highest-value sources.

Correct order (archive is the first durable write, seen is the last mark):

    archive_article()      -> permanent record exists
    mark_stage(...)        -> per-stage outcomes recorded
    _store_article()       -> hot cache (derived, disposable)
    _feed_to_brain()       -> ledger + absorb
    _mark_seen()           -> ONLY now, and only if the archive write succeeded
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from aria_service.intel import news_monitor, news_archive


_SRC = pathlib.Path(__file__).resolve().parents[1] / "intel" / "news_monitor.py"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(news_archive, "_DB_PATH", tmp_path / "news_archive.db")
    news_archive._reset_for_tests()
    yield
    news_archive._reset_for_tests()


def _article(url="https://janes.com/story-1"):
    return {"url": url, "title": "Poland orders K2 tanks",
            "summary": "Warsaw signed a contract.", "source": "Janes",
            "category": "global_defence", "tier": "1A"}


class TestArchiveBeforeSeen:

    @pytest.mark.asyncio
    async def test_article_is_archived_before_being_marked_seen(self, monkeypatch):
        """The capability property: the durable record must exist first."""
        order: list[str] = []

        async def _spy_archive(article):
            order.append("archive")
            return await news_archive.archive_article(article)

        async def _spy_seen(url):
            order.append("mark_seen")

        monkeypatch.setattr(news_monitor, "_archive_article", _spy_archive,
                            raising=False)
        monkeypatch.setattr(news_monitor, "_mark_seen", _spy_seen)
        monkeypatch.setattr(news_monitor, "_store_article",
                            lambda _a: _noop(), raising=False)
        monkeypatch.setattr(news_monitor, "_feed_to_brain",
                            lambda _a: _false(), raising=False)

        await news_monitor._ingest_article(_article())
        assert order, "ingest helper did not run"
        assert order.index("archive") < order.index("mark_seen"), (
            f"marked seen before archiving: {order}"
        )

    @pytest.mark.asyncio
    async def test_a_failed_archive_does_NOT_mark_the_url_seen(self, monkeypatch):
        """The silent-loss condition. If the durable write fails the article must
        remain retryable, not be suppressed for 50,000 URLs' worth of history."""
        marked: list[str] = []

        async def _boom(_article):
            raise RuntimeError("disk full")

        async def _spy_seen(url):
            marked.append(url)

        monkeypatch.setattr(news_monitor, "_archive_article", _boom, raising=False)
        monkeypatch.setattr(news_monitor, "_mark_seen", _spy_seen)
        monkeypatch.setattr(news_monitor, "_store_article",
                            lambda _a: _noop(), raising=False)
        monkeypatch.setattr(news_monitor, "_feed_to_brain",
                            lambda _a: _false(), raising=False)

        res = await news_monitor._ingest_article(_article())
        assert marked == [], "URL was marked seen despite the archive failing"
        assert res.get("archived") is False
        assert res.get("retryable") is True

    @pytest.mark.asyncio
    async def test_downstream_failure_still_records_the_stage(self, monkeypatch):
        """A brain-absorb failure must be VISIBLE, not swallowed — and the
        article stays archived so it can be retried from the archive."""
        async def _seen(_u):
            return None

        async def _brain_fails(_a):
            raise RuntimeError("absorb timeout")

        monkeypatch.setattr(news_monitor, "_mark_seen", _seen)
        monkeypatch.setattr(news_monitor, "_store_article",
                            lambda _a: _noop(), raising=False)
        monkeypatch.setattr(news_monitor, "_feed_to_brain", _brain_fails,
                            raising=False)

        res = await news_monitor._ingest_article(_article())
        assert res["archived"] is True
        aid = res["article_id"]
        rec = await news_archive.get_article(aid)
        assert rec["stages"]["brain_absorbed"]["ok"] is False, rec["stages"]
        pending = await news_archive.pending_stage("brain_absorbed", limit=10)
        assert aid in {p["article_id"] for p in pending}


class TestBothIngestPathsAreFixed:
    """The review found one site. There are two."""

    def test_no_mark_seen_precedes_a_durable_write(self):
        """AST guard: in any function that both marks seen and archives/stores,
        the archive call must come first."""
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        offenders: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            seen_line = archive_line = None
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                name = (getattr(sub.func, "id", None)
                        or getattr(sub.func, "attr", None) or "")
                if name == "_mark_seen" and seen_line is None:
                    seen_line = sub.lineno
                if name in ("_archive_article", "_ingest_article") and archive_line is None:
                    archive_line = sub.lineno
            if seen_line and archive_line and seen_line < archive_line:
                offenders.append(f"{node.name}(): _mark_seen at :{seen_line} "
                                 f"precedes the durable write at :{archive_line}")
            if seen_line and not archive_line and node.name in (
                    "poll_feeds", "_poll_vault_curated_source"):
                offenders.append(f"{node.name}(): marks seen with no archive call")

        assert not offenders, (
            "an article can be suppressed before it is durably stored:\n  "
            + "\n  ".join(offenders)
        )


async def _noop():
    return None


async def _false():
    return False
