"""R-F3532 — deleting a DD report deletes the report the USER sees.

Operator report (2026-07-31): "once a report has several versions the delete
button does not delete the report, it has to delete the entire versions until
the final v1 then the report would be deleted fully."

Root cause, confirmed against live production state before writing this:
the library collapses an entity's whole version history into ONE row
(``_collapse_index``) and the confirm dialog promises "removes the report and
its full version history" — while ``DELETE /dd/report/{run_id}`` removed a
SINGLE run. The previous version was then promoted into the row, so the report
appeared to survive deletion. Live index at the time: BAE Systems plc 7 rows,
Rolls-Royce Holdings 4, Chemring Group 3, HSA Group 3, five more entities at 2 —
every one of them displayed as a single row.

The dangerous half is the fix, not the bug: Chemring's three runs belonged to
TWO different tenants, so a blind "delete the whole chain" would have destroyed
another account's report — the R-F2653 mistake one layer up. The cascade is
therefore ACL-checked per run, and anything it may not delete is reported.
"""

from __future__ import annotations

import asyncio
import fnmatch

import pytest
from fastapi import HTTPException

from aria_service.intel import dd_orchestrator as ddo
from aria_service.routes import aria as A


@pytest.fixture
def store(monkeypatch):
    """In-memory redis_store + a vault that models the R-F2653 API."""
    data: dict = {}
    import aria_service.intel.redis_store as rs
    import aria_service.intel.dd_vault as dv

    async def set_json(k, v, ex=None, keepttl=False):
        await asyncio.sleep(0)
        data[k] = v
        return True

    async def get_json(k):
        await asyncio.sleep(0)
        return data.get(k)

    async def get_json_strict(k):
        await asyncio.sleep(0)
        return data.get(k)

    async def delete(k):
        await asyncio.sleep(0)
        return data.pop(k, None) is not None

    async def scan_json(pat, count=200):
        return [(k, v) for k, v in list(data.items()) if fnmatch.fnmatch(k, pat)]

    async def scan_keys(pat, count=200):
        return [k for k in list(data) if fnmatch.fnmatch(k, pat)]

    for name, fn in [("set_json", set_json), ("get_json", get_json),
                     ("get_json_strict", get_json_strict), ("delete", delete),
                     ("scan_json", scan_json), ("scan_keys", scan_keys)]:
        monkeypatch.setattr(rs, name, fn)

    class _Vault:
        def list_all(self, limit=500):
            return []

        def remove_report_from_case(self, cid, run_id):
            return {"found": True, "case_deleted": True, "remaining_reports": 0}

    monkeypatch.setattr(dv, "get_vault", lambda: _Vault())
    return data


def _seed(store, runs):
    """runs = [(run_id, owner, domain)] newest first, one shared entity."""
    index = []
    for i, (run_id, owner, domain) in enumerate(runs):
        entry = {
            "run_id": run_id,
            "entity_name": "Chemring Group PLC",
            "jurisdiction": "United Kingdom",
            "canonical_entity_id": "company:GB:00086662",
            "user_id": owner,
            "user_email_domain": domain,
            "share_to_company": True,
            "generated_at": f"2026-07-{20 - i:02d}T10:00:00+00:00",
        }
        index.append(entry)
        store[ddo.REPORT_REDIS_KEY.format(run_id=run_id)] = {
            "run_id": run_id, "user_id": owner, "user_email_domain": domain,
            "canonical_entity_id": "company:GB:00086662",
            "identity": {"entity_name": "Chemring Group PLC"},
        }
    store[ddo.REPORT_INDEX_KEY] = index
    return index


def _remaining(store):
    return [e.get("run_id") for e in (store.get(ddo.REPORT_INDEX_KEY) or [])]


# ── The defect, as the operator hit it ───────────────────────────────────────


def test_the_library_shows_one_row_for_many_runs():
    """The premise: this is why deleting one run looks like nothing happened."""
    index = []
    _seed(index_store := {}, [("v3", "alice", "a.com"), ("v2", "alice", "a.com"),
                              ("v1", "alice", "a.com")])
    index = index_store[ddo.REPORT_INDEX_KEY]
    collapsed = ddo._collapse_index(index, 50)
    assert len(index) == 3
    assert len(collapsed) == 1, "three runs, one row — deleting the row must clear all three"
    assert collapsed[0]["run_id"] == "v3", "the row carries the LATEST run's id"


@pytest.mark.asyncio
async def test_capability_deleting_the_row_deletes_every_version(store):
    _seed(store, [("v3", "alice", "a.com"), ("v2", "alice", "a.com"), ("v1", "alice", "a.com")])

    out = await A.dd_report_delete_ep(run_id="v3", user_id="alice", user_email_domain="a.com")

    assert out["ok"] is True
    assert out["versions_deleted"] == 3
    assert sorted(out["deleted_run_ids"]) == ["v1", "v2", "v3"]
    assert out["skipped_run_ids"] == []
    assert _remaining(store) == [], "an earlier version survived and will resurface as the row"
    for rid in ("v1", "v2", "v3"):
        assert ddo.REPORT_REDIS_KEY.format(run_id=rid) not in store


@pytest.mark.asyncio
async def test_capability_the_entity_is_gone_from_the_list_after_one_delete(store):
    """The operator's actual symptom: the row came back with the previous version."""
    _seed(store, [("v3", "alice", "a.com"), ("v2", "alice", "a.com"), ("v1", "alice", "a.com")])
    await A.dd_report_delete_ep(run_id="v3", user_id="alice", user_email_domain="a.com")

    rows = await ddo.list_reports(limit=50, user_id="alice", user_email_domain="a.com")
    assert [r for r in rows if r.get("entity_name") == "Chemring Group PLC"] == []


# ── The dangerous half: a chain can span tenants ─────────────────────────────


@pytest.mark.asyncio
async def test_cascade_never_deletes_another_tenants_version(store):
    """Live Chemring had two owners across three runs. A blind chain delete
    would have destroyed the other account's report."""
    _seed(store, [("v3", "alice", "a.com"), ("v2", "alice", "a.com"), ("v1", "bob", "b.com")])

    out = await A.dd_report_delete_ep(run_id="v3", user_id="alice", user_email_domain="a.com")

    assert sorted(out["deleted_run_ids"]) == ["v2", "v3"]
    assert out["skipped_run_ids"] == ["v1"]
    assert "another account" in out["skipped_reason"]
    assert _remaining(store) == ["v1"], "bob's run must survive alice's delete"
    assert ddo.REPORT_REDIS_KEY.format(run_id="v1") in store, "bob's report body was destroyed"


@pytest.mark.asyncio
async def test_a_partial_delete_never_reads_as_a_clean_one(store):
    _seed(store, [("v2", "alice", "a.com"), ("v1", "bob", "b.com")])
    out = await A.dd_report_delete_ep(run_id="v2", user_id="alice", user_email_domain="a.com")
    assert out.get("skipped_reason"), "a partial delete reported no caveat at all"


@pytest.mark.asyncio
async def test_cross_user_delete_is_still_refused(store):
    """R-F1820 regression guard — the cascade must not widen access."""
    _seed(store, [("v2", "alice", "a.com"), ("v1", "alice", "a.com")])
    with pytest.raises(HTTPException) as e:
        await A.dd_report_delete_ep(run_id="v2", user_id="mallory", user_email_domain="evil.com")
    assert e.value.status_code == 404
    assert _remaining(store) == ["v2", "v1"], "a refused delete removed something"


@pytest.mark.asyncio
async def test_same_company_colleague_may_delete_the_shared_chain(store):
    """R-F2291 sharing still applies, per run, across the whole chain."""
    _seed(store, [("v2", "alice", "a.com"), ("v1", "alice", "a.com")])
    out = await A.dd_report_delete_ep(run_id="v2", user_id="carol", user_email_domain="a.com")
    assert out["versions_deleted"] == 2
    assert _remaining(store) == []


# ── Idempotency: a second click must not strand the row ──────────────────────


@pytest.mark.asyncio
async def test_second_delete_is_success_not_a_stranded_row(store):
    _seed(store, [("v1", "alice", "a.com")])
    first = await A.dd_report_delete_ep(run_id="v1", user_id="alice", user_email_domain="a.com")
    assert first["ok"] is True

    again = await A.dd_report_delete_ep(run_id="v1", user_id="alice", user_email_domain="a.com")
    assert again["ok"] is True, "a second click reported failure and left the row on screen"
    assert again["already_absent"] is True
    assert again["blob_deleted"] is False and again["index_entries_removed"] == 0


@pytest.mark.asyncio
async def test_orphan_index_row_with_no_body_is_deletable(store):
    """An index entry whose blob aged out was previously undeletable."""
    _seed(store, [("v1", "alice", "a.com")])
    del store[ddo.REPORT_REDIS_KEY.format(run_id="v1")]

    out = await A.dd_report_delete_ep(run_id="v1", user_id="alice", user_email_domain="a.com")
    assert out["ok"] is True
    assert out["index_entries_removed"] == 1
    assert _remaining(store) == []


# ── The opt-out still works ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_false_deletes_exactly_one_run(store):
    _seed(store, [("v3", "alice", "a.com"), ("v2", "alice", "a.com"), ("v1", "alice", "a.com")])
    out = await A.dd_report_delete_ep(run_id="v3", user_id="alice",
                                      user_email_domain="a.com", cascade=False)
    assert out["versions_deleted"] == 1
    assert _remaining(store) == ["v2", "v1"]


# ── The coherence property: list and delete share ONE grouping ───────────────


def test_delete_group_is_exactly_the_row_the_list_collapses():
    """One definition of "the same report". Two was the whole defect."""
    rows = [
        {"run_id": "a1", "entity_name": "BAE Systems plc", "jurisdiction": None,
         "generated_at": "2026-07-20T00:00:00+00:00"},
        {"run_id": "a2", "entity_name": "BAE Systems plc", "jurisdiction": None,
         "generated_at": "2026-07-19T00:00:00+00:00"},
        {"run_id": "b1", "entity_name": "QinetiQ Group plc", "jurisdiction": "United Kingdom",
         "generated_at": "2026-07-18T00:00:00+00:00"},
    ]
    groups = ddo._group_index(rows)
    collapsed = ddo._collapse_index(rows, 50)

    assert len(collapsed) == len(groups), "the list shows one row per group, by definition"
    for row in collapsed:
        member_ids = next(
            [m["run_id"] for m in members]
            for members in groups.values()
            if any(m["run_id"] == row["run_id"] for m in members)
        )
        # every run folded into this row must be in the group the delete uses
        assert row["run_id"] in member_ids
    assert sorted(
        m["run_id"] for members in groups.values() for m in members
    ) == ["a1", "a2", "b1"], "no run may be dropped from, or duplicated across, groups"


@pytest.mark.asyncio
async def test_group_resolution_degrades_to_a_single_run_on_failure(monkeypatch):
    """Best-effort by construction: never raise, never widen."""
    import aria_service.intel.redis_store as rs

    async def _boom(_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(rs, "get_json", _boom)
    assert await ddo.entity_group_run_ids("dd_x") == ["dd_x"]
    assert await ddo.entity_group_run_ids("") == []
