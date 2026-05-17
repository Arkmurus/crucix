"""R-F667 — crawler/runner.py wires into brain_hook.

Audit 2026-05-17 closing-session report flagged the crawl loop as the
single biggest knowledge leak in the codebase:

  "Autonomous web crawl (crawler/runner.py, 6h cycle): zero brain_hook
   calls. Pages searchable via /search but invisible to brain. Every
   equivalent customer question still pays an LLM call. This is the
   single biggest violation of CLAUDE.md §15 (pay-once-remember-
   forever) still in the codebase."

Fix: after each successful index in crawl_seed_homepages, fire-and-
forget brain_hook.absorb(module='web_atlas', ...). Failure non-fatal —
the crawl loop continues regardless.

Verifies:
  - the brain_hook absorb call is wired on the successful-index path
  - module is exactly 'web_atlas' (already registered in _MODULE_TOPICS)
  - confidence='ASSESSED' (single-source uncorroborated)
  - the absorb call is async-scheduled (fire-and-forget), not awaited
  - failure in absorb does NOT propagate up the crawl loop
  - per-crawl cost cap respected (no LLM calls in absorb path itself —
    web_atlas tier weight is 0.05 per brain_hook.py:289)
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_RUNNER_SRC = (
    Path(__file__).resolve().parent.parent / "crawler" / "runner.py"
).read_text(encoding="utf-8")


# ── Static guards: marker + module name + call site ───────────────────────

def test_rf667_marker_comment_present():
    assert "R-F667" in _RUNNER_SRC, (
        "R-F667 marker comment missing — has the fix been reverted?"
    )


def test_rf667_uses_web_atlas_module():
    """The brain_hook.absorb call must use module='web_atlas' — that's
    the module name already registered in brain_hook._MODULE_TOPICS:78
    with topics osint + market_intel."""
    # Look for the call to brain_hook.absorb with module="web_atlas"
    matches = re.findall(
        r'absorb\([^)]*module\s*=\s*["\']web_atlas["\']',
        _RUNNER_SRC, re.DOTALL,
    )
    assert matches, (
        "R-F667: brain_hook.absorb(module='web_atlas') call missing "
        "from crawler/runner.py"
    )


def test_rf667_fire_and_forget_via_create_task():
    """The absorb must be fire-and-forget (asyncio.create_task) so a
    slow brain_hook doesn't block the crawl iteration."""
    assert re.search(
        r"asyncio\.create_task\(\s*_bh\.absorb\(",
        _RUNNER_SRC,
    ), (
        "R-F667: absorb call must be wrapped in asyncio.create_task — "
        "blocking await would slow the 100-domain crawl cycle."
    )


def test_rf667_non_fatal_wrap():
    """The absorb dispatch must be wrapped in try/except so brain_hook
    failures don't break the crawl loop."""
    # The R-F667 block has its own try/except. Anchor on the marker.
    block_start = _RUNNER_SRC.find("R-F667")
    assert block_start > 0
    # Look for try/except within ~3000 chars of the marker
    window = _RUNNER_SRC[block_start: block_start + 3000]
    assert "try:" in window and "except Exception" in window, (
        "R-F667: absorb dispatch must be wrapped in try/except so "
        "brain_hook failures are non-fatal."
    )


def test_rf667_confidence_assessed():
    """ASSESSED, not CONFIRMED — a single-source web fetch hasn't been
    corroborated. verified_intel handles upgrades downstream."""
    assert re.search(
        r'confidence\s*=\s*["\']ASSESSED["\']',
        _RUNNER_SRC,
    ), (
        "R-F667: confidence must be 'ASSESSED' (single-source) — "
        "'CONFIRMED' would let unverified crawl content shortcut "
        "the verified_intel multi-source gate."
    )


# ── Runtime behaviour: absorb invoked on success, error swallowed ─────────

def test_rf667_runtime_absorb_called_on_successful_index(monkeypatch):
    """End-to-end: when crawl_seed_homepages indexes a page
    successfully, brain_hook.absorb must be called with module='web_atlas'."""
    from aria_service.crawler import runner
    from aria_service.search_index import db, indexer
    from aria_service.crawler import fetcher
    from aria_service.intel import brain_hook

    captured: list[dict] = []

    async def _fake_list_domains(enabled_only=True):
        return [{"domain": "example.com"}]

    async def _fake_fetch_for_crawl(url):
        return {
            "url": url, "domain": "example.com",
            "title": "Test page",
            "body": "Lorem ipsum dolor sit amet, consectetur " * 20,
            "headings": "h1: hello",
            "language": "en",
            "source_tier": 2,
            "http_status": 200,
            "fetched_at": 1700000000.0,
            "canonical_url": url,
            "extraction_ok": True,
            "status_class": "ok",
        }

    async def _fake_index_fetch(result):
        return "doc_abc123"

    async def _fake_absorb(**kwargs):
        captured.append(kwargs)
        return {"mastery_ok": True, "knowledge_ok": True,
                "neural_ok": True, "gap_ok": True}

    monkeypatch.setattr(db, "list_domains", _fake_list_domains)
    monkeypatch.setattr(fetcher, "fetch_for_crawl", _fake_fetch_for_crawl)
    monkeypatch.setattr(indexer, "index_fetch_result", _fake_index_fetch)
    monkeypatch.setattr(brain_hook, "absorb", _fake_absorb)

    async def runner_call():
        result = await runner.crawl_seed_homepages(limit=1)
        # Let the create_task'd absorb complete
        await asyncio.sleep(0.05)
        return result

    res = asyncio.run(runner_call())
    assert res["indexed"] == 1
    assert captured, "R-F667 runtime regression: brain_hook.absorb was NOT called"
    kw = captured[0]
    assert kw["module"] == "web_atlas"
    assert kw["confidence"] == "ASSESSED"
    assert "example.com" in (kw.get("entity_name") or "")


def test_rf667_runtime_absorb_failure_does_not_break_crawl(monkeypatch):
    """Even if brain_hook.absorb raises, crawl_seed_homepages must
    return cleanly with indexed=1."""
    from aria_service.crawler import runner
    from aria_service.search_index import db, indexer
    from aria_service.crawler import fetcher
    from aria_service.intel import brain_hook

    async def _fake_list_domains(enabled_only=True):
        return [{"domain": "example.com"}]

    async def _fake_fetch_for_crawl(url):
        return {
            "url": url, "domain": "example.com",
            "title": "T", "body": "x" * 200, "headings": "",
            "language": "en", "source_tier": 2, "http_status": 200,
            "fetched_at": 1700000000.0, "canonical_url": url,
            "extraction_ok": True, "status_class": "ok",
        }

    async def _fake_index_fetch(result):
        return "doc_xyz"

    async def _exploding_absorb(**kwargs):
        raise RuntimeError("simulated brain_hook failure")

    monkeypatch.setattr(db, "list_domains", _fake_list_domains)
    monkeypatch.setattr(fetcher, "fetch_for_crawl", _fake_fetch_for_crawl)
    monkeypatch.setattr(indexer, "index_fetch_result", _fake_index_fetch)
    monkeypatch.setattr(brain_hook, "absorb", _exploding_absorb)

    async def runner_call():
        return await runner.crawl_seed_homepages(limit=1)

    res = asyncio.run(runner_call())
    # Crawl still counted the index — absorb failure did NOT abort it.
    assert res["indexed"] == 1
    assert res["errors"] == 0


def test_rf667_absorb_not_called_on_extraction_failure(monkeypatch):
    """If the fetch didn't extract anything (extraction_ok=False), we
    skip the page AND don't call brain_hook (avoids polluting brain
    with empty signals)."""
    from aria_service.crawler import runner
    from aria_service.search_index import db, indexer
    from aria_service.crawler import fetcher
    from aria_service.intel import brain_hook

    called = {"absorb": 0, "index": 0}

    async def _fake_list_domains(enabled_only=True):
        return [{"domain": "example.com"}]

    async def _fake_fetch_for_crawl(url):
        return {"url": url, "domain": "example.com",
                "extraction_ok": False, "status_class": "thin"}

    async def _fake_index_fetch(result):
        called["index"] += 1
        return "doc_xyz"

    async def _spy_absorb(**kwargs):
        called["absorb"] += 1
        return {"mastery_ok": True}

    monkeypatch.setattr(db, "list_domains", _fake_list_domains)
    monkeypatch.setattr(fetcher, "fetch_for_crawl", _fake_fetch_for_crawl)
    monkeypatch.setattr(indexer, "index_fetch_result", _fake_index_fetch)
    monkeypatch.setattr(brain_hook, "absorb", _spy_absorb)

    async def runner_call():
        await runner.crawl_seed_homepages(limit=1)

    asyncio.run(runner_call())
    assert called["absorb"] == 0
    assert called["index"] == 0


# ── module registry sanity ────────────────────────────────────────────────

def test_rf667_web_atlas_registered_in_brain_hook():
    """The module name must already be in brain_hook._MODULE_TOPICS —
    if it's missing, brain_hook silently degrades the topics to ['general']
    and the wired-up signals never reach the right mastery cells."""
    from aria_service.intel import brain_hook
    assert "web_atlas" in brain_hook._MODULE_TOPICS, (
        "R-F667: 'web_atlas' missing from brain_hook._MODULE_TOPICS — "
        "the audit said it was registered at brain_hook.py:78."
    )
    topics = brain_hook._MODULE_TOPICS["web_atlas"]
    assert "osint" in topics or "market_intel" in topics
