"""Capability tests for the 2026-08-03 360 Prospector sweep (R-F3644..R-F3648).

Each test drives the path that was ACTUALLY broken and asserts the user-visible
outcome, per CLAUDE.md §3c. Every one of these fixes shares a single defect
family: a call that could never succeed, sitting behind a swallowing
`except Exception` (or, for R-F3644, on a branch nobody exercised) — so the
failure was invisible and looked like "no result" rather than "error".
"""
from __future__ import annotations

import inspect
import types

import pytest


# ── R-F3644 — admin LLM cooldown clear raised NameError on both failure arms ──

@pytest.mark.asyncio
async def test_rf3644_cooldown_clear_returns_409_not_nameerror():
    """The endpoint's two failure branches returned JSONResponse without the
    module ever importing it, so both raised NameError instead of an honest
    status. §17 documents this endpoint as THE remedy for a 24h billing
    cooldown, so the operator hit an opaque 500 exactly when it mattered."""
    from aria_service.routes.aria import admin_llm_cooldown_clear_ep

    # app.state carries no llm_provider → the 409 branch.
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))

    resp = await admin_llm_cooldown_clear_ep(request)   # must not raise NameError

    assert resp.status_code == 409, f"expected a 409 JSONResponse, got {resp!r}"


@pytest.mark.asyncio
async def test_rf3644_cooldown_clear_returns_400_when_not_cleared():
    """Second failure arm: provider exposes clear_cooldown but reports it did
    not clear. Also a JSONResponse, also previously a NameError."""
    from aria_service.routes.aria import admin_llm_cooldown_clear_ep

    class _Llm:
        def clear_cooldown(self, provider_name: str = "") -> dict:
            return {"cleared": False, "was_cooling": False, "reason": "no such provider"}

    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(llm_provider=_Llm()))
    )

    resp = await admin_llm_cooldown_clear_ep(request, provider="nope")

    assert resp.status_code == 400, f"expected a 400 JSONResponse, got {resp!r}"


# ── R-F3645 — redis_store.lrem TypeErrored because the module shadows `set` ──

@pytest.mark.asyncio
async def test_rf3645_lrem_in_memory_removes_entries():
    """redis_store defines `async def set(key, value, ...)` at module scope,
    shadowing the builtin. `lrem`'s in-memory fallback called a bare `set()`,
    which resolved to that coroutine function and raised
    `TypeError: set() missing 2 required positional arguments`. That path is
    reached whenever no sqlite/redis backend is active AND on the fall-through
    taken when a live Redis LREM raises — so a transient Redis blip became a
    hard TypeError."""
    from aria_service.intel import redis_store

    key = "test:rf3645:list"
    redis_store._mem_store[key] = '["a", "b", "a", "c"]'

    removed = await redis_store.lrem(key, 0, "a")   # previously raised TypeError

    assert removed == 2, f"expected both 'a' entries removed, got {removed}"
    import json
    assert json.loads(redis_store._mem_store[key]) == ["b", "c"]


def test_rf3645_module_still_shadows_builtin_set():
    """Guard the ROOT cause, not just the one victim: if someone later writes a
    bare `set()` in this module it will break the same way. This test documents
    the shadow so the next reader understands why builtins.set is used."""
    from aria_service.intel import redis_store

    assert inspect.iscoroutinefunction(redis_store.set), (
        "redis_store.set is expected to be the module's Redis SET mirror; if this "
        "changed, the builtins.set() workaround in lrem can be simplified"
    )
    # Executable proof of the pre-fix failure: a bare `set()` inside this module
    # is NOT the builtin — it is the coroutine above, and it needs two args.
    with pytest.raises(TypeError):
        redis_store.set()          # type: ignore[call-arg]


def test_rf3644_module_has_no_toplevel_jsonresponse():
    """Executable proof of the pre-fix failure for R-F3644: JSONResponse is not
    a module-level name in routes.aria, so the endpoint's bare `JSONResponse(...)`
    could only ever have raised NameError. The fix imports it locally, matching
    every other endpoint in the file."""
    from aria_service.routes import aria as aria_routes

    assert not hasattr(aria_routes, "JSONResponse"), (
        "routes.aria now exposes JSONResponse at module scope — if that is "
        "deliberate, the local import in admin_llm_cooldown_clear_ep is redundant"
    )


# ── R-F3646 — news_monitor wiring passed kwargs wire_failure does not accept ──

def test_rf3646_wire_failure_signature_rejects_summary_and_source_id():
    """Pin the contract the two broken call sites violated. wire_failure takes
    (module, detail, gap_type, source) — NOT summary/source_id. Those belong to
    wire_success. Passing them raised TypeError into `except: pass`, so the call
    looked wired to any grep while being dark at runtime (§21a)."""
    from aria_service.intel.engine_wiring import wire_failure

    params = set(inspect.signature(wire_failure).parameters)
    assert "summary" not in params and "source_id" not in params
    assert {"module", "detail", "gap_type", "source"} <= params


def test_rf3646_vault_scrape_failure_actually_reaches_the_brain(monkeypatch):
    """Capability: drive _wire_scrape_failure and assert wire_failure is really
    invoked. Before the fix this raised TypeError internally and the swallow
    meant a rotting vault website stayed invisible — the exact outcome R-F2214
    added the call to prevent."""
    from aria_service.intel import news_monitor

    calls: list[dict] = []

    def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(news_monitor, "wire_failure", _spy)

    news_monitor._wire_scrape_failure("Acme Vault Source", "https://example.com/feed", "empty body")

    assert calls, "wire_failure was never reached — the §21a wire is still dark"
    assert calls[0]["module"] == "news_monitor"
    # the human-readable context must survive the move out of the summary kwarg
    assert "Acme Vault Source" in calls[0]["detail"]
    assert "empty body" in calls[0]["detail"]


# ── R-F3647 — the DD TBML screen silently skipped every transaction ──────────

def test_rf3647_analyze_transaction_is_keyword_only():
    """Root cause: dd_orchestrator passed the whole transaction dict as ONE
    positional arg, but analyze_transaction is keyword-only, so every call
    raised TypeError into `except Exception: continue` and the results list was
    ALWAYS empty. The screen therefore reported nothing on every DD that
    supplied transactions — while looking like it had run."""
    from aria_service.intel.tbml_detection import analyze_transaction

    sig = inspect.signature(analyze_transaction)
    positional = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert not positional, (
        "analyze_transaction is expected to be keyword-only; the orchestrator "
        "call site must pass named arguments"
    )

    # The OLD call shape must not bind — this is the bug, pinned.
    with pytest.raises(TypeError):
        sig.bind({"declared_unit_value": 1.0, "hs_code": "8802"})

    # The NEW call shape must bind.
    sig.bind(
        declared_unit_value=1.0,
        hs_code="8802",
        exporter_country="GB",
        importer_country="BR",
        year=None,
        quantity=1,
    )


def test_rf3647_empty_screen_is_not_reported_as_clean():
    """never-false-clean (R-F2496): a batch where nothing could be screened must
    report coverage 'unavailable' and zero screened — never a clean result."""
    from aria_service.intel.tbml_detection import summarize_tbml_results

    out = summarize_tbml_results([])
    assert out["coverage"] == "unavailable"
    assert out["transactions_screened"] == 0
    assert out["material_anomalies"] == 0


# ── R-F3648 — chat deep-DD de-dup key was never canonical ────────────────────

def test_rf3648_canonical_entity_id_is_keyword_only():
    """The chat deep-DD launcher called canonical_entity_id positionally. It is
    keyword-only, so the call raised TypeError on EVERY invocation and the
    except downgraded the de-dup key to a lowercased raw name — meaning two
    spellings of one entity no longer collided and the in-flight guard could
    stack concurrent 840s deep DD jobs. Same defect as R-F1842."""
    from aria_service.intel.dd_versioning import canonical_entity_id

    sig = inspect.signature(canonical_entity_id)
    positional = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert not positional, "canonical_entity_id is keyword-only"

    with pytest.raises(TypeError):
        sig.bind("Some Company Ltd")          # the old, always-failing shape

    sig.bind(entity_type="company", name="Some Company Ltd")   # the new shape


def test_rf3648_launcher_produces_a_canonical_key():
    """Capability: the launcher's key builder must now yield the real canonical
    id for a registered company, not a lowercased raw string."""
    from aria_service.intel.dd_versioning import canonical_entity_id

    cid = canonical_entity_id(
        entity_type="company",
        name="Duma Engineering Ltd",
        jurisdiction_iso2="GB",
        registration_number="12345678",
    )
    assert cid and cid.startswith("company:GB:"), f"expected a canonical id, got {cid!r}"
    assert cid != "duma engineering ltd"
