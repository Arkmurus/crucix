"""R-F2642 — 'search_all_engines_blocked' was emitted but never registered.

Live symptom (aria-intel, 2026-07-16): every SearXNG total-block logged

    Unknown gap type 'search_all_engines_blocked' — recording anyway

`web_search.py:593` has emitted this via `engine_wiring.wire_failure` since
R-F1656/R-F1657, but it was absent from `VALID_GAP_TYPES`.

SEVERITY — stated honestly (CLAUDE.md §19d): this is NOT data loss. Verified at
`capability_gaps.py:259`, `record_gap` only *warns* on an unknown type and then
records it anyway, so the coder loop still saw the gap. The cost is log noise
plus a real search-degradation signal demoted to noise — the same rationale the
file already gives for registering "llm_provider_failure" (R-F2551).

Same class as the already-registered "search_zero_results" (2026-04-27),
"codebase_health" (R-F1592) and "llm_provider_failure" (R-F2551) — the registry
drifts from the emit sites because nothing guards it. See the session report:
42 of 133 emitted gap types are currently unregistered; this test pins the one
fixed here and guards ITS emit site against re-drift.
"""

from __future__ import annotations

import pathlib
import re

from aria_service.intel.capability_gaps import VALID_GAP_TYPES

_GAP_TYPE = "search_all_engines_blocked"


def test_search_all_engines_blocked_is_registered() -> None:
    """The fix: the type web_search actually emits must be known to the registry."""
    assert _GAP_TYPE in VALID_GAP_TYPES


def test_web_search_emit_site_matches_the_registry() -> None:
    """Anti-drift: the literal at the emit site must stay registered.

    Pins the actual production call site rather than a copy of the string, so a
    rename on either side fails here instead of silently reopening the warning.
    """
    src = pathlib.Path("aria_service/intel/web_search.py").read_text(
        encoding="utf-8", errors="ignore",
    )

    emitted = set(re.findall(r'gap_type\s*=\s*["\']([a-z0-9_]+)["\']', src))

    assert _GAP_TYPE in emitted, (
        "web_search.py no longer emits "
        f"{_GAP_TYPE!r} — update this test if the emit site was renamed"
    )

    unregistered = {g for g in emitted if g not in VALID_GAP_TYPES}
    assert _GAP_TYPE not in unregistered
