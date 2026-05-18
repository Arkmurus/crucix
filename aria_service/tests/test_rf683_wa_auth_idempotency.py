"""R-F683 (2026-05-18) — wa-auth/backup server-side idempotency.

Live fly logs 2026-05-18 09:51:46-09:52:02 and 2026-05-18 07:51:46-07:52:02
both showed 31+ POSTs from the seenode WA listener within 18 seconds.
Root cause is on the seenode side: `backupAuthToRedis` fires from every
Baileys `creds.update` event with no client-side debounce.

R-F683 caps the damage server-side: hash the incoming bundle on arrival
and short-circuit if the same content arrived within
_WA_BACKUP_DEBOUNCE_SEC (5s). Response shape unchanged; adds
``debounced: bool`` so callers / tests can tell.

These tests use the FastAPI TestClient to exercise the public route.
"""
from __future__ import annotations


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    # The aria router already carries prefix="/api/aria" — don't double it.
    # Auth dep `require_aria_token` is no-op when accepted-tokens set is
    # empty; force-empty for tests so we don't have to send Bearer headers.
    monkeypatch.setattr(aria_routes, "_accepted_tokens", lambda: set())

    app = FastAPI()
    app.include_router(aria_routes.router)
    return TestClient(app)


def _reset_debounce():
    from aria_service.routes import aria as aria_routes
    aria_routes._WA_LAST_BACKUP["hash"] = None
    aria_routes._WA_LAST_BACKUP["at"] = 0.0
    aria_routes._WA_LAST_BACKUP["result"] = None


def _patch_state_store(monkeypatch):
    """Stub the state_store.set_json so tests don't need /data volume."""
    from aria_service.intel import state_store

    writes: list[tuple[str, object]] = []

    async def fake_set_json(key, value, **kwargs):
        writes.append((key, value))

    monkeypatch.setattr(state_store, "set_json", fake_set_json)
    return writes


def test_rf683_first_call_writes_and_returns_debounced_false(monkeypatch):
    _reset_debounce()
    writes = _patch_state_store(monkeypatch)
    client = _client(monkeypatch)

    bundle = {"creds.json": "x" * 100, "session-foo": "y" * 50}
    r = client.post("/api/aria/wa-auth/backup", json={"files": bundle})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["files"] == 2
    assert body["debounced"] is False  # first call always writes
    assert len(writes) == 1  # SQLite was touched


def test_rf683_identical_repeat_within_window_is_debounced(monkeypatch):
    """Second call with the same bundle within 5s must short-circuit:
    no SQLite write, response says debounced=true."""
    _reset_debounce()
    writes = _patch_state_store(monkeypatch)
    client = _client(monkeypatch)

    bundle = {"creds.json": "x" * 100, "session-foo": "y" * 50}

    r1 = client.post("/api/aria/wa-auth/backup", json={"files": bundle})
    assert r1.status_code == 200
    assert r1.json()["debounced"] is False
    assert len(writes) == 1

    # Identical bundle, immediately — should debounce.
    r2 = client.post("/api/aria/wa-auth/backup", json={"files": bundle})
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert body["debounced"] is True
    assert body["files"] == 2  # cached result fields preserved
    assert body["bytes"] == 150
    # CRITICAL: no second SQLite write.
    assert len(writes) == 1, (
        f"R-F683: debounced call still wrote to SQLite: {len(writes)} writes"
    )


def test_rf683_storm_31_calls_only_writes_once(monkeypatch):
    """Replay the actual production pattern: 31 identical POSTs in
    quick succession. R-F683 must let only ONE through to SQLite."""
    _reset_debounce()
    writes = _patch_state_store(monkeypatch)
    client = _client(monkeypatch)

    bundle = {"creds.json": "x" * 200, "app-state-1": "z" * 80}
    debounced_count = 0
    for _ in range(31):
        r = client.post("/api/aria/wa-auth/backup", json={"files": bundle})
        assert r.status_code == 200
        if r.json().get("debounced"):
            debounced_count += 1

    assert len(writes) == 1, (
        f"R-F683: storm replay broke debounce — {len(writes)} SQLite writes "
        f"(expected 1)"
    )
    assert debounced_count == 30, (
        f"R-F683: expected 30 debounced responses, got {debounced_count}"
    )


def test_rf683_different_bundle_bypasses_debounce(monkeypatch):
    """A legitimate update (different content) must write through even
    if it arrives <5s after the previous backup."""
    _reset_debounce()
    writes = _patch_state_store(monkeypatch)
    client = _client(monkeypatch)

    bundle_v1 = {"creds.json": "version-1"}
    bundle_v2 = {"creds.json": "version-2-after-rotation"}

    r1 = client.post("/api/aria/wa-auth/backup", json={"files": bundle_v1})
    assert r1.json()["debounced"] is False
    r2 = client.post("/api/aria/wa-auth/backup", json={"files": bundle_v2})
    assert r2.json()["debounced"] is False, (
        "R-F683: different bundle wrongly debounced"
    )
    assert len(writes) == 2


def test_rf683_after_debounce_window_writes_through(monkeypatch):
    """Same bundle arriving AFTER the 5s window must hit SQLite again
    (operator wants the updated_at timestamp to advance)."""
    _reset_debounce()
    writes = _patch_state_store(monkeypatch)
    client = _client(monkeypatch)

    bundle = {"creds.json": "x"}
    r1 = client.post("/api/aria/wa-auth/backup", json={"files": bundle})
    assert r1.json()["debounced"] is False

    # Manually age the cache past the debounce window.
    from aria_service.routes import aria as aria_routes
    aria_routes._WA_LAST_BACKUP["at"] -= 10.0  # 10 seconds ago

    r2 = client.post("/api/aria/wa-auth/backup", json={"files": bundle})
    assert r2.json()["debounced"] is False, (
        "R-F683: stale-window identical bundle wrongly debounced"
    )
    assert len(writes) == 2


def test_rf683_oversized_bundle_still_413s(monkeypatch):
    """The 1MB cap must still fire even after my changes (regression
    guard — don't let the idempotency path bypass validation)."""
    _reset_debounce()
    _patch_state_store(monkeypatch)
    client = _client(monkeypatch)

    huge = {"creds.json": "x" * 1_200_000}
    r = client.post("/api/aria/wa-auth/backup", json={"files": huge})
    assert r.status_code == 413


def test_rf683_empty_body_still_400s(monkeypatch):
    _reset_debounce()
    _patch_state_store(monkeypatch)
    client = _client(monkeypatch)
    r = client.post("/api/aria/wa-auth/backup", json={"files": {}})
    assert r.status_code == 400
