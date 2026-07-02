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
    assert "no us-listed" in fs[0]["title"].lower()   # honest, source-scoped — not a clean bill
    assert "info" == fs[0]["severity"]                # UNKNOWN is never a positive/clean verdict
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


@pytest.mark.asyncio
async def test_flow_metric_duration_guard_rejects_extended_year(monkeypatch):
    """R-F2326: an EXTENDED-year (>1yr, e.g. a 15–18mo transition 10-KT) flow row ending on
    the fiscal-year-end must NOT be reported as the annual figure — it OVERSTATES revenue.
    The one-sided (<300d) guard missed this; the two-sided 350–380d window catches it."""
    async def fake_resolve(name): return ("0000000123", "Extended Co")
    async def fake_facts(cik):
        return {"cik": 123, "entityName": "Extended Co", "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 1000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
                {"start": "2022-06-01", "end": "2023-12-31", "val": 1800, "fy": 2023, "fp": "FY", "form": "10-KT", "filed": "2024-02-01"},
            ]}},
            "Assets": {"units": {"USD": [{"end": "2023-12-31", "val": 2000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
            "Liabilities": {"units": {"USD": [{"end": "2023-12-31", "val": 500, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
        }}}
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    monkeypatch.setattr(fh, "_fetch_company_facts", fake_facts)
    r = await fh._assess_sec_edgar("Extended Co")
    assert r["financials"]["2023"]["revenue"] == 1000   # 364-day annual, NOT the 578-day extended 1800


def test_is_annual_duration_window():
    """R-F2326: the two-sided window accepts ~annual periods (52/53-week + leap) and rejects
    quarterly/stub AND extended-year periods; a missing/unparseable start fails closed."""
    assert fh._is_annual_duration("2023-01-01", "2023-12-31") is True    # 364d calendar year
    assert fh._is_annual_duration("2022-12-31", "2023-12-31") is True    # 365d
    assert fh._is_annual_duration("2023-01-02", "2024-01-06") is True    # 369d (53-week fiscal)
    assert fh._is_annual_duration("2023-10-01", "2023-12-31") is False   # 91d stub
    assert fh._is_annual_duration("2022-06-01", "2023-12-31") is False   # 578d extended year
    assert fh._is_annual_duration("", "2023-12-31") is False             # missing start → closed
    assert fh._is_annual_duration("not-a-date", "2023-12-31") is False   # unparseable → closed


@pytest.mark.asyncio
async def test_instant_balance_sheet_tag_not_duration_filtered(monkeypatch):
    """R-F2326: the duration window is scoped to FLOW tags only — a balance-sheet INSTANT
    row must NEVER be dropped by duration logic even if it carries a spurious short `start`."""
    async def fake_resolve(name): return ("0000000123", "Instant Co")
    async def fake_facts(cik):
        return {"cik": 123, "entityName": "Instant Co", "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 1000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
            # Assets carries a spurious 0-day `start` — must still be kept (instant, not flow).
            "Assets": {"units": {"USD": [
                {"start": "2023-12-31", "end": "2023-12-31", "val": 2000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
            "Liabilities": {"units": {"USD": [{"end": "2023-12-31", "val": 500, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
        }}}
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    monkeypatch.setattr(fh, "_fetch_company_facts", fake_facts)
    r = await fh._assess_sec_edgar("Instant Co")
    assert r["financials"]["2023"]["assets"] == 2000    # instant kept despite 0-day start
    assert r["financials"]["2023"]["revenue"] == 1000


@pytest.mark.asyncio
async def test_income_only_data_is_unknown_not_stable(monkeypatch):
    """R-F2328 (review #2): income-statement data with NO balance sheet cannot yield a
    solvency signal (Altman / leverage / liquidity), so a POSITIVE verdict is impossible —
    the result must be an honest UNKNOWN (data_available False), never STABLE/STRONG."""
    async def fake_resolve(name): return ("0000000123", "Income Only Co")
    async def fake_facts(cik):
        return {"cik": 123, "entityName": "Income Only Co", "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 5000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
            # positive net income (no loss flag), and NO Assets/Liabilities/Equity at all
            "NetIncomeLoss": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 400, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
        }}}
    monkeypatch.setattr(fh, "_resolve_cik", fake_resolve)
    monkeypatch.setattr(fh, "_fetch_company_facts", fake_facts)
    r = await fh._assess_sec_edgar("Income Only Co")
    assert r["health_verdict"] == "UNKNOWN"          # NOT "STABLE" on thin data
    assert r["data_available"] is False
    assert r.get("partial_financials")               # figures retained, but honestly UNKNOWN
    assert "not a clean" in r["summary"].lower()


@pytest.mark.asyncio
async def test_cik_single_token_low_similarity_rejected(monkeypatch):
    """R-F2328 (review #3): a candidate sharing ONE token but with fuzzy similarity below
    0.92 must NOT resolve — attaching the wrong company's financials is a decision-grade
    safety error. `_resolve_cik` returns None (→ honest UNKNOWN downstream)."""
    async def fake_tickers(): return [{"title": "Zephyr Airlines", "cik_str": "0000000999"}]
    monkeypatch.setattr(fh._sec, "_load_tickers", fake_tickers)
    # one shared 6-char token ("zephyr") but score 0.88 (< 0.92) → rejected
    monkeypatch.setattr(fh._common, "fuzzy_filter",
                        lambda hits, name, **kw: [{"title": "Zephyr Airlines",
                                                   "cik_str": "0000000999", "_match_score": 0.88}])
    assert await fh._resolve_cik("Zephyr Dynamics") is None


@pytest.mark.asyncio
async def test_cik_resolves_on_strong_single_token_or_multi_token(monkeypatch):
    """R-F2328 (review #3): the tightening must NOT over-reject — a strong single-token
    match (≥5 chars AND sim ≥0.92) OR a two-token match still resolves correctly."""
    async def fake_tickers(): return [{"title": "x", "cik_str": "1"}]
    monkeypatch.setattr(fh._sec, "_load_tickers", fake_tickers)
    # strong single token: "zephyr" (6 chars) + score 0.93 → resolves
    monkeypatch.setattr(fh._common, "fuzzy_filter",
                        lambda hits, name, **kw: [{"title": "Zephyr Airlines",
                                                   "cik_str": "0000000999", "_match_score": 0.93}])
    assert await fh._resolve_cik("Zephyr Dynamics") == ("0000000999", "Zephyr Airlines")
    # two shared tokens ("lockheed","martin") resolve even at a lower score (0.90)
    monkeypatch.setattr(fh._common, "fuzzy_filter",
                        lambda hits, name, **kw: [{"title": "Lockheed Martin Corp",
                                                   "cik_str": "0000000936", "_match_score": 0.90}])
    assert await fh._resolve_cik("Lockheed Martin") == ("0000000936", "Lockheed Martin Corp")
