"""R-F1965 — brain-side contract for degraded-outcome proprioception.

The Node surface fix (lib/aria/deliveryOutcome.mjs, used by the WA listener +
web proxy) records a DEGRADED non-answer as timeout_fallback/error instead of
delivered_real_answer — but only if the BRAIN tags the response. These tests
guard that contract: the degraded chat result carries a machine-readable flag
(`degraded` / `degradation_reason`) the surface can read, so today's "Cannot
reason without my brain" outage can never again be logged as a success.
"""
import asyncio
from pathlib import Path

from aria_service.intel import local_brain


def test_degraded_response_returns_a_marked_usable_dict():
    res = asyncio.run(local_brain.degraded_response("what can you analyse?", reason="LLM unavailable"))
    assert isinstance(res, dict)
    assert res.get("response"), "degraded path must still return SOME response text"
    # It must carry a reason the surface can surface as the failure detail.
    assert "degradation_reason" in res


def test_aria_engine_tags_the_chat_result_as_degraded():
    """The flag the Node surfaces read (`degraded: True`) must be set on the
    degraded-return paths in aria_engine — regression guard for R-F1965."""
    src = (Path(local_brain.__file__).resolve().parents[1] / "aria_engine.py").read_text(encoding="utf-8")
    assert '"degraded": True' in src, "aria_chat degraded-return must tag the result so surfaces can detect a non-answer"


def test_chat_ep_tags_llm_failure():
    """The other failure path (chat_ep catch) must tag llm_failure so the surface
    classifies it as a failure outcome, not delivered_real_answer."""
    src = (Path(local_brain.__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    assert '"llm_failure": True' in src
