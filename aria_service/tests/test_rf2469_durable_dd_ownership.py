"""R-F2469 — DD report ownership must SURVIVE a state_store wipe, and a rebuild
must restore the REAL owner (never fabricate the operator's — the R-F2466 leak).

The report index (with user_id) lives in the wipeable state_store; the vault DB
survives. This adds a per-run dd_report_owners table in the vault, written at
persist time + backfilled on read, and read on index rebuild.

Test 1 (round-trip): vault.record_report_owner / get_report_owner.
Test 2 (durability): simulate a wipe (empty index, vault has the case + owner)
    → list_reports rebuild restores the real owner; another user does NOT see it.
Test 3 (fail-closed): a case with NO owner record stays owner-less on rebuild.
"""
import asyncio
import os
import tempfile

import aria_service.intel.dd_vault as _ddv
import aria_service.intel.redis_store as _rs
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel.dd_vault import DDVault

OWNER_B = "userB_web_id"
OPERATOR = "5834252728d3"


def _fresh_vault():
    fd, path = tempfile.mkstemp(prefix="rf2469_", suffix=".db")
    os.close(fd)
    return DDVault(db_path=path), path


def test_report_owner_roundtrip():
    v, _ = _fresh_vault()
    v.record_report_owner("rid1", canonical_entity_id="company:GB:X",
                          user_id=OWNER_B, user_email_domain="B.com", share_to_company=True)
    got = v.get_report_owner("rid1")
    assert got == {"user_id": OWNER_B, "user_email_domain": "b.com", "share_to_company": True}, got
    assert v.get_report_owner("nope") is None
    # owner-less write is a no-op (never fabricate)
    v.record_report_owner("rid2", user_id="")
    assert v.get_report_owner("rid2") is None


async def _list(vault, *, user_id):
    dor._R2469_OWNER_BACKFILLED.clear()

    async def fake_get_json(key, *a, **k):
        if key in (dor.REPORT_INDEX_KEY, dor.WATCHLIST_KEY):
            return []  # index WIPED → triggers vault rebuild
        return None

    async def fake_set_json(key, val, *a, **k):
        return True

    orig_g, orig_s, orig_gv = _rs.get_json, _rs.set_json, _ddv.get_vault
    _rs.get_json, _rs.set_json = fake_get_json, fake_set_json
    _ddv.get_vault = lambda: vault
    # ensure legacy-operator fallback is OFF (R-F2466 default)
    os.environ.pop("ARIA_DD_LEGACY_OWNER_FALLBACK", None)
    os.environ["ARIA_DD_LEGACY_OWNER_UID"] = OPERATOR
    try:
        return await dor.list_reports(limit=50, user_id=user_id, user_email_domain=None)
    finally:
        _rs.get_json, _rs.set_json, _ddv.get_vault = orig_g, orig_s, orig_gv


def test_wipe_rebuild_restores_real_owner():
    v, _ = _fresh_vault()
    v.record_case(canonical_entity_id="company:GB:X", entity_name="Acme Ltd",
                  latest_report_id="ridX")
    v.record_report_owner("ridX", canonical_entity_id="company:GB:X",
                          user_id=OWNER_B, user_email_domain="b.com")
    # The real owner (userB) sees their report after a wipe:
    got_b = asyncio.run(_list(v, user_id=OWNER_B))
    names_b = {r.get("entity_name") for r in got_b}
    assert "Acme Ltd" in names_b, f"real owner must see their rebuilt report, got {names_b}"
    # The operator must NOT see userB's report (no fabricated operator ownership):
    got_op = asyncio.run(_list(v, user_id=OPERATOR))
    names_op = {r.get("entity_name") for r in got_op}
    assert "Acme Ltd" not in names_op, f"LEAK: operator saw another user's report: {names_op}"


def test_ownerless_case_stays_hidden():
    v, _ = _fresh_vault()
    v.record_case(canonical_entity_id="company:GB:Y", entity_name="Ghost Co",
                  latest_report_id="ridY")
    # No record_report_owner → owner unknown → must stay owner-less (fail-closed).
    got_op = asyncio.run(_list(v, user_id=OPERATOR))
    assert "Ghost Co" not in {r.get("entity_name") for r in got_op}, \
        "owner-unknown report must NOT be fabricated onto the operator"


if __name__ == "__main__":
    test_report_owner_roundtrip()
    print("PASS test_report_owner_roundtrip")
    test_wipe_rebuild_restores_real_owner()
    print("PASS test_wipe_rebuild_restores_real_owner")
    test_ownerless_case_stays_hidden()
    print("PASS test_ownerless_case_stays_hidden")
    print("ALL PASS")
