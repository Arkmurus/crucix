"""R-F242 — reasoning_library archives cases instead of deleting.

Operator directive `aria_infinite_memory.md` (2026-05-11):
  "ARIA has INFINITE memory — never forgets, never loses data.
   No TTL on knowledge, no oldest-first prune, no eviction."

R-F173 prune was reversed by R-F238 for the same reason.

Tonight's 360 audit (2026-05-13) found `reasoning_library.consolidate()`
was deleting cases at lines 1086-1098 — same bug-shape, different module:
  (a) cases with access_count=0 + age>30d + confidence<0.5
  (b) cases with negative_outcomes>3 + positive_outcomes=0

R-F242 fix: replace both DELETE branches with archive flags. Cases
STAY in Redis with `archived=True` + `archived_at` + `archived_reason`.
Active index drops them (keeps search fast); forensic recall via
direct Redis lookup is preserved.

These tests pin the no-delete invariant.
"""
from __future__ import annotations

import pathlib
import re


def _src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/intel/reasoning_library.py"
    ).read_text(encoding="utf-8", errors="ignore")


def _consolidate_block() -> str:
    src = _src()
    idx = src.find("async def consolidate(")
    assert idx > 0, "consolidate() not found"
    nxt = src.find("\nasync def ", idx + 1)
    if nxt < 0:
        nxt = idx + 5000
    return src[idx:nxt]


# ── 1. Delete pattern is GONE ────────────────────────────────────

def test_rf242_consolidate_does_not_delete_cases():
    """The CRITICAL assertion. consolidate() must not contain the
    delete pattern `rs.set_json(key, None, ex=1)` — that's the
    "evict-by-TTL-1s" trick that violates aria_infinite_memory.md."""
    block = _consolidate_block()
    # The deletion pattern: set_json(key, None, ex=N) where N is small.
    delete_pattern = re.compile(r"set_json\([^,]+,\s*None\s*,\s*ex\s*=\s*\d+\s*\)")
    matches = delete_pattern.findall(block)
    assert not matches, (
        f"R-F242 CRITICAL: consolidate() still contains delete pattern(s):\n"
        f"  - {chr(10).join(matches)}\n"
        f"This violates aria_infinite_memory.md. Replace with the "
        f"archive flag pattern."
    )


def test_rf242_consolidate_uses_archive_flag():
    """The archive pattern must be in place — case stays in Redis
    with archived=True."""
    block = _consolidate_block()
    assert 'case["archived"] = True' in block, (
        "R-F242: archive flag pattern missing. The fix didn't land."
    )
    assert 'case["archived_at"]' in block, (
        "R-F242: archived_at timestamp missing — forensic recall needs the date."
    )
    assert 'case["archived_reason"]' in block, (
        "R-F242: archived_reason missing — chain-of-custody for WHY broken."
    )


def test_rf242_consolidate_persists_archive_flag():
    """The flag must be persisted via rs.set_json (NOT with None / ex=1)."""
    block = _consolidate_block()
    # set_json call with case (NOT None) confirms persist-not-delete.
    assert "await rs.set_json(_case_key(case[\"id\"]), case)" in block, (
        "R-F242: archived case not persisted back to Redis. Archive "
        "flag will be lost on next consolidate()."
    )


# ── 2. Archive reasons cite the directive + diagnostic data ───────

def test_rf242_archive_reason_cites_directive():
    """archived_reason text must reference aria_infinite_memory.md
    so future readers see WHY we archive instead of delete."""
    block = _consolidate_block()
    assert "aria_infinite_memory.md" in block, (
        "R-F242: archive reason doesn't cite the directive — chain-of-"
        "custody broken for future contributors."
    )


def test_rf242_archive_reason_includes_diagnostic():
    """Each archive must include the diagnostic that triggered it
    (age, access_count, confidence) so forensic recall can audit."""
    block = _consolidate_block()
    # Both branches must include diagnostic in the reason.
    assert "age=" in block or "age_days" in block
    assert "confidence" in block
    assert "negative_outcomes" in block


# ── 3. Return shape — archived field replaces pruned ─────────────

def test_rf242_consolidate_returns_archived_count():
    """The return dict must report `archived` (not just `pruned`).
    Legacy `pruned` field stays for back-compat but should equal
    archived + missing-data."""
    block = _consolidate_block()
    assert '"archived":' in block, (
        "R-F242: return dict missing 'archived' field."
    )


def test_rf242_consolidate_distinguishes_missing_from_archived():
    """If a case is missing from Redis (data lost, not our action),
    it should be counted as 'missing' — distinct from intentional
    archive."""
    block = _consolidate_block()
    assert '"missing":' in block, (
        "R-F242: return dict doesn't distinguish missing data from "
        "intentional archive. Operators can't tell the difference."
    )


def test_rf242_log_line_says_archive_not_prune():
    """The log line at the end of consolidate() must say 'archived'
    so the daily log review sees the correct verb. Past 'pruned'
    log line was misleading."""
    block = _consolidate_block()
    assert "archived" in block, "R-F242: log line doesn't mention archive"
    assert "R-F242" in block


# ── 4. Active index drops archived cases (search performance) ─────

def test_rf242_active_index_drops_archived_entries():
    """The post-consolidate index must NOT contain archived cases —
    keeps find_match fast. Cases stay in Redis (preserved) but the
    index only carries active ones."""
    block = _consolidate_block()
    # The two archive branches must NOT append to new_index — they
    # use `continue` after the archive write.
    assert block.count("continue") >= 3, (
        "R-F242: archive branches should `continue` to skip the "
        "new_index.append. Currently only N continues found — check "
        "the loop structure."
    )


# ── 5. Regression — no other module-level prune leaked back in ──

def test_rf242_no_other_consolidate_prune_logic_in_module():
    """Sanity scan: the WHOLE module should not contain any
    `set_json(key, None, ex=)` pattern that would constitute eviction.

    The legacy purge_polluted_cases / purge_unsafe_cases at the top
    of the file use rs.delete_pattern, not the ex=1 trick, and are
    operator-invoked rather than auto-fire — those are OUT of scope
    for R-F242 (operator decision, not directive violation)."""
    src = _src()
    consolidate_idx = src.find("async def consolidate(")
    # Check only inside the consolidate function
    nxt = src.find("\nasync def ", consolidate_idx + 1)
    consolidate_only = src[consolidate_idx:nxt if nxt > 0 else consolidate_idx + 5000]
    delete_pattern = re.compile(r"set_json\([^,]+,\s*None\s*,\s*ex\s*=\s*\d+\s*\)")
    assert not delete_pattern.search(consolidate_only), (
        "R-F242 regression: delete pattern crept back into consolidate()."
    )
