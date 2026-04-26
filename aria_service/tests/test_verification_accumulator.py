"""Tests for the 2026-04-26 cross-sweep verification accumulator
(angle a).

Closes the residual training-pipeline starvation: claims that are
1-source on first appearance stay below the grounded threshold forever,
so the audit row stays `well_formed` and the training pipeline misses
it. The accumulator queues these entries and re-evaluates them against
the current verified_intel snapshot at training-export time. Once
2+-source corroboration arrives, the queue entry is upgraded to
`grounded` and the training filter accepts it.
"""
from __future__ import annotations

import asyncio
import json


def _run(coro):
    return asyncio.run(coro)


def _setup_fake_redis(monkeypatch, initial: dict | None = None):
    """In-process redis_store stub so the accumulator queue is testable
    without a live Redis."""
    from aria_service.intel import redis_store as rs

    store: dict[str, str] = {}
    if initial:
        for k, v in initial.items():
            store[k] = json.dumps(v, default=str)

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ex=None):
        store[key] = value

    async def fake_get_json(key):
        v = store.get(key)
        return json.loads(v) if v else None

    async def fake_set_json(key, obj, ex=None):
        store[key] = json.dumps(obj, default=str)

    async def fake_scan_keys(pattern, count=200):
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in store.keys() if k.startswith(prefix)]
        return [k for k in store.keys() if k == pattern]

    monkeypatch.setattr(rs, "get", fake_get)
    monkeypatch.setattr(rs, "set", fake_set)
    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    monkeypatch.setattr(rs, "scan_keys", fake_scan_keys)
    return store


def test_enqueue_skips_grounded_entries(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    _run(va.enqueue_for_reconcile(
        response_hash="h1",
        claims=["[CONFIRMED] Iran sanctioned a major shipping firm yesterday."],
        original_status="grounded",  # already grounded — must NOT enqueue
        audit_timestamp="2026-04-26T10:00:00Z",
    ))
    assert _run(va.get_status("h1")) is None


def test_enqueue_skips_no_claims_entries(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    _run(va.enqueue_for_reconcile(
        response_hash="h2",
        claims=[],
        original_status="well_formed",
        audit_timestamp="2026-04-26T10:00:00Z",
    ))
    assert _run(va.get_status("h2")) is None


def test_enqueue_persists_well_formed_entry(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    _run(va.enqueue_for_reconcile(
        response_hash="h3",
        claims=[
            "Iran sanctioned a major shipping firm yesterday under SDN program.",
            "OFAC published new entity designations for Tehran-linked individuals.",
            "EU CFSP decision adopted parallel sanctions on five named entities.",
        ],
        original_status="well_formed",
        audit_timestamp="2026-04-26T10:00:00Z",
    ))
    entry = _run(va.get_status("h3"))
    assert entry is not None
    assert entry["original_status"] == "well_formed"
    assert entry["current_status"] == "well_formed"
    assert len(entry["claims"]) == 3
    assert entry["upgraded_at"] is None


def test_reconcile_upgrades_when_corroboration_arrives(monkeypatch):
    """The core promise: a claim that was 1-source at recording time
    gets 2+-source corroboration in a later sweep, and reconcile()
    upgrades the queue entry to `grounded`."""
    store = _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    # Step 1 — enqueue with thin sources (well_formed).
    _run(va.enqueue_for_reconcile(
        response_hash="h-upg",
        claims=[
            "Tehran shipping firm sanctioned by OFAC yesterday designation",
            "EU CFSP parallel sanctions Tehran shipping",
        ],
        original_status="well_formed",
        audit_timestamp="2026-04-26T10:00:00Z",
    ))

    # Step 2 — simulate later sweep populating verified_intel with 2+
    # corroborating facts per claim.
    facts = [
        {"claim": "tehran shipping firm sanctioned ofac yesterday",
         "entity_name": "OFAC"},
        {"claim": "tehran shipping firm designation under sdn program",
         "entity_name": "OFAC SDN"},
        {"claim": "eu cfsp parallel sanctions tehran shipping firms",
         "entity_name": "EU"},
        {"claim": "european council parallel sanctions tehran shipping",
         "entity_name": "European Council"},
    ]
    store["crucix:verified_intel:facts"] = json.dumps(facts)

    # Step 3 — reconcile.
    diag = _run(va.reconcile())
    assert diag["scanned"] >= 1
    assert diag["upgraded"] == 1, f"expected upgrade, diag={diag}"

    entry = _run(va.get_status("h-upg"))
    assert entry["current_status"] == "grounded"
    assert entry["upgraded_at"] is not None
    assert entry["upgraded_grounded_rate"] >= 0.40


def test_reconcile_keeps_pending_when_corroboration_missing(monkeypatch):
    """Until verified_intel actually carries 2+ matches, the entry
    stays pending. checks_run increments so we can see reconcile is
    actually visiting the entry."""
    store = _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    _run(va.enqueue_for_reconcile(
        response_hash="h-stay",
        claims=["Tehran shipping firm sanctioned yesterday."],
        original_status="well_formed",
        audit_timestamp="2026-04-26T10:00:00Z",
    ))
    # Empty verified_intel snapshot — no matches possible.
    store["crucix:verified_intel:facts"] = json.dumps([])

    diag = _run(va.reconcile())
    assert diag["upgraded"] == 0
    assert diag["still_pending"] == 1

    entry = _run(va.get_status("h-stay"))
    assert entry["current_status"] == "well_formed"
    assert entry["checks_run"] == 1
    assert entry["last_check"] > 0


def test_reconcile_handles_empty_queue(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    diag = _run(va.reconcile())
    assert diag["scanned"] == 0
    assert diag["upgraded"] == 0
    assert diag["still_pending"] == 0
    assert diag["errors"] == 0


def test_stats_reports_pending_and_upgraded_counts(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import verification_accumulator as va

    # Two pending entries, one of which we'll mark upgraded by hand.
    _run(va.enqueue_for_reconcile(
        response_hash="hA",
        claims=["claim a one source body text"],
        original_status="well_formed",
        audit_timestamp="t1",
    ))
    _run(va.enqueue_for_reconcile(
        response_hash="hB",
        claims=["claim b one source body text"],
        original_status="unverified",
        audit_timestamp="t2",
    ))

    # Mark hA upgraded by mutating its stored entry directly.
    from aria_service.intel import redis_store as rs
    entry = _run(rs.get_json("crucix:verification:accumulator:hA"))
    entry["current_status"] = "grounded"
    entry["upgraded_at"] = 1.0
    _run(rs.set_json("crucix:verification:accumulator:hA", entry))

    s = _run(va.stats())
    assert s["pending"] == 1
    assert s["upgraded"] == 1


def test_training_filter_consults_accumulator_for_upgrades(monkeypatch):
    """End-to-end: an audit entry recorded as `well_formed` whose
    accumulator queue says `grounded` must be accepted by the training
    filter as a `grounded` entry."""
    store = _setup_fake_redis(monkeypatch)
    from aria_service.intel import chat_audit_log as cal
    from aria_service.intel import verification_accumulator as va
    from aria_service.learning import training_export as te

    long_reply = (
        "[CONFIRMED] Iran sanctions update from OFAC yesterday's release. "
        "[ASSESSED] EU adopted parallel sanctions under CFSP framework. "
        "[PROBABLE] Coordination at G7 level continues this quarter. "
    ) * 5

    response_hash_val = "abc123ef"

    async def fake_get_recent(limit: int = 500):
        return [
            {
                "user_message": "What are the latest Iran sanctions?",
                "response": long_reply,
                "verification_status": "well_formed",
                "grounded_rate": 0.0,
                "response_hash": response_hash_val,
                "trace_id": "trace-acc-1",
            },
        ]

    monkeypatch.setattr(cal, "get_recent", fake_get_recent)

    # Inject an accumulator entry as if the reconciler had already
    # upgraded this audit row.
    upgraded_entry = {
        "response_hash": response_hash_val,
        "audit_timestamp": "t",
        "claims": ["x"],
        "original_status": "well_formed",
        "current_status": "grounded",
        "queued_at": 1.0,
        "last_check": 2.0,
        "checks_run": 1,
        "upgraded_at": 2.0,
        "upgraded_grounded_rate": 0.6,
    }
    from aria_service.intel import redis_store as rs
    _run(rs.set_json(
        f"crucix:verification:accumulator:{response_hash_val}",
        upgraded_entry,
    ))

    out = _run(te._collect_chat_turns(days=7))
    assert len(out) == 1, "upgraded entry must be accepted as grounded"
    assert out[0]["meta"]["verification_tier"] == "upgraded"
    diag = te._last_collection_diag.get("chat_turns")
    assert diag.get("kept_by_tier", {}).get("upgraded") == 1


def test_training_filter_unaffected_when_accumulator_silent(monkeypatch):
    """When the accumulator has no entry for a response_hash (already
    grounded, or aged out), the training filter falls back to the
    audit row's own verification_status. well_formed continues to be
    accepted as well_formed."""
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import chat_audit_log as cal
    from aria_service.learning import training_export as te

    long_reply = (
        "[ASSESSED] Test claim with substantial body of detail. " * 20
    )

    async def fake_get_recent(limit: int = 500):
        return [
            {
                "user_message": "test",
                "response": long_reply,
                "verification_status": "well_formed",
                "grounded_rate": 0.0,
                "response_hash": "no-accumulator-entry",
                "trace_id": "wf-fallback",
            },
        ]

    monkeypatch.setattr(cal, "get_recent", fake_get_recent)
    out = _run(te._collect_chat_turns(days=7))
    assert len(out) == 1
    assert out[0]["meta"]["verification_tier"] == "well_formed"
