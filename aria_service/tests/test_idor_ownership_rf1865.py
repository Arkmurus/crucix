# -*- coding: utf-8 -*-
"""Capability tests for the IDOR-ownership hardening batch R-F1865 (DD-02..07).

Each test invokes the ACTUAL fixed endpoint coroutine (not a helper proxy) and
asserts the user-visible security outcome, per CLAUDE.md §3c / §23:

  * the OWNER can read their own record,
  * a CROSS-TENANT caller is denied (404 / not_found — no existence leak,
    no confidential content),
  * an ADMIN / internal caller (user_id="") still resolves,
  * a LEGACY record with no stored user_id stays readable (backward compat).

  DD-02  GET /trace/{trace_id}              trace_get_ep
  DD-03  GET /scratchpad/{trace_id}         scratchpad_for_trace_ep  (owner via trace)
  DD-04  GET /feedback/{feedback_id}        feedback_get_ep
  DD-05  GET /honesty/{judgment_id}         honesty_get_ep
  DD-06  GET /verify/{verification_id}      verify_get_ep
  DD-07  GET /ocr/result/{job_id}           ocr_result_ep
"""
import asyncio

import pytest
from fastapi import HTTPException


def _run(coro):
    return asyncio.run(coro)


def _seed(prefix_module, prefix_attr, rec_id, record):
    """Seed a record straight into the module's redis_store under its key prefix."""
    import importlib
    mod = importlib.import_module(prefix_module)
    prefix = getattr(mod, prefix_attr)
    _run(mod.rs.set_json(f"{prefix}{rec_id}", record))


# ── DD-02 — /trace/{trace_id} ─────────────────────────────────────────────────
def test_dd02_trace_owner_enforced():
    from aria_service.routes import aria

    _seed("aria_service.intel.trace_stream", "TRACE_RECORD_PREFIX", "rf1865_tr",
          {"id": "rf1865_tr", "question": "CONFIDENTIAL Q", "user_id": "alice"})

    owner = _run(aria.trace_get_ep("rf1865_tr", user_id="alice"))
    assert owner.get("question") == "CONFIDENTIAL Q"

    with pytest.raises(HTTPException) as ei:
        _run(aria.trace_get_ep("rf1865_tr", user_id="mallory"))
    assert ei.value.status_code == 404

    # Admin / internal path still works.
    admin = _run(aria.trace_get_ep("rf1865_tr", user_id=""))
    assert admin.get("question") == "CONFIDENTIAL Q"


def test_dd02_trace_legacy_record_readable():
    from aria_service.routes import aria

    _seed("aria_service.intel.trace_stream", "TRACE_RECORD_PREFIX", "rf1865_tr_legacy",
          {"id": "rf1865_tr_legacy", "question": "LEGACY"})  # no user_id

    out = _run(aria.trace_get_ep("rf1865_tr_legacy", user_id="anyone"))
    assert out.get("question") == "LEGACY"


def test_dd02_start_trace_stamps_user_id():
    from aria_service.intel import trace_stream

    tid = _run(trace_stream.start_trace(question="q", user_id="bob"))
    rec = _run(trace_stream.get_trace(tid))
    assert rec.get("user_id") == "bob"


# ── DD-03 — /scratchpad/{trace_id} (owner derived from the trace) ─────────────
def test_dd03_scratchpad_owner_enforced_via_trace():
    from aria_service.routes import aria
    from aria_service.intel import redis_store as rs

    _seed("aria_service.intel.trace_stream", "TRACE_RECORD_PREFIX", "rf1865_sp",
          {"id": "rf1865_sp", "user_id": "alice"})
    _run(rs.set_json("crucix:scratchpad:rf1865_sp", {"steps": ["SECRET REASONING"]}))

    owner = _run(aria.scratchpad_for_trace_ep("rf1865_sp", user_id="alice"))
    assert owner.get("ok") is True
    assert owner.get("scratchpad", {}).get("steps") == ["SECRET REASONING"]

    attacker = _run(aria.scratchpad_for_trace_ep("rf1865_sp", user_id="mallory"))
    assert attacker.get("ok") is False
    assert attacker.get("error") == "not_found"
    assert "scratchpad" not in attacker

    admin = _run(aria.scratchpad_for_trace_ep("rf1865_sp", user_id=""))
    assert admin.get("ok") is True


# ── DD-04 — /feedback/{feedback_id} ───────────────────────────────────────────
def test_dd04_feedback_owner_enforced():
    from aria_service.routes import aria

    _seed("aria_service.intel.feedback", "FEEDBACK_KEY_PREFIX", "rf1865_fb",
          {"id": "rf1865_fb", "question": "CONFIDENTIAL", "user_id": "alice"})

    owner = _run(aria.feedback_get_ep("rf1865_fb", user_id="alice"))
    assert owner.get("question") == "CONFIDENTIAL"

    with pytest.raises(HTTPException) as ei:
        _run(aria.feedback_get_ep("rf1865_fb", user_id="mallory"))
    assert ei.value.status_code == 404

    admin = _run(aria.feedback_get_ep("rf1865_fb", user_id=""))
    assert admin.get("question") == "CONFIDENTIAL"


# ── DD-05 — /honesty/{judgment_id} ────────────────────────────────────────────
def test_dd05_honesty_owner_enforced():
    from aria_service.routes import aria

    _seed("aria_service.intel.honesty_judge", "JUDGMENT_KEY_PREFIX", "rf1865_jdg",
          {"id": "rf1865_jdg", "response_preview": "CONFIDENTIAL", "user_id": "alice"})

    owner = _run(aria.honesty_get_ep("rf1865_jdg", user_id="alice"))
    assert owner.get("response_preview") == "CONFIDENTIAL"

    with pytest.raises(HTTPException) as ei:
        _run(aria.honesty_get_ep("rf1865_jdg", user_id="mallory"))
    assert ei.value.status_code == 404

    admin = _run(aria.honesty_get_ep("rf1865_jdg", user_id=""))
    assert admin.get("response_preview") == "CONFIDENTIAL"


# ── DD-06 — /verify/{verification_id} ─────────────────────────────────────────
def test_dd06_verify_owner_enforced():
    from aria_service.routes import aria

    _seed("aria_service.intel.source_verifier", "VERIFICATION_KEY_PREFIX", "rf1865_vfy",
          {"id": "rf1865_vfy", "response_preview": "CONFIDENTIAL", "user_id": "alice"})

    owner = _run(aria.verify_get_ep("rf1865_vfy", user_id="alice"))
    assert owner.get("response_preview") == "CONFIDENTIAL"

    with pytest.raises(HTTPException) as ei:
        _run(aria.verify_get_ep("rf1865_vfy", user_id="mallory"))
    assert ei.value.status_code == 404

    admin = _run(aria.verify_get_ep("rf1865_vfy", user_id=""))
    assert admin.get("response_preview") == "CONFIDENTIAL"


# ── DD-07 — /ocr/result/{job_id} ──────────────────────────────────────────────
def test_dd07_ocr_result_owner_enforced():
    from aria_service.routes import aria

    _run(aria._ocr_job_set("rf1865_ocr",
                           {"status": "done", "result": {"text": "SECRET OCR"},
                            "user_id": "alice"}))

    owner = _run(aria.ocr_result_ep("rf1865_ocr", user_id="alice"))
    assert owner.get("status") == "done"
    assert owner.get("result", {}).get("text") == "SECRET OCR"

    attacker = _run(aria.ocr_result_ep("rf1865_ocr", user_id="mallory"))
    assert attacker.get("status") == "not_found", attacker
    assert "result" not in attacker

    admin = _run(aria.ocr_result_ep("rf1865_ocr", user_id=""))
    assert admin.get("status") == "done"


def test_dd07_ocr_result_legacy_job_readable():
    from aria_service.routes import aria

    _run(aria._ocr_job_set("rf1865_ocr_legacy",
                           {"status": "done", "result": {"text": "LEGACY"}}))  # no user_id

    out = _run(aria.ocr_result_ep("rf1865_ocr_legacy", user_id="anyone"))
    assert out.get("status") == "done"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
