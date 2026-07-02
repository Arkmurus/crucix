"""R-F2322 — real financial-health DD: SEC EDGAR structured financials + ratios + Altman
Z'' distress + honest UNKNOWN (never-false-clean) + multi-jurisdiction Vault + search element."""
import pytest

from aria_service.intel import financial_health as fh
from aria_service.intel import dd_vault


def _facts(year=2023, **vals):
    tagmap = {"revenue": "Revenues", "net_income": "NetIncomeLoss", "assets": "Assets",
              "assets_current": "AssetsCurrent", "liabilities": "Liabilities",
              "liabilities_current": "LiabilitiesCurrent", "equity": "StockholdersEquity",
              "retained_earnings": "RetainedEarningsAccumulatedDeficit", "ebit": "OperatingIncomeLoss"}
    gaap = {}
    for k, tag in tagmap.items():
        if vals.get(k) is not None:
            gaap[tag] = {"units": {"USD": [{"end": f"{year}-12-31", "start": f"{year-1}-12-31",
                        "val": vals[k], "fy": year, "fp": "FY", "form": "10-K", "filed": f"{year+1}-02-01"}]}}
    return {"cik": 123, "entityName": "Test Co", "facts": {"us-gaap": gaap}}


@pytest.mark.asyncio
async def test_sec_healthy(monkeypatch):
    async def fake_resolve(name): return ("0000000123", "Test Co")
    async def fake_facts(cik): return _facts(revenue=1000, net_income=200, assets=2000,
        assets_current=800, liabilities=600, liabilities_current=300, equity=1400,
        retained_earnings=900, ebit=250)
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    monkeypatch.setattr(fh, "_fetch_company_facts", fake_facts)
    r = await fh._assess_sec_edgar("Test Co")
    assert r["data_available"] is True
    assert r["health_verdict"] in ("STRONG", "STABLE")
    assert r["ratios"]["current_ratio"] == round(800 / 300, 4)
    assert r["altman_zone"] == "SAFE" and r["altman_z"] is not None
    assert r["health_verdict"] != "UNKNOWN"


@pytest.mark.asyncio
async def test_sec_distressed(monkeypatch):
    async def fake_resolve(name): return ("0000000123", "Distress Co")
    async def fake_facts(cik): return _facts(revenue=1000, net_income=-300, assets=1000,
        assets_current=200, liabilities=1200, liabilities_current=500, equity=-200,
        retained_earnings=-400, ebit=-250)
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    monkeypatch.setattr(fh, "_fetch_company_facts", fake_facts)
    r = await fh._assess_sec_edgar("Distress Co")
    assert r["health_verdict"] == "DISTRESSED"
    assert any("negative shareholders" in f for f in r["distress_flags"])
    assert r["altman_zone"] == "DISTRESS"


@pytest.mark.asyncio
async def test_sec_unknown_no_match(monkeypatch):
    async def fake_resolve(name): return None
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    r = await fh._assess_sec_edgar("Some Private Ltd")
    assert r["data_available"] is False
    assert r["health_verdict"] == "UNKNOWN"
    assert "not" in r["summary"].lower()   # honest "not publicly filed" — not a clean bill


def test_findings_severities_and_never_false_clean():
    valid = {"info", "amber", "red", "hard_stop"}
    unknown = {"data_available": False, "summary": "no public financials", "health_verdict": "UNKNOWN"}
    fs = fh.financial_health_findings(unknown)
    assert fs and all(f["severity"] in valid for f in fs)
    assert "not publicly filed" in fs[0]["title"].lower()   # honest — not a clean bill
    distress = {"data_available": True, "health_verdict": "DISTRESSED", "summary": "bad",
                "distress_flags": ["negative shareholders' equity"]}
    sev = [f["severity"] for f in fh.financial_health_findings(distress)]
    assert "red" in sev and all(s in valid for s in sev)


def test_vault_financial_profile_roundtrip(tmp_path):
    v = dd_vault.DDVault(db_path=str(tmp_path / "t.db"))
    assert v.get_financial_profile("company:US:X") is None
    assert v.set_financial_profile("company:US:X",
        {"entity": "X Corp", "health_verdict": "STABLE", "data_available": True},
        entity_name="X Corp", jurisdiction="US") is True
    got = v.get_financial_profile("company:US:X")
    assert got["health_verdict"] == "STABLE" and got["_vault_updated_at"] is not None
    assert any(r["entity_name"] == "X Corp" for r in v.search_financial_profiles("X Corp"))


@pytest.mark.asyncio
async def test_assess_vault_first_shortcircuits_sec(monkeypatch, tmp_path):
    v = dd_vault.DDVault(db_path=str(tmp_path / "t.db"))
    v.set_financial_profile("company:US:APPLE",
        {"entity": "Apple Inc.", "health_verdict": "STRONG", "data_available": True},
        entity_name="Apple Inc.", jurisdiction="US")
    monkeypatch.setattr(dd_vault, "get_vault", lambda: v)
    called = {"sec": False}
    async def fake_sec(name, cik=None):
        called["sec"] = True
        return {"data_available": False, "health_verdict": "UNKNOWN"}
    monkeypatch.setattr(fh, "_assess_sec_edgar", fake_sec)
    r = await fh.assess("Apple Inc.", canonical_id="company:US:APPLE")
    assert r["health_verdict"] == "STRONG" and r.get("from_vault") is True
    assert called["sec"] is False   # vault (pay-once) short-circuited the search


@pytest.mark.asyncio
async def test_assess_search_footprint_cross_jurisdiction(monkeypatch, tmp_path):
    v = dd_vault.DDVault(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(dd_vault, "get_vault", lambda: v)
    async def fake_sec(name, cik=None):
        return {"source": "sec_edgar_financials", "entity": name, "data_available": False,
                "health_verdict": "UNKNOWN", "financials": {}, "ratios": {},
                "summary": "not US-listed", "reason": "no SEC match"}
    monkeypatch.setattr(fh, "_assess_sec_edgar", fake_sec)

    class R:
        def __init__(s, u, t): s.url, s.title, s.snippet = u, t, "revenue"
    async def fake_search(q, max_results=6):
        return [R("https://x.ao/annual-report-2023", "Angola Defence Ltd Annual Report 2023")]
    import aria_service.intel.web_search as ws
    monkeypatch.setattr(ws, "search", fake_search)

    r = await fh.assess("Angola Defence Ltd", jurisdiction_iso2="AO", canonical_id="company:AO:ADL")
    assert r["data_available"] is False                        # still honest UNKNOWN
    assert r.get("search_footprint", {}).get("found") is True  # but search added value-added refs
    assert v.get_financial_profile("company:AO:ADL") is not None  # registered to the vault (accumulation)
    fs = fh.financial_health_findings(r)
    assert any(f["source"] == "financial_search" for f in fs)


@pytest.mark.asyncio
async def test_flow_metric_duration_guard_ignores_stub_period(monkeypatch):
    """R-F2322 review: a sub-annual (e.g. 90-day transition/stub) flow row ending on the
    fiscal-year-end must NOT be picked as the annual figure over the true 365-day row."""
    async def fake_resolve(name): return ("0000000123", "Stub Co")
    async def fake_facts(cik):
        return {"cik": 123, "entityName": "Stub Co", "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 1000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
                {"start": "2023-10-01", "end": "2023-12-31", "val": 250, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
            ]}},
            "Assets": {"units": {"USD": [{"end": "2023-12-31", "val": 2000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
            "Liabilities": {"units": {"USD": [{"end": "2023-12-31", "val": 500, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
        }}}
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    monkeypatch.setattr(fh, "_fetch_company_facts", fake_facts)
    r = await fh._assess_sec_edgar("Stub Co")
    assert r["financials"]["2023"]["revenue"] == 1000   # the 365-day row, NOT the 250 stub
