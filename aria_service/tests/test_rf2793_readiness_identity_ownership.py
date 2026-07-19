"""R-F2793 — decision-readiness must not answer identity/ownership on junk.

THE DEFECTS (probed against R-F2786 before this fix):

  IDENTITY  — ``_substantive()`` only rejected a five-item denylist
              {unknown, unavailable, n/a, na, none}. Every other string passed,
              so a company whose registry status was ``dissolved``,
              ``struck off``, ``liquidation``, ``pending`` — or literally
              ``banana`` — answered "Verified legal identity: ANSWERED".
              A struck-off entity is arguably the single most decision-critical
              negative identity signal in due diligence; certifying it as
              verified identity is a false clean.

  OWNERSHIP — ``ownership_present`` was the truthiness of the shareholders
              list, so ``[{}]`` (an empty dict) or ``['x']`` answered
              "Ownership and control: ANSWERED".

Both were flagged as open questions by the author of R-F2786 (their Q2 and Q3).
Probing confirmed both instincts were right.

CONTRACT: identity is answered only on a recognisably LIVE registry status;
ownership only on a holder with actual substance. Unrecognised values fail
CLOSED — "we cannot tell whether this company is alive" is not "verified".
"""

from aria_service.intel.dd_schema import _dd_decision_readiness


def _q(report: dict, key: str) -> dict:
    return (_dd_decision_readiness(report).get("questions") or {}).get(key) or {}


def _identity(status, **extra) -> dict:
    ident = {
        "registration_number": "12345678",
        "registration_status": status,
        "incorporation_date": "2011-04-02",
        "directors": [{"name": "J. Doe"}],
    }
    ident.update(extra)
    return {"identity": ident, "compliance": {}, "network": {}, "adverse_media": {}}


# ── IDENTITY ────────────────────────────────────────────────────────────────

DEAD_STATUSES = [
    "dissolved", "Dissolved", "liquidation", "in liquidation", "struck off",
    "struck-off", "closed", "terminated", "administration", "receivership",
    "insolvent", "cancelled", "revoked", "suspended", "inactive", "defunct",
    "converted-closed",
]


def test_dead_registry_status_never_answers_identity():
    """A dissolved/struck-off company must NOT read as verified legal identity."""
    for status in DEAD_STATUSES:
        q = _q(_identity(status), "identity")
        assert q.get("answered") is False, (
            f"registration_status={status!r} must not answer identity"
        )
        assert q.get("blocker"), f"{status!r} must name a blocker"


def test_unrecognised_registry_status_fails_closed():
    """If we cannot tell the company is alive, identity is not verified."""
    for status in ["banana", "???", "pending", "proposal", "unknown-to-us"]:
        q = _q(_identity(status), "identity")
        assert q.get("answered") is False, (
            f"unrecognised registration_status={status!r} must fail closed"
        )


def test_live_registry_status_answers_identity():
    """The legitimate case must keep working — no over-tightening."""
    for status in ["active", "Active", "ACTIVE", "registered", "live",
                   "in good standing", "current", "incorporated"]:
        q = _q(_identity(status), "identity")
        assert q.get("answered") is True, (
            f"registration_status={status!r} is a live company and must answer identity"
        )


def test_dead_status_blocker_names_the_actual_status():
    """The blocker must be actionable, not generic."""
    q = _q(_identity("dissolved"), "identity")
    assert "dissolved" in (q.get("blocker") or "").lower(), (
        f"blocker must name the offending status, got {q.get('blocker')!r}"
    )


# ── OWNERSHIP ───────────────────────────────────────────────────────────────

def _ownership(shareholders) -> dict:
    return {
        "identity": {"shareholders": shareholders},
        "compliance": {}, "network": {}, "adverse_media": {},
    }


def test_junk_shareholder_entries_never_answer_ownership():
    for junk in [[{}], [{"name": ""}], [{"name": "unknown"}], ["x"], [""], [None], [{}, {}]]:
        q = _q(_ownership(junk), "ownership_control")
        assert q.get("answered") is False, (
            f"shareholders={junk!r} has no substance and must not answer ownership"
        )


def test_substantive_shareholder_answers_ownership():
    for good in [
        [{"name": "Acme Holdings BV"}],
        ["Acme Holdings BV"],
        [{"name": "A. Person", "percentage": 51}],
    ]:
        q = _q(_ownership(good), "ownership_control")
        assert q.get("answered") is True, (
            f"shareholders={good!r} is substantive and must answer ownership"
        )


def test_substantive_ubo_chain_answers_ownership():
    report = {
        "identity": {}, "compliance": {}, "adverse_media": {},
        "network": {"ubo_chain": [{"name": "Ultimate Owner Ltd"}]},
    }
    assert _q(report, "ownership_control").get("answered") is True
