"""R-F952 (verifier scores doc reviews) + R-F954 (self-review window noise)."""
from __future__ import annotations

import asyncio


# ── R-F952 — verifier recognises BOTH close-tag dialects as grounded ─────────

def test_rf952_verifier_grounds_web_close_tag():
    from aria_service.intel import source_verifier as sv
    ctx_web = "[ATTACHED DOCUMENT: c.docx]\nClause 1. The parties agree.\n[/ATTACHED DOCUMENT]"
    v = sv.verify_response("Per Clause 1 the parties agree to X.", ctx_web)
    assert v["verdict"] == "grounded", "web-UI doc review must score grounded, not no_citations"


def test_rf952_verifier_grounds_wa_close_tag():
    from aria_service.intel import source_verifier as sv
    ctx_wa = "[ATTACHED DOCUMENT: c.docx]\nClause 1. The parties agree.\n[END ATTACHED DOCUMENT]"
    assert sv.verify_response("Per Clause 1 the parties agree.", ctx_wa)["verdict"] == "grounded"


def test_rf952_no_doc_still_no_tool():
    from aria_service.intel import source_verifier as sv
    assert sv.verify_response("some answer with no sources", "")["verdict"] == "no_tool"


# ── R-F954 — self-review windows must not flag cross-window clauses ──────────

def test_rf954_window_prompt_forbids_cross_window_flagging():
    from aria_service.intel import contract_intelligence as ci
    captured = []

    class _LLM:
        is_configured = True

        async def complete(self, system, user, **kw):
            captured.append(user)

            class _R:
                text = "No issues in this window."
                model = "test"
            return _R()

    asyncio.run(ci.self_review_contract("1. DEFINITIONS\n" + ("body " * 200), "ARIA draft review text", _LLM()))
    assert captured, "a window prompt should have been built"
    p = captured[0]
    # the prompt must instruct NOT to flag clauses absent from this window
    assert "MUST NOT flag it as missing" in p
    assert "memory contamination" in p.lower()
