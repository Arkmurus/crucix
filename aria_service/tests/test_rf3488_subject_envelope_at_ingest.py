"""R-F3488 — the erasure capability reached nothing, because nothing carried a key.

R-F3484 gave ARIA exact, provable Art. 17 erasure keyed on `data_subject_key`. It reached
NOTHING: no write path carried one. A capability with no caller is a dormant
specification — the same defect this session criticised in the R-F3474 evidence contract,
and it would have been dishonest to leave it while calling the GDPR work done.

WHAT THIS ADDS. `ingest_document` now carries the envelope — subject key, lawful basis,
retention class — and `/rag/ingest`, the endpoint whose own docstring says it takes
"customer document drops", can declare it.

THE DESIGN DECISION, and why the write is not refused. Personal data stored without a
subject key cannot be erased on request, only swept best-effort. Refusing the write would
enforce a metadata rule by DROPPING a customer's document — trading a data-protection gap
for data loss. So the record is accepted and the deficiency is made VISIBLE: flagged on
the record as `erasure_reachable: false`, logged, and wired to the brain as a gap.

That flag is the point. It lets a controller answer *"what personal data do we hold that
we could not erase if asked?"* from the record itself, instead of discovering it midway
through a request.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import rag_store
from aria_service.intel.rag_store import (
    DATA_SUBJECT_KEY,
    LAWFUL_BASIS_KEY,
    RETENTION_CLASS_KEY,
)

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


@pytest.fixture
def captured(monkeypatch):
    """Capture what ingest_document would write, without a live chromadb."""
    seen: dict = {}

    class _Coll:
        # The production path calls upsert(); add() alone left the capture empty and
        # made three tests fail against correct code.
        def upsert(self, ids=None, documents=None, metadatas=None, embeddings=None, **kw):
            seen["metadatas"] = metadatas

        add = upsert

        def get(self, **kw):
            return {"ids": [], "documents": [], "metadatas": []}

        def count(self):
            return 0

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setattr(rag_store, "_documents_collection", _Coll())
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll())
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll())
    return seen


_TEXT = "Jane Doe was a director of Example Ltd between 2019 and 2024. " * 3


def _ingest(**kw):
    return asyncio.run(rag_store.ingest_document(
        text=_TEXT, source="customer-drop:case-1", **kw))


def test_capability_the_subject_key_is_written_to_the_record(captured):
    """THE FIX: without this, R-F3484's exact erasure matches nothing."""
    _ingest(data_subject_key="subject:jane-doe-1974",
            lawful_basis="legitimate_interests",
            retention_class="dd_evidence_7y",
            personal_data=True)
    metas = captured.get("metadatas") or []
    assert metas, "nothing was written"
    m = metas[0]
    assert m[DATA_SUBJECT_KEY] == "subject:jane-doe-1974"
    assert m[LAWFUL_BASIS_KEY] == "legitimate_interests"
    assert m[RETENTION_CLASS_KEY] == "dd_evidence_7y"
    assert m["erasure_reachable"] is True


def test_personal_data_without_a_key_is_flagged_not_hidden(captured):
    """The honest half. The write is accepted — refusing would drop a customer document
    to enforce a metadata rule — but the record says it is not erasable."""
    _ingest(personal_data=True)
    m = (captured.get("metadatas") or [{}])[0]
    assert m.get("personal_data") is True
    assert m.get("erasure_reachable") is False, (
        "unkeyed personal data was stored with no indication that it cannot be erased")
    assert DATA_SUBJECT_KEY not in m


def test_a_controller_can_find_the_unerasable_records(captured):
    """The flag exists to answer 'what could we NOT erase if asked?' — assert it is a
    real boolean on the record, not merely a log line that scrolls away."""
    _ingest(personal_data=True)
    m = (captured.get("metadatas") or [{}])[0]
    assert "erasure_reachable" in m and isinstance(m["erasure_reachable"], bool)


def test_non_personal_ingests_are_not_burdened(captured):
    """Guard against over-correction: most ingests are public filings and press, not
    personal data. They must not acquire empty compliance fields."""
    _ingest()
    m = (captured.get("metadatas") or [{}])[0]
    for k in (DATA_SUBJECT_KEY, LAWFUL_BASIS_KEY, RETENTION_CLASS_KEY,
              "personal_data", "erasure_reachable"):
        assert k not in m, f"{k} was added to a non-personal ingest"


def test_the_written_key_is_what_erase_by_subject_matches_on():
    """The two halves must agree. If ingest wrote one key name and erasure matched
    another, both would look correct in isolation and erase nothing together — the
    producer/consumer mismatch this codebase keeps finding."""
    import inspect
    src = function_source(rag_store, "erase_by_subject")
    assert "DATA_SUBJECT_KEY" in src, (
        "erase_by_subject no longer matches on the constant ingest writes")


def test_the_endpoint_exposes_the_envelope():
    """The capability has to be reachable by the caller that holds the subject identity;
    a parameter only ingest_document accepts is still dormant."""
    from aria_service.routes.aria import RagIngestRequest
    fields = set(RagIngestRequest.model_fields)
    for f in ("data_subject_key", "lawful_basis", "retention_class", "personal_data"):
        assert f in fields, f"/rag/ingest cannot declare {f}"
    # Optional, so no existing caller breaks.
    assert RagIngestRequest.model_fields["personal_data"].default is False
    assert RagIngestRequest.model_fields["data_subject_key"].default == ""
