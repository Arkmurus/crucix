"""R-F4202 — hard DD operation cancellation preserves retained evidence."""

import asyncio

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport


def test_bounded_operation_returns_callee_partial_payload_after_timeout():
    """Drive the real cancellation wrapper and prove completed work survives."""
    progress = {}
    report = ARKDDReport(target={"name": "Vigilo Solutions Limited"})
    retained = {
        "partial": True,
        "articles_read": 10,
        "facts_learned": 10,
        "facts": [{
            "content": "A retained source-linked fact.",
            "source_url": "https://registry.example/vigilo",
        }],
        "synthesis": None,
    }

    async def _operation():
        progress.update({
            "stage": "fact retention",
            "retained": 10,
            "partial_result": retained,
        })
        await asyncio.sleep(0.2)
        return {"unexpected": True}

    result = asyncio.run(DD._bounded_dd_op(
        _operation(), 0.02, report.digital, "deep research",
        default={}, progress=progress,
    ))

    assert result == retained
    assert "retained=10" in report.digital.data_gaps[-1]
    assert "partial_result" not in report.digital.data_gaps[-1]


def test_hard_cancel_payload_reaches_unverified_findings():
    """Prove the preserved payload crosses the same final conversion boundary."""
    payload = {
        "partial": True,
        "articles_read": 10,
        "facts_learned": 10,
        "facts": [{
            "topic": "Registry",
            "content": "The filing records a current director.",
            "confidence": "PROBABLE",
            "source_url": "https://registry.example/vigilo",
        }],
        "synthesis": None,
    }

    findings = DD._retained_research_findings(payload)
    assert len(findings) == 1
    assert findings[0].confidence == "UNVERIFIED"
    assert findings[0].source == "https://registry.example/vigilo"
