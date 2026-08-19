"""R-F2407 — DD re-run unnamed-row live fix.

R-F2406 preserved lineage, but live reports can still have legacy blank
``entity_name`` index rows. The reports list must repair those from the stored
target before the browser can re-run them as "(unnamed)".

R-F4158 (C-179) — THIS TEST READ A MACHINE-GLOBAL DATABASE.

It stubbed `redis_store.get_json`/`set_json` and nothing else. But `list_reports`
was later taught (R-F1973 / R-F2485 / R-F2652) to reconcile the volatile index
against the DURABLE DD VAULT on **every** read — a separate SQLite file at
``/data/dd_vault.db`` (``C:\\data\\dd_vault.db`` on a Windows dev box), outside
the repo and shared by every test run on the machine.

So this test merged whatever other tests had left in that file. Measured
2026-08-18: six residual fixture rows — ``Risky Business SARL``,
``Clean Corp Ltd``, ``Sanctioned Entity Ltd``, run ids ``dd_test_red`` /
``dd_test_green`` / ``dd_test_hardstop`` — and the assertion on
``reports[0]`` picked up ``Risky Business SARL`` instead of the seeded row.

**The production code was never wrong.** With the vault stubbed empty the same
call returns ``Acme Ltd``, i.e. the R-F2407 repair works exactly as intended.
What failed was a test that depended on the state of a real database it never
created — green on a clean box, red on a used one, and invisible to the §16
baseline because the baseline was recorded on a machine where that file happened
to be empty.

Stubbing `get_vault` is the idiom the other DD tests already use (see
``test_rf2097_dd_vault_ownership``); this one simply predated the vault merge.
"""
import pytest


class _EmptyVault:
    """No cases, so `list_reports` reconciles against nothing and the assertion
    below is about the seeded row only.

    Deliberately empty rather than pre-loaded: this test is about repairing a
    blank ``entity_name`` from the stored target, and any vault row would be an
    unrelated input to that question.
    """

    def list_all(self, limit: int = 200):
        return []

    def get_report_owner(self, run_id):
        return None


@pytest.mark.asyncio
async def test_list_reports_repairs_blank_entity_name_from_target(monkeypatch):
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel import redis_store as rs
    from aria_service.intel import dd_vault

    # R-F4158 (C-179) — isolate the durable vault; see the module docstring.
    monkeypatch.setattr(dd_vault, "get_vault", lambda: _EmptyVault())

    index = [{
        "run_id": "dd_blank",
        "entity_name": "",
        "canonical_entity_id": "company:GB:12345678",
        "created_at": "2026-07-07T20:00:00Z",
        "risk_classification": "AMBER-LIGHT",
    }]
    body = {
        "run_id": "dd_blank",
        "canonical_entity_id": "company:GB:12345678",
        "target": {
            "name": "Acme Ltd",
            "website_url": "https://acme.example",
        },
        "identity": {
            "entity_name": "",
            "entity_type": "company",
            "jurisdiction_iso2": "GB",
            "registration_number": "12345678",
        },
    }
    writes = {}

    async def fake_get_json(key):
        if key == ddo.REPORT_INDEX_KEY:
            return [dict(index[0])]
        if key == ddo.REPORT_REDIS_KEY.format(run_id="dd_blank"):
            return body
        return None

    async def fake_set_json(key, value, ex=None):
        writes[key] = value
        return True

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)

    reports = await ddo.list_reports(limit=10)

    assert reports[0]["entity_name"] == "Acme Ltd"
    assert writes[ddo.REPORT_INDEX_KEY][0]["entity_name"] == "Acme Ltd"
    repaired_body = writes[ddo.REPORT_REDIS_KEY.format(run_id="dd_blank")]
    assert repaired_body["identity"]["entity_name"] == "Acme Ltd"
