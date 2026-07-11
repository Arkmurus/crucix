"""R-F2545 — CI FABRICATION GATE. Build fails if the honesty guarantee regresses.

This is the deterministic, offline gate (no LLM, no network) that CI runs on every
build. It fails — blocking the build — if:
  1. the citation verifier stops eliminating fabricated citations, OR
  2. the live synthesis paths (complete_synthesis / stream_synthesis) stop applying
     verification, OR
  3. someone removes the _verify_grounded wiring from model_router.

The property being gated, in one line: AFTER verification, grounding_reward must see
ZERO fabricated citations on any grounded answer that reaches a user.
"""
from __future__ import annotations

import inspect

import pytest

from aria_service.intel import citation_verifier as cv
from aria_service.intel import grounding_reward as gr
from aria_service.llm import model_router as mr
from aria_service.llm.provider import LLMResult

# Real production-format context: authoritative "↳ source:" labels + typed headers.
_CTX = (
    "• [1.02] web_search: sanctions\n  ↳ source: ofac.treasury.gov | 2026-05-01\n"
    "• [2.01] registry: filing\n  ↳ source: companies_house_uk | 2026-04-10\n"
    "The entity is OFAC-designated and registered in the UK." + " padding" * 40
)

# Grounded answers that MIX a real citation with a fabricated one. Post-verify,
# the fabricated source must be gone and grounding_reward must count 0 fabrication.
_FIXTURES = [
    "Designated by OFAC [Source: ofac.treasury.gov]; also indicted [Source: fabricated_court_2026].",
    "Registered in the UK [from companies_house_uk] and flagged by [from interpol_rednotice].",
    "Sanctioned [Source: ofac.treasury.gov] and linked to [Source: madeup_intel_feed] and [Source: fake_db].",
    "All fabricated: [Source: ghost_source_a], [Source: ghost_source_b].",
]


@pytest.mark.parametrize("answer", _FIXTURES)
def test_gate_verifier_eliminates_fabrication(answer):
    """After verify_and_clean, grounding_reward sees ZERO fabricated citations."""
    before = gr.score(answer, _CTX)
    cleaned = cv.verify_and_clean(answer, _CTX)["answer"]
    after = gr.score(cleaned, _CTX)
    assert before.fabricated_citations >= 1, "fixture must contain a fabricated citation"
    assert after.fabricated_citations == 0, f"fabrication survived verification: {cleaned!r}"


def test_gate_real_citations_survive():
    """Verification must NOT strip citations that resolve to real evidence."""
    ans = "Designated by OFAC [Source: ofac.treasury.gov]."
    cleaned = cv.verify_and_clean(ans, _CTX)["answer"]
    assert "ofac.treasury.gov" in cleaned
    assert cv.verify_and_clean(ans, _CTX)["fabricated_removed"] == 0


class _FabLLM:
    async def complete(self, system, user, *, max_tokens=4096, timeout=60.0):
        return LLMResult(text=_FIXTURES[2], model="deepseek-chat", routed_via="deepseek")
    async def stream(self, system, user, *, max_tokens=4096, timeout=120.0, on_done=None):
        yield _FIXTURES[2]


@pytest.mark.asyncio
async def test_gate_complete_synthesis_wired(monkeypatch):
    """complete_synthesis must strip fabrication from the shipped answer."""
    r = await mr.complete_synthesis(_FabLLM(), "sys", "user",
                                    message="[TOOL: web_search] summarise", context=_CTX)
    assert gr.score(r.text, _CTX).fabricated_citations == 0
    assert "ofac.treasury.gov" in r.text  # real source survives


@pytest.mark.asyncio
async def test_gate_stream_synthesis_wired(monkeypatch):
    """stream_synthesis (chat_stream) must strip fabrication before it reaches the user."""
    out = []
    async for c in mr.stream_synthesis(_FabLLM(), "sys", "user",
                                       message="[TOOL: web_search] summarise", context=_CTX):
        out.append(c)
    txt = "".join(out)
    assert gr.score(txt, _CTX).fabricated_citations == 0
    assert "ofac.treasury.gov" in txt


def test_gate_wiring_present_in_source():
    """Structural guard: _verify_grounded must be invoked in BOTH synthesis functions,
    so the wiring can't be silently removed while a narrow behavioral test still passes."""
    for fn in (mr.complete_synthesis, mr.stream_synthesis):
        src = inspect.getsource(fn)
        assert "_verify_grounded" in src, f"{fn.__name__} no longer calls the citation verifier"


def test_gate_bypass_paths_stay_wired():
    """R-F2546 structural guard: the citation-capable paths that bypass model_router
    (doc-review, /think, grounding-repair, report draft) must each call the verifier,
    so a coverage gap cannot silently re-open."""
    from aria_service import aria_engine
    from aria_service.intel import report_builder
    from aria_service.routes import aria as routes_aria
    for name, fn in (
        ("doc_lane_chat", aria_engine.doc_lane_chat),
        ("aria_think", aria_engine.aria_think),
        ("maybe_repair_grounding", routes_aria.maybe_repair_grounding),
        ("build_report", report_builder.build_report),
    ):
        src = inspect.getsource(fn)
        assert ("citation_verifier" in src or "verify_and_clean" in src), \
            f"{name} no longer verifies citations — a fabricated source could ship"
