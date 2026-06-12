"""
RAG-Augmented Code Generator — improves fix quality via past-experience retrieval (R-F1531).

Uses the CodingRAGIndexer to retrieve relevant past fixes, known failures,
codebase structure, and constitutional constraints before generating new
code. This is the core of the ~23% success rate improvement: instead of
generating fixes from scratch, ARIA learns from what worked (and what
didn't) in previous R-numbers.

Integration:
  - Called from SovereignLLM.generate_fix() to augment the prompt
  - Called from self_coder.py to retrieve context before planning
  - All retrieval is sync (chromadb blocking) — callers in async contexts
    MUST wrap in asyncio.to_thread()
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import coding_rag_indexer as cri

logger = logging.getLogger("aria.rag_augmented_generator")

# R-F1531: wire module health to the brain on import
try:
    from .engine_wiring import wire_success as _ws1531
    _ws1531(
        module="rag_augmented_generator",
        summary="RAG-Augmented Generator active — fix memory retrieval ready",
        source_id="rag_augmented_generator:R-F1531",
    )
except Exception:
    pass


# ── Context building ─────────────────────────────────────────────────────────


def build_augmented_context(
    gap_type: str,
    module: str,
    title: str = "",
    error_type: str | None = None,
    codebase_context: str = "",
    top_k_fixes: int = 3,
    top_k_failures: int = 2,
    top_k_structure: int = 5,
    top_k_constraints: int = 3,
    min_similarity: float = 0.35,  # R-F1534: was unset (0.0) → every plan got irrelevant context injected
) -> dict:
    """Build an augmented context dict for LLM fix generation.

    Queries all four CodingRAG collections and returns structured context
    that the caller can inject into an LLM prompt. This is the primary
    integration point for SovereignLLM.

    Args:
        gap_type: The gap type (e.g. "module_bug", "missing_capability").
        module: The module name being fixed.
        title: Gap title for richer similarity matching.
        error_type: Specific error type for failure avoidance.
        codebase_context: Current codebase state text (optional).
        top_k_fixes: How many past fixes to retrieve.
        top_k_failures: How many past failures to retrieve.
        top_k_structure: How many structural chunks to retrieve.
        top_k_constraints: How many constitutional rules to retrieve.

    Returns:
        Dict with keys:
          - augmented_context (str): formatted prompt block
          - similar_fixes (list): raw fix results
          - known_failures (list): raw failure results
          - structural_context (list): raw structure results
          - constraints (list): raw constraint results
          - tokens_saved (int): estimated tokens saved vs reading files
          - retrieval_count (int): total documents retrieved
    """
    # Build query from gap data
    query_parts = [gap_type, module]
    if title:
        query_parts.append(title)
    query = " ".join(query_parts)

    # Retrieve from all four collections (R-F1534: apply the relevance floor on the
    # PRODUCTION path — chromadb always returns top_k, so without this every plan got
    # up to 13 chunks regardless of relevance, the opposite of the intended quality gain).
    similar_fixes = cri.query_relevant_fixes(query, top_k=top_k_fixes, min_similarity=min_similarity)
    known_failures = cri.query_known_failures(gap_type, error_type, top_k=top_k_failures, min_similarity=min_similarity)
    structural_context = cri.query_codebase_context(module, top_k=top_k_structure, min_similarity=min_similarity)
    constraints = cri.query_constitutional_constraints(
        f"modifying {module} {gap_type}",
        top_k=top_k_constraints,
        min_similarity=min_similarity,
    )

    # Build formatted text
    parts = []

    # 1. Previous successful fixes (highest value)
    if similar_fixes:
        parts.append("## RELEVANT PREVIOUS FIXES (Use these as templates):\n")
        for i, fix in enumerate(similar_fixes, 1):
            content = fix.get("content", "")
            sim = fix.get("similarity", 0)
            parts.append(f"### Successful Fix {i} (similarity: {sim:.2f})")
            parts.append(content[:800])
            parts.append("")

    # 2. Failures to avoid (prevents repeating mistakes)
    if known_failures:
        parts.append("## FAILURES TO AVOID (These approaches did NOT work):\n")
        for failure in known_failures:
            content = failure.get("content", "")
            parts.append(content[:500])
            parts.append("")

    # 3. Codebase structure (reduces file reads)
    if structural_context:
        parts.append("## CODEBASE STRUCTURE (Related modules):\n")
        for struct in structural_context:
            meta = struct.get("metadata", {})
            chunk_type = meta.get("chunk_type", "code")
            name = meta.get("name", "")
            content = struct.get("content", "")
            parts.append(f"### {chunk_type}: {name}")
            parts.append(content[:400])
            parts.append("")

    # 4. Constitutional constraints
    if constraints:
        parts.append("## CONSTITUTIONAL CONSTRAINTS (Must follow):\n")
        for constraint in constraints:
            rule = constraint.get("rule", "")
            parts.append(rule[:300])
            parts.append("")

    # 5. Current codebase state
    if codebase_context:
        parts.append("## CURRENT CODEBASE CONTEXT:\n")
        parts.append(codebase_context[:2000])

    augmented_text = "\n".join(parts)

    # Estimate tokens saved: each file read avoided ~3000 tokens,
    # each RAG retrieval ~400 tokens
    files_avoided = max(1, len(structural_context))
    tokens_saved = max(0, (files_avoided * 3000) - (len(structural_context) * 400))

    return {
        "augmented_context": augmented_text,
        "similar_fixes": similar_fixes,
        "known_failures": known_failures,
        "structural_context": structural_context,
        "constraints": constraints,
        "tokens_saved": tokens_saved,
        "retrieval_count": len(similar_fixes) + len(known_failures) + len(structural_context) + len(constraints),
    }


def build_fix_prompt_section(
    gap_type: str,
    module: str,
    title: str = "",
    error_type: str | None = None,
) -> str:
    """Build just the augmented context text (no metadata) for prompt injection.

    Convenience wrapper around build_augmented_context() that returns only
    the formatted text block. Use this when you just want to prepend context
    to an LLM prompt without handling the raw result dict.
    """
    result = build_augmented_context(
        gap_type=gap_type,
        module=module,
        title=title,
        error_type=error_type,
    )
    return result.get("augmented_context", "")
