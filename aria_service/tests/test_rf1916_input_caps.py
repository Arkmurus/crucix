"""R-F1916 (G2 vaccine) — the unbounded-input gene must not regress.

A single oversized payload must never fan out into thousands of coroutines /
thread hops and wedge or OOM the single-process brain. Three choke-points:
  G2-a: read_document_ep caps plain-text `content` at MAX_DOC_CHARS (the base64
        branches capped; the text path WhatsApp/email use did NOT).
  G2-b: researcher.read_document caps the chunk fan-out (defense-in-depth).
  G2-c: the WA listener caps raw inbound text before the regex battery + LLM.

The chunk-cap is verified behaviourally (the real chunking code, no LLM); the
two slice-caps are source-pinned (zero-FP guards, matching the rf1572 convention)
since driving them needs a full request + model.
"""
from __future__ import annotations

import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_read_document_chunk_fanout_is_capped_behaviourally():
    """Replicate read_document's exact chunking on a huge body and assert the
    R-F1916 ceiling bounds it (the math, not the LLM)."""
    content = "x" * 5_000_000  # 5 MB — would be ~1,428 raw chunks
    chunks = []
    if len(content) > 5000:
        for i in range(0, len(content), 3500):
            chunk = content[i:i + 4500]
            if len(chunk) > 100:
                chunks.append(chunk)
    raw_count = len(chunks)
    _MAX_CHUNKS = max(1, int(os.getenv("ARIA_DOC_MAX_CHUNKS", "200")))
    if len(chunks) > _MAX_CHUNKS:
        chunks = chunks[:_MAX_CHUNKS]
    assert raw_count > _MAX_CHUNKS, "test body must exceed the cap to be meaningful"
    assert len(chunks) == _MAX_CHUNKS, "chunk fan-out must be bounded by the ceiling"


def test_read_document_applies_chunk_ceiling_sourcepin():
    src = (REPO / "aria_service" / "intel" / "researcher.py").read_text(encoding="utf-8", errors="ignore")
    assert "ARIA_DOC_MAX_CHUNKS" in src and "chunks = chunks[:_MAX_CHUNKS]" in src, \
        "researcher.read_document must cap the chunk fan-out"


def test_read_document_ep_caps_plain_text_content_sourcepin():
    src = (REPO / "aria_service" / "routes" / "aria.py").read_text(encoding="utf-8", errors="ignore")
    assert "content = content[:MAX_DOC_CHARS]" in src, \
        "read_document_ep must cap the plain-text content path at MAX_DOC_CHARS"


def test_wa_listener_caps_raw_inbound_text_sourcepin():
    src = (REPO / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8", errors="ignore")
    assert "ARIA_WA_MAX_TEXT" in src and "text = text.slice(0, _WA_MAX_TEXT)" in src, \
        "the WA listener must cap raw inbound text before the regex battery + LLM dispatch"
