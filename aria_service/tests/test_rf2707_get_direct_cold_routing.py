"""R-F2707 — the storm-proof direct reader (get_direct/_get_direct_sync) must be
hot/cold-split aware.

Latent gap: _get_direct_sync (R-F2500) opened a fresh connection to _DB_PATH (the HOT
db) unconditionally. With the hot/cold split on, a cold-prefix key (verified_facts /
audit:by_hash / verified_intel:fact / reasoning_library) lives in aria_knowledge_store.db,
so a direct read of one returned a false None — a present fact read as ABSENT
(data-blindness/fabrication) the moment any cold-key reader used the storm-proof path.
This drives the real reader against two db files and asserts correct routing.
"""
import sqlite3

import aria_service.intel.state_store as ss


def _make_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS state "
        "(key TEXT PRIMARY KEY, value TEXT, kind TEXT, expires_at REAL)"
    )
    for k, v in rows:
        conn.execute(
            "INSERT INTO state(key, value, kind, expires_at) VALUES(?,?,'string',NULL)",
            (k, v),
        )
    conn.commit()
    conn.close()


def test_cold_key_routes_to_cold_db_when_split_on(tmp_path, monkeypatch):
    hot = tmp_path / "aria_state.db"
    cold = tmp_path / "aria_knowledge_store.db"
    _make_db(hot, [("crucix:dd:report:x", "HOTVAL")])
    _make_db(cold, [("aria:verified_facts:f1", "COLDVAL")])

    monkeypatch.setattr(ss, "_DB_PATH", hot)
    monkeypatch.setattr(ss, "_COLD_DB_PATH", cold)
    monkeypatch.setattr(ss, "_HOTCOLD_SPLIT", True)

    # the FIX: a cold-prefix key is now found in the cold db (was a false None)
    assert ss._get_direct_sync("aria:verified_facts:f1") == "COLDVAL"
    # hot keys still read from the hot db
    assert ss._get_direct_sync("crucix:dd:report:x") == "HOTVAL"
    # a cold key is NOT in the hot db (proves the routing actually switched files)
    assert ss._get_direct_sync("crucix:dd:report:missing") is None


def test_split_off_is_byte_identical_hot_only(tmp_path, monkeypatch):
    hot = tmp_path / "aria_state.db"
    cold = tmp_path / "aria_knowledge_store.db"
    _make_db(hot, [("crucix:dd:report:x", "HOTVAL")])
    _make_db(cold, [("aria:verified_facts:f1", "COLDVAL")])

    monkeypatch.setattr(ss, "_DB_PATH", hot)
    monkeypatch.setattr(ss, "_COLD_DB_PATH", cold)
    monkeypatch.setattr(ss, "_HOTCOLD_SPLIT", False)  # split OFF → always hot db

    # with the split off, a cold key reads the hot db (absent there) → None, exactly
    # the pre-R-F2707 behaviour. No regression.
    assert ss._get_direct_sync("aria:verified_facts:f1") is None
    assert ss._get_direct_sync("crucix:dd:report:x") == "HOTVAL"
