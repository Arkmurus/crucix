"""R-F3534 — the official-designation lane: global coverage + its own heartbeat.

Measured on production before this change (100-signal golden feed):

    Grade A: natural_hazard 46, active_tender 8, conflict_escalation 2
    Grade A: sanctions_change ......................................... 0

Zero. Every one of the seven live sanctions signals sat at Grade B, so the
public channel published Czech grid sensors instead of designations. Three
root causes, all fixed here:

1. CADENCE. run_designation_diff() had no schedule of its own. It ran only as a
   non-fatal afterthought inside WEEKLY-DD-WATCHLIST ("0 7 * * mon"), wrapped in
   a bare `except: pass`. OFAC designated on seven separate days in July 2026
   while ARIA looked once — and a failure told nobody.
2. COVERAGE. The diff watched OFAC + UN + FCDO: US, UN, UK. ARIA's own canonical
   store already held the EU consolidated list (5,994 live designations,
   refreshed daily) and nothing watched it.
3. CITATION. The bridge's URL fallback knew only un/fcdo and sent every other
   source to the OFAC search page — so an EU listing would have cited the US
   Treasury as its register. A fabricated citation on a compliance signal is
   worse than no citation.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest
import yaml

from aria_service.intel import sanctions_designation_diff as sdd


_REPO = pathlib.Path(__file__).resolve().parents[2]


def _run(coro):
    return asyncio.run(coro)


# ── Coverage is global ───────────────────────────────────────────────────────


def test_loaders_cover_every_regime_the_canonical_store_holds(monkeypatch):
    from aria_service.intel.sanctions_canonical import store as canon
    monkeypatch.setattr(canon, "list_sources", lambda: ["ofac_sdn", "eu_consolidated"])

    sources = [s for s, _ in _run(sdd._loaders())]

    assert "canon:eu_consolidated" in sources, (
        "the EU consolidated list is in ARIA's own store and still unwatched"
    )
    assert "worldbank" in sources, "a newly debarred supplier is the same decision as a designation"
    for legacy in ("ofac", "un", "fcdo"):
        assert legacy in sources, f"{legacy} coverage was dropped"


def test_ofac_is_not_watched_twice_under_two_id_schemes(monkeypatch):
    """canon:ofac_sdn would duplicate every US alert AND baseline 18,959 rows."""
    from aria_service.intel.sanctions_canonical import store as canon
    monkeypatch.setattr(canon, "list_sources", lambda: ["ofac_sdn", "eu_consolidated"])
    sources = [s for s, _ in _run(sdd._loaders())]
    assert "canon:ofac_sdn" not in sources
    assert sum(1 for s in sources if "ofac" in s) == 1


def test_a_new_source_baselines_silently_instead_of_flooding(monkeypatch):
    """Adding a list must NOT emit its whole back catalogue as breaking news.

    This is the property that makes widening coverage safe: `prior is None`
    records a baseline and emits nothing.
    """
    store: dict = {}
    pushed: list = []

    async def get_json(k):
        return store.get(k)

    async def set_json(k, v, *a, **kw):
        store[k] = v
        return True

    async def lpush(k, v, **kw):
        pushed.append(v)

    async def ltrim(k, a, b):
        return None

    async def loader():
        return [{"uid": f"eu-{i}", "name": f"Entity {i}"} for i in range(5994)]

    monkeypatch.setattr(sdd.rs, "get_json", get_json)
    monkeypatch.setattr(sdd.rs, "set_json", set_json)
    monkeypatch.setattr(sdd.rs, "lpush", lpush)
    monkeypatch.setattr(sdd.rs, "ltrim", ltrim)

    async def one_loader():
        return [("canon:eu_consolidated", loader)]

    monkeypatch.setattr(sdd, "_loaders", one_loader)

    first = _run(sdd.run_designation_diff())
    assert first["new_total"] == 0, "a first-time source flooded the channel with its back catalogue"
    assert pushed == []
    assert first["sources"]["canon:eu_consolidated"]["baseline"] == 5994

    # ...and a genuinely new designation on the SECOND run does emit.
    async def loader_plus_one():
        rows = [{"uid": f"eu-{i}", "name": f"Entity {i}"} for i in range(5994)]
        rows.append({"uid": "eu-NEW", "name": "Newly Designated SARL"})
        return rows

    async def one_loader_plus():
        return [("canon:eu_consolidated", loader_plus_one)]

    monkeypatch.setattr(sdd, "_loaders", one_loader_plus)
    second = _run(sdd.run_designation_diff())
    assert second["new_total"] == 1
    assert len(pushed) == 1
    assert "Newly Designated SARL" in pushed[0]


# ── Citation honesty ─────────────────────────────────────────────────────────


def test_no_source_ever_cites_another_regimes_register():
    ofac = sdd.source_citation("ofac")
    for source in ("canon:eu_consolidated", "un", "fcdo", "worldbank"):
        citation = sdd.source_citation(source)
        assert citation, f"{source} has no register to cite"
        assert citation != ofac, f"{source} cites the OFAC search page — a fabricated citation"
    assert "europa" in sdd.source_citation("canon:eu_consolidated") or \
           "sanctionsmap" in sdd.source_citation("canon:eu_consolidated")
    assert "worldbank" in sdd.source_citation("worldbank")


def test_an_unknown_source_yields_no_citation_rather_than_a_wrong_one():
    assert sdd.source_citation("canon:some_future_list") == ""


def test_internal_source_keys_never_reach_a_customer_label():
    assert sdd.source_label("canon:eu_consolidated") == "EU Consolidated"
    assert "canon:" not in sdd.source_label("canon:some_future_list")
    assert sdd.source_label("worldbank") == "World Bank Debarment"


def test_alert_carries_the_register_and_the_jurisdiction():
    alert = sdd._designation_alert(
        "canon:eu_consolidated",
        {"uid": "eu-1", "name": "Some Entity", "countries": ["RU", "BY"],
         "programs": ["Ukraine sovereignty"], "designation_date": "2026-07-30"},
        "eu-1",
    )
    assert alert["entity"] == "Some Entity"
    assert alert["list_type"] == "EU Consolidated"
    assert alert["countries"] == "RU, BY"
    assert "europa" in alert["citation_url"] or "sanctionsmap" in alert["citation_url"]


# ── Identity of a debarment ──────────────────────────────────────────────────


def test_a_redebarment_for_a_new_period_is_a_new_event():
    rec = {"name": "Contractor Ltd", "ineligibility_from": "2026-01-01",
           "ineligibility_to": "2027-01-01"}
    same = dict(rec)
    later = {**rec, "ineligibility_from": "2028-01-01", "ineligibility_to": "2030-01-01"}

    assert sdd._record_id("worldbank", rec) == sdd._record_id("worldbank", same), \
        "the same debarment re-alerts on every run"
    assert sdd._record_id("worldbank", rec) != sdd._record_id("worldbank", later), \
        "a fresh debarment period is a new decision and must alert"


def test_canonical_records_diff_on_their_upstream_id_not_their_name():
    a = {"uid": "eu-42", "name": "ACME SARL"}
    renamed = {"uid": "eu-42", "name": "ACME S.A.R.L."}
    assert sdd._record_id("canon:eu_consolidated", a) == sdd._record_id("canon:eu_consolidated", renamed), \
        "a cosmetic name edit upstream would re-alert as a new designation"


# ── The heartbeat ────────────────────────────────────────────────────────────


def test_the_lane_has_its_own_schedule_and_is_not_a_weekly_afterthought():
    cfg = yaml.safe_load((_REPO / "aria_service" / "autonomous" / "tasks.yaml").read_text(encoding="utf-8"))
    tasks = cfg.get("tasks") or []
    watch = next((t for t in tasks if t.get("id") == "HOURLY-SANCTIONS-DESIGNATIONS"), None)
    assert watch is not None, "the designation lane has no schedule of its own"
    assert watch["enabled"] is True
    assert watch["tool_chain"][0]["tool"] == "sanctions_designation_watch"

    minute, hour, *_ = str(watch["cron"]).split()
    assert hour == "*", f"the lane must not run on a daily/weekly cron (got {watch['cron']!r})"
    assert float(watch.get("cost_cap_usd", 1)) == 0.0, "a list fetch + set diff costs no LLM spend"


def test_the_watch_tool_is_actually_dispatched():
    """A scheduled tool with no handler is a task that silently does nothing."""
    src = (_REPO / "aria_service" / "autonomous" / "tasks.py").read_text(encoding="utf-8")
    assert 'tool_kind == "sanctions_designation_watch"' in src, (
        "tasks.yaml schedules a tool the dispatcher does not implement"
    )
    handler = src.split('tool_kind == "sanctions_designation_watch"', 1)[1][:600]
    assert "run_designation_diff" in handler


def test_the_piggyback_failure_branch_is_no_longer_silent():
    """§21a — a path is wired only when its FAILURE branch emits."""
    src = (_REPO / "aria_service" / "autonomous" / "tasks.py").read_text(encoding="utf-8")
    block = src.split("R-F2560 — refresh the designation-diff feed", 1)[1][:1400]
    assert "wire_failure" in block, "the designation diff still fails silently"
    assert "except Exception:\n            pass" not in block


def test_diff_read_failure_reaches_the_brain(monkeypatch):
    from aria_service.intel import golden_intel_bridge as gib
    failures = []
    monkeypatch.setattr(gib, "wire_failure", lambda *a, **kw: failures.append((a, kw)))

    async def boom(**kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(sdd, "get_designation_alerts", boom)
    out = _run(gib._sanctions_diff_adapter())
    assert out == []
    assert failures, "a dead designation lane reached no sink"
