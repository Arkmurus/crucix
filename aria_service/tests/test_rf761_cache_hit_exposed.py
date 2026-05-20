"""R-F761 — chat response now exposes cache_hit boolean.

Audit on 2026-05-20 (P2) flagged that the pay-once-remember-forever
guarantee (R-F655 from 2026-05-17 wires brain_hook absorb on every
chat response) was REAL but UNMEASURABLE — the chat response carried
no signal to the caller / UI / cost dashboard about whether the
answer came from memory vs required a fresh paid LLM call.

R-F761 adds `cache_hit` (boolean) and `cache_signal` (dict with
rag_sources count + rag_context char count) to the aria_chat return
dict. cache_hit is a CONSERVATIVE proxy:

    cache_hit := len(rag_sources) >= 2 AND len(rag_ctx) >= 500

i.e. brain memory contributed >=2 distinct citations AND >=500 chars
of context to the LLM prompt. Less stringent thresholds would
generate false positives (one citation is coincidence; 100 chars
is the model_card-style snippet that ships on every turn).

This test is source-level: verify the keys exist in the return dict
and the threshold values are sane. A full end-to-end test would need
the chat stack live.
"""
from __future__ import annotations

from pathlib import Path


def _engine_src() -> str:
    p = Path(__file__).resolve().parents[1] / "aria_engine.py"
    return p.read_text(encoding="utf-8")


def test_rf761_return_dict_includes_cache_hit():
    """aria_chat return dict must carry cache_hit as a top-level key."""
    src = _engine_src()
    # The aria_chat impl return at line ~3539 must include cache_hit.
    needle = '"cache_hit": _r761_cache_hit'
    assert needle in src, (
        "R-F761 regression: aria_chat return dict missing 'cache_hit' "
        f"key. Expected literal {needle!r} in aria_engine.py."
    )


def test_rf761_return_dict_includes_cache_signal_breakdown():
    """For debuggability (why was cache_hit true/false?), the response
    must also carry the raw counts."""
    src = _engine_src()
    for needle in (
        '"rag_sources":',
        '"rag_context_chars":',
        '"cache_signal":',
    ):
        assert needle in src, (
            f"R-F761 regression: cache_signal breakdown missing "
            f"{needle!r}. Operator can't diagnose pay-once misses "
            "without the raw counts."
        )


def test_rf761_thresholds_are_conservative():
    """Two thresholds must remain in the source so a future edit can't
    accidentally loosen them to false-positive levels (e.g. 1 source
    + 100 chars). The values are tuned conservatively:
      - sources >= 2  (single citation is coincidence)
      - context >= 500 chars (anything shorter is the system-prompt
        boilerplate, not memory hit)
    """
    src = _engine_src()
    assert "len(_r761_rag_sources) >= 2" in src, (
        "R-F761 regression: rag_sources threshold weakened below 2. "
        "Single-citation hits are too noisy to count as 'cache hit'."
    )
    assert "len(_r761_rag_ctx) >= 500" in src, (
        "R-F761 regression: rag_context_chars threshold weakened "
        "below 500. <500 char context is system-prompt boilerplate, "
        "not memory hit."
    )


def test_rf761_reads_from_contextvars():
    """The rag_sources + rag_ctx values must come from contextvars
    (set during _prefetch_rag), not from a fresh re-query. A re-query
    would double the cost — the whole point of pay-once-remember-
    forever is that the LLM call already saw the RAG context."""
    src = _engine_src()
    assert "_rag_sources_var.get([])" in src, (
        "R-F761 regression: cache_hit must read rag_sources from the "
        "contextvars set during _prefetch_rag (line ~2954), not via "
        "a fresh rag_store.search() call."
    )
    assert '_rag_ctx_var.get("")' in src, (
        "R-F761 regression: cache_hit must read rag_ctx from the "
        "contextvars set during _prefetch_rag, not via a fresh query."
    )
