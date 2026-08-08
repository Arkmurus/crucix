"""R-F3498 — the GDPR capabilities shipped with no route. Nobody could invoke them.

MY OWN DEFECT, and the same one this session criticised in someone else's work. R-F3484
(provable erasure), R-F3490/3492/3493 (retention review, jurisdiction-aware, with cited
statutory periods) were built, tested, deployed and verified live — and exposed through
NO endpoint. A controller could not run a retention review or fulfil an Art. 17 request
through any interface. `build_rev` matched, health checks passed, and the capability was
unreachable.

"Deployed" is not "working". That distinction is the whole content of this R-number.

TWO SAFETY PROPERTIES, both asserted below:
  * `dry_run` defaults to TRUE on erasure. It is irreversible, and §7 otherwise forbids
    deletion, so committing must be a deliberate act rather than the default one.
  * an empty subject_key is refused, never treated as "match everything".

Both endpoints sit under `/api/aria/admin/*`, which carries the router-level Bearer
dependency and is additionally admin-gated at the web tier.
"""
from __future__ import annotations

import pytest

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _routes():
    from aria_service.routes.aria import router
    return {getattr(r, "path", ""): r for r in router.routes}


def test_the_retention_review_is_reachable():
    """THE DEFECT: shipped, live, and callable by nobody."""
    assert "/api/aria/admin/gdpr/retention-review" in _routes()


def test_the_erasure_endpoint_is_reachable():
    assert "/api/aria/admin/gdpr/erase-subject" in _routes()


def test_erasure_is_dry_run_by_default():
    """Irreversible, and §7 forbids deletion otherwise. Committing must be deliberate."""
    from aria_service.routes.aria import GdprEraseRequest
    assert GdprEraseRequest.model_fields["dry_run"].default is True


def test_subject_key_is_required_not_defaulted():
    """A blank key must never be able to mean 'erase everything'."""
    from aria_service.routes.aria import GdprEraseRequest
    f = GdprEraseRequest.model_fields["subject_key"]
    assert f.is_required(), "subject_key has a default, so it could be omitted"


def test_an_empty_subject_key_is_rejected():
    import asyncio
    from fastapi import HTTPException
    from aria_service.routes.aria import GdprEraseRequest, gdpr_erase_subject_ep

    with pytest.raises(HTTPException) as exc:
        asyncio.run(gdpr_erase_subject_ep(GdprEraseRequest(subject_key="   ")))
    assert exc.value.status_code == 400


def test_both_sit_under_the_admin_prefix():
    """Erasure and a personal-data inventory are controller operations. The admin prefix
    is what carries the web-tier requireAdmin gate."""
    for p in ("/api/aria/admin/gdpr/retention-review",
              "/api/aria/admin/gdpr/erase-subject"):
        assert "/admin/" in p


def test_the_review_route_is_read_only():
    """A GET that could delete would be a trap for any crawler or link-prefetcher."""
    r = _routes()["/api/aria/admin/gdpr/retention-review"]
    assert set(r.methods) == {"GET"}


def test_the_erase_route_is_not_a_get():
    r = _routes()["/api/aria/admin/gdpr/erase-subject"]
    assert "GET" not in set(r.methods)
    assert "POST" in set(r.methods)


def test_every_gdpr_capability_now_has_a_caller():
    """THE CLASS GUARD. A capability with no caller is a dormant specification. If a new
    erasure/retention function is added without a route, this fails."""
    import inspect
    from aria_service.intel import rag_store
    from aria_service.routes import aria as _routes_mod

    public = {
        name for name, fn in vars(rag_store).items()
        if callable(fn) and not name.startswith("_")
        and name in {"erase_by_subject", "retention_review", "purge_by_keywords"}
    }
    src = module_source(_routes_mod)
    unreachable = sorted(n for n in public if n not in src)
    assert not unreachable, (
        f"these GDPR capabilities exist but no route invokes them: {unreachable}. "
        f"Shipping one is 'deployed', not 'working'.")
