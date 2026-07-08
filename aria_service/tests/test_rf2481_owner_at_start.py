"""R-F2481 — DD ownership must be captured at START, so an orphaned / interrupted
run (deploy restart, crash before persist) is never owner-less.

mark_dd_running now writes the owner to the wipe-surviving vault (dd_report_owners,
keyed by run_id) at the moment the DD starts — before any layer runs. Completion
(R-F2469) re-writes it idempotently. An owner-less start writes nothing.
"""
import asyncio
import os
import tempfile

import aria_service.intel.dd_vault as _ddv
import aria_service.intel.redis_store as _rs
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel.dd_vault import DDVault


def _fresh_vault():
    fd, path = tempfile.mkstemp(prefix="rf2481_", suffix=".db")
    os.close(fd)
    return DDVault(db_path=path)


async def _mark(run_id, user_id):
    v = _fresh_vault()

    async def _noop_set(*a, **k):
        return True

    async def _noop_mutate(mutator, **k):
        return []

    orig_gv, orig_set, orig_mut = _ddv.get_vault, _rs.set_json, dor._mutate_report_index
    _ddv.get_vault = lambda: v
    _rs.set_json = _noop_set
    dor._mutate_report_index = _noop_mutate
    try:
        await dor.mark_dd_running(
            run_id, "Acme Ltd", "standard", "company:BR:123",
            user_id=user_id, user_email_lower=("a@x.com" if user_id else None),
            user_email_domain=("x.com" if user_id else None), share_to_company=True,
        )
    finally:
        _ddv.get_vault, _rs.set_json, dor._mutate_report_index = orig_gv, orig_set, orig_mut
    return v


def test_owner_captured_at_start():
    v = asyncio.run(_mark("dd_start1", "user_owner_A"))
    owner = v.get_report_owner("dd_start1")
    assert owner and owner["user_id"] == "user_owner_A", owner
    assert owner["user_email_domain"] == "x.com"


def test_ownerless_start_records_nothing():
    v = asyncio.run(_mark("dd_start2", None))
    assert v.get_report_owner("dd_start2") is None, "owner-less start must not fabricate an owner"


if __name__ == "__main__":
    test_owner_captured_at_start()
    print("PASS test_owner_captured_at_start")
    test_ownerless_start_records_nothing()
    print("PASS test_ownerless_start_records_nothing")
    print("ALL PASS")
