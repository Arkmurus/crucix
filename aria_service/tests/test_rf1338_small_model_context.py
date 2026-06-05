"""R-F1338 — small-model user-context reduction.

Live failure fixed (2026-06-05): even with the compact system prompt
(R-F1337), v0.1-SFT derailed through the chat pipeline ("What is ITAR?"
-> PMESII-Angola ramble; "UNDERSTOOD AS:" / "DAILY SUBSCRIPTION STATUS"
fragments) because the USER prompt still carried the full 7-layer recall +
intel + mode-block + scratchpad context. The clean direct endpoint (no
context) answered ITAR in 2.4s. This strips the injected context to only
compliance-critical blocks when a 7B is serving.
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


# A realistic noisy context: mode block + recall + a sanctions verdict +
# an intel layer — the exact shape that derailed the 7B.
NOISY_CONTEXT = (
    "[RESPONSE MODE: DIALOGUE]\nUNDERSTOOD AS: answer conversationally\n\n"
    "[SANCTIONS LIVE CHECK — AUTHORITATIVE]\nEntity: Acme Corp — NOT on OFAC SDN as of 2026-06-05\n\n"
    "[MEMORY RECALL]\nPMESII assessment of Angola defence market: political...\n\n"
    "[LIVE INTELLIGENCE]\nDAILY SUBSCRIPTION STATUS: forecasts enabled\n"
)


# ── the reducer ──────────────────────────────────────────────────────────


def test_reducer_keeps_sanctions_drops_noise():
    out = ae._reduce_context_for_small_model(NOISY_CONTEXT)
    assert "SANCTIONS LIVE CHECK" in out
    assert "NOT on OFAC SDN" in out
    # the derailment sources are gone
    assert "PMESII" not in out
    assert "UNDERSTOOD AS" not in out
    assert "DAILY SUBSCRIPTION STATUS" not in out
    assert "MEMORY RECALL" not in out


def test_reducer_keeps_self_introspect_anti_fabrication():
    # Clause 25 grounding must survive for a 7B so it doesn't invent counts.
    ctx = (
        "[RESPONSE MODE: DIALOGUE]\nUNDERSTOOD AS: x\n\n"
        "[TOOL: self_introspect — auto-fired]\nknowledge_facts: 86,266\nautonomy: enabled\n\n"
        "[MEMORY RECALL]\nPMESII Angola noise...\n"
    )
    out = ae._reduce_context_for_small_model(ctx)
    assert "self_introspect" in out
    assert "86,266" in out
    assert "PMESII" not in out
    assert "UNDERSTOOD AS" not in out


def test_reducer_two_adjacent_whitelisted_blocks():
    ctx = (
        "[SANCTIONS LIVE CHECK — AUTHORITATIVE]\nAcme: clear\n\n"
        "[TOOL: self_introspect — auto-fired]\nfacts: 86,266\n\n"
        "[MEMORY RECALL]\nnoise\n"
    )
    out = ae._reduce_context_for_small_model(ctx)
    assert "Acme: clear" in out
    assert "86,266" in out
    assert "noise" not in out


def test_reducer_empty_when_no_whitelisted_block():
    ctx = (
        "[RESPONSE MODE: DIALOGUE]\nUNDERSTOOD AS: x\n\n"
        "[MEMORY RECALL]\nPMESII Angola...\n\n[LIVE INTELLIGENCE]\nnoise\n"
    )
    assert ae._reduce_context_for_small_model(ctx) == ""


def test_reducer_handles_empty():
    assert ae._reduce_context_for_small_model("") == ""
    assert ae._reduce_context_for_small_model(None) == ""


def test_reducer_never_truncates_a_block_mid_text():
    """R-F1346 (R-F949 lesson): a single oversized whitelisted block (e.g. a
    sanctions verdict) is kept WHOLE — never sliced mid-sentence — even past
    the budget. Truncating a compliance verdict is worse than going over."""
    big = "[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n" + ("x" * 9000)
    out = ae._reduce_context_for_small_model(big, max_chars=500)
    assert out.count("x") == 9000  # full verdict preserved, not cut to 500


def test_reducer_drops_whole_lower_priority_blocks_over_budget():
    """Over budget, later blocks are dropped WHOLE (not truncated). The first
    (compliance) block always survives intact."""
    ctx = (
        "[SANCTIONS LIVE CHECK — AUTHORITATIVE]\nAcme: NOT on OFAC SDN\n\n"
        "[TOOL: self_introspect — auto-fired]\n" + ("y" * 4000) + "\n"
    )
    out = ae._reduce_context_for_small_model(ctx, max_chars=200)
    assert "Acme: NOT on OFAC SDN" in out          # priority block kept whole
    assert "yyyy" not in out                        # oversized later block dropped whole
    assert "self_introspect" not in out


# ── the shared builder (both chat paths) ─────────────────────────────────


def test_builder_strips_context_when_small_model(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    up = ae._format_history_user_prompt([], "", "What is ITAR?", NOISY_CONTEXT)
    assert "What is ITAR?" in up
    assert "SANCTIONS LIVE CHECK" in up   # compliance verdict survives
    assert "PMESII" not in up             # the exact derailer is gone
    assert "UNDERSTOOD AS" not in up
    assert "DAILY SUBSCRIPTION STATUS" not in up


def test_builder_preserves_full_context_for_frontier():
    # flag off -> nothing stripped (frontier model gets the full pipeline)
    up = ae._format_history_user_prompt([], "", "What is ITAR?", NOISY_CONTEXT)
    assert "PMESII" in up
    assert "UNDERSTOOD AS" in up


def test_builder_truncates_history_for_small_model(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    history = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
    up = ae._format_history_user_prompt(history, "", "current question", "")
    assert "current question" in up
    assert "msg0" not in up   # old history dropped
    assert "msg19" in up      # last exchange kept


def test_builder_keeps_full_history_for_frontier():
    history = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
    up = ae._format_history_user_prompt(history, "", "current question", "")
    # frontier path keeps its (compacted) history including older turns
    assert "msg0" in up


def test_attached_document_preserved_for_small_model(monkeypatch):
    """Attached docs live in `message`, not `context` — must survive."""
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    msg = "[ATTACHED DOCUMENT: nda.pdf]\nConfidentiality clause 4...\nreview this"
    up = ae._format_history_user_prompt([], "", msg, NOISY_CONTEXT)
    assert "ATTACHED DOCUMENT: nda.pdf" in up
    assert "Confidentiality clause 4" in up
