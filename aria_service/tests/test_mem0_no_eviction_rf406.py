"""R-F406 — pin mem0 retention as PERMANENT (no eviction).

Live evidence 2026-05-13 07:27 (WhatsApp): ARIA claimed "MEM0 Notebook
... each new entry can overwrite/compress older ones." Code audit
2026-05-13 found NO delete / prune / evict / cleanup / trim / overwrite
calls in `aria_service/intel/mem0.py`. The claim was a hallucination
that violates operator directive aria_infinite_memory.md.

This test is a REGRESSION GUARD: it asserts the file contains no
eviction-call grammar. If a future contributor adds a `redis_del` /
`.delete()` / `prune_*` / `compact_*` to mem0.py, this test fails
loudly and forces a conscious operator decision (likely a revert,
since aria_infinite_memory.md is binding).

Reference for the live caps that are NOT eviction (only retrieval-
side per-query injection limits):
  _MAX_MEM0_RECALL_CHARS = 1200   (line ~297)
  _MAX_MEM0_RECALL_FACTS = 6      (line ~298)

These cap WHAT GETS INJECTED INTO A REPLY — they do not delete
stored entries. The test below allows these specific identifiers
to appear; everything else with eviction grammar is forbidden.
"""
from __future__ import annotations

import pathlib
import re


def _mem0_src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/intel/mem0.py"
    ).read_text(encoding="utf-8", errors="ignore")


# ── 1. Documentation pin ──────────────────────────────────────────

def test_rf406_docstring_says_permanent():
    """The module docstring must explicitly state the permanent
    retention policy + name R-F406 as the audit anchor."""
    src = _mem0_src()
    assert "R-F406" in src, (
        "R-F406 regression: audit anchor missing from mem0.py docstring."
    )
    assert "PERMANENT" in src, (
        "R-F406: module docstring doesn't state PERMANENT retention."
    )
    assert "aria_infinite_memory.md" in src


def test_rf406_docstring_explicitly_refutes_overwrite_claim():
    """The docstring must explicitly call out the 07:27 hallucination
    so a future ARIA introspection (or future contributor) sees the
    chain of custody."""
    src = _mem0_src()
    assert "2026-05-13" in src
    assert "overwrite/compress" in src or "overwrite or compress" in src, (
        "R-F406: docstring doesn't explicitly refute the 07:27 "
        "'overwrite/compress' claim. Chain-of-custody broken."
    )


def test_rf406_docstring_removes_misleading_lifecycle_note():
    """The old 'auto-cleanup of stale notebook entries possible' line
    was the seed of the hallucination. It must be removed (the cleanup
    was never implemented; the line implied capability that doesn't
    exist)."""
    src = _mem0_src()
    assert "auto-cleanup of stale notebook entries possible" not in src, (
        "R-F406 regression: the 'auto-cleanup ... possible' phrasing "
        "is back. This was the seed of the 2026-05-13 hallucination — "
        "ARIA reading her own code saw 'possible' and reported it as "
        "implemented. Remove or rewrite."
    )


# ── 2. Code pin — no eviction calls ──────────────────────────────

# Patterns that would signal eviction was added.
_FORBIDDEN_PATTERNS = [
    # Delete calls
    (r"\bredis_?del\b", "redis del call"),
    (r"\bredisDel\b", "redisDel call"),
    (r"\.delete\(", ".delete( call"),
    (r"\.remove\(", ".remove( call"),
    # Prune / evict grammar
    (r"\bprune[_a-z]*\(", "prune function call"),
    (r"\bevict[_a-z]*\(", "evict function call"),
    (r"\bcompact[_a-z]*\(", "compact function call"),
    # Overwrite grammar specific to stored entries
    (r"\boverwrite[_a-z]*\(", "overwrite function call"),
    # Cleanup grammar (the docstring removed this; code must not reintroduce)
    (r"\bauto[_-]?cleanup\b", "auto-cleanup logic"),
]


def test_rf406_no_eviction_calls_in_module():
    """The big one: scan mem0.py for any eviction-grammar function
    calls. Caps like `_MAX_MEM0_RECALL_CHARS` are OK (retrieval-side
    injection limits, not eviction). Function calls that would delete
    stored entries are NOT OK."""
    src = _mem0_src()
    # Strip docstrings + comments before pattern matching so the
    # documentation references to "evict" / "overwrite" (which are
    # DESCRIBING what we don't do) don't trip the regex.
    # Simple approach: remove all triple-quoted strings + #-comments.
    no_docstr = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
    no_docstr = re.sub(r"'''.*?'''", '', no_docstr, flags=re.DOTALL)
    code_only = re.sub(r"#[^\n]*", '', no_docstr)

    hits: list[str] = []
    for pat, desc in _FORBIDDEN_PATTERNS:
        m = re.search(pat, code_only, re.IGNORECASE)
        if m:
            hits.append(f"{desc} matched as {m.group(0)!r}")

    assert not hits, (
        f"R-F406 CRITICAL: eviction grammar detected in mem0.py code body:\n"
        f"  - {chr(10).join(hits)}\n"
        f"This violates aria_infinite_memory.md. Either:\n"
        f"  (a) Revert the change — eviction is forbidden per operator directive\n"
        f"  (b) Get explicit operator approval to amend the directive\n"
        f"     and update this test + aria_infinite_memory.md memory note.\n"
        f"Past incident anchor: 2026-05-13 07:27 hallucination + R-F406 audit."
    )


def test_rf406_retrieval_caps_still_allowed():
    """The retrieval-side caps (_MAX_MEM0_RECALL_CHARS,
    _MAX_MEM0_RECALL_FACTS) must REMAIN — they're per-query injection
    limits, not eviction. This test pins them so a future refactor
    that misreads the audit doesn't accidentally remove the caps."""
    src = _mem0_src()
    assert "_MAX_MEM0_RECALL_CHARS" in src
    assert "_MAX_MEM0_RECALL_FACTS" in src


# ── 3. /health/perf reflects the audit ───────────────────────────

def test_rf406_health_perf_retention_mem0_marked_none():
    """The /health/perf retention.mem0.eviction field must now read
    'none' (was 'unverified — see R-F406' before this audit). The
    new anchor field must cite R-F406 + 2026-05-13."""
    perf_src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    # Find the mem0 retention block
    idx = perf_src.find('"mem0": {')
    assert idx > 0
    end = perf_src.find("}", idx) + 1
    block = perf_src[idx:end]
    assert '"eviction": "none"' in block, (
        "R-F406: /health/perf retention.mem0.eviction must read 'none' "
        "after the audit completed. Currently still hedged."
    )
    # And the anchor must cite the audit
    assert "R-F406" in block
    assert "2026-05-13" in block
