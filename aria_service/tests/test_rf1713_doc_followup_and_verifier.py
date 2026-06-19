"""R-F1713 — two chat bugs surfaced on the NDA review:

Bug 1: a follow-up after a document review ("what are your recommendations, so no
       red flags?") misrouted to a generic web search. Guard: suppress web-class
       intent for a recent-doc-review follow-up so the answer comes from history.
Bug 2: the R-F448 verifier re-printed the ENTIRE answer ("Revised response:") for
       tag-only corrections, so the user read it twice.
"""
from aria_service.intel.stream_honesty import build_correction_banner
from aria_service.routes.aria import (
    _is_recent_doc_review_rf1713,
    _WEB_CLASS_TOOLS_RF1713,
)


# ── Bug 2 — verifier no longer double-prints for tag-only corrections ──
def test_tag_only_change_is_concise_not_a_full_reprint():
    res = {"changed": True, "changes": [("response_verifier", 2)],
           "rewritten": "THE WHOLE LONG NDA ANALYSIS " * 50}
    banner = build_correction_banner(res)
    assert banner is not None
    assert "Revised response:" not in banner          # no full re-print
    assert "THE WHOLE LONG NDA ANALYSIS" not in banner  # answer NOT duplicated
    assert "PENDING CORROBORATION" in banner           # concise note instead


def test_substantive_rewrite_still_reprints_the_corrected_answer():
    res = {"changed": True, "changes": [("commitment_guard", 1)],
           "rewritten": "CORRECTED ANSWER BODY"}
    banner = build_correction_banner(res)
    assert "Revised response:" in banner
    assert "CORRECTED ANSWER BODY" in banner


def test_no_change_no_banner():
    assert build_correction_banner({"changed": False}) is None
    assert build_correction_banner({"changed": True, "changes": [], "rewritten": "x"}) is None


# ── Bug 1 — doc-review follow-up recency guard ──
def test_recent_doc_review_is_within_window():
    assert _is_recent_doc_review_rf1713(1000.0, now=1000.0 + 600) is True   # 10 min later


def test_old_doc_review_is_outside_window():
    assert _is_recent_doc_review_rf1713(1000.0, now=1000.0 + 3600) is False  # 60 min later


def test_no_doc_review_flag_is_false():
    assert _is_recent_doc_review_rf1713(None) is False
    assert _is_recent_doc_review_rf1713(0) is False
    assert _is_recent_doc_review_rf1713("bad") is False


def test_only_web_class_tools_are_guarded():
    # The generic web tools that misfired are guarded...
    assert "brave_answer" in _WEB_CLASS_TOOLS_RF1713
    assert "web_search" in _WEB_CLASS_TOOLS_RF1713
    # ...but explicit/entity tools still fire after a doc review.
    assert "screen" not in _WEB_CLASS_TOOLS_RF1713
    assert "dd_orchestrate" not in _WEB_CLASS_TOOLS_RF1713
    assert "registry_lookup" not in _WEB_CLASS_TOOLS_RF1713
