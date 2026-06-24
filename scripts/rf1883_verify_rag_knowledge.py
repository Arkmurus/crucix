"""
R-F1883: 4-step verification that ARIA can retrieve and use Python on Windows knowledge.

Step 1 — RAG store: verify chunks exist with correct metadata
Step 2 — Semantic search: verify relevant chunks are retrieved for Windows-specific queries
Step 3 — Knowledge facts: verify facts are stored and retrievable
Step 4 — End-to-end: verify ARIA's query path can surface this knowledge
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def step1_rag_store() -> dict:
    """Verify RAG store has the Python Windows docs with correct metadata."""
    print("=" * 70)
    print("STEP 1: RAG Store — verify chunks exist with correct metadata")
    print("=" * 70)

    from aria_service.intel import rag_store

    ok = await rag_store._ensure_async()
    assert ok, "RAG store not ready"

    docs = rag_store._documents_collection
    assert docs is not None, "Documents collection is None"

    doc_count = docs.count()
    print(f"  Total documents in RAG: {doc_count}")
    assert doc_count >= 160, f"Expected >=160 docs, got {doc_count}"

    # Query for Python Windows chunks specifically
    def _query_source():
        r = docs.query(
            query_texts=["Python on Windows documentation"],
            n_results=20,
            where={"source": "docs.python.org:using/windows"},
        )
        return r

    r = await asyncio.wait_for(asyncio.to_thread(_query_source), timeout=120)
    ids = r.get("ids", [[]])[0]
    texts = r.get("documents", [[]])[0]
    metas = r.get("metadatas", [[]])[0]
    dists = r.get("distances", [[]])[0]

    print(f"  Chunks from docs.python.org:using/windows: {len(ids)}")
    assert len(ids) > 0, "No chunks found from the Python Windows docs source"

    # Verify metadata
    for i in range(min(3, len(ids))):
        meta = metas[i] if isinstance(metas[i], dict) else {}
        print(f"  Chunk {i}:")
        print(f"    source: {meta.get('source', 'MISSING')}")
        print(f"    source_type: {meta.get('source_type', 'MISSING')}")
        print(f"    title: {meta.get('title', 'MISSING')}")
        print(f"    topic: {meta.get('topic', 'MISSING')}")
        print(f"    dist: {dists[i]:.3f}")
        print(f"    preview: {texts[i][:120]}...")

    # Verify key metadata fields (chromadb stores metadata as dict per chunk)
    meta0 = metas[0] if isinstance(metas[0], dict) else {}
    source_val = meta0.get("source", "")
    print(f"\n  First chunk metadata: source={source_val}, source_type={meta0.get('source_type')}, topic={meta0.get('topic')}")
    # The source might be truncated or formatted differently in chromadb metadata
    assert "docs.python.org" in source_val, f"Expected 'docs.python.org' in source, got: {source_val}"

    print(f"\n  ✅ STEP 1 PASSED: {len(ids)} chunks with correct metadata")
    return {"chunks_found": len(ids), "total_docs": doc_count}


async def step2_semantic_search() -> dict:
    """Verify semantic search retrieves relevant chunks for Windows-specific queries."""
    print("\n" + "=" * 70)
    print("STEP 2: Semantic Search — verify relevant retrieval for Windows queries")
    print("=" * 70)

    from aria_service.intel import rag_store

    # Pre-warm: run one throwaway query to load the embedding model
    print("  Pre-warming embedding model...")
    _warm = await rag_store.search("Python Windows", top_k=1)
    print(f"  Model warmed ({len(_warm)} results from warmup)")

    queries = [
        "How do I set the PATH environment variable for Python on Windows?",
        "Python Launcher for Windows py.exe shebang line",
        "How to install Python using the Microsoft Store on Windows",
        "UTF-8 mode in Python on Windows",
        "Compiling Python from source on Windows",
        "Python as a Windows service",
        "Embedding Python in a Windows application",
        "PyWin32 module Windows API",
        "Windows Subsystem for Linux Python",
        "Python embeddable package Windows",
    ]

    results_summary = []
    for q in queries:
        t0 = time.time()
        results = await rag_store.search(q, top_k=3)
        elapsed = time.time() - t0

        # rag_store.search() returns flat dicts with 'source' at top level
        our_results = [
            r for r in results
            if r.get("source", "") == "docs.python.org:using/windows"
        ]
        results_summary.append({
            "query": q[:60],
            "total_results": len(results),
            "our_results": len(our_results),
            "top_score": our_results[0].get("score", 0) if our_results else 0,
            "elapsed_s": round(elapsed, 1),
        })

        status = "✅" if our_results else "❌"
        print(f"  {status} [{elapsed:.1f}s] {q[:70]}")
        if our_results:
            print(f"       → {len(our_results)} relevant chunks, top score={our_results[0].get('score', 0):.3f}")
            print(f"       → {our_results[0].get('text', '')[:150]}")
        else:
            if results:
                print(f"       → {len(results)} non-target results (scores: {[r.get('score',0) for r in results[:3]]})")
            else:
                print(f"       → NO results returned")

    passed = sum(1 for r in results_summary if r["our_results"] > 0)
    print(f"\n  Queries returning Python Windows docs: {passed}/10")
    assert passed >= 8, f"Only {passed}/10 queries returned relevant chunks"

    print(f"  ✅ STEP 2 PASSED: {passed}/10 queries retrieve relevant chunks")
    return {"queries_passed": passed, "total_queries": len(queries)}


async def step3_knowledge_facts() -> dict:
    """Verify knowledge facts are stored and retrievable."""
    print("\n" + "=" * 70)
    print("STEP 3: Knowledge Facts — verify facts are stored")
    print("=" * 70)

    from aria_service.intel import knowledge

    kb = await knowledge._load()
    facts = kb.get("facts", [])
    print(f"  Total knowledge facts: {len(facts)}")

    # Find Python Windows facts
    windows_facts = [
        f for f in facts
        if "python" in str(f.get("topic", "")).lower()
        and "windows" in str(f.get("topic", "")).lower()
    ]
    print(f"  Python on Windows facts: {len(windows_facts)}")
    assert len(windows_facts) > 0, "No Python Windows facts found"

    # Show the facts
    for f in windows_facts[:10]:
        topic = f.get("topic", "?")[:100]
        source = f.get("source", "?")
        confidence = f.get("confidence", "?")
        content_preview = (f.get("content") or "")[:100]
        print(f"  📌 [{confidence}] {topic}")
        print(f"       source: {source}")
        print(f"       content: {content_preview}...")

    # Verify the primary fact
    primary_facts = [f for f in windows_facts if "complete guide" in f.get("topic", "").lower()]
    assert len(primary_facts) > 0, "No primary 'complete guide' fact found"
    print(f"\n  Primary fact found: {primary_facts[0].get('topic', '?')}")
    print(f"  Content length: {len(primary_facts[0].get('content', ''))} chars")

    print(f"  ✅ STEP 3 PASSED: {len(windows_facts)} knowledge facts stored")
    return {"facts_found": len(windows_facts)}


async def step4_end_to_end() -> dict:
    """Verify ARIA's query path can surface this knowledge end-to-end.

    This tests the actual retrieval pipeline that ARIA uses at query time:
    rag_store.search() → LLM context injection.
    """
    print("\n" + "=" * 70)
    print("STEP 4: End-to-End — verify ARIA can surface this knowledge")
    print("=" * 70)

    from aria_service.intel import rag_store

    # Model already warmed from step 2

    # Simulate the kind of questions ARIA would get about Python on Windows
    test_questions = [
        {
            "question": "How do I install Python on Windows using the command line?",
            "expected_topics": ["install", "command", "full installer"],
        },
        {
            "question": "What is the Python Launcher for Windows and how do shebang lines work?",
            "expected_topics": ["launcher", "shebang", "py.exe"],
        },
        {
            "question": "How do I set environment variables for Python on Windows?",
            "expected_topics": ["PATH", "environment variable", "PYTHON"],
        },
        {
            "question": "Can I compile Python from source on Windows?",
            "expected_topics": ["compile", "build", "source"],
        },
        {
            "question": "How do I run Python as a Windows service?",
            "expected_topics": ["service", "Windows service"],
        },
    ]

    passed_questions = 0
    for tq in test_questions:
        q = tq["question"]
        results = await rag_store.search(q, top_k=5)

        # rag_store.search() returns flat dicts with 'source' at top level
        our_results = [
            r for r in results
            if r.get("source", "") == "docs.python.org:using/windows"
        ]

        if our_results:
            # Check if ANY of the top 3 results contain expected topics
            expected = tq["expected_topics"]
            found_topics = []
            for res in our_results[:3]:
                res_text = res.get("text", "").lower()
                for t in expected:
                    if t.lower() in res_text and t not in found_topics:
                        found_topics.append(t)

            if found_topics:
                passed_questions += 1
                print(f"  ✅ [{len(our_results)} chunks] {q[:70]}")
                print(f"       topics found: {found_topics}")
            else:
                print(f"  ⚠️  [{len(our_results)} chunks] {q[:70]}")
                print(f"       expected topics {expected} not found in top chunks")
                print(f"       top chunk starts: {our_results[0].get('text', '')[:150]}")
        else:
            print(f"  ❌ [0 chunks] {q[:70]}")

    print(f"\n  Questions with relevant knowledge: {passed_questions}/{len(test_questions)}")
    assert passed_questions >= 4, f"Only {passed_questions}/5 questions surfaced relevant knowledge"

    print(f"  ✅ STEP 4 PASSED: {passed_questions}/5 questions surface relevant knowledge")
    return {"questions_passed": passed_questions, "total_questions": len(test_questions)}


async def main() -> None:
    print("🔍 R-F1883: 4-Step Verification — Python on Windows RAG Knowledge\n")

    results = {}

    try:
        s1 = await step1_rag_store()
        results["step1"] = s1
    except Exception as e:
        print(f"\n  ❌ STEP 1 FAILED: {e}")
        results["step1"] = {"error": str(e)}

    try:
        s2 = await step2_semantic_search()
        results["step2"] = s2
    except Exception as e:
        print(f"\n  ❌ STEP 2 FAILED: {e}")
        results["step2"] = {"error": str(e)}

    try:
        s3 = await step3_knowledge_facts()
        results["step3"] = s3
    except Exception as e:
        print(f"\n  ❌ STEP 3 FAILED: {e}")
        results["step3"] = {"error": str(e)}

    try:
        s4 = await step4_end_to_end()
        results["step4"] = s4
    except Exception as e:
        print(f"\n  ❌ STEP 4 FAILED: {e}")
        results["step4"] = {"error": str(e)}

    # Summary
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    all_passed = True
    for step_name, result in results.items():
        if "error" in result:
            print(f"  ❌ {step_name}: FAILED — {result['error']}")
            all_passed = False
        else:
            print(f"  ✅ {step_name}: PASSED")
            for k, v in result.items():
                if k != "error":
                    print(f"       {k}: {v}")

    print(f"\n  Overall: {'✅ ALL PASSED' if all_passed else '❌ SOME FAILED'}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
