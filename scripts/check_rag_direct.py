"""Direct chromadb query to verify Python Windows docs are in RAG."""
from __future__ import annotations

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main() -> None:
    from aria_service.intel import rag_store

    ok = await rag_store._ensure_async()
    print(f"RAG ready: {ok}")

    docs = rag_store._documents_collection
    if docs is None:
        print("Documents collection is None")
        return

    # Check count
    print(f"Documents count: {docs.count()}")

    # Run query in a thread (chromadb is sync)
    def _query():
        t1 = time.time()
        qresults = docs.query(query_texts=["Python on Windows installer PATH environment variable"], n_results=5)
        print(f"Query took {time.time() - t1:.2f}s")
        return qresults

    qresults = await asyncio.wait_for(asyncio.to_thread(_query), timeout=120)

    ids = qresults.get("ids", [[]])[0]
    texts = qresults.get("documents", [[]])[0]
    dists = qresults.get("distances", [[]])[0]
    metas = qresults.get("metadatas", [[]])[0]

    print(f"Results: {len(ids)}")
    for i in range(len(ids)):
        source = metas[i].get("source", "N/A") if isinstance(metas[i], dict) else "N/A"
        print(f"  [{i}] dist={dists[i]:.3f}")
        print(f"       source: {source}")
        print(f"       text: {texts[i][:200]}")


if __name__ == "__main__":
    asyncio.run(main())
