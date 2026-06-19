"""R-F739 (2026-05-20) — conversation-history load wiring.

Bug: clicking a conversation in the sidebar showed "Failed to load
conversation." for every user. Root cause: the frontend's `load(sid)`
function fetched `/api/aria/conversations/{sid}/detail` WITHOUT a
`user_id` query param, but the backend endpoint (R-F606, 2026-05-16)
requires `user_id` for the ownership check and returns HTTP 400
otherwise.

R-F739:
  1. Frontend now passes `?user_id=<USER_ID_SLUG>` on every /detail
     fetch — matching the list/delete/rename endpoints.
  2. Both convos-refresh + load-conversation catch blocks now surface
     the real error message (with HTTP status) so operators can debug
     instead of seeing the generic "Failed to load." string.

These tests catch the regression: removing `user_id` from the detail
call, or reverting the error-message surfacing, would fail here.
"""
from __future__ import annotations

import re
from pathlib import Path

ARIA_HTML = Path(__file__).resolve().parents[2] / "public" / "aria.html"
REPORTS_ROUTES = Path(__file__).resolve().parents[2] / "lib" / "reports" / "routes.mjs"


def _read_html() -> str:
    return ARIA_HTML.read_text(encoding="utf-8")


def _read_reports_routes() -> str:
    return REPORTS_ROUTES.read_text(encoding="utf-8")


def test_load_detail_passes_user_id():
    """R-F739: frontend MUST include user_id when fetching conversation
    detail, or R-F606 ownership check returns HTTP 400."""
    src = _read_html()
    # Locate the load(sid) function block. R-F1717: widened 800→3000 chars — the
    # R-F1691 loading skeleton added ~400 chars at the top of load(), pushing the
    # (still-present, correct) /detail?user_id= line past the old 800-char window.
    m = re.search(r"async function load\(sid\)[\s\S]{0,3000}", src)
    assert m is not None, "load(sid) function missing"
    block = m.group(0)
    # Must use the /detail endpoint and pass user_id as a query param
    assert "/detail" in block
    assert "user_id=" in block, (
        "R-F739 regression: load(sid) does not pass user_id query "
        "param — clicking a conversation will return HTTP 400 from "
        "the R-F606 ownership check."
    )
    assert "USER_ID_SLUG" in block


def test_load_detail_url_well_formed():
    """R-F739: the constructed URL string must put user_id after a
    single `?` (not `&`)."""
    src = _read_html()
    # Frontend builds the URL via string concat:
    #   '/api/aria/conversations/' + encodeURIComponent(sid) + '/detail?user_id=' + ...
    assert "'/detail?user_id=' +" in src or '"/detail?user_id=" +' in src, (
        "R-F739: expected `/detail?user_id=` concatenation token not "
        "found — URL may use & instead of ?"
    )


def test_refresh_error_surfaces_message():
    """R-F739: convos-panel error state must surface the actual error,
    not the generic 'Failed to load.' string."""
    src = _read_html()
    # The improved string starts with "Failed to load conversations: "
    assert "Failed to load conversations: " in src
    assert "err && err.message" in src


def test_load_error_surfaces_message():
    """R-F739: per-conversation load error must surface the actual error.

    R-F1717: updated to the current form. R-F791 (de949ccb, 2026-05-21) refactored
    the catch to derive `errMsg = (err && err.message) ? err.message : 'unknown
    error'` and surface `'Failed to load conversation: ' + errMsg` — which still
    shows the real error. The old assertion expected the pre-R-F791 inline
    `(err && err.message` form and had been failing ever since.
    """
    src = _read_html()
    # Current form: errMsg is derived from err.message and surfaced inline.
    assert "var errMsg = (err && err.message)" in src
    assert "'Failed to load conversation: ' + errMsg" in src


def test_rf739_markers_present():
    """R-F739: marker present in at least 3 places (load fix, refresh
    error message, load error message)."""
    src = _read_html()
    count = src.count("R-F739")
    assert count >= 3, (
        f"R-F739: expected ≥3 markers across the addition zones, "
        f"found {count}."
    )


def test_rf769_reports_route_passes_user_id():
    """R-F769 (2026-05-21) — companion to R-F739. The PDF report route
    in lib/reports/routes.mjs fetches the same /detail endpoint to
    render audit-grade PDFs. Pre-R-F769 it omitted user_id, so the
    brain returned HTTP 400 (R-F606 ownership check) which the route
    mapped to a 502 — every PDF export failed with 'failed to fetch
    conversation'. Symptom in fly logs: bare
    'GET /api/aria/conversations/<sid>/detail HTTP/1.1 400 Bad Request'
    with no user_id query string. This test ensures the URL retains
    the user_id query param."""
    src = _read_reports_routes()
    # The fetch URL must include ?user_id= before being sent to the brain
    assert "/detail?user_id=" in src, (
        "R-F769 regression: lib/reports/routes.mjs /detail fetch is "
        "missing ?user_id= — every PDF export will 502."
    )
    # Slug must come from the same R-F742 derivation as aria.html:652
    assert re.search(
        r"user\.email\s*\|\|\s*user\.username\s*\|\|\s*user\.id",
        src,
    ), (
        "R-F769: user slug derivation must match aria.html (EMAIL > "
        "USERNAME > id) so the brain ownership check finds the "
        "conversation under the same key."
    )


def test_no_regression_in_other_convos_endpoints():
    """R-F739: list / delete / rename calls already passed user_id —
    must not have been touched by the fix."""
    src = _read_html()
    # List: ?user_id=<SLUG>&offset=...
    assert re.search(
        r"/api/aria/conversations\?user_id=' \+ encodeURIComponent\(USER_ID_SLUG\)",
        src,
    )
    # Delete: /api/aria/conversations/<sid>?user_id=<SLUG>
    assert re.search(
        r"/conversations/' \+ encodeURIComponent\(sid\) \+\s*\n?\s*'\?user_id=' \+ encodeURIComponent\(USER_ID_SLUG\)",
        src,
    )
