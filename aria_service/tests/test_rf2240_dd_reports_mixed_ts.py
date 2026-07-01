"""R-F2240 — DD library (/api/aria/dd/reports) must not 500 on mixed-type timestamps.

LIVE bug (proven via curl + server traceback): report-index entries store
`generated_at`/`created_at` mixed-type — ISO strings from report writes, float
epochs from the vault-rebuild branch (dd_orchestrator.py ~9509 copying
`last_run_at`). `_collapse_index` sorted on the raw value, so >=2 mixed entries
raised `TypeError: '<' not supported between 'str' and 'float'` → HTTP 500 on the
user's DD reports page.

These capability tests drive the ACTUAL broken function (`_collapse_index`) with a
mixed-type index and assert it returns a correctly-ordered list without raising.
They FAIL against the pre-R-F2240 code (raw-value sort) and PASS after.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_service.intel import dd_orchestrator as ddo


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def test_collapse_index_mixed_str_and_float_does_not_raise():
    """The exact 500 repro: two DIFFERENT entities, one ISO ts, one float ts.
    Pre-fix this raised TypeError in result.sort(); post-fix it returns sorted."""
    index = [
        {"entity_name": "Alpha Ltd", "jurisdiction": "GB", "run_id": "a1",
         "generated_at": "2026-06-01T10:00:00+00:00"},
        {"entity_name": "Beta LLC", "jurisdiction": "US", "run_id": "b1",
         "created_at": _epoch("2026-06-15T10:00:00+00:00")},  # float epoch
    ]
    out = ddo._collapse_index(index, limit=50)
    assert isinstance(out, list) and len(out) == 2
    # newest first (Beta, 2026-06-15) — float epoch ordered correctly vs ISO string
    assert out[0]["entity_name"] == "Beta LLC"
    assert out[1]["entity_name"] == "Alpha Ltd"


def test_collapse_index_mixed_ts_same_entity_dedup():
    """Dedup compare (e_ts > cur_ts) also compared raw mixed types — same crash
    class. Two runs of the SAME entity, one ISO one float; keep the newer."""
    index = [
        {"entity_name": "Gamma Co", "jurisdiction": "GB", "run_id": "old",
         "created_at": _epoch("2026-05-01T00:00:00+00:00")},   # float, older
        {"entity_name": "Gamma Co", "jurisdiction": "GB", "run_id": "new",
         "generated_at": "2026-06-01T00:00:00+00:00"},         # ISO, newer
    ]
    out = ddo._collapse_index(index, limit=50)
    assert len(out) == 1, "same entity+jurisdiction must collapse to one"
    assert out[0]["run_id"] == "new", "dedup must keep the newer run"


def test_iso_ts_coercion_contract():
    """_iso_ts normalizes every timestamp shape to a comparable str."""
    assert ddo._iso_ts("2026-06-01T10:00:00+00:00") == "2026-06-01T10:00:00+00:00"
    assert ddo._iso_ts(_epoch("2026-06-01T10:00:00+00:00")).startswith("2026-06-01T10:00:00")
    assert ddo._iso_ts(None) == ""
    assert ddo._iso_ts("") == ""
    assert ddo._iso_ts(True) == ""   # bool is an int subclass — must not become a ts
    # every result is a str → sorting a list of them can never raise TypeError
    keys = [ddo._iso_ts(v) for v in ("2026-01-01", _epoch("2026-06-01T00:00:00+00:00"), None, True)]
    assert all(isinstance(k, str) for k in keys)
    sorted(keys)  # must not raise


def test_collapse_index_all_float_and_all_str_still_work():
    """Regression: the homogeneous cases (all ISO, all float) still sort right."""
    all_iso = [
        {"entity_name": f"E{i}", "run_id": str(i), "generated_at": f"2026-06-0{i}T00:00:00+00:00"}
        for i in (1, 2, 3)
    ]
    out = ddo._collapse_index(all_iso, limit=50)
    assert [e["entity_name"] for e in out] == ["E3", "E2", "E1"]

    all_float = [
        {"entity_name": f"F{i}", "run_id": str(i),
         "created_at": _epoch(f"2026-06-0{i}T00:00:00+00:00")}
        for i in (1, 2, 3)
    ]
    out = ddo._collapse_index(all_float, limit=50)
    assert [e["entity_name"] for e in out] == ["F3", "F2", "F1"]
