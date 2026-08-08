"""R-F2834 — the DD vault must not mask evidence sources it predates.

THE DEFECT (already cost us live, twice). `financial_health.assess()` serves any
vault profile younger than `max_age_days` (30), and the vault carries NO capability
or schema version. So a profile written BEFORE an evidence source existed keeps
suppressing that source for the entire freshness window: the code is deployed,
tested and correct, and the entity simply never runs it.

This is not hypothetical. R-F2782 phase 1 shipped GB registry-accounts evidence and
two live deep DDs — BAE and Rolls-Royce — both returned `from_vault: True` with no
registry evidence, because their profiles predated the feature. The feature looked
broken in production while being perfectly correct.

R-F2817 fixed it for ONE field, by hardcoding a backfill call for registry accounts
on the vault-hit path. That leaves the general defect intact: EVERY future evidence
source needs someone to remember to add another hardcoded backfill, and a forgotten
one fails silently — masked for 30 days, indistinguishable from "the entity has no
such evidence". Absence of evidence presented as evidence of absence is the exact
false-clean class this product exists to refuse.

THE FIX UNDER TEST: profiles are stamped with the set of capabilities that produced
them. On a vault hit the assessor computes `CURRENT - stamped`, runs only the
enrichers for what is missing, re-stamps and re-persists (pay-once, §15). Adding an
evidence source means adding ONE registry entry — it then backfills automatically,
and it cannot be silently masked.
"""
import asyncio

import pytest

from aria_service.intel import financial_health as FH

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_capability_registry_exists_and_is_the_single_source_of_truth():
    """Adding an evidence source must be a registry entry, not a new hardcoded call."""
    assert hasattr(FH, "FINANCIAL_CAPABILITIES"), (
        "financial_health must declare a capability registry — without one, every "
        "new evidence source needs a hand-added backfill and a forgotten one is "
        "silently masked for the whole 30-day freshness window"
    )
    caps = FH.FINANCIAL_CAPABILITIES
    assert isinstance(caps, dict) and caps, "registry must be a non-empty mapping"
    # R-F2782/R-F2817's source is the first member; it must be registered, not special.
    assert any("registry_accounts" in k for k in caps), (
        "the GB registry-accounts source must be a REGISTRY ENTRY, not a hardcoded "
        "call — otherwise R-F2817 remains a one-off and the class stays open"
    )
    for cap_id, fn in caps.items():
        assert callable(fn), f"capability {cap_id} must map to a callable enricher"


def test_current_capability_set_is_derived_not_hardcoded():
    """The 'current' set must come FROM the registry, so the two cannot drift."""
    assert set(FH.current_capabilities()) == set(FH.FINANCIAL_CAPABILITIES), (
        "current_capabilities() must be derived from FINANCIAL_CAPABILITIES; a "
        "hand-maintained second list is how the nav gate drifted in R-F2822"
    )


def test_missing_capabilities_detects_a_stale_profile():
    """A profile written before a capability existed must be reported as missing it."""
    old_profile = {"_capabilities": []}          # written before any stamping
    missing = FH.missing_capabilities(old_profile)
    assert set(missing) == set(FH.current_capabilities()), (
        "an unstamped profile must be treated as missing EVERY capability — this is "
        "the BAE / Rolls-Royce case, where the profile predated the feature"
    )


def test_a_fully_stamped_profile_needs_no_backfill():
    """Pay-once (§15): a current profile must not be re-enriched on every read."""
    current = {"_capabilities": list(FH.current_capabilities())}
    assert FH.missing_capabilities(current) == [], (
        "a profile stamped with the current capability set must require no work — "
        "otherwise every vault hit re-fetches and the vault stops being pay-once"
    )


def test_unknown_stamped_capability_is_ignored_not_crashed():
    """A profile from a NEWER build (rollback case) must not break the reader."""
    from_future = {"_capabilities": list(FH.current_capabilities()) + ["some_future_source"]}
    assert FH.missing_capabilities(from_future) == [], (
        "a profile stamped by a newer build must be treated as complete, not crash "
        "and not trigger a pointless re-enrichment"
    )


@pytest.mark.asyncio
async def test_vault_hit_backfills_only_what_is_missing_then_restamps(monkeypatch):
    """CAPABILITY: the BAE/Rolls-Royce case — a stale profile gets enriched on read.

    Asserts the three properties that matter: the missing enricher RUNS, the
    already-present one does NOT (pay-once), and the profile is RE-STAMPED so the
    next read is free.
    """
    ran: list[str] = []

    async def _fake_enricher(result, name, jurisdiction_iso2, registration_number=""):
        ran.append("registry_accounts")
        result["registry_accounts"] = {"source": "companies_house"}
        return True

    # Replace the registry with a controllable one-entry set.
    monkeypatch.setattr(
        FH, "FINANCIAL_CAPABILITIES", {"registry_accounts": _fake_enricher}, raising=False
    )

    stale = {"_capabilities": [], "_vault_updated_at": 0.0}
    changed = await FH.backfill_missing_capabilities(
        stale, name="BAE Systems", jurisdiction_iso2="GB", registration_number="",
    )

    assert changed is True, "a stale profile must report that it was enriched"
    assert ran == ["registry_accounts"], f"expected the missing enricher to run, got {ran}"
    assert "registry_accounts" in stale, "the evidence must be attached to the profile"
    assert "registry_accounts" in stale.get("_capabilities", []), (
        "the profile must be RE-STAMPED, or it is re-enriched on every single read"
    )

    # Second pass: nothing missing, nothing should run.
    ran.clear()
    changed2 = await FH.backfill_missing_capabilities(
        stale, name="BAE Systems", jurisdiction_iso2="GB", registration_number="",
    )
    assert changed2 is False and ran == [], (
        "a re-stamped profile must require no further work (pay-once, §15)"
    )


@pytest.mark.asyncio
async def test_a_failing_enricher_does_not_stamp_its_capability(monkeypatch):
    """HONESTY: a capability that FAILED must stay missing, so it retries later.

    Stamping on failure would record 'this evidence source has run' when it has not
    — the profile would then look complete while carrying no evidence, which is the
    same absence-read-as-clean defect the vault masking already caused.
    """
    async def _broken(result, name, jurisdiction_iso2, registration_number=""):
        raise RuntimeError("companies house 503")

    monkeypatch.setattr(
        FH, "FINANCIAL_CAPABILITIES", {"registry_accounts": _broken}, raising=False
    )
    prof = {"_capabilities": []}
    changed = await FH.backfill_missing_capabilities(
        prof, name="X", jurisdiction_iso2="GB", registration_number="",
    )
    assert changed is False
    assert "registry_accounts" not in prof.get("_capabilities", []), (
        "a failed enricher must NOT be stamped — it must remain missing so the next "
        "read retries it, rather than recording evidence that was never gathered"
    )


def test_fresh_assessments_are_stamped_too():
    """A profile written today must carry the stamp, or it is stale the moment it lands."""
    import inspect
    src = function_source(FH, "assess")
    assert "current_capabilities()" in src or "_stamp_capabilities" in src, (
        "assess() must stamp the capability set on the profiles it writes; an "
        "unstamped fresh profile would be re-enriched on every subsequent read"
    )
