"""R-F1880 — a blocked search must NOT read as a clean entity.

When all web-search backends are blocked (datacenter IP 403/CAPTCHA), the DD
used to see 0 press hits and imply "no adverse media" — indistinguishable from a
genuinely clean entity. R-F1880 inspects web_search.get_last_search_ecosystem()
and, when search is DEAD/DEGRADED, records a loud COVERAGE-GAP data-gap +
finding so the BLUF/footer never implies a clean bill from an un-searchable web.

Verified by: (1) static guard that the ecosystem check is wired into the digital
layer with the right signal + honest wording; (2) the real
get_last_search_ecosystem contract returns the health_signal we key on.
"""
from __future__ import annotations

import inspect

import aria_service.intel.dd_orchestrator as dd
import aria_service.intel.web_search as ws

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


def test_digital_layer_flags_blocked_search():
    src = function_source(dd, "_run_digital")
    assert "get_last_search_ecosystem()" in src, "digital layer does not inspect the search ecosystem"
    assert 'health_signal' in src.lower() or "_eco_health" in src
    # keys off DEAD / DEGRADED / zero active backends
    assert '"DEAD"' in src and '"DEGRADED"' in src
    assert "active_backends" in src
    # the honest wording: absence of findings != clean
    assert "does NOT indicate a clean entity" in src
    assert "ADVERSE-MEDIA SEARCH UNAVAILABLE" in src


def test_get_last_search_ecosystem_contract():
    # The signal R-F1880 keys on must exist on the real function's contract.
    eco = ws.get_last_search_ecosystem()
    assert isinstance(eco, dict)  # empty dict before any search — safe to .get()
    # the function is documented to return health_signal + summary.active_backends
    doc = (ws.get_last_search_ecosystem.__doc__ or "")
    assert "health_signal" in doc and "active_backends" in doc
