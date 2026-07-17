"""R-F2695 — a registry adapter must never invent the SUBJECT's identifiers.

Three adapters (Angola GUE, Panama Registro Público, Bulgaria BRRA) fetched their
portal's HOMEPAGE — `_PA_BASE` / `_BG_BASE` / "https://gue.gov.ao/", with NO query and
the subject's name never sent — then regexed that HTML for an identifier pattern
(NIF / Folio / ЕИК) and a name label, and on ANY match returned:

    _build_result(company_name=<text after the label on the homepage>,
                  company_number=<the number found on the homepage>, ...)

dd_orchestrator then assigns those to report.identity.registration_number /
entity_name FOR THE SUBJECT. So a number belonging to some other company — or to a
worked example in the portal's own help text — could be attached to the entity under
due diligence, and reported as its registration number.

This is not a stub problem (R-F2693 covered stubs). These branches carry a NON-stub
adapter name and look like real registry hits. The sibling adapters that DO send a
query (Kenya `q=`, Saudi `entityName=`, Ghana `search=`, Israel params) are real
searches and are deliberately left alone.

A lookup that never searched for the entity cannot confirm it. The honest result is
the stub the adapter already builds — with its data_gaps saying manual verification
is required.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import registry_adapters
from aria_service.intel.registry_adapters import RegistryStatus


# Each portal homepage, carrying EXACTLY the boilerplate the old regexes matched:
# a plausible identifier and a name label that belong to SOMEONE ELSE.
_HOMEPAGES = {
    "angola": (
        registry_adapters._lookup_angola,
        "<html><body>Bem-vindo ao GUE. Exemplo — Empresa: Outra Empresa Lda "
        "· NIF: 5417098765</body></html>",
        "5417098765",
    ),
    "panama": (
        registry_adapters._lookup_panama,
        "<html><body>Consulta. Ejemplo — Denominación: Otra Sociedad SA "
        "· Folio: 155987654</body></html>",
        "155987654",
    ),
    "bulgaria": (
        registry_adapters._lookup_bulgaria,
        "<html><body>Пример — Наименование: Друга Фирма ЕООД · ЕИК: 203987654"
        "</body></html>",
        "203987654",
    ),
}


def _serve(monkeypatch, html: str):
    """Point the adapter's httpx at a portal homepage that returns `html`."""
    class _Resp:
        status_code = 200
        text = html

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(registry_adapters.httpx, "AsyncClient", _Client)


@pytest.mark.parametrize("jurisdiction", sorted(_HOMEPAGES))
def test_homepage_boilerplate_never_becomes_the_subjects_identifiers(jurisdiction, monkeypatch):
    """CAPABILITY: the real bug. A number on the homepage must not become the
    subject's registration number."""
    lookup, html, foreign_number = _HOMEPAGES[jurisdiction]
    _serve(monkeypatch, html)

    result = asyncio.run(lookup("Subject Company Under DD", ""))

    assert result is not None, "the adapter must still return its stub entry"
    profile = result["profile"]

    # The identifier scraped off the homepage belongs to another company.
    assert profile["company_number"] != foreign_number, (
        f"{jurisdiction}: homepage identifier {foreign_number} was attached to the subject"
    )
    assert foreign_number not in str(profile), f"{jurisdiction}: homepage identifier leaked"
    # ...and so does the name.
    assert "Outra" not in profile["company_name"]
    assert "Otra" not in profile["company_name"]
    assert "Друга" not in profile["company_name"]
    # The subject's own name is echoed back (that is an input, not a claim).
    assert profile["company_name"] == "Subject Company Under DD"


@pytest.mark.parametrize("jurisdiction", sorted(_HOMEPAGES))
def test_homepage_response_yields_the_honest_stub(jurisdiction, monkeypatch):
    """A lookup that never searched must report manual verification, not a hit."""
    lookup, html, _ = _HOMEPAGES[jurisdiction]
    _serve(monkeypatch, html)

    result = asyncio.run(lookup("Subject Company Under DD", ""))

    assert result["adapter"].endswith("_stub"), (
        f"{jurisdiction}: still claims a non-stub (real-registry) adapter name"
    )
    assert result["registry_status"] == RegistryStatus.MANUAL_REQUIRED.value
    assert not RegistryStatus(result["registry_status"]).is_authority()
    assert result["data_gaps"], "the stub must explain what a human has to do"


@pytest.mark.parametrize("jurisdiction", sorted(_HOMEPAGES))
def test_unreachable_portal_still_yields_the_stub(jurisdiction, monkeypatch):
    """Regression: the network path must stay non-fatal (it was inside a try/except)."""
    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("portal down")

    monkeypatch.setattr(registry_adapters.httpx, "AsyncClient", _Boom)
    lookup, _, _ = _HOMEPAGES[jurisdiction]

    result = asyncio.run(lookup("Subject Company Under DD", ""))
    assert result["adapter"].endswith("_stub")
    assert result["data_gaps"]


def test_real_search_adapters_are_untouched():
    """Kenya/Saudi/Ghana DO send the subject in the query — they are real searches and
    must NOT be swept up in this fix."""
    import inspect

    for fn in (registry_adapters._lookup_kenya,
               registry_adapters._lookup_saudi_arabia,
               registry_adapters._lookup_ghana):
        src = inspect.getsource(fn)
        assert "params=" in src, f"{fn.__name__} no longer sends a query"


def test_grade_cannot_be_lifted_by_a_homepage_scrape(monkeypatch):
    """End-to-end honesty: the homepage path must not certify identity authority."""
    from aria_service.intel.dd_schema import _quality_metrics

    lookup, html, _ = _HOMEPAGES["angola"]
    _serve(monkeypatch, html)
    result = asyncio.run(lookup("Subject Company Under DD", ""))

    m = _quality_metrics({
        "identity": {
            "meta": {"status": "ok"},
            "registration_status": result["profile"]["company_status"],
            "registry_status": result["registry_status"],
        },
        "compliance": {"meta": {"status": "ok"}},
    })
    assert m["identity_authority_present"] is False
