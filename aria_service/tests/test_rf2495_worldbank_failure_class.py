"""R-F2495 — the World Bank indicators overlay must expose the RAW failure class
(the actual WB API error message / empty result / wrong type), not a generic
"unexpected payload shape". Codex review #4: "WB Indicators overlay unavailable for
BR: unexpected payload shape" was undiagnosable because the real reason was hidden.
Also: country_risk_overlay must surface partial coverage + per-indicator errors.
"""
import asyncio

import aria_service.intel.sources.worldbank_indicators as wb


# ---------- failure-class classifier (the core of the fix) ----------

def test_wb_api_error_payload_classified():
    # WB v2 error format: list whose first element carries {"message":[{...}]}
    payload = [{"message": [{"id": "120", "key": "Invalid value",
                             "value": "The provided parameter value is not valid"}]}]
    fc = wb._payload_failure_class(payload)
    assert fc.startswith("wb_api_error:"), fc
    assert "not valid" in fc


def test_empty_result_classified():
    assert "empty_result" in wb._payload_failure_class([{"page": 1, "total": 0}, []])
    assert "empty_result" in wb._payload_failure_class([{"page": 1}, None])


def test_unexpected_object_and_type_classified():
    assert wb._payload_failure_class({"foo": 1, "bar": 2}).startswith("unexpected_object")
    assert wb._payload_failure_class("boom").startswith("unexpected_type")


# ---------- error return now carries failure_class ----------

def test_single_indicator_error_return_has_failure_class(monkeypatch=None):
    # Drive the real fetch with a mocked HTTP layer returning a WB error payload.
    import httpx

    class _Resp:
        status_code = 200
        class request:  # noqa
            url = "https://api.worldbank.org/v2/country/zz/indicator/BAD"
        def json(self):
            return [{"message": [{"value": "Invalid country code"}]}]

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    orig = httpx.AsyncClient
    httpx.AsyncClient = _Client
    try:
        out = asyncio.run(wb.fetch_country_indicators("ZZ", indicators=["BAD"]))
    finally:
        httpx.AsyncClient = orig
    assert out["ok"] is False
    assert out.get("failure_class", "").startswith("wb_api_error:"), out
    assert "Invalid country code" in out["failure_class"]
    assert out.get("source_url")  # URL for debugging preserved


# ---------- overlay surfaces partial coverage ----------

def test_overlay_surfaces_partial_and_errors():
    async def _fake_fetch(code, **k):
        return {
            "ok": True,
            "country_code": "BR",
            "indicators": {"NY.GDP.MKTP.CD": [{"year": "2023", "value": 2.1e12}]},
            "partial": True,
            "indicator_errors": {"MS.MIL.XPND.GD.ZS": "wb_api_error: no data"},
            "source_url": "https://api.worldbank.org/v2/country/br/indicator/...",
        }
    orig = wb.fetch_country_indicators
    wb.fetch_country_indicators = _fake_fetch
    try:
        ov = asyncio.run(wb.country_risk_overlay("BR"))
    finally:
        wb.fetch_country_indicators = orig
    assert ov["ok"] is True
    assert ov["partial"] is True
    assert "MS.MIL.XPND.GD.ZS" in ov["indicator_errors"]
    assert ov["macro"]["gdp_usd"] == 2.1e12   # recovered indicator still used


if __name__ == "__main__":
    for fn in (test_wb_api_error_payload_classified, test_empty_result_classified,
               test_unexpected_object_and_type_classified,
               test_single_indicator_error_return_has_failure_class,
               test_overlay_surfaces_partial_and_errors):
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS")
