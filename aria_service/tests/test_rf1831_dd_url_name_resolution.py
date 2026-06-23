"""R-F1831 — DD entity resolution: a bare-domain "name" is resolved to the org.

ROOT CAUSE (operator-visible, 2026-06-23): a live web DD on
"https://modirumgespi.com/en" returned directors_found=0, registration=MISSING,
jurisdiction=MISSING — an empty report on a trivially resolvable entity
(Modirum | Gespi = Modirum Defence's Brazilian arm). The chat intent detector
passed the bare domain "modirumgespi.com" through as the entity NAME, which
satisfied the early-return in _enrich_target_from_url (dd_orchestrator.py) — so
the URL→org resolver never ran and every registry / people / jurisdiction layer
searched for a dead domain string instead of the organisation.

R-F1831: a "name" that is itself a URL / bare domain no longer counts as a real
org name. It is moved into the `website` slot and cleared, so
_extract_entity_from_url fetches the page <title> and recovers the real org
name BEFORE any layer runs.

CAPABILITY tests: drive the real _enrich_target_from_url chokepoint (the broken
path) with a stubbed page resolver and assert the dead-domain name is replaced
by the resolved org — plus the URL/domain classifier that gates it.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as ddo


def test_rf1831_classifier_flags_urls_and_domains_not_org_names():
    f = ddo._looks_like_url_or_domain
    # URL-shaped → must be resolved, not used as a name.
    assert f("modirumgespi.com") is True
    assert f("https://modirumgespi.com/en") is True
    assert f("http://x.co") is True
    assert f("sub.example.co.uk/path") is True
    # Real org names → keep as the name (no fetch).
    assert f("Modirum Gespi") is False
    assert f("Acme Corp Ltd") is False
    assert f("Gespi") is False           # single word, no TLD
    assert f("") is False
    assert f("   ") is False


@pytest.mark.asyncio
async def test_rf1831_bare_domain_name_is_resolved_to_org(monkeypatch):
    """The smoking gun: a target whose NAME is a bare domain must be resolved
    to the real org via the page <title>, not run as the dead domain string."""
    async def _fake_extract(url):
        assert "modirumgespi.com" in url, f"resolver got the wrong url: {url!r}"
        return {"name": "Modirum", "domain": "modirumgespi.com", "snippet": ""}

    monkeypatch.setattr(ddo, "_extract_entity_from_url", _fake_extract)

    target = {"name": "modirumgespi.com", "type": "company"}
    out = await ddo._enrich_target_from_url(target)

    assert out["name"] == "Modirum", (
        f"bare-domain name was not resolved (got {out['name']!r}) — the DD would "
        f"still run against the dead domain string → empty report."
    )
    assert out.get("website") == "modirumgespi.com", (
        "the original domain should be preserved as the website for the layers."
    )


@pytest.mark.asyncio
async def test_rf1831_url_name_with_scheme_and_path_is_resolved(monkeypatch):
    """The exact operator input form: a full https URL with a path."""
    async def _fake_extract(url):
        return {"name": "Modirum Gespi", "domain": "modirumgespi.com"}

    monkeypatch.setattr(ddo, "_extract_entity_from_url", _fake_extract)

    target = {"entity": "https://modirumgespi.com/en", "type": "company"}
    out = await ddo._enrich_target_from_url(target)

    assert out["name"] == "Modirum Gespi", (
        f"full-URL entity not resolved (got {out.get('name')!r})."
    )


@pytest.mark.asyncio
async def test_rf1831_orchestrator_callsite_invokes_enrich_for_url_name(monkeypatch):
    """THE REAL BUG (live 2026-06-23): orchestrate_dd's call-site guard only
    invoked _enrich_target_from_url when the name was EMPTY, so a URL-shaped
    name ('modirumgespi.com') short-circuited it and the org was never resolved.
    Unit-testing _enrich directly missed this — this drives the gated call path.
    """
    called = {"n": 0}

    async def _rec_enrich(target):
        called["n"] += 1
        target["name"] = "Modirum Resolved"
        return target
    monkeypatch.setattr(ddo, "_enrich_target_from_url", _rec_enrich)

    # Stub the heavy layers so orchestrate_dd returns fast & offline.
    async def _identity(target, report):
        report.identity.entity_name = target.get("name", "")
        return False
    async def _noop(*a, **k):
        return None
    async def _noop_dict(*a, **k):
        return {}
    async def _ext(*a, **k):
        return {"ran": False}
    for fn in ("_run_network", "_run_compliance", "_run_digital",
               "_run_sweep_intelligence", "_run_verification", "_run_synthesis",
               "_assemble_bluf"):
        monkeypatch.setattr(ddo, fn, _noop, raising=False)
    monkeypatch.setattr(ddo, "_run_identity", _identity, raising=False)
    monkeypatch.setenv("ARIA_LAYER_5C_ENABLED", "0")
    try:
        from aria_service.intel import dd_layer_extensions as _dlx
        monkeypatch.setattr(_dlx, "run_all_extensions", _ext, raising=False)
    except Exception:
        pass

    report = await ddo.orchestrate_dd(
        target={"name": "modirumgespi.com", "type": "company"},
        llm=None, total_budget_s=5.0,
    )
    assert called["n"] >= 1, (
        "orchestrate_dd's call-site guard did NOT invoke _enrich_target_from_url "
        "for a URL-shaped name — the live bug (entity ran as the dead domain)."
    )
    assert report is not None


@pytest.mark.asyncio
async def test_rf1831_real_org_name_is_left_untouched(monkeypatch):
    """Guard against over-reach: a real org name must NOT trigger a fetch or be
    overwritten — only URL/domain-shaped names get re-routed."""
    called = {"fetched": False}

    async def _fake_extract(url):
        called["fetched"] = True
        return {"name": "WRONG"}

    monkeypatch.setattr(ddo, "_extract_entity_from_url", _fake_extract)

    target = {"name": "Modirum Gespi", "type": "company"}
    out = await ddo._enrich_target_from_url(target)

    assert out["name"] == "Modirum Gespi", "a real org name was overwritten."
    assert called["fetched"] is False, (
        "the resolver fetched a page for a real org name — wasted call / SSRF surface."
    )
