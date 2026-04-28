"""Regression guards for F68 / F70 / F71 / F72 (2026-04-28 batch 5).

F68 — LLM fallback HARD cooldown was in-process only, so every fly.io
restart re-probed the failed (billing) backend and burned 5 wasted
calls before the in-memory cooldown re-engaged. Now mirrored to Redis;
hydrate_from_redis() re-applies on startup.

F70 — `airtable sync network error for X:` logged with empty `str(e)`
for httpx.ReadTimeout, ending the line at the colon with no error
detail. Now always includes the exception type name.

F71 — neural_memory.detect_conflict fired same-value "conflicts"
(`existing=HIGH_RISK new=HIGH_RISK`) for entities like UN Security
Council whose neuron metadata legitimately contains both HIGH and LOW
risk signals. Mixed-signal existing now returns no conflict.

F72 — search_zero_results gap_detail dropped the `language` arg, so
the F66 (gap_type, detail) dedupe collapsed all language variants of
a query into one fingerprint and the first zero-language blocked the
next hour's gaps for the same query string.
"""
from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import patch


# ── F68: LLM cooldown Redis mirror ────────────────────────────────────────


def test_hard_cooldown_mirror_writes_to_redis():
    """When auth/billing failure trips the HARD cooldown, the cooldown
    must also be written to Redis so a restart can rehydrate it."""
    from aria_service.llm.fallback import FallbackProvider
    from aria_service.llm.provider import ProviderError

    # Fake provider with .name only — fallback never calls .complete here.
    class _P:
        name = "anthropic"
        is_configured = True

    redis_writes: dict[str, str] = {}

    async def fake_set(key, value, ex=None, keepttl=False):
        redis_writes[key] = value

    async def run():
        with patch("aria_service.intel.redis_store.set", side_effect=fake_set):
            fp = FallbackProvider([_P()])
            stats = fp._stats["anthropic"]
            err = ProviderError("anthropic", "billing / credit exhausted",
                                status=400, kind="billing", retryable=False)
            fp._record_failure(_P(), stats, err)
            # Let the create_task fire
            await asyncio.sleep(0.05)

    asyncio.run(run())

    assert "crucix:aria:llm:cooldown:anthropic" in redis_writes, (
        f"HARD cooldown was not mirrored to Redis. Writes seen: "
        f"{list(redis_writes.keys())}"
    )


def test_hydrate_from_redis_restores_cooldown():
    """On startup, hydrate_from_redis must read the persisted cooldown
    and apply it to in-memory _stats so _should_skip immediately returns
    True without needing a fresh failure to re-probe."""
    from aria_service.llm.fallback import FallbackProvider

    class _P:
        name = "anthropic"
        is_configured = True

    future_until = time.time() + 1500  # 25 min in the future

    async def fake_get(key):
        import json
        if key == "crucix:aria:llm:cooldown:anthropic":
            return json.dumps({"until": future_until, "kind": "billing"})
        return None

    async def run():
        with patch("aria_service.intel.redis_store.get", side_effect=fake_get):
            fp = FallbackProvider([_P()])
            n = await fp.hydrate_from_redis()
            return n, fp._stats["anthropic"]

    n, stats = asyncio.run(run())
    assert n == 1, f"expected 1 cooldown rehydrated, got {n}"
    assert stats["cooldown_until"] == future_until
    assert stats["last_kind"] == "billing"


# ── F70: airtable sync log format ─────────────────────────────────────────


def test_airtable_sync_logs_exception_type_on_empty_message(caplog, monkeypatch):
    """httpx.ReadTimeout has empty str(e); the sync_record path's network
    handler must still produce a log line carrying the type name so the
    operator can triage. Drives the real code path end-to-end with a
    patched httpx.AsyncClient that raises ReadTimeout(\"\")."""
    import httpx
    from aria_service.integrations import airtable_sync

    # Force "enabled" + return a minimal field set so sync_record reaches
    # the network call.
    monkeypatch.setattr(airtable_sync, "_is_enabled",
                        lambda: (True, "ok"))
    monkeypatch.setattr(airtable_sync, "_entry_to_fields",
                        lambda entry: {"Action ID": "abc123"})
    monkeypatch.setattr(airtable_sync, "_table_url",
                        lambda: "https://api.airtable.com/v0/app/Task")
    monkeypatch.setattr(airtable_sync, "_field_map_mode",
                        lambda: "full")
    monkeypatch.setattr(airtable_sync, "_headers",
                        lambda: {"Authorization": "Bearer test"})

    async def fake_post(self, url, headers=None, json=None):
        raise httpx.ReadTimeout("")

    async def fake_patch(self, url, headers=None, json=None):
        raise httpx.ReadTimeout("")

    async def run():
        with caplog.at_level(logging.WARNING, logger="aria.integrations.airtable"), \
             patch.object(httpx.AsyncClient, "post", new=fake_post), \
             patch.object(httpx.AsyncClient, "patch", new=fake_patch):
            await airtable_sync.sync_record({"action_id": "abc123"})

    asyncio.run(run())
    matching = [r for r in caplog.records
                if "airtable sync network error" in r.getMessage()]
    assert matching, "no airtable network error log emitted"
    msg = matching[0].getMessage()
    assert "ReadTimeout" in msg, (
        f"exception type name missing from log line: {msg!r}"
    )
    # Must NOT end with bare ': ' (the original F70 failure shape)
    assert not msg.rstrip().endswith(":"), (
        f"log line ends in a bare colon — the F70 failure shape: {msg!r}"
    )


# ── F71: neural memory same-value conflict guard ──────────────────────────


def test_mixed_existing_signals_do_not_fire_conflict():
    """When an existing entity's metadata contains BOTH HIGH and LOW
    risk signals (real case: UN Security Council, EU, CPLP), no clean
    assessment can be derived — return None instead of firing a
    same=same conflict."""
    from aria_service.intel import neural_memory as nm

    # Force the module to look loaded so detect_conflict doesn't bail.
    saved_loaded = nm._loaded
    nm._loaded = True
    nm._neurons["test:un-sc"] = {
        "id": "test:un-sc",
        "concept": "un security council",
        "label": "UN Security Council",
        # Metadata that contains both HIGH ("sanctioned") and LOW
        # ("verified") signals — a real shape from the production logs.
        "metadata": {
            # Mix of HIGH ('sanctioned', 'embargo') AND LOW ('verified',
            # 'compliant') — the real shape that produced same=same
            # conflicts for UN Security Council on 2026-04-28.
            "summary": "Verified UN body. Issues sanctioned and embargo "
                       "designations. Compliant with charter chapter VII.",
        },
        "source": "knowledge_seed",
        "confidence": "VERIFIED",
    }
    nm._edges.setdefault("test:un-sc", {})

    try:
        # New text containing only HIGH-risk signal — would have fired a
        # same=same conflict pre-fix.
        result = nm.detect_conflict(
            "UN Security Council",
            "Body imposing fresh sanctioned action against entities",
        )
    finally:
        del nm._neurons["test:un-sc"]
        nm._edges.pop("test:un-sc", None)
        nm._loaded = saved_loaded

    assert result is None, (
        f"mixed-signal existing entity should not produce a conflict, "
        f"got {result!r}"
    )


def test_genuine_opposite_signal_still_fires_conflict():
    """A real conflict — existing pure-LOW, new pure-HIGH — must still
    be detected. The fix must not over-relax."""
    from aria_service.intel import neural_memory as nm

    saved_loaded = nm._loaded
    nm._loaded = True
    # Entity name chosen to avoid colliding with the (substring) HIGH/LOW
    # risk signal sets — "Vega Industries" doesn't trip "clean" / "ghost"
    # / "shell" / similar matches just from its label.
    nm._neurons["test:vega"] = {
        "id": "test:vega",
        "concept": "vega industries",
        "label": "Vega Industries",
        "metadata": {"summary": "compliant verified reputable supplier"},
        "source": "manual",
        "confidence": "VERIFIED",
    }
    nm._edges.setdefault("test:vega", {})
    try:
        result = nm.detect_conflict(
            "Vega Industries",
            "Vega Industries has been sanctioned and added to the embargo list",
        )
    finally:
        del nm._neurons["test:vega"]
        nm._edges.pop("test:vega", None)
        nm._loaded = saved_loaded

    assert result is not None, "genuine opposite-signal conflict was missed"
    assert result["existing_assessment"] == "LOW_RISK"
    assert result["new_assessment"] == "HIGH_RISK"


# ── F72: search_zero_results gap detail includes language ────────────────


def test_opensanctions_search_rejects_prompt_fragment():
    """F73 2026-04-28: production trace 10:13:40 sent a system-prompt
    fragment as `q=` to /search/default and OpenSanctions returned 400.
    The same `_looks_like_entity_name` guard that gates `screen_with_aliases`
    must now also gate `_opensanctions_search`, so non-entity input
    never reaches the upstream API."""
    from aria_service.intel import sanctions

    # The exact (decoded) query from the production trace.
    bad_query = (
        "HIGH-STAKES and you are NOT 100% confident, state what you "
        "would need to confirm — do NOT fabricate verifiable facts "
        "(jurisdictions, financials"
    )

    sent_to_http: list = []

    async def fake_get(*args, **kwargs):
        sent_to_http.append(kwargs.get("params"))
        # Non-200 to make sure we'd notice if it slipped through
        class _Fake:
            status_code = 400
            def json(self): return {}
        return _Fake()

    async def run():
        # If the guard works, AsyncClient.get is never called.
        import httpx
        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            result = await sanctions._opensanctions_search(bad_query, limit=8)
        return result

    result = asyncio.run(run())
    assert result == [], (
        f"prompt-fragment search should return [] without hitting "
        f"OpenSanctions, got {result!r}"
    )
    assert sent_to_http == [], (
        f"prompt-fragment query reached OpenSanctions HTTP call: "
        f"{sent_to_http}"
    )


def test_all_emitted_gap_types_are_registered():
    """F74 2026-04-28: production was emitting `compliance_gate` (and
    11 other gap types) that weren't in VALID_GAP_TYPES, producing
    `Unknown gap type` warnings that the error_log_handler then mirrored
    into the error ledger as noise. Lock this in: every gap_type that
    aria_service code emits via gap_type=\"...\" must be a member of
    capability_gaps.VALID_GAP_TYPES, or the warning re-surfaces."""
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES

    # The set produced by `grep -rn 'gap_type=' aria_service/` against
    # production code, deduped. If a future commit adds a new gap_type
    # without registering it, this test fails until it's added.
    emitted_in_production = {
        "adversarial_critical_failure",
        "api_missing",
        "compliance_gate",
        "counterparty_risk_materialised",
        "defective_dd_run",
        "domain_ownership",
        "euc_critical_clauses_missing",
        "file_parse",
        "ghost_entity",
        "insufficient_public_intel",
        "knowledge_gap",
        "mistake",
        "paraphrase_violation",
        "pdf_parse_failure",
        "provider_disagreement",
        "registry_lookup",
        "rescreen_errors",
        "sanctions_hit",
        "source_auto_suspended",
        "source_validator_rejected",
        "stalled_cell",
        "timeout",
        "unverified_citation",
        "verified_contradiction",
    }
    unregistered = emitted_in_production - VALID_GAP_TYPES
    assert unregistered == set(), (
        f"these gap types are emitted in production but not registered "
        f"in VALID_GAP_TYPES — they will fire Unknown gap type warnings: "
        f"{sorted(unregistered)}"
    )


def test_zero_result_gap_detail_includes_language():
    """The gap_detail string passed to brain_hook.absorb must include
    the language code so each lang variant gets a distinct fingerprint
    under F66 dedupe. Without this, a Portuguese zero-result blocks the
    English variant from filing its own gap for the same query string."""
    import aria_service.intel.web_search as ws

    captured: dict = {}

    async def fake_absorb(**kwargs):
        captured.update(kwargs)

    async def fake_brave(*a, **kw):
        return []
    async def fake_searxng(*a, **kw):
        return []
    async def fake_google(*a, **kw):
        return []
    async def fake_academic(*a, **kw):
        return []

    async def run():
        with patch("aria_service.intel.brain_hook.absorb", side_effect=fake_absorb), \
             patch("aria_service.intel.web_search._search_brave", side_effect=fake_brave), \
             patch("aria_service.intel.web_search._search_searxng", side_effect=fake_searxng), \
             patch("aria_service.intel.web_search._search_google_news", side_effect=fake_google), \
             patch("aria_service.intel.web_search._search_academic", side_effect=fake_academic):
            await ws.search(
                "Angola Mozambique defesa aquisição 2026",
                language="pt-PT",
            )

    asyncio.run(run())
    assert captured.get("gap_type") == "search_zero_results"
    detail = captured.get("gap_detail") or ""
    assert "lang=pt-PT" in detail, (
        f"gap_detail must distinguish per-language. got: {detail!r}"
    )
