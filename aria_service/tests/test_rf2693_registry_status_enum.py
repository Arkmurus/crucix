"""R-F2693 — a registry STUB must never count as Grade-A identity authority.

DD Grade-A Phase-0 item 3. `registry_adapters` had no formal status vocabulary: a
stub/fallback adapter (Angola GUE, Kenya BRS, Saudi MOCI, …) returns a result whose
only marker is a `_stub` suffix buried in the adapter NAME, and whose
`company_status` is the string "unknown".

The live chain that makes that dangerous:
    dd_orchestrator: identity.registration_status = profile["company_status"]  # "unknown"
    dd_schema._quality_metrics: registry_substance = bool(registration_status or …)
                                                   = bool("unknown") = TRUE
                                identity_authority = TRUE
    dd_schema._quality_penalties: the 25-point "no identity authority" penalty is SKIPPED

So an adapter that looked up NOTHING — and whose own data_gaps say "no public registry
API, recommend manual verification" — silently certifies identity authority and lifts
the evidence grade. That is a fabricated pass of exactly the R-F2413/never-false-clean
class this codebase keeps re-discovering.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import _quality_metrics
from aria_service.intel.registry_adapters import RegistryStatus


def _report(**identity) -> dict:
    ident = {"meta": {"status": "ok"}}
    ident.update(identity)
    return {"identity": ident, "compliance": {"meta": {"status": "ok"}}}


# ── the enum itself ────────────────────────────────────────────────────────

def test_registry_status_vocabulary_is_formal():
    """A free string cannot be reasoned about; the grade needs a closed set."""
    assert RegistryStatus.VERIFIED.value == "verified"
    assert RegistryStatus.PARTIAL.value == "partial"
    assert RegistryStatus.MANUAL_REQUIRED.value == "manual_required"
    assert RegistryStatus.NOT_AVAILABLE.value == "not_available"
    assert RegistryStatus.PROVIDER_REQUIRED.value == "provider_required"


def test_only_verified_and_partial_are_identity_authority():
    """The whole point: a stub/fallback is NEVER identity authority."""
    assert RegistryStatus.VERIFIED.is_authority() is True
    assert RegistryStatus.PARTIAL.is_authority() is True
    assert RegistryStatus.MANUAL_REQUIRED.is_authority() is False
    assert RegistryStatus.NOT_AVAILABLE.is_authority() is False
    assert RegistryStatus.PROVIDER_REQUIRED.is_authority() is False


def test_stub_adapters_are_classified_from_the_existing_naming_convention():
    """The 8 `*_stub` adapters must classify themselves — no per-adapter edit needed,
    and a NEW stub added later is caught by the same convention."""
    assert RegistryStatus.for_adapter("angola_gue_stub") is RegistryStatus.MANUAL_REQUIRED
    assert RegistryStatus.for_adapter("kenya_brs_stub") is RegistryStatus.MANUAL_REQUIRED
    assert RegistryStatus.for_adapter("companies_house") is RegistryStatus.VERIFIED
    assert RegistryStatus.for_adapter("") is RegistryStatus.NOT_AVAILABLE


# ── the live chain ─────────────────────────────────────────────────────────

def test_stub_result_carries_a_non_authoritative_status(monkeypatch):
    """CAPABILITY: the real Angola stub path, driven end to end.

    HERMETIC: the adapter's first branch does a best-effort GET of the live GUE
    portal, so without this the test hits the internet and (observed) takes the
    scrape branch instead — flaky, and it silently stops testing the stub.
    """
    import asyncio

    from aria_service.intel import registry_adapters

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            raise RuntimeError("network disabled in test")

    monkeypatch.setattr(registry_adapters.httpx, "AsyncClient", _Boom)
    result = asyncio.run(registry_adapters._lookup_angola("Some Angolan Co", ""))

    assert result["adapter"] == "angola_gue_stub"
    assert result["registry_status"] == RegistryStatus.MANUAL_REQUIRED.value
    # Its own data_gaps already said manual verification was required — the status
    # must now agree with the data_gaps instead of contradicting them.
    assert result["data_gaps"]

    # ...and the stub must not lift the grade through the live chain.
    profile = result["profile"]
    m = _quality_metrics(_report(
        registration_status=profile["company_status"],       # "unknown"
        incorporation_date=profile["date_of_creation"],      # ""
        registry_status=result["registry_status"],
    ))
    assert m["identity_authority_present"] is False


def test_angola_homepage_scrape_branch_is_not_authority(monkeypatch):
    """The non-stub `angola_gue` branch GETs the portal HOMEPAGE and regexes it for
    Empresa/NIF — it never searches for THIS entity, so a match is boilerplate, not a
    confirmation. Its adapter name would otherwise classify it VERIFIED."""
    import asyncio

    from aria_service.intel import registry_adapters

    class _Homepage:
        status_code = 200
        text = "<html><body>Empresa: Alguma Outra Empresa SA · NIF: 5417123456</body></html>"

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Homepage()

    monkeypatch.setattr(registry_adapters.httpx, "AsyncClient", _Client)
    result = asyncio.run(registry_adapters._lookup_angola("Some Angolan Co", ""))

    assert result["adapter"] == "angola_gue"          # non-stub name...
    assert result["registry_status"] == RegistryStatus.MANUAL_REQUIRED.value  # ...but NOT authority
    assert _quality_metrics(_report(
        registration_status="active",  # even if something substantive appeared
        registry_status=result["registry_status"],
    ))["identity_authority_present"] is False


def test_unknown_registration_status_is_not_registry_substance():
    """CAPABILITY: the bug's core. "unknown" is a truthy string, not substance."""
    m = _quality_metrics(_report(registration_status="unknown"))
    assert m["registry_substance_present"] is False
    assert m["identity_authority_present"] is False


@pytest.mark.parametrize("empty", ["", "unknown", "n/a", "not available", None])
def test_non_substantive_status_values_never_grant_authority(empty):
    m = _quality_metrics(_report(registration_status=empty))
    assert m["identity_authority_present"] is False


def test_a_stub_backed_identity_is_not_identity_authority():
    """CAPABILITY: even WITH a plausible-looking status, a stub-sourced identity
    must not certify authority."""
    m = _quality_metrics(_report(
        registration_status="active",
        registry_status=RegistryStatus.MANUAL_REQUIRED.value,
    ))
    assert m["identity_authority_present"] is False


def test_a_real_registry_hit_still_counts_as_authority():
    """The fix must not clamp the honest case — a real registry hit still passes."""
    m = _quality_metrics(_report(
        registration_status="active",
        incorporation_date="2011-04-02",
        registry_status=RegistryStatus.VERIFIED.value,
    ))
    assert m["registry_substance_present"] is True
    assert m["identity_authority_present"] is True


def test_legacy_identity_without_registry_status_still_works():
    """Backward compatibility: reports persisted before R-F2693 carry no
    registry_status. Real substance must still count — absence of the new field is
    NOT evidence of a stub."""
    m = _quality_metrics(_report(
        registration_status="active",
        incorporation_date="2011-04-02",
        directors=[{"name": "A Director"}],
    ))
    assert m["identity_authority_present"] is True


def test_verified_sanctions_source_is_still_authority_without_a_registry():
    """The other authority route must survive untouched."""
    r = _report()
    r["identity"]["sanctions_screen"] = {"verified_sources": ["ofac"]}
    assert _quality_metrics(r)["identity_authority_present"] is True
