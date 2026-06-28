"""R-F2089 (Tier 2) — LLM rate limiter bounds the GLOBAL rate across workers.

Each process keeps its own in-memory request-time window, so N web/engine workers
would otherwise allow N x ARIA_LLM_RPM globally (N x the cost-rate). The limiter
now divides its budget by ARIA_TOTAL_LLM_WORKERS so the global rate stays
~ARIA_LLM_RPM regardless of worker count. Default (1 worker) keeps the full budget.
"""
import importlib
import os

import pytest


class _FakeInner:
    name = "fake"
    is_configured = True


def _fresh_limiter(monkeypatch, *, rpm=None, workers=None):
    if rpm is not None:
        monkeypatch.setenv("ARIA_LLM_RPM", str(rpm))
    else:
        monkeypatch.delenv("ARIA_LLM_RPM", raising=False)
    if workers is not None:
        monkeypatch.setenv("ARIA_TOTAL_LLM_WORKERS", str(workers))
    else:
        monkeypatch.delenv("ARIA_TOTAL_LLM_WORKERS", raising=False)
    import aria_service.llm.rate_limiter as RL
    importlib.reload(RL)
    return RL.RateLimitedProvider(_FakeInner())


def test_rf2089_default_is_150_single_worker(monkeypatch):
    """Operator-approved default raised 50->150; single worker keeps the full budget."""
    lim = _fresh_limiter(monkeypatch, rpm=None, workers=None)
    assert lim._rpm == 150


def test_rf2089_budget_divided_across_workers(monkeypatch):
    """With N workers, each process gets RPM/N so the GLOBAL rate stays ~RPM."""
    lim = _fresh_limiter(monkeypatch, rpm=150, workers=4)
    assert lim._rpm == 150 // 4 == 37          # global = 4 * 37 = 148 ~ 150


def test_rf2089_never_below_one(monkeypatch):
    """A tiny RPM with many workers must not floor to 0 (would deadlock waits)."""
    lim = _fresh_limiter(monkeypatch, rpm=2, workers=8)
    assert lim._rpm >= 1


def test_rf2089_explicit_rpm_arg_still_divided(monkeypatch):
    """An explicit rpm= still respects the worker split (so wrappers stay honest)."""
    lim = _fresh_limiter(monkeypatch, rpm=999, workers=3)
    # rpm arg passed directly bypasses the env default but the worker split applies
    import aria_service.llm.rate_limiter as RL
    lim2 = RL.RateLimitedProvider(_FakeInner(), rpm=90)
    assert lim2._rpm == 90 // 3 == 30
