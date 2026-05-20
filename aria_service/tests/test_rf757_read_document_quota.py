"""R-F757 — per-uploader quota gate on /read-document.

Audit on 2026-05-20 (P2) flagged that /read-document had no per-user
throttle. The path has R-F725 (45s hard cap per call) and
MAX_DOC_CHARS (500k chars per upload) but nothing stopping the same
caller from firing 100 back-to-back 45s uploads — 45 minutes of
event-loop saturation that the chat path's H3 quota would have
blocked.

R-F757 wires the same user_quota module the chat path uses
(CLAUDE.md §H3) into the upload entrypoint, identity derived from
the `source` body field. The result: an email reader that spams
PDFs at the seenode mailbox is rate-limited per sender, not as a
single "seenode" identity.

Source-level test verifying:
  1. _read_document_ep_impl imports user_quota
  2. await user_quota.check(...) runs before any document parsing
  3. on `not allowed`, HTTPException(429) is raised
  4. on quota-module crash, fails OPEN with _log.error (so an aria
     bug doesn't lose ingestion silently)
  5. the source identity is length-capped (key bloat protection)
"""
from __future__ import annotations

from pathlib import Path


def _aria_source() -> str:
    p = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    return p.read_text(encoding="utf-8")


def test_rf757_imports_user_quota():
    src = _aria_source()
    assert "from ..intel import user_quota as _r757_uq" in src, (
        "R-F757 regression: /read-document path no longer imports "
        "user_quota. Chat-path DoS guardrail was meant to be reused here."
    )


def test_rf757_check_runs_before_parsing():
    src = _aria_source()
    # Find the indices of (a) the quota check and (b) the base64
    # parsing branch. The check must come first.
    quota_idx = src.find("_r757_uq.check(_r757_who)")
    base64_idx = src.find('if encoding == "base64" and content:')
    assert quota_idx > 0, "R-F757: quota check call not found in aria.py"
    assert base64_idx > 0, "R-F757: base64 parsing branch not found"
    assert quota_idx < base64_idx, (
        "R-F757 regression: quota check must execute BEFORE the body "
        "parsing branches (base64/utf8/etc). Putting it after means a "
        "rejected request still pays the parse cost — defeats the "
        "DoS protection."
    )


def test_rf757_raises_429_on_reject():
    src = _aria_source()
    assert "raise HTTPException(status_code=429, detail=_r757_reason)" in src, (
        "R-F757 regression: must raise HTTPException(429) — same status "
        "as the chat-path quota rejection — when quota check fails."
    )


def test_rf757_fails_open_on_quota_module_error():
    """If user_quota itself crashes, /read-document should NOT lose
    ingestion. Fail open with an ERROR log so the bug surfaces."""
    src = _aria_source()
    # Match across the line break in the source.
    expected_open = "FAILING OPEN"
    assert expected_open in src, (
        "R-F757 regression: quota crash path must log FAILING OPEN "
        "so the operator knows ingestion is unguarded."
    )
    assert "_log.error(" in src, (
        "R-F757: failure path must use _log.error (not debug/warning) "
        "so the unguarded ingestion is visible in fly logs."
    )


def test_rf757_identity_length_capped():
    src = _aria_source()
    # The source field is operator-typed and may be unbounded; we cap
    # to 80 chars to keep redis keys / in-memory dict bounded.
    assert "_r757_who[:80]" in src, (
        "R-F757 regression: source identity must be length-capped (we "
        "use [:80]) before being used as a quota key. Otherwise an "
        "attacker can flood the user_quota keyspace with full email "
        "headers as identities."
    )


def test_rf757_register_request_called_on_success():
    """The chat path calls await user_quota.register_request(user)
    on the allowed path so the sliding window actually advances. The
    upload path must do the same — otherwise the quota counter
    sees zero traffic and never trips."""
    src = _aria_source()
    assert "_r757_uq.register_request(_r757_who)" in src, (
        "R-F757 regression: register_request not called on the allowed "
        "path. The sliding window won't advance and the cap is moot."
    )
