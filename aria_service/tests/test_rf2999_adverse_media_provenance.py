"""R-F2999 — verdict-driving adverse-media evidence must carry provenance.

Live Silverbrook defect: the AMBER verdict rested on "32 credible adverse-media
item(s)" whose only shown "examples" were the source label 'brain_hook:web_search'
— no headline, URL, date or publisher a reviewer could check. The researcher DOES
capture title/url per hit, so we render real provenance when present; when it is
NOT carried, we say so plainly (the count alone is not evidence). The conservative
verdict is untouched — only the honesty of the evidence display changes.
"""
from aria_service.intel.dd_orchestrator import (
    _append_adverse_verdict_finding,
    _format_adverse_example,
    _adverse_example_has_provenance,
)


def _detail(body):
    return body["synthesis"]["key_findings"][0]["detail"]


def test_rf2999_real_provenance_is_rendered():
    body = {}
    mat = {"credible_count": 2, "official": 1, "examples": [
        {"title": "Firm fined by regulator", "url": "https://ex.com/a", "date": "2025-03-01", "source": "Reuters"},
    ]}
    _append_adverse_verdict_finding(body, mat, escalated=True)
    d = _detail(body)
    assert "Firm fined by regulator" in d
    assert "https://ex.com/a" in d
    assert "2025-03-01" in d
    assert "⚠" not in d  # provenance present → no warning


def test_rf2999_source_label_not_shown_as_headline_and_flagged():
    body = {}
    mat = {"credible_count": 32, "official": 0, "examples": [
        {"title": "brain_hook:web_search", "source": "web_search"},
        {"title": "brain_hook:web_search", "source": "web_search"},
    ]}
    _append_adverse_verdict_finding(body, mat, escalated=True)
    d = _detail(body)
    assert "brain_hook:web_search" not in d, "a source label must not render as an article headline"
    assert "⚠" in d, "missing-provenance warning must be present"
    assert "count alone is not evidence" in d


def test_rf2999_provenance_helpers():
    assert _adverse_example_has_provenance({"url": "http://x"}) is True
    assert _adverse_example_has_provenance({"link": "http://x"}) is True
    assert _adverse_example_has_provenance({"title": "brain_hook:web_search"}) is False
    assert _adverse_example_has_provenance({}) is False
    out = _format_adverse_example({"url": "http://x/y", "title": "Real Headline", "date": "2024-01-02"})
    assert "Real Headline" in out and "http://x/y" in out and "2024-01-02" in out
    # a source-label 'title' with no url renders honestly, not as a headline
    lbl = _format_adverse_example({"title": "brain_hook:web_search", "source": "web_search"})
    assert "brain_hook:web_search" not in lbl and "web_search" in lbl
