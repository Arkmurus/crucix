"""R-F927 — hermetic, non-hanging test environment.

The full `pytest aria_service/tests` run hung at ~45-49% (a TestClient
read-document request → `_semantic_index_queue._persist_all` →
`semantic_search._get_embedder()` → a 21s `import sentence_transformers` under
the embedder lock inside the index-queue worker thread, racing the in-flight
request → deadlock; pytest-timeout's thread method then `os._exit()`s the whole
run so the suite never completes).

NOTE: this hang is LOCAL-DEV ONLY. CI's "Test ARIA Python service" workflow
(test-aria.yml) runs ONLY test_imports.py + test_lifespan_smoke.py with a MINIMAL
dep set (no torch / sentence-transformers), so it never hits this path — deploys
are NOT gated on the full suite. This conftest exists so a developer can run the
full suite to completion locally (and so the §16 baseline is measurable).

R-F3378 CORRECTION: the paragraph above says "CI", and a reader (this one) took
it to mean CI never runs the suite at all. ci.yml is a SECOND workflow and it
does invoke `pytest aria_service/tests/` — but with the same minimal dep set, so
most of the suite errors on import there, and its result was piped into `tail`,
which discarded the exit code. That step is now explicitly ADVISORY. Separately,
ci.yml had been structurally invalid since R-F1073 (2026-05-29) — column-0 python
inside a YAML block scalar — so it failed in 0s on every push for two months and
ran nothing at all. Both are fixed; neither makes the full suite a deploy gate.
The suite's real gate is scripts/admin/suite_baseline.py (R-F3373).

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
# ── R-F3446: NOW ON BY DEFAULT, because the blast radius was finally MEASURED ──
#
# This was off by default for a stated and correct reason: enabling it globally changes the
# outcome of any test that reaches the network, and the first full-suite baseline
# (11,534 passed / 149 failed, 2026-07-28) was measured without it. That reason was
# evidence-shaped, so it could only be retired with evidence.
#
# The measurement (R-F3446): all 1,531 test files were run with the guard ENABLED, in
# eight segments, and every failure was diffed against the documented baseline rather than
# counted. EXACTLY FOUR tests were attributable to the guard, and all four were tests
# genuinely doing live network I/O:
#
#     test_cap__extract_entity_from_url::test_extract_entity_from_url_known_site   (R-F3440)
#     test_rf1010_learning::TestUniversalWebCrawler::test_fetch_page_success       (R-F3440)
#     test_rf541_spider_robots_txt::test_rf541_robots_disallow_skips_fetch         (R-F3444)
#     test_security_sprint::test_url_safety_allows_public_urls                     (R-F3444)
#
# All four are fixed by stubbing the resolution seam, so the current blast radius is ZERO.
# Every one of them got STRONGER in the process: the first stopped depending on a third
# party's page title, the third now actually reaches the robots logic it claims to test,
# and the fourth stopped depending on four real domains continuing to exist.
#
# WHY ON BY DEFAULT IS WORTH IT: R-F3439 proved live DNS is a root cause of the
# intermittent suite hang — a degraded resolver inflated a jurisdiction-STRING test to
# 91.68s, and pytest.ini's 120s timeout uses the THREAD method on Windows, which kills the
# PROCESS and prints no summary. An opt-in guard protects only whoever remembers to switch
# it on, which is nobody at the moment the hang actually happens.
#
# Opt OUT for a deliberately live run (a real smoke test against real endpoints):
#
#     ARIA_TEST_ALLOW_NETWORK=1 python -m pytest ...
#
# ARIA_TEST_BLOCK_NETWORK=1 still works and is now redundant; it is kept so existing
# invocations and CI lines do not break.
#
# Loopback stays open so a local fixture server still works.
#
# KNOWN LIMIT, stated so nobody trusts a green run further than it deserves.
# This hooks socket.connect/connect_ex and (since R-F3433) socket.getaddrinfo, so it
# catches a live REQUEST and a live RESOLUTION. It does NOT catch R-F3318's own
# block, which happened in ssl.create_default_context() while the httpx client was
# being CONSTRUCTED, before any connect(). Guarding that would mean intercepting
# SSL-context creation, which legitimate tests do when they build a client around a
# mock transport, so it is deliberately not done here. Verified honestly: the guard
# fires on a real connect to ("api.semanticscholar.org", 443) and allows loopback; it
# would NOT have caught R-F3318 unaided.
#
# R-F3433 — the DNS half was MISSING and that mattered. Measured over the 140-file DD
# set with this guard enabled: 25 live DNS lookups to 9 external hosts (OFSI, OFAC,
# UN, World Bank, SEC, Treasury, api.opensanctions.org, two cloud buckets) and ZERO
# connects. So the guard reported a clean run while the suite was still talking to
# the internet — the worst kind of green, because it was being used to rule live I/O
# OUT while chasing a hang. getaddrinfo is a blocking syscall with no application
# timeout, which is the same reason a request `timeout=` could not save R-F3318.
# Blast radius when enabled, measured over the DD set: exactly ONE test, fixed by
# R-F3433, so the diagnostic now runs clean there.
#
# The implementation lives in _net_block.py so it can be tested directly rather than
# only through its own side effects.
def _truthy(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


# R-F3446 — default ON. The opt-OUT is explicit and must be the only way to reach the live
# network from a test, so that doing so is always a deliberate, visible choice.
if not _truthy("ARIA_TEST_ALLOW_NETWORK"):
    from aria_service.tests import _net_block as _nb

    _nb.install()
    LiveNetworkBlocked = _nb.LiveNetworkBlocked


# ── R-F3449: reset the per-request auth ContextVar between tests ──────────────
#
# `routes/aria.py::require_aria_token` records whether the caller authenticated with the
# INTERNAL or the user-facing token, via `_auth_is_internal_var` (a ContextVar whose
# declared default is True, i.e. unrestricted — see its comment: "Defaults True
# (unrestricted) ONLY for callers that bypass auth entirely").
#
# THE LEAK. A test that sets ARIA_API_TOKEN and makes an authenticated request drives
# `_auth_is_internal_var.set(False)`. `monkeypatch` restores the ENV VAR afterwards but
# knows nothing about a ContextVar, and the value was set in the AMBIENT context rather
# than a per-request task, so the False outlives the test and every later test inherits it.
#
# Measured: `test_rf1566_auth_fail_closed.py` running first turns
#   test_rf1820_dd_report_ownership::test_dd_report_admin_no_filter_ok
#   test_rf2097_dd_vault_ownership::test_rf2097_dd_case_cross_tenant_404
#   test_rf2097_dd_vault_ownership::test_rf2097_dd_vault_search_filtered
# from green into "404 != 200" and "set() != {company_GB_OWNED}" — three of the fifteen
# order-dependent failures in the R-F3448 baseline. They pass alone and fail in-suite for
# exactly this reason.
#
# Fixed at the class rather than per test: no authenticating test can leak the flag now.
# Guarded on sys.modules so this does NOT import the (very large) routes module for tests
# that never touch it — if it was never imported, there is no ContextVar to leak.
import pytest  # R-F3449 — conftest had no pytest import; the fixture below needs it


@pytest.fixture(autouse=True)
def _reset_auth_internal_contextvar():
    import sys as _sys

    mod = _sys.modules.get("aria_service.routes.aria")
    var = getattr(mod, "_auth_is_internal_var", None) if mod is not None else None
    if var is not None:
        # R-F3628 — DERIVE the default, never mirror it. This was `var.set(True)`
        # commented "the declared default", i.e. a hardcoded copy of
        # routes/aria.py's value. The copy wins over the declaration, so flipping the
        # real default would have left the whole suite forced to the old value and the
        # change invisible to its own tests. Absent constant => fail closed.
        var.set(getattr(mod, "_AUTH_INTERNAL_DEFAULT", False))
    yield


# ── R-F3449: no test may inherit another test's TRIPPED circuit breaker ───────
#
# `intel/circuit_breaker.py` keeps a process-global `_breakers` registry. A backend that
# fails repeatedly during one test leaves its breaker OPEN for the rest of the session, and
# every guarded call site then SHORT-CIRCUITS instead of running:
#
#     _cb = get_breaker("bing_news"); if _cb.is_open(): return []   # web_search.py:1140-43
#
# So a later test asserting "a backend error wires a failure to the brain" sees the
# fail-open [] it expects but NO wire_failure, because httpx was never reached. Measured as
# two of the fifteen order-dependent failures in the R-F3448 baseline:
#     test_rf1614_make_loud::test_rf1614_bing_news_backend_error_wires_failure
#     test_rf1614_make_loud::test_rf1614_google_news_backend_error_wires_failure
# Both pass alone (fresh breaker, CLOSED) and fail in-suite (inherited OPEN).
#
# Reset IN PLACE rather than clearing the registry: some modules capture a breaker
# reference at import time, and a cleared dict would hand out a NEW object while the old
# one stayed open behind that reference. Mutating the existing objects fixes both paths.
#
# A test that deliberately trips a breaker within itself is unaffected — it trips it during
# the test. What is no longer possible is INHERITING one.
@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    import sys as _sys

    cb = _sys.modules.get("aria_service.intel.circuit_breaker")
    reg = getattr(cb, "_breakers", None) if cb is not None else None
    if isinstance(reg, dict):
        for _b in reg.values():
            try:
                _b.state = "CLOSED"
                _b.consecutive_failures = 0
            except Exception:
                pass
    yield


# ── R-F4162 (C-181) — the DD vault is a MACHINE-GLOBAL file; isolate it ─────────
#
# `dd_vault` resolves its database to `${ARIA_DATA_DIR:-/data}/dd_vault.db`. On a
# dev box that is `C:\data\dd_vault.db` (or `/data/dd_vault.db` on Linux) —
# OUTSIDE the repo, shared by every test run on the machine, and never cleaned.
#
# `dd_orchestrator.list_reports` reconciles the volatile index against that vault
# on EVERY read (R-F1973 / R-F2485 / R-F2652), so any DD list test silently
# merges whatever earlier runs left behind. Measured 2026-08-18: six residual
# fixture rows (`Risky Business SARL`, `dd_test_red`, ...) turned
# `test_rf2407_dd_rerun_unnamed` red on this box while it stayed green in CI —
# and the §16 baseline could not see it, because the baseline machine's copy
# happened to be empty. A suite whose verdict depends on the machine's history
# is not a suite.
#
# C-179 fixed that one test by stubbing `get_vault`. This is the class fix, in
# the spirit of the R-F3449 note above: "fixed at the class rather than per
# test".
#
# WHY NOT SET `ARIA_DATA_DIR`. That variable is shared with `agent_registry`,
# `agent_contract`, `brave_distill` and `brave_student`, all of which read it at
# IMPORT time — setting it suite-wide would relocate their databases too, a far
# larger blast radius than the problem. Patching the vault's own module globals
# touches exactly the store at fault.
#
# SESSION-scoped, not per-test, deliberately: tests that write in one test and
# read in another within a run keep working exactly as before. What changes is
# that the file is a throwaway created per run, so nothing leaks between runs or
# out onto the machine. The singleton is reset because `get_vault()` caches one
# (`_vault_instance`), so repointing the path alone would hand back a handle to
# the old database.
@pytest.fixture(scope="session", autouse=True)
def _isolate_dd_vault(tmp_path_factory):
    try:
        from aria_service.intel import dd_vault
    except Exception:
        yield
        return

    tmp = tmp_path_factory.mktemp("dd_vault")
    orig_db = getattr(dd_vault, "_VAULT_DB", None)
    orig_dir = getattr(dd_vault, "_VAULT_DIR", None)
    orig_instance = getattr(dd_vault, "_vault_instance", None)
    try:
        dd_vault._VAULT_DIR = tmp
        dd_vault._VAULT_DB = tmp / "dd_vault.db"
        dd_vault._vault_instance = None      # get_vault() caches; force a rebuild
        yield
    finally:
        if orig_db is not None:
            dd_vault._VAULT_DB = orig_db
        if orig_dir is not None:
            dd_vault._VAULT_DIR = orig_dir
        dd_vault._vault_instance = orig_instance
