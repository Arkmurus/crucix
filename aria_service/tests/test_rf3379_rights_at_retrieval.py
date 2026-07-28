"""R-F3379 — enforce rights AT RETRIEVAL. `may_quote_verbatim` had no caller.

THE GAP. R-F3376 put a rights gate on ingest and wrote `rights` into chunk
metadata, and shipped `may_quote_verbatim()` for consumers to honour. Nothing
called it. Worse, it could not have worked even if something had:

    rag_store.search() builds each result from a SELECTED field list —
    source, source_type, title, url, market, ingested_at, credibility_tier —
    and DROPS `rights` entirely.

So the marking was written at ingest, stored in chroma, and thrown away at the
retrieval boundary. That is the producer→consumer-no-carrier defect this repo
keeps rediscovering, and it made the ingest gate half a control: it could stop
new copyrighted material arriving, but nothing stopped licensed or
unknown-provenance text already in the store being quoted verbatim into a
customer deliverable.

WHERE THE FIX BELONGS. `_format_rag_context()` is the SINGLE shared renderer for
both `get_rag_context()` and `get_rag_context_with_sources()`, and the latter is
what the chat path injects into the LLM prompt (aria_engine.py:3878, :4805).
One function, both callers — so the gate goes there, not in each consumer.

WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT. Non-quotable chunks are still
RETRIEVED and still inform the answer — removing them would destroy the value of
licensed material we legitimately hold. They are MARKED, so the model summarises
instead of reproducing. That mirrors how this renderer already handles staleness
(⚠ STALE markers) rather than dropping aged chunks.

LEGACY CHUNKS. Everything ingested before R-F3376 has no `rights` key at all.
Fail-closed means those are marked too — we genuinely do not know their
provenance, which is exactly the condition the gate exists for. That is a real
behaviour change on every existing chunk, so it is measurable
(`rights_gate_stats`) and reversible (`ARIA_RAG_RIGHTS_GATE=0`).
"""
from __future__ import annotations

import pytest

from aria_service.intel import rag_store as RS
from aria_service.intel import corpus_ingest as CI


def _chunk(text="body text here", rights=None, score=0.9):
    r = {"text": text, "score": score, "title": "T", "source": "corpus:A:owned:f.pdf",
         "ingested_at": "", "url": ""}
    if rights is not None:
        r["rights"] = rights
    return r


# ── the carrier: search() must not drop `rights` ───────────────────────────

def test_search_result_shape_carries_rights():
    """Guard against the exact regression: a selected-field list that silently
    omits the marking makes every downstream check impossible."""
    import inspect
    src = inspect.getsource(RS.search)
    assert '"rights"' in src, "search() drops `rights` — the gate cannot fire downstream"


# ── the renderer honours it ────────────────────────────────────────────────

def _body_lines(out: str) -> list[str]:
    """Chunk lines only. The header legitimately EXPLAINS the marker, so asserting
    over the whole blob would fail on a correctly-unmarked chunk."""
    return [l for l in out.splitlines() if l.strip().startswith("•")]


def test_quotable_chunk_is_not_marked():
    out = RS._format_rag_context([_chunk(rights="owned")], 4000)
    assert "body text here" in out
    assert all("DO NOT QUOTE" not in l.upper() for l in _body_lines(out))


@pytest.mark.parametrize("rights", ["licensed"])
def test_licensed_chunk_is_marked_do_not_quote(rights):
    out = RS._format_rag_context([_chunk(rights=rights)], 4000)
    assert "body text here" in out, "content was removed rather than marked"
    assert "DO NOT QUOTE" in out.upper()


def test_legacy_chunk_without_rights_is_marked():
    """Pre-R-F3376 chunks have no rights key. Unknown provenance is exactly the
    condition the gate exists for, so it is marked, not waved through."""
    out = RS._format_rag_context([_chunk()], 4000)
    assert "DO NOT QUOTE" in out.upper()


def test_marker_says_why_not_just_that():
    out = RS._format_rag_context([_chunk(rights="licensed")], 4000)
    assert "licensed" in out.lower()
    out2 = RS._format_rag_context([_chunk()], 4000)
    assert "unrecorded" in out2.lower() or "unknown" in out2.lower()


def test_mixed_results_are_marked_individually():
    out = RS._format_rag_context(
        [_chunk(text="quotable one", rights="public_domain"),
         _chunk(text="restricted one", rights="licensed")], 4000)
    lines = [l for l in out.split("\n") if "one" in l]
    quotable = [l for l in lines if "quotable one" in l][0]
    restricted = [l for l in lines if "restricted one" in l][0]
    assert "DO NOT QUOTE" not in quotable.upper()
    assert "DO NOT QUOTE" in restricted.upper()


def test_header_instructs_the_model_about_the_marker():
    """A marker the prompt never explains is a marker the model ignores."""
    out = RS._format_rag_context([_chunk(rights="licensed")], 4000)
    header = out.split("\n•")[0]
    assert "quote" in header.lower()


# ── it uses the canonical predicate, not a re-implementation ──────────────

def test_gate_delegates_to_may_quote_verbatim():
    import inspect
    # the rule lives in _rights_marker, which the renderer calls — assert on the
    # unit that decides, not the one that formats
    src = inspect.getsource(RS._rights_marker) + inspect.getsource(RS._format_rag_context)
    assert "may_quote_verbatim" in src, (
        "the renderer re-implements the rights rule instead of calling the "
        "canonical predicate — two measures of one thing is how they drift"
    )


@pytest.mark.parametrize("rights,quotable", [
    ("owned", True), ("public_domain", True), ("open_licence", True),
    ("derived_facts", True), ("licensed", False), ("", False), (None, False),
])
def test_predicate_agreement(rights, quotable):
    meta = {} if rights is None else {"rights": rights}
    assert CI.may_quote_verbatim(meta) is quotable


# ── measurable and reversible ─────────────────────────────────────────────

def test_stats_report_the_migration_surface():
    stats = RS.rights_gate_stats([
        _chunk(rights="owned"), _chunk(rights="licensed"), _chunk(),
    ])
    assert stats["total"] == 3
    assert stats["quotable"] == 1
    assert stats["marked"] == 2
    assert stats["unrecorded"] == 1


def test_gate_can_be_disabled_for_rollback(monkeypatch):
    monkeypatch.setenv("ARIA_RAG_RIGHTS_GATE", "0")
    out = RS._format_rag_context([_chunk(rights="licensed")], 4000)
    assert "DO NOT QUOTE" not in out.upper()


def test_gate_is_on_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_RAG_RIGHTS_GATE", raising=False)
    out = RS._format_rag_context([_chunk(rights="licensed")], 4000)
    assert "DO NOT QUOTE" in out.upper()


# ── no regression on the renderer's existing behaviour ───────────────────

def test_staleness_marker_still_works():
    out = RS._format_rag_context(
        [_chunk(rights="owned") | {"ingested_at": "2020-01-01T00:00:00+00:00"}], 4000)
    assert "STALE" in out.upper()


def test_max_chars_is_still_respected():
    out = RS._format_rag_context([_chunk(text="x" * 500, rights="owned") for _ in range(20)], 300)
    assert len(out) < 1200
