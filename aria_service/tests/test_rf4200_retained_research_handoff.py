"""R-F4200 — retained deep-research evidence survives missing synthesis."""

import asyncio

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport


def _partial_research_result() -> dict:
    return {
        "partial": True,
        "stopped_after": "synthesis (did not complete in the remaining budget)",
        "articles_read": 3,
        "facts_learned": 3,
        "search_angles": 4,
        "synthesis": None,
        "facts": [
            {
                "topic": "Ownership",
                "content": "Vigilo Solutions identifies a named director in its filing.",
                "confidence": "PROBABLE",
                "source_url": "https://registry.example/vigilo",
            },
            {
                "topic": "Ownership duplicate",
                "content": "Vigilo Solutions identifies a named director in its filing.",
                "confidence": "CONFIRMED",
                "source_url": "https://duplicate.example/vigilo",
            },
            {
                "topic": "Untraceable",
                "content": "A claim with no auditable source must not be published.",
                "confidence": "CONFIRMED",
            },
        ],
        "people": [],
        "people_disclosures": [],
        "verification_summary": {"failed": None},
    }


def test_retained_fact_fallback_is_bounded_deduplicated_and_unverified():
    findings = DD._retained_research_findings(_partial_research_result())

    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert findings[0].confidence == "UNVERIFIED"
    assert findings[0].source == "https://duplicate.example/vigilo"
    assert "verify this claim" in findings[0].detail


def test_real_digital_layer_preserves_facts_when_synthesis_is_missing(monkeypatch):
    """Drive the broken consumer, replacing external I/O but not its handoff logic."""
    from aria_service.intel import deep_researcher, knowledge, neural_memory, rag_store, web_search

    async def _empty_async(*args, **kwargs):
        return []

    async def _empty_text(*args, **kwargs):
        return ""

    async def _investigate(*args, **kwargs):
        return _partial_research_result()

    monkeypatch.setattr(DD, "DEEP_RESEARCH_ENABLED", True)
    monkeypatch.setattr(DD, "_multi_query_search", _empty_async)
    monkeypatch.setattr(DD, "_register_credential_sweep", _empty_async)
    monkeypatch.setattr(deep_researcher, "investigate", _investigate)
    monkeypatch.setattr(rag_store, "get_rag_context", _empty_text)
    monkeypatch.setattr(rag_store, "search", _empty_async)
    monkeypatch.setattr(neural_memory, "get_neural_context", _empty_text)
    monkeypatch.setattr(knowledge, "search_knowledge", lambda *args, **kwargs: "")
    monkeypatch.setattr(web_search, "get_last_search_ecosystem", lambda: {})

    report = ARKDDReport(target={"name": "Vigilo Solutions Limited"})
    report.identity.entity_name = "Vigilo Solutions Limited"
    asyncio.run(DD._run_digital(
        {"name": "Vigilo Solutions Limited"}, report, llm=object(), _mode_is_deep=True
    ))

    retained = [f for f in report.digital.findings if "Retained research lead" in f.title]
    assert len(retained) == 1
    assert retained[0].confidence == "UNVERIFIED"
    assert retained[0].source.startswith("https://")
    assert any("28" not in gap and "3 fact(s) retained" in gap
               for gap in report.digital.data_gaps)
