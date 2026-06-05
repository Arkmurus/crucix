"""R-F1337 — compact system prompt for small-model (aria-llm 7B) serving.

Live failures this fixes (2026-06-05): with the full 100K+ char calibrated
prompt, v0.1-SFT answered "What is ITAR?" with an off-prompt PMESII-Angola
assessment (it followed an in-prompt scaffold instead of the user), and
produced incoherent mixed-language alert text on a simple identity probe.

Capability under test: when ARIA_LLM_URL is set (her model is chain
primary), _build_calibrated_system_prompt returns the compact
invariants-only prompt — small, scaffold-free — for BOTH chat paths
(they share this builder, §13). When unset, the full prompt returns.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service import aria_engine as ae


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    monkeypatch.delenv("ARIA_LLM_COMPACT_PROMPT", raising=False)
    yield


# ── activation logic ─────────────────────────────────────────────────────


def test_inactive_by_default():
    assert ae._compact_prompt_active() is False


def test_active_when_aria_llm_url_set(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    assert ae._compact_prompt_active() is True


def test_explicit_disable_overrides_url(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    monkeypatch.setenv("ARIA_LLM_COMPACT_PROMPT", "0")
    assert ae._compact_prompt_active() is False


def test_explicit_enable_without_url(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_COMPACT_PROMPT", "1")
    assert ae._compact_prompt_active() is True


# ── the compact prompt itself ────────────────────────────────────────────


def test_compact_prompt_is_small_and_holds_invariants():
    p = ae.ARIA_SYSTEM_PROMPT_COMPACT
    assert len(p) < 6000, f"compact prompt must stay 7B-sized, got {len(p)}"
    # Operational invariants that MUST survive distillation
    for marker in ("[CONFIRMED]", "[ASSESSED]", "[SPECULATIVE]",
                   "NEVER FABRICATE", "COMPLIANCE FIRST", "ARIA",
                   "ANSWER THE QUESTION ASKED"):
        assert marker in p, f"compact prompt lost invariant: {marker}"
    # Scaffolds that MUST NOT be present (they derail a 7B)
    assert "PMESII" not in p
    assert "Past incident" not in p


# ── capability: the real builder returns compact for the 7B ──────────────


@pytest.mark.asyncio
async def test_builder_returns_compact_when_active(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    out = await ae._build_calibrated_system_prompt("What is ITAR?")
    assert out == ae.ARIA_SYSTEM_PROMPT_COMPACT
    assert "PMESII" not in out  # the exact scaffold that hijacked the live reply
    assert len(out) < 6000


@pytest.mark.asyncio
async def test_builder_returns_full_when_inactive():
    out = await ae._build_calibrated_system_prompt("What is ITAR?")
    # The full calibrated prompt starts from the full constitution
    assert out.startswith(ae.ARIA_SYSTEM_PROMPT[:200])
    assert len(out) > len(ae.ARIA_SYSTEM_PROMPT_COMPACT)


@pytest.mark.asyncio
async def test_compact_applies_in_doc_mode_too(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    msg = '[ATTACHED DOCUMENT: nda.pdf]\nsome text\nreview the NDA for feedback'
    out = await ae._build_calibrated_system_prompt(msg)
    assert out == ae.ARIA_SYSTEM_PROMPT_COMPACT  # doc review rule is IN the compact prompt
    assert "DOCUMENT REVIEW" in out
