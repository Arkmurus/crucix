"""R-F561 (2026-05-16) — operator-pending dashboard panel tests.

Capability proofs:
  - env-var status flips correctly (missing/set/stale)
  - billing + manual items always render as TODO
  - gate_blockers list correctly identifies P1 items not satisfied
  - the dashboard endpoint is wired into the router
"""
from __future__ import annotations

import pytest


def test_catalogue_has_minimum_phase_a_items():
    """The catalogue must list each Phase A gate #5 env var so the
    dashboard at least surfaces what's outstanding."""
    from aria_service.intel import operator_pending as op
    keys = op.list_keys()
    for must in (
        "acled_email", "acled_password",
        "report_signing_key", "aria_output_harvest_enabled",
        "anthropic_topup", "phase_a_seed_latam_asia",
        "phase_a_design_partners", "phase_a_eval_seed",
    ):
        assert must in keys, f"R-F561 catalogue missing: {must!r}"


def test_missing_env_classified(monkeypatch):
    monkeypatch.delenv("ACLED_EMAIL", raising=False)
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    acled = next(i for i in panel["items"] if i["key"] == "acled_email")
    assert acled["status"] == "missing"
    assert acled["value_masked"] is None


def test_set_env_classified_and_masked(monkeypatch):
    monkeypatch.setenv("ACLED_EMAIL", "operator@example.com")
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    acled = next(i for i in panel["items"] if i["key"] == "acled_email")
    assert acled["status"] == "set"
    # last-4 visible, rest masked
    assert acled["value_masked"].endswith(".com")
    assert "*" in acled["value_masked"]


def test_stale_env_classified(monkeypatch):
    """Placeholder values count as not-yet-set."""
    monkeypatch.setenv("REPORT_SIGNING_KEY", "changeme-please")
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    rsk = next(i for i in panel["items"] if i["key"] == "report_signing_key")
    assert rsk["status"] == "stale"


def test_gate_blockers_listed_only_for_p1_open(monkeypatch):
    """Capability: gate_blockers should NOT include P1 items that are
    already 'set' (satisfied)."""
    monkeypatch.setenv("ACLED_EMAIL", "op@example.com")
    monkeypatch.setenv("ACLED_PASSWORD", "g7Q9_realpassword_xyz")
    monkeypatch.delenv("REPORT_SIGNING_KEY", raising=False)
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    # Satisfied — must NOT be a blocker.
    assert "acled_email" not in panel["gate_blockers"]
    assert "acled_password" not in panel["gate_blockers"]
    # Unsatisfied P1 — MUST be a blocker.
    assert "report_signing_key" in panel["gate_blockers"]


def test_manual_items_are_always_in_blockers_when_p1():
    """Capability: manual actions (no env var) can't be auto-marked
    'done' — they always appear in gate_blockers if P1."""
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    for key in ("phase_a_seed_latam_asia", "phase_a_design_partners",
                "phase_a_eval_seed"):
        assert key in panel["gate_blockers"], (
            f"{key} should appear in gate_blockers (P1 manual)."
        )


def test_counts_consistency():
    """Capability: counts must sum back to total."""
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    c = panel["counts"]
    # Every entry contributes to exactly one of {missing, stale, set,
    # manual_open, billing_open}.
    bucket_sum = (
        c.get("missing", 0)
        + c.get("stale", 0)
        + c.get("set", 0)
        + c.get("manual_open", 0)
        + c.get("billing_open", 0)
    )
    assert bucket_sum == c["total"], (
        f"R-F561 counts mismatch: buckets {bucket_sum} != total {c['total']}"
    )


def test_route_endpoint_is_registered():
    """Capability: /api/aria/operator-pending is mounted on the router."""
    from aria_service.routes import aria as aria_routes
    paths = [getattr(route, "path", "") for route in aria_routes.router.routes]
    assert any(p.endswith("/operator-pending") for p in paths), (
        f"R-F561 endpoint missing. Sample paths: {sorted(paths)[:5]}"
    )


def test_priority_one_items_sorted_first():
    """The dashboard displays in priority order. P1 first."""
    from aria_service.intel import operator_pending as op
    panel = op.build_status()
    items = panel["items"]
    last_p = 0
    for it in items:
        assert it["priority"] >= last_p, (
            f"Sort order broken: {it['key']} priority {it['priority']} "
            f"after priority {last_p}"
        )
        last_p = it["priority"]
