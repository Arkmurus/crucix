"""R-F518 — reasoning_library entity-overlap gate.

Live failure 2026-05-14 ~11:00 BST: probed /chat with the fresh-entity
question "Is Bumblestaff Industries sanctioned?" — reasoning_library
matched semantically at confidence 0.912 against a cached answer about
Hikvision and returned the Hikvision answer verbatim. Sample trace:

  reasoning_trace = [
    {"stage": "symbolic_reasoner", "matched": False, ...},
    {"stage": "reasoning_library", "matched": True,
     "confidence": 0.912, "method": "semantic"}
  ]
  response = "UNDERSTOOD AS: You are asking whether Hikvision is on
              the UK financial sanctions list..."

The Bumblestaff query has NOTHING to do with Hikvision. Question-shape
similarity ("Is X sanctioned in Y?") dominated the embedding space, so
every sanctions-shaped question was being served the same cached
Hikvision answer regardless of which entity was asked about. Severe
honesty bug: wrong-entity factual answers shipping live to /chat.

R-F518 adds an entity-overlap gate: before accepting a non-exact
match (semantic / lexical_jaccard), require ≥1 shared content token
after stripping shared sanctions/jurisdiction jargon. The remaining
tokens are overwhelmingly entity names. Exact-normalised matches are
unaffected because they already pin the entity by full-text equality.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import reasoning_library


# ───────────── R-F518 — _entity_tokens unit tests ─────────────


@pytest.mark.parametrize(
    "normalised,expected_entities",
    [
        # Real-world repro pair — normalised forms after _normalise_question
        ("hikvision sanctioned uk", {"hikvision"}),
        ("bumblestaff industries sanctioned", {"bumblestaff", "industries"}),
        # OFSI / OFAC / EU stripped (regulator names are jargon, not entities)
        ("ofsi check acme", {"acme"}),
        ("ofac sdn putin", {"putin"}),
        # Pure-jargon question (only sanctions/jurisdiction vocabulary) — empty set
        ("sanctions designated listed", set()),
        # Multi-token entity preserved
        ("zephyr mining trust bahamas screen", {"zephyr", "mining", "trust", "bahamas"}),
        # Empty input handled
        ("", set()),
    ],
)
def test_rf518_entity_tokens_strips_jargon(normalised, expected_entities):
    """_entity_tokens must return entity-bearing tokens only — the
    sanctions/jurisdiction/compliance jargon should be filtered."""
    assert reasoning_library._entity_tokens(normalised) == expected_entities


# ───────────── R-F518 — find_match guard fires on entity mismatch ──────────


@pytest.fixture
def mock_index_with_hikvision(monkeypatch):
    """Seed a reasoning_library index with the cached Hikvision case
    that caused the live failure. Patch _load_index, _embed_async,
    rs.get_json so find_match runs without hitting Redis."""

    # Cached entry — what the live system had
    hikvision_entry = {
        "id": "case_hikvision_uk_sanctions",
        "normalised": "hikvision sanctioned uk",  # post-normalisation
        "intent": "chat",
        "confidence_score": 0.95,
        "ts_created": 1700000000.0,
        "ts_last_used": 9999999999.0,  # very recent
        # Embedding similar to "Is X sanctioned in the UK?" shape.
        # Using a 384-dim unit-vector stand-in (mock model output).
        "embedding": [0.1] * 384,
    }
    fake_index = [hikvision_entry]

    async def _fake_load_index():
        return fake_index

    async def _fake_embed(q):
        # Return a similar but not identical embedding so cosine is high
        # (mimicking the live 0.912 confidence). Slight perturbation.
        return [0.1] * 384

    async def _fake_get_json(key):
        return {
            "id": "case_hikvision_uk_sanctions",
            "question": "Is Hikvision sanctioned in the UK?",
            "response": "UNDERSTOOD AS: ... Hikvision is NOT on OFSI ...",
            "intent": "chat",
            "confidence_score": 0.95,
            "ts_created": 1700000000.0,
            "ts_last_used": 9999999999.0,
            "access_count": 17,
        }

    async def _fake_load_meta():
        return {"total_lookups": 0, "total_cases": 1}

    async def _fake_save_meta():
        pass

    monkeypatch.setattr(reasoning_library, "_load_index", _fake_load_index)
    monkeypatch.setattr(reasoning_library, "_embed_async", _fake_embed)
    monkeypatch.setattr(reasoning_library.rs, "get_json", _fake_get_json)
    monkeypatch.setattr(reasoning_library, "_load_meta", _fake_load_meta)
    monkeypatch.setattr(reasoning_library, "_save_meta", _fake_save_meta)


def test_rf518_live_failure_repro_no_longer_matches(mock_index_with_hikvision):
    """Direct repro of the 2026-05-14 11:00 BST live failure. The
    Bumblestaff query must NOT match the cached Hikvision case."""
    out = asyncio.run(
        reasoning_library.find_match("Is Bumblestaff Industries sanctioned?")
    )
    assert out["match"] is False, (
        f"R-F518 entity-gate failed: cached Hikvision answer was served "
        f"for fresh Bumblestaff query. Score={out.get('score')}, "
        f"method={out.get('method')}."
    )


def test_rf518_nantong_query_does_not_match_hikvision(mock_index_with_hikvision):
    """Real-traffic shape: Asian-electronics-company-style entity name
    that semantic embeddings cluster near 'Hikvision' but is a distinct
    company. Must yield to LLM."""
    out = asyncio.run(
        reasoning_library.find_match(
            "Is Nantong Manguang Optical sanctioned in the UK?"
        )
    )
    assert out["match"] is False


# ───────────── R-F518 — does NOT break legitimate matches ──────────


def test_rf518_same_entity_still_matches(mock_index_with_hikvision):
    """Genuinely about Hikvision: must still hit the cached answer.
    'Hikvision' overlaps so the gate passes."""
    out = asyncio.run(
        reasoning_library.find_match("Is Hikvision designated under UK sanctions?")
    )
    # Score must be above LIBRARY_MIN_CONFIDENCE (0.78). Confirms semantic
    # path still works once the entity gate passes.
    assert out["match"] is True, (
        f"Hikvision-vs-Hikvision query MUST still hit cache "
        f"(score={out.get('score')}, method={out.get('method')})."
    )


def test_rf518_exact_normalised_match_unaffected(monkeypatch):
    """The exact_normalised path doesn't depend on entity tokens because
    full-text equality already pins the entity. Same string => match."""

    # Need ≥3 salient tokens to clear the existing MIN_SALIENT_TOKENS=3
    # guard at find_match line 681 (unrelated to R-F518).
    fake_index = [{
        "id": "case_exact",
        # 3 tokens (clears MIN_SALIENT_TOKENS) and identical post-
        # normalisation to the query below.
        "normalised": "acme corp sanctioned",
        "intent": "chat",
        "confidence_score": 0.9,
        "ts_created": 1700000000.0,
        "ts_last_used": 9999999999.0,
        "embedding": None,
    }]

    async def _li():
        return fake_index

    async def _e(_):
        return None

    async def _g(_):
        return {
            "id": "case_exact",
            "question": "Is Acme sanctioned?",
            "response": "ACME response ...",
            "intent": "chat",
            "confidence_score": 0.9,
            "ts_created": 1700000000.0,
            "ts_last_used": 9999999999.0,
            "access_count": 5,
        }

    async def _lm():
        return {"total_lookups": 0}

    async def _sm():
        pass

    monkeypatch.setattr(reasoning_library, "_load_index", _li)
    monkeypatch.setattr(reasoning_library, "_embed_async", _e)
    monkeypatch.setattr(reasoning_library.rs, "get_json", _g)
    monkeypatch.setattr(reasoning_library, "_load_meta", _lm)
    monkeypatch.setattr(reasoning_library, "_save_meta", _sm)

    # _normalise_question("Is Acme Corp sanctioned?") -> "acme corp sanctioned"
    # which exactly matches the seeded entry.
    out = asyncio.run(reasoning_library.find_match("Is Acme Corp sanctioned?"))
    assert out["match"] is True
    assert out["method"] == "exact_normalised"
