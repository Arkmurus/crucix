"""R-F927 — hermetic, non-hanging test environment.

The full `pytest aria_service/tests` run hung at ~45-49% (a TestClient
read-document request → `_semantic_index_queue._persist_all` →
`semantic_search._get_embedder()` → a 21s `import sentence_transformers` under
the embedder lock inside the index-queue worker thread, racing the in-flight
request → deadlock; pytest-timeout's thread method then `os._exit()`s the whole
run so the suite never completes).

NOTE: this hang is LOCAL-DEV ONLY. CI's "Test ARIA Python service" workflow runs
ONLY test_imports.py + test_lifespan_smoke.py with a MINIMAL dep set (no torch /
sentence-transformers), so it never hits this path — deploys are NOT gated on
the full suite. This conftest exists so a developer can run the full suite to
completion locally (and so the §16 baseline is measurable).

Fixes:
1. HuggingFace OFFLINE — the embedder load uses the local cache or fails fast to
   the TF-IDF fallback, never a network download.
2. ARIA_INDEX_QUEUE_DISABLED=1 — the background semantic-index queue (the
   worker that triggers the embedder load + lingers past the request) is the
   root of the deadlock. Disabling it makes `enqueue()` a no-op; search falls
   back to TF-IDF/Jaccard (the same degradation CI runs under). No background
   embed worker → no deadlock, no lingering task on TestClient shutdown.
3. Mirror CI's hermetic flags (ARIA_SEMANTIC_INDEX_BUILD=0, RAG backfill off).

All via setdefault so a developer can override any of them for an integration
run (e.g. export ARIA_INDEX_QUEUE_DISABLED=0 to exercise the real queue).
"""
import os

# Set BEFORE any test imports the embedder / index queue. conftest.py is
# imported by pytest ahead of test collection; these subsystems init lazily.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("ARIA_INDEX_QUEUE_DISABLED", "1")
os.environ.setdefault("ARIA_SEMANTIC_INDEX_BUILD", "0")
os.environ.setdefault("ARIA_RAG_BACKFILL_ENABLED", "false")
