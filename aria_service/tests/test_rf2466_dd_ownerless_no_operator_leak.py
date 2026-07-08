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

import aria_service.intel.redis_store as _rs
from aria_service.intel import dd_orchestrator as dor

OPERATOR = "5834252728d3"


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


async def _list(monkeyenv):
    import os
    saved = {k: os.environ.get(k) for k in
             ("ARIA_DD_LEGACY_OWNER_UID", "ARIA_OPERATOR_EMAIL", "ARIA_DD_LEGACY_OWNER_FALLBACK")}
    os.environ["ARIA_DD_LEGACY_OWNER_UID"] = OPERATOR
    os.environ["ARIA_OPERATOR_EMAIL"] = "acorrea@arkmurus.com"
    if monkeyenv.get("fallback") is None:
        os.environ.pop("ARIA_DD_LEGACY_OWNER_FALLBACK", None)
    else:
        os.environ["ARIA_DD_LEGACY_OWNER_FALLBACK"] = monkeyenv["fallback"]

    idx = _index()

    async def fake_get_json(key, *a, **k):
        if key == dor.REPORT_INDEX_KEY:
            return idx
        if key == dor.WATCHLIST_KEY:
            return []  # no watchlist trail → owner-less stays unclaimed
        return None

    async def fake_set_json(key, val, *a, **k):
        return True

    orig_g, orig_s = _rs.get_json, _rs.set_json
    _rs.get_json, _rs.set_json = fake_get_json, fake_set_json
    try:
        return await dor.list_reports(limit=50, user_id=OPERATOR, user_email_domain="arkmurus.com")
    finally:
        _rs.get_json, _rs.set_json = orig_g, orig_s
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
