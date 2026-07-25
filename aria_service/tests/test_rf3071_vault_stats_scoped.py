"""R-F3071 — the DD Vault stats panel must show the CALLER's cases, not everyone's.

BROKEN PATH (reproduced 2026-07-25 with a brand-new free account that owned
nothing): `GET /api/aria/dd/vault/stats` took no user_id and returned
`vault.stats()` — the platform-wide aggregate. That account was served
`total_cases: 28, by_type {company:27, person:1}, run_last_7d: 19`, i.e. other
tenants' DD activity, while its own (correctly R-F2097-scoped) vault search
returned zero. The dd-reports.html panel therefore showed a headline of 28
cases that matched nothing the user could open.

R-F2097 scoped `search` and `case` and MISSED `stats`.
"""
import time

import pytest

from aria_service.intel.dd_vault import DDVault


@pytest.fixture()
def vault(tmp_path):
    """Seed through the vault's OWN write API so the fixture can't drift from
    the real schema (a hand-rolled INSERT already missed created_at/updated_at)."""
    v = DDVault(db_path=str(tmp_path / "rf3071_vault.db"))
    for cid, name, etype in [
        ("company:GB:001", "Alpha Ltd", "company"),
        ("company:GB:002", "Beta Ltd", "company"),
        ("company:GB:003", "Gamma Ltd", "company"),
        ("person:carol:GB:1980", "Carol", "person"),
    ]:
        v.record_case(cid, name, entity_type=etype, jurisdiction="GB")
    v.add_cross_reference("company:GB:001", "company:GB:002",
                          "shared_director", user_id="alice")
    return v


ALICE = {"company:GB:001", "company:GB:002"}
BOB = {"company:GB:003", "person:carol:GB:1980"}


def test_stats_are_scoped_to_the_callers_cases(vault):
    s = vault.stats(entity_ids=ALICE)
    assert s["total_cases"] == 2, (
        f"alice owns 2 cases, got {s['total_cases']} — an unscoped call returns 4 "
        "and publishes other tenants' case volume"
    )
    assert s["by_type"] == {"company": 2}
    assert s["total_cross_references"] == 1
    assert s["run_last_7d"] == 2


def test_a_different_tenant_sees_only_their_own(vault):
    s = vault.stats(entity_ids=BOB)
    assert s["total_cases"] == 2
    assert s["by_type"] == {"company": 1, "person": 1}
    assert s["total_cross_references"] == 0, \
        "bob owns no cross-reference edges — must not inherit alice's"


def test_owning_nothing_reports_zero_not_everything(vault):
    """The exact case that leaked: a brand-new account with no DD history."""
    s = vault.stats(entity_ids=set())
    assert s == {
        "total_cases": 0, "by_status": {}, "by_type": {},
        "total_cross_references": 0, "run_last_7d": 0,
    }, "an empty ownership set must NEVER fall through to the global aggregate"


def test_none_is_still_the_unrestricted_internal_view(vault):
    """None = the internal service token / operator. Kept explicit, not implicit."""
    s = vault.stats(entity_ids=None)
    assert s["total_cases"] == 4
    assert s["by_type"] == {"company": 3, "person": 1}


def test_large_ownership_set_is_chunked_not_truncated(vault):
    """A big portfolio must not blow SQLite's variable limit or silently drop ids."""
    big = {f"company:XX:{i:05d}" for i in range(1200)} | ALICE
    s = vault.stats(entity_ids=big)
    assert s["total_cases"] == 2, \
        "only the 2 real rows exist; the 1200 synthetic ids must chunk cleanly"


def test_route_passes_ownership_through(monkeypatch):
    """Capability check on the ENDPOINT, not just the store: dd_vault_stats_ep
    must resolve the caller's owned ids and hand them to stats()."""
    import asyncio
    from aria_service.routes import aria as aria_routes

    seen = {}

    class _FakeVault:
        def stats(self, entity_ids=None):
            seen["entity_ids"] = entity_ids
            return {"total_cases": 0}

    monkeypatch.setattr("aria_service.intel.dd_vault.get_vault", lambda: _FakeVault())

    async def _owned(user_id, user_email_domain=""):
        return {"company:GB:001"} if user_id else set()

    monkeypatch.setattr(aria_routes, "_dd_owned_entity_ids", _owned)

    out = asyncio.run(aria_routes.dd_vault_stats_ep(user_id="alice"))
    assert out["success"] is True
    assert seen["entity_ids"] == {"company:GB:001"}, (
        "the route must pass the caller's owned ids — passing None here is the "
        "pre-R-F3071 defect (it published the global aggregate)"
    )


def test_every_vault_endpoint_takes_an_owner():
    """R-F3071 recurrence guard — the reason this defect existed.

    R-F2097 scoped the vault endpoints BY HAND, one at a time, and missed
    `stats`, which then served platform-wide totals for months. The vault has no
    owner column, so ownership can only come in as a parameter — an endpoint
    that does not accept `user_id` CANNOT be scoped, and that is statically
    checkable. Any new /dd/vault route must declare it (and use it) rather than
    relying on someone remembering.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))

    unscoped = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        routes = [d for d in node.decorator_list
                  if isinstance(d, ast.Call)
                  and any("/dd/vault" in a.value for a in d.args
                          if isinstance(a, ast.Constant) and isinstance(a.value, str))]
        if not routes:
            continue
        args = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        body = ast.dump(node)
        if "user_id" not in args:
            unscoped.append(f"{node.name} (no user_id parameter)")
        elif "_dd_owned_entity_ids" not in body:
            unscoped.append(f"{node.name} (takes user_id but never resolves ownership)")

    assert not unscoped, (
        "every /dd/vault endpoint must accept user_id AND resolve it through "
        f"_dd_owned_entity_ids — these do not: {unscoped}. The vault is "
        "entity-keyed with no owner column, so an endpoint that skips this "
        "serves every tenant's data (the R-F3071 defect)."
    )
