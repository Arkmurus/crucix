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

# R-F950 (2026-05-28) — use the in-memory state backend for tests. The prod
# default is sqlite (ARIA_STATE_BACKEND=sqlite), but the sqlite backend only
# works after redis_store.connect() runs (the app lifespan does this at boot).
# test_imports.py exercises store round-trips (golden_autogen / web_atlas /
# source_validator / verified_intel reject+roundtrip) DIRECTLY, without the
# lifespan, so every set_json→get_json no-op'd to None and 8 tests failed —
# silently turning the CI deploy gate RED and forcing manual deploys all of
# 2026-05-27. The memory backend round-trips in-process with no connect() and
# no /data file, which is exactly what a hermetic CI run needs. setdefault so a
# developer can still run an integration suite against sqlite by exporting it.
os.environ.setdefault("ARIA_STATE_BACKEND", "memory")

# R-F1534: use a temp dir for RAG in tests so we don't pollute the prod
# chromadb store. _resolve_rag_path() reads ARIA_RAG_PATH at module import
# time, so this MUST be set before any test imports rag_store or
# coding_rag_indexer.
import tempfile as _tf
_rag_tmp = _tf.mkdtemp(prefix="aria_rag_test_")
os.environ.setdefault("ARIA_RAG_PATH", _rag_tmp)

# R-F3324: send the coder's verifiable-gold appends to a temp file.
#
# self_coder.py:1353 appends a training record per fix attempt, resolving
# ARIA_CODER_GOLD_PATH or falling back to data/aria_training/coder_verifiable_gold.jsonl
# — a TRACKED file. So every suite run dirtied the working tree with 7 synthetic
# records ("Fix gap: End-to-end test gap / A simulated bug for pipeline testing").
#
# Why that is not cosmetic:
#   * it silently poisons a TRAINING corpus with simulated fixtures, and this repo
#     trains on that data (§24 requires a pre-flight review of dataset quality, and
#     a cycle that would train on contaminated data is cancelled, not run)
#   * a permanently dirty tree makes `git status` useless for spotting real WIP,
#     and deploy.ps1 builds from the WORKING TREE, so test residue can reach an image
#   * it defeats test_rf3284_registry_must_be_committed-style cleanliness checks
#
# Same mechanism the RAG path above already uses (R-F1534), and setdefault so a
# deliberate integration run can still point it somewhere real.
_gold_tmp = _tf.mkdtemp(prefix="aria_coder_gold_test_")
os.environ.setdefault("ARIA_CODER_GOLD_PATH",
                      os.path.join(_gold_tmp, "coder_verifiable_gold.jsonl"))

# ── R-F3319: turn an un-mocked live network call from a HANG into a named failure
#
# Four separate suite blockers have now been diagnosed the hard way, and all four
# were the same defect: a unit test performing real network I/O it never intended.
#   R-F2812  un-mocked auto_register_all reaching live gov portals
#   R-F3298  a portal-DIGEST test driving live registration on 24 portals
#   R-F3307  a wrong diagnosis of this same family (reverted by R-F3314)
#   R-F3318  reading_session awaiting a live Semantic Scholar search
#
# Each cost hours, for one reason: the failure mode is a HANG, not an error.
# R-F3318's stack showed the block inside ssl.create_default_context() loading
# the Windows certificate store, which is why a request `timeout=` cannot save
# you: the block happens while the client is being CONSTRUCTED, before any
# request timeout applies. A hang looks identical to a slow test until
# pytest-timeout kills the whole process without printing a summary.
#
# OFF BY DEFAULT, deliberately. Enabling it globally would change the outcome of
# any test that currently reaches the network, and the first full-suite baseline
# (11,534 passed / 149 failed, 2026-07-28) was measured without it. Turning it on
# is a diagnostic action, not a silent policy change:
#
#     ARIA_TEST_BLOCK_NETWORK=1 python -m pytest aria_service/tests/... -q
#
# Loopback stays open so a local fixture server still works.
#
# KNOWN LIMIT, stated so nobody trusts a green run further than it deserves.
# This hooks socket.connect, so it catches a live REQUEST. It does NOT catch
# R-F3318's own block, which happened in ssl.create_default_context() while the
# httpx client was being CONSTRUCTED, before any connect(). Guarding that would
# mean intercepting SSL-context creation, which legitimate tests do when they
# build a client around a mock transport, so it is deliberately not done here.
# Verified honestly: the guard fires on a real connect to
# ("api.semanticscholar.org", 443) and allows loopback; it would NOT have caught
# R-F3318 unaided. Treat a clean run as "no live request", not "no live I/O".
if (os.getenv("ARIA_TEST_BLOCK_NETWORK", "") or "").strip() in ("1", "true", "yes", "on"):
    import socket as _socket

    _real_connect = _socket.socket.connect
    _real_connect_ex = _socket.socket.connect_ex

    def _is_local(addr) -> bool:
        try:
            host = addr[0] if isinstance(addr, tuple) else str(addr)
        except Exception:
            return False
        return str(host) in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")

    class LiveNetworkBlocked(RuntimeError):
        """An un-mocked outbound connection was attempted from a test."""

    def _blocked_connect(self, addr, *a, **kw):
        if _is_local(addr):
            return _real_connect(self, addr, *a, **kw)
        raise LiveNetworkBlocked(
            f"[R-F3319] a test attempted a LIVE outbound connection to {addr!r}. "
            "Unit tests must not do real network I/O: it hangs the run rather "
            "than failing it. Stub the call at its boundary "
            "(see R-F3298 and R-F3318 for the pattern)."
        )

    def _blocked_connect_ex(self, addr, *a, **kw):
        if _is_local(addr):
            return _real_connect_ex(self, addr, *a, **kw)
        raise LiveNetworkBlocked(
            f"[R-F3319] a test attempted a LIVE outbound connection to {addr!r}."
        )

    _socket.socket.connect = _blocked_connect
    _socket.socket.connect_ex = _blocked_connect_ex
