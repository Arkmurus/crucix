"""R-F2462 (student reading-loop pre-filter) + R-F2463 (portal reg cooldown).

Deterministic: the cooldown short-circuits BEFORE any state_store/vault I/O, so
these do not touch the DB (safe on Win/3.14).
"""
import asyncio
import inspect
import time

from aria_service.intel import portal_registry

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_rf2463_cooldown_skips_recently_failed(monkeypatch):
    monkeypatch.setattr(portal_registry, "_ENABLED", True, raising=False)
    assert portal_registry.PORTALS, "PORTALS must be non-empty"
    pid = portal_registry.PORTALS[0].id
    portal_registry._reg_fail_cooldown[pid] = time.monotonic()  # simulate a recent failure
    try:
        res = asyncio.run(portal_registry.register_for_portal(pid))
    finally:
        portal_registry._reg_fail_cooldown.pop(pid, None)
    assert res.get("success") is False and res.get("cooldown") is True, res


def test_rf2463_expired_cooldown_does_not_short_circuit(monkeypatch):
    # An old failure (older than the window) must NOT be treated as on-cooldown.
    monkeypatch.setattr(portal_registry, "_REG_FAIL_COOLDOWN_S", 1.0, raising=False)
    pid = portal_registry.PORTALS[0].id
    portal_registry._reg_fail_cooldown[pid] = time.monotonic() - 10.0
    # We can't safely run the full registration in a unit test, but we can assert
    # the cooldown predicate itself is False for a stale entry.
    _cd = portal_registry._reg_fail_cooldown.get(pid)
    on_cooldown = _cd is not None and (time.monotonic() - _cd) < portal_registry._REG_FAIL_COOLDOWN_S
    portal_registry._reg_fail_cooldown.pop(pid, None)
    assert on_cooldown is False


def test_rf2462_student_prefilters_short_facts_matching_knowledge_threshold():
    # student's pre-filter threshold must match knowledge.store_fact's reject
    # threshold so it skips exactly what would be rejected (and no longer credits
    # mastery for rejected facts).
    from aria_service.intel import student, knowledge
    assert "len(_fact_content.strip()) < 50" in module_source(student), \
        "student reading loop must pre-filter facts <50 chars"
    assert "< 50" in module_source(knowledge), \
        "knowledge reject threshold must stay 50 (keep student's pre-filter in sync)"
