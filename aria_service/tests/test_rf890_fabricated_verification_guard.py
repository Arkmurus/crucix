"""R-F890 — self_claim_guard catches fabricated external-verification claims.

Live 2026-05-25: ARIA answered "who is the current US president?" with
"…verified via the official White House website, which I queried using corrected
search parameters" while the footer showed Tools:(none — from memory/training)
/ Verification:NO_TOOL — a Clause 20(f) fabricated-tool claim. (The root search
bug is R-F888; this guard is defence-in-depth.) BLOCK when no web/search tool
ran; WARN if one did; clean for properly-cited answers.
"""
from __future__ import annotations

from aria_service.intel.self_claim_guard import scan_response

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source

_FAB = ("The current President is Donald Trump. This has been verified via the "
        "official White House website, which I queried using corrected search parameters.")


def _ids(violations):
    return [v for v in violations if v.pattern_id == "rf890_fabricated_verification"]


def test_fabrication_blocks_when_no_web_tool():
    vs = _ids(scan_response(_FAB, web_tool_ran=False))
    assert vs and all(v.severity == "BLOCK" for v in vs)


def test_fabrication_warns_when_web_tool_ran():
    vs = _ids(scan_response(_FAB, web_tool_ran=True))
    assert vs and all(v.severity == "WARN" for v in vs)


def test_properly_cited_answer_is_clean():
    clean = "Donald Trump is the current US president [from https://whitehouse.gov]."
    assert not _ids(scan_response(clean, web_tool_ran=True))
    assert not _ids(scan_response(clean, web_tool_ran=False))


def test_plain_answer_without_verification_claim_is_clean():
    plain = "Based on my training (not verified live this turn), the US president is Donald Trump."
    assert not _ids(scan_response(plain, web_tool_ran=False))


def test_footer_threads_web_tool_ran():
    import inspect
    from aria_service.intel import confidence_footer as cf
    src = function_source(cf, "build_footer")
    assert "_web_tool_ran" in src
    assert "web_tool_ran=_web_tool_ran" in src
