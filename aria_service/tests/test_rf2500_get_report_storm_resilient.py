"""R-F2500 — a FINISHED DD's report must always be openable, even while a write storm
saturates the single writer. Live symptom: get_report('dd_6e27eff2857f') returned empty
(→ "running forever") for a completed DD whose 21KB blob was present + durable in the DB,
because get()/_row timed out (5s) under the storm. Fix: get_report falls back to
state_store.get_direct() — a fresh connection (query_only, reads WAL) immune to the wedge
+ read pool. Tested against a hand-built DB so the query/TTL/fallback logic is
deterministic (WAL-snapshot timing is a live property, verified separately by smoke).
"""
import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path

import aria_service.intel.redis_store as rs
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel import state_store as ss


def _build_db(rows):
    """rows: list of (key, value, kind, expires_at)."""
    p = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    p.close()
    c = sqlite3.connect(p.name)
    c.execute(
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "kind TEXT NOT NULL, expires_at REAL)"
    )
    c.executemany("INSERT INTO state VALUES (?,?,?,?)", rows)
    c.commit()
    c.close()
    return Path(p.name)


def test_get_direct_reads_value_and_respects_ttl_and_kind():
    saved = ss._DB_PATH
    ss._DB_PATH = _build_db([
        ("crucix:dd:report:dd_t1", json.dumps({"bottom_line": "BLUF-A"}), "string", None),
        ("crucix:dd:report:dd_exp", json.dumps({"x": 1}), "string", time.time() - 10),  # expired
        ("crucix:dd:report:dd_list", "[]", "list", None),                                # wrong kind
    ])
    try:
        raw = asyncio.run(ss.get_direct("crucix:dd:report:dd_t1"))
        assert raw and json.loads(raw)["bottom_line"] == "BLUF-A"
        assert asyncio.run(ss.get_direct("crucix:dd:report:dd_exp")) is None   # TTL expired
        assert asyncio.run(ss.get_direct("crucix:dd:report:dd_list")) is None  # kind != string
        assert asyncio.run(ss.get_direct("crucix:dd:report:missing")) is None  # absent
    finally:
        ss._DB_PATH = saved


def test_get_report_falls_back_to_direct_when_normal_read_blinded():
    saved = ss._DB_PATH
    ss._DB_PATH = _build_db([
        (dor.REPORT_REDIS_KEY.format(run_id="dd_t2"),
         json.dumps({"bottom_line": "BLUF-B", "run_id": "dd_t2", "user_id": "u1"}), "string", None),
    ])
    orig = rs.get_json

    async def _blinded(k, *a, **kw):
        return None  # storm: every state_store read returns None

    rs.get_json = _blinded
    try:
        r = asyncio.run(dor.get_report("dd_t2"))
    finally:
        rs.get_json = orig
        ss._DB_PATH = saved
    assert r is not None, "get_report must recover the durable blob via get_direct"
    assert r.get("bottom_line") == "BLUF-B" and r.get("user_id") == "u1"


def test_get_report_none_when_truly_absent():
    saved = ss._DB_PATH
    ss._DB_PATH = _build_db([])
    orig = rs.get_json

    async def _blinded(k, *a, **kw):
        return None

    rs.get_json = _blinded
    try:
        assert asyncio.run(dor.get_report("dd_ghost")) is None
    finally:
        rs.get_json = orig
        ss._DB_PATH = saved


if __name__ == "__main__":
    test_get_direct_reads_value_and_respects_ttl_and_kind()
    print("PASS test_get_direct_reads_value_and_respects_ttl_and_kind")
    test_get_report_falls_back_to_direct_when_normal_read_blinded()
    print("PASS test_get_report_falls_back_to_direct_when_normal_read_blinded")
    test_get_report_none_when_truly_absent()
    print("PASS test_get_report_none_when_truly_absent")
    print("ALL PASS")
