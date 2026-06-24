"""
R-F1888: 4-step verification that ARIA can retrieve and use PowerShell knowledge.

Step 1 — RAG store: verify PowerShell chunks exist with correct metadata
Step 2 — Semantic search: verify relevant chunks retrieved for PowerShell queries
Step 3 — Knowledge facts: verify facts are stored and retrievable
Step 4 — End-to-end: verify ARIA's query path can surface this knowledge
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def step1_rag_store() -> dict:
    """Verify RAG store has PowerShell docs with correct metadata."""
    print("=" * 70)
    print("STEP 1: RAG Store — verify PowerShell chunks exist with metadata")
    print("=" * 70)

    from aria_service.intel import rag_store

    ok = await rag_store._ensure_async()
    assert ok, "RAG store not ready"

    docs = rag_store._documents_collection
    doc_count = docs.count()
    print(f"  Total documents in RAG: {doc_count}")

    # Get all docs and count PowerShell ones
    r = docs.get(limit=10000)
    metas = r.get("metadatas", [])
    pwsh_metas = [m for m in metas if isinstance(m, dict) and m.get("technology") == "powershell"]
    print(f"  PowerShell documents: {len(pwsh_metas)}")
    assert len(pwsh_metas) > 100, f"Expected >100 PowerShell docs, got {len(pwsh_metas)}"

    # Show unique topics
    topics = set()
    for m in pwsh_metas:
        t = m.get("topic", "")
        if t:
            topics.add(t)
    print(f"  PowerShell topics covered: {len(topics)}")
    for t in sorted(topics)[:15]:
        print(f"    - {t}")

    # Verify metadata
    sample = pwsh_metas[0]
    assert "learn.microsoft.com" in sample.get("source", ""), f"Wrong source: {sample.get('source')}"
    assert sample.get("source_type") == "article", f"Wrong source_type: {sample.get('source_type')}"
    assert sample.get("technology") == "powershell", f"Wrong technology: {sample.get('technology')}"

    print(f"\n  ✅ STEP 1 PASSED: {len(pwsh_metas)} PowerShell chunks, {len(topics)} topics")
    return {"chunks_found": len(pwsh_metas), "topics": len(topics)}


async def step2_semantic_search() -> dict:
    """Verify semantic search retrieves relevant PowerShell chunks."""
    print("\n" + "=" * 70)
    print("STEP 2: Semantic Search — verify relevant retrieval for PowerShell queries")
    print("=" * 70)

    from aria_service.intel import rag_store

    # Warm model
    _ = await rag_store.search("PowerShell test", top_k=1)

    queries = [
        "PowerShell cmdlet syntax and parameters",
        "PowerShell pipeline object passing",
        "PowerShell remoting WinRM Enter-PSSession",
        "PowerShell execution policy Restricted RemoteSigned",
        "PowerShell variables automatic preference scopes",
        "PowerShell advanced functions CmdletBinding",
        "PowerShell error handling try catch trap",
        "PowerShell modules manifests PowerShellGet",
        "PowerShell DSC Desired State Configuration",
        "PowerShell providers registry file system certificate",
        "PowerShell jobs background runspaces Start-Job",
        "PowerShell script signing Authenticode signature",
        "PowerShell profiles PROFILE loading order",
        "PowerShell .NET interop Add-Type C# code",
        "PowerShell arrays hashtables collections",
        "PowerShell regular expressions match replace",
        "PowerShell switch statement regex wildcard",
        "PowerShell comparison operators equality matching",
        "PowerShell operators arithmetic comparison logical",
        "PowerShell language keywords syntax",
    ]

    passed = 0
    for q in queries:
        t0 = time.time()
        results = await rag_store.search(q, top_k=3)
        elapsed = time.time() - t0

        our_results = [
            r
            for r in results
            if "learn.microsoft.com" in r.get("source", "")
            and "powershell" in r.get("source", "")
        ]
        if our_results:
            passed += 1
            print(f"  ✅ [{elapsed:.1f}s] {q[:65]}")
            print(f"       {len(our_results)} chunks, top score={our_results[0].get('score', 0):.3f}")
        else:
            print(f"  ❌ [{elapsed:.1f}s] {q[:65]}")
            if results:
                print(f"       {len(results)} non-target results")

    print(f"\n  Queries returning PowerShell docs: {passed}/{len(queries)}")
    assert passed >= 18, f"Only {passed}/20 queries returned relevant chunks"

    print(f"  ✅ STEP 2 PASSED: {passed}/20 queries retrieve relevant chunks")
    return {"queries_passed": passed, "total_queries": len(queries)}


async def step3_knowledge_facts() -> dict:
    """Verify knowledge facts are stored and retrievable."""
    print("\n" + "=" * 70)
    print("STEP 3: Knowledge Facts — verify PowerShell facts are stored")
    print("=" * 70)

    from aria_service.intel import knowledge

    kb = await knowledge._load()
    facts = kb.get("facts", [])
    print(f"  Total knowledge facts: {len(facts)}")

    pwsh_facts = [
        f
        for f in facts
        if "powershell" in str(f.get("topic", "")).lower()
        and "learn.microsoft.com" in str(f.get("source", ""))
    ]
    print(f"  PowerShell documentation facts: {len(pwsh_facts)}")
    assert len(pwsh_facts) > 10, f"Expected >10 PowerShell facts, got {len(pwsh_facts)}"

    # Show the facts
    for f in pwsh_facts[:12]:
        topic = f.get("topic", "?")[:100]
        source = f.get("source", "?")
        confidence = f.get("confidence", "?")
        content_len = len(f.get("content", ""))
        print(f"  📌 [{confidence}] {topic}")
        print(f"       source: {source} ({content_len} chars)")

    print(f"\n  ✅ STEP 3 PASSED: {len(pwsh_facts)} PowerShell knowledge facts")
    return {"facts_found": len(pwsh_facts)}


async def step4_end_to_end() -> dict:
    """Verify ARIA's query path can surface PowerShell knowledge end-to-end."""
    print("\n" + "=" * 70)
    print("STEP 4: End-to-End — verify ARIA can surface PowerShell knowledge")
    print("=" * 70)

    from aria_service.intel import rag_store

    test_questions = [
        {
            "question": "How do I install PowerShell on Windows using winget or MSI?",
            "expected_topics": ["install", "winget", "msi"],
        },
        {
            "question": "What are PowerShell execution policies and how do I set them?",
            "expected_topics": ["execution policy", "restricted", "remotesigned"],
        },
        {
            "question": "How do PowerShell advanced functions work with CmdletBinding?",
            "expected_topics": ["advanced function", "cmdletbinding", "parameter"],
        },
        {
            "question": "How does PowerShell remoting work with Enter-PSSession?",
            "expected_topics": ["remoting", "enter-pssession", "winrm"],
        },
        {
            "question": "How do I handle errors in PowerShell with try/catch?",
            "expected_topics": ["try", "catch", "error"],
        },
        {
            "question": "What PowerShell providers exist for registry and file system?",
            "expected_topics": ["provider", "registry", "filesystem"],
        },
        {
            "question": "How do PowerShell modules and manifests work?",
            "expected_topics": ["module", "manifest", "psmoduleinfo"],
        },
        {
            "question": "How do I use PowerShell DSC for configuration management?",
            "expected_topics": ["dsc", "desired state", "configuration"],
        },
    ]

    passed = 0
    for tq in test_questions:
        q = tq["question"]
        results = await rag_store.search(q, top_k=5)

        our_results = [
            r
            for r in results
            if "learn.microsoft.com" in r.get("source", "")
            and "powershell" in r.get("source", "")
        ]

        if our_results:
            expected = tq["expected_topics"]
            found_topics = []
            for res in our_results[:3]:
                res_text = res.get("text", "").lower()
                for t in expected:
                    if t.lower() in res_text and t not in found_topics:
                        found_topics.append(t)

            if found_topics:
                passed += 1
                print(f"  ✅ [{len(our_results)} chunks] {q[:65]}")
                print(f"       topics found: {found_topics}")
            else:
                print(f"  ⚠️  [{len(our_results)} chunks] {q[:65]}")
                print(f"       expected {expected} not in top chunks")
        else:
            print(f"  ❌ [0 chunks] {q[:65]}")

    print(f"\n  Questions with relevant knowledge: {passed}/{len(test_questions)}")
    assert passed >= 7, f"Only {passed}/8 questions surfaced relevant knowledge"

    print(f"  ✅ STEP 4 PASSED: {passed}/8 questions surface relevant knowledge")
    return {"questions_passed": passed, "total_questions": len(test_questions)}


async def main() -> None:
    print("🔍 R-F1888: 4-Step Verification — PowerShell RAG Knowledge\n")

    results = {}

    for step_name, step_fn in [
        ("step1", step1_rag_store),
        ("step2", step2_semantic_search),
        ("step3", step3_knowledge_facts),
        ("step4", step4_end_to_end),
    ]:
        try:
            r = await step_fn()
            results[step_name] = r
        except Exception as e:
            print(f"\n  ❌ {step_name} FAILED: {e}")
            results[step_name] = {"error": str(e)}

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
