"""R-F2294 — DD digital layer multi-angle query planner + parallel retrieval.

_run_digital fired ONE generic query ("<name> defence procurement") → adverse
media / ownership / sanctions-adjacent / regulatory coverage was systematically
under-retrieved (the confirmed digital-layer weakness). R-F2294 plans the facets
a defence-DD analyst searches by hand and retrieves them in parallel through
ARIA's own free backends (Brave-class breadth, natively), §21a-wired, default on.

These capability tests drive the REAL planner + the REAL parallel-search
aggregator (with the web_search backends + brain wiring stubbed).
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import web_search as ws
from aria_service.intel import engine_wiring as ew


def _sr(url, title="t"):
    return types.SimpleNamespace(url=url, title=title, snippet="", source_tier="UNVERIFIED")


class TestPlanner:
    def test_base_angles_present_and_named(self):
        plan = ddo._plan_digital_queries("Acme Defence", {})
        labels = {l for (_, l, _) in plan}
        assert {"procurement", "sanctions_adverse", "adverse_media", "ownership", "leadership"} <= labels
        assert all("Acme Defence" in q for (q, _, _) in plan)
        # procurement angle is the multilingual one
        assert any(l == "procurement" and ml is True for (_, l, ml) in plan)

    def test_regulatory_added_only_with_jurisdiction(self):
        assert not any(l == "regulatory" for (_, l, _) in ddo._plan_digital_queries("Acme", {}))
        plan = ddo._plan_digital_queries("Acme", {"jurisdiction": "Nigeria"})
        assert any(l == "regulatory" and "Nigeria" in q for (q, l, _) in plan)

    def test_product_context_added_only_with_product(self):
        assert not any(l == "product_context" for (_, l, _) in ddo._plan_digital_queries("Acme", {}))
        plan = ddo._plan_digital_queries("Acme", {"product_description": "Bayraktar TB2 UAV"})
        assert any(l == "product_context" and "UAV" in q for (q, l, _) in plan)


class TestMultiQuery:
    @pytest.fixture(autouse=True)
    def _stub_wiring(self, monkeypatch):
        self.wired = {}
        monkeypatch.setattr(ew, "wire_success", lambda **k: self.wired.__setitem__("success", k))
        monkeypatch.setattr(ew, "wire_failure", lambda **k: self.wired.__setitem__("failure", k))

    @pytest.mark.asyncio
    async def test_aggregates_and_dedupes_by_url(self, monkeypatch):
        async def fake_search(q, max_results=6):
            return [_sr("http://a.com/1"), _sr("http://dup.com/x")]
        async def fake_ml(q, max_results=6):
            return [_sr("http://b.com/1"), _sr("http://dup.com/x")]
        monkeypatch.setattr(ws, "search", fake_search)
        monkeypatch.setattr(ws, "search_multilingual", fake_ml)
        merged = await ddo._multi_query_search("Acme", {})
        urls = [h.url for h in merged]
        assert "http://a.com/1" in urls and "http://b.com/1" in urls
        # the duplicate URL from two different angles appears exactly once
        assert urls.count("http://dup.com/x") == 1
        assert "success" in self.wired  # non-zero → success telemetry

    @pytest.mark.asyncio
    async def test_zero_results_records_capability_gap(self, monkeypatch):
        async def empty(*a, **k):
            return []
        monkeypatch.setattr(ws, "search", empty)
        monkeypatch.setattr(ws, "search_multilingual", empty)
        merged = await ddo._multi_query_search("Acme", {})
        assert merged == []
        assert "failure" in self.wired  # zero-yield → capability gap (source_failure)
        assert self.wired["failure"].get("gap_type") == "source_failure"

    @pytest.mark.asyncio
    async def test_failing_angle_is_skipped_not_fatal(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("backend blocked")
        async def ok(*a, **k):
            return [_sr("http://ok.com/1")]
        monkeypatch.setattr(ws, "search", boom)          # every non-multilingual angle fails
        monkeypatch.setattr(ws, "search_multilingual", ok)  # procurement angle survives
        merged = await ddo._multi_query_search("Acme", {})
        assert [h.url for h in merged] == ["http://ok.com/1"]
