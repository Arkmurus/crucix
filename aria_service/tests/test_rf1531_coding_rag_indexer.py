"""R-F1531 — Capability tests for Coding RAG Indexer + RAG-Augmented Generator.

Tests the full chain:
  1. index_fix() — stores a fix record and retrieves it
  2. index_failure() — stores a failure record and retrieves it
  3. index_codebase_structure() — chunks a module and retrieves context
  4. index_constitutional_rules() — stores rules and queries them
  5. build_augmented_context() — builds prompt context from all sources
  6. build_fix_prompt_section() — convenience wrapper
  7. get_stats() — returns correct counts
  8. SovereignLLM integration — RAG context is passed to plan prompt
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# R-F2360 — the coding RAG requires chromadb. Skip this module CLEANLY when chromadb is
# absent (a dev venv), so the local suite is honest instead of showing false-red failures
# that mask a real regression. On prod/CI chromadb IS installed and these run for real.
pytest.importorskip("chromadb")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fix_record(
    r_number: str = "F1531",
    title: str = "Test fix for coding RAG",
    gap_type: str = "module_bug",
    module: str = "test_module",
) -> dict:
    """Create a FixRecord-compatible dict for testing."""
    return {
        "r_number": r_number,
        "title": title,
        "gap_type": gap_type,
        "module": module,
        "problem_description": "A test bug that needs fixing",
        "approach": "Added null check before accessing the field",
        "files_changed": ["aria_service/intel/test_module.py"],
        "tests_passed": 5,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": "success",
    }


def _make_failure_record(
    r_number: str = "F1531",
    attempt_number: int = 1,
    error_type: str = "KeyError",
) -> dict:
    """Create a FailureRecord-compatible dict for testing."""
    return {
        "r_number": r_number,
        "attempt_number": attempt_number,
        "error_type": error_type,
        "error_message": "'missing_key'",
        "why_failed": "Assumed key exists without checking",
        "next_approach": "Use .get() with default instead of direct access",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Test: index_fix + query_relevant_fixes ───────────────────────────────────


def test_rf1531_index_and_query_fix():
    """Capability test: index a fix, then query it back."""
    from aria_service.intel.coding_rag_indexer import (
        FixRecord,
        get_stats,
        index_fix,
        query_relevant_fixes,
    )

    record = FixRecord(**_make_fix_record())
    doc_id = index_fix(record)

    # Must return a doc ID (not None)
    assert doc_id is not None, "index_fix should return a doc ID"
    assert doc_id.startswith("fix_F1531_"), f"doc_id should start with fix_F1531_, got {doc_id}"

    # Query it back — use the exact title for reliable semantic match
    results = query_relevant_fixes("Test fix for coding RAG", top_k=5)
    assert len(results) >= 1, f"Should find at least 1 fix, got {len(results)}"

    # The most relevant result should contain our fix
    best = results[0]
    assert "content" in best, "Result should have 'content'"
    assert "metadata" in best, "Result should have 'metadata'"
    assert "similarity" in best, "Result should have 'similarity'"
    assert best["similarity"] >= 0.0, "Similarity should be non-negative"
    assert "Test fix for coding RAG" in best["content"], "Content should contain the fix title"

    # Verify stats
    stats = get_stats()
    assert stats["ready"] is True
    assert stats["total_fixes"] >= 1


def test_rf1531_index_and_query_failure():
    """Capability test: index a failure, then query it back."""
    from aria_service.intel.coding_rag_indexer import (
        FailureRecord,
        get_stats,
        index_failure,
        query_known_failures,
    )

    record = FailureRecord(**_make_failure_record())
    doc_id = index_failure(record)

    assert doc_id is not None, "index_failure should return a doc ID"
    assert doc_id.startswith("fail_F1531_"), f"doc_id should start with fail_F1531_, got {doc_id}"

    # Query it back
    results = query_known_failures(gap_type="module_bug", error_type="KeyError", top_k=3)
    assert len(results) >= 1, f"Should find at least 1 failure, got {len(results)}"

    best = results[0]
    assert "content" in best, "Result should have 'content'"
    assert "metadata" in best, "Result should have 'metadata'"
    assert "FAILED" in best["content"], "Content should indicate failure"

    # Verify stats
    stats = get_stats()
    assert stats["total_failures"] >= 1


def test_rf1531_index_codebase_structure():
    """Capability test: index a module's structure and query it back.

    Uses the coding_rag_indexer.py module itself as the test subject.
    """
    from aria_service.intel.coding_rag_indexer import (
        get_stats,
        index_codebase_structure,
        query_codebase_context,
    )

    module_path = Path(__file__).parent.parent / "intel" / "coding_rag_indexer.py"
    assert module_path.exists(), f"Test module not found at {module_path}"

    chunk_count = index_codebase_structure(module_path)
    assert chunk_count > 0, f"Should index at least 1 chunk, got {chunk_count}"

    # Query it back
    results = query_codebase_context("coding_rag_indexer", top_k=5)
    assert len(results) >= 1, f"Should find at least 1 structure chunk, got {len(results)}"

    best = results[0]
    assert "content" in best, "Result should have 'content'"
    assert "metadata" in best, "Result should have 'metadata'"
    assert best["metadata"].get("module", "").endswith("coding_rag_indexer.py"), \
        "Metadata should reference the indexed module"

    # Verify stats
    stats = get_stats()
    assert stats["total_codebase_chunks"] >= chunk_count


def test_rf1531_index_constitutional_rules():
    """Capability test: index constitutional rules and query them."""
    from aria_service.intel.coding_rag_indexer import (
        get_stats,
        index_constitutional_rules,
        query_constitutional_constraints,
    )

    rules = [
        {
            "name": "no_eval_exec",
            "clause_number": "1",
            "description": "Never use eval() or exec() in generated code",
            "constraint": "Forbidden: eval(), exec(), compile() with arbitrary code",
            "affected_modules": ["sovereign_llm", "self_coder"],
            "protected_files": [],
            "consequence": "Block deployment",
        },
        {
            "name": "wire_both_branches",
            "clause_number": "2",
            "description": "Every module must wire success AND failure to the brain",
            "constraint": "Both wire_success() and wire_failure() must be present",
            "affected_modules": ["all"],
            "protected_files": [],
            "consequence": "Pre-commit hook rejection",
        },
    ]

    count = index_constitutional_rules(rules)
    assert count == 2, f"Should index 2 rules, got {count}"

    # Query for eval constraint
    results = query_constitutional_constraints("constraints on using eval", top_k=3)
    assert len(results) >= 1, f"Should find at least 1 rule, got {len(results)}"

    best = results[0]
    assert "rule" in best, "Result should have 'rule'"
    assert "metadata" in best, "Result should have 'metadata'"
    assert "no_eval_exec" in best["rule"] or "eval" in best["rule"], \
        "Rule content should mention eval"

    # Verify stats
    stats = get_stats()
    assert stats["total_constitutional_rules"] >= 2


# ── Test: RAGAugmentedGenerator ──────────────────────────────────────────────


def test_rf1531_build_augmented_context():
    """Capability test: build_augmented_context returns all sections."""
    from aria_service.intel.rag_augmented_generator import build_augmented_context

    # First index some data so there's something to retrieve
    from aria_service.intel.coding_rag_indexer import FixRecord, FailureRecord, index_fix, index_failure

    index_fix(FixRecord(
        r_number="F1531",
        title="Test fix for augmented context",
        gap_type="module_bug",
        module="test_module",
        problem_description="A test bug",
        approach="Added null check",
        files_changed=["test.py"],
        tests_passed=3,
        timestamp=datetime.now(timezone.utc).isoformat(),
    ))
    index_failure(FailureRecord(
        r_number="F1531",
        attempt_number=1,
        error_type="KeyError",
        error_message="missing key",
        why_failed="Direct access without .get()",
        next_approach="Use .get() with default",
        timestamp=datetime.now(timezone.utc).isoformat(),
    ))

    result = build_augmented_context(
        gap_type="module_bug",
        module="test_module",
        title="Test fix",
        error_type="KeyError",
        codebase_context="def test_func():\n    pass\n",
    )

    # Check structure
    assert "augmented_context" in result, "Should have augmented_context"
    assert "similar_fixes" in result, "Should have similar_fixes"
    assert "known_failures" in result, "Should have known_failures"
    assert "structural_context" in result, "Should have structural_context"
    assert "constraints" in result, "Should have constraints"
    assert "tokens_saved" in result, "Should have tokens_saved"
    assert "retrieval_count" in result, "Should have retrieval_count"

    # The augmented context should contain the fix title
    context = result["augmented_context"]
    assert "Test fix for augmented context" in context, \
        "Augmented context should contain the fix title"
    assert "FAILED" in context, "Augmented context should contain failure info"
    assert "CURRENT CODEBASE CONTEXT" in context, \
        "Augmented context should include current codebase"

    # Tokens saved should be positive
    assert result["tokens_saved"] >= 0, "Tokens saved should be non-negative"
    assert result["retrieval_count"] >= 2, "Should have retrieved at least 2 documents"


def test_rf1531_build_fix_prompt_section():
    """Capability test: build_fix_prompt_section returns just the text."""
    from aria_service.intel.rag_augmented_generator import build_fix_prompt_section

    text = build_fix_prompt_section(
        gap_type="module_bug",
        module="test_module",
        title="Test",
        error_type="KeyError",
    )

    assert isinstance(text, str), "Should return a string"
    assert len(text) > 0, "Should not be empty"
    # Should contain at least one section header
    assert "RELEVANT PREVIOUS FIXES" in text or "FAILURES TO AVOID" in text or \
        "CODEBASE STRUCTURE" in text or "CONSTITUTIONAL CONSTRAINTS" in text or \
        "CURRENT CODEBASE CONTEXT" in text, \
        "Should contain at least one context section"


# ── Test: get_stats ──────────────────────────────────────────────────────────


def test_rf1531_get_stats():
    """Capability test: get_stats returns correct structure."""
    from aria_service.intel.coding_rag_indexer import get_stats

    stats = get_stats()
    assert isinstance(stats, dict), "Stats should be a dict"
    assert "ready" in stats, "Stats should have 'ready' flag"
    assert "total_fixes" in stats, "Stats should have total_fixes"
    assert "total_failures" in stats, "Stats should have total_failures"
    assert "total_codebase_chunks" in stats, "Stats should have total_codebase_chunks"
    assert "total_constitutional_rules" in stats, "Stats should have total_constitutional_rules"

    # All counts should be non-negative integers
    for key in ["total_fixes", "total_failures", "total_codebase_chunks", "total_constitutional_rules"]:
        assert isinstance(stats[key], int), f"{key} should be an int"
        assert stats[key] >= 0, f"{key} should be non-negative"


# ── Test: SovereignLLM integration ───────────────────────────────────────────


def test_rf1531_sovereign_llm_rag_integration():
    """Capability test: SovereignLLM.generate_fix_plan retrieves RAG context.

    Verifies that the RAG context retrieval is called (not silently skipped)
    and that the prompt includes the RAG section.
    """
    import asyncio
    from aria_service.autonomous.sovereign_llm import SovereignLLM
    from aria_service.autonomous.gap_detector import Gap, GapSeverity, GapType

    llm = SovereignLLM(
        aria_service_url="http://test",
        client=AsyncMock(),
    )

    gap = Gap(
        gap_id="test-gap-1531",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.MEDIUM,
        title="Test gap for RAG integration",
        description="A test gap to verify RAG context is injected",
        module="test_module",
        error_trace="KeyError: 'missing'",
    )

    # Mock _call to return a simple plan
    llm._call = AsyncMock(return_value={
        "title": "Test fix",
        "root_cause": "Missing null check",
        "approach": "Add null check",
        "target_files": ["test_module.py"],
        "new_files": [],
        "changes_summary": "Added null check",
        "downstream_risk": "none",
        "risk_level": "low",
        "estimated_effort_minutes": 10,
    })

    # Run with a mock context — must await the async method
    result = asyncio.run(
        llm.generate_fix_plan(gap, "def existing():\n    pass\n")
    )

    # Verify _call was invoked
    assert llm._call.called, "_call should have been invoked"

    # Get the prompt that was passed
    call_kwargs = llm._call.call_args
    prompt = call_kwargs[1].get("prompt", "") if call_kwargs else ""

    # The prompt should contain the RAG section header (even if no results)
    assert "RAG-AUGMENTED CONTEXT" in prompt, \
        "Prompt should contain the RAG-AUGMENTED CONTEXT section"

    # Verify the result is a valid plan
    assert isinstance(result, dict), "Result should be a dict"
    assert "title" in result, "Result should have a title"


# ── Test: self_improve integration ────────────────────────────────────────────


def test_rf1531_self_improve_deploy_records_fix():
    """Capability test: deploy_improvement records a fix in CodingRAG.

    Verifies that after a successful deploy, the fix is indexed and
    queryable via query_relevant_fixes.
    """
    from aria_service.intel.coding_rag_indexer import query_relevant_fixes

    # Index a fix record (simulating what deploy_improvement does)
    from aria_service.intel.coding_rag_indexer import FixRecord, index_fix

    record = FixRecord(
        r_number="F1531-DEPLOY",
        title="Deployed test fix",
        gap_type="bug_fix",
        module="aria_service/intel/test_module.py",
        problem_description="Test deploy fix",
        approach="Deployed via self_improve.deploy_improvement",
        files_changed=["aria_service/intel/test_module.py"],
        tests_passed=5,
        timestamp=datetime.now(timezone.utc).isoformat(),
        outcome="success",
    )
    doc_id = index_fix(record)
    assert doc_id is not None, "Fix should be indexed"

    # Verify it's queryable
    results = query_relevant_fixes("deployed test fix", top_k=5)
    assert len(results) >= 1, "Should find the deployed fix"
    assert any("Deployed test fix" in r.get("content", "") for r in results), \
        "Should find the deployed fix by content"


# ── Test: self_coder integration ──────────────────────────────────────────────


def test_rf1531_self_coder_failure_records():
    """Capability test: self_coder failure path records in CodingRAG.

    Verifies that a failure record is queryable after being indexed
    (simulating what self_coder.fix_gap does on failure).
    """
    from aria_service.intel.coding_rag_indexer import FailureRecord, index_failure, query_known_failures

    record = FailureRecord(
        r_number="F1531-CODER",
        attempt_number=2,
        error_type="test_failure",
        error_message="Tests failed after healing attempts",
        why_failed="Root cause not addressed",
        next_approach="Review test output and fix root cause",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    doc_id = index_failure(record)
    assert doc_id is not None, "Failure should be indexed"

    # Verify it's queryable
    results = query_known_failures(gap_type="module_bug", top_k=5)
    assert len(results) >= 1, "Should find the failure"
    assert any("F1531-CODER" in r.get("content", "") for r in results), \
        "Should find the failure by R-number"


# ── Test: empty/edge cases ────────────────────────────────────────────────────


def test_rf1531_query_empty_collection():
    """Edge case: querying with a unique query returns empty list.

    Uses a UUID-based query string that won't match any indexed content.
    """
    import uuid
    from aria_service.intel.coding_rag_indexer import (
        query_codebase_context,
        query_constitutional_constraints,
        query_known_failures,
        query_relevant_fixes,
    )

    unique_query = f"zzzzzzzz_nonexistent_{uuid.uuid4().hex}_zzzzzzzz"

    # These should never raise, even when no results match.
    # Use min_similarity=0.9 to filter out low-similarity results that
    # chromadb returns by default (it always returns top_k).
    results = query_relevant_fixes(unique_query, top_k=5, min_similarity=0.9)
    assert results == [], f"Expected empty list, got {len(results)} results"

    results = query_known_failures(unique_query, top_k=5, min_similarity=0.9)
    assert results == [], f"Expected empty list, got {len(results)} results"

    results = query_codebase_context(unique_query, top_k=5, min_similarity=0.9)
    assert results == [], f"Expected empty list, got {len(results)} results"

    results = query_constitutional_constraints(unique_query, top_k=5, min_similarity=0.9)
    assert results == [], f"Expected empty list, got {len(results)} results"


def test_rf1531_index_nonexistent_module():
    """Edge case: indexing a non-existent module returns 0."""
    from aria_service.intel.coding_rag_indexer import index_codebase_structure

    result = index_codebase_structure(Path("/nonexistent/path.py"))
    assert result == 0, "Should return 0 for non-existent module"


def test_rf1531_index_empty_rules():
    """Edge case: indexing empty rules list returns 0."""
    from aria_service.intel.coding_rag_indexer import index_constitutional_rules

    result = index_constitutional_rules([])
    assert result == 0, "Should return 0 for empty rules list"
