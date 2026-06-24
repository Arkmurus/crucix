"""Verify the Python on Windows documentation was ingested into ARIA's RAG + knowledge."""
from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main() -> None:
    from aria_service.intel import rag_store, knowledge

    # ── Verify RAG ──────────────────────────────────────────────────────
    print("=== RAG Store Verification ===")
    # search() returns list[dict] with 'text', 'score', 'metadata' keys
    rag_results = await rag_store.search(
        "Python on Windows installer PATH environment variable",
        top_k=5,
    )
    results = rag_results
    print(f"RAG search returned {len(results)} results")
    print(f"RAG search returned {len(results)} results")
    for r in results[:3]:
        score = r.get("score", 0)
        text = r.get("text", "")[:150]
        print(f"  score={score:.3f}  {text}")

    # Check source metadata
    if results:
        meta = results[0].get("metadata", {})
        print(f"\nFirst result metadata:")
        print(f"  source: {meta.get('source', 'N/A')}")
        print(f"  source_type: {meta.get('source_type', 'N/A')}")
        print(f"  title: {meta.get('title', 'N/A')}")

    # ── Verify Knowledge Facts ──────────────────────────────────────────
    print("\n=== Knowledge Facts Verification ===")
    kb = await knowledge._load()
    facts = kb.get("facts", [])
    windows_facts = [
        f
        for f in facts
        if "python" in str(f.get("topic", "")).lower()
        and "windows" in str(f.get("topic", "")).lower()
    ]
    print(f"Knowledge facts about Python on Windows: {len(windows_facts)}")
    for f in windows_facts[:8]:
        topic = f.get("topic", "")[:100]
        action = f.get("action", "stored")
        print(f"  [{action}] {topic}")

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n=== Summary ===")
    rag_ok = len(results) > 0
    kb_ok = len(windows_facts) > 0
    print(f"RAG ingest: {'PASS' if rag_ok else 'FAIL'} ({len(results)} results for query)")
    print(f"Knowledge facts: {'PASS' if kb_ok else 'FAIL'} ({len(windows_facts)} facts)")
    print(f"Overall: {'PASS' if rag_ok and kb_ok else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
