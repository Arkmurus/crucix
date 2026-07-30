"""R-F410 — RAG corpus sanitization on every ingest path.

Pre-R-F410: email/webhook content landed in chromadb without HTML
strip, control-char filter, or UTF-8 validation. Adversarial fact-
teaching surface — an attacker could send an email with raw HTML or
zero-width characters that ARIA would later return verbatim in chat.

Sanitization performed (in order):
  1. UTF-8 validation (invalid sequences → U+FFFD)
  2. Hard length cap (1 MB → truncated with marker)
  3. HTML strip via bs4 (script/style/noscript decomposed first;
     regex fallback if bs4 missing)
  4. Control-char strip (preserves \\n \\r \\t)
  5. Zero-width / direction-override char strip (ZWSP/ZWNJ/BOM/LRM/RLM/…)
  6. Whitespace collapse

Applied at:
  - ingest_document() — primary entry, all crawl + ocr + manual paths
  - ingest_fact()      — knowledge-store sync per fact
  - add_facts_batch()  — batched variant per-fact
"""
from __future__ import annotations


# ───────────────────────── _sanitize_ingest_text — unit ────────────────────


def test_rf410_sanitize_strips_html_tags():
    """Raw HTML tags must be removed before chunking — chromadb shouldn't
    learn `<script>alert(1)</script>` as a fact."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    text = "<p>Hello <b>world</b></p><script>alert('xss')</script> Visit me"
    out, meta = _sanitize_ingest_text(text)
    assert "<p>" not in out
    assert "<script>" not in out
    assert "alert" not in out  # script body decomposed entirely
    assert "Hello" in out and "world" in out
    assert meta["html_stripped"] > 0


def test_rf410_sanitize_drops_control_chars():
    """C0/C1 control chars (except \\n \\r \\t) are dropped."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    text = "Hello\x00\x01\x02world\nLine 2\tTabbed"  # NUL/SOH/STX dropped
    out, meta = _sanitize_ingest_text(text)
    assert "\x00" not in out
    assert "\x01" not in out
    assert "Hello" in out
    assert "world" in out
    assert "\n" in out  # newline preserved
    assert "\t" in out  # tab preserved
    assert meta["control_chars"] >= 3


def test_rf410_sanitize_drops_zero_width_chars():
    """ZWSP / ZWNJ / BOM / LRM / RLM must be dropped — they hide
    injected text from human review but get parsed as text by LLMs."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    # ZWSP (U+200B) between every char of "evil"
    text = "Pay​​​load"  # "Payload" hidden via ZWSP
    out, meta = _sanitize_ingest_text(text)
    assert "​" not in out
    assert "Payload" in out
    assert meta["zero_width"] >= 3


def test_rf410_sanitize_invalid_utf8_replaced():
    """Invalid UTF-8 byte sequences → U+FFFD replacement char, not crash."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    bad_bytes = b"Hello \xff\xfe world"  # invalid UTF-8
    out, meta = _sanitize_ingest_text(bad_bytes)
    assert "Hello" in out
    assert "world" in out
    assert meta["invalid_utf8_replaced"] is True


def test_rf410_sanitize_length_cap_truncates():
    """Content > 1 MB is truncated with marker — defends against OOM
    attacks via giant payloads."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    big = "A" * 1_200_000  # 1.2 MB
    out, meta = _sanitize_ingest_text(big)
    assert meta["truncated_bytes"] >= 100_000
    assert "[!RAG_INGEST_TRUNCATED]" in out
    assert len(out.encode("utf-8")) <= 1_048_576 + 80  # cap + marker


def test_rf410_sanitize_empty_input_safe():
    """Empty / None / falsy input must not crash."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    for inp in ("", None, "   ", "\n\n"):
        out, meta = _sanitize_ingest_text(inp)
        assert isinstance(out, str)
        assert isinstance(meta, dict)


def test_rf410_sanitize_collapses_whitespace():
    """After HTML strip, runs of whitespace are collapsed."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    text = "Hello       world   \n\n\n\n\n with     gaps"
    out, _ = _sanitize_ingest_text(text)
    assert "       " not in out  # 7-space run collapsed
    assert "\n\n\n" not in out   # 3+ newlines collapsed


def test_rf410_sanitize_preserves_legitimate_text():
    """Standard prose must pass through unchanged — sanitization is
    surgical, not destructive."""
    from aria_service.intel.rag_store import _sanitize_ingest_text
    text = "Joe Bloggs is CEO of Acme Defence Ltd. Founded in 2015."
    out, meta = _sanitize_ingest_text(text)
    assert out == text
    assert meta["html_stripped"] == 0
    assert meta["control_chars"] == 0
    assert meta["zero_width"] == 0
    assert meta["invalid_utf8_replaced"] is False
    assert meta["truncated_bytes"] == 0


# ───────────────────────── Capability — ingest paths use the sanitizer ─────


def test_rf410_capability_pure_html_rejected_as_too_short(monkeypatch):
    """When raw HTML strips to <20 chars, ingest_document must reject
    it as too_short and surface the sanitization metadata so the
    operator can see what happened."""
    import asyncio
    from aria_service.intel import rag_store

    # Bypass chromadb init — we only need the sanitizer + length check
    async def _fake_ensure():
        return True

    # R-F3449 — a bare assignment left rag_store._ensure_async stubbed for the REST OF THE
    # SESSION, so every later RAG call skipped its real chromadb init. Latent in the R-F3448
    # baseline only because nothing after this file depended on that init actually running.
    monkeypatch.setattr(rag_store, "_ensure_async", _fake_ensure)

    text = "<p>Hi</p><b>x</b>"  # strips to ~3 chars
    out = asyncio.run(rag_store.ingest_document(text, source="email:test"))
    assert out["ingested"] is False
    assert out["reason"] == "text_too_short"
    # Sanitization metadata MUST be exposed on the rejection so the
    # operator can tell "this was HTML-stripped to nothing" from
    # "this was always empty".
    assert "sanitization" in out
    assert out["sanitization"]["html_stripped"] > 0
