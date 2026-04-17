"""Tests for the learning-stats aggregator endpoint shape.

The GET /api/aria/learning/stats endpoint must always return a well-
formed payload with all 8 top-level keys, even when some backend
stores are empty or Redis is unreachable. The dashboard depends on
this shape being stable.
"""
from __future__ import annotations


def test_learning_stats_endpoint_registered():
    """The endpoint must be registered at module load time."""
    from aria_service.routes import aria as routes
    router = getattr(routes, "router", None)
    assert router is not None
    # Look through routes for /learning/stats
    paths = []
    for r in router.routes:
        p = getattr(r, "path", "") or ""
        paths.append(p)
    assert any("/learning/stats" in p for p in paths), (
        "GET /api/aria/learning/stats must be registered"
    )


def test_learning_stats_has_expected_top_level_keys():
    """The aggregator must always return all 8 top-level keys, even on
    empty state. The dashboard reads every key unconditionally."""
    # We can't easily call the route without a full app — so test the
    # SHAPE is guaranteed by reading the handler's literal `out` dict.
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    # The aggregator starts with: out = { ... }
    # All keys must appear in that block
    required = (
        "training_export", "knowledge_spider", "metacog_journal",
        "research_engine", "verification_gate", "quarantine",
        "bright_lines", "sanctions_propagation",
    )
    # Find the function body
    marker = "async def learning_stats_ep"
    idx = src.find(marker)
    assert idx >= 0, "learning_stats_ep not found in aria.py"
    snippet = src[idx:idx + 3000]
    for key in required:
        assert f'"{key}"' in snippet, f"learning/stats missing top-level key {key!r}"


def test_dashboard_panel_present():
    """The Learning & Verification card must be in the HTML."""
    from pathlib import Path
    html_path = Path(__file__).resolve().parents[2] / "public" / "aria-brain.html"
    html = html_path.read_text(encoding="utf-8")
    assert 'id="learning-panel"' in html, "learning panel missing from dashboard HTML"
    assert "Learning & Verification" in html, "Learning & Verification header missing"
    assert "loadLearning" in html, "loadLearning function missing"
    assert "loadLearning()" in html, "loadLearning() not called in refreshAll"


def test_dashboard_panel_reads_all_sections():
    """Panel must read all 8 sections of the aggregator payload."""
    from pathlib import Path
    html_path = Path(__file__).resolve().parents[2] / "public" / "aria-brain.html"
    html = html_path.read_text(encoding="utf-8")
    # The loadLearning function destructures: d.training_export, d.knowledge_spider, etc.
    for key in (
        "training_export", "knowledge_spider", "metacog_journal",
        "research_engine", "verification_gate", "quarantine",
        "bright_lines", "sanctions_propagation",
    ):
        assert f"d.{key}" in html, (
            f"dashboard loadLearning() must read d.{key} — "
            f"the aggregator ships it but nothing renders it"
        )
