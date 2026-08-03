"""R-F422 — adversarial amendments dedupe + auto-reject stale.

Live evidence 2026-05-13 22:30: operator's dashboard showed 17 staged
amendments going back to 2026-04-19, including:
  - 5× E1_FABRICATED_COMMITMENT (same attack, anchor clauses [11, 20])
  - 3× C1_MULTITURN_COMPLIANCE_DRIFT (same attack, anchor clauses [3,4,6])
  - 9 newer entries from 2026-05-10

The same attack failing N times was creating N queue entries instead of
bumping a fail_count on the existing one. Result: noisy queue, operator
ignores it, real high-priority amendments get lost in stale duplicates.

R-F422 fix in `adversarial_challenge.py` staging logic:
  - On stage: look for existing entry by (attack_id, anchor_clauses).
    If found, increment fail_count + update last_failed_at + refresh
    proposed_amendment text. Move to top of queue.
    If not found, insert with fail_count=1.

R-F422 fix in `routes/aria.py` GET endpoint (on-read maintenance):
  - Backfill missing fail_count + last_failed_at on legacy pre-R-F422
    entries (the operator's 17-entry queue).
  - Auto-reject entries where last_failed_at > 14 days AND fail_count
    < 3 — stale low-priority duplicates the operator hasn't triaged.
  - High-failure attacks (fail_count >= 3) STAY in queue — they need
    real operator attention.
  - Audit-log auto-rejections to rejected_amendments with explicit
    reason.

Dashboard pin: panel surfaces fail_count + last_failed_at columns
(was: only staged_at).
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


def _staging_src() -> str:
    return pathlib.Path(
        repo_path("aria_service/intel/adversarial_challenge.py")
    ).read_text(encoding="utf-8", errors="ignore")


def _routes_src() -> str:
    return pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")


def _dashboard_src() -> str:
    return pathlib.Path(
        repo_path("public/aria-brain.html")
    ).read_text(encoding="utf-8", errors="ignore")


# ── 1. Staging dedupe — adversarial_challenge.py ───────────────────

def test_rf422_staging_looks_up_existing_by_attack_id_and_clauses():
    """The staging logic must check for an existing entry by BOTH
    attack_id AND anchor_clauses — same attack with different clauses
    should create separate amendments."""
    src = _staging_src()
    # Find the staging block (look for the R-F422 marker)
    idx = src.find("R-F422")
    assert idx > 0, (
        "R-F422 regression: staging logic doesn't carry the R-F422 "
        "anchor comment — dedupe likely missing."
    )
    # Read the block
    block = src[idx:idx + 3000]
    # Must look up by attack_id
    assert "attack_id" in block
    # Must look up by anchor_clauses (the tuple comparison)
    assert "anchor_clauses" in block
    # Must use list comparison so order/type mismatches don't fail
    assert "list(" in block or "tuple(" in block, (
        "R-F422: anchor_clauses comparison should use list()/tuple() "
        "wrap so list-vs-tuple Redis round-trip doesn't break dedupe."
    )


def test_rf422_staging_increments_fail_count():
    """When a duplicate is found, fail_count must increment, not stay at 1."""
    src = _staging_src()
    assert 'queue[existing_idx]["fail_count"]' in src, (
        "R-F422 regression: fail_count increment missing."
    )
    # Look for the +1 increment pattern
    idx = src.find('queue[existing_idx]["fail_count"]')
    block = src[idx:idx + 500]
    assert "+ 1" in block, (
        "R-F422: fail_count increment doesn't add 1. The dedupe wins "
        "the wrong way — counts stay flat."
    )


def test_rf422_staging_refreshes_last_failed_at():
    """last_failed_at must update on every re-stage so the dashboard
    can sort by recency."""
    src = _staging_src()
    assert "last_failed_at" in src
    assert 'queue[existing_idx]["last_failed_at"] = now_iso' in src or (
        "last_failed_at" in src and "now_iso" in src
    )


def test_rf422_staging_new_entry_has_fail_count_1():
    """A brand-new staging (no existing entry) must set fail_count=1
    AND last_failed_at = staged_at so the dashboard renders consistently."""
    src = _staging_src()
    # Look for the new-entry block
    idx = src.find('"fail_count": 1')
    assert idx > 0, (
        "R-F422: new-entry path doesn't initialize fail_count=1."
    )


def test_rf422_staging_moves_recent_to_top():
    """When a duplicate is updated, it should move to the top of the
    queue so the operator sees most-recently-failed first."""
    src = _staging_src()
    assert "queue.pop(existing_idx)" in src, (
        "R-F422: re-staged duplicate doesn't move to top of queue."
    )


# ── 2. On-read maintenance — routes/aria.py GET endpoint ──────────

def test_rf422_get_endpoint_backfills_legacy_entries():
    """The GET endpoint must backfill missing fail_count / last_failed_at
    on pre-R-F422 entries (the 17-entry legacy queue)."""
    src = _routes_src()
    idx = src.find("adversarial_amendments_ep")
    block = src[idx:idx + 4000]
    assert "R-F422" in block
    # Backfill check
    assert 'if "last_failed_at" not in entry' in block, (
        "R-F422: GET endpoint doesn't backfill legacy entries — old "
        "rows render with empty fail_count column."
    )
    assert 'if "fail_count" not in entry' in block


def test_rf422_get_endpoint_auto_rejects_stale():
    """Entries older than 14 days with fail_count < 3 must be
    auto-rejected on the next GET."""
    src = _routes_src()
    idx = src.find("adversarial_amendments_ep")
    block = src[idx:idx + 4000]
    assert "timedelta(days=14)" in block
    assert "fc < 3" in block, (
        "R-F422: auto-reject threshold not fail_count<3 — high-failure "
        "attacks must STAY in queue regardless of age."
    )


def test_rf422_get_endpoint_audit_logs_auto_rejections():
    """Auto-rejected entries must be appended to rejected_amendments
    so the chain-of-custody survives."""
    src = _routes_src()
    idx = src.find("adversarial_amendments_ep")
    block = src[idx:idx + 4000]
    assert "rejected_amendments" in block
    assert "auto" in block.lower()
    # Reason text must mention R-F422 + the threshold
    assert "R-F422 auto-reject" in block


def test_rf422_get_endpoint_surfaces_rf422_metadata():
    """The response must include the count of auto-rejections from
    this call so an operator can see when the cleanup fires."""
    src = _routes_src()
    idx = src.find("adversarial_amendments_ep")
    block = src[idx:idx + 4000]
    assert "_rf422_auto_rejected_this_call" in block


# ── 3. Dashboard — fail_count column ─────────────────────────────

def test_rf422_dashboard_shows_fail_count_column():
    """The Pending Amendments panel must render a Fails column with
    a colour-coded count so the operator can prioritise high-recurrence
    attacks at-a-glance."""
    src = _dashboard_src()
    assert "Fails" in src, (
        "R-F422: dashboard column header 'Fails' missing — operator "
        "can't prioritise high-fail attacks."
    )
    # The cell must read from a.fail_count
    assert "a.fail_count" in src


def test_rf422_dashboard_colour_codes_recurrence():
    """High-recurrence attacks (fail_count >= 5) must render in red
    so they pop visually. >= 3 is yellow. Below is grey."""
    src = _dashboard_src()
    idx = src.find("R-F422")
    block = src[idx:idx + 1500]
    # Red threshold
    assert "failCount >= 5" in block
    # Yellow threshold
    assert "failCount >= 3" in block
    # The actual colour codes (red + yellow from the CURRENT design system).
    # R-F2711: the old GitHub-dark tokens (#f85149 / #d29922) were replaced by
    # the design-system tokens below; this assertion drifted, not the UI.
    assert "#dc2626" in block  # red
    assert "#ca8a04" in block  # yellow


def test_rf422_dashboard_shows_last_failed_column():
    """Replace 'Staged' with 'Last failed' — operator needs to know
    recency, not first-seen date. (Staged still rendered as fallback
    when last_failed_at is missing on legacy entries.)"""
    src = _dashboard_src()
    assert "Last failed" in src
    # Cell value reads last_failed_at OR falls back to staged_at
    assert "a.last_failed_at || a.staged_at" in src
