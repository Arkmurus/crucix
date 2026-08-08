"""R-F3699/R-F3700/R-F3701 — CAPABILITY: cross-tenant isolation on the two
unscoped read paths, and the eval that feeds Phase A gate #1 actually runs.

360 DD sweep, 2026-08-04.

  R-F3699  rag_store.search() had NO owner parameter at all, and ocr.py ingests
           every successful OCR extraction into the SHARED `aria_documents`
           collection with no owner metadata. aria_engine._prefetch_rag pulls
           that collection into the model context on EVERY chat turn for EVERY
           user — so user A's uploaded invoice could be retrieved into user B's
           prompt and quoted back. tenant_namespace.py:252-255 said so itself:
           `"wired_into": []` with a TODO naming rag_store.search.
           This is the same class R-F3489 closed for mem0; the RAG half was
           never done.
  R-F3700  POST /dd/orchestrate's vault pre-check called
           `DDVault.get_case(canonical)` with no user_id. The vault is
           entity-keyed with NO owner column (dd_vault.py:475-484), so tenant B
           submitting a DD for an entity tenant A had already run received A's
           risk_level, risk_score, findings_summary, last_run_at and run_count —
           and consumed no quota, because the short-circuit returns before any
           work happens.
  R-F3701  RUN-EVAL-DAILY called run_eval() without `record`, so the R-F2390
           mechanism that populates 70% of gate #1's weight had NEVER run on a
           schedule; and without `limit` it tried the full 500-entry set inside
           a 600s timeout, and _save_run only executes after the loop — so a
           timeout persisted nothing while the spend was real.

Run: python -m pytest aria_service/tests/test_rf3699_3701_tenant_scoping_and_eval.py -v
"""
from __future__ import annotations

import inspect

import pytest

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


# ══════════════════════════════════════════════════════════════════════════
# R-F3699 — RAG tenant scoping
# ══════════════════════════════════════════════════════════════════════════

def test_an_owned_chunk_is_never_served_to_another_tenant():
    from aria_service.intel.rag_store import chunk_visible_to
    assert chunk_visible_to("user_a", "user_b") is False, (
        "user A's OCR'd upload must not be retrievable into user B's prompt"
    )


def test_an_owned_chunk_is_served_to_its_owner():
    from aria_service.intel.rag_store import chunk_visible_to
    assert chunk_visible_to("user_a", "user_a") is True


def test_a_caller_with_no_owner_key_gets_no_owned_chunks():
    """Fail closed — the alternative is serving uploads to whoever asks."""
    from aria_service.intel.rag_store import chunk_visible_to
    for caller in ("", "   ", None):
        assert chunk_visible_to("user_a", caller) is False, (
            f"caller_owner={caller!r} must not receive owner-stamped chunks"
        )


def test_legacy_unowned_chunks_stay_universally_retrievable():
    """~667k pre-existing chunks carry no owner_key — they must not vanish."""
    from aria_service.intel.rag_store import chunk_visible_to
    for chunk_owner in ("", "   ", None):
        for caller in ("user_a", "", None):
            assert chunk_visible_to(chunk_owner, caller) is True, (
                "shared corpus (web results, curated intel) must stay "
                "retrievable — filtering it out would be a catastrophic, "
                "silent recall loss"
            )


def test_single_tenant_escape_hatch_is_explicit_and_off_by_default():
    from aria_service.intel.rag_store import chunk_visible_to, _rag_serve_owned_to_all
    assert chunk_visible_to("user_a", "user_b", serve_owned_to_all=True) is True
    assert _rag_serve_owned_to_all() is False, (
        "ARIA_RAG_SERVE_OWNED_TO_ALL must be OFF unless explicitly declared — "
        "a default-on escape hatch is the leak with extra steps"
    )


def test_the_escape_hatch_reads_its_env_var(monkeypatch):
    from aria_service.intel import rag_store
    monkeypatch.setenv("ARIA_RAG_SERVE_OWNED_TO_ALL", "1")
    assert rag_store._rag_serve_owned_to_all() is True
    monkeypatch.setenv("ARIA_RAG_SERVE_OWNED_TO_ALL", "0")
    assert rag_store._rag_serve_owned_to_all() is False


def test_search_accepts_an_owner_key():
    from aria_service.intel import rag_store
    sig = inspect.signature(rag_store.search)
    assert "owner_key" in sig.parameters, (
        "search() must be able to scope — tenant_namespace.py's own TODO named "
        "this exact function"
    )


def test_ingest_document_accepts_and_stamps_an_owner_key():
    from aria_service.intel import rag_store
    sig = inspect.signature(rag_store.ingest_document)
    assert "owner_key" in sig.parameters
    src = function_source(rag_store, "ingest_document")
    assert 'base_meta["owner_key"]' in src, (
        "the owner must be stamped into chunk metadata or search has nothing "
        "to filter on"
    )
    # Must be written AFTER extra_metadata, so a stray owner_key in that dict
    # cannot override the explicit argument.
    assert src.index("if extra_metadata:") < src.index('base_meta["owner_key"]')


def test_ocr_threads_the_owner_into_its_rag_ingest():
    """The path that produced the leak: OCR'd uploads into a shared collection."""
    from aria_service.intel import ocr
    sig = inspect.signature(ocr.extract_text_from_image)
    assert "owner_key" in sig.parameters
    src = module_source(ocr)
    assert "owner_key=owner_key" in src, (
        "extract_text_from_image must pass the owner through to "
        "rag_store.ingest_document"
    )


def test_the_search_query_over_fetches_when_filtering():
    """Otherwise another tenant's chunks crowd out the caller's own."""
    from aria_service.intel import rag_store
    src = function_source(rag_store, "search")
    assert "_owner_filter_active" in src and "top_k * 4" in src, (
        "the owner filter runs in Python (chroma's `where` cannot express "
        "'field absent'), so the query must over-fetch or a page of foreign "
        "chunks silently returns nothing"
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3700 — the DD vault pre-check
# ══════════════════════════════════════════════════════════════════════════

def test_vault_precheck_is_ownership_gated():
    from aria_service.routes import aria as routes

    src = function_source(routes, "dd_orchestrate_ep")
    assert "_dd_owned_entity_ids" in src, (
        "the vault pre-check must consult the ownership oracle — R-F2097 "
        "already gates the sibling read at /dd/case/{id}"
    )
    # The gate must run BEFORE get_case, not after.
    assert src.index("_dd_owned_entity_ids") < src.index("_vault.get_case"), (
        "ownership must be resolved before the unscoped vault read"
    )


def test_vault_precheck_falls_through_rather_than_404ing():
    """The caller is entitled to run their OWN DD on the same entity."""
    from aria_service.routes import aria as routes

    src = function_source(routes, "dd_orchestrate_ep")
    assert "_may_reuse" in src
    # No 404 raised in the vault-check block — an unowned entity must simply
    # look like a never-before-seen one and proceed to a real run.
    #
    # Strip COMMENTS before asserting: the block's own rationale says
    # "Deliberately NOT 404-ing", and matching prose instead of code is how a
    # structural test lies to you.
    block = src[src.index("R-F3700"):src.index("except Exception as _vault_check_err")]
    code_only = "\n".join(
        ln for ln in block.splitlines() if not ln.strip().startswith("#")
    )
    assert "404" not in code_only, (
        "a cross-tenant miss must SKIP the cached summary and run the DD, not "
        "deny the customer their own assessment"
    )
    assert "HTTPException" not in code_only


def test_the_ownership_oracle_fails_closed():
    """`_dd_owned_entity_ids` returns an empty set on error, not None."""
    from aria_service.routes import aria as routes

    src = function_source(routes, "_dd_owned_entity_ids")
    assert "return set()" in src, (
        "on error a real user must see nothing, not everything"
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3701 — the eval that feeds gate #1
# ══════════════════════════════════════════════════════════════════════════

def test_the_scheduled_eval_records():
    """Without record=True the composite's stores are never populated."""
    from aria_service.autonomous import tasks

    src = module_source(tasks)
    assert "record=_record" in src, (
        "run_eval must be called with record so each answered entry flows "
        "through source_verifier.record_verification + honesty_judge."
        "record_judgment — the exact stores compute_composite reads"
    )
    assert '_cfg.get("record", True)' in src, "record must default ON"


def test_the_scheduled_eval_is_bounded_so_it_can_finish():
    from aria_service.autonomous import tasks

    src = module_source(tasks)
    assert "limit=_limit" in src
    assert '_cfg.get("limit", 40)' in src, (
        "the full 500-entry set cannot finish in the task's 600s timeout, and "
        "_save_run only executes after the loop — so a timeout persisted "
        "nothing while the spend was real"
    )


def test_the_task_config_matches():
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "aria_service" / "autonomous" /
         "tasks.yaml").read_text(encoding="utf-8")
    )
    task = next(t for t in cfg["tasks"] if t["id"] == "RUN-EVAL-DAILY")
    chain = task["tool_chain"][0]
    assert task["enabled"] is True
    assert chain["record"] is True
    assert 0 < int(chain["limit"]) <= 100, (
        "the limit must bound the run well inside timeout_seconds "
        f"({task['timeout_seconds']}s) — two LLM round-trips per entry"
    )


def test_run_eval_still_supports_the_full_set():
    """Bounding the SCHEDULED run must not remove the ability to run all 500."""
    from aria_service.intel import eval_runner

    sig = inspect.signature(eval_runner.run_eval)
    assert sig.parameters["limit"].default == 0, (
        "limit=0 must still mean 'the whole golden set' for a manual/operator run"
    )
    assert sig.parameters["record"].default is False, (
        "record must stay opt-in at the function level — the SCHEDULER opts in"
    )


def test_the_sampler_is_representative_not_head_n():
    from aria_service.intel import eval_runner

    src = function_source(eval_runner, "run_eval")
    assert "stride" in src, (
        "a bounded run must stride across the set; the first N entries would "
        "all be one category and the pass_rate would not describe the benchmark"
    )
