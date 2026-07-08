"""R-F2496 — trade-flow (TBML) rollup must be never-false-clean:
  1. a COMTRADE-unavailable / INDETERMINATE batch must NOT read as "0 anomalies".
  2. real anomalies (graded FLAG/SEVERE/BLATANT) must be counted — the old
     `anomaly_tier == 'HIGH'` sum was always 0 (no such key), dropping them all.
"""
from aria_service.intel.tbml_detection import summarize_tbml_results


def test_rf2496_source_unavailable_is_not_clean():
    r = summarize_tbml_results([{"grade": "INDETERMINATE"}, {"grade": "INDETERMINATE"}])
    assert r["coverage"] == "unavailable"
    assert r["transactions_screened"] == 0
    assert r["transactions_indeterminate"] == 2
    assert r["material_anomalies"] == 0
    assert r["high_anomalies"] == 0  # back-compat alias, honest


def test_rf2496_real_anomalies_counted_by_grade():
    r = summarize_tbml_results(
        [{"grade": "OK"}, {"grade": "FLAG"}, {"grade": "SEVERE"}, {"grade": "BLATANT"}]
    )
    assert r["material_anomalies"] == 3          # FLAG + SEVERE + BLATANT
    assert r["high_anomalies"] == 3              # the old code returned 0 here
    assert r["transactions_screened"] == 4
    assert r["coverage"] == "full"


def test_rf2496_mixed_is_partial():
    r = summarize_tbml_results(
        [{"grade": "OK"}, {"grade": "INDETERMINATE"}, {"grade": "FLAG"}]
    )
    assert r["coverage"] == "partial"
    assert r["transactions_screened"] == 2
    assert r["transactions_indeterminate"] == 1
    assert r["material_anomalies"] == 1


def test_rf2496_empty_batch_is_unavailable():
    r = summarize_tbml_results([])
    assert r["coverage"] == "unavailable"
    assert r["transactions_screened"] == 0
