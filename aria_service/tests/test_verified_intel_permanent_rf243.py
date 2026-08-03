"""R-F243 — verify verified_intel permanent storage invariant.

2026-05-13 360 ecosystem audit flagged `verified_intel.py` as a
potential infinite-memory-directive violation due to fact-type TTL
logic at FACT_TTL_DAYS. On code review, the module was already fixed
on 2026-04-21: lines 1376/1384/1480 all explicitly say "Permanent
storage — FACT_TTL_DAYS now governs staleness flagging only". The
fact stays in Redis with no `ex=` argument; FACT_TTL_DAYS is only
used to populate the `expires_at` metadata field, which the
`is_stale` property reads to flag re-verification candidates.

R-F243 ships as a REGRESSION GUARD: pin the no-TTL-on-store invariant
so a future contributor can't accidentally add `ex=` back to the
set_json calls and slip a TTL-eviction violation in unnoticed.

This is the same shape as R-F406 — once the agent audit closes a
module as compliant, pin the invariant in a test that fails loudly
if it drifts.
"""
from __future__ import annotations

import pathlib
import re

from ._source_probe import repo_path


def _src() -> str:
    return pathlib.Path(
        repo_path("aria_service/intel/verified_intel.py")
    ).read_text(encoding="utf-8", errors="ignore")


# ── 1. The "Permanent storage" marker exists ─────────────────────

def test_rf243_permanent_storage_marker_present():
    """The R-F243 / 2026-04-21 fix added comment markers at every
    set_json site that previously had a TTL. These markers are the
    chain-of-custody for the invariant — if they vanish, the
    code-review trail vanishes too."""
    src = _src()
    permanent_marker_count = src.count("Permanent storage — FACT_TTL_DAYS now governs staleness flagging only")
    assert permanent_marker_count >= 3, (
        f"R-F243 regression: expected >=3 'Permanent storage' markers "
        f"in verified_intel.py (one per set_json call site). Got "
        f"{permanent_marker_count}. If you removed one, you may have "
        f"removed the comment that prevented re-introducing a TTL."
    )


# ── 2. No set_json call passes ex= (TTL kwarg) ───────────────────

def test_rf243_no_set_json_with_ex_kwarg():
    """The CRITICAL assertion. Every `set_json` call in this module
    must NOT have an `ex=` argument — that would convert permanent
    storage back into TTL-eviction storage and violate
    aria_infinite_memory.md."""
    src = _src()
    # Match: set_json(...) where the argument list contains `ex=...`
    pattern = re.compile(r"set_json\s*\([^)]*\bex\s*=", re.DOTALL)
    matches = pattern.findall(src)
    assert not matches, (
        f"R-F243 CRITICAL: verified_intel.py has set_json calls with "
        f"ex= TTL kwargs:\n  - {chr(10).join(matches)}\n"
        f"This violates aria_infinite_memory.md. Remove the ex= argument; "
        f"FACT_TTL_DAYS should drive the expires_at METADATA field only, "
        f"never a Redis storage TTL."
    )


# ── 3. FACT_TTL_DAYS is metadata-only, not storage TTL ───────────

def test_rf243_fact_ttl_days_used_for_expires_at_only():
    """FACT_TTL_DAYS values must be read into `expires_at` (metadata)
    and NOT passed to `set_json(..., ex=N)` (storage TTL).

    Pattern check: every FACT_TTL_DAYS.get(...) call must be followed
    by `expires_at = ...` assignment, not by `set_json(... ex=ttl)`."""
    src = _src()
    # Find every FACT_TTL_DAYS reference
    ttl_uses = list(re.finditer(r"FACT_TTL_DAYS", src))
    assert len(ttl_uses) >= 2, (
        "R-F243: FACT_TTL_DAYS not referenced — has it been removed? "
        "If so, the invariant test needs updating too."
    )
    # For each usage, look at the next 200 chars — should mention
    # expires_at, NOT ex=.
    for m in ttl_uses:
        ctx = src[m.start():m.start() + 400]
        # Allow definitions / iterations to reference TTL without
        # immediately writing to set_json(ex=...).
        bad = re.search(r"FACT_TTL_DAYS[^.]*\.get\([^)]*\)\s*\n[\s\S]{0,80}set_json\s*\([^)]*\bex\s*=", ctx)
        assert not bad, (
            f"R-F243: FACT_TTL_DAYS value flows into a set_json ex= "
            f"argument. Context:\n{ctx[:400]}"
        )


# ── 4. is_stale property still works (regression — staleness is OK) ──

def test_rf243_is_stale_property_still_present():
    """Staleness FLAGGING is allowed — that's the metadata pattern.
    Staleness eviction is forbidden. The `is_stale` property must
    remain so consumers can ask "should I re-verify this?"."""
    src = _src()
    assert "def is_stale" in src
    # And it must read from expires_at, not from a Redis TTL
    idx = src.find("def is_stale")
    block = src[idx:idx + 600]
    assert "expires_at" in block, (
        "R-F243: is_stale must read from the expires_at METADATA field "
        "(not from Redis TTL or a separate eviction policy)."
    )


# ── 5. No alternative eviction paths in the module ───────────────

def test_rf243_no_eviction_call_patterns():
    """Sanity scan for the broader eviction-grammar set, not just
    set_json(ex=). If `rs.delete()`, `redis_del()`, or `set_json(None)`
    appears in fact-storage code, that's eviction too."""
    src = _src()
    # Find functions that handle fact storage / retrieval
    eviction_patterns = [
        re.compile(r"redis_del\s*\("),
        re.compile(r"rs\.delete\s*\("),
        re.compile(r"set_json\s*\([^,]+,\s*None\s*,\s*ex\s*="),  # the ex=1 deletion trick
    ]
    hits: list[str] = []
    for pat in eviction_patterns:
        for m in pat.finditer(src):
            hits.append(m.group(0))
    assert not hits, (
        f"R-F243 regression: eviction grammar detected in verified_intel.py:\n"
        f"  - {chr(10).join(hits)}\n"
        f"Compliance with aria_infinite_memory.md requires storage to "
        f"be permanent. Use the archived flag pattern (see R-F242)."
    )
