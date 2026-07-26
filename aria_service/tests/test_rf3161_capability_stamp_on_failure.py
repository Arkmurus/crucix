"""R-F3161 — a transient failure was stamped as a completed capability, freezing
"financial capacity UNKNOWN" in the vault and making R-F3146 unreachable.

MEASURED, live, twice on Babcock International Group plc:

  dd_6e11c978dc86  issuer_financials {"ok": false,
                     "reason": "retrieved the document but could not parse it: ",
                     "gates": {"provenance": true, "retrievable": true,
                               "text_layer": false}}

  dd_440ef012b068  IDENTICAL reason string, byte for byte — AFTER R-F3146 was
                   deployed and live (build_rev dcdaeec3 contains it).

That second run is the tell. Under R-F3146 the string "could not parse it: " with
nothing after the colon is IMPOSSIBLE: a timeout renders "did not finish parsing
within 45s", and any exception with an empty str() falls back to its type name. The
fixed code could not have run. The profile showed why:

    from_vault: true, vault_age_days: 0.0
    _capabilities: ["issuer_report", "registry_accounts", "registry_figures"]

`issuer_report` was stamped DESPITE having failed, so `missing_capabilities()`
reported nothing missing, `backfill_missing_capabilities` never re-ran it, and the
stale failure blob was replayed from the vault on every subsequent DD.

TWO stamping paths were wrong:
  1. `_stamp_capabilities` unioned ALL of FINANCIAL_CAPABILITIES unconditionally.
  2. the backfill loop stamped whenever the enricher RETURNED — and an enricher
     signals a transient obstacle by returning False, not by raising, so the
     `except` branch never saw it.

R-F3128's docstring already promised the correct contract ("stamped only on success,
so a blocked fetch or a refused gate retries on the next read instead of freezing an
UNKNOWN for 30 days"). The code never honoured it. This is the R-F2834 masking defect
recurring inside the mechanism built to prevent it.

THE DISTINCTION THAT MATTERS: "consulted, found nothing" is real negative evidence and
must stay stamped (§15 pay-once). "Could not complete" is not evidence about the entity
at all.
"""
import asyncio

import pytest

from aria_service.intel import financial_health as fh


# The exact blob the live vault held for Babcock.
BABCOCK_TRANSIENT = {
    "ok": False,
    "reason": "retrieved the document but could not parse it: ",
    "gates": {"provenance": True, "retrievable": True, "text_layer": False},
}
# G1 looked and the issuer publishes no such document — a real negative.
NO_DOCUMENT = {
    "ok": False,
    "reason": ("no annual report found on the issuer's own domain — a third party's "
               "summary is not the issuer's accounts"),
    "gates": {"provenance": False},
}
ANSWERED = {"ok": True, "net_assets": 2_121_000_000, "currency": "GBP"}


def test_rf3161_the_live_babcock_failure_is_not_stamped():
    """THE DEFECT: this profile was served from the vault forever."""
    profile = {"issuer_financials": dict(BABCOCK_TRANSIENT)}
    assert fh._capability_retry_needed("issuer_report", profile) is True
    fh._stamp_capabilities(profile)
    assert "issuer_report" not in profile["_capabilities"], (
        "R-F3161 REGRESSION: a transient parse failure is stamped again — the vault "
        "will replay this UNKNOWN and no later fix can reach the path")
    assert fh.missing_capabilities(profile) == ["issuer_report"], (
        "the next read must see it as missing so the backfill retries")


@pytest.mark.parametrize("reason,gates", [
    ("the issuer's document did not finish parsing within 45s (18,000,000 bytes)",
     {"provenance": True, "retrievable": True, "text_layer": False}),
    ("the issuer's document could not be retrieved (HTTP 403) — the site refused",
     {"provenance": True, "retrievable": False}),
    ("no LLM available to read the document", {}),
    ("model call failed: ReadTimeout", {"provenance": True, "retrievable": True,
                                        "text_layer": True}),
])
def test_rf3161_every_our_side_obstacle_retries(reason, gates):
    """None of these is evidence ABOUT THE ENTITY."""
    profile = {"issuer_financials": {"ok": False, "reason": reason, "gates": gates}}
    assert fh._capability_retry_needed("issuer_report", profile) is True, reason


def test_rf3161_consulted_found_nothing_stays_stamped():
    """§15 pay-once-remember-forever: do NOT re-pay to re-learn a real negative."""
    profile = {"issuer_financials": dict(NO_DOCUMENT)}
    assert fh._capability_retry_needed("issuer_report", profile) is False
    fh._stamp_capabilities(profile)
    assert "issuer_report" in profile["_capabilities"]
    assert fh.missing_capabilities(profile) == []


def test_rf3161_success_stays_stamped():
    profile = {"issuer_financials": dict(ANSWERED)}
    fh._stamp_capabilities(profile)
    assert "issuer_report" in profile["_capabilities"]


def test_rf3161_never_attempted_is_unchanged():
    """A stronger route answered first, so the issuer route never ran — prior
    behaviour preserved; do not start re-enriching every healthy profile."""
    profile = {}
    fh._stamp_capabilities(profile)
    assert set(profile["_capabilities"]) == set(fh.FINANCIAL_CAPABILITIES)


def test_rf3161_other_capabilities_are_untouched():
    """Only the issuer route can report a transient obstacle today."""
    profile = {"issuer_financials": dict(BABCOCK_TRANSIENT)}
    fh._stamp_capabilities(profile)
    assert "registry_accounts" in profile["_capabilities"]
    assert "registry_figures" in profile["_capabilities"]


def test_rf3161_a_previously_earned_stamp_is_withdrawn_when_it_regresses():
    """A profile carrying the stamp from an older build, whose stored result is a
    transient failure, must be re-tried rather than trusted."""
    profile = {
        "_capabilities": ["issuer_report", "registry_accounts", "registry_figures"],
        "issuer_financials": dict(BABCOCK_TRANSIENT),
    }
    fh._stamp_capabilities(profile)
    assert "issuer_report" not in profile["_capabilities"], (
        "this is the exact live vault state — it must not survive re-stamping")


def test_rf3161_unknown_stamps_from_other_builds_are_preserved():
    """Never drop a capability this build does not know about (rollback safety)."""
    profile = {"_capabilities": ["some_future_capability"],
               "issuer_financials": dict(ANSWERED)}
    fh._stamp_capabilities(profile)
    assert "some_future_capability" in profile["_capabilities"]


def test_rf3161_capability_backfill_does_not_stamp_a_transient_failure(monkeypatch):
    """THE SECOND PATH: the enricher signals a transient obstacle by RETURNING FALSE,
    so the loop's `except` never sees it and it was stamped as 'consulted'."""
    async def _fake_issuer(profile, name, jur, reg=""):
        profile["issuer_financials"] = dict(BABCOCK_TRANSIENT)
        return False

    monkeypatch.setitem(fh.FINANCIAL_CAPABILITIES, "issuer_report", _fake_issuer)

    profile = {"_capabilities": ["registry_accounts", "registry_figures"]}
    asyncio.run(fh.backfill_missing_capabilities(
        profile, name="Babcock International Group plc",
        jurisdiction_iso2="GB", registration_number="02342138"))

    assert "issuer_report" not in (profile.get("_capabilities") or []), (
        "R-F3161 REGRESSION: the backfill stamped a capability that did not complete")


def test_rf3161_capability_backfill_stamps_a_real_negative(monkeypatch):
    """The same loop must still stamp 'consulted, found nothing'."""
    async def _fake_issuer(profile, name, jur, reg=""):
        profile["issuer_financials"] = dict(NO_DOCUMENT)
        return False

    monkeypatch.setitem(fh.FINANCIAL_CAPABILITIES, "issuer_report", _fake_issuer)

    profile = {"_capabilities": ["registry_accounts", "registry_figures"]}
    asyncio.run(fh.backfill_missing_capabilities(
        profile, name="X Ltd", jurisdiction_iso2="GB"))

    assert "issuer_report" in (profile.get("_capabilities") or []), (
        "a genuine negative must be remembered, not re-paid for (§15)")


def test_rf3161_existing_poisoned_vault_record_self_heals():
    """THE MIGRATION CASE — fixing only the WRITE path would leave every already-
    poisoned profile frozen until it aged out. This is the live Babcock record."""
    poisoned = {
        "_capabilities": ["issuer_report", "registry_accounts", "registry_figures"],
        "issuer_financials": dict(BABCOCK_TRANSIENT),
    }
    assert fh.missing_capabilities(poisoned) == ["issuer_report"], (
        "a stamp sitting next to its own transient failure must read as MISSING, "
        "or the vault replays the failure forever")


def test_rf3161_healthy_and_negative_records_are_not_re_enriched():
    """Self-healing must not become re-running everything (§15/§17)."""
    healthy = {"_capabilities": list(fh.FINANCIAL_CAPABILITIES),
               "issuer_financials": dict(ANSWERED)}
    negative = {"_capabilities": list(fh.FINANCIAL_CAPABILITIES),
                "issuer_financials": dict(NO_DOCUMENT)}
    assert fh.missing_capabilities(healthy) == []
    assert fh.missing_capabilities(negative) == []
