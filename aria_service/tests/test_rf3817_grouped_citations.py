"""R-F3817 — grouped citations validate each returned source independently."""
from __future__ import annotations

from scripts.train import build_tooluse_corpus as B
from scripts.train.build_tooluse_dpo import build_pairs


SEARCH = {
    "query": "L3Harris market impact",
    "results": [
        {"title": "Coverage one", "source": "aria_search",
         "url": "https://www.cnbc.com/story", "snippet": "Contract coverage."},
        {"title": "Coverage two", "source": "aria_search",
         "url": "https://finance.yahoo.com/story", "snippet": "Market coverage."},
        {"title": "Company release", "source": "aria_search",
         "url": "https://www.l3harris.com/story", "snippet": "Company statement."},
    ],
}


def _trace(citation: str) -> dict:
    trace = B.build_news_impact_trace("L3Harris Technologies", SEARCH)
    trace["messages"][-1]["content"] = (
        "The contract may affect revenue, but does not establish a change in "
        f"ownership or sanctions exposure [from {citation}]."
    )
    return trace


def _citation_errors(trace: dict) -> list[str]:
    return [
        error for error in B.validate_trace(trace)
        if "no tool result contains" in error or "independent source" in error
    ]


def test_grouped_returned_sources_are_accepted_by_real_validator() -> None:
    """Replay the live v4 false positive through the actual scoring function."""
    assert _citation_errors(
        _trace("cnbc.com, finance.yahoo.com, l3harris.com")
    ) == []


def test_one_fabricated_member_keeps_grouped_citation_rejected() -> None:
    errors = _citation_errors(_trace("cnbc.com, invented.example"))
    assert errors
    assert any("invented.example" in error for error in errors)


def test_empty_group_member_is_rejected_fail_closed() -> None:
    errors = _citation_errors(_trace("cnbc.com, "))
    assert errors
    assert any("cites ''" in error for error in errors)


def test_preference_builder_rescores_stale_false_failure() -> None:
    trace = _trace("cnbc.com, finance.yahoo.com, l3harris.com")
    report = {"rows": [{
        "label": trace["label"],
        "subject": trace["subject"],
        "honest": False,
        "errors": ["stale grouped-citation false positive"],
        "answer": trace["messages"][-1]["content"],
    }]}
    pairs = build_pairs(
        report,
        [trace],
        eval_entities={"held out entity"},
        validate_chosen=True,
    )
    assert pairs == []
