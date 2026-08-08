"""R-F3383 — corpus ingest had no personal-data gate: PII entered the shared RAG unchecked.

THE GAP. R-F3376 gated WHO may hold a document (rights) and R-F3379 carried that
to retrieval. Neither asked what is IN it. `ingest_corpus_document()` stored the
text verbatim, so an uploaded DD pack, vetting file or email export put live
emails, phone numbers, IBANs, card numbers and API keys into a corpus that is
semantically searchable by every later query — in a system with a documented
history of cross-tenant leaks.

DELEGATES, DOES NOT REINVENT. `intel/pii_redaction.py::redact_pii` is the
canonical pattern set, already proven on the WA notifier and log paths, and
deliberately tuned NOT to shred due-diligence content (it does not use a
proper-name regex, so "John Smith of Acme Ltd" survives — a director's name is
the POINT of a DD document). Detection here is derived from that one function, so
the two can never drift apart.

TWO DIFFERENT RISKS, TWO DIFFERENT ANSWERS:

  - CREDENTIALS are REFUSED outright. There is no legitimate reason for an API
    key or password to enter a retrieval corpus, so there is no override.
  - DIRECT IDENTIFIERS are REDACTED, not refused. Refusing would break the
    product: real DD and vetting documents contain contact details. RAG is a
    reasoning surface, not the system of record — the DD vault holds the actual
    report — so replacing an IBAN with [IBAN] keeps the document's meaning and
    removes the leak vector.

AND THE COUNTS CARRY. `pii_redacted` goes into chunk metadata, so what was
removed is auditable later. A control whose effect leaves no trace cannot be
verified after the fact.

NI NUMBERS — a real gap in the canonical set for THIS product. A UK National
Insurance number is exactly what BS 7858 screening handles, and `redact_pii` did
not catch it. Fixed at the canonical module so every consumer benefits (logs, WA,
chat capture), not patched locally here.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import corpus_ingest as CI
from aria_service.intel import pii_redaction as PR

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


def _ingest(text, **kw):
    defaults = dict(filename="doc.pdf", tier="A", source_class="Test", rights="owned")
    defaults.update(kw)
    with patch("aria_service.intel.rag_store.ingest_document",
               new=AsyncMock(return_value={"chunks": 1})) as m:
        result = _run(CI.ingest_corpus_document(text, **defaults))
    return result, m


CLEAN = "Acme Ltd is a UK defence supplier. Director John Smith joined in 2019. " * 4


# ── the canonical detector gains NI numbers ────────────────────────────────

def test_ni_number_is_now_redacted_at_the_canonical_module():
    """BS 7858 vetting handles NI numbers; the shared redactor missed them."""
    out = PR.redact_pii("Applicant NI number QQ123456C on file")
    assert "QQ123456C" not in out
    assert "[ID_NUMBER]" in out or "[NI]" in out


def test_ni_pattern_does_not_shred_ordinary_text():
    """Must not fire on company numbers, R-numbers, SHAs or ordinary prose."""
    for safe in ("company number 00445790", "R-F3383 shipped", "commit a1b2c3d4e5",
                 "BS 7858:2019 clause 4", "Acme Holdings Limited"):
        assert PR.redact_pii(safe) == safe, safe


# ── detection delegates to the canonical set ──────────────────────────────

def test_detect_pii_delegates_and_types_what_it_finds():
    found = CI.detect_pii("mail me at a.b@acme.co.uk or +44 7700 900123")
    assert found.get("EMAIL") == 1
    assert found.get("PHONE") == 1


def test_detect_pii_is_empty_on_clean_dd_prose():
    assert CI.detect_pii(CLEAN) == {}


def test_detect_pii_keeps_director_names():
    """A director's name is the POINT of a DD document — it must not count as
    PII to be stripped, or the corpus becomes useless."""
    assert CI.detect_pii("Director John Smith of Acme Ltd") == {}


def test_detect_pii_is_total_on_junk():
    for junk in (None, "", 123, []):
        assert CI.detect_pii(junk) == {}


def test_detection_uses_the_canonical_redactor():
    import inspect
    src = function_source(CI, "detect_pii")
    assert "redact_pii" in src, (
        "a second pattern set would drift from pii_redaction — delegate to it"
    )


# ── credentials: refused outright ─────────────────────────────────────────

def test_document_containing_a_secret_is_refused():
    with pytest.raises(ValueError) as e:
        _ingest(CLEAN + " api_key=sk-live-abc123def456ghi789")
    assert "credential" in str(e.value).lower() or "secret" in str(e.value).lower()


def test_secret_refusal_happens_before_any_write():
    with patch("aria_service.intel.rag_store.ingest_document", new=AsyncMock()) as m:
        with pytest.raises(ValueError):
            _run(CI.ingest_corpus_document(
                CLEAN + " api_key=sk-live-abc123def456ghi789",
                filename="f.pdf", tier="A", source_class="X", rights="owned"))
    assert not m.called, "a document containing a credential reached the RAG store"


# ── direct identifiers: redacted, not refused ─────────────────────────────

def test_identifiers_are_redacted_and_the_document_still_ingests():
    body = CLEAN + " Contact j.smith@acme.co.uk or +44 7700 900123."
    result, m = _ingest(body)
    stored = m.call_args.kwargs["text"]
    assert "j.smith@acme.co.uk" not in stored
    assert "+44 7700 900123" not in stored
    assert "[EMAIL]" in stored and "[PHONE]" in stored
    assert "Acme Ltd is a UK defence supplier" in stored, "meaning was destroyed"
    assert result["pii_redacted"]["EMAIL"] == 1


def test_iban_and_card_are_redacted():
    body = CLEAN + " IBAN GB29NWBK60161331926819 card 4111111111111111"
    _result, m = _ingest(body)
    stored = m.call_args.kwargs["text"]
    assert "GB29NWBK60161331926819" not in stored
    assert "4111111111111111" not in stored


def test_clean_document_is_stored_byte_identical():
    """No gratuitous rewriting of documents that contain no PII."""
    _result, m = _ingest(CLEAN)
    assert m.call_args.kwargs["text"] == CLEAN


# ── the counts must CARRY, or the control leaves no trace ────────────────

def test_redaction_counts_reach_chunk_metadata():
    _result, m = _ingest(CLEAN + " a.b@acme.co.uk")
    meta = m.call_args.kwargs["extra_metadata"]
    assert meta.get("pii_redacted"), "what was removed is not auditable later"
    assert "EMAIL" in str(meta["pii_redacted"])


def test_clean_document_records_no_pii_marker():
    _result, m = _ingest(CLEAN)
    assert not m.call_args.kwargs["extra_metadata"].get("pii_redacted")


# ── the rights gate still works alongside it ─────────────────────────────

def test_rights_gate_is_unaffected():
    with pytest.raises(ValueError):
        _ingest(CLEAN, rights="")
    with pytest.raises(ValueError):
        _ingest(CLEAN, rights="third_party_copyright")


def test_pii_check_runs_even_when_rights_are_fine():
    with pytest.raises(ValueError):
        _ingest(CLEAN + " password=hunter2supersecretvalue", rights="public_domain")
