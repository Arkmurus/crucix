"""R-F1826 — H7: document-extraction by-id ownership.

Authorization review H7 (MED-HIGH): document_extraction_get_ep returned any uploaded
document's extracted content (PII/contracts) by id — the record stored no owner.

Fix: save_extraction stores user_id (threaded request → process_document → save);
get_extraction enforces ownership when user_id is passed; the endpoint takes user_id
(Node pins it from the JWT). Read-side ownership is sound via the Node pin (like H3).

Capability test drives the REAL helpers/endpoint: cross-user → None/404, owner → rec,
admin (None) → rec.
"""
import pytest
from fastapi import HTTPException


@pytest.fixture
def seeded(monkeypatch):
    from aria_service.intel import document_corrections as dc
    store = {"extractions": {"x1": {"id": "x1", "user_id": "alice", "filename": "ndA.pdf"}}, "by_form": {}}

    async def _load():
        return store
    monkeypatch.setattr(dc, "_load", _load)
    return dc


@pytest.mark.asyncio
async def test_get_extraction_ownership(seeded):
    assert (await seeded.get_extraction("x1", user_id="alice"))["id"] == "x1"
    assert await seeded.get_extraction("x1", user_id="bob") is None      # cross-user blocked
    assert (await seeded.get_extraction("x1", user_id=None))["id"] == "x1"  # admin/no-filter


@pytest.mark.asyncio
async def test_extraction_endpoint_blocks_cross_user(seeded):
    from aria_service.routes import aria as A
    with pytest.raises(HTTPException) as e:
        await A.document_extraction_get_ep(extraction_id="x1", user_id="bob")
    assert e.value.status_code == 404
    out = await A.document_extraction_get_ep(extraction_id="x1", user_id="alice")
    assert out["id"] == "x1"
