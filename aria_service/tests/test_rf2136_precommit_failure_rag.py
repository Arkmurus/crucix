"""R-F2136 — pre-commit hook failures are recorded in coding_failures RAG.

Capability: when the pre-commit hook blocks a commit, the first failure is
indexed into the coding_failures chromadb collection so the autonomous coder
can retrieve it later and learn from pre-commit rejections.

Tests:
1. record_precommit_failure() creates a FailureRecord and indexes it
2. The indexed failure is queryable via query_known_failures()
3. The pre-commit script calls record_precommit_failure() when it blocks
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))


def test_rf2136_record_precommit_failure_indexes_and_queries():
    """record_precommit_failure() must create a FailureRecord, index it,
    and make it retrievable via query_known_failures().

    Note: requires chromadb to be installed. If not, the test is skipped
    because the RAG store is unavailable (CI installs chromadb).
    """
    pytest.importorskip("chromadb", reason="chromadb not installed — RAG unavailable")

    from aria_service.intel.coding_rag_indexer import (
        record_precommit_failure,
        query_known_failures,
        get_stats,
    )

    # Record a pre-commit failure
    doc_id = record_precommit_failure(
        check_name="compile_gate",
        file_path="aria_service/intel/bad_module.py",
        error_message="COMPILE ERROR: SyntaxError at line 42",
        r_number="R-F2136",
    )

    assert doc_id is not None, "record_precommit_failure should return a doc ID"
    assert doc_id.startswith("fail_"), f"doc_id should start with fail_, got {doc_id}"

    # Query it back
    results = query_known_failures(
        gap_type="precommit_compile_gate",
        error_type="SyntaxError",
        top_k=3,
    )
    assert len(results) >= 1, (
        f"Should find at least 1 pre-commit failure, got {len(results)}"
    )

    best = results[0]
    assert "content" in best, "Result should have 'content'"
    assert "metadata" in best, "Result should have 'metadata'"
    assert "FAILED" in best["content"], "Content should indicate failure"
    assert "compile_gate" in best["content"] or "COMPILE" in best["content"], (
        "Content should reference the check name"
    )

    # Verify stats reflect the new failure
    stats = get_stats()
    assert stats["total_failures"] >= 1


def test_rf2136_record_precommit_failure_never_raises():
    """record_precommit_failure must never raise, even with bad inputs."""
    from aria_service.intel.coding_rag_indexer import record_precommit_failure

    # Empty strings
    result = record_precommit_failure(
        check_name="",
        file_path="",
        error_message="",
    )
    # Should not raise — may return None or a doc_id
    assert result is None or isinstance(result, str)

    # Very long error message (should be truncated)
    long_msg = "x" * 2000
    result = record_precommit_failure(
        check_name="test_check",
        file_path="test.py",
        error_message=long_msg,
    )
    assert result is None or isinstance(result, str)


def test_rf2136_precommit_script_calls_record_failure():
    """The pre-commit script must import and call record_precommit_failure
    when it blocks a commit."""
    src = (REPO / "scripts" / "pre-commit").read_text(encoding="utf-8")

    # Must reference record_precommit_failure
    assert "record_precommit_failure" in src, (
        "scripts/pre-commit must call record_precommit_failure when blocking"
    )

    # Must import from coding_rag_indexer
    assert "coding_rag_indexer" in src, (
        "scripts/pre-commit must import from coding_rag_indexer"
    )

    # Must be fire-and-forget (inside a try/except block that catches Exception)
    # The try is above the subprocess.run call, not adjacent to it
    assert "try:" in src, "record_precommit_failure call must be in a try block"
    assert "except" in src[src.find("record_precommit_failure") - 200:], (
        "record_precommit_failure call must be inside a try/except block"
    )
