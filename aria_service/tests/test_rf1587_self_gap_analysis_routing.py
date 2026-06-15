"""R-F1587 — route system/ecosystem gap-analysis queries to self_introspect.

Live WhatsApp failure 2026-06-15: the operator asked for a "system gap
analysis" of ARIA's ecosystem. The request matched neither the capability
nor the state introspection regex (no "your X" / "how many" construction),
so it fell through — "DD"/"deep" phrasings hit company dd_orchestrate (the
"checking multiple sources" interim), plain ones hit the from-memory LLM,
and ARIA answered a DIFFERENT question four turns running (UAE/Swiss law,
email drafts, a DeltaGuard DD) and fabricated Swiss legal citations.

R-F1587 adds is_self_analysis_request() and ORs it into
is_capability_introspection_query() (the routes/aria.py R-F399 gate that
dispatches tool=="self_introspect", checked BEFORE dd_orchestrate). These are
CAPABILITY tests on the real routing predicate the chat handler calls.
"""
from __future__ import annotations

import pytest

from aria_service.intel.self_infra_detector import (
    is_capability_introspection_query as routes_to_self_introspect,
    is_self_analysis_request,
)


# Phrasings the operator actually used / would use — MUST route to self_introspect.
SELF_PHRASINGS = [
    "do a system gap analysis",
    "give me a system gap analysis",
    "do a full gap analysis on your ecosystem",
    "analyse the gaps in your ecosystem",
    "do a gap analysis of your system",
    "do a deep DD of your ecosystem",
    "run a self-assessment",
    "do a self-audit of your capabilities",
    "review of your ecosystem",
    "ecosystem gap analysis",
]

# External analysis requests — must NOT be hijacked to self_introspect.
EXTERNAL_PHRASINGS = [
    "do a gap analysis on Acme Corp",
    "gap analysis of the Romanian defence sector",
    "do a DD on deltaguard.org",
    "review the NDA for me",
    "analyse the gaps in this contract",
]


@pytest.mark.parametrize("msg", SELF_PHRASINGS)
def test_rf1587_self_analysis_routes_to_introspect(msg):
    assert is_self_analysis_request(msg) is True, (
        f"R-F1587: {msg!r} not recognised as a self-analysis request — it will "
        f"fall through to research/DD/from-memory (the WA misroute)."
    )
    assert routes_to_self_introspect(msg) is True, (
        f"R-F1587: {msg!r} does not route to self_introspect via the chat "
        f"handler's R-F399 gate."
    )


@pytest.mark.parametrize("msg", EXTERNAL_PHRASINGS)
def test_rf1587_external_analysis_stays_external(msg):
    assert is_self_analysis_request(msg) is False, (
        f"R-F1587 over-match: {msg!r} is an EXTERNAL analysis request but was "
        f"hijacked to self_introspect — anchor on the self-reference."
    )


def test_rf1587_does_not_regress_existing_capability_queries():
    """The existing introspection phrasings must still route."""
    for msg in (
        "how many facts do you have",
        "what's your build",
        "why are you unavailable",
        "what are your current system diagnostics",
    ):
        assert routes_to_self_introspect(msg) is True, (
            f"R-F1587 regression: {msg!r} no longer routes to self_introspect."
        )
