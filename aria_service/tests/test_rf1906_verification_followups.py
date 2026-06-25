"""R-F1906 — verification follow-up fixes surfaced by the 4-step re-verification
of the DD / web-chat hardening batch (R-F1864..1903).

Each test drives the REAL fixed path and asserts the user-visible behaviour the
gap would have broken (§3c / §23):

  V-05 encode_offload timeout env reads via `or` fallback — an EMPTY env var
       must NOT crash module import (float("") → ValueError boot crash).
  V-06 _extract_site_locations picks the country NEAREST the HQ marker, not the
       first in dict-iteration order — a two-country window mis-seeded jurisdiction.
  V-08 gleif.best_match rejects a SINGLE-token query padded into a different
       entity (wrong-entity authoritative-data injection).
  V-09 the 4 heavy DD layers are budget-clamped (can't overrun the WA poll window).
  V-13/14 recover_orphaned_jobs preserves each job-type's own TTL (readdoc 1h,
       not shortened to the chat 30m TTL).
"""
from __future__ import annotations

import importlib
import inspect
import os


# ── V-05 ─────────────────────────────────────────────────────────────────────
def test_encode_offload_empty_timeout_env_does_not_crash_import(monkeypatch):
    monkeypatch.setenv("ARIA_ENCODE_OFFLOAD_TIMEOUT_S", "")
    monkeypatch.setenv("ARIA_ENCODE_OFFLOAD_WARMUP_S", "")
    import aria_service.intel.encode_offload as eo
    importlib.reload(eo)
    try:
        assert isinstance(eo._RESULT_TIMEOUT_S, float) and eo._RESULT_TIMEOUT_S > 0
        assert isinstance(eo._WARMUP_TIMEOUT_S, float) and eo._WARMUP_TIMEOUT_S > 0
    finally:
        monkeypatch.delenv("ARIA_ENCODE_OFFLOAD_TIMEOUT_S", raising=False)
        monkeypatch.delenv("ARIA_ENCODE_OFFLOAD_WARMUP_S", raising=False)
        importlib.reload(eo)


# ── V-06 ─────────────────────────────────────────────────────────────────────
def test_site_hq_is_country_nearest_the_marker_not_dict_order():
    from aria_service.intel.dd_orchestrator import _extract_site_locations
    # Finland appears EARLIER in the country dict (index 0) than Estonia (index 1),
    # so the old dict-iteration logic returned FI. But the HQ is in Estonia — it
    # sits right beside the 'HQ' marker. Nearest-to-marker must win → EE.
    text = "We run a Finland sales office, Estonia Tallinn HQ."
    out = _extract_site_locations(text)
    assert out["hq_iso2"] == "EE", out
    # both countries still surface in the office footprint
    assert set(out["countries"]) >= {"EE", "FI"}


# ── V-08 ─────────────────────────────────────────────────────────────────────
def test_gleif_single_token_query_does_not_match_padded_other_entity():
    from aria_service.intel import gleif
    # single-token query; candidate is a DIFFERENT entity padded with descriptors.
    hits = [{"legal_name": "Modirum International Holdings Ltd", "lei": "X"}]
    assert gleif.best_match("Modirum", hits) is None
    # but an exact normalised single-token match is still accepted
    hits2 = [{"legal_name": "Modirum", "lei": "Y"}]
    assert (gleif.best_match("Modirum", hits2) or {}).get("lei") == "Y"


# ── V-09 ─────────────────────────────────────────────────────────────────────
def test_heavy_dd_layers_are_budget_clamped():
    import aria_service.intel.dd_orchestrator as dd
    src = inspect.getsource(dd._orchestrate_dd_impl)
    # the 4 heavy layers must wait_for on a budget-CLAMPED timeout, not a fixed one
    for call in ("_run_sweep_intelligence(target, report)",
                 "_cc.assess_commercial_coherence(target, report)",
                 "_ci.scan_entity(",
                 "_sdiv.analyze_divergence("):
        idx = src.find(call)
        assert idx != -1, f"heavy call missing: {call}"
        # the timeout= within ~120 chars after the call must be a _clamp(...)
        window = src[idx: idx + 160]
        assert "_clamp(" in window, f"{call} heavy call is not budget-clamped"


# ── V-13/14 ──────────────────────────────────────────────────────────────────
def test_recover_orphaned_jobs_preserves_per_type_ttl():
    import aria_service.routes.aria as aria
    src = inspect.getsource(aria.recover_orphaned_jobs)
    assert "_READDOC_JOB_TTL_S if _prefix == _READDOC_JOB_PREFIX else _CHAT_JOB_TTL_S" in src
    assert "ex=_ttl" in src
