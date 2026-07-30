"""R-F3512 — capture raw source responses, without creating an unmanaged PII store.

WHY. Twice this session a piece of work stopped at the same wall: R-F3482 and R-F3501
could gate the derivation and the report-assembly layer but NOT a full orchestrator
replay, because "frozen RAW source responses do not exist". Without them no gold case can
prove retrieval recall, resolver precision or end-to-end rendering, and the R-F3510
consolidation cannot accumulate real-state evidence.

THE TRAP THIS AVOIDS. A recording of DD evidence contains officer names, dates of birth,
addresses and sanctions results about identified people. Writing that to disk without the
machinery built earlier today would have created precisely the unmanaged, unerasable
store that R-F3478/R-F3484/R-F3488/R-F3490 exist to prevent — building a test fixture by
committing a GDPR breach.

So: off by default, enveloped as personal data so `erase_by_subject` can reach it and
`retention_review` can see it, and written to a gitignored path so a corpus is committed
deliberately rather than swept in by an incidental `git add`.

FAILURES ARE RECORDED TOO. A replay corpus that silently omits the adapter exceptions
would be a corpus of happy paths — and the failure modes are exactly what this session's
honesty work addresses.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aria_service.intel import dd_evidence_recorder as rec


@pytest.fixture
def recording(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_DD_RECORD_EVIDENCE", "1")
    monkeypatch.setattr(rec, "RECORDING_DIR", tmp_path / "dd_recordings")
    return tmp_path / "dd_recordings"


def test_off_by_default(monkeypatch):
    """A normal customer DD must write nothing. Recording is a deliberate act."""
    monkeypatch.delenv("ARIA_DD_RECORD_EVIDENCE", raising=False)
    assert rec.is_recording() is False
    out = rec.record_source_results(
        run_id="r1", subject_name="X", labels=["ofac_sdn"], results=[{"ok": True}])
    assert out["recorded"] is False and out["reason"] == "disabled"


def test_capability_it_records_what_the_adapters_returned(recording):
    out = rec.record_source_results(
        run_id="dd_abc", subject_name="Babcock International Group PLC",
        subject_key="company:GB:02342138", jurisdiction="uk",
        labels=["ofac_sdn", "uk_ofsi"],
        results=[{"ok": True, "hits": []}, {"ok": True, "hits": [{"name": "X"}]}])
    assert out["recorded"] is True
    body = json.loads(pathlib.Path(out["path"]).read_text(encoding="utf-8"))
    assert set(body["sources"]) == {"ofac_sdn", "uk_ofsi"}
    assert body["sources"]["uk_ofsi"]["hits"][0]["name"] == "X"


def test_the_recording_carries_the_data_subject_envelope(recording):
    """Without this it is an unmanaged PII store that erasure cannot reach."""
    out = rec.record_source_results(
        run_id="dd_abc", subject_name="Subject Ltd",
        subject_key="company:GB:0001", jurisdiction="uk",
        labels=["ofac_sdn"], results=[{"ok": True}])
    body = json.loads(pathlib.Path(out["path"]).read_text(encoding="utf-8"))
    assert body["personal_data"] is True
    assert body["data_subject_key"] == "company:GB:0001"
    assert body["data_jurisdiction"] == "uk"
    assert body["retention_class"] == rec.RECORDING_RETENTION_CLASS
    assert body["lawful_basis"]
    assert body["erasure_reachable"] is True


def test_a_recording_without_a_subject_key_says_it_is_unerasable(recording):
    """The same honesty R-F3488 applies at ingest: unkeyed personal data is flagged,
    not silently stored as though it could be erased on request."""
    out = rec.record_source_results(
        run_id="dd_xyz", subject_name="Subject Ltd",
        labels=["ofac_sdn"], results=[{"ok": True}])
    body = json.loads(pathlib.Path(out["path"]).read_text(encoding="utf-8"))
    assert body["erasure_reachable"] is False
    assert out["erasure_reachable"] is False


def test_the_retention_class_is_cdd_not_a_longer_lived_fixture_class(recording):
    """A recording of a due-diligence run IS due-diligence material. Calling it a
    'fixture' would quietly grant it a longer life than the evidence it copies."""
    assert rec.RECORDING_RETENTION_CLASS == "cdd_evidence"


def test_adapter_exceptions_are_recorded_not_dropped(recording):
    """A corpus that omits failures is a corpus of happy paths."""
    out = rec.record_source_results(
        run_id="dd_err", subject_name="S", subject_key="k",
        labels=["ofac_sdn", "acled"],
        results=[{"ok": True}, RuntimeError("upstream 503")])
    body = json.loads(pathlib.Path(out["path"]).read_text(encoding="utf-8"))
    assert body["sources"]["acled"]["error"] == "RuntimeError"
    assert "503" in body["sources"]["acled"]["detail"]


def test_unserialisable_results_do_not_lose_the_run(recording):
    """One odd adapter must not discard the whole capture."""
    class _Odd:
        pass

    out = rec.record_source_results(
        run_id="dd_odd", subject_name="S", subject_key="k",
        labels=["weird"], results=[{"obj": _Odd()}])
    assert out["recorded"] is True
    body = json.loads(pathlib.Path(out["path"]).read_text(encoding="utf-8"))
    assert body["sources"]["weird"]


def test_recording_never_raises(monkeypatch):
    """A capture must never be able to cost a report."""
    monkeypatch.setenv("ARIA_DD_RECORD_EVIDENCE", "1")
    monkeypatch.setattr(rec, "RECORDING_DIR", pathlib.Path("\x00invalid"))
    out = rec.record_source_results(
        run_id="r", subject_name="S", labels=["a"], results=[{}])
    assert out["recorded"] is False


def test_the_output_path_is_gitignored():
    """Personal data must never be committed by an incidental `git add`."""
    gi = (pathlib.Path(__file__).resolve().parents[2] / ".gitignore").read_text(
        encoding="utf-8", errors="replace")
    assert "data/eval/dd_recordings/" in gi


def test_the_orchestrator_captures_and_cannot_be_broken_by_it():
    import pathlib as _p
    src = (_p.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")
    assert "record_source_results as _rec3512" in src
    idx = src.index("record_source_results as _rec3512")
    assert "except Exception" in src[idx: idx + 1200], (
        "the capture is not guarded — it could fail a DD")
