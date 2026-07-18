"""R-F2745 — adverse-media deep search must not attribute off-subject news.

run_adverse_media_deep_search appended EVERY web result for its entity-anchored
queries as an adverse-media finding for the subject — feeding the grade's
adverse_media_findings — with no check that the article NAMES the subject. So a
result matching only the adverse topic ("...fraud...") or a DIFFERENT same-named
entity inflated the subject's adverse exposure. Now a finding is kept only if the
article names the subject, a director, or a UBO.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.researcher as r


# ── the gate unit ────────────────────────────────────────────────────────────

def test_rf2745_gate_contract():
    ns = r._adverse_relevance_token_sets(["Acme Ventures Ltd", "John Smith"])
    g = lambda t: r._adverse_hit_names_subject(t, ns)
    assert g("Acme Ventures fined for fraud by the regulator") is True      # names entity
    assert g("John Smith charged with bribery") is True                     # names director
    assert g("Major fraud scandal rocks the defence sector") is False       # topic only
    assert g("Acme Widgets Inc under investigation") is False               # different same-named entity
    assert r._adverse_hit_names_subject("anything", []) is True             # no usable names → no gate


# ── the real deep-search path ────────────────────────────────────────────────

def test_rf2745_deep_search_drops_off_subject_findings(monkeypatch):
    # One entity-anchored template.
    monkeypatch.setattr(
        r._dd_disc if hasattr(r, "_dd_disc") else __import__(
            "aria_service.intel.dd_disciplines", fromlist=["x"]),
        "adverse_media_query_templates",
        lambda **k: [{"query": '"Acme Ventures" fraud', "source_class": "news", "purpose": "fraud"}],
    )

    class _Hit:
        def __init__(self, title, snippet=""):
            self.title = title
            self.snippet = snippet
            self.url = "https://example.com/x"
            self.link = "https://example.com/x"

    async def _fake_search(query, timeout=10.0):
        return [
            _Hit("Acme Ventures Ltd fined $2m for fraud"),          # KEEP — names the entity
            _Hit("Generic fraud crackdown in the sector"),          # DROP — topic only
            _Hit("Acme Widgets Inc probed by SEC"),                 # DROP — different entity
        ]

    monkeypatch.setattr(r, "_web_search", _fake_search)
    # neutralise the hit->dict shaping to keep title/snippet intact
    monkeypatch.setattr(r, "_search_hit_to_dict",
                        lambda h: {"title": h.title, "snippet": h.snippet, "url": h.url,
                                   "link": h.link, "_credibility_tier": "tier_2"})

    out = asyncio.run(r.run_adverse_media_deep_search("Acme Ventures", max_templates=1))
    assert out["ok"] is True
    assert out["findings_count"] == 1, f"only the on-subject finding should count: {out['findings']}"
    assert out["off_subject_dropped"] == 2
    assert "Acme Ventures" in out["findings"][0]["title"]
