"""R-F680 (2026-05-18) — operator-dashboard read-only endpoints public-bypass.

Live fly logs 2026-05-18 07:09:02-06 showed the operator dashboard
hitting five endpoints without a bearer token and getting 401:
  - /health/error-streak                (gate #3 indicator)
  - /dd/quarantine/closure-summary      (gate #4 indicator)
  - /learning/reading-queue             (what ARIA is reading)
  - /autonomous/cost-summary            (billing burn — SENSITIVE)
  - /mastery/topic-completion           (topic-progress aggregate)

R-F680 adds the four non-sensitive endpoints to
_PUBLIC_AUTH_BYPASS_PATHS following the R-F677 precedent.
/autonomous/cost-summary stays behind auth on purpose — reveals
the operator's LLM cost burn.
"""
from __future__ import annotations


def test_rf680_gate3_error_streak_public():
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/health/error-streak" in aria_routes._PUBLIC_AUTH_BYPASS_PATHS


def test_rf680_gate4_dd_quarantine_public():
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/dd/quarantine/closure-summary" in aria_routes._PUBLIC_AUTH_BYPASS_PATHS


def test_rf680_learning_reading_queue_public():
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/learning/reading-queue" in aria_routes._PUBLIC_AUTH_BYPASS_PATHS


def test_rf680_mastery_topic_completion_public():
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/mastery/topic-completion" in aria_routes._PUBLIC_AUTH_BYPASS_PATHS


def test_rf680_cost_summary_STAYS_auth_required():
    """Explicit guard: /autonomous/cost-summary must NOT be public.
    Billing burn data is sensitive and the operator has not requested
    that it be exposed."""
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/autonomous/cost-summary" not in aria_routes._PUBLIC_AUTH_BYPASS_PATHS, (
        "R-F680: cost-summary must stay behind auth — exposes billing data"
    )
