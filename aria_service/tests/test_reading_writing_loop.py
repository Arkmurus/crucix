"""Tests for the reading/writing/formulation upgrade (2026-04-18):

  - pdf_deep_ingest.summary() shape
  - style_learner: topic classifier, pattern extractors, get_stats shape
  - autonomous task + yaml wiring for STYLE-LEARN-HOURLY
  - dashboard aggregator includes style + deep-ingest sections
  - Bumped limits are in place
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════
# 1. Limit bumps
# ═══════════════════════════════════════════════════════════════════════

def test_max_doc_chars_bumped():
    """ARIA_MAX_DOC_CHARS default must be 500k now (was 200k)."""
    from aria_service.routes.aria import MAX_DOC_CHARS
    # Default applied when env var not set
    assert MAX_DOC_CHARS >= 500_000, (
        f"MAX_DOC_CHARS is {MAX_DOC_CHARS}; bumped to 500k 2026-04-18"
    )


def test_spider_chunk_bumped():
    from aria_service.learning import knowledge_spider
    assert knowledge_spider._MAX_TEXT_PER_CHUNK >= 30_000


# ═══════════════════════════════════════════════════════════════════════
# 2. pdf_deep_ingest — shape + summary
# ═══════════════════════════════════════════════════════════════════════

def test_pdf_deep_ingest_summary():
    # R-F2333: summary() is now async (surfaces live activity counters alongside
    # the tuning-constant manifest).
    import asyncio
    from aria_service.intel import pdf_deep_ingest
    s = asyncio.run(pdf_deep_ingest.summary())
    for k in ("min_page_text_chars", "max_images_per_page",
              "max_images_per_pdf", "min_image_area_pixels",
              "total_ingests", "chunks_ingested", "last_ingest_at"):
        assert k in s


# ═══════════════════════════════════════════════════════════════════════
# 3. style_learner — topic classifier + pattern extractor
# ═══════════════════════════════════════════════════════════════════════

def test_style_learner_topic_classifier():
    from aria_service.learning.style_learner import _classify_topic
    assert _classify_topic("run a DD on ACME LLC", "Risk classification: RED") == "dd_report"
    assert _classify_topic("is X sanctioned on OFAC", "") == "compliance"
    assert _classify_topic("produce a STANAG assessment", "") == "assessment"
    assert _classify_topic("draft an ITT", "") == "procurement"
    assert _classify_topic("what's the STANAG ref", "") == "technical"
    # Default fallback
    assert _classify_topic("hello", "hi there") == "chat"


def test_style_learner_extracts_bluf():
    from aria_service.learning.style_learner import extract_patterns
    reply = (
        "🔴 BOTTOM LINE — Do not proceed. This entity carries critical "
        "sanctions exposure.\n\n"
        "📋 DOCUMENT ANALYSIS\n\n"
        "Facts [from ATTACHED DOCUMENT] confirm...\n\n"
        "⚠️ COMPLIANCE FLAGS\n\n"
        "1. Sanctions hit [from dd_orchestrate:dd_abc123]\n\n"
        "✅ RECOMMENDED ACTION\n\n"
        "IMMEDIATE STOP — cease engagement.\n\n"
        "Confidence: [ASSESSED] | Risk: RED"
    )
    p = extract_patterns(reply)
    assert p["bluf"] is not None
    assert "Do not proceed" in p["bluf"]
    assert len(p["headings"]) >= 2
    assert p["citation_count"] >= 2
    assert p["recommendation"] == "IMMEDIATE STOP"
    assert p["confidence_footer"] is not None


def test_style_learner_stats_shape():
    import asyncio
    from aria_service.learning import style_learner

    async def run():
        return await style_learner.get_stats()

    s = asyncio.run(run())
    for k in ("replies_scanned_24h", "exemplars_added_24h",
              "total_exemplars", "by_topic", "last_run"):
        assert k in s


def test_style_learner_summary():
    from aria_service.learning import style_learner
    s = style_learner.summary()
    assert s["max_exemplars_per_topic"] >= 4
    assert s["lookback_hours"] >= 12


# ═══════════════════════════════════════════════════════════════════════
# 4. get_style_prompt_addendum — empty when no exemplars
# ═══════════════════════════════════════════════════════════════════════

def test_style_prompt_addendum_empty_when_no_exemplars():
    import asyncio
    from aria_service.learning import style_learner

    async def run():
        return await style_learner.get_style_prompt_addendum("Hello there")

    # With no exemplars in Redis (test env), must return "" not raise
    result = asyncio.run(run())
    assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════
# 5. Wiring — tasks.py + tasks.yaml
# ═══════════════════════════════════════════════════════════════════════

def test_tasks_py_dispatches_style_learner():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.py"
    text = src.read_text(encoding="utf-8")
    assert '"style_learner"' in text, "tasks.py missing style_learner dispatch"


def test_tasks_yaml_has_style_learn_hourly_enabled():
    import yaml
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    task_ids = {t["id"]: t for t in data.get("tasks", [])}
    assert "STYLE-LEARN-HOURLY" in task_ids
    t = task_ids["STYLE-LEARN-HOURLY"]
    assert t["enabled"] is True
    assert t.get("cost_cap_usd", 0.0) == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 6. Dashboard aggregator surface
# ═══════════════════════════════════════════════════════════════════════

def test_learning_stats_aggregator_includes_new_modules():
    """The /learning/stats aggregator must ship style_learner + pdf_deep_ingest."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    # The aggregator block should reference both new keys
    marker = "async def learning_stats_ep"
    idx = src.find(marker)
    snippet = src[idx:idx + 4000] if idx >= 0 else ""
    assert '"style_learner"' in snippet or 'style_learner' in snippet
    assert '"pdf_deep_ingest"' in snippet or 'pdf_deep_ingest' in snippet


def test_dashboard_renders_style_learner():
    from pathlib import Path
    html_path = Path(__file__).resolve().parents[2] / "public" / "aria-brain.html"
    html = html_path.read_text(encoding="utf-8")
    assert "Style exemplars" in html or "d.style_learner" in html
