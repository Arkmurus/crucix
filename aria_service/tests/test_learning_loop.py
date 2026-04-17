"""Tests for the continuous learning loop (2026-04-18).

Four modules — all LLM-free, zero external cost:
  - training_export
  - knowledge_spider
  - metacognitive_journal
  - research_engine

These tests verify module wiring, summary shape, and pure-function
behaviour. Full integration (spider actually fetching URLs, research
engine actually hitting web_search) is deferred to staging because
it touches live infrastructure.
"""
from __future__ import annotations

import asyncio


# ═══════════════════════════════════════════════════════════════════════
# 1. training_export
# ═══════════════════════════════════════════════════════════════════════

def test_training_export_has_summary():
    from aria_service.learning import training_export
    s = training_export.summary()
    assert "export_dir" in s
    assert "total_examples" in s


def test_training_export_manifest_safe_when_empty():
    from aria_service.learning import training_export
    m = training_export.get_manifest()
    assert isinstance(m, dict)


def test_training_export_excludes_quarantine_tags():
    """Responses containing CITATION-QUARANTINED must never reach training data."""
    from aria_service.learning import training_export
    bad_reply = (
        "[from dd_orchestrate:dd_abc123] Previously confirmed sanctions hit "
        "[CITATION-QUARANTINED — prior run defective; re-verify]"
    )
    excluded = any(tag.lower() in bad_reply.lower() for tag in training_export._EXCLUDE_TAGS)
    assert excluded is True


# ═══════════════════════════════════════════════════════════════════════
# 2. knowledge_spider
# ═══════════════════════════════════════════════════════════════════════

def test_spider_normalises_urls():
    from aria_service.learning.knowledge_spider import _normalise_url
    # http+host preserved, tracking dropped, fragment dropped
    assert _normalise_url("https://example.com/path?utm_source=x&real=1") \
        == "https://example.com/path?real=1"
    assert _normalise_url("https://example.com/path#frag") == "https://example.com/path"


def test_spider_rejects_blocklist():
    from aria_service.learning.knowledge_spider import _normalise_url
    assert _normalise_url("https://facebook.com/profile") is None
    assert _normalise_url("https://twitter.com/foo") is None


def test_spider_rejects_non_http():
    from aria_service.learning.knowledge_spider import _normalise_url
    assert _normalise_url("ftp://example.com/file") is None
    assert _normalise_url("mailto:x@example.com") is None
    assert _normalise_url("javascript:alert(1)") is None


def test_spider_summary_shape():
    from aria_service.learning import knowledge_spider
    s = knowledge_spider.summary()
    assert s["max_depth"] >= 2
    assert s["wall_budget_sec"] > 0


def test_spider_url_hash_stable():
    from aria_service.learning.knowledge_spider import _url_hash
    h1 = _url_hash("https://example.com/path")
    h2 = _url_hash("https://example.com/path")
    h3 = _url_hash("https://OTHER.com/path")
    assert h1 == h2
    assert h1 != h3


# ═══════════════════════════════════════════════════════════════════════
# 3. metacognitive_journal
# ═══════════════════════════════════════════════════════════════════════

def test_metacog_summary_has_settings():
    from aria_service.learning import metacognitive_journal
    s = metacognitive_journal.summary()
    assert s["lookback_hours"] >= 1
    assert s["max_entries"] >= 24


def test_metacog_is_after_ts_parsing():
    from aria_service.learning.metacognitive_journal import _is_after
    from datetime import datetime, timezone, timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    # Unix timestamps
    future_ts = (datetime.now(timezone.utc)).timestamp()
    past_ts   = (since - timedelta(hours=10)).timestamp()
    assert _is_after(future_ts, since) is True
    assert _is_after(past_ts, since) is False
    # ISO strings
    assert _is_after(datetime.now(timezone.utc).isoformat(), since) is True
    # Bogus inputs → False, never raise
    assert _is_after("not a date", since) is False
    assert _is_after(None, since) is False


def test_metacog_run_returns_entry_shape():
    """Even when no data exists, the journal must produce a well-formed entry."""
    from aria_service.learning import metacognitive_journal

    async def run():
        return await metacognitive_journal.run_hourly_journal()

    entry = asyncio.run(run())
    assert isinstance(entry, dict)
    for k in ("timestamp", "window_start", "successes", "failures",
              "novelties", "counts", "hash"):
        assert k in entry
    assert isinstance(entry["counts"], dict)


# ═══════════════════════════════════════════════════════════════════════
# 4. research_engine
# ═══════════════════════════════════════════════════════════════════════

def test_research_generates_queries_for_topic():
    from aria_service.learning.research_engine import _generate_queries
    q = _generate_queries("procurement", "west_africa")
    assert len(q) >= 1
    # Region should be expanded to display name
    assert any("West Africa" in qq for qq in q)


def test_research_region_display_names():
    from aria_service.learning.research_engine import _region_display_name
    assert _region_display_name("turkey") == "Türkiye"
    assert _region_display_name("west_africa") == "West Africa"
    assert _region_display_name("latam_non_lusophone") == "Latin America"
    # Unknown slug → human-readable fallback
    assert _region_display_name("unknown_slug") == "Unknown Slug"


def test_research_generates_queries_for_unknown_topic():
    """Unknown topics must fall back to general templates, not crash."""
    from aria_service.learning.research_engine import _generate_queries
    q = _generate_queries("made_up_topic", "gulf")
    assert len(q) >= 1


def test_research_summary_lists_covered_topics():
    from aria_service.learning import research_engine
    s = research_engine.summary()
    assert "topics_with_templates" in s
    # At least procurement + compliance + geopolitics must be covered
    assert "procurement" in s["topics_with_templates"]
    assert "compliance" in s["topics_with_templates"]


# ═══════════════════════════════════════════════════════════════════════
# 5. Autonomous engine wiring
# ═══════════════════════════════════════════════════════════════════════

def test_tasks_py_dispatches_all_four_learning_tools():
    """The autonomous task runner must route to each of the 4 new tools."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.py"
    text = src.read_text(encoding="utf-8")
    for kind in ("training_export", "knowledge_spider",
                 "metacognitive_journal", "research_engine"):
        assert f'"{kind}"' in text, f"tasks.py missing dispatch for {kind!r}"


def test_tasks_yaml_has_all_four_learning_tasks_enabled():
    """All four must be in tasks.yaml with enabled: true + cost_cap 0."""
    import yaml
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    required = {"LEARNING-EXPORT-DAILY", "SPIDER-HOURLY",
                "METACOG-JOURNAL-HOURLY", "RESEARCH-WEAK-CELL-HOURLY"}
    task_ids = {t["id"] for t in data.get("tasks", [])}
    missing = required - task_ids
    assert not missing, f"tasks.yaml missing: {missing}"
    for t in data["tasks"]:
        if t["id"] in required:
            assert t["enabled"] is True, f"{t['id']} must be enabled"
            assert t.get("cost_cap_usd", 0.0) == 0.0, f"{t['id']} must be cost-free"
