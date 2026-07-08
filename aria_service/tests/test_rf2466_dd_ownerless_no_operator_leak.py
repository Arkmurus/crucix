"""R-F2466 — SECURITY: owner-less DD reports must NEVER be adopted to the
configured operator (confidential cross-tenant leak).

Root cause: dd_vault has no user_id column, so an index rebuild / any owner-less
report was stamped to ARIA_DD_LEGACY_OWNER_UID via _dd_legacy_owner_fallback(),
then matched the operator's own user_id filter and appeared on their DD-reports
page — leaking OTHER users' reports. Fix: the fallback is fail-CLOSED by default
(returns None,None) so owner-less entries stay owner-less (admin-only), never
adopted to a scoped user. Opt back in only on a single-operator deployment via
ARIA_DD_LEGACY_OWNER_FALLBACK=1. The PRECISE watchlist reclaim still works.

Drives the REAL list_reports() with a mocked redis_store index.
"""
import asyncio
import os
import tempfile

import aria_service.intel.dd_vault as _ddv
import aria_service.intel.redis_store as _rs
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel.dd_vault import DDVault

OPERATOR = "5834252728d3"
OTHER_TENANT = "9999othertenant9999"


def _index():
    return [
        {"run_id": "rid_op", "entity_name": "Operator Own Co", "entity_type": "company",
         "canonical_entity_id": "company:GB:OP", "user_id": OPERATOR,
         "user_email_domain": "arkmurus.com", "share_to_company": True, "created_at": "2026-07-07T00:00:00Z"},
        # OWNER-LESS report that really belongs to another tenant (owner lost on wipe):
        {"run_id": "rid_secret", "entity_name": "Other Tenant Secret Co", "entity_type": "company",
         "canonical_entity_id": "company:GB:SECRET", "user_id": None,
         "user_email_domain": None, "created_at": "2026-07-07T00:00:00Z"},
    ]


def _isolated_vault():
    """A CONTROLLED vault (not the real dd_vault.db) — R-F2485 makes list_reports
    reconcile against the vault on every read, so the leak test must supply its own.
    Contains: the operator's case, an owner-less case (rid_secret, NO owner record),
    and a case OWNED BY ANOTHER TENANT that is NOT in the index — reconcile must add
    it as other-tenant-owned and the operator's scoped view must NOT include it."""
    fd, path = tempfile.mkstemp(prefix="rf2466_", suffix=".db")
    os.close(fd)
    v = DDVault(db_path=path)
    v.record_case(canonical_entity_id="company:GB:OP", entity_name="Operator Own Co",
                  latest_report_id="rid_op")
    v.record_report_owner("rid_op", canonical_entity_id="company:GB:OP", user_id=OPERATOR,
                          user_email_domain="arkmurus.com")
    # owner-less: a case exists but NO owner record → must stay hidden from scoped users
    v.record_case(canonical_entity_id="company:GB:SECRET", entity_name="Other Tenant Secret Co",
                  latest_report_id="rid_secret")
    # another tenant's OWNED case, absent from the index → reconcile adds it, scoped-out
    v.record_case(canonical_entity_id="company:GB:OTHEROWN", entity_name="Other Tenant Owned Co",
                  latest_report_id="rid_otherown")
    v.record_report_owner("rid_otherown", canonical_entity_id="company:GB:OTHEROWN",
                          user_id=OTHER_TENANT, user_email_domain="rival.com")
    return v


async def _list(monkeyenv):
    saved = {k: os.environ.get(k) for k in
             ("ARIA_DD_LEGACY_OWNER_UID", "ARIA_OPERATOR_EMAIL", "ARIA_DD_LEGACY_OWNER_FALLBACK")}
    os.environ["ARIA_DD_LEGACY_OWNER_UID"] = OPERATOR
    os.environ["ARIA_OPERATOR_EMAIL"] = "acorrea@arkmurus.com"
    if monkeyenv.get("fallback") is None:
        os.environ.pop("ARIA_DD_LEGACY_OWNER_FALLBACK", None)
    else:
        os.environ["ARIA_DD_LEGACY_OWNER_FALLBACK"] = monkeyenv["fallback"]

    idx = _index()
    vault = _isolated_vault()

    async def fake_get_json(key, *a, **k):
        if key == dor.REPORT_INDEX_KEY:
            return idx
        if key == dor.WATCHLIST_KEY:
            return []  # no watchlist trail → owner-less stays unclaimed
        return None

    async def fake_set_json(key, val, *a, **k):
        return True

    async def fake_mutate(mutator, **k):
        return mutator(list(idx))

    orig = (_rs.get_json, _rs.set_json, _ddv.get_vault, dor._mutate_report_index)
    _rs.get_json, _rs.set_json = fake_get_json, fake_set_json
    _ddv.get_vault = lambda: vault
    dor._mutate_report_index = fake_mutate
    dor._R2469_OWNER_BACKFILLED.clear()
    try:
        return await dor.list_reports(limit=50, user_id=OPERATOR, user_email_domain="arkmurus.com")
    finally:
        _rs.get_json, _rs.set_json, _ddv.get_vault, dor._mutate_report_index = orig
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _names(reports):
    return {(r.get("entity_name") or "") for r in reports}


def test_ownerless_not_leaked_to_operator_by_default():
    reports = asyncio.run(_list({"fallback": None}))  # default = fail-closed
    names = _names(reports)
    assert "Operator Own Co" in names, f"operator's OWN report must still show, got {names}"
    assert "Other Tenant Secret Co" not in names, \
        f"BREACH: another tenant's owner-less report leaked to the operator: {names}"
    # R-F2485 — the vault reconcile must not leak another tenant's OWNED case either.
    assert "Other Tenant Owned Co" not in names, \
        f"BREACH: reconcile leaked another tenant's OWNED report to the operator: {names}"


def test_optin_flag_restores_single_operator_adoption():
    reports = asyncio.run(_list({"fallback": "1"}))  # explicit single-operator opt-in
    names = _names(reports)
    assert "Other Tenant Secret Co" in names, \
        f"with ARIA_DD_LEGACY_OWNER_FALLBACK=1 the single-operator adoption should apply, got {names}"


if __name__ == "__main__":
    test_ownerless_not_leaked_to_operator_by_default()
    print("PASS test_ownerless_not_leaked_to_operator_by_default")
    test_optin_flag_restores_single_operator_adoption()
    print("PASS test_optin_flag_restores_single_operator_adoption")
    print("ALL PASS")
